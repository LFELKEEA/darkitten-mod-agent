from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from .config import AIConfig


class AIError(Exception):
    pass


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise AIError("接口地址为空，请先在「连接器」里填写。")
    if base.endswith("/chat/completions"):
        return base
    if not re.search(r"/v\d+(?:beta)?$", base):
        base += "/v1"
    return base + "/chat/completions"


def _http_json(url: str, payload: dict, headers: dict, timeout: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:600]
        except Exception:
            pass
        raise AIError(f"接口返回 {exc.code}。{detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise AIError(f"网络连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise AIError("连接超时，可以在连接器里把超时时间调大。") from exc
    except OSError as exc:
        raise AIError(f"网络错误：{exc}") from exc
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


def request_chat(ai: AIConfig, messages: list[dict[str, Any]]) -> str:
    cfg = ai.normalized()
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if cfg.provider == "pollinations":
        url = "https://text.pollinations.ai/openai"
        headers["User-Agent"] = "DarkittenModAgent/0.1"
        payload: dict[str, Any] = {
            "model": cfg.model or "openai-fast",
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": min(4096, cfg.max_tokens),
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
            raise AIError("还没有填写 API Key。打开「连接器」页填写后即可开始。")
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

    data = _http_json(url, payload, headers, cfg.timeout)
    try:
        return _text_from(data)
    except AIError as exc:
        text = str(exc)
        # 部分兼容接口不支持 reasoning_effort，去掉后重试一次。
        if "reasoning_effort" in payload and re.search(r"接口返回 (?:400|422)", text):
            retry = dict(payload)
            retry.pop("reasoning_effort", None)
            return _text_from(_http_json(url, retry, headers, cfg.timeout))
        # max_tokens 超过服务端上限时逐级下调。
        if re.search(r"接口返回 (?:400|422)", text) and int(payload.get("max_tokens", 0)) > 4096:
            for limit in (4096, 2048):
                retry = dict(payload)
                retry["max_tokens"] = limit
                try:
                    return _text_from(_http_json(url, retry, headers, cfg.timeout))
                except AIError:
                    continue
        raise


def test_connection(ai: AIConfig) -> str:
    reply = request_chat(
        ai,
        [
            {"role": "system", "content": "你是连通性测试助手，只回复 OK。"},
            {"role": "user", "content": "请回复 OK"},
        ],
    )
    return reply.strip()[:120]
