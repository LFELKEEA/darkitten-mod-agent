from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Callable

from . import paths

DEFAULT_DISTRIBUTION = "9.8.2"

# ModDevGradle 2.x 官方只声明兼容 Gradle 8.x，9.x 还没验证。
MODDEV_DISTRIBUTION = "8.10.2"

PROPERTIES = """distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https://services.gradle.org/distributions/gradle-{version}-bin.zip
networkTimeout=10000
validateDistributionUrl=true
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
"""

BUILD_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")

_PLUGIN_PATTERNS = {
    "moddev": re.compile(r"net\.neoforged\.moddev"),
    "neogradle": re.compile(r"net\.neoforged\.gradle(?:\.userdev)?"),
    "loom": re.compile(r"fabric-loom"),
}

Log = Callable[[str], None]


class WrapperError(Exception):
    pass


def assets_dir() -> Path:
    return paths.resource_dir() / "wrapper"


def has_wrapper(project_root: Path | None) -> bool:
    if project_root is None:
        return False
    root = Path(project_root)
    return (root / "gradlew.bat").is_file() or (root / "gradlew").is_file()


def current_distribution(project_root: Path | None) -> str:
    """读取工程 Wrapper 里配置的 Gradle 版本，读不到就返回空字符串。"""
    if project_root is None:
        return ""
    properties = Path(project_root) / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        text = properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.strip().startswith("distributionUrl="):
            value = line.split("=", 1)[1].strip()
            name = value.rsplit("/", 1)[-1]
            if name.startswith("gradle-"):
                name = name[len("gradle-"):]
            if name.endswith("-bin.zip") or name.endswith("-all.zip"):
                name = name.rsplit("-", 1)[0]
            return name
    return ""


def properties_valid(project_root: Path | None) -> bool:
    """Wrapper 配置既能读到版本，URL 也必须是合法 scheme。

    早期生成的 properties 里写着 ``https\\://``（YAML 风格的转义），
    gradlew 会把它当成非法 URL 直接失败；这里一并判为无效，让上层重写。
    """
    if project_root is None:
        return False
    properties = Path(project_root) / "gradle" / "wrapper" / "gradle-wrapper.properties"
    try:
        text = properties.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    url = ""
    for line in text.splitlines():
        if line.strip().startswith("distributionUrl="):
            url = line.split("=", 1)[1].strip()
            break
    if not url or "\\" in url or not url.startswith("https://"):
        return False
    return bool(current_distribution(project_root))


def build_stack(project_root: Path | None) -> dict[str, str]:
    """识别构建脚本用了哪些 Gradle 插件，返回 {插件类型: 版本或空字符串}。"""
    if project_root is None:
        return {}
    root = Path(project_root)
    texts: list[str] = []
    for name in BUILD_FILES:
        path = root / name
        try:
            texts.append(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            continue
    text = "\n".join(texts)
    if not text:
        return {}
    found: dict[str, str] = {}
    for key, pattern in _PLUGIN_PATTERNS.items():
        if not pattern.search(text):
            continue
        version = ""
        for match in re.finditer(pattern, text):
            tail = text[match.end(): match.end() + 80]
            item = re.search(r"['\"]([0-9][^'\"]*)['\"]", tail)
            if item:
                version = item.group(1)
                break
        found[key] = version
    return found


def compatible_version(project_root: Path | None) -> tuple[str, str]:
    """返回 (推荐 Gradle 版本, 原因)。空字符串表示保持工程现有配置。"""
    current = current_distribution(project_root) if properties_valid(project_root) else ""
    stack = build_stack(project_root)
    if "moddev" in stack:
        version = stack["moddev"] or "2.x"
        if not current:
            return MODDEV_DISTRIBUTION, f"ModDevGradle {version} 只兼容 Gradle 8.x"
        if current.split(".")[0] != "8":
            return (
                MODDEV_DISTRIBUTION,
                f"ModDevGradle {version} 只兼容 Gradle 8.x，当前是 {current}",
            )
        return "", ""
    if "neogradle" in stack:
        version = stack["neogradle"] or "7.x"
        if not current:
            return MODDEV_DISTRIBUTION, f"NeoGradle {version} 建议使用 Gradle 8.x"
        if current.split(".")[0] != "8":
            return (
                MODDEV_DISTRIBUTION,
                f"NeoGradle {version} 建议使用 Gradle 8.x，当前是 {current}",
            )
        return "", ""
    if not current:
        return DEFAULT_DISTRIBUTION, "Wrapper 版本缺失或配置损坏"
    return "", ""


def set_distribution(project_root: Path, version: str) -> list[str]:
    """只改 gradle-wrapper.properties 里的发行版，不动 gradlew 和 jar。"""
    folder = Path(project_root) / "gradle" / "wrapper"
    folder.mkdir(parents=True, exist_ok=True)
    props = folder / "gradle-wrapper.properties"
    try:
        text = props.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    url = (
        "https://services.gradle.org/distributions/"
        f"gradle-{version}-bin.zip"
    )
    if "distributionUrl=" not in text:
        props.write_text(PROPERTIES.format(version=version), encoding="utf-8")
        return ["gradle/wrapper/gradle-wrapper.properties"]
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("distributionUrl="):
            lines.append(f"distributionUrl={url}")
        elif stripped.startswith("distributionSha256Sum="):
            continue
        else:
            lines.append(line)
    props.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return ["gradle/wrapper/gradle-wrapper.properties"]


def ensure_compatible(
    project_root: Path, *, log: Log | None = None
) -> tuple[bool, str, str, str]:
    """让 Wrapper 版本与构建脚本匹配，返回 (是否改动, 原版本, 新版本, 原因)。"""
    current = current_distribution(project_root) if properties_valid(project_root) else ""
    target, reason = compatible_version(project_root)
    if not target or target == current:
        return False, current, current, ""
    set_distribution(project_root, target)
    if log is not None:
        try:
            log(
                f"[准备] Gradle Wrapper：{current or '缺失/损坏'} → {target}"
                f"（{reason}）"
            )
        except Exception:
            pass
    return True, current, target, reason


def ensure_wrapper(project_root: Path, version: str | None = None) -> list[str]:
    root = Path(project_root)
    if not root.is_dir():
        raise WrapperError(f"工程目录不存在：{root}")
    if not version:
        version = compatible_version(root)[0] or DEFAULT_DISTRIBUTION
    source = assets_dir()
    required = ("gradlew", "gradlew.bat", "gradle-wrapper.jar")
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise WrapperError("程序缺少 Wrapper 资源：" + "、".join(missing))

    written: list[str] = []
    for name in ("gradlew", "gradlew.bat"):
        shutil.copyfile(source / name, root / name)
        written.append(name)

    wrapper_dir = root / "gradle" / "wrapper"
    wrapper_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / "gradle-wrapper.jar", wrapper_dir / "gradle-wrapper.jar")
    written.append("gradle/wrapper/gradle-wrapper.jar")

    properties = wrapper_dir / "gradle-wrapper.properties"
    properties.write_text(PROPERTIES.format(version=version), encoding="utf-8")
    written.append("gradle/wrapper/gradle-wrapper.properties")

    if os.name != "nt":
        mode = (root / "gradlew").stat().st_mode
        (root / "gradlew").chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return written
