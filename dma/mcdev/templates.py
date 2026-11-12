"""内置工程骨架与探针源码的本地模板（1.20.1 的 Forge / Fabric / NeoForge）。"""

from __future__ import annotations

import json
import re
import shutil
import sys
import time
from pathlib import Path

from .. import paths
from . import gradle

MC_VERSION = "1.20.1"
FORGE_VERSION = "47.4.28"
NEOFORGE_VERSION = "47.1.106"
PROBE_JAR_NAME = "mcdev-probe-dev.jar"

# 内置骨架覆盖的 MC 版本。Forge 1.20.1 之后被 NeoForge 取代，故 Forge 只有 1.20.1；
# Fabric / NeoForge 额外提供 1.21.1。其他版本由助手按目标版本生成构建脚本。
SCAFFOLD_MC_VERSIONS = ("1.20.1", "1.21.1")
SCAFFOLD_LOADERS = ("Forge", "Fabric", "NeoForge")

TEXT_SUFFIXES = {
    ".java", ".gradle", ".properties", ".toml", ".mcmeta", ".json",
    ".md", ".txt", ".xml", ".yml", ".yaml", ".bat", ".sh",
}


def resources_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        return base / "dma" / "resources"
    return Path(__file__).resolve().parent.parent / "resources"


def _render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _values(
    *,
    mod_id: str,
    mod_name: str,
    package: str,
    mc_version: str,
    mod_version: str,
) -> dict[str, str]:
    package = (package or f"com.example.{mod_id}").strip()
    class_name = "".join(part.capitalize() for part in mod_id.split("_")) + "Mod"
    return {
        "mod_id": mod_id,
        "mod_name": mod_name,
        "package": package,
        "class_name": class_name,
        "mc_version": mc_version,
        "mod_version": mod_version,
        "year": time.strftime("%Y"),
        "java_homes": _java_homes_value(),
    }


def _java_homes_value() -> str:
    """列出本机 JDK，写给 Gradle 的 toolchain 自动探测。

    ModDevGradle 的 createMinecraftArtifacts 要求 Java 21。默认的自动探测
    只扫注册表和固定目录，装在 D 盘这类自定义位置的 JDK 找不到，Gradle 就
    会去 foojay 现场下载；下载失败后只报一句“找不到 Java 21”，很难排查。
    """
    from .. import envcheck

    seen: list[str] = []
    for home in envcheck.java_homes():
        # gradle.properties 是 Java properties 格式，反斜杠会被当成转义符，
        # Windows 路径必须写成正斜杠，否则 D:\developer 会变成 D:developer。
        text = str(home).replace("\\", "/")
        if text and text not in seen:
            seen.append(text)
    return ",".join(seen)


def _copy_template(source: Path, target: Path, values: dict[str, str]) -> None:
    package_path = values["package"].replace(".", "/")
    for item in source.rglob("*"):
        if item.is_dir():
            continue
        relative = item.relative_to(source).as_posix()
        relative = relative.replace("__package__", package_path)
        for key, value in values.items():
            relative = relative.replace("{{" + key + "}}", value)
        is_text = (
            item.suffix.lower() in TEXT_SUFFIXES
            or item.name in {"gradlew", "LICENSE"}
        )
        rendered = ""
        if is_text:
            text = item.read_text(encoding="utf-8", errors="replace")
            rendered = _render(text, values)
        # 模板文件都叫 ModMain.java，但 Fabric / NeoForge 骨架里的公开类
        # 是 <Mod>Mod；文件名不改，javac 会直接报“类 X 是公共的，应在
        # X.java 中声明”。Forge 1.20.1 的入口类确实叫 ModMain，保持原名。
        if item.name == "ModMain.java" and f"class {values['class_name']}" in rendered:
            relative = relative[: -len("ModMain.java")] + f"{values['class_name']}.java"
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if is_text:
            dest.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            shutil.copy2(item, dest)
    # 保留可执行位（Windows 上靠 .bat，Unix 上 gradlew 需要 +x）
    gradlew = target / "gradlew"
    if gradlew.is_file():
        try:
            gradlew.chmod(gradlew.stat().st_mode | 0o111)
        except OSError:
            pass


def supports_scaffold(loader: str, mc_version: str = MC_VERSION) -> bool:
    version = (mc_version or "").strip()
    if loader == "Forge":
        return version == MC_VERSION and loader in SCAFFOLD_LOADERS
    return version in SCAFFOLD_MC_VERSIONS and loader in SCAFFOLD_LOADERS


def restore_incompatible_scaffold(root: str | Path, *, log=None) -> str:
    """1.20.1 工程被改写成 NeoGradle 7 时，用内置骨架恢复构建脚本。

    NeoGradle 7（`net.neoforged.gradle.userdev`）和 `net.neoforged:neoforge`
    坐标都是 1.20.2+ 的写法，1.20.1 用它只会得到“找不到模块”，再怎么调
    runs / repositories 也修不好。这里只处理这一种已确认不兼容的组合，
    原文件先备份到 .dma/backups，返回给调用方写进构建日志。
    """
    project = Path(str(root)).expanduser()
    meta_path = project / ".dma" / "project.json"
    build_file = project / "build.gradle"
    if not meta_path.is_file() or not build_file.is_file():
        return ""
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    if not isinstance(meta, dict):
        return ""
    if str(meta.get("loader") or "").strip().lower() != "neoforge":
        return ""
    if str(meta.get("mc_version") or "").strip() != MC_VERSION:
        return ""
    try:
        text = build_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if (
        "net.neoforged.gradle.userdev" not in text
        and "net.neoforged:neoforge:" not in text
    ):
        return ""

    mod_id = str(meta.get("mod_id") or "").strip() or "mod"
    mod_name = str(meta.get("name") or mod_id).strip() or mod_id
    src_root = project / "src" / "main" / "java"
    package = ""
    if src_root.is_dir():
        # 骨架里除了主入口类还有 content/ 等子包（如 ModRegistries），
        # 按路径排序第一个文件可能是子包里的类；主工程的根包是所有
        # package 声明里最短的那个，用它恢复入口文件位置。
        packages: list[str] = []
        for item in sorted(src_root.rglob("*.java")):
            try:
                head = item.read_text(encoding="utf-8", errors="replace")[:400]
            except OSError:
                continue
            match = re.search(r"^\s*package\s+([\w.]+)\s*;", head, re.MULTILINE)
            if match:
                packages.append(match.group(1))
        if packages:
            package = min(packages, key=len)
    mod_version = "1.0.0"
    props_file = project / "gradle.properties"
    if props_file.is_file():
        try:
            for line in props_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines():
                if line.strip().startswith("mod_version="):
                    mod_version = line.split("=", 1)[1].strip() or mod_version
        except OSError:
            pass

    backup = (
        project
        / ".dma"
        / "backups"
        / f"scaffold-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    for relative in (
        "build.gradle",
        "settings.gradle",
        "gradle.properties",
        "src/main/resources/META-INF/mods.toml",
    ):
        source = project / relative
        if source.is_file():
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    create_neoforge_project(
        project,
        mod_id=mod_id,
        mod_name=mod_name,
        package=package,
        mc_version=MC_VERSION,
        mod_version=mod_version,
    )

    # 旧骨架的 ModMain.java 会和新入口类重复声明同一个类，挪进备份目录。
    if package:
        class_name = "".join(part.capitalize() for part in mod_id.split("_")) + "Mod"
        stray = src_root / package.replace(".", "/") / "ModMain.java"
        if stray.is_file() and (stray.parent / f"{class_name}.java").is_file():
            target = backup / "src/main/java" / package.replace(".", "/") / "ModMain.java"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stray, target)
            stray.unlink()

    notice = (
        f"[dma] {project.name} 的 build.gradle 用了 NeoGradle 7 / "
        "net.neoforged:neoforge（1.20.2+ 才支持），1.20.1 无法解析。"
        f"已用内置骨架恢复构建脚本，原文件备份在 {backup}。"
    )
    if log:
        try:
            log(notice)
        except Exception:  # noqa: BLE001 - 日志回调不能影响构建
            pass
    return notice


def supports_probe(project_root: str | Path) -> bool:
    """探针是 Forge 1.20.1 开发端 mod；其他加载器只能用日志/GameTest 验证。"""
    root = Path(str(project_root)).expanduser()
    loader = ""
    version = ""
    meta_path = root / ".dma" / "project.json"
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            loader = str(data.get("loader") or "").strip()
            version = str(data.get("mc_version") or "").strip()
    if loader or version:
        return loader.lower() == "forge" and version == MC_VERSION

    # 手工打开的工程没有元数据，从构建脚本里认。
    build_gradle = root / "build.gradle"
    properties = root / "gradle.properties"
    text = ""
    props = ""
    try:
        if build_gradle.is_file():
            text = build_gradle.read_text(encoding="utf-8", errors="replace")
        if properties.is_file():
            props = properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if "net.minecraftforge.gradle" not in text:
        return False
    return "minecraft_version=1.20.1" in props or "1.20.1-47" in text


def create_forge_project(
    root: str | Path,
    *,
    mod_id: str,
    mod_name: str,
    package: str = "",
    mc_version: str = MC_VERSION,
    forge_version: str = FORGE_VERSION,
    mod_version: str = "1.0.0",
) -> Path:
    """把内置 Forge 骨架复制到目标目录，并替换 mod 信息。"""
    if (mc_version or "").strip() != MC_VERSION:
        raise ValueError(
            f"内置 Forge 骨架只覆盖 {MC_VERSION}；{mc_version} 请先核对官方模板再生成构建脚本。"
        )
    target = Path(str(root)).expanduser()
    source = resources_dir() / "forge-1.20.1"
    if not source.is_dir():
        raise FileNotFoundError(f"内置 Forge 模板缺失：{source}")
    values = _values(
        mod_id=mod_id,
        mod_name=mod_name,
        package=package,
        mc_version=mc_version,
        mod_version=mod_version,
    )
    values["forge_version"] = forge_version
    _copy_template(source, target, values)
    return target


def create_fabric_project(
    root: str | Path,
    *,
    mod_id: str,
    mod_name: str,
    package: str = "",
    mc_version: str = MC_VERSION,
    mod_version: str = "1.0.0",
) -> Path:
    """生成 Fabric 骨架：fabric-loom + Yarn + ModInitializer。"""
    version = (mc_version or "").strip()
    if version not in SCAFFOLD_MC_VERSIONS:
        raise ValueError(
            f"内置 Fabric 骨架只覆盖 {', '.join(SCAFFOLD_MC_VERSIONS)}；"
            f"{mc_version} 请先核对 Yarn / Loom 版本再生成。"
        )
    target = Path(str(root)).expanduser()
    source = resources_dir() / f"fabric-{version}"
    if not source.is_dir():
        raise FileNotFoundError(f"内置 Fabric 模板缺失：{source}")
    values = _values(
        mod_id=mod_id,
        mod_name=mod_name,
        package=package,
        mc_version=mc_version,
        mod_version=mod_version,
    )
    _copy_template(source, target, values)
    return target


def create_neoforge_project(
    root: str | Path,
    *,
    mod_id: str,
    mod_name: str,
    package: str = "",
    mc_version: str = MC_VERSION,
    neo_version: str = NEOFORGE_VERSION,
    mod_version: str = "1.0.0",
) -> Path:
    """生成 NeoForge 骨架：NeoGradle + mods.toml + DeferredRegister 入口。"""
    version = (mc_version or "").strip()
    if version not in SCAFFOLD_MC_VERSIONS:
        raise ValueError(
            f"内置 NeoForge 骨架只覆盖 {', '.join(SCAFFOLD_MC_VERSIONS)}；"
            f"{mc_version} 请先核对 NeoGradle / NeoForge 版本再生成。"
        )
    target = Path(str(root)).expanduser()
    source = resources_dir() / f"neoforge-{version}"
    if not source.is_dir():
        raise FileNotFoundError(f"内置 NeoForge 模板缺失：{source}")
    values = _values(
        mod_id=mod_id,
        mod_name=mod_name,
        package=package,
        mc_version=mc_version,
        mod_version=mod_version,
    )
    values["neo_version"] = neo_version
    _copy_template(source, target, values)
    return target


def create_scaffold(
    loader: str,
    root: str | Path,
    *,
    mod_id: str,
    mod_name: str,
    package: str = "",
    mc_version: str = MC_VERSION,
    mod_version: str = "1.0.0",
) -> Path:
    """按加载器分派内置骨架；Architectury 不内置，交给助手按官方模板生成。"""
    if loader == "Forge":
        return create_forge_project(
            root,
            mod_id=mod_id,
            mod_name=mod_name,
            package=package,
            mc_version=mc_version,
            mod_version=mod_version,
        )
    if loader == "Fabric":
        return create_fabric_project(
            root,
            mod_id=mod_id,
            mod_name=mod_name,
            package=package,
            mc_version=mc_version,
            mod_version=mod_version,
        )
    if loader == "NeoForge":
        return create_neoforge_project(
            root,
            mod_id=mod_id,
            mod_name=mod_name,
            package=package,
            mc_version=mc_version,
            mod_version=mod_version,
        )
    raise ValueError(
        f"{loader} 没有内置骨架；请用官方模板生成，或让助手先写构建脚本。"
    )


def _probe_cache() -> Path:
    return paths.data_dir() / "cache" / PROBE_JAR_NAME


def find_probe_jar(project_root: str | Path | None = None) -> Path | None:
    candidates: list[Path] = [_probe_cache(), resources_dir() / PROBE_JAR_NAME]
    if project_root is not None:
        libs = Path(str(project_root)).expanduser() / ".mcdev" / "probe" / "build" / "libs"
        if libs.is_dir():
            candidates.extend(sorted(libs.glob("*dev.jar")))
    for item in candidates:
        if item.is_file():
            return item
    return None


def build_probe(
    project_root: str | Path,
    *,
    log=None,
    cancel=None,
) -> Path:
    """在工程 .mcdev/probe 下编译内置探针源码，返回 dev jar。"""
    root = Path(str(project_root)).expanduser()
    source = resources_dir() / "mcdev-probe"
    if not source.is_dir():
        raise FileNotFoundError(
            "没有内置探针 JAR，也没有探针源码。请把 mcdev-probe-dev.jar 放到 "
            f"{resources_dir()}。"
        )
    target = root / ".mcdev" / "probe"
    if not (target / "build.gradle").is_file():
        target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if item.is_dir():
                continue
            relative = item.relative_to(source)
            dest = target / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
        try:
            (target / "gradlew").chmod(0o755)
        except OSError:
            pass
    if log:
        log("[mcdev] 首次使用需要编译探针，可能要几分钟…")
    code, output = gradle.run_gradle(
        target, "devJar", timeout=1800, cancel=cancel, log=log
    )
    if code != 0:
        tail = output[-4000:]
        raise RuntimeError(f"探针编译失败（退出码 {code}）：\n{tail}")
    libs = target / "build" / "libs"
    jars = sorted(libs.glob("*dev.jar")) if libs.is_dir() else []
    if not jars:
        raise RuntimeError(f"探针编译完成，但没有找到 dev jar：{libs}")
    # 先放到用户数据目录，换工程也不用重编；程序目录只作只读兜底。
    for cached in (_probe_cache(), resources_dir() / PROBE_JAR_NAME):
        try:
            cached.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(jars[-1], cached)
            break
        except OSError:
            continue
    return jars[-1]
