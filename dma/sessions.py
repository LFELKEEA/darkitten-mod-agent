from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import paths


def _slug(project_root: Path | str) -> str:
    return hashlib.sha1(str(project_root).encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def _path(project_root: Path | str) -> Path:
    return paths.sessions_dir() / f"{_slug(project_root)}.json"


def load_history(project_root: Path | str) -> list[dict[str, str]]:
    try:
        data = json.loads(_path(project_root).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    items = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    result: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "")
        if role in {"user", "assistant"} and content:
            result.append({"role": role, "content": content})
    return result[-200:]


def save_history(project_root: Path | str, history: list[dict[str, Any]]) -> None:
    payload = {
        "project": str(project_root),
        "messages": [
            {"role": item.get("role"), "content": item.get("content")}
            for item in history[-200:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ],
    }
    try:
        _path(project_root).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_history(project_root: Path | str) -> None:
    try:
        _path(project_root).unlink(missing_ok=True)
    except OSError:
        pass
