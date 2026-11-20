---
id: mod-recipe-loot
name: 配方与战利品表
description: 配方与战利品表：有序/无序/熔炼/锻造台/切石机配方 JSON 与战利品池/条件/函数全规范，自定义配方类型。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 配方与战利品表

## 配方 JSON（1.20.1 格式）
目录 `data/<ns>/recipes/<name>.json`。字段 `type`、`result`（1.20.1 用 `{"item": "...", "count": n}`，1.20.5+ 改 `{"id": ...}`）、`ingredients`/`key`。

### 有序（shaped）
```json
{
  "type": "minecraft:crafting_shaped",
  "pattern": ["AAA", "B B", "AAA"],
  "key": { "A": {"item": "minecraft:iron_ingot"}, "B": {"item": "minecraft:stick"} },
  "result": {"item": "modid:example", "count": 1}
}
```
- `pattern` 每行等长（空位用空格），最多 3×3；`key` 里字符→材料映射。
- 支持 tag 材料：`"A": {"tag": "minecraft:planks"}`，比写死 item 更稳。

### 无序（shapeless）
```json
{
  "type": "minecraft:crafting_shapeless",
  "ingredients": [{"item": "minecraft:diamond"}, {"tag": "minecraft:planks"}],
  "result": {"item": "modid:example", "count": 2}
}
```
- 无序不关心位置，`ingredients` 平铺数组。

### 熔炼 / 烟熏 / 高炉
```json
{
  "type": "minecraft:smelting",
  "ingredient": {"item": "modid:ore"},
  "result": "modid:ingot",
  "experience": 0.7,
  "cookingtime": 200
}
```
- `type` 用 `smelting` / `blasting` / `smoking` / `campfire_cooking`；`result` 是单 item 字符串（1.20.1），`cookingtime` 单位 tick（200=10秒）。

### 锻造台（smithing）
```json
{
  "type": "minecraft:smithing_transform",
  "template": {"item": "minecraft:netherite_upgrade_smithing_template"},
  "base": {"item": "minecraft:diamond_sword"},
  "addition": {"item": "minecraft:netherite_ingot"},
  "result": {"item": "minecraft:netherite_sword"}
}
```
- 1.20+ 分 `smithing_transform`（有模板）和 `smithing_trim`（盔甲纹饰）；1.19 及以前是 `smithing`（无 template 字段）。

### 切石机（stonecutting）
```json
{
  "type": "minecraft:stonecutting",
  "ingredient": {"item": "minecraft:stone"},
  "result": "modid:slab",
  "count": 2
}
```

## 自定义配方类型
1. 实现 `Recipe<Container>`（`matches`/`assemble`/`getSerializer`/`getType`）。
2. `RecipeSerializer`：实现 `fromJson` / `toNetwork` / `fromNetwork`，用 `Codec` 或手动读 JSON。
3. 注册：Forge `DeferredRegister.create(ForgeRegistries.RECIPE_SERIALIZERS/RECIPE_TYPES)`；Fabric `Registry.register(Registries.RECIPE_SERIALIZER/RECIPE_TYPE)`。
4. 配方 JSON 的 `type` 写自定义序列化器 id，如 `"type": "modid:my_recipe"`。
5. 复杂配方类型标【⚠️人工复核】，并给 JEI 兼容提示。

## 战利品表 JSON
目录 `data/<ns>/loot_tables/blocks/<name>.json`（方块）、`loot_tables/entities/<id>.json`（实体）、`loot_tables/chests/<name>.json`（战利品箱）。结构：
```json
{
  "type": "minecraft:block",
  "pools": [
    {
      "rolls": 1,
      "bonus_rolls": 0,
      "entries": [
        {"type": "minecraft:item", "name": "modid:example", "functions": [], "conditions": []}
      ],
      "conditions": []
    }
  ]
}
```
- **pools**：多个池独立掷骰；`rolls` 掷的次数，`bonus_rolls` 每级幸运加的次数。
- **entries**：`item` / `tag` / `loot_table`（引用别的表）/ `empty` / `alternatives` / `group`；`weight` 权重、`quality` 受幸运影响。
- **functions**：`set_count`、`set_damage`、`enchant_randomly`、`set_nbt`、`looting_enchant`、`furnace_smelt`、`set_name`、`explosion_decay`。
- **conditions**：`survives_explosion`、`killed_by_player`、`random_chance`、`match_tool`、`table_bonus`、`entity_properties`。
- 方块默认掉落由 `Block.getLootTable()` 决定；改方块掉落优先换战利品表路径，不要重写 `getDrops`。

## 常见坑
- 1.20.1 配方用 `{"item":"...","count":n}`，1.20.5+ 用 `{"id":"..."}`；混用直接解析失败。
- 战利品条件不满足时整条 entry 不落，不是“落一半”。
- `explosion_decay` 让方块被炸掉时按概率掉落，矿石类要加。
- 配方解锁：`data/<ns>/advancements/recipes/...` 的 `criteria` 决定玩家何时学会配方；漏掉则知识书里不显示。
