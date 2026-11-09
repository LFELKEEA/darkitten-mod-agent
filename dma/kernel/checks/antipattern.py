"""Memory, lifecycle and anti-pattern checks (P2-49..55 / P3-23..32)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

STATIC_HOLDERS = ("Level", "World", "Player", "Entity", "BlockEntity", "LevelChunk", "WorldChunk", "ServerPlayer")
TEMP_FIELDS = ("cooldown", "timer", "animation", "lastTick", "temp", "cache", "tickCounter", "lastUpdate", "progress")
HEAVY_COLLECTIONS = ("getAllEntities", "getEntities", "getPlayers", "players()", "getAllLevels", "getBlockEntities", "getEntitiesOfClass")
RESOURCE_TYPES = (
    "RenderTarget", "VertexBuffer", "BufferBuilder", "DynamicTexture", "Texture",
    "SoundInstance", "ShaderInstance", "FrameBuffer", "PoseStack", "BufferSource",
)
EVICTION_CALLS = ("remove(", "clear(", "evict", "invalidate", "prune", "WeakHashMap", "CacheBuilder", "Caffeine")
SCOPE_MARKERS = (
    "// client", "// server", "// global", "// per-world", "// perworld", "// per-session",
    "// shared", "@OnlyIn", "@Environment", "CLIENT", "SERVER", "GLOBAL", "WORLD", "SESSION",
)


def _resource_lifecycle(java, java_class, rel: str) -> list[Finding]:
    rows: list[Finding] = []
    text = java.text
    released = any(token in text for token in ("close()", "dispose()", ".release(", "closeQuietly"))
    for field in java_class.fields:
        if not any(kind in field.type for kind in RESOURCE_TYPES):
            continue
        if released:
            continue
        rows.append(finding(
            "P2-49", f"{java_class.name}.{field.name} 持有可释放资源，但没有 close/dispose 路径",
            path=rel, line=field.line, severity="warning",
            suggestion="把生命周期绑定到 Screen/Level/服务端关闭事件，并在卸载时释放。",
        ))
    for field in java_class.fields:
        if "static" not in field.modifiers:
            continue
        if not field.type.startswith(("Map<", "List<", "Set<", "ConcurrentHashMap", "Cache<")):
            continue
        if any(token in text for token in EVICTION_CALLS):
            continue
        rows.append(finding(
            "P2-49", f"静态缓存 {field.name} 没有淘汰策略，长期运行会持续增长",
            path=rel, line=field.line, severity="info",
            suggestion="使用 LRU/软引用/WeakHashMap，或在世界卸载时清理。",
        ))
    return rows


def _static_scope(java_class, rel: str) -> list[Finding]:
    """P2-55: static fields must declare global/client/server/world scope."""
    rows: list[Finding] = []
    for field in java_class.fields:
        if "static" not in field.modifiers or "final" in field.modifiers:
            continue
        if field.type in {"int", "long", "float", "double", "boolean", "String", "byte", "short"}:
            continue
        marker = " ".join([field.name, field.type, field.initializer])
        if any(token.lower() in marker.lower() for token in SCOPE_MARKERS):
            continue
        rows.append(finding(
            "P2-55", f"静态可变字段 {field.name} 未标注作用域（全局/每客户端/每世界）",
            path=rel, line=field.line, severity="info",
            suggestion="用命名或注释标明 client/server/global/per-world，跨世界数据必须可清理。",
        ))
    return rows


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        # Empty catches (P3-23)
        for match in re.finditer(r"catch\s*\([^)]*\)\s*\{\s*\}", java.masked):
            rows.append(finding(
                "P3-23", "空 catch 吞掉异常，功能会无声失效",
                path=rel, line=java.masked.count("\n", 0, match.start()) + 1,
                suggestion="至少记录日志并保留上下文；可恢复异常要给出降级路径。",
            ))
        for match in re.finditer(r"\bSystem\.(out|err)\.print", java.masked):
            rows.append(finding(
                "P3-20", "使用 System.out/err 代替 Mod logger",
                path=rel, line=java.masked.count("\n", 0, match.start()) + 1, severity="warning",
                suggestion="使用 mod logger，并附带玩家/维度/版本等结构化上下文。",
            ))
        # Static references to world objects
        for field in java_class.fields:
            if "static" not in field.modifiers:
                continue
            if any(holder in field.type for holder in STATIC_HOLDERS):
                rows.append(finding(
                    "P2-01", f"静态字段 {field.name} 持有世界/区块/实体引用，卸载后会泄漏",
                    path=rel, line=field.line,
                    suggestion="改用 WeakReference/按维度清理，或在卸载钩子中移除。",
                ))
            if any(field.type.startswith(prefix) for prefix in ("Map<", "List<", "Set<")) and any(
                holder in field.type for holder in STATIC_HOLDERS
            ):
                rows.append(finding(
                    "P2-01", f"静态集合 {field.name} 缓存世界对象，必须做淘汰",
                    path=rel, line=field.line,
                    suggestion="使用 LRU/软引用，并在区块卸载时清理。",
                ))
            if "final" in field.modifiers and field.type.startswith(("Map<", "List<", "Set<", "ConcurrentHashMap")):
                if "Map.of(" not in field.initializer and "List.of(" not in field.initializer and "Set.of(" not in field.initializer:
                    rows.append(finding(
                        "P3-32", f"static final 可变集合 {field.name} 在热重载后不会重置",
                        path=rel, line=field.line, severity="info",
                        suggestion="提供 reload/clear 入口，或改为实例级状态。",
                    ))
        # Constructor accessing world/registries (P2-52)
        for method in java_class.methods:
            if method.is_constructor:
                if any(key in method.body for key in ("getLevel()", "this.level", "BuiltInRegistries", "Minecraft.getInstance()", "getServer()")):
                    rows.append(finding(
                        "P2-52", f"构造器 {java_class.name} 访问世界/注册表，启动时序不稳",
                        path=rel, line=method.line,
                        suggestion="构造阶段只保存依赖，延迟到初始化事件/首次 tick。",
                    ))
            if method.name.startswith(("get", "is", "has")) and len(method.params) == 0:
                if any(key in method.body for key in ("setChanged()", ".save(", ".send(", "remove(", "setBlock(")):
                    rows.append(finding(
                        "P2-51", f"getter {method.name} 内修改状态/触发同步",
                        path=rel, line=method.line, severity="warning",
                        suggestion="getter 保持只读；写操作移到显式方法或 tick 中。",
                    ))
            if method.name in {"tick", "onTick", "serverTick"}:
                if any(key in method.body for key in HEAVY_COLLECTIONS):
                    filtered = any(
                        key in method.body
                        for key in ("distanceToSqr", "distanceTo", "getEntitiesOfClass", "AABB", "boundingBox", "filter", "predicate")
                    )
                    if not filtered:
                        rows.append(finding(
                            "P2-50", f"{java_class.name}.{method.name} 每 tick 全量遍历，没有距离/标签过滤",
                            path=rel, line=method.line,
                            suggestion="按 AABB/距离/标签过滤，或改为事件驱动。",
                        ))
                if "setBlock" in method.body and "Random" in text:
                    rows.append(finding(
                        "P2-54", f"{java_class.name} 持续 tick 并写方块，考虑改为随机 tick/状态驱动",
                        path=rel, line=method.line, severity="info",
                        suggestion="用 randomTick 或激活式 tick，降低服务器负担。",
                    ))
            if "saveAdditional" in method.name or "writeNbt" in method.name:
                for field_name in TEMP_FIELDS:
                    if re.search(rf"put\w*\(\s*\"[^\"]*{field_name}[^\"]*\"", method.body, re.IGNORECASE):
                        rows.append(finding(
                            "P2-53", f"临时状态 `{field_name}` 被写入持久 NBT",
                            path=rel, line=method.line, severity="warning",
                            suggestion="临时量不落盘；只持久化需要跨存档恢复的字段。",
                        ))
        rows.extend(_resource_lifecycle(java, java_class, rel))
        rows.extend(_static_scope(java_class, rel))
        # BlockEntity tick necessity
        if "TickableBlockEntity" in (java_class.implements or []) or (
            "BlockEntity" in (java_class.extends or "") and any(m.name == "tick" for m in java_class.methods)
        ):
            tick = next((m for m in java_class.methods if m.name == "tick"), None)
            if tick is not None and "if (" not in tick.body:
                rows.append(finding(
                    "P2-54", f"{java_class.name} 无条件 tick，可能导致空转",
                    path=rel, line=tick.line, severity="info",
                    suggestion="只在有状态时激活 tick，或使用 scheduled tick。",
                ))
        # File sandbox (P3-43)
        for match in re.finditer(r"(?:new\s+File|Paths?\.get)\s*\(\s*[\"']((?:[A-Za-z]:[\\/]|/)[^\"']*)", java.masked):
            rows.append(finding(
                "P3-43", f"直接使用绝对路径：{match.group(1)[:80]}",
                path=rel, line=java.masked.count("\n", 0, match.start()) + 1,
                suggestion="路径限制在游戏目录内（FMLPaths/GAMEDIR），拒绝 ../ 与系统目录。",
            ))
        # Command injection (P3-41)
        if any(key in text for key in ("performCommand", "CommandDispatcher", "execute(")) and re.search(
            r"performCommand\s*\(\s*\w+\s*\+", java.masked
        ):
            rows.append(finding(
                "P3-41", "命令字符串由变量拼接，存在注入风险",
                path=rel, line=java_class.line,
                suggestion="不要拼接命令；用参数化 API 或严格白名单校验。",
            ))
        # Script sandbox (P3-44)
        if any(key in text for key in ("ScriptEngineManager", "GroovyShell", "Nashorn", "eval(")):
            rows.append(finding(
                "P3-44", "存在脚本执行入口，需要配额与沙箱",
                path=rel, line=java_class.line, severity="warning",
                suggestion="限制执行时间/指令数/可访问类，默认关闭脚本功能。",
            ))
        # Version comparator (P3-29)
        if re.search(r"(?:compareTo|isNewer|isHigher)\s*\([^)]*\)[^{]*\{", java.masked) and 'split(".")' in text:
            rows.append(finding(
                "P3-29", "手写版本比较器（split('.')），快照/beta 判断容易出错",
                path=rel, line=java_class.line, severity="warning",
                suggestion="使用 loader 提供的 Version/VersionPredicate 比较器。",
            ))
        # Capability exposure (P3-42)
        if "Capability" in text or "AttachmentType" in text:
            if "getCapability" in text and "public" in text and "ReadOnly" not in text:
                rows.append(finding(
                    "P3-42", "能力/附件暴露给其他 Mod，但没有只读标记或访问控制",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="提供只读视图/白名单，并对敏感写入做审计。",
                ))
    # mcfunction recursion
    for path in Path(ctx.root).rglob("*.mcfunction"):
        rel = ctx.rel(path)
        text = ctx.read(path)
        name = path.stem
        if re.search(rf"\bfunction\s+[^\s]*{re.escape(name)}\b", text):
            rows.append(finding(
                "P2-48", f"mcfunction `{rel}` 递归调用自身",
                path=rel,
                suggestion="加递归深度/执行次数上限，tick 函数必须节流。",
            ))
        if "tick" in path.name.lower() and "schedule" not in text and len(text.splitlines()) > 20:
            rows.append(finding(
                "P2-48", f"tick 函数 `{rel}` 行数较多且没有节流",
                path=rel, severity="info",
                suggestion="拆分为状态机/计分板计时，避免每 tick 全量执行。",
            ))
    return rows
