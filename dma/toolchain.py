from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import deps, envcheck, gradle, paths, wrapper

USER_AGENT = "DarkittenModAgent/0.1 (+https://lfelkeea.github.io/darkitten-launcher/)"
DOWNLOAD_TIMEOUT = 30
CHUNK = 1024 * 1024

GRADLE_MIRROR = "https://repo.huaweicloud.com/gradle/{name}"
TEMURIN_INDEX = (
    "https://mirrors.ustc.edu.cn/adoptium/releases/temurin{major}-binaries/LatestRelease/"
)
HUAWEI_OPENJDK = "https://mirrors.huaweicloud.com/openjdk/"
ADOPTIUM_API = (
    "https://api.adoptium.net/v3/binary/latest/{major}/ga/windows/x64/jdk/"
    "hotspot/normal/eclipse"
)

JAVA_SEARCH_FILE = "java.exe" if os.name == "nt" else "java"
Log = Callable[[str], None]


class ToolchainError(Exception):
    pass


@dataclass
class JavaInstall:
    home: Path
    version: str
    major: int
    source: str = ""


@dataclass
class Distribution:
    url: str
    name: str
    version: str
    cache: Path
    home: Path
    marker: Path

    @property
    def executable(self) -> Path:
        return self.home / "bin" / ("gradle.bat" if os.name == "nt" else "gradle")

    def installed(self) -> bool:
        return self.executable.is_file() and self.marker.is_file()


@dataclass
class Prepared:
    java_home: Path | None = None
    java_version: str = ""
    gradle_home: Path | None = None
    gradle_version: str = ""
    wrapper_written: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------
# 基础工具


def _log(log: Log | None, text: str) -> None:
    if log is None:
        return
    try:
        log(text)
    except Exception:
        pass


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fetch_text(url: str, timeout: int = 20, limit: int = 2_000_000) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(limit + 1)
    return raw[:limit].decode("utf-8", "replace")


def _download_one(url: str, dest: Path, log: Log | None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    resume = part.stat().st_size if part.is_file() else 0
    headers = {"User-Agent": USER_AGENT}
    if resume:
        headers["Range"] = f"bytes={resume}-"
    request = urllib.request.Request(url, headers=headers)
    try:
        response = urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and resume:
            part.replace(dest)
            return
        raise

    with response:
        status = getattr(response, "status", 200)
        length = response.headers.get("Content-Length")
        if resume and status != 206:
            resume = 0
        total = int(length) + resume if length else 0
        mode = "ab" if resume else "wb"
        done = resume
        started = time.monotonic()
        last_report = 0.0
        with part.open(mode) as handle:
            while True:
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                handle.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_report >= 2.0:
                    last_report = now
                    elapsed = max(0.001, now - started)
                    speed = (done - resume) / elapsed / 1024 / 1024
                    percent = f"{done * 100 // total}% · " if total else ""
                    total_text = f" / {_human(total)}" if total else ""
                    _log(
                        log,
                        f"[准备] 下载 {percent}{_human(done)}{total_text} · {speed:.1f} MB/s",
                    )
        if total and done < total:
            raise ToolchainError(f"下载不完整（{_human(done)} / {_human(total)}）")
    part.replace(dest)


def download(
    url: str,
    dest: Path,
    *,
    log: Log | None = None,
    sha256: str = "",
    mirrors: tuple[str, ...] | list[str] = (),
) -> Path:
    """依次尝试镜像和官方地址；支持断点续传和 SHA-256 校验。"""
    dest = Path(dest)
    if dest.is_file() and (not sha256 or sha256_file(dest) == sha256):
        return dest
    candidates = [*mirrors, url]
    last: Exception | None = None
    for candidate in candidates:
        try:
            _download_one(candidate, dest, log)
        except Exception as exc:  # noqa: BLE001 - 逐个源降级
            last = exc
            _log(log, f"[准备] 来源不可用：{urllib.parse.urlparse(candidate).hostname} · {exc}")
            continue
        if sha256:
            actual = sha256_file(dest)
            if actual.lower() != sha256.lower():
                part = dest.with_name(dest.name + ".part")
                dest.unlink(missing_ok=True)
                part.unlink(missing_ok=True)
                last = ToolchainError("SHA-256 校验失败")
                _log(log, f"[准备] {urllib.parse.urlparse(candidate).hostname} 的文件校验失败，换下一个源")
                continue
            _log(log, "[准备] SHA-256 校验通过")
        return dest
    raise ToolchainError(f"下载失败：{last}")


def _extract_zip(archive: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    root = target.resolve()
    with zipfile.ZipFile(archive) as zf:
        broken = zf.testzip()
        if broken:
            raise ToolchainError(f"压缩包损坏：{broken}")
        for member in zf.infolist():
            path = (target / member.filename).resolve()
            try:
                path.relative_to(root)
            except ValueError as exc:
                raise ToolchainError("压缩包包含异常路径") from exc
        zf.extractall(target)


# ----------------------------------------------------------------------
# JDK


def java_user_home() -> Path:
    value = os.environ.get("JAVA_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home()


def find_java(homes: list[Path] | None = None) -> JavaInstall | None:
    found: list[JavaInstall] = []
    for home in envcheck.java_homes() if homes is None else homes:
        version = envcheck.java_version_at(home) or envcheck.probe_java_home(home)
        if not version:
            continue
        found.append(
            JavaInstall(
                home=Path(home),
                version=version,
                major=envcheck.java_major(version),
                source="installed",
            )
        )
    if not found:
        return None
    return min(found, key=lambda item: (envcheck.java_rank(item.major), -item.major))


def _temurin_sources(major: int) -> list[tuple[str, str, str]]:
    sources: list[tuple[str, str, str]] = []

    index = TEMURIN_INDEX.format(major=major)
    try:
        html = _fetch_text(index)
    except Exception:
        html = ""
    pattern = re.compile(
        rf'href="([^"]*/OpenJDK{major}U-jdk_x64_windows_hotspot_[^"/]+\.zip)"'
    )
    entries: list[tuple[tuple[int, ...], str, str]] = []
    for href in pattern.findall(html):
        name = href.rsplit("/", 1)[-1]
        if "debugimage" in name or "aarch64" in name:
            continue
        match = re.search(r"hotspot_(\d+(?:\.\d+)+)", name)
        key = gradle.version_tuple(match.group(1)) if match else ()
        entries.append((key, urllib.parse.urljoin(index, href), name))
    for _, url, _ in sorted(entries, reverse=True):
        sources.append((url, url + ".sha256.txt", "中科大镜像"))

    try:
        html = _fetch_text(HUAWEI_OPENJDK)
    except Exception:
        html = ""
    versions = sorted(
        {
            item
            for item in re.findall(r'href="(\d+(?:\.\d+)+)/"', html)
            if item.split(".")[0] == str(major)
        },
        key=gradle.version_tuple,
    )
    if versions:
        version = versions[-1]
        name = f"openjdk-{version}_windows-x64_bin.zip"
        sources.append(
            (f"{HUAWEI_OPENJDK}{version}/{name}", "", "华为云镜像")
        )

    sources.append((ADOPTIUM_API.format(major=major), "", "Adoptium 官方"))
    unique: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for item in sources:
        if item[0] not in seen:
            seen.add(item[0])
            unique.append(item)
    return unique


def install_jdk_zip(archive: Path, major: int) -> Path:
    tools = paths.tools_dir()
    staging = Path(tempfile.mkdtemp(prefix=".jdk-", dir=str(tools)))
    try:
        _extract_zip(archive, staging)
        home = _find_java_home(staging)
        if home is None:
            raise ToolchainError("压缩包里没有找到 bin/java")
        version = envcheck.java_version_at(home) or f"{major}"
        target = tools / f"jdk-{version}"
        if target.exists():
            if envcheck.java_executable(target) is not None:
                return target
            target = tools / f"jdk-{version}-{int(time.time())}"
        shutil.move(str(home), str(target))
        (target / ".darkitten-jdk").write_text(
            f"downloaded by {USER_AGENT}\n", encoding="utf-8"
        )
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _find_java_home(root: Path) -> Path | None:
    if envcheck.java_executable(root) is not None:
        return root
    try:
        level1 = list(root.iterdir())
    except OSError:
        return None
    for first in level1:
        if not first.is_dir():
            continue
        if envcheck.java_executable(first) is not None:
            return first
        try:
            level2 = list(first.iterdir())
        except OSError:
            continue
        for second in level2:
            if second.is_dir() and envcheck.java_executable(second) is not None:
                return second
    return None


def download_temurin(major: int = 21, log: Log | None = None) -> Path:
    downloads = paths.tools_dir() / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for url, sha_url, label in _temurin_sources(major):
        name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1] or f"temurin-{major}.zip"
        if not name.lower().endswith(".zip"):
            name = f"temurin-{major}.zip"
        dest = downloads / name
        digest = ""
        if sha_url:
            try:
                text = _fetch_text(sha_url, timeout=15)
                match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
                digest = match.group(1) if match else ""
            except Exception:
                digest = ""
        _log(log, f"[准备] 从{label}下载 JDK {major}…")
        try:
            archive = download(url, dest, log=log, sha256=digest)
            home = install_jdk_zip(archive, major)
        except Exception as exc:  # noqa: BLE001 - 逐个源降级
            last = exc
            _log(log, f"[准备] {label}不可用：{exc}")
            continue
        archive.unlink(missing_ok=True)
        envcheck.java_homes(refresh=True)
        _log(log, f"[准备] JDK {major} 已安装：{home}")
        return home
    raise ToolchainError(f"JDK {major} 下载失败：{last}")


def ensure_java(log: Log | None = None, allow_download: bool = True) -> JavaInstall | None:
    install = find_java()
    if install is not None and 17 <= install.major <= 25:
        _log(log, f"[准备] 使用 Java {install.version}：{install.home}")
        return install
    if install is None:
        _log(log, "[准备] 本机没有找到 JDK")
    else:
        _log(
            log,
            f"[准备] 本机 Java {install.version} 不适合模组构建，需要 JDK 21",
        )
    if not allow_download:
        return install
    try:
        home = download_temurin(21, log=log)
    except Exception as exc:  # noqa: BLE001 - 下载失败时退回本机 JDK
        _log(log, f"[准备] 自动下载 JDK 失败：{exc}")
        return install
    version = envcheck.java_version_at(home) or "21"
    return JavaInstall(
        home=home,
        version=version,
        major=envcheck.java_major(version),
        source="download",
    )


# ----------------------------------------------------------------------
# Gradle


def _base36(number: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    result = ""
    while number:
        number, remainder = divmod(number, 36)
        result = digits[remainder] + result
    return result or "0"


def distribution_url(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    props = Path(project_root) / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        text = props.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip().startswith("distributionUrl="):
            value = line.split("=", 1)[1].strip()
            return re.sub(r"\\(.)", r"\1", value)
    return ""


def distribution_sha256(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    props = Path(project_root) / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        text = props.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip().startswith("distributionSha256Sum="):
            return line.split("=", 1)[1].strip().lower()
    return ""


def default_distribution_url(version: str | None = None) -> str:
    return (
        "https://services.gradle.org/distributions/"
        f"gradle-{version or wrapper.DEFAULT_DISTRIBUTION}-bin.zip"
    )


def gradle_user_home() -> Path:
    value = os.environ.get("GRADLE_USER_HOME", "").strip()
    return Path(value).expanduser() if value else Path.home() / ".gradle"


def distribution_for(url: str, user_home: Path | None = None) -> Distribution | None:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name
    if not name.lower().endswith(".zip"):
        return None
    base = name[:-4]
    match = re.match(r"gradle-(\d+(?:\.\d+)*)", base)
    if not match:
        return None
    digest = hashlib.md5(url.encode("utf-8")).digest()
    key = _base36(int.from_bytes(digest, "big"))
    version = match.group(1)
    cache = (user_home or gradle_user_home()) / "wrapper" / "dists" / base / key
    return Distribution(
        url=url,
        name=base,
        version=version,
        cache=cache,
        home=cache / f"gradle-{version}",
        marker=cache / f"{base}.zip.ok",
    )


def install_distribution_zip(archive: Path, dist: Distribution) -> None:
    if dist.executable.is_file():
        dist.marker.write_text("installed by Darkitten Mod Agent\n", encoding="utf-8")
        return
    dist.cache.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=str(dist.cache)))
    try:
        _extract_zip(archive, staging)
        source = staging / dist.home.name
        if not source.is_dir():
            found = None
            for child in staging.iterdir():
                if child.is_dir() and (child / "bin").is_dir():
                    found = child
                    break
            if found is None:
                raise ToolchainError("Gradle 压缩包里没有找到发行目录")
            source = found
        if dist.home.exists():
            broken = dist.cache / f"{dist.name}.broken-{int(time.time())}"
            dist.home.replace(broken)
        shutil.move(str(source), str(dist.home))
        dist.marker.write_text("installed by Darkitten Mod Agent\n", encoding="utf-8")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def ensure_distribution(
    project_root: Path,
    *,
    log: Log | None = None,
    allow_download: bool = True,
) -> Distribution | None:
    url = distribution_url(project_root) or default_distribution_url()
    dist = distribution_for(url)
    if dist is None:
        _log(log, "[准备] Wrapper 里的 Gradle 地址无法识别，交给 gradlew 自行处理")
        return None
    if dist.installed():
        _log(log, f"[准备] Gradle {dist.version} 已安装：{dist.home}")
        return dist
    if not allow_download:
        return dist

    archive = dist.cache / f"{dist.name}.zip"
    expect = distribution_sha256(project_root)
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    mirrors: list[str] = []
    if host in ("services.gradle.org", "downloads.gradle.org"):
        mirrors.append(GRADLE_MIRROR.format(name=archive.name))
    _log(log, f"[准备] 开始下载 Gradle {dist.version}…")
    try:
        download(url, archive, log=log, sha256=expect, mirrors=mirrors)
        _log(log, f"[准备] 解压 Gradle {dist.version}…")
        install_distribution_zip(archive, dist)
    except Exception as exc:
        _log(log, f"[准备] Gradle {dist.version} 自动安装失败：{exc}")
        _log(log, "[准备] 会继续交给 gradlew 自己下载，首次编译可能比较慢")
        return dist
    archive.unlink(missing_ok=True)
    _log(log, f"[准备] Gradle {dist.version} 已就绪：{dist.home}")
    return dist


# ----------------------------------------------------------------------
# 组合流程


def wrapper_ready(project_root: Path) -> bool:
    root = Path(project_root)
    wrapper_dir = root / "gradle" / "wrapper"
    return (
        wrapper.has_wrapper(root)
        and (wrapper_dir / "gradle-wrapper.jar").is_file()
        and wrapper.properties_valid(root)
    )


def ensure_wrapper_files(project_root: Path, version: str | None = None) -> list[str]:
    """补齐缺失的 Wrapper 文件；配置损坏时重写，版本正常时不覆盖。"""
    root = Path(project_root)
    target_version = version or wrapper.compatible_version(root)[0] or wrapper.DEFAULT_DISTRIBUTION
    source = wrapper.assets_dir()
    required = ("gradlew", "gradlew.bat", "gradle-wrapper.jar")
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise ToolchainError("程序缺少 Wrapper 资源：" + "、".join(missing))

    written: list[str] = []
    for name in ("gradlew", "gradlew.bat"):
        if not (root / name).is_file():
            shutil.copyfile(source / name, root / name)
            written.append(name)

    wrapper_dir = root / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    jar = wrapper_dir / "gradle-wrapper.jar"
    if not jar.is_file():
        shutil.copyfile(source / "gradle-wrapper.jar", jar)
        written.append("gradle/wrapper/gradle-wrapper.jar")

    props = wrapper_dir / "gradle-wrapper.properties"
    if not props.is_file() or not wrapper.properties_valid(root):
        props.write_text(
            wrapper.PROPERTIES.format(version=target_version),
            encoding="utf-8",
        )
        written.append("gradle/wrapper/gradle-wrapper.properties")

    if os.name != "nt" and (root / "gradlew").is_file():
        mode = (root / "gradlew").stat().st_mode
        (root / "gradlew").chmod(mode | 0o111)
    return written


def with_java_home(env: dict[str, str], java_home: Path | str | None) -> dict[str, str]:
    if not java_home:
        return env
    return gradle.build_env(base=env, java_home=java_home)


def prepare(
    project_root: Path,
    *,
    log: Log | None = None,
    allow_download: bool = True,
    extra_jar_dirs: list[Path] | None = None,
) -> Prepared:
    root = Path(project_root)
    if not root.is_dir():
        raise ToolchainError(f"工程目录不存在：{root}")
    result = Prepared()

    try:
        jars = deps.ensure_local_jars(
            root, log=log, extra_dirs=extra_jar_dirs or ()
        )
        if jars.missing:
            result.notes.append(
                "缺少本地 jar 依赖："
                + "、".join(jars.missing)
                + "。把文件放进下载文件夹或工程的 libs/ 后重新点「一键准备」。"
            )
    except Exception as exc:  # noqa: BLE001 - 本地依赖不阻塞构建
        result.notes.append(f"自动整理 libs 失败：{exc}")

    install = ensure_java(log=log, allow_download=allow_download)
    if install is not None:
        result.java_home = install.home
        result.java_version = install.version
        if not 17 <= install.major <= 25:
            result.notes.append(
                f"当前只有 Java {install.version}，Gradle 可能不兼容；建议再运行一次一键准备。"
            )
    else:
        result.notes.append("没有可用的 JDK，Gradle 可能无法启动。")

    if not wrapper_ready(root):
        try:
            result.wrapper_written = ensure_wrapper_files(root)
            if result.wrapper_written:
                _log(log, "[准备] 已补齐 Gradle Wrapper 文件")
            else:
                _log(log, "[准备] Gradle Wrapper 已完整")
        except Exception as exc:  # noqa: BLE001 - 不阻塞后续步骤
            result.notes.append(f"写入 Wrapper 失败：{exc}")
    else:
        _log(log, "[准备] Gradle Wrapper 已存在")

    try:
        changed, _before, after, _reason = wrapper.ensure_compatible(root, log=log)
        if changed:
            result.notes.append(f"已自动把 Wrapper 调整为 Gradle {after}")
    except Exception as exc:  # noqa: BLE001 - 版本修正失败时保留原配置
        result.notes.append(f"自动调整 Wrapper 版本失败：{exc}")

    dist = ensure_distribution(root, log=log, allow_download=allow_download)
    if dist is not None:
        result.gradle_version = dist.version
        if dist.installed():
            result.gradle_home = dist.home
        elif allow_download:
            result.notes.append(f"Gradle {dist.version} 没有下载完成，gradlew 会再试一次。")
    return result
