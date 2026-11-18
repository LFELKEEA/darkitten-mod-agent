"""Offline API signature index built from cached loader sources.

The online search in :mod:`dma.devdocs` answers "where is this string" by
scanning a ``sources.jar`` every time.  That is fine for a one-off lookup but
wasteful inside a long agent run.  This module builds a compact, deterministic
signature index once per source jar:

* classes (kind, modifiers, extends, implements);
* public/protected methods (name, parameters, return type, static);
* public/protected fields (name, type, static).

The index lives under ``data/online/apiindex/<loader>/<jar>.json`` and is
rebuilt only when the jar changes.  Queries never touch the network; callers
can ask for the jar to be downloaded first via :func:`ensure`.
"""

from __future__ import annotations

import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import devdocs, paths
from .kernel.java import parse_java

SCHEMA = "dma-apiindex/1"
MAX_ENTRY_BYTES = 1_500_000
MAX_ENTRIES = 8_000
MAX_CLASSES = 25_000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
Progress = Callable[[int, str], None]


def index_root() -> Path:
    path = paths.data_dir() / "online" / "apiindex"
    path.mkdir(parents=True, exist_ok=True)
    return path


def index_path(loader: str, jar: Path) -> Path:
    key = str(loader or "unknown").lower() or "unknown"
    return index_root() / key / f"{Path(jar).stem}.json"


def _public(modifiers: Iterable[str]) -> bool:
    values = {str(item) for item in modifiers}
    return "public" in values or "protected" in values


def _walk(classes: Iterable[Any], prefix: str = "") -> Iterable[tuple[str, Any]]:
    for item in classes:
        name = f"{prefix}.{item.name}" if prefix else item.name
        yield name, item
        yield from _walk(item.inner, name)


def _signature(method: Any) -> dict[str, Any]:
    return {
        "name": "<init>" if method.is_constructor else method.name,
        "params": [str(param_type) for param_type, _ in method.params],
        "returns": str(method.return_type or ""),
        "modifiers": sorted(str(item) for item in method.modifiers),
        "static": "static" in method.modifiers,
        "constructor": bool(method.is_constructor),
    }


def _class_record(java_class: Any, entry: str) -> dict[str, Any]:
    methods: list[dict[str, Any]] = []
    for method in java_class.methods:
        if "private" in method.modifiers:
            continue
        if not _public(method.modifiers) and not method.is_constructor:
            continue
        methods.append(_signature(method))
    fields: list[dict[str, Any]] = []
    for thing in java_class.fields:
        if not _public(thing.modifiers):
            continue
        fields.append(
            {
                "name": str(thing.name),
                "type": str(thing.type or ""),
                "modifiers": sorted(str(item) for item in thing.modifiers),
                "static": "static" in thing.modifiers,
            }
        )
    return {
        "kind": str(java_class.kind or "class"),
        "modifiers": sorted(str(item) for item in java_class.modifiers),
        "extends": str(java_class.extends or ""),
        "implements": sorted(str(item) for item in java_class.implements),
        "methods": methods,
        "fields": fields,
        "path": entry,
    }


def build(
    jar: Path,
    loader: str = "",
    *,
    progress: Progress | None = None,
    force: bool = False,
    persist: bool = True,
) -> dict[str, Any]:
    jar = Path(jar)
    if not jar.is_file():
        raise FileNotFoundError(f"sources.jar 不存在：{jar}")
    target = index_path(loader, jar)
    if target.is_file() and not force:
        try:
            if target.stat().st_mtime >= jar.stat().st_mtime:
                cached = _load(target)
                if cached is not None:
                    return cached
        except OSError:
            pass

    classes: dict[str, dict[str, Any]] = {}
    entries = 0
    processed = 0
    total_bytes = 0
    truncated = False
    with zipfile.ZipFile(jar) as archive:
        infos = [item for item in archive.infolist() if not item.is_dir()]
        total = len(infos)
        for info in infos:
            name = info.filename
            if not name.lower().endswith(".java"):
                continue
            if info.file_size > MAX_ENTRY_BYTES:
                continue
            if entries >= MAX_ENTRIES or len(classes) >= MAX_CLASSES or total_bytes >= MAX_TOTAL_BYTES:
                truncated = True
                break
            try:
                raw = archive.read(info)
            except (OSError, KeyError):
                continue
            entries += 1
            total_bytes += len(raw)
            processed += 1
            if progress is not None and processed % 200 == 0:
                percent = min(95, int(processed * 100 / max(1, min(total, MAX_ENTRIES))))
                progress(percent, f"解析源码 {entries} 个文件，已索引 {len(classes)} 个类")
            text = raw.decode("utf-8", errors="replace")
            try:
                java = parse_java(text, name)
            except Exception:  # noqa: BLE001 - 单个源文件解析失败不能中断整库
                continue
            package = str(java.package or "")
            for simple, java_class in _walk(java.classes):
                qualified = f"{package}.{simple}" if package else simple
                if qualified in classes:
                    continue
                if "private" in java_class.modifiers:
                    continue
                classes[qualified] = _class_record(java_class, name)
                if len(classes) >= MAX_CLASSES:
                    truncated = True
                    break

    counts = {
        "classes": len(classes),
        "methods": sum(len(item["methods"]) for item in classes.values()),
        "fields": sum(len(item["fields"]) for item in classes.values()),
        "entries": entries,
        "bytes": total_bytes,
    }
    payload = {
        "schema": SCHEMA,
        "loader": str(loader or "").lower(),
        "source": str(jar),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "truncated": truncated,
        "counts": counts,
        "classes": dict(sorted(classes.items())),
    }
    if persist:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
        except OSError:
            pass
    if progress is not None:
        progress(100, f"索引完成：{counts['classes']} 个类 / {counts['methods']} 个方法")
    return payload


def _load(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    if not isinstance(raw.get("classes"), dict):
        return None
    return raw


def load(loader: str, jar: Path) -> dict[str, Any] | None:
    return _load(index_path(loader, jar))


def ensure(
    loader: str,
    mc: str = "",
    version: str = "",
    *,
    allow_download: bool = True,
    force: bool = False,
    progress: Progress | None = None,
) -> tuple[dict[str, Any] | None, Path | None, list[str]]:
    """Return ``(index, jar, notes)``; download the sources jar if allowed."""
    notes: list[str] = []
    jar: Path | None = None
    if allow_download:
        try:
            jar = devdocs.ensure_sources(loader, mc, version, progress=progress, force=False)
        except Exception as exc:  # noqa: BLE001 - 下载失败要退回缓存
            notes.append(str(exc))
    if jar is None:
        jar = devdocs.cached_sources(loader, mc, version)
    if jar is None or not Path(jar).is_file():
        notes.append(
            "没有可用的 sources.jar；请先调用 resource_ensure kind=sources 或 dev_docs action=sources。"
        )
        return None, None, notes
    try:
        index = build(jar, loader, progress=progress, force=force)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"构建签名索引失败：{exc}")
        return None, Path(jar), notes
    if index.get("truncated"):
        notes.append(
            "源码规模超过索引上限，结果只覆盖前 "
            f"{index['counts']['entries']} 个文件；需要更精确的结果时用 dev_docs lookup。"
        )
    return index, Path(jar), notes


def _tokens(text: str) -> list[str]:
    return [item for item in re.split(r"[^A-Za-z0-9_$]+", text) if item]


def query(
    index: dict[str, Any],
    text: str,
    *,
    kind: str = "auto",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search an index by class / method / field name, ranked deterministically."""
    value = str(text or "").strip()
    if not value:
        return []
    lowered = value.lower()
    simple_query = lowered.rsplit(".", 1)[-1]
    kind = str(kind or "auto").lower()
    limit = max(1, min(int(limit or 20), 100))
    rows: list[tuple[int, dict[str, Any]]] = []

    def add(score: int, payload: dict[str, Any]) -> None:
        if score > 0:
            rows.append((score, payload))

    for name, item in (index.get("classes") or {}).items():
        lowered_name = name.lower()
        simple = lowered_name.rsplit(".", 1)[-1]
        if kind in {"auto", "class"}:
            if lowered_name == lowered:
                add(140, {"hit": "class", "class": name, **item})
            elif simple == simple_query:
                add(120, {"hit": "class", "class": name, **item})
            elif simple.startswith(simple_query):
                add(80, {"hit": "class", "class": name, **item})
            elif simple_query in simple:
                add(55, {"hit": "class", "class": name, **item})
            elif simple_query in lowered_name:
                add(25, {"hit": "class", "class": name, **item})
        if kind in {"auto", "method"}:
            for method in item.get("methods") or []:
                method_name = str(method.get("name") or "").lower()
                if method_name == lowered or method_name == simple_query:
                    add(110, {"hit": "method", "class": name, **method})
                elif simple_query and method_name.startswith(simple_query):
                    add(70, {"hit": "method", "class": name, **method})
                elif simple_query and simple_query in method_name:
                    add(45, {"hit": "method", "class": name, **method})
        if kind in {"auto", "field"}:
            for field_item in item.get("fields") or []:
                field_name = str(field_item.get("name") or "").lower()
                if field_name == lowered or field_name == simple_query:
                    add(100, {"hit": "field", "class": name, **field_item})
                elif simple_query and field_name.startswith(simple_query):
                    add(60, {"hit": "field", "class": name, **field_item})

    rows.sort(key=lambda pair: pair[0], reverse=True)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for _score, payload in rows:
        key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        out.append(payload)
        if len(out) >= limit:
            break
    return out


def render(result: list[dict[str, Any]], *, limit: int = 20) -> str:
    """Render query hits as compact, copyable lines."""
    if not result:
        return "没有匹配的类 / 方法 / 字段。"
    lines: list[str] = []
    for item in result[:limit]:
        hit = item.get("hit")
        owner = item.get("class") or ""
        if hit == "class":
            header = f"{'interface' if item.get('kind') == 'interface' else item.get('kind', 'class')} {owner}"
            lines.append(f"- {header}")
            if item.get("extends"):
                lines.append(f"  extends {item['extends']}")
            if item.get("implements"):
                lines.append(f"  implements {', '.join(item['implements'])}")
        elif hit == "method":
            params = ", ".join(item.get("params") or [])
            static = "static " if item.get("static") else ""
            returns = item.get("returns") or ""
            lines.append(f"- {owner}#{item.get('name')}({params}) → {returns}  [{static}method]")
        elif hit == "field":
            static = "static " if item.get("static") else ""
            lines.append(f"- {owner}#{item.get('name')} : {item.get('type') or '?'}  [{static}field]")
    return "\n".join(lines)


def stats(index: dict[str, Any]) -> dict[str, Any]:
    return {
        "loader": index.get("loader", ""),
        "source": index.get("source", ""),
        "built_at": index.get("built_at", ""),
        "truncated": bool(index.get("truncated")),
        "counts": index.get("counts") or {},
    }
