"""Stress, fuzz and unbounded-work checks (P1-04)."""

from __future__ import annotations

import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

TICK_METHODS = {
    "tick", "serverTick", "clientTick", "aiStep", "customServerAiStep",
    "render", "renderGui", "update", "onTick", "gameTick",
}
GUARDS = ("limit", "budget", "max", "counter", "depth", "visited", "size()", "hasNext", "isRemoved")


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_loops(ctx))
    rows.extend(_recursion(ctx))
    rows.extend(_blocking(ctx))
    rows.extend(_allocations(ctx))
    rows.extend(_harness(ctx))
    return rows


def _loops(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            hot = method.name in TICK_METHODS or method.name.endswith(("Tick", "Update"))
            if re.search(r"\bwhile\s*\(\s*true\s*\)", body):
                rows.append(finding(
                    "P1-04", f"{java_class.name}.{method.name} 使用 while(true)，缺少退出条件风险",
                    path=rel, line=method.line, severity="warning",
                    suggestion="改为有界循环，或显式 break/return 并加迭代上限。",
                ))
            if hot and re.search(r"(?:getEntities|getAllEntities|players\(\)|getEntitiesOfClass)", body):
                if not any(guard in body for guard in GUARDS) and "for (" in body:
                    rows.append(finding(
                        "P1-04", f"{java_class.name}.{method.name} 每 tick 全量遍历实体，没有数量上限",
                        path=rel, line=method.line, severity="info",
                        suggestion="先用 AABB/区块过滤，再限制每 tick 处理数量。",
                    ))
            if hot and re.search(r"for\s*\([^;]*\)\s*\{", body) and body.count("for (") >= 2:
                rows.append(finding(
                    "P1-04", f"{java_class.name}.{method.name} 热路径存在嵌套循环",
                    path=rel, line=method.line, severity="info",
                    suggestion="把 O(n²) 改为索引/空间分区，并补压力测试。",
                ))
    return rows


def _recursion(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        for method in java_class.methods:
            if not method.body or method.is_constructor:
                continue
            target = f"{method.name}("
            if target not in method.body:
                continue
            if any(guard in method.body for guard in GUARDS):
                continue
            if method.name in {"toString", "hashCode", "equals", "compareTo"}:
                continue
            rows.append(finding(
                "P1-04", f"{java_class.name}.{method.name} 递归调用自己且没有深度保护",
                path=rel, line=method.line, severity="warning",
                suggestion="加深度/预算参数或改为显式栈，避免 StackOverflowError。",
            ))
    return rows


def _blocking(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            hot = method.name in TICK_METHODS or method.name.endswith(("Tick", "Update"))
            if not hot:
                continue
            if "Thread.sleep(" in body or "LockSupport.park" in body:
                rows.append(finding(
                    "P1-04", f"{java_class.name}.{method.name} 在主线程 Tick 中阻塞",
                    path=rel, line=method.line, severity="error",
                    suggestion="移除 sleep；异步任务提交到执行器并回调主线程。",
                ))
            if "join()" in body and "CompletableFuture" in java.text:
                rows.append(finding(
                    "P1-04", f"{java_class.name}.{method.name} 在 Tick 中等待异步任务完成",
                    path=rel, line=method.line, severity="warning",
                    suggestion="不要用 join 同步等待；改为完成回调。",
                ))
            if "System.gc()" in body:
                rows.append(finding(
                    "P1-04", f"{java_class.name}.{method.name} 在 Tick 中触发 GC",
                    path=rel, line=method.line, severity="warning",
                    suggestion="移除显式 GC，用对象复用/缓存降低分配。",
                ))
    return rows


def _allocations(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java in ctx.java_files:
        text = java.text
        rel = ctx.rel(java.path)
        for match in re.finditer(r"new\s+\w+\s*\[\s*(\d{6,})\s*\]", text):
            rows.append(finding(
                "P1-04", f"一次性分配 {match.group(1)} 长度数组",
                path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                suggestion="改为按需增长/分块，避免 OOM 与长时间 GC。",
            ))
        for match in re.finditer(r"for\s*\([^;]*;\s*\w+\s*<\s*(\d{5,})\s*;", text):
            rows.append(finding(
                "P1-04", f"固定循环 {match.group(1)} 次，可能造成单 tick 卡顿",
                path=rel, line=text[:match.start()].count("\n") + 1, severity="info",
                suggestion="分摊到多 tick 执行，并测量耗时。",
            ))
        if re.search(r"new\s+ArrayList\s*<[^>]*>\s*\(\s*\d{6,}\s*\)", text):
            rows.append(finding(
                "P1-04", "预分配超大集合容量",
                path=rel, line=1, severity="info",
                suggestion="按实际规模估算容量，避免启动即占大量内存。",
            ))
    return rows


def _harness(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    candidates = [
        "src/test/java", "src/test", "src/gametest", "src/gameTest",
        "scenarios", "tests",
    ]
    has_tests = any((ctx.root / item).exists() for item in candidates)
    build = ""
    for name in ("build.gradle", "build.gradle.kts"):
        if ctx.exists(name):
            build += ctx.read_rel(name)
    fuzz = any(token in build.lower() for token in ("jqwik", "quicktheories", "randomizedtesting", "fuzz"))
    if not has_tests:
        rows.append(finding(
            "P1-04", "没有测试目录，压力/模糊测试无从执行",
            path="src/test", severity="info",
            suggestion="至少补单元测试与 GameTest；关键路径加随机/压力用例。",
        ))
    elif not fuzz:
        rows.append(finding(
            "P1-04", "没有随机化/模糊测试依赖，边界条件覆盖不足",
            path="build.gradle", severity="info",
            suggestion="引入 jqwik 或自建随机输入生成器，固定种子便于复现。",
        ))
    return rows
