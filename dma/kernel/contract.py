"""Public surface contract freeze / drift audit.

Large mods fail in a very specific way: a refactor quietly removes or renames
a public class, method or field that another package already uses, and the
breakage only shows up two features later.  The contract module records the
*declared* public surface (classes, methods, fields, resource paths) in
``.dma/contract.json`` before a refactor, then compares the current source tree
against it.

This is deliberately deterministic: no model, no heuristics about intent.  The
agent is expected to freeze the contract before touching shared code and to
run the audit afterwards.  Additions are allowed; removals and signature
changes are reported as violations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Iterator

from .java import iter_java_files, parse_java

SCHEMA = "dma-contract/1"
CONTRACT_RELATIVE = Path(".dma") / "contract.json"
RESOURCE_ROOTS = (Path("src") / "main" / "resources", Path("src") / "client" / "resources")


def contract_path(root: Path) -> Path:
    return Path(root) / CONTRACT_RELATIVE


def _member_key(name: str, params: Iterable[str] = ()) -> str:
    values = ",".join(str(item) for item in params)
    return f"{name}({values})"


def _is_public(modifiers: Iterable[str]) -> bool:
    values = {str(item) for item in modifiers}
    return "public" in values or "protected" in values


def _class_entry(java_class: Any, *, top_level: bool) -> dict[str, Any] | None:
    modifiers = [str(item) for item in java_class.modifiers]
    # Private nested helpers are implementation details; keep package-private
    # top-level types because mods often declare them as the main entry point.
    if "private" in modifiers:
        return None
    if not top_level and not _is_public(modifiers):
        return None
    methods: dict[str, dict[str, Any]] = {}
    for method in java_class.methods:
        if "private" in method.modifiers:
            continue
        name = "<init>" if method.is_constructor else method.name
        params = [str(param_type) for param_type, _ in method.params]
        methods[_member_key(name, params)] = {
            "return": str(method.return_type or ""),
            "modifiers": sorted(str(item) for item in method.modifiers),
            "static": "static" in method.modifiers,
        }
    fields: dict[str, dict[str, Any]] = {}
    for thing in java_class.fields:
        if "private" in thing.modifiers:
            continue
        fields[str(thing.name)] = {
            "type": str(thing.type or ""),
            "modifiers": sorted(str(item) for item in thing.modifiers),
            "static": "static" in thing.modifiers,
        }
    return {
        "kind": str(java_class.kind or "class"),
        "modifiers": sorted(modifiers),
        "extends": str(java_class.extends or ""),
        "implements": sorted(str(item) for item in java_class.implements),
        "methods": methods,
        "fields": fields,
    }


def surface(root: Path) -> dict[str, Any]:
    """Collect the declared public surface of the project's Java sources."""
    root = Path(root)
    classes: dict[str, dict[str, Any]] = {}
    files: list[str] = []
    for path in iter_java_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            relative = path.resolve().relative_to(root.resolve()).as_posix()
        except (ValueError, OSError):
            relative = path.as_posix()
        files.append(relative)
        java = parse_java(text, str(path))
        top_level_ids = {id(item) for item in java.classes}
        for java_class in java.all_classes():
            qualified = java_class.qualified or java_class.name
            top_level = id(java_class) in top_level_ids
            entry = _class_entry(java_class, top_level=top_level)
            if entry is None:
                continue
            entry["path"] = relative
            classes[qualified] = entry

    resources: list[str] = []
    for base in RESOURCE_ROOTS:
        directory = root / base
        if not directory.is_dir():
            continue
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            try:
                resources.append(path.relative_to(root).as_posix())
            except ValueError:
                continue

    return {
        "classes": dict(sorted(classes.items())),
        "resources": sorted(set(resources)),
        "files": sorted(files),
    }


def _counts(snap: dict[str, Any]) -> dict[str, int]:
    methods = sum(len(item.get("methods") or {}) for item in (snap.get("classes") or {}).values())
    fields = sum(len(item.get("fields") or {}) for item in (snap.get("classes") or {}).values())
    return {
        "classes": len(snap.get("classes") or {}),
        "methods": methods,
        "fields": fields,
        "resources": len(snap.get("resources") or []),
    }


def freeze(root: Path, *, note: str = "") -> dict[str, Any]:
    """Write the current public surface to ``.dma/contract.json``."""
    root = Path(root)
    snap = surface(root)
    from datetime import datetime, timezone

    payload = {
        "schema": SCHEMA,
        "note": str(note or ""),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "surface": snap,
        "counts": _counts(snap),
    }
    path = contract_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(path), "counts": payload["counts"]}


def load(root: Path) -> dict[str, Any] | None:
    path = contract_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return None
    if not isinstance(raw.get("surface"), dict):
        return None
    return raw


def _diff_members(
    owner: str,
    old: dict[str, dict[str, Any]],
    new: dict[str, dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    removed = [f"{owner}#{key}" for key in sorted(old) if key not in new]
    changed: list[dict[str, Any]] = []
    for key in sorted(old):
        if key not in new:
            continue
        before = old[key] or {}
        after = new[key] or {}
        if before == after:
            continue
        changed.append(
            {
                "symbol": f"{owner}#{key}",
                "before": before,
                "after": after,
            }
        )
    return removed, changed


def check(root: Path, *, strict_resources: bool = False) -> dict[str, Any]:
    """Compare current sources against the frozen contract."""
    root = Path(root)
    frozen = load(root)
    if frozen is None:
        return {
            "ok": False,
            "frozen": False,
            "reason": "还没有冻结契约；先调用 contract_freeze。",
            "removed": [],
            "changed": [],
            "added": [],
            "warnings": [],
        }
    before = frozen.get("surface") or {}
    after = surface(root)
    old_classes = before.get("classes") or {}
    new_classes = after.get("classes") or {}

    removed: list[str] = [f"class:{name}" for name in sorted(old_classes) if name not in new_classes]
    added: list[str] = [f"class:{name}" for name in sorted(new_classes) if name not in old_classes]
    changed: list[dict[str, Any]] = []
    for name in sorted(old_classes):
        if name not in new_classes:
            continue
        old = old_classes[name] or {}
        new = new_classes[name] or {}
        for key in ("kind", "extends", "implements"):
            if (old.get(key) or None) != (new.get(key) or None):
                changed.append(
                    {
                        "symbol": f"class:{name}.{key}",
                        "before": old.get(key),
                        "after": new.get(key),
                    }
                )
        member_removed, member_changed = _diff_members(
            name, old.get("methods") or {}, new.get("methods") or {}
        )
        removed.extend(f"method:{item}" for item in member_removed)
        changed.extend(
            {
                "symbol": f"method:{item['symbol']}",
                "before": item["before"],
                "after": item["after"],
            }
            for item in member_changed
        )
        field_removed, field_changed = _diff_members(
            name, old.get("fields") or {}, new.get("fields") or {}
        )
        removed.extend(f"field:{item}" for item in field_removed)
        changed.extend(
            {
                "symbol": f"field:{item['symbol']}",
                "before": item["before"],
                "after": item["after"],
            }
            for item in field_changed
        )
        for key in sorted(new.get("methods") or {}):
            if key not in (old.get("methods") or {}):
                added.append(f"method:{name}#{key}")
        for key in sorted(new.get("fields") or {}):
            if key not in (old.get("fields") or {}):
                added.append(f"field:{name}#{key}")

    old_resources = set(before.get("resources") or [])
    new_resources = set(after.get("resources") or [])
    resource_removed = sorted(old_resources - new_resources)
    resource_added = sorted(new_resources - old_resources)

    warnings: list[str] = []
    if resource_removed:
        warnings.append(
            f"{len(resource_removed)} 个资源文件被删除："
            + "、".join(resource_removed[:8])
        )

    ok = not removed and not changed and (not strict_resources or not resource_removed)
    return {
        "ok": ok,
        "frozen": True,
        "frozen_at": frozen.get("created_at") or "",
        "note": frozen.get("note") or "",
        "counts_before": frozen.get("counts") or {},
        "counts_after": _counts(after),
        "removed": removed,
        "changed": changed,
        "added": added,
        "resources_removed": resource_removed,
        "resources_added": resource_added,
        "warnings": warnings,
    }


def report_markdown(result: dict[str, Any]) -> str:
    """Render a contract check result for the agent/user."""
    if not result.get("frozen"):
        return f"## 契约审计\n\n{result.get('reason') or '没有冻结契约。'}\n"
    lines = [
        "## 契约审计",
        "",
        f"- 结果：{'通过' if result.get('ok') else '存在破坏性变更'}",
        f"- 冻结时间：{result.get('frozen_at') or '未知'}",
        f"- 当前规模：类 {result['counts_after']['classes']} / 方法 {result['counts_after']['methods']} "
        f"/ 字段 {result['counts_after']['fields']} / 资源 {result['counts_after']['resources']}",
        "",
    ]
    removed = result.get("removed") or []
    changed = result.get("changed") or []
    added = result.get("added") or []
    if removed:
        lines.append("### 已删除（必须恢复或显式迁移调用方）")
        lines += [f"- `{item}`" for item in removed[:40]]
        if len(removed) > 40:
            lines.append(f"- …另有 {len(removed) - 40} 项")
        lines.append("")
    if changed:
        lines.append("### 签名变更")
        for item in changed[:30]:
            lines.append(
                f"- `{item['symbol']}`：{item.get('before')} → {item.get('after')}"
            )
        if len(changed) > 30:
            lines.append(f"- …另有 {len(changed) - 30} 项")
        lines.append("")
    if added:
        lines.append(f"### 新增 {len(added)} 项（允许）")
        lines.append("")
    for warning in result.get("warnings") or []:
        lines.append(f"- 注意：{warning}")
    if not removed and not changed:
        lines.append("公共 API 没有被删除或改签名。")
    lines.append("")
    return "\n".join(lines)
