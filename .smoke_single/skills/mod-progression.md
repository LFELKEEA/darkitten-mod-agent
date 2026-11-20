---
id: mod-progression
name: 进度配置标签页与 JEI
description: 进度系统、配置项、创造模式标签页与 JEI 兼容思路。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 进度 / 配置 / 创造标签页 / JEI

## 进度（Advancement）
目录 `data/<ns>/advancements/<category>/<name>.json`。结构：
```json
{
  "display": {
    "icon": {"item": "modid:example"},
    "title": {"translate": "advancements.modid.example.title"},
    "description": {"translate": "advancements.modid.example.desc"},
    "frame": "task",
    "background": "minecraft:textures/gui/advancements/backgrounds/stone.png",
    "show_toast": true, "announce_to_chat": true, "hidden": false
  },
  "parent": "modid:category/root",
  "criteria": {
    "got_item": {"trigger": "minecraft:inventory_changed", "conditions": {"items": [{"items": ["modid:example"]}]}}
  }
}
```
- 根进度（无 `parent`）要设 `background`；`frame` 用 `task`/`goal`/`challenge`。
- `criteria` 的 `trigger` 用原版触发器（`inventory_changed`、`entity_killed_player`、`tick`、`recipe_unlocked`、`location` 等）+ `conditions`；多条件用 `requirements` 数组控制 AND/OR。
- 语言键写进 `lang/en_us.json`、`zh_cn.json`。
- 进度树：根 → 子级用 `parent` 链起来；`hidden` 进度不预先显示，达成才出现。

## 配置项（Config）
- Forge 1.20.1：用 `ForgeConfigSpec`（`COMMON`/`CLIENT`/`SERVER` 三个 spec），构造器里 `Builder` 定义条目，`ModLoadingContext.get().registerConfig(...)` 挂载；`FMLPaths.CONFIGDIR` 定位配置目录。
- NeoForge：`ModConfigSpec` 替代 `ForgeConfigSpec`；`ModContainer.registerConfig`。
- Fabric：用 `fabric-loader` 的 `config` 或 Cloth Config / `AutoConfig`；`FabricLoader.getInstance().getConfigDir()` 定位。
- 配置值用 getter 包装，不要在运行中改 `ConfigSpec` 定义；布尔/整数/枚举/列表都支持。
- 服务器配置与客户端配置分开；同步型配置走网络包标【⚠️人工复核】。

## 创造模式标签页
- Forge：`DeferredRegister.create(ForgeRegistries.CREATIVE_MODE_TABS, MODID)`，注册 `CreativeModeTab`，`displayItems` 用 `output.accept(item)` 填充。
- 1.20.1 用 `CreativeModeTab.builder().title(...).icon(...).displayItems((params, output) -> {...}).build()`。
- Fabric：`Registries.ITEM_GROUP` + `ItemGroup.Builder` + `ItemGroupEvents.modifyEntriesEvent(...)`。
- 图标要注册名对应的物品纹理；自定义排序用 `output.accept` 顺序，不要靠名字排序。
- 客户端图标渲染别进服务端注册路径。

## JEI 兼容思路
- 显示自定义配方：给配方类型写 JEI 插件类（`@JeiPlugin` 或 `IModPlugin`），`registerRecipeCatalysts`/`registerRecipes` 把配方类型和合成台类型绑起来。
- 配方类别 `RecipeCategory` + `RecipeType`，渲染用 `IRecipeSlotDrawable`；复杂类别（多方块、自定义 GUI）标【⚠️人工复核】。
- `getRecipeType` 必须与注册的 `RecipeType` 一致，否则 JEI 不显示。
- 没有 JEI 依赖时先留扩展点，不把 JEI 类打进生产构建（用 `Optional` 或独立模块）。

## 检查
- 进度 criteria 的 trigger 名和条件字段拼写正确，语言键齐全；根进度有 background。
- 配置默认值合理、有注释；服务器/客户端配置不混淆。
- 标签页物品能正常显示图标；JEI 插件类只在 JEI 存在时加载。
