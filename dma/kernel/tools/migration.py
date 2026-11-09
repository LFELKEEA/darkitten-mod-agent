"""Save/config migration framework (P1-19 / P3-30 / P3-31)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

MIGRATION_SCHEMA = "dma-migration/1"


@dataclass
class Migration:
    modid: str
    from_version: str
    to_version: str
    ids: dict[str, str] = field(default_factory=dict)
    config_keys: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return f"{_safe(self.from_version)}_to_{_safe(self.to_version)}"

    def to_dict(self) -> dict:
        return {
            "schema": MIGRATION_SCHEMA,
            "modid": self.modid,
            "from": self.from_version,
            "to": self.to_version,
            "ids": dict(self.ids),
            "config_keys": dict(self.config_keys),
            "notes": list(self.notes),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "Migration":
        return cls(
            modid=str(raw.get("modid") or ""),
            from_version=str(raw.get("from") or ""),
            to_version=str(raw.get("to") or ""),
            ids={str(key): str(value) for key, value in (raw.get("ids") or {}).items()},
            config_keys={str(key): str(value) for key, value in (raw.get("config_keys") or {}).items()},
            notes=[str(item) for item in raw.get("notes") or []],
        )


def write(root: Path, migration: Migration) -> Path:
    target = Path(root) / "src/main/resources/data" / migration.modid / "dma" / "migration" / f"{migration.slug}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(migration.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load(path: Path) -> Migration:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("迁移文件必须是 JSON 对象")
    return Migration.from_dict(raw)


def load_all(root: Path, modid: str) -> list[Migration]:
    folder = Path(root) / "src/main/resources/data" / modid / "dma" / "migration"
    rows: list[Migration] = []
    if folder.is_dir():
        for path in sorted(folder.glob("*.json")):
            try:
                rows.append(load(path))
            except (OSError, ValueError):
                continue
    return rows


def rewrite(text: str, migration: Migration) -> tuple[str, list[dict]]:
    hits: list[dict] = []
    result = text
    for old, new in migration.ids.items():
        pattern = re.compile(rf"(?<![\w:]){re.escape(old)}(?![\w:])")
        for match in pattern.finditer(result):
            hits.append({
                "kind": "id", "old": old, "new": new,
                "line": result[:match.start()].count("\n") + 1,
            })
        result = pattern.sub(new, result)
    for old, new in migration.config_keys.items():
        if old in result:
            hits.append({"kind": "config", "old": old, "new": new, "line": 0})
            result = result.replace(old, new)
    return result, hits


def scan(root: Path, migration: Migration, *, suffixes: Iterable[str] = (".java", ".json", ".toml", ".mcfunction")) -> list[dict]:
    root = Path(root)
    allowed = tuple(suffixes)
    rows: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed:
            continue
        if any(part in {"build", ".gradle", "run", ".git"} for part in path.relative_to(root).parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, hits = rewrite(text, migration)
        for item in hits:
            item["path"] = path.relative_to(root).as_posix()
            rows.append(item)
    return rows


def apply(
    root: Path,
    migration: Migration,
    *,
    dry_run: bool = True,
    backup: bool = True,
    save_dirs: Iterable[str] = ("run/saves", "run/config", "run/world"),
) -> list[dict]:
    """Return changed files; only writes when ``dry_run`` is false.

    Before the first write the player/config/save directories are copied to
    ``.dma/backups/<migration>/<timestamp>`` (P1-19).  The backup path is
    attached to every returned row.
    """
    root = Path(root)
    changes: list[tuple[Path, str, int]] = []
    for item in scan(root, migration):
        path = root / item["path"]
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        updated, hits = rewrite(text, migration)
        if not hits:
            continue
        changes.append((path, updated, len(hits)))
    if not changes:
        return []
    backup_path = ""
    if not dry_run and backup:
        backup_path = str(backup_saves(root, migration, save_dirs=save_dirs))
    rows: list[dict] = []
    for path, updated, count in changes:
        row = {"path": path.relative_to(root).as_posix(), "hits": count}
        if backup_path:
            row["backup"] = backup_path
        rows.append(row)
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
    return rows


def backup_saves(
    root: Path,
    migration: Migration,
    *,
    save_dirs: Iterable[str] = ("run/saves", "run/config", "run/world"),
) -> Path:
    """Copy player/config/region data before a migration (P1-19)."""
    import shutil

    root = Path(root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = root / ".dma" / "backups" / migration.slug / stamp
    dest.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for relative in save_dirs:
        source = root / relative
        if not source.exists():
            continue
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
        except OSError:
            continue
        copied.append(relative)
    (dest / "manifest.json").write_text(
        json.dumps(
            {
                "migration": migration.slug,
                "modid": migration.modid,
                "copied": copied,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return dest


def id_map_from_java(text: str) -> dict[str, str]:
    """Extract ``register("a", ...)`` names for a naming comparison."""
    return {name: name for name in re.findall(r"\.register\s*\(\s*\"([\w./-]+)\"", text)}


def plan(migration: Migration) -> str:
    lines = [
        f"# 迁移计划：{migration.from_version} → {migration.to_version}",
        "",
        f"- Mod：`{migration.modid}`",
        f"- ID 重映射：{len(migration.ids)} 项",
        f"- 配置键重映射：{len(migration.config_keys)} 项",
        "",
        "## 步骤",
        "",
        "1. 备份存档与配置文件（`.dma/snapshots` 基线）",
        "2. 发布迁移表，并在 `onLoadWorld`/数据重载时读取旧版本号",
        "3. 按 ID 表重写方块/物品/实体注册名（保留旧 ID 作为别名）",
        "4. 按配置表迁移旧键，未知键保留并记录日志",
        "5. 跑数据包/资产校验与 GameTest，确认旧存档可加载",
        "6. 在 CHANGELOG 中标注破坏性变更与回滚方式",
    ]
    if migration.ids:
        lines.extend(["", "## ID 映射", "", "| 旧 | 新 |", "| --- | --- |"])
        lines.extend(f"| `{old}` | `{new}` |" for old, new in sorted(migration.ids.items()))
    if migration.config_keys:
        lines.extend(["", "## 配置键映射", "", "| 旧 | 新 |", "| --- | --- |"])
        lines.extend(f"| `{old}` | `{new}` |" for old, new in sorted(migration.config_keys.items()))
    if migration.notes:
        lines.extend(["", "## 备注", ""])
        lines.extend(f"- {item}" for item in migration.notes)
    return "\n".join(lines) + "\n"


def _safe(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "_", value or "unknown")
