"""版本事实核查：把 modfacts 的静态冲突接进 lint_project。

原来版本差异只靠提示词提醒，模型仍可能把 1.20.1 的目录结构写进 1.21 工程。
这里把 Java、元数据、数据目录、物品定义、API 混用等硬冲突变成 lint 结果，
在编写阶段就暴露，而不是等运行崩溃。
"""

from __future__ import annotations

from typing import Iterable

from .common import ProjectContext, finding


def run(ctx: ProjectContext) -> Iterable:
    if not ctx.loader or not ctx.mc:
        return
    try:
        from ... import modfacts
    except Exception:  # pragma: no cover - 事实层不可用时不影响其他检查
        return
    severity_map = {"error": "error", "warn": "warning", "info": "info"}
    for issue in modfacts.check_project(ctx.root, ctx.loader, ctx.mc):
        if issue.severity == "info":
            # pack.mcmeta 之类的提示不必在 lint 里刷屏。
            continue
        yield finding(
            "P3-30",
            issue.message,
            path=issue.path,
            severity=severity_map.get(issue.severity, "warning"),
            suggestion=issue.fix,
        )