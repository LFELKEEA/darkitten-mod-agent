"""Findings and reports shared by every linter (2.0).

All checks emit :class:`Finding` objects with a checklist rule id so the
acceptance gate can decide whether a feature may be marked done.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
LEVEL_SEVERITY = {"P0": "error", "P1": "error", "P2": "warning", "P3": "info", "TOOL": "info"}


@dataclass
class Finding:
    rule_id: str
    message: str
    level: str = "P2"
    severity: str = ""
    path: str = ""
    line: int = 0
    column: int = 0
    suggestion: str = ""
    evidence: str = ""
    checker: str = ""
    fixable: bool = False

    def __post_init__(self) -> None:
        if not self.severity:
            self.severity = LEVEL_SEVERITY.get(self.level, "warning")
        if self.severity not in SEVERITY_ORDER:
            self.severity = "warning"

    @property
    def key(self) -> tuple[str, str, int, str]:
        return (self.rule_id, self.path, self.line, self.message[:120])

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "level": self.level,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "checker": self.checker,
            "fixable": self.fixable,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Finding":
        return cls(
            rule_id=str(raw.get("rule_id") or ""),
            message=str(raw.get("message") or ""),
            level=str(raw.get("level") or "P2"),
            severity=str(raw.get("severity") or ""),
            path=str(raw.get("path") or ""),
            line=int(raw.get("line") or 0),
            column=int(raw.get("column") or 0),
            suggestion=str(raw.get("suggestion") or ""),
            evidence=str(raw.get("evidence") or ""),
            checker=str(raw.get("checker") or ""),
            fixable=bool(raw.get("fixable")),
        )

    def location(self) -> str:
        if self.path and self.line:
            return f"{self.path}:{self.line}"
        return self.path or "（项目级）"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    scanned_files: int = 0
    rules_run: list[str] = field(default_factory=list)
    duration_ms: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    def add(self, finding: Finding) -> None:
        self.findings.append(finding)

    def add_many(self, findings: Iterable[Finding]) -> None:
        for finding in findings:
            self.add(finding)

    def extend(self, other: "Report") -> "Report":
        self.findings.extend(other.findings)
        self.scanned_files = max(self.scanned_files, other.scanned_files)
        for rule_id in other.rules_run:
            if rule_id not in self.rules_run:
                self.rules_run.append(rule_id)
        self.duration_ms += other.duration_ms
        self.meta.update(other.meta)
        return self

    def dedupe(self) -> "Report":
        seen: set[tuple[str, str, int, str]] = set()
        unique: list[Finding] = []
        for item in self.findings:
            if item.key in seen:
                continue
            seen.add(item.key)
            unique.append(item)
        self.findings = unique
        return self

    def sort(self) -> "Report":
        self.findings.sort(
            key=lambda item: (
                SEVERITY_ORDER.get(item.severity, 9),
                item.path,
                item.line,
                item.rule_id,
            )
        )
        return self

    # ------------------------------------------------------------------
    def by_severity(self, severity: str) -> list[Finding]:
        return [item for item in self.findings if item.severity == severity]

    @property
    def errors(self) -> list[Finding]:
        return self.by_severity("error")

    @property
    def warnings(self) -> list[Finding]:
        return self.by_severity("warning")

    @property
    def infos(self) -> list[Finding]:
        return self.by_severity("info")

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for item in self.findings:
            counts[item.severity] = counts.get(item.severity, 0) + 1
        counts["total"] = len(self.findings)
        return counts

    def by_level(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.findings:
            counts[item.level] = counts.get(item.level, 0) + 1
        return counts

    def score(self) -> int:
        """0-100 delivery score; errors dominate."""
        counts = self.counts()
        penalty = counts["error"] * 12 + counts["warning"] * 3 + counts["info"]
        return max(0, 100 - min(100, penalty))

    def failed_rules(self, levels: Iterable[str] | None = None) -> list[str]:
        allowed = set(levels or [])
        rows: list[str] = []
        for item in self.errors:
            if allowed and item.level not in allowed:
                continue
            if item.rule_id not in rows:
                rows.append(item.rule_id)
        return rows

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "score": self.score(),
            "counts": self.counts(),
            "by_level": self.by_level(),
            "scanned_files": self.scanned_files,
            "rules_run": list(self.rules_run),
            "duration_ms": self.duration_ms,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "findings": [item.to_dict() for item in self.findings],
            "meta": self.meta,
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def markdown(self, limit: int = 60) -> str:
        counts = self.counts()
        lines = [
            "# 静态检查报告",
            "",
            f"- 结果：{'通过' if self.ok else '未通过'}（评分 {self.score()}）",
            f"- 问题：error {counts['error']} / warning {counts['warning']} / info {counts['info']}",
            f"- 扫描文件：{self.scanned_files}",
            "",
        ]
        if not self.findings:
            lines.append("没有发现问题。")
            return "\n".join(lines) + "\n"
        lines.extend([
            "| 规则 | 级别 | 位置 | 问题 | 建议 |",
            "| --- | --- | --- | --- | --- |",
        ])
        for item in self.findings[:limit]:
            message = item.message.replace("|", "\\|")
            suggestion = item.suggestion.replace("|", "\\|")[:120]
            lines.append(
                f"| {item.rule_id} | {item.severity} | `{item.location()}` | {message} | {suggestion} |"
            )
        if len(self.findings) > limit:
            lines.append(f"| … | | | 其余 {len(self.findings) - limit} 条已省略 | |")
        return "\n".join(lines) + "\n"

    def write(self, path: Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix.lower() == ".json":
            target.write_text(self.to_json(), encoding="utf-8")
        else:
            target.write_text(self.markdown(), encoding="utf-8")
        return target
