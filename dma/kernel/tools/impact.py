"""Change impact analysis (TOOL-19).

Given changed files, answer: which classes call them, which resources and
tests are coupled to them, and which checklist rules must be re-run.  This is
what lets the agent verify a local edit without rebuilding the whole world.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..java import enclosing_method, parse_java
from ..symbols import SymbolIndex, build_index


@dataclass
class ImpactReport:
    changed: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    callers: list[dict] = field(default_factory=list)
    subclasses: list[dict] = field(default_factory=list)
    overrides: list[dict] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "changed": list(self.changed),
            "classes": list(self.classes),
            "callers": list(self.callers),
            "subclasses": list(self.subclasses),
            "overrides": list(self.overrides),
            "resources": list(self.resources),
            "tests": list(self.tests),
            "rules": list(self.rules),
            "notes": list(self.notes),
        }

    def markdown(self) -> str:
        lines = [
            "# 改动影响分析",
            "",
            "## 改动文件",
            "",
        ]
        lines.extend(f"- `{item}`" for item in self.changed) if self.changed else lines.append("- （无）")
        lines.extend(["", "## 受影响类", ""])
        lines.extend(f"- `{item}`" for item in self.classes) if self.classes else lines.append("- （无）")
        if self.callers:
            lines.extend(["", "## 调用方", "", "| 类 | 方法 | 位置 |", "| --- | --- | --- |"])
            for item in self.callers[:40]:
                lines.append(f"| {item.get('owner', '')} | {item.get('name', '')} | `{item.get('file', '')}:{item.get('line', 0)}` |")
        if self.subclasses:
            lines.extend(["", "## 子类/实现", ""])
            lines.extend(f"- `{item.get('name', '')}`" for item in self.subclasses)
        if self.resources:
            lines.extend(["", "## 关联资源", ""])
            lines.extend(f"- `{item}`" for item in self.resources)
        if self.tests:
            lines.extend(["", "## 相关测试", ""])
            lines.extend(f"- `{item}`" for item in self.tests)
        if self.rules:
            lines.extend(["", "## 需要复跑的检查", ""])
            lines.extend(f"- `{item}`" for item in self.rules)
        if self.notes:
            lines.extend(["", "## 备注", ""])
            lines.extend(f"- {item}" for item in self.notes)
        return "\n".join(lines) + "\n"


def analyze(
    root: Path,
    changed: Iterable[str | Path],
    *,
    index: SymbolIndex | None = None,
    max_callers: int = 60,
) -> ImpactReport:
    root = Path(root).resolve()
    report = ImpactReport()
    changed_paths = [_normalize(root, item) for item in changed]
    report.changed = changed_paths
    index = index or build_index(root, persist=False)

    java_changed = [item for item in changed_paths if item.endswith(".java")]
    class_names: list[str] = []
    for relative in java_changed:
        path = root / relative
        if not path.is_file():
            continue
        try:
            java = parse_java(path.read_text(encoding="utf-8", errors="replace"), str(path))
        except OSError:
            continue
        for java_class in java.all_classes():
            if java_class.qualified not in class_names:
                class_names.append(java_class.qualified)
    report.classes = class_names
    parsed_cache: dict[str, Any] = {}
    for name in class_names:
        simple = name.rsplit(".", 1)[-1]
        for item in index.find_class(name):
            if item.source != "project":
                continue
            for child in index.subclasses_of(item.name):
                row = {"name": child.name, "file": child.path}
                if row not in report.subclasses:
                    report.subclasses.append(row)
            methods = [
                method for method in index.methods
                if method.owner in {item.name, simple} and method.source == "project"
            ]
            for method in methods:
                for raw_caller in index.callers_of(method.name, limit=max_callers):
                    caller = _caller_context(root, raw_caller, parsed_cache)
                    if caller is None:
                        continue
                    if caller["owner"] in {name, simple} and caller["name"] == method.name:
                        continue
                    if caller not in report.callers:
                        report.callers.append(caller)
                    if len(report.callers) >= max_callers:
                        break
                for override in index.overrides_of(item.name, method.name):
                    row = override.to_dict()
                    if row not in report.overrides:
                        report.overrides.append(row)
        report.resources.extend(_resources_for(root, simple))
        report.tests.extend(_tests_for(root, simple))

    report.resources = sorted(dict.fromkeys(report.resources))
    report.tests = sorted(dict.fromkeys(report.tests))
    report.rules = sorted(set(_rules_for(changed_paths)))
    if not java_changed:
        report.notes.append("没有 Java 改动：主要复跑数据包/资产检查。")
    if len(report.callers) >= max_callers:
        report.notes.append(f"调用方超过 {max_callers} 个，已截断；建议扩大符号索引范围。")
    return report


def changed_from_git(root: Path, *, base: str = "HEAD", staged: bool = True) -> list[str]:
    command = ["git", "diff", "--name-only"]
    if staged:
        command.append("--cached")
    command.append(base)
    try:
        output = subprocess.run(
            command, cwd=str(root), capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if output.returncode != 0:
        return []
    return [line.strip().replace("\\", "/") for line in output.stdout.splitlines() if line.strip()]


def _caller_context(root: Path, raw: dict, cache: dict[str, Any]) -> dict | None:
    path_value = str(raw.get("path") or "")
    line = int(raw.get("line") or 0)
    if not path_value:
        return None
    path = Path(path_value)
    key = str(path)
    if key not in cache:
        try:
            cache[key] = parse_java(path.read_text(encoding="utf-8", errors="replace"), key)
        except OSError:
            cache[key] = None
    java = cache[key]
    if java is None:
        return None
    method = enclosing_method(java, line)
    if method is None:
        return None
    snippet = str(raw.get("snippet") or "").strip()
    if method.line == line or line <= method.line + 1:
        return None  # annotation/declaration, not a call
    if snippet.endswith("{") and re.search(r"\b(?:void|public|private|protected|static|final)\b", snippet):
        return None
    try:
        relative = path.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        relative = str(path).replace("\\", "/")
    return {
        "owner": method.owner,
        "name": method.name,
        "file": relative,
        "line": line,
        "snippet": snippet[:160],
    }


def _normalize(root: Path, value: str | Path) -> str:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(root).as_posix()
        except ValueError:
            return str(path).replace("\\", "/")
    return str(value).replace("\\", "/")


def _resources_for(root: Path, class_name: str) -> list[str]:
    name = _snake(class_name)
    candidates: list[str] = []
    for pattern in (
        f"src/main/resources/assets/**/blockstates/{name}.json",
        f"src/main/resources/assets/**/models/block/{name}.json",
        f"src/main/resources/assets/**/models/item/{name}.json",
        f"src/main/resources/assets/**/textures/block/{name}.png",
        f"src/main/resources/assets/**/textures/item/{name}.png",
        f"src/main/resources/assets/**/lang/*.json",
        f"src/main/resources/data/**/loot_table/blocks/{name}.json",
        f"src/main/resources/data/**/recipe/*{name}*.json",
        f"src/main/resources/data/**/tags/**/*{name}*.json",
    ):
        for path in root.glob(pattern):
            if path.is_file():
                try:
                    relative = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if relative not in candidates:
                    candidates.append(relative)
    return candidates


def _tests_for(root: Path, class_name: str) -> list[str]:
    rows: list[str] = []
    for base in ("src/test", "src/gametest", "scenarios", "tests"):
        folder = root / base
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".java", ".py", ".json", ".md"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if class_name in text:
                rows.append(path.relative_to(root).as_posix())
    return rows


def _rules_for(changed: Iterable[str]) -> list[str]:
    rules: set[str] = set()
    for path in changed:
        value = path.replace("\\", "/")
        if value.endswith(".java"):
            rules.update({"P0-07", "P0-08", "P0-09", "P0-10", "P1-13"})
            if "/item/" in value or "Item" in value:
                rules.update({"P1-27", "P1-28", "P1-29"})
            if "Block" in value:
                rules.update({"P1-30", "P1-31", "P1-32"})
            if "Entity" in value:
                rules.update({"P1-34", "P2-34", "P2-35"})
        elif "/data/" in value:
            rules.update({"P1-11", "P2-44", "P2-45", "P2-46"})
        elif "/assets/" in value:
            rules.update({"P1-12", "P2-15", "P2-18", "P3-40"})
        elif value.endswith(("build.gradle", "build.gradle.kts", "gradle.properties")):
            rules.update({"P0-12", "P1-40", "P3-01"})
        elif "lang/" in value:
            rules.update({"P3-37", "P3-38"})
    return sorted(rules)


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
