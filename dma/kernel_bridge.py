"""Bridge between the desktop agent loop and the 2.0 kernel.

The agent loop is intentionally thin: this module owns the spec, the DAG, the
snapshot baseline, the loop guard and the acceptance gate.  Every write goes
through :meth:`KernelBridge.record_edit`, every build through
:meth:`before_build` / :meth:`record_build`, and a run is not allowed to claim
success while the gate has blocking checks.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .kernel.checks import run_checks
from .kernel.findings import Report
from .kernel.gate import verify
from .kernel.loopguard import LoopGuard
from .kernel.snapshot import SnapshotStore
from .kernel.spec import (
    ModSpec,
    derive_spec,
    load_spec,
    save_spec,
    spec_markdown,
)
from .kernel.workflow import (
    Workflow,
    load_workflow,
    plan_tasks,
    save_workflow,
)


class KernelBridge:
    def __init__(
        self,
        root: Path | None,
        *,
        idea: str,
        loader: str = "NeoForge",
        mc: str = "1.21.1",
        modid: str = "",
        package: str = "",
        side: str = "both",
    ) -> None:
        self.root = Path(root).resolve() if root is not None else None
        self.idea = (idea or "").strip()
        self.loader = loader or "NeoForge"
        self.mc = mc or "1.21.1"
        self.modid = modid or ""
        self.package = package or ""
        self.side = side or "both"
        self.spec: ModSpec | None = None
        self.workflow: Workflow | None = None
        self.snapshots: SnapshotStore | None = None
        # 大模组一次编译经常带出多个错误，熔断阈值放宽：
        # 同一处失败 5 次、累计 60 次失败才停止自动修复。
        self.guard = LoopGuard(max_repeats=5, max_total=60)
        self.last_build_ok: bool | None = None
        self.last_build_output = ""
        self.last_test_ok: bool | None = None
        self.last_runtime_ok: bool | None = None
        self.last_lint: Report | None = None
        self.blocked: list[str] = []
        self.notes: list[str] = []
        self._last_snapshot = ""
        if self.root is not None and self.root.is_dir():
            self.snapshots = SnapshotStore(self.root)
            self._load()
        else:
            self.spec = self._derive()
            self.workflow = plan_tasks(self.spec)

    # ------------------------------------------------------------------
    def _derive(self) -> ModSpec:
        return derive_spec(
            self.idea,
            self.loader,
            self.mc,
            modid=self.modid or None,
            package=self.package or None,
        )

    def _load(self) -> None:
        if self.root is None:
            raise RuntimeError("工程目录未设置，无法加载规格。")
        spec = load_spec(self.root)
        if spec is None:
            spec = self._derive()
            save_spec(spec, self.root)
        else:
            self._merge(spec)
        self.spec = spec
        self.workflow = load_workflow(self.root)
        if self.workflow is None or [item.id for item in self.workflow.tasks] != [
            item.id for item in plan_tasks(spec).tasks
        ]:
            old = self.workflow
            self.workflow = plan_tasks(spec)
            if old is not None:
                self._carry_status(old, self.workflow)
            save_workflow(self.workflow, self.root)

    def _merge(self, spec: ModSpec) -> None:
        """Keep existing state, add features discovered in the new request."""
        if not self.idea:
            return
        fresh = self._derive()
        existing = set(spec.feature_ids())
        added = [item for item in fresh.features if item.id not in existing]
        if added:
            spec.features.extend(added)
            spec.updated_at = fresh.updated_at
            self.notes.append(f"新增需求特征：{'、'.join(item.id for item in added)}")

    @staticmethod
    def _carry_status(old: Workflow, new: Workflow) -> None:
        for task in new.tasks:
            previous = old.task(task.id)
            if previous is not None and previous.status in {"done", "failed", "blocked", "running"}:
                task.status = previous.status
                task.detail = previous.detail
                task.attempts = previous.attempts

    # ------------------------------------------------------------------
    def prompt_block(self) -> str:
        if self.spec is None or self.workflow is None:
            return ""
        counts = self.spec.summary()
        lines = [
            "【规格与任务图（2.0）】",
            f"- 目标：{self.spec.idea[:220] or '（未填写）'}",
            f"- 目标平台：{self.spec.loader} {self.spec.mc} / {self.spec.side}",
            f"- 功能：{counts['features']} 个；状态 {counts['status']}",
            f"- 验收项：{counts['acceptance']} 个（必需项未通过时不得声称完成）",
            "",
            "任务顺序（依赖未完成的节点不得跳过）：",
        ]
        for task in self.workflow.order()[:18]:
            mark = {"done": "✓", "running": "→", "failed": "✗", "blocked": "!"}.get(task.status, "·")
            lines.append(f"  {mark} {task.id} {task.title} [{task.status}]")
        current = self.workflow.current() or next(
            (task for task in self.workflow.order() if task.is_open), None
        )
        if current is not None:
            lines.append("")
            lines.append(f"当前应执行：{current.id} {current.title}")
        lines.append("")
        lines.append(
            "规则：每次写文件都归属于某个功能；先完成被依赖的注册/骨架，再写业务；"
            "禁止跳过后面的编译与验收。"
        )
        return "\n".join(lines)

    def summary_markdown(self) -> str:
        if self.spec is None or self.workflow is None:
            return ""
        lines = ["## 2.0 规格与任务", "", spec_markdown(self.spec)]
        lines.append(self.workflow.markdown())
        return "\n\n".join(lines)

    # ------------------------------------------------------------------
    def record_edit(self, relative: str) -> str:
        if self.spec is None or self.workflow is None or self.root is None:
            return ""
        path = str(relative or "").replace("\\", "/").lstrip("./")
        feature = self.spec.record_edit(path)
        if feature is None:
            return ""
        save_spec(self.spec, self.root)
        task = self.workflow.task(f"F-{feature.id}")
        if task is not None and task.status == "pending":
            try:
                self.workflow.start(task.id)
            except Exception:
                pass
            save_workflow(self.workflow, self.root)
        return feature.id

    def lint(self, *, with_index: bool = True) -> tuple[Report | None, str]:
        if self.root is None:
            return None, "没有工程，跳过静态检查。"
        try:
            report = run_checks(self.root, with_index=with_index)
        except Exception as exc:  # noqa: BLE001 - 检查器崩溃不能中断交付
            return None, f"静态检查执行失败：{exc}"
        self.last_lint = report
        return report, report.markdown() if hasattr(report, "markdown") else str(report)

    # ------------------------------------------------------------------
    def before_build(self, label: str = "pre-build") -> str:
        if self.snapshots is None:
            return ""
        try:
            snapshot = self.snapshots.create(label, note=f"自动创建于 {label}")
            self._last_snapshot = snapshot.id
            return snapshot.id
        except Exception as exc:  # noqa: BLE001 - 快照失败不阻断构建
            self.notes.append(f"快照失败：{exc}")
            return ""

    def record_build(self, ok: bool, output: str = "") -> dict[str, Any]:
        self.last_build_ok = bool(ok)
        self.last_build_output = output or ""
        if self.workflow is None:
            return {"action": "continue"}
        try:
            if ok:
                self.guard.record_success(kind="build")
                task = self.workflow.task("T91-build")
                if task is not None:
                    self.workflow.complete(task.id, evidence="build=pass")
                if self.snapshots is not None and self._last_snapshot:
                    self.snapshots.mark_good(self._last_snapshot)
                for feature in self.spec.features if self.spec else []:
                    if feature.status == "in_progress":
                        feature.status = "built"
            else:
                advice = self.guard.record_failure(
                    _first_error(output) or "Gradle build failed",
                    kind="build",
                    location=_first_error_line(output),
                )
                task = self.workflow.task("T91-build")
                if task is not None:
                    self.workflow.fail(task.id, _first_error(output)[:300])
                if self.root is not None:
                    save_workflow(self.workflow, self.root)
                    if self.spec is not None:
                        save_spec(self.spec, self.root)
                return advice
        except Exception as exc:  # noqa: BLE001 - 状态记录失败不影响修复循环
            self.notes.append(f"任务状态更新失败：{exc}")
        if self.root is not None:
            try:
                save_workflow(self.workflow, self.root)
                if self.spec is not None:
                    save_spec(self.spec, self.root)
            except Exception:
                pass
        return {"action": "continue"}

    def record_test(self, ok: bool, output: str = "") -> None:
        self.last_test_ok = bool(ok)
        self.last_build_output = output or self.last_build_output

    def record_runtime(self, ok: bool) -> None:
        self.last_runtime_ok = bool(ok)

    def rollback(self, snapshot: str = "", *, dry_run: bool = False) -> dict[str, Any]:
        if self.snapshots is None:
            return {"ok": False, "reason": "没有工程"}
        snapshot_id = snapshot or self.snapshots.latest_good() or self._last_snapshot
        if not snapshot_id:
            return {"ok": False, "reason": "还没有可用快照"}
        try:
            result = self.snapshots.restore(snapshot_id, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - 回滚失败要完整返回
            return {"ok": False, "reason": str(exc), "snapshot": snapshot_id}
        return {"ok": True, "snapshot": snapshot_id, **result}

    def list_snapshots(self) -> dict[str, Any]:
        if self.snapshots is None:
            return {"snapshots": [], "latestGood": ""}
        return {
            "snapshots": [item.to_dict() for item in self.snapshots.snapshots()[:12]],
            "latestGood": self.snapshots.latest_good(),
        }

    # ------------------------------------------------------------------
    def finalize(
        self,
        *,
        build_ok: bool | None = None,
        test_ok: bool | None = None,
        runtime_ok: bool | None = None,
    ) -> str:
        """Run lint + acceptance gate and return a markdown evidence block."""
        if self.root is None or self.spec is None or self.workflow is None:
            return ""
        report, _ = self.lint()
        effective_build = self.last_build_ok if build_ok is None else build_ok
        effective_test = self.last_test_ok if test_ok is None else test_ok
        effective_runtime = self.last_runtime_ok if runtime_ok is None else runtime_ok
        lines: list[str] = ["## 交付自检（2.0 验收门禁）"]
        blocking: list[str] = []
        for feature in self.spec.features:
            task = self.workflow.task(f"F-{feature.id}") or self.workflow.task(
                "T01-scaffold" if feature.id == "core" else ""
            )
            if task is None:
                continue
            gate = verify(
                self.spec,
                task,
                self.root,
                build_ok=effective_build,
                test_ok=effective_test,
                runtime_ok=effective_runtime,
                manual_ok=None,
                report=report,
            )
            if gate.passed:
                if task.status != "done":
                    try:
                        self.workflow.complete(task.id, evidence="; ".join(gate.evidence()))
                    except Exception:
                        pass
                if feature.status in {"planned", "in_progress"}:
                    feature.status = "done"
            elif task.status == "running":
                task.status = "blocked"
                task.detail = "; ".join(item.message for item in gate.blocking)[:400]
                blocking.append(f"{feature.id}: " + "；".join(item.message for item in gate.blocking))
        for task_id in ("T91-build", "T92-verify", "T99-acceptance"):
            task = self.workflow.task(task_id)
            if task is None:
                continue
            gate = verify(
                self.spec,
                task,
                self.root,
                build_ok=effective_build,
                test_ok=effective_test,
                runtime_ok=effective_runtime,
                manual_ok=None,
                report=report,
            )
            for check in gate.checks:
                mark = "通过" if check.passed else ("未通过" if check.required else "警告")
                lines.append(f"- {task_id} / {check.id}：{mark}（{check.message}）")
            if not gate.passed and task.status != "blocked":
                task.status = "blocked"
                task.detail = "; ".join(item.message for item in gate.blocking)[:400]
                blocking.extend(f"{task_id}: {item.id} {item.message}" for item in gate.blocking)
        if report is not None:
            counts = report.counts()
            lines.append(
                f"- 静态检查：error={counts.get('error', 0)} warning={counts.get('warning', 0)} "
                f"info={counts.get('info', 0)}"
            )
        lines.append(
            f"- 构建：{'通过' if effective_build else '未通过' if effective_build is False else '尚未执行'}"
        )
        lines.append(f"- 测试：{'通过' if effective_test else '未通过' if effective_test is False else '没有测试证据'}")
        if blocking:
            lines.append("- 阻断项：" + "；".join(blocking[:6]))
            lines.append("- 结论：必需验收项未全部通过，不能标记为完成。")
        else:
            lines.append("- 结论：必需验收项已通过。")
        self.blocked = blocking
        try:
            save_workflow(self.workflow, self.root)
            save_spec(self.spec, self.root)
        except Exception:
            pass
        return "\n".join(lines)


def _first_error(output: str) -> str:
    for line in (output or "").splitlines():
        text = line.strip()
        if text.lower().startswith(("error:", "e: ", "> task", "failure:")):
            return text[:500]
        if " error: " in text or text.lower().startswith("caused by"):
            return text[:500]
    rows = [line.strip() for line in (output or "").splitlines() if line.strip()]
    return rows[0][:500] if rows else ""


def _first_error_line(output: str) -> str:
    for line in (output or "").splitlines():
        text = line.strip()
        if ".java:" in text or ".kt:" in text or "build.gradle" in text:
            return text[:240]
    return ""
