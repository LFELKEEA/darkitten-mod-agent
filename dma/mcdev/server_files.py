"""开发服务端启动前必须准备好的运行文件。

Minecraft 独立服务器首次启动会检查 ``eula.txt``，读不到 ``eula=true`` 就
直接退出；同时 ``server.properties`` 默认是 ``server-port=25565`` 且
``online-mode=true``，与开发端的回环端口和离线账号对不上。这两处不处理，
探针永远连不上，验证流程第一步就会失败。
"""

from __future__ import annotations

import re
from pathlib import Path

# Forge 的 Gradle 运行配置：
#   workingDirectory project.file('run-server')
# NeoForge / ModDevGradle 的写法：
#   gameDirectory = file('run-server')
_RUN_DIR_PATTERNS = (
    re.compile(r"workingDirectory\s+project\.file\(\s*['\"]([^'\"]+)['\"]"),
    re.compile(r"gameDirectory\s*=\s*file\(\s*['\"]([^'\"]+)['\"]"),
)

DEFAULT_PORTS = {"server": 25566, "client": 25565, "gametest": 25565}


def declared_run_dirs(project_root: str | Path) -> list[str]:
    root = Path(str(project_root)).expanduser()
    gradle_file = root / "build.gradle"
    try:
        text = gradle_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found: list[str] = []
    for pattern in _RUN_DIR_PATTERNS:
        for value in pattern.findall(text):
            clean = value.strip().strip("./\\")
            if clean and clean not in found:
                found.append(clean)
    return found


def run_dir_for(project_root: str | Path, side: str = "server") -> Path:
    """定位某一侧（server / gametest / client）的 run 工作目录。"""
    root = Path(str(project_root)).expanduser()
    side = (side or "server").strip().lower()
    for value in declared_run_dirs(root):
        name = Path(value).name.lower()
        if side in name:
            return root / value
    for fallback in (f"run-{side}", "run"):
        candidate = root / fallback
        if candidate.is_dir():
            return candidate
    return root / f"run-{side}"


def ensure_eula(project_root: str | Path, side: str = "server") -> list[str]:
    """写好 ``eula.txt``；返回实际改动的文件名。"""
    run_dir = run_dir_for(project_root, side)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "eula.txt"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    if any(line.strip().lower() == "eula=true" for line in text.splitlines()):
        return []
    path.write_text("eula=true\n", encoding="utf-8")
    return ["eula.txt"]


_SERVER_DEFAULTS = {
    "online-mode": "false",
    "level-type": "normal",
    "generate-structures": "false",
    "spawn-protection": "0",
    "max-tick-time": "-1",
    "view-distance": "6",
    "simulation-distance": "6",
    "allow-nether": "false",
    "enable-command-block": "true",
}


def ensure_server_properties(project_root: str | Path, port: int = 25566) -> list[str]:
    """写好/修正 ``server.properties``，让开发端能连上且接受离线账号。

    已存在的文件只强制两项：``server-port``（与客户端连接端口一致）和
    ``online-mode=false``（dev 客户端用的是离线账号），其余配置保留。
    """
    run_dir = run_dir_for(project_root, "server")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "server.properties"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []

    wanted = dict(_SERVER_DEFAULTS)
    wanted["server-port"] = str(max(1, min(int(port or 25566), 65535)))
    wanted["motd"] = "Darkitten dev server"

    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            output.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in wanted:
            seen.add(key)
            output.append(f"{key}={wanted[key]}")
        else:
            output.append(line)
    for key, value in wanted.items():
        if key not in seen:
            output.append(f"{key}={value}")

    text = "\n".join(output).rstrip() + "\n"
    if path.is_file() and text == path.read_text(encoding="utf-8", errors="replace"):
        return []
    path.write_text(text, encoding="utf-8")
    return ["server.properties"]


def ensure_server_files(project_root: str | Path, port: int = 25566) -> list[str]:
    changed = ensure_eula(project_root, "server")
    changed.extend(ensure_server_properties(project_root, port))
    return changed
