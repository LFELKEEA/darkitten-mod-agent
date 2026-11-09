"""Offline cache for symbols, mappings, templates, assets and docs (TOOL-20).

Network access is optional in this product.  Everything the agent needs to
repair a mod must therefore be cacheable, hash-verified and prunable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_MAX_BYTES = 256 * 1024 * 1024


@dataclass
class CacheEntry:
    key: str
    path: str
    sha256: str
    size: int
    source: str = ""
    updated_at: float = 0.0
    kind: str = "file"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "source": self.source,
            "updated_at": self.updated_at,
            "kind": self.kind,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CacheEntry":
        return cls(
            key=str(raw.get("key") or ""),
            path=str(raw.get("path") or ""),
            sha256=str(raw.get("sha256") or ""),
            size=int(raw.get("size") or 0),
            source=str(raw.get("source") or ""),
            updated_at=float(raw.get("updated_at") or 0),
            kind=str(raw.get("kind") or "file"),
        )


class OfflineCache:
    def __init__(self, root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES):
        self.root = Path(root)
        self.base = self.root / ".dma" / "cache"
        self.files = self.base / "files"
        self.manifest_path = self.base / "manifest.json"
        self.max_bytes = max_bytes
        self.entries: dict[str, CacheEntry] = {}
        self._load()

    # ------------------------------------------------------------------
    def _load(self) -> None:
        if not self.manifest_path.is_file():
            return
        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in raw.get("entries") or []:
            entry = CacheEntry.from_dict(item)
            if entry.key:
                self.entries[entry.key] = entry

    def _save(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema": "dma-cache/1",
            "max_bytes": self.max_bytes,
            "entries": [item.to_dict() for item in sorted(self.entries.values(), key=lambda row: row.key)],
        }
        temporary = self.manifest_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.manifest_path)

    def _slug(self, key: str) -> str:
        value = re.sub(r"[^\w.-]+", "_", key).strip("._")
        if not value:
            value = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
        return value

    # ------------------------------------------------------------------
    def put_bytes(self, key: str, data: bytes, *, source: str = "", kind: str = "file") -> CacheEntry:
        slug = self._slug(key)
        target = self.files / slug
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
        entry = CacheEntry(
            key=key,
            path=str(target.relative_to(self.root)),
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            source=source,
            updated_at=time.time(),
            kind=kind,
        )
        self.entries[key] = entry
        self._save()
        return entry

    def put_file(self, key: str, path: Path, *, source: str = "", kind: str = "file") -> CacheEntry:
        return self.put_bytes(key, Path(path).read_bytes(), source=source, kind=kind)

    def put_json(self, key: str, value: Any, *, source: str = "") -> CacheEntry:
        return self.put_bytes(
            key,
            json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8"),
            source=source,
            kind="json",
        )

    def get_bytes(self, key: str) -> bytes | None:
        entry = self.entries.get(key)
        if not entry:
            return None
        path = self.root / entry.path
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != entry.sha256:
            return None
        return data

    def get_json(self, key: str) -> Any:
        data = self.get_bytes(key)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8"))
        except ValueError:
            return None

    def get_path(self, key: str) -> Path | None:
        entry = self.entries.get(key)
        if not entry:
            return None
        path = self.root / entry.path
        return path if path.is_file() else None

    def has(self, key: str) -> bool:
        return self.get_path(key) is not None

    # ------------------------------------------------------------------
    def import_tree(self, key: str, directory: Path, *, source: str = "") -> CacheEntry:
        """Cache an entire directory as one zip entry (templates/assets)."""
        directory = Path(directory)
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        temporary = self.base / f"{self._slug(key)}.zip.tmp"
        temporary.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(directory).as_posix())
        data = temporary.read_bytes()
        temporary.unlink(missing_ok=True)
        return self.put_bytes(key, data, source=source, kind="tree")

    def export_tree(self, key: str, target: Path) -> Path:
        data = self.get_bytes(key)
        if data is None:
            raise KeyError(f"缓存中没有目录：{key}")
        target = Path(target)
        target.mkdir(parents=True, exist_ok=True)
        temp_zip = target.parent / f"{target.name}.tmp.zip"
        temp_zip.write_bytes(data)
        with zipfile.ZipFile(temp_zip) as archive:
            archive.extractall(target)
        temp_zip.unlink(missing_ok=True)
        return target

    # ------------------------------------------------------------------
    def verify(self) -> dict[str, list[str]]:
        missing: list[str] = []
        corrupted: list[str] = []
        for key, entry in self.entries.items():
            path = self.root / entry.path
            if not path.is_file():
                missing.append(key)
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != entry.sha256:
                corrupted.append(key)
        return {"missing": missing, "corrupted": corrupted}

    def prune(self, *, max_bytes: int | None = None) -> list[str]:
        limit = max_bytes if max_bytes is not None else self.max_bytes
        rows = sorted(self.entries.values(), key=lambda item: item.updated_at)
        total = sum(item.size for item in rows)
        removed: list[str] = []
        while total > limit and rows:
            entry = rows.pop(0)
            path = self.root / entry.path
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self.entries.pop(entry.key, None)
            removed.append(entry.key)
            total -= entry.size
        if removed:
            self._save()
        return removed

    def summary(self) -> dict[str, Any]:
        total = sum(item.size for item in self.entries.values())
        return {
            "root": str(self.base),
            "entries": len(self.entries),
            "bytes": total,
            "max_bytes": self.max_bytes,
            "kinds": _count(item.kind for item in self.entries.values()),
        }


def package_offline(root: Path, out: Path, *, extras: Iterable[Path] = ()) -> Path:
    """Zip the index/cache plus explicit extras for a fully offline run."""
    root = Path(root)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in (".dma/index", ".dma/cache"):
            base = root / relative
            if not base.is_dir():
                continue
            for path in base.rglob("*"):
                if path.is_file():
                    archive.write(path, f"{relative}/{path.relative_to(base).as_posix()}")
        for extra in extras:
            extra = Path(extra)
            if extra.is_file():
                archive.write(extra, extra.name)
            elif extra.is_dir():
                for path in extra.rglob("*"):
                    if path.is_file():
                        archive.write(path, f"{extra.name}/{path.relative_to(extra).as_posix()}")
    return target


def _count(values: Iterable[str]) -> dict[str, int]:
    rows: dict[str, int] = {}
    for value in values:
        rows[value] = rows.get(value, 0) + 1
    return rows
