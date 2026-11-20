---
id: mod-entity
name: 实体开发
description: 实体开发：EntityType+Entity 类、属性、渲染器/模型层、AI goal、生成蛋与战利品，Forge/Fabric 差异。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 实体开发

## 组成
`EntityType` + `Entity` 子类 + 注册 + 属性（Attribute）+ 渲染器（Renderer/ModelLayer）+ AI（Goal）+ 生成蛋 + 战利品表。缺一样实体都“看不见/不会动/不掉东西”。

## 注册与 Entity 类
- Forge：`DeferredRegister.create(ForgeRegistries.ENTITY_TYPES, MODID)`，`EntityType.Builder.of(MyEntity::new, MobCategory.CREATURE).sized(w, h).build("modid:example")`。
- Fabric：`Registry.register(Registries.ENTITY_TYPE, id, FabricEntityTypeBuilder.create(SpawnGroup.CREATURE, MyEntity::new).dimensions(EntityDimensions.fixed(w, h)).build())`。
- `Entity` 子类构造器传 `EntityType` 和 `Level`；生物用 `Mob`/`PathfinderMob`，飞行用 `FlyingMob`，远程用 `Mob` + 投射物。
- `registerAttributes()`（Forge 生物）/ `createMobAttributes()`（Fabric）设置基础属性。

## 属性
- 生命 `MAX_HEALTH`、移动 `MOVEMENT_SPEED`、攻击 `ATTACK_DAMAGE`、护甲 `ARMOR`、跟随距离 `FOLLOW_RANGE`、击退抗性 `KNOCKBACK_RESISTANCE`。
- 用 `AttributeSupplier.Builder`；Forge 在 `EntityAttributeCreationEvent` 注册，Fabric 在 `DefaultAttributeRegistry.register`。

## 渲染器与模型层
- Forge：`EntityRenderers.register(type, MyRenderer::new)` 在 `FMLClientSetupEvent`；`ModelLayer` 用 `LayerDefinition.create(...)` 注册。
- Fabric：`EntityRendererRegistry.register(type, MyRenderer::new)` 在 `ClientModInitializer`；模型层 `EntityModelLayerRegistry.registerModelLayer(...)`。
- `Renderer` 指定纹理（`getTextureLocation`）、模型、阴影大小；缩放用 `scale`。
- 模型、纹理、渲染器、模型层四者注册名/路径必须一致，否则白模或透明。

## AI Goal
- 在 `registerGoals()` 里 `goalSelector.addGoal(priority, new Goal())`，优先级数字越小越先执行（0/1/2…）。
- 常用目标：`FloatGoal`、`MeleeAttackGoal`、`WaterAvoidingRandomStrollGoal`、`LookAtPlayerGoal`、`RandomLookAroundGoal`、`FollowOwnerGoal`、`PanicGoal`、`TemptGoal`。
- 自定义 AI 继承 `Goal`，实现 `canUse()`、`canContinueToUse()`、`tick()`；目标选择器（`targetSelector`）用 `NearestAttackableTargetGoal` 等。
- AI 只跑服务端；客户端只消费渲染，不要用 AI 驱动客户端表现。

## 生成蛋与自然生成
- 生成蛋：Forge `new SpawnEggItem(type, c1, c2, props)`，Fabric `new SpawnEggItem(type, c1, c2, settings)`，颜色给 16 进制。
- 自然生成：用生物群系修改（Forge `AddSpawnsBiomeModifier` / Fabric `BiomeModifications.addSpawn`），配置 `type`、`weight`、`minCount`/`maxCount`。
- 只加实体不加 spawn 配置，生物不会自然出现。

## 战利品
- `data/<ns>/loot_tables/entities/<entity_id>.json`；实体掉落由 `getLootTable` 返回表 id 或重写 `dropCustomDeathLoot` 自定义掉落。
- 避免重复掉落：原版掉落 + 自定义掉落都触发会掉两份。

## Forge / Fabric 差异速查
| 事项 | Forge 1.20.1 | Fabric |
| --- | --- | --- |
| 实体类型 | `EntityType.Builder` | `FabricEntityTypeBuilder` |
| 属性注册 | `EntityAttributeCreationEvent` | `DefaultAttributeRegistry` |
| 渲染器 | `EntityRenderers.register` | `EntityRendererRegistry.register` |
| 生成蛋 | `new SpawnEggItem(type, c1, c2, props)` | `new SpawnEggItem(type, c1, c2, settings)` |
| 世界/玩家 | `Level` / `Player` / `ServerPlayer` | `World` / `PlayerEntity` / `ServerPlayerEntity` |

## 检查
- 客户端类（渲染器/模型）是否只进 client 入口；专用服务端能否启动。
- 碰撞箱、眼睛高度、骑乘位置、命名（`/summon` 用注册名）是否一致。
- 实体 AI、远程攻击投射物、生成蛋颜色、自然生成、跨版本映射标【⚠️人工复核】。
