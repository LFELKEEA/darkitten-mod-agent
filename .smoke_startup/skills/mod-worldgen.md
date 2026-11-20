---
id: mod-worldgen
name: 世界生成
description: 世界生成：configured/placed feature、biome modifier、维度 JSON、datapack 结构与 noise settings，Forge BiomeModifier 与 Fabric biome API。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 世界生成

## 层次（从数据到游戏）
`biome → placed feature → configured feature → feature（生成逻辑）`，再加 `noise settings`（维度地形）、`dimension`（维度 JSON）、`structure`（数据包结构）。逐层是“什么生成在哪儿、多大密度、什么形状”。

## Feature 三层（1.20.1 数据驱动）
- **ConfiguredFeature**：`data/<ns>/worldgen/configured_feature/<name>.json`，指定 `type`（如 `minecraft:ore`、`minecraft:tree`、自定义 feature 类）+ `config`（矿的 `targets`、树的结构）。
- **PlacedFeature**：`data/<ns>/worldgen/placed_feature/<name>.json`，`feature` 引用 configured，`placement` 数组指定 `count`/`rarity`/`height` 过滤器等。
- 自定义 `Feature` 类：`Feature<FC>` + `Codec`，在 `DeferredRegister`（`ForgeRegistries.FEATURES`）注册，JSON 里 `type: "modid:name"` 引用。

## 1.20.1 Forge：BiomeModifier
- 用 `BiomeModifier` 把 placed feature 加进生物群系，而不是直接改 biome JSON。
- 常用 `ForgeBiomeModifiers.AddFeaturesBiomeModifier`，JSON 在 `data/<ns>/forge/biome_modifier/<name>.json`：
```json
{
  "type": "forge:add_features",
  "biomes": "#minecraft:is_overworld",
  "features": "modid:ore_example",
  "step": "underground_ores"
}
```
- `step` 决定生成阶段（`raw_generation`/`underground_ores`/`vegetal_decoration`/`top_layer_modification` 等），错阶段会不生成。
- `biomes` 用 biome 标签（`#`）或单个 biome id；想加进所有生物群系用 `#is_overworld` 这类原版标签。

## Fabric：Biome Modification API
- 用 `fabric-biome-api-v1`，`BiomeModifications.addFeature(BiomeSelectors.foundInOverworld(), GenerationStep.Feature.UNDERGROUND_ORES, registryKey)`。
- `BiomeSelectors`：`foundInOverworld()`/`foundInTheNether()`/`tag(...)`/`includeByKey(...)`。
- `GenerationStep.Feature.*` 对应 Forge 的 `step`。
- 需在 `fabric.mod.json` 声明 `fabric-biome-api-v1` 依赖，并在 entrypoint 里执行。

## 维度 JSON
- `data/<ns>/dimension/<name>.json`：`type`（`minecraft:overworld` 或自定义 `DimensionType`）、`generator`（`type: minecraft:noise` + `settings` 引用 noise settings + `biome_source`）。
- 自定义维度先要有 `DimensionType`（JSON）与 `noise_settings`，再在 JSON 里引用；跨维度传送用 `ServerPlayer.changeDimension` / `teleportTo`，标【⚠️人工复核】。

## Noise settings（地形）
- `data/<ns>/worldgen/noise_settings/<name>.json`：`noise_router`（各层噪声）、`sea_level`、`default_block`/`default_fluid`、`surface_rule` 等。
- 改地形是高风险操作，逐字段核对原版样例，不凭记忆写 `noise_router`；改错直接崩地形或白屏。

## Structure（数据包结构）
- 结构有 `.nbt`（用结构方块导出）+ `structure`/`structure_set` JSON（`data/<ns>/worldgen/structure/` 与 `structure_set/`）+ 模板池（`template_pool`，用于 jigsaw）。
- 结构方块或 `/place` 导出 `.nbt`，放 `data/<ns>/structures/<name>.nbt`。
- `structure` JSON 的 `biomes`、`step`、`spawn_overrides`、`type`（`jigsaw` 需要 `start_pool`）都要配全；jigsaw 结构最容易漏 `template_pool`。

## 验证
- 用 `/locate structure <ns>:<name>`、`/locate biome` 验证存在；`/place feature <ns>:<name>` 直接测试 feature。
- 新世界才生成，旧区块不刷新；测 worldgen 要开新世界或删区块。
- 生成密度、矿石埋深、生物群系过滤都要在游戏里实际看，不能只看 JSON 没报错。
- 维度、地形、跨维度传送、生物群系修改一律标【⚠️人工复核】。
