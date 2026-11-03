from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

from . import paths
from .version import APP_NAME

SHORTCUT_NAME = "Darkitten Mod Agent.lnk"


class ShortcutError(Exception):
    pass


def shortcut_path() -> Path:
    return paths.desktop_dir() / SHORTCUT_NAME


def shortcut_exists() -> bool:
    try:
        return shortcut_path().is_file()
    except Exception:
        return False


def _ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _launch_target() -> tuple[Path, str]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve(), ""
    script = paths.app_dir() / "main.py"
    if not script.is_file():
        raise ShortcutError(f"找不到启动脚本：{script}")
    executable = Path(sys.executable).resolve()
    if executable.name.lower() == "python.exe":
        windowless = executable.with_name("pythonw.exe")
        if windowless.is_file():
            executable = windowless
    return executable, f'"{script}"'


def create_desktop_shortcut() -> Path:
    if sys.platform != "win32":
        raise ShortcutError("只有 Windows 支持创建桌面快捷方式。")
    executable, arguments = _launch_target()
    if not executable.is_file():
        raise ShortcutError(f"找不到启动程序：{executable}")
    icon = (paths.resource_dir() / "dma.ico").resolve()
    target = shortcut_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "$ws = New-Object -ComObject WScript.Shell",
        f"$link = $ws.CreateShortcut({_ps_quote(target)})",
        f"$link.TargetPath = {_ps_quote(executable)}",
        f"$link.Arguments = {_ps_quote(arguments)}",
        f"$link.WorkingDirectory = {_ps_quote(paths.app_dir())}",
        f"$link.Description = {_ps_quote(APP_NAME + ' - Minecraft Mod Agent')}",
        "$link.WindowStyle = 1",
    ]
    if icon.is_file():
        lines.append(f"$link.IconLocation = {_ps_quote(str(icon) + ',0')}")
    lines.append("$link.Save()")

    encoded = base64.b64encode("\n".join(lines).encode("utf-16le")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as exc:
        raise ShortcutError(f"调用 PowerShell 失败：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise ShortcutError(detail or "系统拒绝了快捷方式创建请求。")
    if not target.is_file():
        raise ShortcutError("快捷方式创建后没有出现在桌面上。")
    return target
