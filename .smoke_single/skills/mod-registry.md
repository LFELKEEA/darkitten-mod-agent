---
id: mod-registry
name: 注册中心模式
description: 注册中心模式：DeferredRegister / RegisterEvent 的 Forge/Fabric 双写法，物品/方块/实体/附魔/药水/创造标签页全注册表清单。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 注册中心模式

## 目标
所有内容（物品/方块/实体/方块实体/附魔/药水/创造标签页/菜单/声音/粒子/配方类型/战利品/维度/特征）通过统一注册中心注册，注册名与资源路径一一对应，禁止散落各处手写注册。

## Forge / NeoForge：DeferredRegister
```java
public static final DeferredRegister<Item> ITEMS = DeferredRegister.create(ForgeRegistries.ITEMS, MODID);
public static final RegistryObject<Item> EXAMPLE = ITEMS.register("example", () -> new Item(new Item.Properties()));
// @Mod 构造器里：ITEMS.register(modEventBus);
```
- 每个注册表一个 `DeferredRegister`，字段用 `public static final`，`RegistryObject<T>` 暴露引用。
- 按注册表类型取：`ForgeRegistries.BLOCKS / BLOCK_ENTITY_TYPES / ENTITY_TYPES / MOB_EFFECTS / POTIONS / CREATIVE_MODE_TABS / MENU_TYPES / SOUND_EVENTS / PARTICLE_TYPES / RECIPE_TYPES / LOOT_CONDITION_TYPES / LOOT_FUNCTION_TYPES / FEATURES / ...`。
- 在 `@Mod` 构造器里把每个 DeferredRegister 挂到 `modEventBus`；不要在主类里 new 内容对象。
- 需要额外处理（方块物品绑定、附魔合成、实体属性）用 `bus.addListener(...)` 或 `DeferredRegister` 的 `register` 回调，不要塞进构造器。

## Fabric：Registry.register
```java
public static final Item EXAMPLE = Registry.register(Registries.ITEM,
    new Identifier(MODID, "example"), new Item(new Item.Settings()));
```
- 在 `ModInitializer.onInitialize()` 里注册；用 `Registries.ITEM / BLOCK / ENTITY_TYPE / ENCHANTMENT / STATUS_EFFECT / ...`。
- 1.21+ 部分注册改用 `Registry.register(RegistryKey, ...)`，先核对当前 Yarn 映射。

## 全注册表清单
| 内容 | Forge 注册表 | Fabric 注册表 |
| --- | --- | --- |
| 物品 | `ForgeRegistries.ITEMS` | `Registries.ITEM` |
| 方块 | `ForgeRegistries.BLOCKS` | `Registries.BLOCK` |
| 方块实体 | `ForgeRegistries.BLOCK_ENTITY_TYPES` | `Registries.BLOCK_ENTITY_TYPE` |
| 实体 | `ForgeRegistries.ENTITY_TYPES` | `Registries.ENTITY_TYPE` |
| 附魔 | `ForgeRegistries.ENCHANTMENTS` | `Registries.ENCHANTMENT` |
| 药水效果 | `ForgeRegistries.MOB_EFFECTS` | `Registries.STATUS_EFFECT` |
| 药水 | `ForgeRegistries.POTIONS` | `Registries.POTION` |
| 创造标签页 | `ForgeRegistries.CREATIVE_MODE_TABS` | `Registries.ITEM_GROUP` |
| 菜单类型 | `ForgeRegistries.MENU_TYPES` | `Registries.SCREEN_HANDLER` |
| 声音事件 | `ForgeRegistries.SOUND_EVENTS` | `Registries.SOUND_EVENT` |
| 粒子类型 | `ForgeRegistries.PARTICLE_TYPES` | `Registries.PARTICLE_TYPE` |
| 配方类型 | `ForgeRegistries.RECIPE_TYPES` | `Registries.RECIPE_TYPE` |
| 战利品条件/函数 | `ForgeRegistries.LOOT_CONDITION_TYPES` / `LOOT_FUNCTION_TYPES` | `Registries.LOOT_CONDITION_TYPE` / `LOOT_FUNCTION_TYPE` |
| 维度/特征/生物群系 | 数据包 JSON + `Registries.*` | 数据包 JSON + `Registries.*` |

## 注册顺序与时机
- 内容对象创建要早、副作用（方块掉落、实体属性、附魔合成）要晚、游戏内事件最晚。
- 方块物品：方块注册后，物品注册时 `new BlockItem(block, props)` 引用方块对象。
- 附魔、药水等带参数的注册，先注册“类型”，再在 `FMLCommonSetupEvent` / `onInitialize` 后补参数。
- 不要用“注册表 id 数组 + for 循环下标”的脆弱方式映射内容；用常量引用。

## 资源绑定
- 每个注册名都有 `models/`、`textures/`、`lang/`、`blockstates/`（方块）、`spawn_eggs`/渲染器（实体）、`sounds.json`（声音）对应项，路径用同一 `modid:name`。
- 注册名只用小写 + 下划线，不用中文、空格、大写。

## 检查
- DeferredRegister 是否都挂到正确总线；`RegistryObject` 是否在注册前被取值（会 NPE/崩溃）。
- Fabric 是否都跑在 `onInitialize` 时机；专用服务端是否加载了客户端注册（如创造标签页图标渲染）。
