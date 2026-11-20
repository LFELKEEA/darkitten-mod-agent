"""纯后台假玩家控制。

不启动客户端、不创建任何窗口，只通过开发服务端探针（39001）驱动一个
FakePlayer：移动、转向、挖掘、放置、攻击、使用物品、读背包。

设计参考了 Baritone / Mindcraft / Marionette 一类项目的共同做法：
- 高层动作（走到坐标、挖掉方块、攻击目标）由宿主拆成原语；
- 原语直接调用游戏的服务端接口，不模拟系统鼠标键盘；
- 每个动作都返回坐标、方块、血量等可断言的证据。

限制：假玩家没有网络连接，不参与客户端渲染；需要看画面时仍用客户端探针。
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from . import probe

DEFAULT_BOT = "Dev"


class BotError(RuntimeError):
    """服务端探针不可用或假玩家动作失败。"""


def _call(
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    port: int | None = None,
    timeout: float = 30.0,
) -> dict:
    try:
        body = probe.probe_json(
            "server", path, method=method, payload=payload, port=port, timeout=timeout
        )
    except probe.ProbeError as exc:
        raise BotError(str(exc)) from exc
    if isinstance(body, dict) and body.get("ok") is False:
        error = body.get("error")
        if isinstance(error, dict):
            error = error.get("message") or error
        raise BotError(str(error or body))
    data = body.get("data") if isinstance(body, dict) else None
    if isinstance(data, dict):
        return data
    if isinstance(body, dict):
        return body
    raise BotError(f"服务端探针返回无法解析的内容：{str(body)[:300]}")


def status(*, port: int | None = None) -> dict:
    return _call("/status", port=port, timeout=8.0)


def spawn(
    name: str = DEFAULT_BOT,
    *,
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    gamemode: str = "creative",
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "name": str(name or DEFAULT_BOT),
        "gamemode": str(gamemode or "creative"),
    }
    if x is not None and y is not None and z is not None:
        payload.update({"x": float(x), "y": float(y), "z": float(z)})
    return _call("/bot/spawn", method="POST", payload=payload, port=port)


def bots(*, port: int | None = None) -> list[str]:
    data = _call("/bot/list", port=port, timeout=8.0)
    value = data.get("bots")
    return [str(item) for item in value] if isinstance(value, list) else []


def remove(name: str = DEFAULT_BOT, *, port: int | None = None) -> dict:
    return _call(
        "/bot/remove", method="POST", payload={"name": str(name)}, port=port
    )


def state(
    name: str = DEFAULT_BOT, *, radius: float = 8.0, port: int | None = None
) -> dict:
    return _call(
        f"/bot/state?name={_q(name)}&radius={float(radius)}", port=port
    )


def look(
    name: str = DEFAULT_BOT,
    *,
    yaw: float = 0.0,
    pitch: float = 0.0,
    port: int | None = None,
) -> dict:
    return _call(
        "/bot/look",
        method="POST",
        payload={"name": str(name), "yaw": float(yaw), "pitch": float(pitch)},
        port=port,
    )


def move(
    name: str = DEFAULT_BOT,
    *,
    mode: str = "teleport",
    x: float | None = None,
    y: float | None = None,
    z: float | None = None,
    dx: float = 0.0,
    dy: float = 0.0,
    dz: float = 0.0,
    steps: int = 20,
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "name": str(name),
        "mode": str(mode or "teleport").lower(),
        "dx": float(dx),
        "dy": float(dy),
        "dz": float(dz),
        "steps": max(1, min(int(steps), 200)),
    }
    for key, value in (("x", x), ("y", y), ("z", z)):
        if value is not None:
            payload[key] = float(value)
    return _call("/bot/move", method="POST", payload=payload, port=port)


def mine(
    name: str = DEFAULT_BOT,
    *,
    x: int,
    y: int,
    z: int,
    timeout_ms: int = 6000,
    port: int | None = None,
) -> dict:
    return _call(
        "/bot/mine",
        method="POST",
        payload={
            "name": str(name),
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "timeoutMs": max(500, min(int(timeout_ms), 20_000)),
        },
        port=port,
        timeout=(timeout_ms / 1000.0) + 30.0,
    )


def place(
    name: str = DEFAULT_BOT,
    *,
    x: int,
    y: int,
    z: int,
    face: str = "up",
    item: str = "minecraft:stone",
    count: int = 1,
    port: int | None = None,
) -> dict:
    return _call(
        "/bot/place",
        method="POST",
        payload={
            "name": str(name),
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "face": str(face or "up"),
            "item": str(item or "minecraft:stone"),
            "count": max(1, min(int(count), 64)),
        },
        port=port,
    )


def use(name: str = DEFAULT_BOT, *, port: int | None = None) -> dict:
    return _call("/bot/use", method="POST", payload={"name": str(name)}, port=port)


def give(
    name: str = DEFAULT_BOT,
    *,
    item: str = "minecraft:stone",
    count: int = 1,
    port: int | None = None,
) -> dict:
    return _call(
        "/bot/give",
        method="POST",
        payload={
            "name": str(name),
            "item": str(item or "minecraft:stone"),
            "count": max(1, min(int(count), 64)),
        },
        port=port,
    )


def attack(
    name: str = DEFAULT_BOT,
    *,
    entity: int | None = None,
    target: str | None = None,
    radius: float = 4.0,
    port: int | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "name": str(name),
        "radius": max(1.0, float(radius)),
    }
    if entity is not None:
        payload["entity"] = int(entity)
    if target:
        payload["target"] = str(target)
    return _call("/bot/attack", method="POST", payload=payload, port=port)


def gamemode(
    name: str = DEFAULT_BOT, mode: str = "creative", *, port: int | None = None
) -> dict:
    return _call(
        "/bot/gamemode",
        method="POST",
        payload={"name": str(name), "gamemode": str(mode or "creative")},
        port=port,
    )


def command(cmd: str, *, port: int | None = None) -> dict:
    text = str(cmd or "").strip()
    if not text:
        raise BotError("command 不能为空。")
    return _call("/command", method="POST", payload={"cmd": text}, port=port)


CONTROL_ACTIONS = {
    "spawn",
    "state",
    "list",
    "remove",
    "look",
    "move",
    "teleport",
    "walk",
    "mine",
    "break",
    "place",
    "use",
    "give",
    "attack",
    "gamemode",
    "command",
}


def control(action: str, params: dict[str, Any] | None = None, *, port: int | None = None) -> dict:
    name = str(action or "state").strip().lower()
    p = dict(params or {})
    bot = str(p.get("name") or p.get("bot") or DEFAULT_BOT)
    if name == "spawn":
        return spawn(
            bot,
            x=_opt_float(p.get("x")),
            y=_opt_float(p.get("y")),
            z=_opt_float(p.get("z")),
            gamemode=str(p.get("gamemode") or p.get("mode") or "creative"),
            port=port,
        )
    if name == "state":
        return state(bot, radius=float(p.get("radius") or 8.0), port=port)
    if name == "list":
        return {"bots": bots(port=port)}
    if name == "remove":
        return remove(bot, port=port)
    if name == "look":
        return look(
            bot,
            yaw=float(p.get("yaw") or 0.0),
            pitch=float(p.get("pitch") or 0.0),
            port=port,
        )
    if name in {"move", "teleport", "walk"}:
        mode = "walk" if name == "walk" else str(p.get("mode") or name)
        return move(
            bot,
            mode=mode,
            x=_opt_float(p.get("x")),
            y=_opt_float(p.get("y")),
            z=_opt_float(p.get("z")),
            dx=float(p.get("dx") or 0.0),
            dy=float(p.get("dy") or 0.0),
            dz=float(p.get("dz") or 0.0),
            steps=int(p.get("steps") or 20),
            port=port,
        )
    if name in {"mine", "break"}:
        return mine(
            bot,
            x=int(p.get("x") or 0),
            y=int(p.get("y") or 64),
            z=int(p.get("z") or 0),
            timeout_ms=int(p.get("timeoutMs") or p.get("timeout_ms") or 6000),
            port=port,
        )
    if name == "place":
        return place(
            bot,
            x=int(p.get("x") or 0),
            y=int(p.get("y") or 64),
            z=int(p.get("z") or 0),
            face=str(p.get("face") or "up"),
            item=str(p.get("item") or "minecraft:stone"),
            count=int(p.get("count") or 1),
            port=port,
        )
    if name == "use":
        return use(bot, port=port)
    if name == "give":
        return give(
            bot,
            item=str(p.get("item") or "minecraft:stone"),
            count=int(p.get("count") or 1),
            port=port,
        )
    if name == "attack":
        return attack(
            bot,
            entity=int(p["entity"]) if p.get("entity") is not None else None,
            target=str(p.get("target") or "") or None,
            radius=float(p.get("radius") or 4.0),
            port=port,
        )
    if name == "gamemode":
        return gamemode(
            bot, str(p.get("gamemode") or p.get("mode") or "creative"), port=port
        )
    if name == "command":
        return command(str(p.get("cmd") or p.get("command") or ""), port=port)
    raise BotError(f"未知假玩家动作：{action}。可用：{', '.join(sorted(CONTROL_ACTIONS))}")


def playtest(
    *,
    name: str = DEFAULT_BOT,
    port: int | None = None,
    log: Callable[[str], None] | None = None,
) -> dict:
    """纯后台玩法冒烟：证明不开客户端也能操作人物。

    步骤：出生 -> 确认创造 -> 走两步 -> 放方块 -> 挖掉 -> 召唤并攻击生物。
    """
    log = log or (lambda _text: None)
    steps: list[dict] = []

    def record(step: str, ok: bool, **detail: Any) -> bool:
        row = {"step": step, "ok": bool(ok)}
        row.update(detail)
        steps.append(row)
        log(f"{step}: {'ok' if ok else 'FAIL'}")
        return bool(ok)

    try:
        spawned = spawn(name, gamemode="creative", port=port)
        st = spawned.get("state") or {}
        record(
            "spawn",
            bool(st.get("name")),
            pos=[st.get("x"), st.get("y"), st.get("z")],
            gameMode=st.get("gameMode"),
        )
        record("creative", str(st.get("gameMode") or "") == "creative",
               gameMode=st.get("gameMode"))

        before = state(name, port=port)
        moved = move(name, mode="walk", dx=2.0, dz=0.0, steps=30, port=port)
        after = state(name, port=port)
        distance = math.hypot(
            float(after.get("x") or 0) - float(before.get("x") or 0),
            float(after.get("z") or 0) - float(before.get("z") or 0),
        )
        record("walk", distance > 0.25, distance=round(distance, 2),
               end=[after.get("x"), after.get("y"), after.get("z")])

        # 先铺一块承重方块，再贴着它的上表面放石头，最后挖掉；创造模式破坏是即时的。
        px = int(math.floor(float(after.get("x") or 0)))
        py = int(math.floor(float(after.get("y") or 0)))
        pz = int(math.floor(float(after.get("z") or 0)))
        tx, ty, tz = px + 2, py - 1, pz
        command(f"setblock {tx} {ty} {tz} minecraft:stone", port=port)
        command(f"setblock {tx} {ty + 1} {tz} minecraft:air", port=port)
        placed = place(name, x=tx, y=ty, z=tz, face="up",
                       item="minecraft:stone", port=port)
        record("place", "stone" in str(placed.get("placed") or ""), **{
            "target": placed.get("target"), "placed": placed.get("placed")
        })
        mined = mine(name, x=tx, y=ty + 1, z=tz, port=port)
        record("mine", "air" in str(mined.get("after") or ""), **{
            "before": mined.get("before"), "after": mined.get("after")
        })

        # 召唤僵尸并用服务端攻击接口打它；先切夜晚，避免白天燃烧干扰判定。
        command("difficulty normal", port=port)
        command("time set night", port=port)
        command(f"summon minecraft:zombie {tx + 0.5} {ty + 1} {tz + 0.5}", port=port)
        time.sleep(0.6)
        near = state(name, radius=8.0, port=port)
        zombie = next(
            (item for item in near.get("nearby") or []
             if str(item.get("type")) == "minecraft:zombie"),
            None,
        )
        if zombie is None:
            record("attack", False, reason="zombie did not spawn")
        else:
            result = attack(name, entity=int(zombie["id"]), radius=8.0, port=port)
            record(
                "attack",
                bool(result.get("attacked"))
                and (
                    bool(result.get("killed"))
                    or float(result.get("healthAfter") or 0)
                    < float(result.get("healthBefore") or 0)
                ),
                type=result.get("type"),
                healthBefore=result.get("healthBefore"),
                healthAfter=result.get("healthAfter"),
                killed=result.get("killed"),
            )
    except Exception as exc:  # noqa: BLE001 - 返回证据而不是抛给调用方
        record("error", False, error=str(exc))

    return {
        "ok": all(item["ok"] for item in steps) if steps else False,
        "steps": steps,
        "passed": sum(1 for item in steps if item["ok"]),
        "failed": [item["step"] for item in steps if not item["ok"]],
    }


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value or ""), safe="")
