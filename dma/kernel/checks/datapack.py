"""Data pack schema checks (P1-11 / P2-44..46)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

COUNT_TYPES = {"minecraft:crafting_shaped", "minecraft:crafting_shapeless", "minecraft:smelting",
               "minecraft:blasting", "minecraft:smoking", "minecraft:campfire_cooking",
               "minecraft:stonecutting", "minecraft:smithing_transform", "minecraft:smithing_trim"}


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_syntax(ctx))
    rows.extend(_recipes(ctx))
    rows.extend(_advancements(ctx))
    rows.extend(_tags(ctx))
    rows.extend(_predicates(ctx))
    rows.extend(_functions(ctx))
    return rows


def _paths(ctx: ProjectContext, folder: str) -> list[Path]:
    rows = ctx.json_files(f"src/main/resources/data/**/{folder}/**/*.json")
    return rows


def _syntax(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/data/**/*.json"):
        if not path.is_file():
            continue
        try:
            json.loads(ctx.read(path))
        except ValueError as exc:
            rows.append(finding(
                "P1-11", f"数据包 JSON 解析失败：{exc}",
                path=ctx.rel(path), severity="error",
                suggestion="修复 JSON 语法；数据包整体可能无法加载。",
            ))
    return rows


def _recipes(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in _paths(ctx, "recipe"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        kind = str(data.get("type") or "")
        if not kind:
            rows.append(finding(
                "P1-11", "配方缺少 type 字段",
                path=rel, severity="error",
                suggestion="补上 minecraft:crafting_shaped 等类型。",
            ))
            continue
        if kind == "minecraft:crafting_shaped":
            pattern = data.get("pattern")
            key = data.get("key")
            if not isinstance(pattern, list) or not pattern:
                rows.append(finding("P1-11", "有序配方缺少 pattern", path=rel))
            elif not isinstance(key, dict):
                rows.append(finding("P1-11", "有序配方缺少 key", path=rel))
            else:
                width = {len(str(row)) for row in pattern}
                if len(width) > 1:
                    rows.append(finding("P1-11", f"配方 pattern 每行长度不一致：{sorted(width)}", path=rel))
                if len(pattern) > 3:
                    rows.append(finding("P1-11", "配方 pattern 超过 3 行，工作台放不下", path=rel))
                chars = {char for row in pattern for char in str(row) if char != " "}
                for char in sorted(chars - set(key)):
                    rows.append(finding(
                        "P1-11", f"pattern 使用了未定义的 key：{char}",
                        path=rel, severity="error",
                        suggestion="在 key 中定义该符号对应的 ingredient。",
                    ))
                for char in sorted(set(key) - chars):
                    rows.append(finding(
                        "P1-11", f"key {char} 没有出现在 pattern 中",
                        path=rel, severity="info",
                        suggestion="删除无用 key，避免意图不清晰。",
                    ))
        if kind == "minecraft:crafting_shapeless":
            ingredients = data.get("ingredients")
            if not isinstance(ingredients, list) or not ingredients:
                rows.append(finding("P1-11", "无序配方缺少 ingredients", path=rel))
            elif len(ingredients) > 9:
                rows.append(finding("P1-11", "无序配方 ingredients 超过 9 个", path=rel))
        if kind in {"minecraft:smelting", "minecraft:blasting", "minecraft:smoking", "minecraft:campfire_cooking"}:
            if "ingredient" not in data:
                rows.append(finding("P1-11", "熔炼配方缺少 ingredient", path=rel))
            time_value = data.get("cookingtime")
            if isinstance(time_value, int) and time_value <= 0:
                rows.append(finding("P1-11", "cookingtime 必须大于 0", path=rel))
            exp = data.get("experience")
            if isinstance(exp, (int, float)) and exp < 0:
                rows.append(finding("P1-11", "experience 不能为负", path=rel))
        if kind.startswith("minecraft:smithing") and "template" not in data:
            rows.append(finding("P1-11", "锻造配方缺少 template", path=rel, severity="warning"))
        if kind in COUNT_TYPES:
            result = data.get("result")
            if not isinstance(result, dict):
                rows.append(finding("P1-11", "配方缺少 result", path=rel, severity="error"))
            else:
                count = result.get("count", 1)
                if isinstance(count, int) and not 1 <= count <= 64:
                    rows.append(finding("P1-11", f"result.count={count} 超出 1..64", path=rel))
                if not result.get("item") and not result.get("id"):
                    rows.append(finding("P1-11", "result 缺少 item/id", path=rel, severity="error"))
        if "conditions" in data and not isinstance(data["conditions"], list):
            rows.append(finding("P1-11", "conditions 必须是数组", path=rel))
    return rows


def _advancements(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in _paths(ctx, "advancement"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        criteria = data.get("criteria")
        if not isinstance(criteria, dict) or not criteria:
            rows.append(finding(
                "P1-11", "进度缺少 criteria，无法解锁",
                path=rel, severity="error",
                suggestion="至少提供一个 trigger 条件。",
            ))
            continue
        names = set(criteria)
        for group in data.get("requirements") or []:
            if not isinstance(group, list):
                continue
            for name in group:
                if name not in names:
                    rows.append(finding(
                        "P1-11", f"requirements 引用了不存在的 criteria：{name}",
                        path=rel, severity="error",
                    ))
        display = data.get("display")
        if isinstance(display, dict):
            if "icon" not in display:
                rows.append(finding("P1-11", "进度 display 缺少 icon", path=rel, severity="warning"))
            if not display.get("title") or not display.get("description"):
                rows.append(finding("P1-11", "进度 display 缺少 title/description", path=rel))
        parent = data.get("parent")
        if isinstance(parent, str) and parent.startswith(("minecraft:", "data:")):
            pass
    return rows


def _tags(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/data/**/tags/**/*.json"):
        if not path.is_file():
            continue
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        values = data.get("values")
        if values is None:
            rows.append(finding("P1-11", "标签缺少 values", path=rel, severity="warning"))
            continue
        if not isinstance(values, list):
            rows.append(finding("P1-11", "标签 values 必须是数组", path=rel, severity="error"))
            continue
        replace = bool(data.get("replace"))
        if replace and not values:
            rows.append(finding(
                "P2-44", "标签 replace=true 但 values 为空，会清空原版标签",
                path=rel, severity="warning",
                suggestion="确认这是预期行为；否则改为 append 语义（replace=false）。",
            ))
        for entry in values:
            if isinstance(entry, str):
                if entry.startswith("#") and not _tag_exists(ctx, entry[1:]):
                    rows.append(finding(
                        "P2-45", f"引用了不存在的标签：{entry}",
                        path=rel, severity="info",
                        suggestion="确认该标签由原版/前置提供，或补上定义。",
                    ))
            elif isinstance(entry, dict):
                if "id" not in entry:
                    rows.append(finding("P1-11", "标签条目对象缺少 id", path=rel, severity="warning"))
            else:
                rows.append(finding("P1-11", f"标签条目类型不合法：{type(entry).__name__}", path=rel))
        name = path.stem
        if name != name.lower() or " " in name:
            rows.append(finding(
                "P3-31", f"标签文件名 {name} 不是小写 snake_case",
                path=rel, severity="info",
            ))
        if replace and any(isinstance(item, str) and item.startswith("#") for item in values):
            rows.append(finding(
                "P2-44", "replace=true 同时引用子标签，合并结果依赖加载顺序",
                path=rel, severity="info",
                suggestion="尽量避免标签嵌套 + replace 组合，或写测试固定结果。",
            ))
    return rows


def _predicates(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for folder in ("predicate", "item_modifier"):
        for path in _paths(ctx, folder):
            data = ctx.load_json(path)
            if not isinstance(data, dict):
                continue
            if "condition" not in data:
                rows.append(finding(
                    "P1-11", f"{folder} 缺少 condition",
                    path=ctx.rel(path), severity="error",
                ))
            elif not str(data.get("condition")).startswith("minecraft:"):
                rows.append(finding(
                    "P1-11", "condition 未使用完整命名空间",
                    path=ctx.rel(path), severity="info",
                ))
    return rows


def _functions(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/data/**/function/**/*.mcfunction"):
        if not path.is_file():
            continue
        rel = ctx.rel(path)
        text = ctx.read(path)
        namespace, function_path = _function_id(rel)
        current = f"{namespace}:{function_path}"
        for line_no, line in enumerate(text.splitlines(), 1):
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if value.startswith("/"):
                rows.append(finding(
                    "P2-48", "mcfunction 命令前不能有斜杠",
                    path=rel, line=line_no, severity="error",
                    suggestion="删除行首的 /。",
                ))
                value = value.lstrip("/")
            for match in re.finditer(r"\bfunction\s+([#\w.:/-]+)", value):
                target = match.group(1)
                if target == current:
                    rows.append(finding(
                        "P2-48", f"函数递归调用自身：{current}",
                        path=rel, line=line_no, severity="warning",
                        suggestion="递归会触发 maxCommandChainLength；改为循环/迭代上限。",
                    ))
    return rows


def _tag_exists(ctx: ProjectContext, reference: str) -> bool:
    namespace, _, name = reference.partition(":")
    if not name:
        namespace, name = "minecraft", namespace
    if namespace == "minecraft":
        return True
    pattern = f"src/main/resources/data/{namespace}/tags/**/{name}.json"
    return any(path.is_file() for path in ctx.glob(pattern))


def _function_id(rel: str) -> tuple[str, str]:
    parts = rel.split("/")
    try:
        index = parts.index("data")
        namespace = parts[index + 1]
        rest = parts[index + 3:]
        name = "/".join(rest)
        if name.endswith(".mcfunction"):
            name = name[: -len(".mcfunction")]
        return namespace, name
    except (ValueError, IndexError):
        return "minecraft", rel
