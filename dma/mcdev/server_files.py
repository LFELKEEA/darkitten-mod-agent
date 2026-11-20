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
    # ModDevGradle / Loom 默认就用 run/，不能因为旁边恰好有个 run-server
    # 目录就把 eula.txt 和 server.properties 写到游戏根本不读的地方。
    for fallback in ("run", f"run-{side}"):
        candidate = root / fallback
        if candidate.is_dir():
            return candidate
    # 两个目录都还不存在（工程第一次启动）：按 ModDevGradle / Loom / Forge MDK
    # 的默认值用 run/。写成 run-server 会让游戏去 run/ 找 eula.txt，首次启动直接退出。
    return root / "run"


def server_port(project_root: str | Path, default: int = 25566) -> int:
    """读取游戏实际监听的端口（server.properties 的 server-port）。"""
    path = run_dir_for(project_root, "server") / "server.properties"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "server-port":
            try:
                return max(1, min(int(value.strip()), 65535))
            except ValueError:
                return default
    return default


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
    # 开发验证默认创造模式：假玩家/客户端进来就能直接放置、破坏、飞行，
    # 不必每条测试先发 gamemode 命令。
    "gamemode": "creative",
    "force-gamemode": "true",
}


def ensure_server_properties(project_root: str | Path, port: int = 25566) -> list[str]:
    """写好/修正 ``server.properties``，让开发端能连上且接受离线账号。

    会强制写入开发验证所需的关键项（端口、离线模式、创造模式等），
    未列出的用户配置保持不变。
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


def ensure_probe_options(
    project_root: str | Path,
    side: str = "client",
    *,
    background: bool = True,
    pause_on_lost_focus: bool = False,
) -> list[str]:
    """写入探针自己的运行配置，控制是否隐藏客户端窗口。

    探针在游戏目录 ``config/mcdev-probe.properties`` 里读取这些值；每次启动
    都显式写一遍，避免上一次“显示窗口”的配置残留下来。
    """
    run_dir = run_dir_for(project_root, side)
    path = run_dir / "config" / "mcdev-probe.properties"
    wanted = {
        "background": "true" if background else "false",
        "pause-on-lost-focus": "true" if pause_on_lost_focus else "false",
    }
    lines: list[str] = []
    seen: set[str] = set()
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in wanted:
                seen.add(key)
                lines.append(f"{key}={wanted[key]}")
            else:
                lines.append(line)
    for key, value in wanted.items():
        if key not in seen:
            lines.append(f"{key}={value}")
    text = "\n".join(lines).rstrip() + "\n"
    if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == text:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        return [str(path.relative_to(Path(str(project_root)).expanduser()))]
    except ValueError:
        return [str(path)]


def ensure_client_options(
    project_root: str | Path,
    *,
    pause_on_lost_focus: bool = False,
) -> list[str]:
    """关闭“失焦自动暂停”，隐藏窗口/后台操作时输入不会被暂停菜单吞掉。"""
    run_dir = run_dir_for(project_root, "client")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "options.txt"
    wanted = "true" if pause_on_lost_focus else "false"
    lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("pauseOnLostFocus:"):
                lines.append(f"pauseOnLostFocus:{wanted}")
            else:
                lines.append(line)
    if not any(item.startswith("pauseOnLostFocus:") for item in lines):
        lines.append(f"pauseOnLostFocus:{wanted}")
    text = "\n".join(lines) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == text:
        return []
    path.write_text(text, encoding="utf-8")
    try:
        return [str(path.relative_to(Path(str(project_root)).expanduser()))]
    except ValueError:
        return [str(path)]


def ensure_fml_early_window(project_root: str | Path, *, enabled: bool) -> list[str]:
    """后台模式关掉 Forge 的早期加载窗口。

    1.20.1 在 mod 加载前会先开一个 fmlearlywindow 显示 Mojang 进度条；此时
    探针还没加载，无法从游戏内隐藏，只能靠外部轮询，桌面会短暂闪窗。写入
    ``config/fml.toml`` 的 ``earlyWindowControl=false`` 可以从源头避免它。
    """
    run_dir = run_dir_for(project_root, "client")
    path = run_dir / "config" / "fml.toml"
    wanted = "true" if enabled else "false"
    lines: list[str] = []
    seen = False
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                lines.append(line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key == "earlyWindowControl":
                lines.append(f"earlyWindowControl = {wanted}")
                seen = True
            else:
                lines.append(line)
    if not seen:
        lines.append(f"earlyWindowControl = {wanted}")
    text = "\n".join(lines).rstrip() + "\n"
    if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == text:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        return [str(path.relative_to(Path(str(project_root)).expanduser()))]
    except ValueError:
        return [str(path)]


def ensure_client_background(project_root: str | Path, *, background: bool = True) -> list[str]:
    changed = ensure_client_options(project_root, pause_on_lost_focus=not background)
    changed.extend(
        ensure_probe_options(
            project_root,
            "client",
            background=background,
            pause_on_lost_focus=not background,
        )
    )
    changed.extend(ensure_fml_early_window(project_root, enabled=not background))
    return changed


def ensure_client_language(project_root: str | Path, code: str = "zh_cn") -> list[str]:
    """客户端启动前把 ``options.txt`` 的 ``lang`` 固定成目标语言。

    探针只在 Forge 1.20.1 可用；Fabric / NeoForge 等加载器没有运行时接口，
    至少让 AI 第一次进游戏就是中文，后续截图和人工核对都按中文界面进行。
    已有 options.txt 时只替换 lang 行，其余设置原样保留。
    """
    run_dir = run_dir_for(project_root, "client")
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "options.txt"
    wanted = str(code or "zh_cn").strip() or "zh_cn"
    lines: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("lang:"):
                lines.append(f"lang:{wanted}")
            else:
                lines.append(line)
    if not any(item.startswith("lang:") for item in lines):
        lines.append(f"lang:{wanted}")
    if path.is_file() and path.read_text(encoding="utf-8", errors="replace") == (
        "\n".join(lines) + "\n"
    ):
        return []
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        return [str(path.relative_to(Path(str(project_root)).expanduser()))]
    except ValueError:
        return [str(path)]
