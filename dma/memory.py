from __future__ import annotations

import builtins
import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT 'note',
    content TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project);
CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at);
"""


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or paths.memory_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def add(
        self,
        content: str,
        *,
        project: str = "",
        kind: str = "note",
        tags: str = "",
    ) -> int:
        text = str(content or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空。")
        stamp = _now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT INTO memories(project, kind, content, tags, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(project or ""), str(kind or "note"), text, str(tags or ""), stamp, stamp),
            )
            return int(cursor.lastrowid)

    def update(self, memory_id: int, content: str, *, tags: str | None = None) -> None:
        text = str(content or "").strip()
        if not text:
            raise ValueError("记忆内容不能为空。")
        with self._lock, self._conn:
            if tags is None:
                self._conn.execute(
                    "UPDATE memories SET content=?, updated_at=? WHERE id=?",
                    (text, _now(), int(memory_id)),
                )
            else:
                self._conn.execute(
                    "UPDATE memories SET content=?, tags=?, updated_at=? WHERE id=?",
                    (text, str(tags), _now(), int(memory_id)),
                )

    def delete(self, memory_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM memories WHERE id=?", (int(memory_id),))

    def get(self, memory_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memories WHERE id=?", (int(memory_id),)
            ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        *,
        project: str = "",
        query: str = "",
        limit: int = 200,
    ) -> builtins.list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if project:
            clauses.append("project=?")
            args.append(project)
        needle = str(query or "").strip()
        if needle:
            clauses.append("(content LIKE ? OR tags LIKE ?)")
            like = f"%{needle}%"
            args.extend([like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memories {where} ORDER BY updated_at DESC, id DESC LIMIT ?"
        args.append(max(1, min(1000, int(limit))))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def recall(self, project: str, query: str = "", limit: int = 8) -> builtins.list[dict[str, Any]]:
        items = self.list(project=project, query=query, limit=limit) if query else []
        if len(items) < limit:
            seen = {item["id"] for item in items}
            for item in self.list(project=project, limit=limit * 2):
                if item["id"] not in seen:
                    items.append(item)
                    seen.add(item["id"])
                if len(items) >= limit:
                    break
        return items[:limit]

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
        return int(row["n"] if row else 0)

    def export_json(self, target: Path) -> Path:
        payload = {
            "version": 1,
            "exported_at": _now(),
            "memories": self.list(limit=10000),
        }
        target = Path(target)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target


def memory_context(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = ["以下是这个工程相关的长期记忆，写代码前先参考，避免重复踩坑："]
    for item in items:
        tag = f"（{item.get('tags')}）" if item.get("tags") else ""
        lines.append(f"- [{item.get('kind', 'note')}] {item.get('content', '')}{tag}")
    return "\n".join(lines)
