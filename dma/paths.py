from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

APP_ID = "DarkittenModAgent"

# 按 DMA_DATA_DIR 的取值缓存结果：测试会为每个用例切换到新的临时目录。
_DATA_DIR_CACHE: dict[str, Path] = {}


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", app_dir()))
    assets = base / "assets"
    return assets if assets.is_dir() else app_dir() / "assets"


def _ensure_writable(path: Path) -> Path | None:
    """建目录并真写一个探针文件，确认不是「能 mkdir 但写不进去」。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".dma-write-test"
        with probe.open("wb") as handle:
            handle.write(b"ok")
        probe.unlink()
    except OSError:
        return None
    try:
        return path.resolve()
    except OSError:
        return path


def _data_dir_candidates(override: str) -> list[Path]:
    items: list[Path] = []
    if override:
        items.append(Path(override).expanduser())
    items.append(app_dir() / "data")
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        items.append(Path(local) / APP_ID)
    items.append(Path.home() / APP_ID)
    items.append(Path(tempfile.gettempdir()) / APP_ID)
    return items


def data_dir() -> Path:
    """返回可写入的数据目录。

    某些安全软件会阻止未签名的程序往某个盘写文件，用户的安装目录也可能在只读盘上。
    以前这里直接 mkdir，失败就抛 PermissionError，整个程序连窗口都出不来。
    现在依次尝试「显式指定目录 → 程序目录/data → %LOCALAPPDATA% → 用户目录 → 临时目录」，
    只要有一个能写就继续启动；全部失败才抛错，由界面给出明确提示。
    """
    override = os.environ.get("DMA_DATA_DIR", "").strip()
    cached = _DATA_DIR_CACHE.get(override)
    if cached is not None:
        return cached
    for candidate in _data_dir_candidates(override):
        chosen = _ensure_writable(candidate)
        if chosen is not None:
            _DATA_DIR_CACHE[override] = chosen
            return chosen
    raise OSError(
        "找不到可写入的程序数据目录。请把程序放到有写入权限的位置，"
        "或在安全软件里允许它写入磁盘后重试。"
    )


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
