"""AST-aware edits and hash-cached reads (TOOL-02 / TOOL-03).

Tools should address a class/method, not a fragile character range.  The
parser is intentionally lightweight, but it is enough to locate a method body,
replace it, or insert a member without corrupting braces.
"""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from .java import JavaFile, JavaMethod, _matching_brace, parse_java


@dataclass
class EditResult:
    path: str
    ok: bool
    kind: str = ""
    symbol: str = ""
    before_hash: str = ""
    after_hash: str = ""
    message: str = ""
    diff: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "kind": self.kind,
            "symbol": self.symbol,
            "before_hash": self.before_hash,
            "after_hash": self.after_hash,
            "message": self.message,
            "diff": self.diff,
        }


class FileCache:
    """Read cache keyed by content hash, with a remembered slice cursor."""

    def __init__(self, chunk_lines: int = 200):
        self.chunk_lines = max(20, chunk_lines)
        self._cache: dict[str, tuple[str, str]] = {}
        self._cursor: dict[str, int] = {}

    def read(self, path: Path) -> str:
        key = str(Path(path).resolve())
        try:
            data = Path(path).read_bytes()
        except OSError:
            return ""
        text = data.decode("utf-8", errors="replace")
        # Hash the raw bytes so the digest matches :meth:`hash` regardless of
        # newline translation or encoding fallbacks.
        self._cache[key] = (text, hashlib.sha256(data).hexdigest())
        return text

    def hash(self, path: Path) -> str:
        key = str(Path(path).resolve())
        item = self._cache.get(key)
        if item and item[1]:
            return item[1]
        try:
            data = Path(path).read_bytes()
        except OSError:
            return ""
        return hashlib.sha256(data).hexdigest()

    def unchanged(self, path: Path, expected_hash: str) -> bool:
        return bool(expected_hash) and self.hash(path) == expected_hash

    def slices(self, path: Path) -> list[dict]:
        text = self.read(path)
        lines = text.splitlines()
        rows: list[dict] = []
        for start in range(0, len(lines), self.chunk_lines):
            end = min(len(lines), start + self.chunk_lines)
            rows.append({
                "start": start + 1,
                "end": end,
                "hash": _hash_text("\n".join(lines[start:end])),
                "lines": end - start,
            })
        return rows

    def next_chunk(self, path: Path) -> dict:
        """Return the next unread slice; remember the cursor per file."""
        key = str(Path(path).resolve())
        lines = self.read(path).splitlines()
        start = self._cursor.get(key, 0)
        end = min(len(lines), start + self.chunk_lines)
        chunk = {
            "start": start + 1,
            "end": end,
            "text": "\n".join(lines[start:end]),
            "done": end >= len(lines),
            "total": len(lines),
        }
        self._cursor[key] = end
        return chunk

    def reset(self, path: Path | None = None) -> None:
        if path is None:
            self._cursor.clear()
        else:
            self._cursor.pop(str(Path(path).resolve()), None)

    def invalidate(self, path: Path) -> None:
        key = str(Path(path).resolve())
        self._cache.pop(key, None)
        self._cursor.pop(key, None)


def parse_path(path: Path) -> JavaFile:
    target = Path(path)
    return parse_java(target.read_text(encoding="utf-8", errors="replace"), str(target))


def replace_body_text(
    text: str,
    *,
    class_name: str,
    method: str,
    body: str,
    params: int | None = None,
    source: str = "",
) -> tuple[str | None, EditResult]:
    """Pure variant of :func:`replace_body` used by the agent tool layer."""
    java = parse_java(text, source)
    found = _find_method(java, class_name, method, params)
    if found is None:
        return None, EditResult(source, False, "replace_body", f"{class_name}#{method}", message="找不到方法")
    masked = java.masked
    offset = _line_offset(text, found.line)
    brace = masked.find("{", offset)
    if brace < 0:
        return None, EditResult(source, False, "replace_body", f"{class_name}#{method}", message="方法没有方法体")
    close = _matching_brace(masked, brace)
    if close < 0:
        return None, EditResult(source, False, "replace_body", f"{class_name}#{method}", message="方法体括号不匹配")
    indent = _indent_of(text, brace)
    new_body = _reindent(body, indent + "    ")
    updated = text[:brace + 1] + "\n" + new_body + "\n" + indent + text[close:]
    return updated, EditResult(source, True, "replace_body", f"{class_name}#{method}")


def insert_member_text(
    text: str,
    *,
    class_name: str,
    code: str,
    before: str = "",
    source: str = "",
) -> tuple[str | None, EditResult]:
    java = parse_java(text, source)
    java_class = java.class_named(class_name)
    if java_class is None:
        return None, EditResult(source, False, "insert_member", class_name, message="找不到类")
    masked = java.masked
    offset = _line_offset(text, java_class.line)
    brace = masked.find("{", offset)
    if brace < 0:
        return None, EditResult(source, False, "insert_member", class_name, message="类没有类体")
    close = _matching_brace(masked, brace)
    if close < 0:
        return None, EditResult(source, False, "insert_member", class_name, message="类体括号不匹配")
    if before:
        index = text.find(before, brace)
        if index >= 0:
            close = index
    indent = _indent_of(text, brace) + "    "
    block = _reindent(code, indent)
    insertion = "\n" + block + "\n" + _indent_of(text, brace)
    updated = text[:close] + insertion + text[close:]
    return updated, EditResult(source, True, "insert_member", class_name)


def replace_body(path: Path, class_name: str, method: str, body: str, *, params: int | None = None) -> EditResult:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    updated, result = replace_body_text(
        text, class_name=class_name, method=method, body=body, params=params, source=str(target)
    )
    if updated is None:
        return result
    return _write(target, text, updated, "replace_body", f"{class_name}#{method}")


def insert_member(path: Path, class_name: str, code: str, *, before: str = "") -> EditResult:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    updated, result = insert_member_text(
        text, class_name=class_name, code=code, before=before, source=str(target)
    )
    if updated is None:
        return result
    return _write(target, text, updated, "insert_member", class_name)


def replace_symbol(path: Path, old: str, new: str, *, count: int = 1) -> EditResult:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    if old not in text:
        return EditResult(str(target), False, "replace_symbol", old, message="找不到待替换文本")
    updated = text.replace(old, new, count)
    return _write(target, text, updated, "replace_symbol", old)


def diff_text(before: str, after: str, path: str = "") -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
    ))


# ----------------------------------------------------------------------
def _find_method(java: JavaFile, class_name: str, method: str, params: int | None) -> JavaMethod | None:
    for java_class in java.all_classes():
        if class_name and java_class.name != class_name and java_class.qualified != class_name:
            continue
        for item in java_class.methods:
            if item.name != method:
                continue
            if params is not None and len(item.params) != params:
                continue
            return item
    return None


def _line_offset(text: str, line: int) -> int:
    if line <= 1:
        return 0
    offset = 0
    for _ in range(line - 1):
        index = text.find("\n", offset)
        if index < 0:
            return len(text)
        offset = index + 1
    return offset


def _indent_of(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    line = text[start:index]
    return line[: len(line) - len(line.lstrip(" \t"))] if line.strip() == "" else line[: len(line) - len(line.lstrip())] or ""


def _reindent(code: str, indent: str) -> str:
    rows = code.replace("\r\n", "\n").strip("\n").split("\n")
    if not rows:
        return ""
    base = min((len(row) - len(row.lstrip()) for row in rows if row.strip()), default=0)
    return "\n".join(indent + row[base:] if row.strip() else "" for row in rows)


def _write(target: Path, before: str, after: str, kind: str, symbol: str) -> EditResult:
    if before == after:
        return EditResult(str(target), True, kind, symbol, _hash_text(before), _hash_text(after), "没有变化")
    target.write_text(after, encoding="utf-8")
    return EditResult(
        str(target), True, kind, symbol,
        _hash_text(before), _hash_text(after), "已写入",
        diff_text(before, after, target.name),
    )


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def iter_methods(java: JavaFile) -> Iterator[tuple[str, JavaMethod]]:
    for item in java.all_classes():
        for method in item.methods:
            yield item.name, method
