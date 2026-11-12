"""带硬截止时间和取消的 HTTP 请求。

``urllib.request.urlopen(timeout=...)`` 只约束 socket 的单次操作。实测在
Windows + 打包运行时里，TLS 握手可能绕过这个超时一直卡住（尤其是指向被
黑洞的 CDN）。工具调用因此会永远不返回，界面只能一直显示“生成中”。

这里把整个请求放进工作线程，调用方按总时限等待，并且每 0.2 秒检查一次
取消事件。超时后调用方立刻拿到异常；工作线程是 daemon，会自行退出。
"""

from __future__ import annotations

import threading
import time
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_TIMEOUT = 30.0
POLL_SECONDS = 0.2
BLACKHOLE_TTL = 600.0

_blackhole: dict[str, float] = {}
_blackhole_lock = threading.Lock()
_local = threading.local()


class NetError(RuntimeError):
    pass


class NetTimeout(NetError):
    pass


class NetCancelled(NetError):
    pass


def bind_cancel(event: threading.Event | None) -> threading.Event | None:
    """把取消事件绑定到当前线程，后续网络调用默认使用它。"""
    previous = getattr(_local, "cancel", None)
    _local.cancel = event
    return previous


def restore_cancel(previous: threading.Event | None) -> None:
    _local.cancel = previous


def current_cancel() -> threading.Event | None:
    return getattr(_local, "cancel", None)


def _host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def mark_unreachable(host: str, seconds: float = BLACKHOLE_TTL) -> None:
    if not host:
        return
    with _blackhole_lock:
        _blackhole[host] = time.monotonic() + max(1.0, seconds)


def is_unreachable(host: str) -> bool:
    if not host:
        return False
    with _blackhole_lock:
        until = _blackhole.get(host)
        if until is None:
            return False
        if until <= time.monotonic():
            _blackhole.pop(host, None)
            return False
        return True


def reset_blackhole() -> None:
    with _blackhole_lock:
        _blackhole.clear()


def fetch(
    request: urllib.request.Request,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
    context: Any = None,
) -> bytes:
    """执行一个请求并返回响应正文（总耗时受 ``timeout`` 约束）。"""
    return fetch_info(
        request, timeout=timeout, cancel=cancel, context=context
    )[0]


def fetch_info(
    request: urllib.request.Request,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
    context: Any = None,
) -> tuple[bytes, int, dict[str, str]]:
    """执行请求并返回 ``(正文, 状态码, 响应头)``。

    总耗时不会超过 ``timeout`` 秒；取消事件被设置时最多 0.2 秒返回。
    被判定为黑洞的主机在 ``BLACKHOLE_TTL`` 内直接失败，避免重复卡住。
    """
    cancel = cancel if cancel is not None else current_cancel()
    url = getattr(request, "full_url", "") or ""
    host = _host_of(url)
    if is_unreachable(host):
        raise NetTimeout(
            f"{host} 最近一次请求无响应，已临时跳过（{int(BLACKHOLE_TTL)} 秒后可重试）。"
        )
    budget = max(0.5, float(timeout))
    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            kwargs = {"timeout": budget}
            if context is not None:
                kwargs["context"] = context
            with urllib.request.urlopen(request, **kwargs) as response:
                box["response"] = response
                box["status"] = int(getattr(response, "status", 0) or 0)
                box["headers"] = {
                    str(key): str(value)
                    for key, value in (response.headers.items() if response.headers else [])
                }
                box["raw"] = response.read()
        except BaseException as exc:  # noqa: BLE001 - 原样抛回调用方
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True, name="netclient-fetch").start()
    deadline = time.monotonic() + budget
    while not done.is_set():
        if cancel is not None and cancel.is_set():
            _close_later(box, done)
            raise NetCancelled("请求已取消。")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _close_later(box, done)
            if host:
                mark_unreachable(host)
            raise NetTimeout(
                f"{host or '目标主机'} 在 {int(budget)} 秒内没有完成连接或响应。"
            )
        done.wait(min(POLL_SECONDS, remaining))

    error = box.get("error")
    if isinstance(error, BaseException):
        raise error
    return box.get("raw") or b"", int(box.get("status") or 0), dict(box.get("headers") or {})


class Stream:
    """对 ``urlopen`` 响应的包装：连接和每一次读取都受硬截止时间约束。"""

    def __init__(
        self,
        response: Any,
        timeout: float,
        cancel: threading.Event | None,
    ) -> None:
        self._response = response
        self._timeout = max(0.5, float(timeout))
        self._cancel = cancel
        self._closed = False

    @property
    def status(self) -> int:
        return int(getattr(self._response, "status", 200) or 200)

    @property
    def headers(self) -> Any:
        return self._response.headers

    def read(self, size: int = -1) -> bytes:
        if self._closed:
            return b""
        box: dict[str, Any] = {}
        done = threading.Event()

        def worker() -> None:
            try:
                box["data"] = self._response.read(size)
            except BaseException as exc:  # noqa: BLE001 - 原样抛回调用方
                box["error"] = exc
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True, name="netclient-read").start()
        deadline = time.monotonic() + self._timeout
        while not done.is_set():
            if self._cancel is not None and self._cancel.is_set():
                raise NetCancelled("请求已取消。")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetTimeout(
                    f"读取响应时连续 {int(self._timeout)} 秒没有数据，已放弃。"
                )
            done.wait(min(POLL_SECONDS, remaining))
        error = box.get("error")
        if isinstance(error, BaseException):
            raise error
        return box.get("data") or b""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._response.close()
        except Exception:  # noqa: BLE001 - 清理失败不影响报错
            pass

    def __enter__(self) -> "Stream":
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.close()
        return False


def open_stream(
    request: urllib.request.Request,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cancel: threading.Event | None = None,
    context: Any = None,
) -> Stream:
    """打开响应流；建立连接和读取响应头的总耗时不超过 ``timeout`` 秒。"""
    cancel = cancel if cancel is not None else current_cancel()
    url = getattr(request, "full_url", "") or ""
    host = _host_of(url)
    if is_unreachable(host):
        raise NetTimeout(
            f"{host} 最近一次请求无响应，已临时跳过（{int(BLACKHOLE_TTL)} 秒后可重试）。"
        )
    budget = max(0.5, float(timeout))
    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            kwargs = {"timeout": budget}
            if context is not None:
                kwargs["context"] = context
            box["response"] = urllib.request.urlopen(request, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - 原样抛回调用方
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True, name="netclient-open").start()
    deadline = time.monotonic() + budget
    while not done.is_set():
        if cancel is not None and cancel.is_set():
            _close_later(box, done)
            raise NetCancelled("请求已取消。")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _close_later(box, done)
            if host:
                mark_unreachable(host)
            raise NetTimeout(
                f"{host or '目标主机'} 在 {int(budget)} 秒内没有完成连接或响应。"
            )
        done.wait(min(POLL_SECONDS, remaining))

    error = box.get("error")
    if isinstance(error, BaseException):
        raise error
    response = box.get("response")
    if response is None:
        raise NetError("目标服务器没有返回响应。")
    return Stream(response, budget, cancel)


def _close_later(box: dict[str, Any], done: threading.Event) -> None:
    """调用方已经放弃，但工作线程可能还攥着连接；等它返回后关掉。"""

    def cleanup() -> None:
        if not done.wait(30):
            return
        response = box.get("response")
        try:
            if response is not None:
                response.close()
        except Exception:  # noqa: BLE001 - 清理失败不影响报错
            pass

    threading.Thread(target=cleanup, daemon=True, name="netclient-cleanup").start()
