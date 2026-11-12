"""Acceptance gate: turn ``AcceptanceItem`` definitions into real evidence.

The workflow only stores acceptance *ids*; this module owns the meaning of
each id.  A task may be completed only when every required item produced
evidence, which is what keeps the agent from declaring victory on unbuilt or
unchecked code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .findings import Report
from .spec import AcceptanceItem, Feature, ModSpec
from .workflow import Task


@dataclass
class GateCheck:
    id: str
    kind: str
    required: bool
    passed: bool
    message: str = ""
    evidence: str = ""
    artifacts: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return self.required and not self.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "required": self.required,
            "passed": self.passed,
            "blocking": self.blocking,
            "message": self.message,
            "evidence": self.evidence,
            "artifacts": list(self.artifacts),
        }


@dataclass
class GateReport:
    task_id: str
    checks: list[GateCheck] = field(default_factory=list)
    findings: list[Any] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(item.blocking for item in self.checks)

    @property
    def blocking(self) -> list[GateCheck]:
        return [item for item in self.checks if item.blocking]

    def evidence(self) -> list[str]:
        rows: list[str] = []
        for item in self.checks:
            if item.passed:
                rows.append(f"acceptance:{item.id}={item.evidence or 'pass'}")
        return rows

    def markdown(self) -> str:
        lines = [
            f"## 验收：{self.task_id}",
            "",
            "| 项目 | 类型 | 结果 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for item in self.checks:
            mark = "通过" if item.passed else ("未通过" if item.required else "警告")
            lines.append(f"| {item.id} | {item.kind} | {mark} | {item.message} |")
        return "\n".join(lines) + "\n"


def feature_for(spec: ModSpec, task: Task) -> Feature | None:
    feature_id = ""
    if task.kind == "feature" and task.id.startswith("F-"):
        feature_id = task.id[2:]
    elif task.kind == "scaffold":
        feature_id = "core"
    elif task.kind == "acceptance":
        feature_id = "acceptance"
    if not feature_id:
        return None
    for item in spec.features:
        if item.id == feature_id:
            return item
    return None


def _system_items(task: Task) -> list[AcceptanceItem]:
    """Synthesise acceptance items for build/verify tasks without a feature."""
    rows: list[AcceptanceItem] = []
    for item_id in task.acceptance:
        kind = {
            "build": "build",
            "test": "test",
            "runtime": "runtime",
            "lint": "lint",
            "lint-all": "lint",
            "audit": "lint",
            "acceptance": "manual",
            "package": "file",
        }.get(item_id, "manual")
        params: dict[str, Any] = {}
        if item_id in {"lint-all", "audit"}:
            params = {"levels": ["P0", "P1", "P2", "P3"], "fail_on": "error"}
        elif item_id == "package":
            params = {"patterns": ["build/libs/*.jar"]}
        required = not (task.kind == "verify" and item_id == "runtime")
        rows.append(
            AcceptanceItem(item_id, kind, item_id, required, params)
        )
    return rows


def verify(
    spec: ModSpec,
    task: Task,
    root: Path,
    *,
    build_ok: bool | None = None,
    test_ok: bool | None = None,
    runtime_ok: bool | None = None,
    manual_ok: bool | None = None,
    report: Report | None = None,
    context: Any = None,
) -> GateReport:
    """Evaluate every acceptance item attached to ``task``."""

    feature = feature_for(spec, task)
    result = GateReport(task_id=task.id)
    if feature is None:
        items = _system_items(task)
        if not items:
            result.checks.append(GateCheck(
                "feature", "manual", True, False,
                f"任务 {task.id} 没有对应的 spec 功能，无法验收",
            ))
            return result
        for item in items:
            result.checks.append(_evaluate(
                item, root,
                build_ok=build_ok, test_ok=test_ok, runtime_ok=runtime_ok,
                manual_ok=manual_ok, report=report, context=context,
            ))
        return result

    lint_report = report
    # A task with no declared acceptance items must not be evaluated against
    # *every* item of its feature: that inverts the contract and turns tasks
    # the workflow allows to complete into blocking gate failures.
    for item in feature.acceptance:
        if task.acceptance and item.id not in task.acceptance:
            continue
        check = _evaluate(
            item, root,
            build_ok=build_ok, test_ok=test_ok, runtime_ok=runtime_ok,
            manual_ok=manual_ok, report=lint_report, context=context,
        )
        result.checks.append(check)
        if check.kind == "lint" and lint_report is not None:
            result.findings.extend(lint_report.findings)
    if not result.checks:
        result.checks.append(GateCheck(
            "none", "manual", False, True, "该任务没有声明验收项",
        ))
    return result


def _evaluate(
    item: AcceptanceItem,
    root: Path,
    *,
    build_ok: bool | None,
    test_ok: bool | None,
    runtime_ok: bool | None,
    manual_ok: bool | None,
    report: Report | None,
    context: Any,
) -> GateCheck:
    kind = item.kind
    params = dict(item.params or {})
    root = Path(root)

    if kind == "file":
        patterns = [str(value) for value in params.get("patterns") or []]
        found = _match_files(root, patterns)
        passed = bool(found)
        return GateCheck(
            item.id, kind, item.required, passed,
            f"匹配到 {len(found)} 个文件" if passed else "没有匹配到声明的文件/目录",
            evidence=f"{len(found)} files" if passed else "",
            artifacts=found[:8],
        )

    if kind == "build":
        passed = bool(build_ok)
        return GateCheck(
            item.id, kind, item.required, passed,
            "构建成功" if passed else "尚未执行构建或构建失败",
            evidence="build=pass" if passed else "",
        )

    if kind == "test":
        passed = bool(test_ok)
        return GateCheck(
            item.id, kind, item.required, passed,
            "测试通过" if passed else "没有测试证据",
            evidence="tests=pass" if passed else "",
        )

    if kind == "runtime":
        passed = bool(runtime_ok)
        if not item.required and not passed:
            return GateCheck(
                item.id, kind, False, True,
                "缺少运行时证据（仅警告）",
                evidence="runtime=skipped",
            )
        return GateCheck(
            item.id, kind, item.required, passed,
            "运行时验证通过" if passed else "缺少 GameTest/开发端运行证据",
            evidence="runtime=pass" if passed else "",
        )

    if kind == "lint":
        lint_report = report
        if lint_report is None:
            return GateCheck(
                item.id, kind, item.required, False,
                "尚未执行静态检查",
            )
        rule_ids = set(params.get("rule_ids") or [])
        levels = set(params.get("levels") or [])
        fail_on = str(params.get("fail_on") or "error")
        rows = [
            finding for finding in lint_report.findings
            if (not rule_ids or finding.rule_id in rule_ids)
            and (not levels or finding.level in levels)
        ]
        failures = [
            finding for finding in rows
            if finding.severity == fail_on
            or (fail_on == "error" and finding.severity == "error")
            or (fail_on == "warning" and finding.severity in {"error", "warning"})
        ]
        if failures:
            preview = "; ".join(f"{row.rule_id}@{row.location()}" for row in failures[:3])
            return GateCheck(
                item.id, kind, item.required, False,
                f"{len(failures)} 个 {fail_on} 级问题：{preview}",
                evidence="",
            )
        return GateCheck(
            item.id, kind, item.required, True,
            f"检查通过（{len(rows)} 条相关结果，0 个 {fail_on}）",
            evidence=f"lint={len(rows)}",
        )

    if kind == "manual":
        passed = bool(manual_ok)
        return GateCheck(
            item.id, kind, item.required, passed,
            "人工确认完成" if passed else "需要人工确认（能力矩阵/未自动化项）",
            evidence="manual=confirmed" if passed else "",
        )

    return GateCheck(
        item.id, kind, item.required, False,
        f"未知验收类型：{kind}",
    )


def _match_files(root: Path, patterns: Iterable[str]) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        try:
            matches = list(root.glob(pattern))
        except (ValueError, OSError):
            continue
        for path in matches:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = str(path)
            if rel in seen:
                continue
            seen.add(rel)
            rows.append(rel)
            if len(rows) >= 64:
                return sorted(rows)
    return sorted(rows)
