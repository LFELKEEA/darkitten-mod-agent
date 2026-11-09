"""Rule catalog for the 176-item 2.0 checklist.

Each catalog entry records *how* the item is supported:

``auto``    a real checker/tool emits findings or evidence automatically;
``assist``  a generator/template/report exists, but a human confirms the result;
``manual``  only documented guidance is available.

The mapping is intentionally honest: :func:`coverage` is what the release
notes and the capability matrix report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

CATALOG_PATH = Path(__file__).resolve().parent.parent / "resources" / "checklist_2_0.json"

# checker name -> rule ids
_SUPPORT: dict[str, tuple[str, str]] = {}


def _group(checker: str, automation: str, ids: Iterable[str]) -> None:
    for rule_id in ids:
        _SUPPORT[rule_id] = (automation, checker)


# ----------------------------------------------------------------------
# P0 — all automated
# ----------------------------------------------------------------------
_group("kernel.gate", "auto", ["P0-01"])
_group("kernel.workflow", "auto", ["P0-02"])
_group("kernel.snapshot", "auto", ["P0-03", "TOOL-18"])
_group("kernel.loopguard", "auto", ["P0-04"])
_group("symbols", "auto", ["P0-05", "TOOL-04"])
_group("mappings", "auto", ["P0-06", "TOOL-05"])
_group("mixin", "auto", ["P0-07", "P2-59", "P2-60", "TOOL-06"])
_group("client", "auto", ["P0-08", "TOOL-07"])
_group("client", "auto", ["P0-09", "P2-57", "P2-58", "TOOL-08"])
_group("network", "auto",
       ["P0-10", "P0-11", "P2-31", "P2-32", "P2-33", "P2-27", "P2-28",
        "P2-29", "P2-30", "TOOL-09"])
_group("gradle", "auto", ["P0-12", "P1-40"])

# ----------------------------------------------------------------------
# P1
# ----------------------------------------------------------------------
_group("tools.testgen", "assist", ["P1-01", "P1-02"])
_group("tools.matrix", "assist", ["P1-05", "P3-34"])
_group("tools.crash", "auto", ["P1-03", "TOOL-17"])
_group("stress", "auto", ["P1-04"])
_group("tools.blockbench", "assist", ["P1-06", "TOOL-13"])
_group("tools.library", "assist", ["P1-07"])
_group("tools.texture", "auto", ["P1-08", "TOOL-14"])
_group("tools.vision", "assist", ["P1-09", "TOOL-15"])
_group("assets", "auto",
       ["P1-10", "P1-12", "P1-30", "P1-31", "P1-35", "P1-36",
        "P2-15", "P2-18", "P3-40", "TOOL-12"])
_group("datapack", "auto",
       ["P1-11", "P2-44", "P2-45", "P2-46", "TOOL-11"])
_group("registry", "auto", ["P1-13", "P1-14", "P1-15", "TOOL-10"])
_group("serialization", "auto", ["P1-16", "P1-17", "P1-18", "P2-53"])
_group("tools.migration", "assist", ["P1-19"])
_group("thirdparty", "auto", ["P1-20", "P1-21", "P1-22", "P1-23"])
_group("thirdparty", "assist", ["P1-24"])
_group("domain", "auto", ["P1-25"])
_group("tools.configgen", "assist", ["P1-26", "P3-16"])
_group("logging", "auto", ["P3-19"])
_group("domain", "auto", ["P1-27", "P1-28", "P1-29"])
_group("domain", "auto", ["P1-32", "P2-39", "P2-40"])
_group("domain", "assist", ["P2-41", "P2-42", "P2-43"])
_group("domain", "auto", ["P1-33", "P1-34", "P2-34", "P2-35", "P2-36", "P2-37", "P2-38"])
_group("domain", "auto", ["P1-37", "P1-38", "P2-47", "P2-48"])
_group("domain", "auto", ["P1-39", "P2-56"])

# ----------------------------------------------------------------------
# P2
# ----------------------------------------------------------------------
_group("world", "auto",
       ["P2-01", "P2-03", "P2-04", "P2-05", "P2-07", "P2-08"])
_group("world", "assist", ["P2-02", "P2-06", "P2-09", "P2-10", "P2-11"])
_group("render", "auto",
       ["P2-12", "P2-13", "P2-14", "P2-16", "P2-17", "P2-19"])
_group("render", "auto", ["P2-20", "P2-21", "P2-22", "P2-23", "P2-24"])
_group("domain", "auto", ["P2-25"])
_group("domain", "assist", ["P2-26"])
_group("antipattern", "auto",
       ["P2-49", "P2-50", "P2-51", "P2-52", "P2-54", "P2-55", "P3-32"])

# ----------------------------------------------------------------------
# P3
# ----------------------------------------------------------------------
_group("packaging", "auto",
       ["P3-01", "P3-02", "P3-03", "P3-33"])
_group("packaging", "assist", ["P3-04", "P3-05", "P3-06", "P3-34"])
_group("docs", "auto", ["P3-07", "P3-08", "P3-09", "P3-12"])
_group("docs", "assist", ["P3-10", "P3-11"])
_group("ecosystem", "auto", ["P3-13", "P3-14"])
_group("ecosystem", "assist", ["P3-15", "P3-17", "P3-18"])
_group("logging", "auto", ["P3-20", "P3-21", "P3-23", "P3-27", "P3-29", "P3-31"])
_group("tools.diagnostics", "assist", ["P3-22", "P3-24", "P3-25", "P3-26"])
_group("version", "assist", ["P3-28", "P3-30", "P3-35", "P3-36"])
_group("i18n", "auto", ["P3-37", "P3-38"])
_group("i18n", "assist", ["P3-39"])
_group("security", "auto", ["P3-41", "P3-42", "P3-43"])
_group("security", "assist", ["P3-44"])

# ----------------------------------------------------------------------
# TOOL layer
# ----------------------------------------------------------------------
_group("integration.ai", "auto", ["TOOL-01"])
_group("integration.tools", "auto", ["TOOL-02", "TOOL-03"])
_group("tools.impact", "auto", ["TOOL-19"])
_group("tools.cache", "auto", ["TOOL-20"])
_group("tools.screenshot", "auto", ["TOOL-16"])

AUTOMATION_VALUES = ("auto", "assist", "manual")


@dataclass
class Rule:
    id: str
    level: str
    category: str
    title: str
    section: str
    problem: str
    goal: str
    implementation: str
    automation: str = "manual"
    checker: str = ""

    @property
    def is_auto(self) -> bool:
        return self.automation == "auto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "level": self.level,
            "category": self.category,
            "title": self.title,
            "section": self.section,
            "problem": self.problem,
            "goal": self.goal,
            "implementation": self.implementation,
            "automation": self.automation,
            "checker": self.checker,
        }


def _load(path: Path | None = None) -> list[Rule]:
    source = Path(path) if path else CATALOG_PATH
    raw = json.loads(source.read_text(encoding="utf-8"))
    rows: list[Rule] = []
    for item in raw:
        rule_id = str(item.get("id") or "")
        automation, checker = _SUPPORT.get(rule_id, ("manual", ""))
        rows.append(
            Rule(
                id=rule_id,
                level=str(item.get("level") or ""),
                category=str(item.get("category") or ""),
                title=str(item.get("title") or ""),
                section=str(item.get("section") or ""),
                problem=str(item.get("problem") or ""),
                goal=str(item.get("goal") or ""),
                implementation=str(item.get("implementation") or ""),
                automation=automation,
                checker=checker,
            )
        )
    rows.sort(key=_sort_key)
    return rows


def _sort_key(rule: Rule) -> tuple[int, int]:
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "TOOL": 4}
    prefix, _, number = rule.id.partition("-")
    try:
        index = int(number)
    except ValueError:
        index = 0
    return (order.get(prefix, 9), index)


@lru_cache(maxsize=1)
def catalog() -> list[Rule]:
    return _load()


def rules(
    *,
    level: str | None = None,
    automation: str | None = None,
    checker: str | None = None,
    category: str | None = None,
) -> list[Rule]:
    rows = catalog()
    if level:
        rows = [item for item in rows if item.level == level]
    if automation:
        rows = [item for item in rows if item.automation == automation]
    if checker:
        rows = [item for item in rows if item.checker == checker]
    if category:
        rows = [item for item in rows if item.category == category]
    return list(rows)


def rule(rule_id: str) -> Rule | None:
    for item in catalog():
        if item.id == rule_id:
            return item
    return None


def rule_ids(*, level: str | None = None, automation: str | None = None) -> list[str]:
    return [item.id for item in rules(level=level, automation=automation)]


def coverage() -> dict[str, Any]:
    rows = catalog()
    by_level: dict[str, dict[str, int]] = {}
    total = {"auto": 0, "assist": 0, "manual": 0, "total": len(rows)}
    for item in rows:
        bucket = by_level.setdefault(
            item.level, {"auto": 0, "assist": 0, "manual": 0, "total": 0}
        )
        bucket[item.automation] += 1
        bucket["total"] += 1
        total[item.automation] += 1
    return {
        "total": total,
        "by_level": by_level,
        "checkers": sorted({item.checker for item in rows if item.checker}),
    }


def matrix_markdown() -> str:
    """Capability matrix used by docs and release notes."""
    cov = coverage()
    total = cov["total"]
    lines = [
        "# DMA 2.0 能力矩阵（176 项）",
        "",
        "| 级别 | 总数 | 自动 | 辅助 | 人工 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for level in ("P0", "P1", "P2", "P3", "TOOL"):
        bucket = cov["by_level"].get(level)
        if not bucket:
            continue
        lines.append(
            f"| {level} | {bucket['total']} | {bucket['auto']} | "
            f"{bucket['assist']} | {bucket['manual']} |"
        )
    lines.append(
        f"| **合计** | **{total['total']}** | **{total['auto']}** | "
        f"**{total['assist']}** | **{total['manual']}** |"
    )
    lines.extend([
        "",
        "## 明细",
        "",
        "| 编号 | 级别 | 能力 | 支持方式 | 检查器 |",
        "| --- | --- | --- | --- | --- |",
    ])
    for item in catalog():
        lines.append(
            f"| {item.id} | {item.level} | {item.title} | "
            f"{item.automation} | `{item.checker or '—'}` |"
        )
    return "\n".join(lines) + "\n"
