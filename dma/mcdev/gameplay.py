"""Background gameplay smoke test.

Drives a real dev client through the probe: teleport onto a safe platform,
turn, walk/jump, break a block, summon and hit a mob, use an item, read the
inventory and take a screenshot.  Everything is done by sending packets /
setting key state on the client thread, so the OS mouse and the user's focus
are never touched.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from . import probe


def _call(side: str, path: str, *, method: str = "GET", payload: Any = None,
          port: int | None = None, timeout: float = 15.0) -> dict:
    return probe.probe_json(
        side, path, method=method, payload=payload, port=port, timeout=timeout
    )


def _data(response: dict) -> dict:
    value = response.get("data") if isinstance(response, dict) else None
    return value if isinstance(value, dict) else {}


class _Recorder:
    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self.steps: list[dict] = []
        self.log = log or (lambda _text: None)

    def add(self, name: str, ok: bool, **detail: Any) -> bool:
        row = {"step": name, "ok": bool(ok)}
        row.update(detail)
        self.steps.append(row)
        self.log(f"{name}: {'ok' if ok else 'FAIL'}")
        return bool(ok)

    def summary(self) -> dict:
        return {
            "ok": all(item["ok"] for item in self.steps) if self.steps else False,
            "steps": self.steps,
            "passed": sum(1 for item in self.steps if item["ok"]),
            "failed": [item["step"] for item in self.steps if not item["ok"]],
        }


def _state(port: int | None) -> dict:
    return _data(_call("client", "/player/state", port=port, timeout=8))


def _command(side: str, cmd: str, port: int | None) -> dict:
    return _call(side, "/command", method="POST", payload={"cmd": cmd}, port=port, timeout=20)


def _command_any(cmd: str, port: int | None) -> dict:
    """控制台命令优先走服务端（权限 4），只开了客户端时退回客户端执行。"""
    try:
        response = _command("server", cmd, port)
        if response.get("ok", True) and isinstance(response.get("data"), dict):
            return response
    except Exception:  # noqa: BLE001 - 服务端没起来就走客户端
        pass
    try:
        return _command("client", cmd, port)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _block_at(x: int, y: int, z: int, port: int | None) -> str:
    data = _data(_call("server", f"/world/block?x={x}&y={y}&z={z}", port=port))
    return str(data.get("block") or "")


def goto(x: float, z: float, *, port: int | None = None, timeout: float = 25.0,
         radius: float = 0.9, log: Callable[[str], None] | None = None) -> dict:
    """走到目标水平坐标。

    参考 MCPFabric / Mine Odyssey 的做法：把“看方向 + 按住前进 + 卡住就跳/冲刺”
    合成一个高层动作，模型不用自己算每一帧的按键。纯客户端输入，不碰鼠标焦点。
    """
    log = log or (lambda _text: None)
    state = _state(port)
    if not state.get("inWorld"):
        return {"ok": False, "reason": "客户端还没进入世界", "screen": state.get("screen")}
    started = time.time()
    stuck = 0
    steps = 0
    distance = None
    while time.time() - started < timeout:
        state = _state(port)
        px = _num(state.get("x"))
        pz = _num(state.get("z"))
        if px is None or pz is None:
            return {"ok": False, "reason": "读不到玩家坐标"}
        dx, dz = x - px, z - pz
        distance = math.hypot(dx, dz)
        if distance <= radius:
            _call("client", "/player/move", method="POST",
                  payload={"mode": "stop"}, port=port, timeout=5)
            return {"ok": True, "distance": round(distance, 2), "steps": steps,
                    "elapsed": round(time.time() - started, 1),
                    "pos": [round(px, 2), round(pz, 2)]}
        yaw = math.degrees(math.atan2(dz, dx)) - 90.0
        _call("client", "/player/look", method="POST",
              payload={"yaw": yaw, "pitch": 0.0}, port=port, timeout=5)
        keys = "forward"
        payload = {"mode": "walk", "keys": keys, "ms": 400}
        if stuck >= 2:
            payload["sprint"] = True
        if stuck >= 3:
            payload["jump"] = True
        _call("client", "/player/move", method="POST", payload=payload,
              port=port, timeout=10)
        time.sleep(0.45)
        after = _state(port)
        nx, nz = _num(after.get("x")), _num(after.get("z"))
        if nx is not None and nz is not None:
            now = math.hypot(x - nx, z - nz)
            if now > (distance or now) - 0.05:
                stuck += 1
            else:
                stuck = 0
            distance = now
        steps += 1
        log(f"goto {x:.1f},{z:.1f} 距离 {distance:.2f} 卡住计数 {stuck}")
    _call("client", "/player/move", method="POST", payload={"mode": "stop"},
          port=port, timeout=5)
    return {"ok": False, "reason": "timeout", "distance": None if distance is None
            else round(distance, 2), "steps": steps, "target": [x, z]}


def playtest(*, side: str = "client", port: int | None = None, seconds: int = 2,
             log: Callable[[str], None] | None = None) -> dict:
    """Run the real gameplay smoke test and return per-step evidence."""
    rec = _Recorder(log)
    side = side if side in {"client", "server"} else "client"
    # This scenario only makes sense for an integrated dev client.
    side = "client"

    state = _state(port)
    if state.get("inWorld") and state.get("screen"):
        # 窗口失焦或上一次操作可能留下暂停菜单/背包界面，界面打开时
        # KeyboardInput 不 tick，走动和跳跃都不会生效，先关掉再测。
        try:
            _call(side, "/ui/close", method="POST", payload={}, port=port, timeout=10)
        except Exception:  # noqa: BLE001 - 关界面失败交给后续步骤暴露
            pass
        time.sleep(0.4)
        _call(side, "/player/move", method="POST", payload={"mode": "stop"}, port=port, timeout=10)
        state = _state(port)
    if not state.get("inWorld"):
        rec.add("in_world", False, reason="客户端还没进入世界", screen=state.get("screen"))
        return rec.summary()
    name = str(state.get("name") or "Dev")
    rec.add("in_world", True, player=name, dimension=state.get("dimension"),
            pos=[state.get("x"), state.get("y"), state.get("z")],
            gameMode=state.get("gameMode"))

    # ---- setup on the server console (has permission level 4) ----------
    _command_any("gamerule doDaylightCycle false", port)
    _command_any("time set day", port)
    _command_any("weather clear", port)
    _command_any(f"gamemode creative {name}", port)
    state = _state(port)
    rec.add("creative", bool(state.get("creative")), gameMode=state.get("gameMode"))

    px = float(state.get("x") or 0.0)
    py = float(state.get("y") or 64.0)
    pz = float(state.get("z") or 0.0)
    # platform + teleport: a 17x17 stone pad one block under the feet
    ax, ay, az = int(math.floor(px)), int(math.floor(py)), int(math.floor(pz))
    _command_any(f"fill {ax - 8} {ay - 1} {az - 8} {ax + 8} {ay - 1} {az + 8} minecraft:stone", port)
    _command_any(f"fill {ax - 8} {ay} {az - 8} {ax + 8} {ay + 3} {az + 8} minecraft:air", port)
    _command_any(f"tp {name} {ax + 0.5:.2f} {ay:.2f} {az + 0.5:.2f}", port)
    time.sleep(0.8)
    state = _state(port)

    # ---- look ---------------------------------------------------------
    look = _data(_call(side, "/player/look", method="POST",
                       payload={"yaw": 0.0, "pitch": 0.0}, port=port, timeout=10))
    # 注意 0.0 在 Python 里是假值，不能用 `or 999` 兜底，否则 yaw=0 反而判失败。
    yaw_out = _num(look.get("yaw"))
    rec.add("look", bool(look.get("looked")) and yaw_out is not None and abs(yaw_out) < 5,
            yaw=look.get("yaw"), pitch=look.get("pitch"))

    # ---- walk ---------------------------------------------------------
    before = _state(port)
    moved = False
    delta = 0.0
    for attempt in ({"ms": max(600, seconds * 1000)},
                    {"ms": max(600, seconds * 1000), "sprint": True}):
        try:
            _call(side, "/player/move", method="POST",
                  payload={"mode": "walk", "keys": "forward", **attempt},
                  port=port, timeout=20 + attempt["ms"] / 1000)
        except Exception as exc:  # noqa: BLE001
            rec.add("walk", False, error=str(exc))
            break
        time.sleep(0.4)
        after = _state(port)
        delta = math.hypot(
            float(after.get("x") or 0) - float(before.get("x") or 0),
            float(after.get("z") or 0) - float(before.get("z") or 0),
        )
        if delta > 0.4:
            moved = True
            break
    rec.add("walk", moved, distance=round(delta, 3),
            start=[before.get("x"), before.get("y"), before.get("z")],
            end=[(_state(port).get("x")), (_state(port).get("y")), (_state(port).get("z"))])

    # ---- jump ---------------------------------------------------------
    # 走完之后可能已经离开平台在空中，先把人放回平台中心并等落地，
    # 否则起跳瞬间 onGround=false，按键不产生任何位移。
    _command_any(f"tp {name} {ax + 0.5:.2f} {ay:.2f} {az + 0.5:.2f}", port)
    ground = None
    for _ in range(12):
        time.sleep(0.25)
        state = _state(port)
        if state.get("onGround") and abs((_num(state.get("y")) or 0) - ay) < 0.6:
            ground = state
            break
    state = ground or _state(port)
    y_before = _num(state.get("y")) or 0.0
    y_mid = y_before
    for _ in range(3):
        _call(side, "/player/move", method="POST",
              payload={"mode": "walk", "keys": "jump", "ms": 450}, port=port, timeout=20)
        time.sleep(0.22)
        mid = _state(port)
        y_mid = _num(mid.get("y")) or 0.0
        if y_mid > y_before + 0.15:
            break
        time.sleep(1.0)
        state = _state(port)
        y_before = _num(state.get("y")) or y_before
    time.sleep(0.9)
    rec.add("jump", y_mid > y_before + 0.15, y_before=round(y_before, 2),
            y_mid=round(y_mid, 2), onGround=bool((ground or {}).get("onGround")))

    # ---- place + break a block ---------------------------------------
    state = _state(port)
    px = float(state.get("x") or 0)
    py = float(state.get("y") or 0)
    pz = float(state.get("z") or 0)
    bx, by, bz = int(math.floor(px)), int(math.floor(py)), int(math.floor(pz)) + 3
    _command_any(f"setblock {bx} {by} {bz} minecraft:oak_log", port)
    time.sleep(0.4)
    before_block = _block_at(bx, by, bz, port)
    break_data = _data(_call(side, "/player/break", method="POST",
                             payload={"x": bx, "y": by, "z": bz}, port=port, timeout=15))
    time.sleep(0.3)
    after_block = break_data.get("after") or _block_at(bx, by, bz, port)
    if before_block.endswith("oak_log") and "air" not in str(after_block):
        mine_data = _data(_call(side, "/player/mine", method="POST",
                                payload={"x": bx, "y": by, "z": bz, "timeoutMs": 4000},
                                port=port, timeout=20))
        after_block = mine_data.get("after") or _block_at(bx, by, bz, port)
    rec.add("break_block", "air" in str(after_block),
            pos=[bx, by, bz], before=before_block, after=after_block)

    # ---- summon + attack a mob ---------------------------------------
    target_type = "twilight_grove:moss_crawler"
    sx, sy, sz = ax + 2, ay, az + 2
    _command_any(f"fill {sx} {sy} {sz} {sx} {sy + 1} {sz} minecraft:air", port)
    _command_any(f"summon {target_type} {sx + 0.5:.2f} {sy:.2f} {sz + 0.5:.2f}", port)
    time.sleep(1.2)
    near = _state(port)
    target = None
    for entity in near.get("nearby") or []:
        if str(entity.get("type")) == target_type:
            target = entity
            break
    if target is None:
        # fall back to a vanilla mob so the step still proves attack works
        target_type = "minecraft:zombie"
        _command_any(f"summon {target_type} {sx + 0.5:.2f} {sy:.2f} {sz - 2.0:.2f}", port)
        time.sleep(1.2)
        near = _state(port)
        for entity in near.get("nearby") or []:
            if str(entity.get("type")) == target_type:
                target = entity
                break
    attacked = False
    killed = False
    health_before = health_after = None
    hits = 0
    if target:
        entity_id = target.get("id")
        health_before = target.get("health")
        _call(side, "/player/look", method="POST", payload={"entity": entity_id},
              port=port, timeout=10)
        for _ in range(8):
            result = _data(_call(side, "/player/attack", method="POST",
                                 payload={"entity": entity_id, "radius": 6.0},
                                 port=port, timeout=10))
            hits += 1
            if result.get("attacked"):
                attacked = True
            time.sleep(0.35)
            refreshed = _state(port)
            live = [item for item in refreshed.get("nearby") or []
                    if item.get("id") == entity_id]
            if not live:
                # 被一击打死也算攻击链路通了
                killed = True
                break
            health_after = live[0].get("health")
            if health_after is not None and health_before is not None and health_after < health_before:
                break
    hurt = (
        health_before is not None and health_after is not None and health_after < health_before
    )
    rec.add("attack", bool(attacked and (hurt or killed)), type=target_type,
            hits=hits, health_before=health_before, health_after=health_after,
            killed=killed)

    # ---- use item -----------------------------------------------------
    # 放方块必须在 4.5 格触达范围内，否则客户端预测成功、服务端静默拒绝，
    # 看起来“SUCCESS 但没放上”。先把人挪到目标旁边并看向目标面。
    ux, uy, uz = ax + 2, ay - 1, az
    _command_any(f"setblock {ux} {uy} {uz} minecraft:stone", port)
    _command_any(f"tp {name} {ux + 2.2:.2f} {ay:.2f} {uz + 0.5:.2f}", port)
    time.sleep(0.6)
    _call(side, "/player/look", method="POST",
          payload={"x": ux + 0.5, "y": uy + 1.0, "z": uz + 0.5}, port=port, timeout=10)
    _command_any(f"item replace entity {name} weapon.mainhand with minecraft:oak_planks 4", port)
    time.sleep(0.6)
    use = {}
    placed = ""
    for _ in range(3):
        use = _data(_call(side, "/player/use", method="POST",
                          payload={"x": ux, "y": uy, "z": uz, "face": "up"},
                          port=port, timeout=10))
        time.sleep(0.5)
        placed = _block_at(ux, uy + 1, uz, port)
        if "oak_planks" in placed:
            break
    rec.add("use_item", bool(use.get("used")) and "oak_planks" in placed,
            result=use.get("result"), placed_at=placed, target=f"{ux},{uy},{uz}")

    # ---- inventory ----------------------------------------------------
    try:
        inventory = _data(_call(side, "/player/inventory", port=port, timeout=10))
        opened = bool(inventory.get("opened"))
        _call(side, "/ui/close", method="POST", payload={}, port=port, timeout=10)
        rec.add("inventory", opened, screen=inventory.get("screen"))
    except Exception as exc:  # noqa: BLE001
        rec.add("inventory", False, error=str(exc))

    # ---- screenshot ---------------------------------------------------
    try:
        shot = _data(_call(side, "/screenshot?label=playtest", port=port, timeout=30))
        rec.add("screenshot", bool(shot.get("path")), path=shot.get("path"))
    except Exception as exc:  # noqa: BLE001
        rec.add("screenshot", False, error=str(exc))

    summary = rec.summary()
    summary["player"] = name
    summary["gameMode"] = _state(port).get("gameMode")
    return summary