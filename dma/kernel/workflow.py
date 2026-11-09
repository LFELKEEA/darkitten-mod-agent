"""Task graph for a mod build (checklist P0-02).

The workflow is deliberately small and dependency-free.  It is the single
source of truth for *what may run next*:

* every task declares ``depends_on``;
* ``ready_tasks`` only returns tasks whose dependencies are all ``done``;
* a failure marks every downstream task ``blocked`` instead of letting the
  model keep writing code on top of a broken foundation;
* ``topological_order`` refuses cycles instead of silently looping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .spec import ModSpec, now_iso

WORKFLOW_SCHEMA = "dma-workflow/2"
WORKFLOW_RELATIVE = Path(".dma") / "workflow.json"

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
BLOCKED = "blocked"
SKIPPED = "skipped"

TERMINAL = {DONE, FAILED, BLOCKED, SKIPPED}
OPEN = {PENDING, RUNNING}


class WorkflowCycleError(ValueError):
    """Raised when the task graph contains a dependency cycle."""


class AcceptanceBlocked(RuntimeError):
    """Raised when a task is completed without satisfying its acceptance."""


@dataclass
class Task:
    id: str
    title: str
    kind: str = "feature"
    description: str = ""
    depends_on: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    level: str = "P1"
    priority: int = 50
    status: str = PENDING
    attempts: int = 0
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # ------------------------------------------------------------------
    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def is_open(self) -> bool:
        return self.status in OPEN

    def touch(self) -> None:
        self.updated_at = now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "description": self.description,
            "depends_on": list(self.depends_on),
            "files": list(self.files),
            "acceptance": list(self.acceptance),
            "level": self.level,
            "priority": self.priority,
            "status": self.status,
            "attempts": self.attempts,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "notes": list(self.notes),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Task":
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            kind=str(raw.get("kind") or "feature"),
            description=str(raw.get("description") or ""),
            depends_on=[str(item) for item in raw.get("depends_on") or []],
            files=[str(item) for item in raw.get("files") or []],
            acceptance=[str(item) for item in raw.get("acceptance") or []],
            level=str(raw.get("level") or "P1"),
            priority=int(raw.get("priority") or 50),
            status=str(raw.get("status") or PENDING),
            attempts=int(raw.get("attempts") or 0),
            detail=str(raw.get("detail") or ""),
            evidence=[str(item) for item in raw.get("evidence") or []],
            notes=[str(item) for item in raw.get("notes") or []],
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
        )


def topological_order(tasks: Iterable[Task | dict[str, Any]]) -> list[Task]:
    """Return tasks in dependency order.

    Unknown dependencies are ignored (a task may reference a capability that
    was removed from the spec).  Cycles raise :class:`WorkflowCycleError`.
    """

    items: list[Task] = [
        item if isinstance(item, Task) else Task.from_dict(item) for item in tasks
    ]
    by_id = {item.id: item for item in items}
    indegree: dict[str, int] = {item.id: 0 for item in items}
    children: dict[str, list[str]] = {item.id: [] for item in items}
    for item in items:
        for dep in item.depends_on:
            if dep not in by_id or dep == item.id:
                continue
            indegree[item.id] += 1
            children[dep].append(item.id)

    # Stable tie-break: priority then original order, so the plan stays
    # readable across runs.
    position = {item.id: index for index, item in enumerate(items)}
    ready = [
        item.id
        for item in items
        if indegree[item.id] == 0
    ]
    ready.sort(key=lambda key: (by_id[key].priority, position[key]))
    ordered: list[Task] = []
    while ready:
        current = ready.pop(0)
        ordered.append(by_id[current])
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=lambda key: (by_id[key].priority, position[key]))

    if len(ordered) != len(items):
        remaining = [item.id for item in items if indegree[item.id] > 0]
        raise WorkflowCycleError("任务依赖存在环：" + " -> ".join(remaining))
    return ordered


@dataclass
class Workflow:
    idea: str
    loader: str = "NeoForge"
    mc: str = "1.21.1"
    tasks: list[Task] = field(default_factory=list)
    schema: str = WORKFLOW_SCHEMA
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # ------------------------------------------------------------------
    def task(self, task_id: str) -> Task | None:
        for item in self.tasks:
            if item.id == task_id:
                return item
        return None

    def require(self, task_id: str) -> Task:
        item = self.task(task_id)
        if item is None:
            raise KeyError(f"未知任务：{task_id}")
        return item

    def order(self) -> list[Task]:
        return topological_order(self.tasks)

    def order_ids(self) -> list[str]:
        return [item.id for item in self.order()]

    # ------------------------------------------------------------------
    def _done_dependencies(self, item: Task) -> bool:
        for dep in item.depends_on:
            other = self.task(dep)
            if other is None:
                continue
            if other.status != DONE:
                return False
        return True

    def ready_tasks(self) -> list[Task]:
        """Pending tasks whose dependencies are all done, in DAG order."""
        ready: list[Task] = []
        for item in self.order():
            if item.status == PENDING and self._done_dependencies(item):
                ready.append(item)
        return ready

    def current(self) -> Task | None:
        ready = self.ready_tasks()
        return ready[0] if ready else None

    def blocked_ids(self, task_id: str) -> list[str]:
        """All downstream ids reachable from ``task_id`` (excluding itself)."""
        seen: set[str] = set()
        queue = [task_id]
        while queue:
            current = queue.pop(0)
            for item in self.tasks:
                if current in item.depends_on and item.id not in seen:
                    seen.add(item.id)
                    queue.append(item.id)
        return [item.id for item in self.order() if item.id in seen]

    def dependents(self, task_id: str) -> list[Task]:
        ids = set(self.blocked_ids(task_id))
        return [item for item in self.order() if item.id in ids]

    # ------------------------------------------------------------------
    def start(self, task_id: str) -> Task:
        item = self.require(task_id)
        if item.status == BLOCKED:
            raise RuntimeError(f"任务 {task_id} 已被上游阻塞，请先修复上游")
        item.status = RUNNING
        item.attempts += 1
        item.touch()
        self.updated_at = now_iso()
        return item

    def complete(
        self,
        task_id: str,
        *,
        evidence: Iterable[str] | None = None,
        force: bool = False,
        detail: str = "",
    ) -> Task:
        """Mark a task done.

        A task that carries acceptance ids may only be completed with
        evidence (or ``force=True`` for manual overrides).  This is the
        in-process half of the acceptance gate; the caller is expected to run
        the real checks before calling this.
        """
        item = self.require(task_id)
        evidence_list = [str(value) for value in (evidence or []) if str(value)]
        if item.acceptance and not evidence_list and not force:
            raise AcceptanceBlocked(
                f"任务 {task_id} 未通过验收：缺少 {', '.join(item.acceptance)} 的证据"
            )
        item.status = DONE
        item.detail = detail or item.detail
        if evidence_list:
            item.evidence.extend(value for value in evidence_list if value not in item.evidence)
        item.touch()
        self.updated_at = now_iso()
        return item

    def fail(self, task_id: str, detail: str = "") -> list[str]:
        """Fail a task and block everything downstream.  Returns blocked ids."""
        item = self.require(task_id)
        item.status = FAILED
        if detail:
            item.detail = detail
        item.touch()
        blocked: list[str] = []
        for other in self.dependents(task_id):
            if other.status in {PENDING, RUNNING, SKIPPED}:
                other.status = BLOCKED
                other.detail = f"上游失败：{task_id}"
                other.touch()
                blocked.append(other.id)
        self.updated_at = now_iso()
        return blocked

    def skip(self, task_id: str, reason: str = "") -> None:
        item = self.require(task_id)
        item.status = SKIPPED
        if reason:
            item.detail = reason
        item.touch()
        self.updated_at = now_iso()

    def reopen(self, task_id: str) -> None:
        """Reset a failed task and its downstream back to pending."""
        item = self.require(task_id)
        item.status = PENDING
        item.detail = ""
        item.touch()
        for other in self.dependents(task_id):
            if other.status in {BLOCKED, FAILED, SKIPPED}:
                other.status = PENDING
                other.detail = ""
                other.touch()
        self.updated_at = now_iso()

    # ------------------------------------------------------------------
    def progress(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.tasks:
            counts[item.status] = counts.get(item.status, 0) + 1
        total = len(self.tasks)
        done = counts.get(DONE, 0) + counts.get(SKIPPED, 0)
        current = self.current()
        return {
            "total": total,
            "done": done,
            "percent": round(done * 100 / total, 1) if total else 0.0,
            "status": counts,
            "current": current.id if current else "",
            "current_title": current.title if current else "",
            "failed": [
                item.id for item in self.tasks if item.status == FAILED
            ],
            "blocked": [
                item.id for item in self.tasks if item.status == BLOCKED
            ],
        }

    def next_actions(self, limit: int = 5) -> list[Task]:
        return self.ready_tasks()[:limit]

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "idea": self.idea,
            "loader": self.loader,
            "mc": self.mc,
            "tasks": [item.to_dict() for item in self.tasks],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Workflow":
        return cls(
            idea=str(raw.get("idea") or ""),
            loader=str(raw.get("loader") or "NeoForge"),
            mc=str(raw.get("mc") or "1.21.1"),
            tasks=[
                Task.from_dict(item)
                for item in raw.get("tasks") or []
                if isinstance(item, dict)
            ],
            schema=str(raw.get("schema") or WORKFLOW_SCHEMA),
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
        )

    def markdown(self) -> str:
        lines = [
            f"# 任务图：{self.idea or '未命名需求'}",
            "",
            f"- 目标：{self.loader} {self.mc}",
            f"- 进度：{self.progress()['percent']}%",
            "",
            "| 任务 | 类型 | 依赖 | 状态 | 验收 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in self.order():
            deps = ", ".join(item.depends_on) or "—"
            acceptance = ", ".join(item.acceptance) or "—"
            lines.append(
                f"| `{item.id}` {item.title} | {item.kind} | {deps} | {item.status} | {acceptance} |"
            )
        return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Planning
# ----------------------------------------------------------------------

LEVEL_PRIORITY = {"P0": 0, "P1": 10, "P2": 20, "P3": 30, "TOOL": 40}


def _feature_level(feature_id: str) -> str:
    if feature_id in {"core", "registry", "acceptance"}:
        return "P0"
    if feature_id in {"tests", "network"}:
        return "P1"
    return "P1"


def plan_tasks(spec: ModSpec, *, include_runtime: bool = True) -> Workflow:
    """Turn a :class:`ModSpec` into an executable DAG.

    Layout::

        T00 spec → T01 scaffold → F-<feature> … → T90 lint → T91 build
                 → T92 verify → T99 acceptance → T100 package
    """

    tasks: list[Task] = [
        Task(
            "T00-spec", "需求拆解与验收标准", "spec",
            "把一句话需求固化成本文件与验收清单",
            [], [str(Path(".dma") / "spec.json")],
            [], "P0", LEVEL_PRIORITY["P0"],
        ),
        Task(
            "T01-scaffold", "工程骨架与构建脚本", "scaffold",
            "生成 Gradle、入口类、元数据与目录结构",
            ["T00-spec"],
            ["build.gradle", "settings.gradle", "gradle.properties"],
            ["build", "gradle"], "P0", LEVEL_PRIORITY["P0"] + 1,
        ),
    ]
    feature_tasks: dict[str, str] = {"core": "T01-scaffold"}
    order_index = 2
    for feature in spec.features:
        if feature.id in {"core", "acceptance"}:
            continue
        task_id = f"F-{feature.id}"
        deps = ["T01-scaffold"]
        for dep in feature.depends_on:
            mapped = feature_tasks.get(dep)
            if mapped and mapped not in deps:
                deps.append(mapped)
        acceptance_ids = [
            item.id for item in feature.acceptance if item.required
        ]
        level = _feature_level(feature.id)
        tasks.append(
            Task(
                task_id, feature.title, "feature", feature.description,
                deps, list(feature.files), acceptance_ids,
                level, LEVEL_PRIORITY.get(level, 40) + order_index,
            )
        )
        feature_tasks[feature.id] = task_id
        order_index += 1

    feature_task_ids = [
        item.id for item in tasks if item.kind == "feature" and item.id != "T01-scaffold"
    ]
    tasks.append(
        Task(
            "T90-lint", "静态检查（P0/P1/P2/P3）", "lint",
            "遍历符号、映射、Mixin、线程、网络、注册表、数据包、资产和 Gradle 规则",
            ["T01-scaffold"] + feature_task_ids,
            [], ["lint-p0"], "P0", LEVEL_PRIORITY["P0"] + 90,
        )
    )
    tasks.append(
        Task(
            "T91-build", "编译与构建", "build",
            "Gradle 编译必须成功；失败时进入修复循环",
            ["T90-lint"], ["build.gradle"], ["build"],
            "P0", LEVEL_PRIORITY["P0"] + 91,
        )
    )
    verify_deps = ["T91-build"]
    tasks.append(
        Task(
            "T92-verify", "运行时验证", "verify",
            "GameTest / 开发端探针 / 场景卡片（缺失时记录为警告）",
            verify_deps, ["src/test/**", "src/gametest/**"],
            (["runtime"] if include_runtime else []),
            "P1", LEVEL_PRIORITY["P1"] + 92,
        )
    )

    all_feature_ids = [
        item.id for item in tasks if item.kind in {"feature", "scaffold", "lint", "build", "verify"}
    ]
    tasks.append(
        Task(
            "T99-acceptance", "交付验收门禁", "acceptance",
            "汇总所有证据；任一必需项未通过则不得交付",
            all_feature_ids,
            [],
            ["acceptance", "build", "lint-all", "audit"],
            "P0", LEVEL_PRIORITY["P0"] + 99,
        )
    )
    tasks.append(
        Task(
            "T100-package", "打包与交付", "package",
            "生成发布包、元数据、许可证与校验和",
            ["T99-acceptance"], [], [], "P2", LEVEL_PRIORITY["P2"] + 100,
        )
    )
    return Workflow(
        idea=spec.idea,
        loader=spec.loader,
        mc=spec.mc,
        tasks=tasks,
    )


def workflow_path(root: Path) -> Path:
    return Path(root) / WORKFLOW_RELATIVE


def save_workflow(workflow: Workflow, root: Path) -> Path:
    path = workflow_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(workflow.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def load_workflow(root: Path) -> Workflow | None:
    path = workflow_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return Workflow.from_dict(raw)


def workflow_markdown(workflow: Workflow) -> str:
    return workflow.markdown()
