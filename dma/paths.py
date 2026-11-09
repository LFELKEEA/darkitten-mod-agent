from __future__ import annotations

import os
import sys
from pathlib import Path

APP_ID = "DarkittenModAgent"


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    assets = base / "assets"
    return assets if assets.is_dir() else app_dir() / "assets"


def data_dir() -> Path:
    override = os.environ.get("DMA_DATA_DIR", "").strip()
    path = Path(override).expanduser() if override else app_dir() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def tools_dir() -> Path:
    """程序自己下载的 JDK / 缓存，放在 data 目录里，更新程序时不会被覆盖。"""
    path = data_dir() / "tools"
    path.mkdir(parents=True, exist_ok=True)
    return path


def gradle_home() -> Path:
    """Gradle 用户目录默认放在程序的 data 下。

    Gradle 默认写 ~/.gradle，会把发行版、依赖和 NeoForm 缓存全堆到 C 盘用户目录。
    显式设置了 GRADLE_USER_HOME 时仍以用户的设置为准。
    """
    override = os.environ.get("GRADLE_USER_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    path = data_dir() / "gradle"
    path.mkdir(parents=True, exist_ok=True)
    return path


def temp_dir() -> Path:
    """构建和解析用的临时文件也放在程序目录里，不写系统 Temp。"""
    path = data_dir() / "temp"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.json"


def memory_path() -> Path:
    return data_dir() / "memory.db"


def knowledge_path() -> Path:
    return data_dir() / "knowledge.db"


def skills_dir() -> Path:
    path = data_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def connectors_path() -> Path:
    return data_dir() / "connectors.json"


def projects_path() -> Path:
    return data_dir() / "projects.json"


def sessions_dir() -> Path:
    path = data_dir() / "sessions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_dir() -> Path:
    path = data_dir() / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def updates_dir() -> Path:
    path = data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_project_root() -> Path:
    # 默认工程放在程序目录的 data/projects 下：
    # 1. 不占用 C 盘用户目录；2. data 目录在启动器更新时会被保留。
    root = data_dir() / "projects"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        root = Path.home() / "DarkittenModProjects"
        root.mkdir(parents=True, exist_ok=True)
    return root


def legacy_project_roots() -> tuple[Path, ...]:
    """旧版本默认工程目录，仅用于配置迁移。"""
    return (
        Path.home() / "Documents" / "DarkittenModProjects",
        Path.home() / "DarkittenModProjects",
    )


def desktop_dir() -> Path:
    if os.name == "nt":
        try:
            import subprocess

            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "[Environment]::GetFolderPath('Desktop')",
                ],
                capture_output=True,
                text=True,
                timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            value = (result.stdout or "").strip()
            if value:
                return Path(value)
        except Exception:
            pass
        profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
        for name in ("Desktop", "桌面"):
            candidate = profile / name
            if candidate.is_dir():
                return candidate
        return profile / "Desktop"
    return Path.home() / "Desktop"
