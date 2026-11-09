"""File snapshots, diffs and rollback (checklist P0-03).

Snapshots live under ``<project>/.dma/snapshots/<id>/``.  Every snapshot
contains a ``manifest.json`` (relative path -> sha256/size) and a ``files``
tree.  Restores never destroy data: files that did not exist in the snapshot
are moved to ``.dma/trash/<stamp>/`` instead of being unlinked.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SNAPSHOT_SCHEMA = "dma-snapshot/2"
SNAPSHOT_DIR = Path(".dma") / "snapshots"
TRASH_DIR = Path(".dma") / "trash"

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".idea", ".vscode", ".gradle", "build", "out", "target",
    "run", "runs", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".venv", "venv", ".dma/snapshots", ".dma/trash",
}
DEFAULT_EXCLUDE_SUFFIXES = {
    ".class", ".pyc", ".pyo", ".log", ".lock", ".tmp", ".part",
}
# Gradle caches are large and reproducible; the wrapper jar is kept.
BIG_FILE_BYTES = 16 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")


@dataclass
class Snapshot:
    id: str
    label: str = "auto"
    note: str = ""
    created_at: str = ""
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    schema: str = SNAPSHOT_SCHEMA

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_bytes(self) -> int:
        return sum(int(item.get("size") or 0) for item in self.files.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "id": self.id,
            "label": self.label,
            "note": self.note,
            "created_at": self.created_at,
            "files": self.files,
            "skipped": list(self.skipped),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Snapshot":
        return cls(
            id=str(raw.get("id") or ""),
            label=str(raw.get("label") or "auto"),
            note=str(raw.get("note") or ""),
            created_at=str(raw.get("created_at") or ""),
            files={
                str(key): dict(value)
                for key, value in (raw.get("files") or {}).items()
                if isinstance(value, dict)
            },
            skipped=[str(item) for item in raw.get("skipped") or []],
            schema=str(raw.get("schema") or SNAPSHOT_SCHEMA),
        )


class SnapshotStore:
    """Snapshot stack with diff / restore / bisect helpers."""

    def __init__(
        self,
        root: Path,
        *,
        exclude_dirs: Iterable[str] | None = None,
        exclude_suffixes: Iterable[str] | None = None,
        max_file_bytes: int = BIG_FILE_BYTES,
        keep: int = 24,
    ) -> None:
        self.root = Path(root).resolve()
        self.exclude_dirs = set(DEFAULT_EXCLUDE_DIRS)
        if exclude_dirs:
            self.exclude_dirs.update(str(item).replace("\\", "/").strip("/") for item in exclude_dirs)
        self.exclude_suffixes = set(DEFAULT_EXCLUDE_SUFFIXES)
        if exclude_suffixes:
            self.exclude_suffixes.update(str(item).lower() for item in exclude_suffixes)
        self.max_file_bytes = int(max_file_bytes)
        self.keep = int(keep)

    # ------------------------------------------------------------------
    @property
    def base(self) -> Path:
        return self.root / SNAPSHOT_DIR

    def _dir_for(self, snapshot_id: str) -> Path:
        return self.base / snapshot_id

    def _excluded(self, relative: str) -> bool:
        parts = relative.replace("\\", "/").split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            if prefix in self.exclude_dirs:
                return True
        if Path(relative).suffix.lower() in self.exclude_suffixes:
            return True
        return False

    def _iter_files(self) -> Iterable[str]:
        for current, dirnames, filenames in os.walk(self.root):
            current_path = Path(current)
            relative_dir = "" if current_path == self.root else str(
                current_path.relative_to(self.root)
            ).replace("\\", "/")
            # Prune common heavy directories early.
            pruned = []
            for name in dirnames:
                rel = f"{relative_dir}/{name}".strip("/")
                if rel in self.exclude_dirs or name in {".git", ".gradle", "build", "out"}:
                    continue
                pruned.append(name)
            dirnames[:] = pruned
            for name in filenames:
                rel = f"{relative_dir}/{name}".strip("/")
                if not rel or self._excluded(rel):
                    continue
                yield rel

    def _current_manifest(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for relative in self._iter_files():
            path = self.root / relative
            try:
                stat = path.stat()
                if stat.st_size > self.max_file_bytes:
                    continue
                result[relative] = {
                    "sha256": _sha256(path),
                    "size": stat.st_size,
                }
            except OSError:
                continue
        return result

    # ------------------------------------------------------------------
    def create(self, label: str = "auto", note: str = "") -> Snapshot:
        snapshot_id = f"{_stamp()}-{label or 'auto'}"
        target = self._dir_for(snapshot_id)
        files_root = target / "files"
        files_root.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, dict[str, Any]] = {}
        skipped: list[str] = []
        for relative in self._iter_files():
            source = self.root / relative
            try:
                stat = source.stat()
            except OSError:
                continue
            if stat.st_size > self.max_file_bytes:
                skipped.append(relative)
                continue
            destination = files_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, destination)
            except OSError:
                skipped.append(relative)
                continue
            manifest[relative] = {
                "sha256": _sha256(destination),
                "size": stat.st_size,
            }
        snapshot = Snapshot(
            id=snapshot_id,
            label=label or "auto",
            note=note,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            files=manifest,
            skipped=skipped,
        )
        (target / "manifest.json").write_text(
            json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.prune()
        return snapshot

    def load(self, snapshot_id: str) -> Snapshot | None:
        path = self._dir_for(snapshot_id) / "manifest.json"
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return Snapshot.from_dict(raw)

    def snapshots(self) -> list[Snapshot]:
        """Newest first."""
        if not self.base.is_dir():
            return []
        items: list[Snapshot] = []
        for entry in self.base.iterdir():
            if not entry.is_dir():
                continue
            snapshot = self.load(entry.name)
            if snapshot is not None:
                items.append(snapshot)
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def latest(self) -> Snapshot | None:
        items = self.snapshots()
        return items[0] if items else None

    # ------------------------------------------------------------------
    def diff(self, from_id: str, to_id: str | None = None) -> dict[str, list[str]]:
        """Diff a snapshot against another snapshot or the working tree."""
        left = self.load(from_id)
        if left is None:
            raise KeyError(f"快照不存在：{from_id}")
        right = self._current_manifest() if to_id is None else (
            (self.load(to_id) or Snapshot(id=to_id)).files
        )
        left_files = set(left.files)
        right_files = set(right)
        added = sorted(right_files - left_files)
        removed = sorted(left_files - right_files)
        modified = sorted(
            path for path in left_files & right_files
            if left.files[path].get("sha256") != right[path].get("sha256")
        )
        return {"added": added, "modified": modified, "removed": removed}

    def _trash(self, snapshot_id: str) -> Path:
        path = self.root / TRASH_DIR / f"{_stamp()}-{snapshot_id}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _safe_relative(relative: str) -> str | None:
        value = str(relative or "").replace("\\", "/").lstrip("/")
        parts = [part for part in value.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            return None
        return "/".join(parts)

    def restore(self, snapshot_id: str, *, dry_run: bool = False) -> dict[str, Any]:
        """Restore files from a snapshot.

        Returns a plan with ``restored`` / ``removed`` / ``trashed`` lists.
        In dry-run mode nothing is written.
        """
        snapshot = self.load(snapshot_id)
        if snapshot is None:
            raise KeyError(f"快照不存在：{snapshot_id}")
        source_root = self._dir_for(snapshot_id) / "files"
        current = self._current_manifest()
        restored: list[str] = []
        removed: list[str] = []
        trashed: list[str] = []
        errors: list[str] = []

        for relative, meta in snapshot.files.items():
            safe = self._safe_relative(relative)
            if not safe:
                continue
            source = source_root / safe
            if not source.is_file():
                continue
            destination = self.root / safe
            if current.get(safe, {}).get("sha256") == meta.get("sha256"):
                continue
            if dry_run:
                restored.append(safe)
                continue
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    removed.append(safe)
                tmp = destination.with_name(destination.name + ".dma-restore")
                shutil.copy2(source, tmp)
                os.replace(tmp, destination)
                restored.append(safe)
            except OSError as exc:
                errors.append(f"{safe}: {exc}")

        trash_root: Path | None = None
        for relative in current:
            if relative in snapshot.files:
                continue
            safe = self._safe_relative(relative)
            if not safe:
                continue
            destination = self.root / safe
            if not destination.is_file():
                continue
            if dry_run:
                trashed.append(safe)
                continue
            if trash_root is None:
                trash_root = self._trash(snapshot_id)
            target = trash_root / safe
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(target))
                trashed.append(safe)
            except OSError as exc:
                errors.append(f"{safe}: {exc}")

        return {
            "ok": not errors,
            "snapshot": snapshot_id,
            "dry_run": dry_run,
            "restored": restored,
            "overwritten": removed,
            "trashed": trashed,
            "trash_dir": str(trash_root) if trash_root else "",
            "errors": errors,
        }

    def mark_good(self, snapshot_id: str) -> None:
        path = self.root / ".dma" / "latest_good.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"snapshot": snapshot_id, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def latest_good(self) -> str:
        path = self.root / ".dma" / "latest_good.json"
        if not path.is_file():
            return ""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ""
        return str(raw.get("snapshot") or "") if isinstance(raw, dict) else ""

    def prune(self, keep: int | None = None) -> list[str]:
        """Move old snapshots to trash.  Returns removed ids."""
        limit = self.keep if keep is None else int(keep)
        items = self.snapshots()
        if limit <= 0 or len(items) <= limit:
            return []
        good = self.latest_good()
        victims = [item for item in items[limit:] if item.id != good]
        if not victims:
            return []
        trash = self.root / TRASH_DIR / f"{_stamp()}-prune"
        trash.mkdir(parents=True, exist_ok=True)
        moved: list[str] = []
        for item in victims:
            source = self._dir_for(item.id)
            try:
                shutil.move(str(source), str(trash / item.id))
                moved.append(item.id)
            except OSError:
                continue
        return moved

    def bisect(
        self,
        snapshot_ids: list[str],
        predicate: Callable[[str], bool],
    ) -> dict[str, Any]:
        """Find the first snapshot (oldest -> newest) that fails ``predicate``.

        ``predicate`` returns True for a good build.  The caller supplies an
        ordered list; this helper assumes the oldest is good.
        """
        ordered = list(snapshot_ids)
        if not ordered:
            return {"ok": False, "error": "没有可二分的历史快照"}
        low, high = 0, len(ordered) - 1
        worst: str | None = None
        while low <= high:
            middle = (low + high) // 2
            snapshot_id = ordered[middle]
            if predicate(snapshot_id):
                low = middle + 1
            else:
                worst = snapshot_id
                high = middle - 1
        return {
            "ok": worst is not None,
            "bad": worst or "",
            "index": ordered.index(worst) if worst else -1,
            "checked": len(ordered),
        }
