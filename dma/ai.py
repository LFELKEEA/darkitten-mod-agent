from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import AIConfig


class AIError(Exception):
    pass


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
