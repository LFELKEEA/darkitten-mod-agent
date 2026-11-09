"""World, dimension, worldgen and fluid checks (P2-01..11 / P2-41..43)."""

from __future__ import annotations

import json
import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_dimensions(ctx))
    rows.extend(_worldgen(ctx))
    rows.extend(_java(ctx))
    rows.extend(_loot(ctx))
    return rows


def _dimensions(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.json_files("src/main/resources/data/**/dimension_type/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        height = data.get("height")
        min_y = data.get("min_y")
        logical = data.get("logical_height")
        for key, value in (("height", height), ("min_y", min_y)):
            if isinstance(value, int) and value % 16:
                rows.append(finding(
                    "P2-04", f"维度 {key}={value} 不是 16 的倍数",
                    path=rel,
                    suggestion="height/min_y 必须是 16 的倍数，否则区块生成边界错位。",
                ))
        if isinstance(logical, int) and isinstance(height, int) and logical > height:
            rows.append(finding(
                "P2-04", f"logical_height={logical} 大于 height={height}",
                path=rel, suggestion="逻辑高度不能超过世界高度。",
            ))
        if data.get("bed_works") is False and _uses(ctx, "BedBlock", "bed"):
            rows.append(finding(
                "P2-05", "维度不允许床，但代码仍使用床逻辑",
                path=rel, severity="warning",
                suggestion="改用重生锚或调整 bed_works。",
            ))
        if data.get("respawn_anchor_works") is False and _uses(ctx, "RespawnAnchor"):
            rows.append(finding(
                "P2-05", "维度不允许重生锚，但代码仍使用重生锚",
                path=rel, severity="warning",
                suggestion="调整 respawn_anchor_works 或改用其他重生方式。",
            ))
        if data.get("has_skylight") is False and data.get("has_ceiling") is True:
            if "getBrightness" in _all_text(ctx) and "hasCeiling" not in _all_text(ctx):
                rows.append(finding(
                    "P2-11", "封闭维度使用光照亮度但没有处理 ceiling 光照",
                    path=rel, severity="info",
                    suggestion="封闭维度用方块光/动态光源，不要依赖天空光。",
                ))
    for path in ctx.json_files("src/main/resources/data/**/dimension/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        if "generator" not in data:
            rows.append(finding(
                "P2-04", "维度 JSON 缺少 generator，加载会失败",
                path=ctx.rel(path),
            ))
    return rows


def _worldgen(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        worldgen = any(key in text for key in ("Feature<", "ConfiguredFeature", "PlacedFeature", "ChunkGenerator", "BiomeSource", "StructureType"))
        if not worldgen:
            continue
        if "new Random(" in text or "Math.random()" in text:
            rows.append(finding(
                "P2-08", f"{java_class.name} 使用独立随机源，世界生成结果不可复现",
                path=rel, line=java_class.line,
                suggestion="使用 level.random / RandomSource，并从区块种子派生。",
            ))
        if "setBlock(" in text and not any(key in text for key in ("isLoaded", "getChunk(", "hasChunk", "ensureCanWrite")):
            rows.append(finding(
                "P2-09", f"{java_class.name} 放置方块前未检查区块边界/加载状态",
                path=rel, line=java_class.line, severity="warning",
                suggestion="跨越区块边界时裁剪放置范围，并确认区块已加载可写。",
            ))
        if "SpawnPlacements" in text or "NaturalSpawner" in text:
            if not any(key in text for key in ("spawnCap", "mobCap", "canSpawn", "checkSpawnRules")):
                rows.append(finding(
                    "P2-10", f"{java_class.name} 自定义生成没有检查每区块/全局上限",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="按类别检查 spawn cap，给每 tick 生成设置预算。",
                ))
        if "new Random(" in text:
            rows.append(finding(
                "P2-08", f"{java_class.name} 使用 new Random()，同种子结果不一致",
                path=rel, line=java_class.line, severity="warning",
            ))
    return rows


def _java(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            if "getChunk(" in body and "setBlock(" in body:
                if not any(key in body for key in ("hasChunk", "isLoaded", "getChunkSource", "ensureCanWrite")):
                    rows.append(finding(
                        "P2-03", f"{java_class.name}.{method.name} 拿到区块后直接写入，未校验可写性",
                        path=rel, line=method.line, severity="warning",
                        suggestion="临时/不可写区块写入会被静默丢弃；写前校验 hasChunk/isLoaded。",
                    ))
            if "changeDimension" in body or "teleportTo(" in body:
                if "getPassengers" in text and "ejectPassengers" not in text:
                    rows.append(finding(
                        "P2-07", f"{java_class.name} 传送时没有处理乘客/坐骑",
                        path=rel, line=method.line, severity="warning",
                        suggestion="传送前 ejectPassengers，并按类型过滤不可传送实体；保持事件顺序。",
                    ))
            if "setDayTime" in body or "setTimeOfDay" in body:
                rows.append(finding(
                    "P2-06", f"{java_class.name} 修改全局时间，可能污染其他维度",
                    path=rel, line=method.line, severity="warning",
                    suggestion="自定义维度使用独立时间源/月相，避免直接改主世界时间。",
                ))
            if "for (" in body and "setChanged()" in body and any(
                key in body for key in ("getBlockEntities", "getAllBlockEntities", "for (BlockEntity")
            ):
                rows.append(finding(
                    "P2-02", f"{java_class.name}.{method.name} 批量保存方块实体，可能阻塞主线程",
                    path=rel, line=method.line, severity="warning",
                    suggestion="分批/延迟保存，限制每 tick 的数量。",
                ))
            if "pickupBlock" in body or "placeLiquid" in body:
                if "shrink(" not in body and "setItem(" not in body and "consume" not in body:
                    rows.append(finding(
                        "P2-42", f"{java_class.name}.{method.name} 桶交互可能没有消耗/回填容器",
                        path=rel, line=method.line, severity="warning",
                        suggestion="检查桶物品的消耗与回填，处理创造模式与堆叠。",
                    ))
            if ("Hopper" in java_class.name or "Container" in text) and "mayPlace" not in text and "canTakeItem" not in text:
                rows.append(finding(
                    "P2-43", f"{java_class.name} 容器转移没有实现 mayPlace/canTakeItem 约束",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="按槽位/物品标签限制输入输出，并做服务端校验。",
                ))
        if "extends Block" in text and "getPistonPushReaction" not in text and "PistonPushReaction" not in text:
            rows.append(finding(
                "P2-41", f"{java_class.name} 没有声明活塞推拉行为",
                path=rel, line=java_class.line, severity="info",
                suggestion="明确 getPistonPushReaction，并测试 BlockEntity/NBT 在推拉后的完整性。",
            ))
        if "setBlock(" in text and "UPDATE_LIGHT" in text and "updateNeighborsAt" not in text:
            rows.append(finding(
                "P2-11", "修改光源方块后没有触发光照更新",
                path=rel, line=java_class.line, severity="warning",
                suggestion="移除/放置光源后调用 updateNeighborsAt 或使用正确的 Block.UPDATE_LIGHT。",
            ))
        if "getBrightness" in text and "setBlock" in text and "setLight" in text and "distance" not in text:
            rows.append(finding(
                "P2-11", "动态光源没有距离衰减/节流",
                path=rel, line=java_class.line, severity="info",
                suggestion="按距离衰减并限制每 tick 更新数量。",
            ))
        if "RandomSource" in text or "level.random" in text:
            pass
        if "new Random()" in text:
            rows.append(finding(
                "P2-08", f"{java_class.name} 使用 new Random()，没有绑定世界种子",
                path=rel, line=java_class.line, severity="warning",
            ))
        if "LevelChunk" in text and "static" in text:
            rows.append(finding(
                "P2-01", f"{java_class.name} 静态引用 LevelChunk，可能阻止区块卸载",
                path=rel, line=java_class.line,
                suggestion="不要在静态集合中保存区块；使用位置/弱引用。",
            ))
    return rows


def _loot(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.json_files("src/main/resources/data/**/loot_table*/**/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        pools = data.get("pools")
        if not isinstance(pools, list):
            continue
        rel = ctx.rel(path)
        for pool in pools:
            entries = pool.get("entries") or []
            for entry in entries:
                functions = entry.get("functions") or []
                names = [str(item.get("function") or "") for item in functions if isinstance(item, dict)]
                if "minecraft:set_count" in names and "minecraft:copy_nbt" in names:
                    if names.index("minecraft:set_count") < names.index("minecraft:copy_nbt"):
                        rows.append(finding(
                            "P2-46", "战利品函数顺序错误：set_count 在 copy_nbt 之前，数量会被覆盖",
                            path=rel,
                            suggestion="先 copy_nbt/copy_name，再 set_count。",
                        ))
                if "minecraft:enchant_randomly" in names and "minecraft:set_count" in names:
                    rows.append(finding(
                        "P2-46", "战利品同时使用 enchant_randomly 与 set_count，附魔分支可能被覆盖",
                        path=rel, severity="warning",
                        suggestion="用 set_count 的 add 模式，或拆成独立池。",
                    ))
                for item in functions:
                    if not isinstance(item, dict):
                        continue
                    condition = str(item.get("conditions") or "")
                    if "silk_touch" in condition and "inverted" not in condition and "silk" in rel.lower():
                        rows.append(finding(
                            "P2-46", "精准采集条件可能写反",
                            path=rel, severity="warning",
                            suggestion="确认 match_tool 的 predicate 与 inverted 组合。",
                        ))
    return rows


def _uses(ctx: ProjectContext, *tokens: str) -> bool:
    text = _all_text(ctx)
    return any(token in text for token in tokens)


def _all_text(ctx: ProjectContext) -> str:
    return "\n".join(java.text for java in ctx.java_files)
