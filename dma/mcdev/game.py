"""AI 游戏控制层。

在已启动的 Forge 1.20.1 开发客户端（探针 39002）上执行：
换语言、新建存档、读取画面/玩家/UI 状态、移动视角与角色、按键、攻击/使用/挖掘、
聊天与命令、按文字点击界面、截图并做确定性画面体检。

这一层只负责把探针端点串成可编排的动作，具体判定仍交给 Agent 的测试步骤。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from . import probe


class GameError(RuntimeError):
    """游戏控制失败：探针未启动、端点报错或等待超时。"""


# ----------------------------------------------------------------------
# 探针调用
# ----------------------------------------------------------------------
def _data(
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    port: int | None = None,
    timeout: float = 20.0,
) -> dict:
    try:
        body = probe.probe_json(
            "client", path, method=method, payload=payload, port=port, timeout=timeout
        )
    except probe.ProbeError as exc:
        raise GameError(str(exc)) from exc
    if isinstance(body, dict) and body.get("ok") is False:
        raise GameError(str(body.get("error") or body.get("message") or body))
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        return data
    if isinstance(body, dict):
        return body
    raise GameError(f"探针返回了无法解析的内容：{str(body)[:500]}")


def ensure_ready(port: int | None = None) -> dict:
    try:
        status = _data("/status", port=port, timeout=5.0)
    except GameError as exc:
        raise GameError(
            "游戏客户端探针没有响应。先用 mcdev_prepare 启动 Forge 1.20.1 开发端"
            f"（kind=both 或 client），再执行游戏控制。原始错误：{exc}"
        ) from exc
    if str(status.get("side") or "") != "client":
        raise GameError("39002 上不是客户端探针，无法控制游戏。")
    return status


# ----------------------------------------------------------------------
# 语言
# ----------------------------------------------------------------------
_LANGUAGE_ALIASES = {
    "中文": "zh_cn",
    "简体中文": "zh_cn",
    "chinese": "zh_cn",
    "zh": "zh_cn",
    "zh-cn": "zh_cn",
    "zh_cn": "zh_cn",
    "english": "en_us",
    "en": "en_us",
    "en-us": "en_us",
    "en_us": "en_us",
}


def normalize_language(code: str | None) -> str:
    text = str(code or "zh_cn").strip().lower()
    return _LANGUAGE_ALIASES.get(text, text or "zh_cn")


def language(port: int | None = None) -> dict:
    return _data("/language", port=port)


def set_language(
    code: str = "zh_cn",
    *,
    reload: bool = True,
    port: int | None = None,
    wait: float = 20.0,
) -> dict:
    wanted = normalize_language(code)
    data = _data(
        "/language",
        method="POST",
        payload={"code": wanted, "reload": bool(reload)},
        port=port,
        timeout=60.0,
    )
    # 资源重载是异步的；等 selected 真正落到目标语言再返回，避免下一步截图还是英文。
    deadline = time.time() + max(0.0, wait)
    while time.time() < deadline:
        try:
            current = _data("/language", port=port, timeout=5.0)
        except GameError:
            break
        if normalize_language(current.get("selected") or current.get("code")) == wanted:
            data["verified"] = True
            data["selected"] = current.get("selected")
            data["code"] = current.get("code")
            return data
        time.sleep(0.5)
    data["verified"] = normalize_language(
        data.get("selected") or data.get("code")
    ) == wanted
    return data


# ----------------------------------------------------------------------
# 存档
# ----------------------------------------------------------------------
def create_world(
    name: str = "AI 测试",
    *,
    gamemode: str = "creative",
    difficulty: str = "peaceful",
    world_type: str = "normal",
    seed: str = "",
    cheats: bool = True,
    structures: bool = True,
    bonus_chest: bool = False,
    port: int | None = None,
    wait: float = 120.0,
) -> dict:
    payload = {
        "name": str(name or "AI 测试").strip() or "AI 测试",
        "gamemode": str(gamemode or "creative").strip().lower(),
        "difficulty": str(difficulty or "peaceful").strip().lower(),
        "worldType": str(world_type or "normal").strip().lower(),
        "seed": str(seed or ""),
        "cheats": bool(cheats),
        "structures": bool(structures),
        "bonusChest": bool(bonus_chest),
    }
    data = _data("/world/create", method="POST", payload=payload, port=port, timeout=120.0)
    # 已经是存档里就不重复建，交给调用方决定是否先退出。
    if not data.get("created"):
        return data
    deadline = time.time() + max(10.0, wait)
    while time.time() < deadline:
        try:
            status = _data("/status", port=port, timeout=5.0)
        except GameError:
            status = {}
        if status.get("inWorld"):
            data["inWorld"] = True
            data["player"] = status.get("player")
            data["dimension"] = status.get("dimension")
            data["loadedIn"] = round(wait - (deadline - time.time()), 1)
            return data
        time.sleep(1.0)
    data["inWorld"] = False
    data["waitedSeconds"] = wait
    return data


def wait_world(*, port: int | None = None, timeout: float = 120.0) -> dict:
    deadline = time.time() + max(5.0, timeout)
    last: dict = {}
    while time.time() < deadline:
        try:
            last = _data("/status", port=port, timeout=5.0)
        except GameError:
            last = {}
        if last.get("inWorld"):
            last["ready"] = True
            return last
        time.sleep(1.0)
    last["ready"] = False
    return last


# ----------------------------------------------------------------------
# 状态与画面
# ----------------------------------------------------------------------
def state(*, radius: int = 8, port: int | None = None) -> dict:
    return _data(f"/player/state?radius={max(1, min(int(radius), 64))}", port=port)


def ui_snapshot(*, port: int | None = None, widget_limit: int = 80) -> dict:
    data = _data("/ui/snapshot", port=port)
    widgets = data.get("widgets")
    if isinstance(widgets, list) and len(widgets) > widget_limit:
        data["widgets"] = widgets[:widget_limit]
        data["widgetsTruncated"] = len(widgets)
    return data


def log_tail(*, lines: int = 40, port: int | None = None) -> list[str]:
    data = _data(f"/log/tail?n={max(1, min(int(lines), 500))}", port=port)
    value = data.get("lines")
    return [str(item) for item in value] if isinstance(value, list) else []


def screenshot(
    label: str = "shot",
    *,
    port: int | None = None,
    review: bool = True,
) -> dict:
    safe = "".join(
        ch if (ch.isalnum() or ch in "._-") else "-" for ch in str(label or "shot")
    ).strip("-") or "shot"
    data = _data(f"/screenshot?label={safe}", port=port, timeout=40.0)
    path_text = str(data.get("path") or "")
    result: dict[str, Any] = dict(data)
    if review and path_text:
        try:
            from ..kernel.tools import vision

            item = vision.review(Path(path_text), style="screenshot")
            result["review"] = item.to_dict()
        except Exception as exc:  # noqa: BLE001 - 评审失败不影响截图本身
            result["review"] = {"ok": False, "issues": [f"画面评审不可用：{exc}"]}
    return result


# observe() 的参数名与 screenshot() 同名，这里留一个别名避免遮蔽。
_capture = screenshot


def window_screenshot(
    *,
    pid: int,
    project_root: str | Path | None = None,
    label: str = "window",
    review: bool = True,
) -> dict:
    """非探针加载器的兜底：按进程窗口截图，至少能让 AI 看到实际画面。"""
    if os.name != "nt":
        raise GameError("窗口截图只在 Windows 上可用；当前平台请改用探针截图。")
    from ..studio_engine.verify import _capture_window

    root = Path(str(project_root)).expanduser() if project_root else (
        Path(tempfile.gettempdir()) / "darkitten-game"
    )
    (root / ".mcmod").mkdir(parents=True, exist_ok=True)
    safe = "".join(
        ch if (ch.isalnum() or ch in "._-") else "-" for ch in str(label or "window")
    ).strip("-") or "window"
    out = (Path(tempfile.gettempdir()) / "darkitten-game" /
           f"mcdev-window-{safe}-{int(time.time() * 1000)}.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        _capture_window(root, out, int(pid))
    except Exception as exc:  # noqa: BLE001 - PowerShell 失败要转成可读错误
        raise GameError(f"窗口截图失败：{exc}") from exc
    if not out.is_file():
        raise GameError(
            f"窗口截图没有生成文件（pid={pid}）。确认游戏窗口已打开且没有被最小化。"
        )
    result: dict[str, Any] = {
        "path": str(out),
        "exists": True,
        "size": out.stat().st_size,
        "pid": int(pid),
        "source": "window",
    }
    if review:
        try:
            from ..kernel.tools import vision

            result["review"] = vision.review(out, style="screenshot").to_dict()
        except Exception as exc:  # noqa: BLE001
            result["review"] = {"ok": False, "issues": [f"画面评审不可用：{exc}"]}
    return result


def observe(
    *,
    radius: int = 8,
    screenshot: bool = True,
    label: str = "observe",
    log_lines: int = 40,
    port: int | None = None,
    window_pid: int | None = None,
    project_root: str | Path | None = None,
) -> dict:
    try:
        status = ensure_ready(port)
    except GameError as exc:
        # 没有探针时退到窗口截图；控制与断言仍然不可用，必须如实返回。
        if screenshot and window_pid:
            return {
                "probe": {"available": False, "reason": str(exc)},
                "inWorld": None,
                "window": window_screenshot(
                    pid=int(window_pid),
                    project_root=project_root,
                    label=label,
                ),
            }
        raise
    result: dict[str, Any] = {
        "status": status,
        "inWorld": bool(status.get("inWorld")),
    }
    if result["inWorld"]:
        result["player"] = state(radius=radius, port=port)
    result["ui"] = ui_snapshot(port=port)
    if log_lines:
        result["log"] = log_tail(lines=log_lines, port=port)
    if screenshot:
        result["screenshot"] = _capture(label, port=port, review=True)
    return result


# ----------------------------------------------------------------------
# 玩家控制
# ----------------------------------------------------------------------
def look(
    *,
    yaw: float | None = None,
    pitch: float | None = None,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    entity: int | None = None,
    target: str | None = None,
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {}
    for key, value in (("yaw", yaw), ("pitch", pitch), ("x", x), ("y", y), ("z", z)):
        if value is not None:
            payload[key] = float(value)
    if entity is not None:
        payload["entity"] = int(entity)
    if target:
        payload["target"] = str(target)
    if not payload:
        raise GameError("look 至少需要 yaw/pitch、坐标或实体目标之一。")
    return _data("/player/look", method="POST", payload=payload, port=port)


def move(
    mode: str = "teleport",
    *,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
    keys: str = "",
    ms: int = 1000,
    sprint: bool = False,
    sneak: bool = False,
    jump: bool = False,
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "mode": str(mode or "teleport").lower(),
        "dx": float(dx),
        "dy": float(dy),
        "dz": float(dz),
        "keys": str(keys or ""),
        "ms": max(0, min(int(ms), 10_000)),
        "sprint": bool(sprint),
        "sneak": bool(sneak),
        "jump": bool(jump),
    }
    for key, value in (("x", x), ("y", y), ("z", z)):
        if value is not None:
            payload[key] = float(value)
    return _data("/player/move", method="POST", payload=payload, port=port, timeout=30.0)


def key(
    name: str,
    *,
    action: str = "tap",
    ms: int = 100,
    port: int | None = None,
) -> dict:
    if not str(name or "").strip():
        raise GameError("key 缺少按键名。")
    return _data(
        "/player/key",
        method="POST",
        payload={
            "key": str(name),
            "action": str(action or "tap").lower(),
            "ms": max(0, min(int(ms), 10_000)),
        },
        port=port,
        timeout=30.0,
    )


def attack(
    *,
    entity: int | None = None,
    target: str | None = None,
    radius: float = 4.0,
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {"radius": float(radius)}
    if entity is not None:
        payload["entity"] = int(entity)
    if target:
        payload["target"] = str(target)
    return _data("/player/attack", method="POST", payload=payload, port=port)


def use(
    x: int, y: int, z: int, *, face: str = "up", port: int | None = None
) -> dict:
    return _data(
        "/player/use",
        method="POST",
        payload={"x": int(x), "y": int(y), "z": int(z), "face": str(face or "up")},
        port=port,
    )


def use_item(*, port: int | None = None) -> dict:
    return _data("/player/use_item", method="POST", payload={}, port=port)


def break_block(x: int, y: int, z: int, *, port: int | None = None) -> dict:
    return _data(f"/player/break?x={int(x)}&y={int(y)}&z={int(z)}", port=port)


def mine(
    x: int, y: int, z: int, *, timeout_ms: int = 6000, port: int | None = None
) -> dict:
    ms = max(1000, min(int(timeout_ms), 15_000))
    return _data(
        f"/player/mine?x={int(x)}&y={int(y)}&z={int(z)}&timeoutMs={ms}",
        port=port,
        timeout=(ms / 1000.0) + 20.0,
    )


def chat(text: str, *, port: int | None = None) -> dict:
    return _data(
        "/chat/send", method="POST", payload={"text": str(text)}, port=port
    )


def command(cmd: str, *, port: int | None = None) -> dict:
    text = str(cmd or "").strip()
    if not text:
        raise GameError("command 缺少命令内容。")
    return _data("/command", method="POST", payload={"cmd": text}, port=port)


def click_text(
    text: str = "",
    *,
    key: str = "",
    match: str = "contains",
    index: int = -1,
    button: int = 0,
    port: int | None = None,
) -> dict:
    return _data(
        "/ui/click_text",
        method="POST",
        payload={
            "text": str(text or ""),
            "key": str(key or ""),
            "match": str(match or "contains"),
            "index": int(index),
            "button": int(button),
        },
        port=port,
    )


def type_text(
    text: str,
    *,
    index: int = 0,
    submit: bool = False,
    clear: bool = False,
    port: int | None = None,
) -> dict:
    return _data(
        "/ui/set_text",
        method="POST",
        payload={
            "text": str(text or ""),
            "index": int(index),
            "submit": bool(submit),
            "clear": bool(clear),
        },
        port=port,
    )


def wait_screen(
    *,
    screen: str = "",
    title: str = "",
    text: str = "",
    timeout: float = 20.0,
    port: int | None = None,
) -> dict:
    seconds = max(1.0, min(float(timeout), 60.0))
    query = (
        f"/ui/wait?screen={_q(screen)}&title={_q(title)}&text={_q(text)}"
        f"&timeoutMs={int(seconds * 1000)}"
    )
    return _data(query, port=port, timeout=seconds + 10.0)


def close_screen(*, port: int | None = None) -> dict:
    return _data("/ui/close", port=port)


def inventory(*, port: int | None = None) -> dict:
    return _data("/player/inventory", port=port)


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value or ""), safe="")


# ----------------------------------------------------------------------
# 测试编排
# ----------------------------------------------------------------------
CONTROL_ACTIONS = {
    "look",
    "look_at",
    "move",
    "teleport",
    "walk",
    "key",
    "attack",
    "use",
    "use_item",
    "break",
    "mine",
    "chat",
    "command",
    "click_text",
    "type_text",
    "wait_screen",
    "close_screen",
    "inventory",
    "screenshot",
    "state",
    "wait",
    "stop",
}


def control(action: str, params: dict[str, Any] | None = None, *, port: int | None = None) -> dict:
    name = str(action or "").strip().lower()
    p = dict(params or {})
    if name in {"look", "look_at"}:
        return look(
            yaw=p.get("yaw"),
            pitch=p.get("pitch"),
            x=p.get("x"),
            y=p.get("y"),
            z=p.get("z"),
            entity=p.get("entity"),
            target=p.get("target"),
            port=port,
        )
    if name in {"move", "teleport", "walk", "stop"}:
        mode = "stop" if name == "stop" else ("walk" if name == "walk" else str(p.get("mode") or name))
        return move(
            mode,
            x=p.get("x"),
            y=p.get("y"),
            z=p.get("z"),
            dx=p.get("dx") or 0,
            dy=p.get("dy") or 0,
            dz=p.get("dz") or 0,
            keys=p.get("keys") or "",
            ms=p.get("ms") or 1000,
            sprint=bool(p.get("sprint")),
            sneak=bool(p.get("sneak")),
            jump=bool(p.get("jump")),
            port=port,
        )
    if name == "key":
        return key(
            str(p.get("key") or ""),
            action=str(p.get("action") or "tap"),
            ms=p.get("ms") or 100,
            port=port,
        )
    if name == "attack":
        return attack(
            entity=p.get("entity") or p.get("id"),
            target=p.get("target"),
            radius=p.get("radius") or 4.0,
            port=port,
        )
    if name == "use":
        if p.get("x") is None or p.get("y") is None or p.get("z") is None:
            raise GameError("use 需要 x/y/z 方块坐标。")
        return use(int(p["x"]), int(p["y"]), int(p["z"]), face=str(p.get("face") or "up"), port=port)
    if name == "use_item":
        return use_item(port=port)
    if name == "break":
        return break_block(int(p.get("x") or 0), int(p.get("y") or 0), int(p.get("z") or 0), port=port)
    if name == "mine":
        return mine(
            int(p.get("x") or 0),
            int(p.get("y") or 0),
            int(p.get("z") or 0),
            timeout_ms=p.get("timeoutMs") or p.get("timeout_ms") or 6000,
            port=port,
        )
    if name == "chat":
        return chat(str(p.get("text") or ""), port=port)
    if name == "command":
        return command(str(p.get("cmd") or p.get("command") or ""), port=port)
    if name == "click_text":
        return click_text(
            str(p.get("text") or ""),
            key=str(p.get("key") or ""),
            match=str(p.get("match") or "contains"),
            index=p.get("index", -1),
            button=p.get("button") or 0,
            port=port,
        )
    if name == "type_text":
        return type_text(
            str(p.get("text") or p.get("value") or ""),
            index=p.get("index") or 0,
            submit=bool(p.get("submit")),
            clear=bool(p.get("clear")),
            port=port,
        )
    if name == "wait_screen":
        return wait_screen(
            screen=str(p.get("screen") or ""),
            title=str(p.get("title") or ""),
            text=str(p.get("text") or ""),
            timeout=p.get("timeout") or 20,
            port=port,
        )
    if name == "close_screen":
        return close_screen(port=port)
    if name == "inventory":
        return inventory(port=port)
    if name == "screenshot":
        return screenshot(str(p.get("label") or "shot"), port=port, review=True)
    if name == "state":
        return state(radius=p.get("radius") or 8, port=port)
    if name == "wait":
        ms = max(0, min(int(p.get("ms") or 500), 30_000))
        time.sleep(ms / 1000.0)
        return {"waitedMs": ms}
    raise GameError(f"未知游戏动作：{action}。可用：{', '.join(sorted(CONTROL_ACTIONS))}")


def _get_path(data: Any, path: str) -> Any:
    current = data
    for part in str(path or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
        if current is None:
            return None
    return current


def _check(op: str, actual: Any, expected: Any) -> bool:
    name = str(op or "eq").lower()
    if name == "exists":
        return actual is not None
    if name == "truthy":
        return bool(actual)
    if name == "eq":
        return actual == expected
    if name == "ne":
        return actual != expected
    if name == "contains":
        if isinstance(actual, (list, tuple, str, dict)):
            return expected in actual
        return False
    if name == "not_contains":
        if isinstance(actual, (list, tuple, str, dict)):
            return expected not in actual
        return True
    if name in {"gt", "gte", "lt", "lte"}:
        try:
            left = float(actual)
            right = float(expected)
        except (TypeError, ValueError):
            return False
        return {
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[name]
    return False


def run_test(
    steps: Iterable[dict] | None,
    *,
    port: int | None = None,
    continue_on_error: bool = False,
) -> dict:
    plan = [item for item in (steps or []) if isinstance(item, dict)]
    if not plan:
        raise GameError("测试计划为空。")
    results: list[dict] = []
    context: dict[str, Any] = {"steps": results}
    ok = True
    for index, step in enumerate(plan):
        kind = str(step.get("type") or "control").strip().lower()
        record: dict[str, Any] = {"index": index, "type": kind}
        context["last"] = results[-1]["data"] if results else None
        try:
            record["data"] = _run_step(kind, step, context, port=port)
            record["ok"] = True
        except Exception as exc:  # noqa: BLE001 - 单步失败要保留上下文
            record["ok"] = False
            record["error"] = str(exc)
            ok = False
        results.append(record)
        context["steps"] = results
        if not record["ok"] and not continue_on_error:
            break
    return {"ok": ok, "steps": results, "executed": len(results)}


def _run_step(kind: str, step: dict, context: dict, *, port: int | None) -> Any:
    if kind in {"setup", "prepare"}:
        language_code = normalize_language(step.get("language") or "zh_cn")
        out: dict[str, Any] = {"language": set_language(language_code, port=port)}
        world = step.get("world")
        if isinstance(world, dict):
            name = str(world.get("name") or "AI 测试")
            out["world"] = create_world(
                name,
                gamemode=str(world.get("gamemode") or "creative"),
                difficulty=str(world.get("difficulty") or "peaceful"),
                world_type=str(world.get("worldType") or world.get("world_type") or "normal"),
                seed=str(world.get("seed") or ""),
                cheats=bool(world.get("cheats", True)),
                structures=bool(world.get("structures", True)),
                bonus_chest=bool(world.get("bonusChest", False)),
                port=port,
                wait=float(world.get("wait") or step.get("wait") or 120),
            )
        return out
    if kind in {"language", "lang"}:
        return set_language(str(step.get("code") or step.get("language") or "zh_cn"), port=port)
    if kind in {"world", "new_world"}:
        return create_world(
            str(step.get("name") or "AI 测试"),
            gamemode=str(step.get("gamemode") or "creative"),
            difficulty=str(step.get("difficulty") or "peaceful"),
            world_type=str(step.get("worldType") or step.get("world_type") or "normal"),
            seed=str(step.get("seed") or ""),
            cheats=bool(step.get("cheats", True)),
            structures=bool(step.get("structures", True)),
            bonus_chest=bool(step.get("bonusChest", False)),
            port=port,
            wait=float(step.get("wait") or 120),
        )
    if kind == "wait_world":
        return wait_world(port=port, timeout=float(step.get("timeout") or 120))
    if kind == "control":
        return control(str(step.get("action") or ""), step, port=port)
    if kind == "action":
        return control(str(step.get("action") or ""), step, port=port)
    if kind == "command":
        return command(str(step.get("cmd") or step.get("command") or ""), port=port)
    if kind in {"screenshot", "shot"}:
        return screenshot(str(step.get("label") or "shot"), port=port, review=True)
    if kind in {"observe", "state"}:
        return observe(
            radius=int(step.get("radius") or 8),
            screenshot=bool(step.get("screenshot", False)),
            label=str(step.get("label") or "observe"),
            log_lines=int(step.get("log") or 0),
            port=port,
        )
    if kind == "player":
        return state(radius=int(step.get("radius") or 8), port=port)
    if kind == "log":
        return {"lines": log_tail(lines=int(step.get("lines") or 40), port=port)}
    if kind == "wait":
        ms = max(0, min(int(step.get("ms") or 500), 30_000))
        time.sleep(ms / 1000.0)
        return {"waitedMs": ms}
    if kind == "assert":
        path = str(step.get("path") or "last")
        actual = _get_path(context, path)
        if not _check(str(step.get("op") or "eq"), actual, step.get("value")):
            raise AssertionError(
                f"断言失败：{path} = {json.dumps(actual, ensure_ascii=False)[:300]} "
                f"（期望 {step.get('op') or 'eq'} {json.dumps(step.get('value'), ensure_ascii=False)[:200]}）"
            )
        return {"asserted": path, "actual": actual}
    raise GameError(f"未知测试步骤类型：{kind}")