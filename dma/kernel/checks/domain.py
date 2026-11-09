"""Item / block / entity / command / event / config / lifecycle checks."""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

MUTATORS = (
    "setCount(", "shrink(", "grow(", "setTag(", "getOrCreateTag(",
    "setDamageValue(", "setHoverName(", "addEnchantment(", "set(",
)
CONSUMERS = ("shrink(", "setCount(0", "consume(", "hurtAndBreak(")
RETURNS_PASS = ("InteractionResult.PASS", "ActionResult.PASS", "InteractionResult.FAIL", "ActionResult.FAIL", "return false", "return true")


def _line(text: str, index: int) -> int:
    return text.count("\n", 0, max(0, index)) + 1


def _has_any(text: str, values: Iterable[str]) -> bool:
    return any(value in text for value in values)


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_items(ctx))
    rows.extend(_blocks(ctx))
    rows.extend(_entities(ctx))
    rows.extend(_commands(ctx))
    rows.extend(_events(ctx))
    rows.extend(_config(ctx))
    rows.extend(_lifecycle(ctx))
    return rows


# ----------------------------------------------------------------------
# Items
# ----------------------------------------------------------------------

def _items(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            is_interaction = (
                method.name in {"use", "useOn", "interact", "interactItem", "interactBlock", "onItemUse"}
                or "InteractionResult" in method.return_type
                or "ActionResult" in method.return_type
            )
            if is_interaction:
                consumed = _has_any(body, CONSUMERS)
                returns_pass = _has_any(body, RETURNS_PASS)
                if consumed and returns_pass:
                    rows.append(finding(
                        "P1-27",
                        f"{java_class.name}.{method.name} 已消耗物品/耐久，却返回 PASS/FAIL",
                        path=rel, line=method.line,
                        suggestion="成功消耗返回 SUCCESS/SUCCESS_SERVER；只改状态不消耗用 CONSUME；失败返回 FAIL。",
                    ))
                if "return true;" in body and "InteractionResult" in method.return_type:
                    rows.append(finding(
                        "P1-27", f"{method.name} 返回 boolean，物品交互应返回 InteractionResult",
                        path=rel, line=method.line, severity="warning",
                        suggestion="统一返回 InteractionResult，避免双触发/逻辑错乱。",
                    ))
            stack_params = [name for param_type, name in method.params if "ItemStack" in param_type]
            if stack_params:
                for param in stack_params:
                    mutation = next(
                        (token for token in MUTATORS
                         if re.search(rf"\b{re.escape(param)}\s*\.\s*{re.escape(token[:-1])}\s*\(", body)),
                        "",
                    )
                    if mutation and f"{param}.copy()" not in body and f"{param} = {param}.copy()" not in body:
                        rows.append(finding(
                            "P1-28",
                            f"{java_class.name}.{method.name} 直接修改传入的 ItemStack `{param}`",
                            path=rel, line=method.line,
                            suggestion="修改前先 copy()；只读可共享，写入必须写入副本，避免跨事件篡改。",
                        ))
                        break
        text = java.text
        if "getMaxStackSize" in text or "maxStackSize" in text:
            if "canStack" not in text and "isStackable" not in text and "isSameItemSameTags" not in text:
                rows.append(finding(
                    "P1-29", f"{java_class.name} 自定义了堆叠上限但没有实现 canStack/isStackable",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="重写 canStack 并校验组件/NBT，避免不同数据强行合并。",
                ))
        if "Container" in java.text or "Inventory" in java.text:
            if "getContainerSize" in text and "return 0" in text:
                rows.append(finding(
                    "P1-29", f"{java_class.name} 的容器大小为 0，槽位访问会越界",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="至少返回 1，并校验 slot 范围。",
                ))
            if ".getItem(" in text and not any(key in text for key in ("isEmpty()", "ItemStack.EMPTY")):
                rows.append(finding(
                    "P1-29", f"{java_class.name} 的容器取值未见空栈保护",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="getItem 可能返回 EMPTY；数量/槽位修改前校验空栈与边界。",
                ))
    return rows


# ----------------------------------------------------------------------
# Blocks
# ----------------------------------------------------------------------

def _blocks(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    block_names: list[str] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        is_block = (
            "extends Block" in text
            or "extends BaseEntityBlock" in text
            or "extends HorizontalDirectionalBlock" in text
            or "extends BlockBehaviour" in text
        )
        is_be = "BlockEntity" in java_class.extends or (java_class.extends or "").endswith("BlockEntity")
        if is_block:
            block_names.append(java_class.name)
            if "requiresCorrectToolForDrops" in text:
                tags = _tag_texts(ctx)
                if not any("needs_" in value or "mineable" in value for value in tags):
                    rows.append(finding(
                        "P1-30", f"{java_class.name} 要求正确工具，但项目里没有 mineable/needs_*_tool 标签",
                        path=rel, line=java_class.line,
                        suggestion="补 data/minecraft/tags/block/mineable/*.json 与 needs_*_tool.json，否则永远不掉落。",
                    ))
            if "getDestroySpeed" in text and "getDestroyProgress" in text:
                rows.append(finding(
                    "P1-30", "同时重写 getDestroySpeed 与 getDestroyProgress，破坏速度可能重复计算",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="只保留一个入口，并保证客户端/服务端一致。",
                ))
            if "createBlockStateDefinition" in text:
                definition = text.split("createBlockStateDefinition", 1)[1][:1500]
                property_count = definition.count("Property<") + definition.count(".property(")
                if property_count > 5:
                    rows.append(finding(
                        "P1-31", f"{java_class.name} 的 BlockState 属性数量约 {property_count}，状态会指数爆炸",
                        path=rel, line=java_class.line, severity="warning",
                        suggestion="合并布尔属性为枚举，或把可变数据放进 BlockEntity。",
                    ))
            has_shape = "getShape(" in text
            has_collision = "getCollisionShape(" in text
            if has_shape != has_collision:
                rows.append(finding(
                    "P1-31", f"{java_class.name} 只重写了渲染盒或碰撞盒之一，两者可能不一致",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="同时校验 getShape 与 getCollisionShape，避免视觉与碰撞错位。",
                ))
            if re.search(r"\.neighborChanged\s*\(|\.updateNeighborsAt\s*\(", text) and "setBlock" in text:
                guard = any(key in text for key in ("updating", "isUpdating", "guard", "ThreadLocal", "flag"))
                if not guard:
                    rows.append(finding(
                        "P2-40", f"{java_class.name} 在邻块更新中写方块，存在红石更新风暴风险",
                        path=rel, line=java_class.line, severity="warning",
                        suggestion="加更新抑制标记/递归守卫，避免无限 neighbor update。",
                    ))
        if is_be:
            field_names = {field.name for field in java_class.fields if "static" not in field.modifiers}
            for method in java_class.methods:
                body = method.body
                if not body or method.name in {"setChanged", "saveAdditional", "loadAdditional", "getUpdateTag"}:
                    continue
                assigned = {
                    name for name in field_names
                    if re.search(rf"\bthis\.{re.escape(name)}\s*=|(?<![\w.]){re.escape(name)}\s*=", body)
                }
                if assigned and "setChanged()" not in body:
                    rows.append(finding(
                        "P1-32", f"{java_class.name}.{method.name} 修改了 {', '.join(sorted(assigned)[:3])} 但没有 setChanged()",
                        path=rel, line=method.line, severity="warning",
                        suggestion="修改持久数据后调用 setChanged()；高频路径合并为延迟批量保存。",
                    ))
                if method.name in {"onRemove", "setRemoved"}:
                    if not any(key in body for key in ("shutdown", "close", "clear", "null", "release")):
                        rows.append(finding(
                            "P1-32", f"{java_class.name}.{method.name} 没有释放资源/清理引用",
                            path=rel, line=method.line, severity="info",
                            suggestion="取消任务、清空缓存、解除对其他对象的引用。",
                        ))
        # access-after-removal
        for match in re.finditer(r"(\w+)\s*=\s*\w+\.getBlockEntity\s*\(([^)]*)\)", java.masked):
            variable = match.group(1)
            tail = java.masked[match.end():match.end() + 500]
            if re.search(rf"\b{re.escape(variable)}\s*\.", tail) and "isRemoved()" not in tail and "== null" not in tail:
                rows.append(finding(
                    "P2-39", f"{variable} 来自 getBlockEntity，使用前未检查 isRemoved()/null",
                    path=rel, line=_line(java.masked, match.start()), severity="warning",
                    suggestion="方块实体可能已被移除；先校验 isRemoved()，异步回调里不要持有 BE 引用。",
                ))
    return rows


def _tag_texts(ctx: ProjectContext) -> list[str]:
    rows: list[str] = []
    root = ctx.root / "src" / "main" / "resources" / "data"
    if not root.is_dir():
        return rows
    for path in root.rglob("tags/**/*.json"):
        rows.append(ctx.read(path))
    return rows


# ----------------------------------------------------------------------
# Entities
# ----------------------------------------------------------------------

def _entities(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        extends = java_class.extends or ""
        if not (
            "Entity" in extends or "Mob" in extends or "Monster" in extends
            or "Animal" in extends or "PathfinderMob" in extends
        ):
            continue
        goals = re.findall(r"(?:goalSelector|targetSelector)\.addGoal\s*\(\s*(\d+)", text)
        duplicates = [value for value, count in Counter(goals).items() if count > 1]
        if duplicates:
            rows.append(finding(
                "P1-34", f"{java_class.name} 的 AI 目标优先级重复：{', '.join(duplicates)}",
                path=rel, line=java_class.line, severity="warning",
                suggestion="同一优先级会按注册顺序抢占；给每个目标唯一优先级并验证行为。",
            ))
        if ("Monster" in extends or "Mob" in extends) and "targetSelector" not in text:
            rows.append(finding(
                "P1-34", f"{java_class.name} 没有 targetSelector，敌对目标不会主动索敌",
                path=rel, line=java_class.line, severity="info",
                suggestion="按需求添加 NearestAttackableTargetGoal 等目标选择器。",
            ))
        has_read = "readAdditionalSaveData" in text or "readNbt" in text
        has_write = "addAdditionalSaveData" in text or "writeNbt" in text
        if has_read != has_write:
            rows.append(finding(
                "P2-35", f"{java_class.name} 的保存/读取方法不成对，卸载后会丢数据",
                path=rel, line=java_class.line,
                suggestion="同时实现 readAdditionalSaveData 与 addAdditionalSaveData，字段一一对应。",
            ))
        if "registerGoals" not in text and ("Mob" in extends or "Monster" in extends):
            rows.append(finding(
                "P1-34", f"{java_class.name} 没有 registerGoals，AI 不会初始化",
                path=rel, line=java_class.line, severity="warning",
                suggestion="在 registerGoals 中注册目标，并检查寻路节点类型。",
            ))
        if "getType()." in text and "clientTrackingRange" not in text and "EntityType.Builder" not in text:
            rows.append(finding(
                "P2-34", f"{java_class.name} 未声明追踪范围，可能使用默认过大/过小同步距离",
                path=rel, line=java_class.line, severity="info",
                suggestion="在 EntityType.Builder 上设置 clientTrackingRange 与 updateInterval。",
            ))
        if "removeWhenFarAway" not in text and "checkDespawn" not in text and "Mob" in extends:
            rows.append(finding(
                "P1-34", f"{java_class.name} 未处理 despawn，实体可能长期驻留",
                path=rel, line=java_class.line, severity="info",
                suggestion="明确 removeWhenFarAway/checkDespawn 规则，避免实体累积。",
            ))
        if "die(" in text:
            if not any(key in text for key in ("clear", "removeAllEffects", "setHealth(0", "discard")):
                rows.append(finding(
                    "P2-36", f"{java_class.name}.die 没有清理自定义状态，重生后可能残留幽灵数据",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="在 die/remove 中统一清理附件、冷却、临时目标。",
                ))
        if "getPassengers" in text or "getFirstPassenger" in text:
            if "positionRider" not in text and "getPassengerRidingPosition" not in text:
                rows.append(finding(
                    "P2-37", f"{java_class.name} 有乘客逻辑但没有 positionRider，乘客位置会错乱",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="实现 positionRider，并在下坐骑时清空输入/运动矢量。",
                ))
        if "setTarget(" in text and "setLastHurtByMob" not in text:
            rows.append(finding(
                "P2-38", f"{java_class.name} 的仇恨目标可能不会随受伤/时间清除",
                path=rel, line=java_class.line, severity="info",
                suggestion="在 hurt/die/距离超限时清理目标，服务端权威维护仇恨。",
            ))
        # attribute modifiers
        if "AttributeModifier" in text:
            if "removeModifier" not in text and "getModifiers" not in text:
                rows.append(finding(
                    "P1-33", f"{java_class.name} 添加 AttributeModifier 后没有移除/查询逻辑",
                    path=rel, line=java_class.line,
                    suggestion="修饰器必须绑定生命周期：死亡/换维度/效果结束时移除。",
                ))
            if "UUID.fromString" in text or "new UUID" in text:
                rows.append(finding(
                    "P1-33", f"{java_class.name} 硬编码 AttributeModifier UUID，重复添加会冲突",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="用 nameUUIDFromBytes 生成稳定 UUID，或使用带 ID 的修饰器 API。",
                ))
    return rows


# ----------------------------------------------------------------------
# Commands / advancements
# ----------------------------------------------------------------------

def _commands(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        if "Commands.literal" not in text and "CommandRegistrationCallback" not in text and "CommandDispatcher" not in text:
            continue
        if "hasPermission" not in text and "requires(" not in text and any(
            key in text for key in ("give", "setblock", "summon", "kill", "tp", "effect")
        ):
            rows.append(finding(
                "P1-37", f"{java_class.name} 的命令可能缺少权限检查",
                path=rel, line=java_class.line,
                suggestion="用 requires(source -> source.hasPermission(2)) 保护管理类命令。",
            ))
        if "sendSuccess" not in text and "sendFailure" not in text:
            rows.append(finding(
                "P1-37", f"{java_class.name} 的命令没有 sendSuccess/sendFailure，玩家看不到反馈",
                path=rel, line=java_class.line, severity="warning",
                suggestion="成功/失败都给出明确反馈，并区分玩家、命令方块、控制台。",
            ))
        if "getPlayerOrException" in text and "try" not in text:
            rows.append(finding(
                "P1-37", f"{java_class.name} 直接调用 getPlayerOrException，控制台执行会抛异常",
                path=rel, line=java_class.line, severity="warning",
                suggestion="先判断 source.getEntity() instanceof Player，或捕获异常并 sendFailure。",
            ))
        for match in re.finditer(
            r"(?:IntegerArgumentType\.integer|LongArgumentType\.longArg|FloatArgumentType\.floatArg|DoubleArgumentType\.doubleArg)\s*\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)",
            text,
        ):
            low, high = float(match.group(1)), float(match.group(2))
            if low > high:
                rows.append(finding(
                    "P1-37", f"命令参数范围写反：min={low} > max={high}",
                    path=rel, line=_line(text, match.start()),
                    suggestion="修正范围；参数解析失败应返回错误而不是崩溃。",
                ))
        if "scoreboard" in text.lower() or "Scoreboard" in text:
            if not re.search(r"['\"][a-z0-9_.]+:[a-z0-9_./-]+['\"]", text):
                rows.append(finding(
                    "P2-47", f"{java_class.name} 使用记分板/storage 但没有命名空间前缀",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="所有 objective/storage 名称加 modid 前缀，并在世界卸载时清理。",
                ))
        if "storage" in text.lower() and "version" not in text.lower():
            rows.append(finding(
                "P2-47", "命令 storage 数据没有版本标记，重载/升级后可能读到旧结构",
                path=rel, line=java_class.line, severity="info",
                suggestion="写入版本字段，读取时迁移或忽略未知版本。",
            ))

    # advancement JSON
    for path in ctx.json_files("src/main/resources/**/advancement*/**/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        criteria = data.get("criteria")
        if not isinstance(criteria, dict) or not criteria:
            rows.append(finding(
                "P1-38", "进度没有 criteria，永远不会解锁",
                path=ctx.rel(path),
                suggestion="至少提供一个可触发的 criterion，并在服务端校验。",
            ))
        elif any("impossible" in str(value) for value in criteria.values()):
            rows.append(finding(
                "P1-38", "进度 criteria 使用 impossible 触发器，永远无法完成",
                path=ctx.rel(path), severity="warning",
                suggestion="换成真实触发器，或删除该进度。",
            ))
        if "requirements" in data and isinstance(data["requirements"], list):
            if any(not group for group in data["requirements"]):
                rows.append(finding(
                    "P1-38", "进度 requirements 含空组，可能导致永不触发",
                    path=ctx.rel(path), severity="warning",
                    suggestion="删除空组或补齐条件。",
                ))
    return rows


# ----------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------

def _events(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        for method in java_class.methods:
            annotations = " ".join(method.annotations)
            if "SubscribeEvent" not in annotations and "EventHandler" not in annotations:
                continue
            if method.return_type not in {"void", ""}:
                rows.append(finding(
                    "P2-25", f"事件处理器 {method.name} 返回 {method.return_type}，事件总线只接受 void",
                    path=rel, line=method.line,
                    suggestion="改为 void；需要返回值时直接修改事件对象。",
                ))
            body = method.body
            if "setCanceled(true)" in body and "isCancelable" not in body and "Cancelable" not in body:
                rows.append(finding(
                    "P2-25", f"{method.name} 取消事件前没有检查可取消性",
                    path=rel, line=method.line, severity="warning",
                    suggestion="部分事件不可取消；先判断 isCancelable()，避免运行时异常。",
                ))
            if "priority" not in annotations and "EventPriority" not in annotations:
                rows.append(finding(
                    "P2-25", f"{method.name} 未声明事件优先级",
                    path=rel, line=method.line, severity="info",
                    suggestion="修改数据用 LOWEST/HIGH，观察用 HIGHEST，明确顺序语义。",
                ))
            if any(key in body for key in ("level.setBlock", "world.setBlock", "spawnEntity", "addFreshEntity")):
                if any(key in method.name for key in ("Break", "Place", "Block", "Entity")):
                    rows.append(finding(
                        "P2-26", f"{method.name} 在方块/实体事件中再次放置方块，可能递归触发自身",
                        path=rel, line=method.line, severity="warning",
                        suggestion="加递归守卫（ThreadLocal/标志位），或改用 setBlock 的 flags 跳过事件。",
                    ))
    return rows


# ----------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------

def _config(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        if "ModConfigSpec" not in text and "ForgeConfigSpec" not in text and "ConfigBuilder" not in text:
            continue
        for match in re.finditer(r"defineInRange\s*\(\s*[\"']([^\"']+)[\"'][^,]*,\s*([^,]+),\s*([^,]+),\s*([^,)]+)", java.masked):
            key, default, low, high = match.groups()
            try:
                low_value = float(low.strip())
                high_value = float(high.strip())
            except ValueError:
                continue
            if low_value > high_value:
                rows.append(finding(
                    "P1-25", f"配置项 {key} 范围写反：{low_value} > {high_value}",
                    path=rel, line=_line(java.masked, match.start()),
                    suggestion="修正 min/max，并给默认值加校验。",
                ))
            try:
                default_value = float(default.strip().rstrip("dDfFlL"))
                if not (low_value <= default_value <= high_value):
                    rows.append(finding(
                        "P1-25", f"配置项 {key} 默认值 {default_value} 超出范围 [{low_value}, {high_value}]",
                        path=rel, line=_line(java.masked, match.start()),
                    ))
            except ValueError:
                pass
        if re.search(r"\.define\s*\(\s*[\"'][^\"']+[\"']\s*,\s*-?\d", java.masked) and "defineInRange" not in text:
            rows.append(finding(
                "P1-25", f"{java_class.name} 的数值配置没有范围校验",
                path=rel, line=java_class.line, severity="warning",
                suggestion="用 defineInRange，避免玩家填负数/超大值。",
            ))
        if "client" in java_class.name.lower() and "server" in text.lower():
            rows.append(finding(
                "P1-25", f"{java_class.name} 混用客户端/服务端配置",
                path=rel, line=java_class.line, severity="info",
                suggestion="拆分为 client/server 配置，并标明是否需要重启。",
            ))
        if "ConfigScreen" not in text and "ConfigScreenProvider" not in text and "ClothConfig" not in text:
            rows.append(finding(
                "P1-26", f"{java_class.name} 没有配套配置界面",
                path=rel, line=java_class.line, severity="info",
                suggestion="按配置 schema 生成配置屏幕，并绑定翻译 key。",
            ))
    return rows


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------

def _lifecycle(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        if "ReloadListener" in text or "AddReloadListenerEvent" in text:
            if text.count("registerReloadListener") > 1 or text.count("addListener") > 1:
                rows.append(finding(
                    "P1-39", f"{java_class.name} 可能重复注册重载监听器",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="只注册一次；F3+T 后检查旧资源是否清理。",
                ))
            if "onResourceManagerReload" in text and not any(
                key in text for key in ("clear()", "reload()", "load()")
            ):
                rows.append(finding(
                    "P1-39", f"{java_class.name}.onResourceManagerReload 没有清理/重建状态",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="重载时先清空旧缓存，再重建；保证幂等。",
                ))
        if "Executors.new" in text and "shutdown" not in text and "close()" not in text:
            rows.append(finding(
                "P2-56", f"{java_class.name} 创建线程池但没有关闭逻辑",
                path=rel, line=java_class.line, severity="warning",
                suggestion="在服务停止/Mod 卸载时 shutdownNow，并等待任务结束。",
            ))
        for field in java_class.fields:
            if "static" in field.modifiers and any(
                key in field.initializer for key in ("BuiltInRegistries", "Registry.", "Minecraft.getInstance")
            ):
                rows.append(finding(
                    "P2-56", f"静态字段 {field.name} 在类加载阶段访问注册表/客户端，启动顺序不稳",
                    path=rel, line=field.line, severity="warning",
                    suggestion="延迟到初始化事件中赋值，或用 Supplier 惰性获取。",
                ))
        if "onInitialize" in text and "CompletableFuture" not in text and "ModInitializer" in text:
            rows.append(finding(
                "P2-56", "Fabric onInitialize 没有返回/等待异步依赖，可能在其他 Mod 就绪前访问其 API",
                path=rel, line=java_class.line, severity="info",
                suggestion="用依赖声明与就绪标记，避免早期访问未初始化对象。",
            ))
    return rows
