from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths

MAX_RESPONSE_BYTES = 300_000
ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConnectorError(Exception):
    pass


DEFAULT_CONNECTORS: list[dict[str, Any]] = [
    {
        "id": "modrinth",
        "name": "Modrinth",
        "base_url": "https://api.modrinth.com/v2",
        "description": "查询模组、版本、加载器与依赖。",
        "headers": {"User-Agent": "DarkittenModAgent/0.1 (mod development)"},
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "fabric-meta",
        "name": "Fabric Meta",
        "base_url": "https://meta.fabricmc.net/v2",
        "description": "查询 Fabric Loader、Yarn 映射与游戏版本。",
        "headers": {"User-Agent": "DarkittenModAgent/0.1"},
        "enabled": True,
        "builtin": True,
    },
    {
        "id": "github",
        "name": "GitHub API",
        "base_url": "https://api.github.com",
        "description": "查询公开仓库、Release 与源码。令牌可选，用 ${GITHUB_TOKEN} 引用环境变量。",
        "headers": {
            "Accept": "application/vnd.github+json",
            "User-Agent": "DarkittenModAgent/0.1",
        },
        "enabled": True,
        "builtin": True,
    },
]


def _now_id(value: str) -> str:
    text = re.sub(r"[^a-z0-9_.-]", "-", (value or "").strip().lower()).strip("-")
    return text[:64] or "connector"


def resolve_env(text: str) -> str:
    return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), str(text or ""))


def resolve_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (headers or {}).items():
        resolved = resolve_env(str(value))
        if resolved:
            result[str(key)] = resolved
    return result


@dataclass
class Connector:
    id: str
    name: str
    base_url: str
    description: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    builtin: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "description": self.description,
            "headers": dict(self.headers),
            "enabled": self.enabled,
            "builtin": self.builtin,
        }


class ConnectorStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or paths.connectors_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_builtin()

    def _load(self) -> list[Connector]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            return []
        items = data.get("connectors") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        result: list[Connector] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            result.append(
                Connector(
                    id=_now_id(str(item.get("id") or item.get("name") or "connector")),
                    name=str(item.get("name") or item.get("id") or "连接器"),
                    base_url=str(item.get("base_url") or "").strip(),
                    description=str(item.get("description") or ""),
                    headers={
                        str(k): str(v)
                        for k, v in (item.get("headers") or {}).items()
                        if isinstance(item.get("headers"), dict)
                    },
                    enabled=bool(item.get("enabled", True)),
                    builtin=bool(item.get("builtin", False)),
                )
            )
        return result

    def _save(self, items: list[Connector]) -> None:
        payload = {
            "version": 1,
            "connectors": [item.as_dict() for item in items],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def ensure_builtin(self) -> None:
        items = self._load()
        existing = {item.id for item in items}
        changed = False
        for raw in DEFAULT_CONNECTORS:
            if raw["id"] in existing:
                continue
            items.append(
                Connector(
                    id=raw["id"],
                    name=raw["name"],
                    base_url=raw["base_url"],
                    description=raw["description"],
                    headers=dict(raw["headers"]),
                    enabled=True,
                    builtin=True,
                )
            )
            changed = True
        if changed or not self.path.is_file():
            self._save(items)

    def list(self) -> list[Connector]:
        return self._load()

    def get(self, connector_id: str) -> Connector | None:
        return next((item for item in self._load() if item.id == connector_id), None)

    def save(self, connector: Connector) -> Connector:
        if not connector.base_url.startswith(("http://", "https://")):
            raise ConnectorError("连接器地址必须以 http:// 或 https:// 开头。")
        connector.id = _now_id(connector.id or connector.name)
        items = [item for item in self._load() if item.id != connector.id]
        items.append(connector)
        self._save(items)
        return connector

    def delete(self, connector_id: str) -> None:
        item = self.get(connector_id)
        if item and item.builtin:
            raise ConnectorError("内置连接器可以停用，但不能删除。")
        self._save([item for item in self._load() if item.id != connector_id])

    def set_enabled(self, connector_id: str, enabled: bool) -> None:
        item = self.get(connector_id)
        if item is None:
            raise ConnectorError("连接器不存在。")
        item.enabled = bool(enabled)
        self.save(item)

    def catalog_text(self) -> str:
        rows = [
            f"- {item.id}｜{item.name}：{item.base_url}（{item.description}）"
            for item in self.list()
            if item.enabled
        ]
        return "\n".join(rows) if rows else "（没有启用的连接器）"


def fetch_url(
    url: str,
    *,
    headers: dict[str, Any] | None = None,
    timeout: int = 30,
    max_bytes: int = MAX_RESPONSE_BYTES,
) -> str:
    text = str(url or "").strip()
    scheme = urllib.parse.urlparse(text).scheme.lower()
    if scheme not in ("http", "https"):
        raise ConnectorError("只允许访问 http:// 或 https:// 地址。")
    request = urllib.request.Request(
        text,
        headers=resolve_headers(
            {
                "User-Agent": "DarkittenModAgent/0.1",
                "Accept": "application/json, text/plain, */*",
                **(headers or {}),
            }
        ),
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(5, min(120, timeout))) as response:
            raw = response.read(max_bytes + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        raise ConnectorError(f"HTTP {exc.code}：{detail}".strip()) from exc
    except urllib.error.URLError as exc:
        raise ConnectorError(f"连接失败：{exc.reason}") from exc
    except TimeoutError as exc:
        raise ConnectorError("连接超时。") from exc
    except OSError as exc:
        raise ConnectorError(f"网络错误：{exc}") from exc
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        suffix = "\n…（响应过大，已截断）"
    else:
        suffix = ""
    return raw.decode("utf-8", "replace") + suffix


def test_connector(connector: Connector) -> str:
    if not connector.base_url:
        raise ConnectorError("连接器地址为空。")
    text = fetch_url(connector.base_url, headers=connector.headers, timeout=20, max_bytes=4000)
    return text[:500]
