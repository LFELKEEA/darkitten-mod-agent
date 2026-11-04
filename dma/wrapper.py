from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from . import paths

DEFAULT_DISTRIBUTION = "8.10.2"

PROPERTIES = """distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip
networkTimeout=10000
validateDistributionUrl=true
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
"""


class WrapperError(Exception):
    pass


def assets_dir() -> Path:
    return paths.resource_dir() / "wrapper"


def has_wrapper(project_root: Path | None) -> bool:
    if project_root is None:
        return False
    root = Path(project_root)
    return (root / "gradlew.bat").is_file() or (root / "gradlew").is_file()


def ensure_wrapper(project_root: Path, version: str = DEFAULT_DISTRIBUTION) -> list[str]:
    root = Path(project_root)
    if not root.is_dir():
        raise WrapperError(f"工程目录不存在：{root}")
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
