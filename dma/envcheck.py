from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import connectors, paths
from .config import AIConfig

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_INFO = "info"

JAVA_DOWNLOAD_URL = "https://adoptium.net/temurin/releases/"
GRADLE_DOWNLOAD_URL = "https://gradle.org/releases/"
GIT_DOWNLOAD_URL = "https://git-scm.com/download/win"


@dataclass
class CheckItem:
    key: str
    name: str
    status: str
    summary: str
    detail: str = ""
    hint: str = ""
    url: str = ""


@dataclass
class ToolPaths:
    """在主线程解析 PATH，避免部分 Windows 路径在后台线程 stat 时卡住。"""

    java: list[Path] = field(default_factory=list)
    gradle: str = ""
    git: str = ""


def parse_java_version(text: str) -> str:
    match = re.search(r'version\s+"([^"]+)"', text or "")
    return match.group(1) if match else ""


def java_major(version: str) -> int:
    text = (version or "").strip()
    if text.startswith("1."):
        parts = text.split(".")
        if len(parts) > 1:
            try:
                return int(parts[1])
            except ValueError:
                return 0
    match = re.match(r"(\d+)", text)
    return int(match.group(1)) if match else 0


def java_rank(major: int) -> int:
    """给已安装的 JDK 排优先级：Forge 1.20.1 最想要 17，其次 21，再往后是其他 17–24。"""
    if major == 17:
        return 0
    if major == 21:
        return 1
    if 18 <= major <= 24:
        return 2
    if major == 25:
        return 3
    if major >= 26:
        return 4
    if major >= 8:
        return 9
    return 8


def _run(command: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=flags,
    )


_JAVA_REGISTRY = (
    (r"SOFTWARE\JavaSoft\JDK", "JavaHome"),
    (r"SOFTWARE\JavaSoft\Java Development Kit", "JavaHome"),
    (r"SOFTWARE\Eclipse Adoptium\JDK", "JavaHome"),
    (r"SOFTWARE\Microsoft\JDK", "JavaHome"),
    (r"SOFTWARE\Amazon Corretto\JDK", "JavaHome"),
    (r"SOFTWARE\Azul Systems\Zulu", "InstallationPath"),
    (r"SOFTWARE\BellSoft\LibericaJDK", "Home"),
)

_HOME_CACHE: list[Path] | None = None
_HOME_CACHE_AT = 0.0
_HOME_CACHE_TTL = 15.0


def java_executable(home: Path) -> Path | None:
    names = ("java.exe", "java") if os.name == "nt" else ("java",)
    for name in names:
        item = Path(home) / "bin" / name
        if item.is_file():
            return item
    return None


def is_java_home(home: Path) -> bool:
    return java_executable(home) is not None


def java_version_at(home: Path) -> str:
    """优先读 JDK 自带的 release 文件，比启动 java -version 快很多。"""
    try:
        text = (Path(home) / "release").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith("JAVA_VERSION="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def _registry_homes() -> list[Path]:
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    result: list[Path] = []
    for key_path, value_name in _JAVA_REGISTRY:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    key_path,
                    0,
                    winreg.KEY_READ | view,
                )
            except OSError:
                continue
            with root:
                index = 0
                while True:
                    try:
                        child_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        child = winreg.OpenKey(root, child_name)
                        with child:
                            value, _ = winreg.QueryValueEx(child, value_name)
                    except OSError:
                        continue
                    if value:
                        result.append(Path(str(value)))
    return result


def _search_roots() -> list[Path]:
    roots = [
        Path.home() / ".jdks",
        paths.gradle_home() / "jdks",
        Path.home() / ".gradle" / "jdks",
    ]
    if os.name != "nt":
        return roots
    program_dirs = (
        "Java",
        "Eclipse Adoptium",
        "Microsoft",
        "Amazon Corretto",
        "Zulu",
        "BellSoft",
        "Semeru",
        "JetBrains",
    )
    for base in (Path("C:/Program Files"), Path("C:/Program Files (x86)")):
        roots.extend(base / name for name in program_dirs)
    # 只扫常见的本机盘符，避免在网络盘上挂住。
    for letter in "CDEF":
        drive = Path(f"{letter}:/")
        try:
            if not drive.exists():
                continue
        except OSError:
            continue
        roots.extend(
            [
                drive / "developer" / "Java",
                drive / "Java",
                drive / "tools",
                drive / "dev",
            ]
        )
    return roots


def java_homes(refresh: bool = False) -> list[Path]:
    """列出本机所有 JDK 根目录，包括程序自己下载到 data/tools 的 JDK。"""
    global _HOME_CACHE, _HOME_CACHE_AT
    now = time.monotonic()
    if not refresh and _HOME_CACHE is not None and now - _HOME_CACHE_AT < _HOME_CACHE_TTL:
        return list(_HOME_CACHE)

    found: list[Path] = []

    def add(path: Path | str) -> None:
        try:
            candidate = Path(path).expanduser()
        except (OSError, ValueError):
            return
        if is_java_home(candidate):
            found.append(candidate)

    home = os.environ.get("JAVA_HOME", "").strip()
    if home:
        add(home)

    located = shutil.which("java")
    if located:
        add(Path(located).parent.parent)

    try:
        for item in paths.tools_dir().glob("jdk*"):
            add(item)
    except OSError:
        pass

    for item in _registry_homes():
        add(item)

    for root in _search_roots():
        try:
            children = list(root.iterdir())
        except OSError:
            continue
        for child in children:
            add(child)

    unique: list[Path] = []
    seen: set[str] = set()
    for item in found:
        try:
            key = str(item.resolve())
        except OSError:
            key = str(item)
        key = key.lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    _HOME_CACHE = unique
    _HOME_CACHE_AT = now
    return list(unique)


def _java_candidates() -> list[Path]:
    result: list[Path] = []
    for home in java_homes():
        exe = java_executable(home)
        if exe is not None:
            result.append(exe)
    found = shutil.which("java")
    if found:
        result.append(Path(found))
    unique: list[Path] = []
    seen: set[str] = set()
    for item in result:
        key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def resolve_tools() -> ToolPaths:
    return ToolPaths(
        java=_java_candidates(),
        gradle=shutil.which("gradle") or "",
        git=shutil.which("git") or "",
    )


def _probe_java(java: Path) -> tuple[str, str]:
    """返回 (版本号, 错误信息)。有 release 文件时不启动子进程。"""
    version = java_version_at(Path(java).parent.parent)
    if version:
        return version, ""
    try:
        proc = _run([str(java), "-version"], timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return "", str(exc)
    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    return parse_java_version(output), ""


def probe_java_home(home: Path) -> str:
    exe = java_executable(home)
    if exe is None:
        return ""
    version, _ = _probe_java(exe)
    return version


def check_java(candidates: list[Path] | None = None) -> CheckItem:
    candidates = _java_candidates() if candidates is None else list(candidates)
    if not candidates:
        return CheckItem(
            "java",
            "Java 运行时",
            STATUS_FAIL,
            "未找到 Java",
            "PATH 和 JAVA_HOME 都没有找到 java。",
            "点「一键准备」会自动下载 JDK 21 并配置好；也可以安装 JDK 17 或 21（Temurin / Microsoft OpenJDK）。",
            JAVA_DOWNLOAD_URL,
        )

    probed = []
    for java in candidates:
        version, error = _probe_java(java)
        probed.append((java_rank(java_major(version)), version, java, error))
    valid = [item for item in probed if item[1]]
    if not valid:
        rank, version, java, error = probed[0]
        return CheckItem(
            "java",
            "Java 运行时",
            STATUS_FAIL,
            "Java 无法启动",
            f"{java}：{error or '没有返回版本号'}",
            "确认 JDK 安装完整；也可以点「一键准备」让程序重新下载 JDK 21。",
            JAVA_DOWNLOAD_URL,
        )

    rank, version, java, error = min(valid, key=lambda item: item[0])
    major = java_major(version)
    detail = f"{version or '版本未知'} · {java}"
    others = [
        f"Java {java_major(item[1])}"
        for item in valid
        if str(item[2]).lower() != str(java).lower()
    ]
    if others:
        detail += "\n其他可用：" + "、".join(dict.fromkeys(others))
    if major in (17, 21):
        return CheckItem(
            "java",
            "Java 运行时",
            STATUS_OK,
            f"Java {major} 可用",
            detail,
            "与多数 Fabric / NeoForge 工程匹配；一键准备会优先选这个版本。",
        )
    if major >= 22:
        return CheckItem(
            "java",
            "Java 运行时",
            STATUS_WARN,
            f"Java {major} 可能不兼容",
            detail,
            "多数模组工程要求 Java 17 或 21；Java 22 及以上可能导致 Gradle 或加载器不兼容。点「一键准备」会自动换用或下载 JDK 21。",
            JAVA_DOWNLOAD_URL,
        )
    if major and major < 17:
        return CheckItem(
            "java",
            "Java 运行时",
            STATUS_FAIL,
            f"Java {major} 版本过低",
            detail,
            "模组开发至少需要 Java 17；1.20.5 及以后通常需要 Java 21。点「一键准备」会自动下载 JDK 21。",
            JAVA_DOWNLOAD_URL,
        )
    return CheckItem(
        "java",
        "Java 运行时",
        STATUS_WARN,
        "无法确认 Java 版本",
        detail,
        "点「一键准备」会自动下载并配置 JDK 21。",
        JAVA_DOWNLOAD_URL,
    )


def check_java_home() -> CheckItem:
    home = os.environ.get("JAVA_HOME", "").strip()
    if not home:
        return CheckItem(
            "java_home",
            "JAVA_HOME",
            STATUS_INFO,
            "未设置",
            "不设置也能用 PATH 里的 Java；安装多个 JDK 时建议设置。",
            "把 JAVA_HOME 指向 JDK 根目录，例如 C:\\Program Files\\Eclipse Adoptium\\jdk-21。",
        )
    path = Path(home)
    if not path.is_dir():
        return CheckItem(
            "java_home",
            "JAVA_HOME",
            STATUS_WARN,
            "目录不存在",
            home,
            "JAVA_HOME 指向的目录不存在，修改环境变量或重新安装 JDK。",
            JAVA_DOWNLOAD_URL,
        )
    java_name = "java.exe" if os.name == "nt" else "java"
    if not (path / "bin" / java_name).is_file():
        return CheckItem(
            "java_home",
            "JAVA_HOME",
            STATUS_WARN,
            "目录里没有 java",
            home,
            "JAVA_HOME 应该指向 JDK 根目录，而不是 bin 目录。",
            JAVA_DOWNLOAD_URL,
        )
    return CheckItem(
        "java_home",
        "JAVA_HOME",
        STATUS_OK,
        "已正确指向 JDK",
        home,
        "Gradle 会优先使用这个 JDK。",
    )


def _find_gradlew(project_dir: Path | None) -> Path | None:
    if project_dir is None:
        return None
    for name in ("gradlew.bat", "gradlew"):
        item = project_dir / name
        if item.is_file():
            return item
    return None


def check_gradle(project_dir: Path | None = None, system: str | None = None) -> CheckItem:
    wrapper = _find_gradlew(project_dir)
    system = (shutil.which("gradle") or "") if system is None else system
    system_version = ""
    if system:
        try:
            proc = _run([system, "-v"], timeout=25)
            match = re.search(r"Gradle\s+(\d+(?:\.\d+)+)", (proc.stdout or "") + (proc.stderr or ""))
            if match:
                system_version = match.group(1)
        except (OSError, subprocess.SubprocessError):
            system_version = ""

    if wrapper is not None:
        wrapper_root = wrapper.parent / "gradle" / "wrapper"
        jar = wrapper_root / "gradle-wrapper.jar"
        props = wrapper_root / "gradle-wrapper.properties"
        missing = [name for name, item in (("gradle-wrapper.jar", jar), ("gradle-wrapper.properties", props)) if not item.is_file()]
        detail = f"Wrapper：{wrapper}"
        if system_version:
            detail += f"\n系统 Gradle：{system_version}"
        if missing:
            return CheckItem(
                "gradle",
                "Gradle",
                STATUS_WARN,
                "工程 Wrapper 不完整",
                detail + "\n缺少：" + "、".join(missing),
                "在工程目录安装 Gradle 后执行 gradle wrapper，或重新用官方模板生成工程。",
                GRADLE_DOWNLOAD_URL,
            )
        return CheckItem(
            "gradle",
            "Gradle",
            STATUS_OK,
            "工程 Wrapper 可用",
            detail,
            "直接运行 gradlew build / gradlew runClient；首次运行需要联网下载 Gradle。",
        )

    if system:
        detail = f"系统 Gradle：{system_version or system}"
        return CheckItem(
            "gradle",
            "Gradle",
            STATUS_OK,
            "系统 Gradle 可用" if system_version else "已找到 Gradle",
            detail,
            "当前工程没有 gradlew；可以在工程目录执行 gradle wrapper 生成 Wrapper。",
        )

    return CheckItem(
        "gradle",
        "Gradle",
        STATUS_INFO,
        "未检测到 Gradle",
        "系统 PATH 里没有 gradle，当前工程也没有 gradlew。",
        "在工程目录执行 gradle wrapper 会生成 gradlew；也可以安装 Gradle 8.x 并加入 PATH。",
        GRADLE_DOWNLOAD_URL,
    )


def check_git(git: str | None = None) -> CheckItem:
    git = (shutil.which("git") or "") if git is None else git
    if not git:
        return CheckItem(
            "git",
            "Git（可选）",
            STATUS_INFO,
            "未安装",
            "不影响生成和编译模组，但版本管理与回滚会更方便。",
            "需要时安装 Git，并在工程目录初始化仓库。",
            GIT_DOWNLOAD_URL,
        )
    return CheckItem(
        "git",
        "Git（可选）",
        STATUS_OK,
        "可用",
        str(git),
        "建议在工程目录执行 git init，提交前先确认没有把密钥写进仓库。",
    )


def check_directory(key: str, name: str, folder: Path, hint: str) -> CheckItem:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=str(folder), prefix=".envcheck-", delete=True) as handle:
            handle.write(b"ok")
            handle.flush()
    except OSError as exc:
        return CheckItem(key, name, STATUS_FAIL, "不可写", f"{folder}：{exc}", hint)
    return CheckItem(key, name, STATUS_OK, "可写", str(folder), hint)


def check_ai(cfg: AIConfig | None) -> CheckItem:
    ai = (cfg or AIConfig()).normalized()
    if ai.provider == "pollinations":
        return CheckItem(
            "ai",
            "模型通道",
            STATUS_INFO,
            "免费公共通道",
            f"模型：{ai.model}",
            "开箱可用但稳定性一般。长任务建议在设置里切换到 OpenAI 兼容接口或本地 Ollama。",
        )
    if ai.provider == "ollama":
        return CheckItem(
            "ai",
            "模型通道",
            STATUS_INFO,
            "本地 Ollama",
            f"{ai.base_url} · {ai.model}",
            "需要先启动 Ollama 并拉取对应模型；否则生成时会连接失败。",
        )
    if not ai.api_key:
        return CheckItem(
            "ai",
            "模型通道",
            STATUS_FAIL,
            "缺少 API Key",
            f"{ai.base_url} · {ai.model}",
            "在设置页填写 API Key，或切换到免费公共通道 / 本地 Ollama。",
        )
    return CheckItem(
        "ai",
        "模型通道",
        STATUS_OK,
        "已配置 API Key",
        f"{ai.base_url} · {ai.model}",
        "可以用设置页的「测试连接」进一步确认。",
    )


def check_network() -> CheckItem:
    targets = [
        ("Modrinth", "https://api.modrinth.com/v2/search?limit=1"),
        ("Fabric Meta", "https://meta.fabricmc.net/v2/versions/game"),
        ("GitHub API", "https://api.github.com/rate_limit"),
    ]
    results: dict[str, tuple[bool, str]] = {}
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        futures = {
            pool.submit(connectors.fetch_url, url, timeout=12, max_bytes=4000): name
            for name, url in targets
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                text = future.result()
            except Exception as exc:
                results[name] = (False, str(exc))
            else:
                results[name] = (bool(text.strip()), "可用" if text.strip() else "返回为空")

    ok_count = sum(1 for ok, _ in results.values() if ok)
    detail = "\n".join(
        f"{name}：{'可用' if results[name][0] else results[name][1]}" for name, _ in targets
    )
    if ok_count == len(targets):
        return CheckItem(
            "network",
            "资料源连通性",
            STATUS_OK,
            f"{ok_count}/{len(targets)} 可用",
            detail,
            "可以正常查询模组版本、加载器版本和 GitHub 仓库。",
        )
    status = STATUS_WARN if ok_count else STATUS_FAIL
    return CheckItem(
        "network",
        "资料源连通性",
        status,
        f"{ok_count}/{len(targets)} 可用",
        detail,
        "检查网络、代理或防火墙。离线时仍然可以写代码，但无法自动核对版本和依赖。",
    )


def run_checks(
    *,
    project_root: Path | None = None,
    project_dir: Path | None = None,
    ai: AIConfig | None = None,
    include_network: bool = True,
    tools: ToolPaths | None = None,
) -> list[CheckItem]:
    root = Path(project_root) if project_root is not None else paths.default_project_root()
    if tools is None:
        tools = resolve_tools()
    jobs: list[tuple[str, str, Callable[[], CheckItem]]] = [
        ("java", "Java 运行时", lambda: check_java(tools.java)),
        ("java_home", "JAVA_HOME", check_java_home),
        ("gradle", "Gradle", lambda: check_gradle(project_dir, tools.gradle)),
        ("git", "Git（可选）", lambda: check_git(tools.git)),
        (
            "project_root",
            "工程目录",
            lambda: check_directory(
                "project_root",
                "工程目录",
                root,
                "新建工程会放在这个目录下；如果不可写，请到设置里换一个位置。",
            ),
        ),
        (
            "data_dir",
            "数据目录",
            lambda: check_directory(
                "data_dir",
                "数据目录",
                paths.data_dir(),
                "技能、连接器、知识库、备份和会话都保存在这里。",
            ),
        ),
        ("ai", "模型通道", lambda: check_ai(ai)),
    ]
    if include_network:
        jobs.append(("network", "资料源连通性", check_network))

    items: list[CheckItem] = []
    with ThreadPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        futures = [(key, name, pool.submit(job)) for key, name, job in jobs]
        for key, name, future in futures:
            try:
                items.append(future.result())
            except Exception as exc:
                items.append(
                    CheckItem(
                        key,
                        name,
                        STATUS_FAIL,
                        "检测失败",
                        str(exc),
                        "重试一次；如果持续失败，请把检测报告复制给开发者。",
                    )
                )
    return items


def report_text(items: list[CheckItem]) -> str:
    labels = {STATUS_OK: "通过", STATUS_WARN: "注意", STATUS_FAIL: "缺失", STATUS_INFO: "提示"}
    lines = ["恶魔小猫工坊 · 环境自检"]
    for item in items:
        lines.append(f"[{labels.get(item.status, item.status)}] {item.name}：{item.summary}")
        if item.detail:
            lines.append(f"  {item.detail.replace(chr(10), chr(10) + '  ')}")
        if item.hint:
            lines.append(f"  建议：{item.hint}")
    return "\n".join(lines)
