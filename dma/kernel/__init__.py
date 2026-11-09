"""Darkitten Mod Agent 2.0 engineering kernel.

The kernel turns a one-line request into an explicit specification, a DAG of
tasks and a hard acceptance gate.  It also provides the static-analysis and
asset tooling used by the agent loop.
"""

from __future__ import annotations

from .findings import Finding, Report
from .gate import GateCheck, GateReport, feature_for, verify
from .edit import EditResult, FileCache, diff_text, insert_member, replace_body, replace_symbol
from .loopguard import Failure, LoopGuard
from .mappings import MappingInfo, MappingSet, Translator, detect_project
from .rules import Rule, catalog, coverage, matrix_markdown, rule, rules
from .snapshot import Snapshot, SnapshotStore
from .spec import (
    AcceptanceItem,
    Feature,
    ModSpec,
    derive_spec,
    load_spec,
    save_spec,
    spec_markdown,
)
from .symbols import SymbolIndex, build_index, load_index, query
from . import tools
from .workflow import (
    AcceptanceBlocked,
    Task,
    Workflow,
    WorkflowCycleError,
    load_workflow,
    plan_tasks,
    save_workflow,
    topological_order,
    workflow_markdown,
)

__all__ = [
    "AcceptanceItem",
    "Feature",
    "ModSpec",
    "derive_spec",
    "load_spec",
    "save_spec",
    "spec_markdown",
    "AcceptanceBlocked",
    "Task",
    "Workflow",
    "WorkflowCycleError",
    "load_workflow",
    "plan_tasks",
    "save_workflow",
    "topological_order",
    "workflow_markdown",
    "Snapshot",
    "SnapshotStore",
    "Failure",
    "LoopGuard",
    "Finding",
    "Report",
    "GateCheck",
    "GateReport",
    "feature_for",
    "verify",
    "MappingInfo",
    "MappingSet",
    "Translator",
    "detect_project",
    "Rule",
    "catalog",
    "coverage",
    "matrix_markdown",
    "rule",
    "rules",
    "SymbolIndex",
    "build_index",
    "load_index",
    "query",
    "EditResult",
    "FileCache",
    "diff_text",
    "insert_member",
    "replace_body",
    "replace_symbol",
    "tools",
]
