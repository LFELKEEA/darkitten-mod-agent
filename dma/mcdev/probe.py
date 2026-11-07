"""Minecraft 开发探针的 HTTP 客户端。

探针协议来自 yan-mc/minecraft-mod-dev（MIT）：
只监听回环地址，请求头带 X-Mcdev-Token，响应统一包成 {"ok":true,"data":{...}}。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_PORTS = {"server": 39001, "client": 39002}
TOKEN = "mcdev"

# 探针只在本机回环上跑，不能让系统代理插手，否则公司代理会把请求吞掉。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ProbeError(RuntimeError):
    pass


def probe_port(side: str, port: int | None = None) -> int:
    if port:
        return int(port)
    return DEFAULT_PORTS.get(side, DEFAULT_PORTS["server"])


def probe(
    side: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    port: int | None = None,
    timeout: float = 15.0,
) -> str:
    actual = probe_port(side, port)
    url = f"http://127.0.0.1:{actual}{path}"
    data = None
    headers = {"X-Mcdev-Token": TOKEN}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ProbeError(
            f"probe {side}:{actual} {path} -> HTTP {exc.code}: {body[:2000]}"
        ) from exc
    except Exception as exc:  # noqa: BLE001 - 网络层异常统一转成可读错误
        raise ProbeError(
            f"probe {side}:{actual} {path} 连接失败：{exc}"
        ) from exc


def probe_json(
    side: str,
    path: str,
    *,
    method: str = "GET",
    payload: object | None = None,
    port: int | None = None,
    timeout: float = 15.0,
) -> dict:
    text = probe(
        side, path, method=method, payload=payload, port=port, timeout=timeout
    )
    try:
        value = json.loads(text)
    except ValueError:
        return {"raw": text}
    return value if isinstance(value, dict) else {"data": value}


def probe_status_once(kind: str, port: int | None = None, timeout: float = 2.0) -> bool:
    actual = probe_port(kind, port)
    try:
        with _OPENER.open(
            urllib.request.Request(
                f"http://127.0.0.1:{actual}/status",
                headers={"X-Mcdev-Token": TOKEN},
            ),
            timeout=timeout,
        ) as response:
            body = json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - 探针没起来就是 False
        return False
    data = body.get("data") if isinstance(body, dict) else None
    return isinstance(data, dict) and data.get("side") == kind
