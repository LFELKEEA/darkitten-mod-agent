from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import AIConfig


class AIError(Exception):
    pass


class _DeadlineExceeded(TimeoutError):
    """整个请求（连接 + 读取响应）超过了总时限。"""


class _Cancelled(AIError):
    """用户在等待模型回复时点了停止。"""


class AIToolsUnsupported(AIError):
    """Raised when the endpoint rejects the ``tools`` field."""


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("Base URL 为空，请先在「设置」里填写服务商地址。")
    if base.endswith("/chat/completions"):
        return base
    if not re.search(r"/v\d+(?:beta)?$", base):
        base += "/v1"
    return base + "/chat/completions"


def _host(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc or url
    except ValueError:
        return url


def _connect_error(url: str, reason: object) -> AIError:
    host = _host(url)
    text = str(reason or "").strip() or "未知错误"
    timing_out = isinstance(reason, TimeoutError) or any(
        token in text.lower()
        for token in ("10060", "10061", "timed out", "timeout", "超时", "拒绝", "refused")
    )
    hint = "请检查网络是否可用、Base URL 是否填写正确，以及系统代理是否生效。"
    if host.endswith("api.openai.com"):
        hint += (
            "国内网络通常无法直连 api.openai.com，请改用服务商提供的 OpenAI 兼容地址"
            "（例如百炼、千问、DeepSeek 或本地 Ollama）。"
        )
    if timing_out:
        return AIError(f"连接 {host} 超时：{text}。{hint}")
    return AIError(f"无法连接 {host}：{text}。{hint}")


def _close_later(box: dict[str, Any], done: threading.Event) -> None:
    """请求已经放弃，但 worker 还攥着连接；等它返回后关掉，别泄漏 socket。"""

    def cleanup() -> None:
        if not done.wait(30):
            return
        response = box.get("response")
        try:
            if response is not None:
                response.close()
        except Exception:  # noqa: BLE001 - 清理失败不影响报错
            pass

    threading.Thread(target=cleanup, daemon=True).start()


def _fetch(
    request: urllib.request.Request,
    timeout: float,
    cancel: threading.Event | None = None,
) -> bytes:
    """把连接和读取整段放进工作线程，给整个请求一个总时限。

    ``urlopen(timeout=...)`` 只限制单次 socket 操作；服务端接受了连接却不发正文时，
    主线程会一直停在 ``read()`` 上。这里用总时限兜住，并且每 0.25 秒检查一次取消。
    """
    box: dict[str, Any] = {}
    done = threading.Event()

    def worker() -> None:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                box["response"] = response
                box["raw"] = response.read()
        except BaseException as exc:  # noqa: BLE001 - 原样抛回主线程
            box["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    budget = max(1.0, float(timeout) + 2.0)
    deadline = time.time() + budget
    while not done.is_set():
        if cancel is not None and cancel.is_set():
            _close_later(box, done)
            raise _Cancelled("已取消。")
        remaining = deadline - time.time()
        if remaining <= 0:
            _close_later(box, done)
            raise _DeadlineExceeded(
                f"请求在 {int(budget)} 秒内没有完成（连接或读取响应超时）"
            )
        done.wait(min(0.25, remaining))
    error = box.get("error")
    if isinstance(error, BaseException):
        raise error
    return box.get("raw") or b""


def _http_json(
    url: str,
    payload: dict,
    headers: dict,
    timeout: int,
    cancel: threading.Event | None = None,
) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        raw = _fetch(request, timeout, cancel)
    except _DeadlineExceeded as exc:
        raise AIError(
            f"接口 {_host(url)} 在 {int(max(1.0, float(timeout) + 2.0))} 秒内没有返回完整响应。"
            "请检查网络或代理是否稳定；也可以在「设置」里调大超时后重试。"
        ) from exc
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        hints = {
            401: "API Key 无效，或 Base URL 与 Key 不属于同一家服务商。",
            403: "这个 Key 没有访问当前模型或地址的权限。",
            404: "Base URL 或模型名不正确，有的服务地址需要以 /v1 结尾。",
            429: "请求太频繁或额度不足，请稍后重试。",
        }
        hint = hints.get(exc.code, "")
        message = f"接口返回 {exc.code}。" + (hint + " " if hint else "") + detail
        raise AIError(message.strip()) from exc
    except urllib.error.URLError as exc:
        raise _connect_error(url, exc.reason) from exc
    except TimeoutError as exc:
        raise _connect_error(url, exc) from exc
    except OSError as exc:
        raise _connect_error(url, exc) from exc
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise AIError("接口返回了无法解析的内容。") from exc
    if not isinstance(data, dict):
        raise AIError("接口返回格式不正确。")
    return data


def _text_from(data: dict) -> str:
    try:
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            error = error.get("message") or error
        raise AIError(f"接口没有返回有效回复。{error or ''}".strip()) from exc
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    text = str(content or "").strip()
    if not text:
        finish = str(choice.get("finish_reason") or "")
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        hints = []
        if finish:
            hints.append(f"finish_reason={finish}")
        if usage.get("completion_tokens") is not None:
            hints.append(f"completion_tokens={usage.get('completion_tokens')}")
        if message.get("reasoning_content"):
            hints.append("模型把输出用在了思考内容上")
        raise AIError("模型返回了空回复" + (f"（{'，'.join(hints)}）" if hints else "。"))
    return text


def _payload(ai: AIConfig, messages: list[dict[str, Any]]) -> tuple[str, dict, dict]:
    cfg = ai.normalized()
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if cfg.provider == "pollinations":
        url = "https://text.pollinations.ai/openai"
        headers["User-Agent"] = "DarkittenModAgent/2.0"
        payload: dict[str, Any] = {
            "model": cfg.model or "openai-fast",
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "private": True,
            "referrer": "darkitten-mod-agent",
        }
    elif cfg.provider == "ollama":
        url = _endpoint(cfg.base_url or "http://127.0.0.1:11434/v1")
        payload = {
            "model": cfg.model or "qwen2.5-coder:7b",
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }
    else:
        if not cfg.api_key:
            raise AIError("还没有填写 API Key。打开「设置」页填写后即可开始。")
        url = _endpoint(cfg.base_url or "https://api.openai.com/v1")
        headers["Authorization"] = f"Bearer {cfg.api_key}"
        payload = {
            "model": cfg.model or "gpt-4o-mini",
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
        }
        if "deepseek" in cfg.model.lower():
            payload["reasoning_effort"] = "none"
    return url, payload, headers


def _request_with_retries(
    ai: AIConfig,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str = "auto",
    cancel: threading.Event | None = None,
) -> dict:
    cfg = ai.normalized()
    url, payload, headers = _payload(ai, messages)
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    try:
        return _http_json(url, payload, headers, cfg.timeout, cancel)
    except _Cancelled:
        raise
    except AIError as exc:
        text = str(exc)
        if tools and re.search(r"接口返回 (?:400|404|422)", text):
            lowered = text.lower()
            if any(word in lowered for word in ("tool", "function", "unknown field", "invalid")):
                raise AIToolsUnsupported(text) from exc
        # 部分兼容接口不支持 reasoning_effort，去掉后重试一次。
        if "reasoning_effort" in payload and re.search(r"接口返回 (?:400|422)", text):
            retry = dict(payload)
            retry.pop("reasoning_effort", None)
            try:
                return _http_json(url, retry, headers, cfg.timeout, cancel)
            except _Cancelled:
                raise
            except AIError as retry_exc:
                text = str(retry_exc)
                payload = retry
        # max_tokens 超过服务端上限时逐级下调。
        if re.search(r"接口返回 (?:400|422)", text) and int(payload.get("max_tokens", 0)) > 4096:
            for limit in (4096, 2048):
                retry = dict(payload)
                retry["max_tokens"] = limit
                try:
                    return _http_json(url, retry, headers, cfg.timeout, cancel)
                except _Cancelled:
                    raise
                except AIError:
                    continue
        raise


def request_chat(
    ai: AIConfig,
    messages: list[dict[str, Any]],
    *,
    cancel: threading.Event | None = None,
) -> str:
    data = _request_with_retries(ai, messages, cancel=cancel)
    return _text_from(data)


def _tool_calls(message: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(message.get("tool_calls") or []):
        if not isinstance(item, dict):
            continue
        function = item.get("function") if isinstance(item.get("function"), dict) else {}
        name = str(function.get("name") or item.get("name") or "").strip()
        if not name:
            continue
        raw_args = function.get("arguments")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args or "{}", strict=False)
            except ValueError:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        rows.append(
            {
                "id": str(item.get("id") or f"call_{index}"),
                "name": name,
                "arguments": args if isinstance(args, dict) else {},
            }
        )
    return rows


def request_chat_tools(
    ai: AIConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    tool_choice: str = "auto",
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Native function calling. Returns content + parsed tool calls + raw message."""
    data = _request_with_retries(
        ai, messages, tools=tools, tool_choice=tool_choice, cancel=cancel
    )
    try:
        choice = data["choices"][0]
        message = choice.get("message") or {}
    except (KeyError, IndexError, TypeError) as exc:
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            error = error.get("message") or error
        raise AIError(f"接口没有返回有效回复。{error or ''}".strip()) from exc
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in (None, "text")
        )
    calls = _tool_calls(message)
    text = str(content or "").strip()
    if not text and not calls:
        finish = str(choice.get("finish_reason") or "")
        raise AIError("模型返回了空回复" + (f"（finish_reason={finish}）" if finish else "。"))
    return {
        "content": text,
        "tool_calls": calls,
        "message": message,
        "finish_reason": choice.get("finish_reason"),
    }


def test_connection(ai: AIConfig) -> str:
    reply = request_chat(
        ai,
        [
            {"role": "system", "content": "你是连通性测试助手，只回复 OK。"},
            {"role": "user", "content": "请回复 OK"},
        ],
    )
    return reply.strip()[:120]
