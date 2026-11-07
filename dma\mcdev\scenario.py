"""场景步骤执行器：按顺序调用探针端点，遇到第一个失败就停下。"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import probe as probe_mod
from .probe import ProbeError

MAX_WAIT_MS = 60_000


def js_string(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def get_path(value: Any, path: str | None) -> Any:
    if not path:
        return value
    current = value
    for key in str(path).split("."):
        if not key:
            continue
        if current is None:
            return None
        if isinstance(current, list):
            if not key.isdigit():
                return None
            index = int(key)
            if index >= len(current):
                return None
            current = current[index]
            continue
        if isinstance(current, dict):
            current = current.get(key)
            continue
        return None
    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def check_assert(op: str, actual: Any, expected: Any) -> bool:
    if op == "eq":
        return js_string(actual) == js_string(expected)
    if op == "ne":
        return js_string(actual) != js_string(expected)
    if op == "contains":
        return js_string(expected) in js_string(actual)
    if op == "exists":
        return actual is not None
    left = _number(actual)
    right = _number(expected)
    if left is None or right is None:
        return False
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    raise ValueError(f"未知断言操作：{op}")


def _query(step: dict, keys: tuple[str, ...], defaults: dict | None = None) -> str:
    values = defaults or {}
    pairs = []
    for key in keys:
        value = step.get(key, values.get(key))
        if value is None:
            value = ""
        pairs.append(f"{key}={_quote(value)}")
    return "&".join(pairs)


def _quote(value: Any) -> str:
    from urllib.parse import quote

    return quote(js_string(value), safe="")


def _data(step: dict, side: str, path: str, **kwargs) -> dict:
    return probe_mod.probe_json(side, path, port=step.get("port"), **kwargs)


def execute_step(step: dict, cancel: threading.Event | None = None) -> dict:
    kind = str(step.get("type") or "")
    if kind == "status":
        return _data(step, str(step.get("side") or "server"), "/status")
    if kind == "command":
        cmd = str(step.get("cmd") or "")
        if not cmd:
            raise ValueError("command 步骤缺少 cmd")
        return _data(
            step,
            str(step.get("side") or "server"),
            "/command",
            method="POST",
            payload={"cmd": cmd},
        )
    if kind == "block":
        path = "/world/block?" + _query(
            step, ("x", "y", "z"), {"x": 0, "y": 64, "z": 0}
        )
        return _data(step, "server", path)
    if kind == "break":
        path = "/world/break?" + _query(
            step, ("x", "y", "z"), {"x": 0, "y": 64, "z": 0}
        )
        return _data(step, "server", path)
    if kind == "mine":
        path = "/world/mine?" + _query(
            step,
            ("x", "y", "z", "player"),
            {"x": 0, "y": 64, "z": 0, "player": "Dev"},
        )
        return _data(step, "server", path)
    if kind == "client_mine":
        path = "/player/mine?" + _query(
            step,
            ("x", "y", "z", "timeoutMs"),
            {"x": 0, "y": 64, "z": 0, "timeoutMs": 6000},
        )
        return _data(step, "client", path)
    if kind == "ui_close":
        return _data(step, "client", "/ui/close", method="POST", payload={})
    if kind == "craft":
        path = "/craft?" + _query(step, ("recipe", "player"), {"player": "Dev"})
        return _data(step, "server", path)
    if kind == "items":
        path = "/items?" + _query(
            step, ("x", "y", "z", "r"), {"x": 0, "y": 64, "z": 0, "r": 4}
        )
        return _data(step, "server", path)
    if kind == "recipe":
        return _data(step, "server", "/recipe?" + _query(step, ("id",)))
    if kind == "loot":
        path = "/loot?" + _query(
            step, ("block", "x", "y", "z"), {"x": 0, "y": 64, "z": 0}
        )
        return _data(step, "server", path)
    if kind == "item":
        return _data(step, "server", "/item?" + _query(step, ("item", "nbt")))
    if kind == "use":
        return _data(
            step,
            "client",
            "/player/use",
            method="POST",
            payload={
                "x": step.get("x"),
                "y": step.get("y"),
                "z": step.get("z"),
                "face": step.get("face") or "up",
            },
        )
    if kind == "ui":
        return _data(step, "client", "/ui/snapshot")
    if kind == "click":
        body: dict[str, Any] = {"button": step.get("button", 0)}
        slot = step.get("slot")
        if isinstance(slot, int) and slot >= 0:
            body["slot"] = slot
        else:
            body["x"] = step.get("x", 0)
            body["y"] = step.get("y", 0)
        if step.get("shift"):
            body["shift"] = True
        return _data(step, "client", "/ui/click", method="POST", payload=body)
    if kind == "screenshot":
        return _data(step, "client", "/screenshot")
    if kind == "run_dump":
        return _data(
            step,
            str(step.get("side") or "server"),
            f"/run/dump?n={int(step.get('n') or 200)}",
        )
    raise ValueError(f"未知步骤类型：{kind}")


def run_steps(
    steps: list[dict],
    *,
    cancel: threading.Event | None = None,
    warnings: list[str] | None = None,
) -> dict:
    results: list[dict] = []
    last_data = -1
    for index, step in enumerate(steps):
        if cancel is not None and cancel.is_set():
            results.append({"index": index, "type": step.get("type"), "ok": False, "error": "已取消"})
            return {"ok": False, "failedAt": index, "warnings": warnings or [], "steps": results}
        kind = str(step.get("type") or "")
        try:
            if kind == "wait":
                ms = max(0, min(int(step.get("ms") or 0), MAX_WAIT_MS))
                deadline = time.time() + ms / 1000.0
                while time.time() < deadline:
                    if cancel is not None and cancel.is_set():
                        raise RuntimeError("已取消")
                    time.sleep(min(0.2, max(0.0, deadline - time.time())))
                results.append({"index": index, "type": kind, "ok": True, "ms": ms})
                continue
            if kind == "assert":
                target = None
                if "step" in step:
                    ref = int(step["step"])
                    if 0 <= ref < len(results):
                        target = results[ref].get("data")
                elif last_data >= 0:
                    target = results[last_data].get("data")
                actual = get_path(target, step.get("path"))
                op = str(step.get("op") or "eq")
                expected = step.get("value")
                if not check_assert(op, actual, expected):
                    raise AssertionError(
                        f"断言失败：actual={js_string(actual)}，期望 {op} "
                        f"{js_string(expected)}"
                        + (f"（{step['label']}）" if step.get("label") else "")
                    )
                results.append(
                    {"index": index, "type": kind, "ok": True, "actual": actual}
                )
                continue
            data = execute_step(step, cancel)
            results.append({"index": index, "type": kind, "ok": True, "data": data})
            last_data = len(results) - 1
        except Exception as exc:  # noqa: BLE001 - 失败要作为结果返回
            message = str(exc)
            if isinstance(exc, ProbeError):
                message = str(exc)
            elif isinstance(exc, AssertionError):
                message = str(exc)
            results.append(
                {"index": index, "type": kind, "ok": False, "error": message}
            )
            return {
                "ok": False,
                "failedAt": index,
                "warnings": warnings or [],
                "steps": results,
            }
    return {"ok": True, "warnings": warnings or [], "steps": results}
