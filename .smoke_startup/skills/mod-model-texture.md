---
id: mod-model-texture
name: 物品方块实体模型规范
description: 物品/方块/实体 JSON 模型规范：parents、display、elements、rotations、blockstates、cuboid UV，16/32 像素纹理与 Blockbench 导出。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 物品 / 方块 / 实体模型与纹理规范

## 模型类型速查
- **物品模型**：`assets/<modid>/models/item/<id>.json`，扁平图标用 `parent: "item/generated"` + `layer0`；手持 3D 用 `parent` + `display` + `elements`。
- **方块模型**：`assets/<modid>/models/block/<id>.json`（16×16×16 元素盒）+ `assets/<modid>/blockstates/<id>.json`（variants / multipart）。
- **实体模型**：Java `EntityModel<T>` + `ModelPart`（原版）或 GeckoLib `.geo.json`。

## 物品模型 JSON 要点
```json
{
  "parent": "item/generated",
  "textures": { "layer0": "modid:item/example" }
}
```
- `parent` 可继承原版（`item/generated`、`item/handheld`、`block/cube_all` 等）或自己的父模型，省重复。
- `display` 键：`gui`、`ground`、`fixed`、`thirdperson_righthand`、`thirdperson_lefthand`、`firstperson_righthand`、`firstperson_lefthand`；`rotation`/`translation`/`scale` 控制手持姿势。
- 手持模型不要超出 16×16×16 太多，否则第三人称/第一人称穿模。

## 方块模型 JSON 要点
```json
{
  "parent": "block/cube_all",
  "textures": { "all": "modid:block/example" }
}
```
- `elements`：`from`/`to` 为 `[x,y,z]`（0–16），`faces` 每面 `uv: [u1,v1,u2,v2]` + `texture: "#0"`，按需 `cullface`、`rotation`、`tintindex`。
- `rotation`：`{origin: [x,y,z], axis: "x"|"y"|"z", angle: ±22.5|±45, rescale: bool}`；`rescale` 让旋转后贴图随盒缩放而不是被裁。
- 透明/半透明方块：`parent` 选 `cutout` 或 `translucent`（按版本），否则透明区域显示黑块。
- 光照/粒子：`textures` 里加 `"particle"` 键指定粒子贴图，避免掉落物粒子全黑。

## blockstates：variants 与 multipart
- **variants**：一个属性组合一个模型，如 `{"variants": {"facing=north": {"model": "modid:block/x"}, "facing=south": {"model": "modid:block/x", "y": 180}}}`；属性名必须与 BlockState 属性完全一致。
- **multipart**：按条件叠加多个部分（栅栏、红石、墙），`when` 条件 + `apply` 模型；适合有多个独立可开关部分的方块。
- 旋转用 `y`（绕 Y 轴 0/90/180/270）和 `x`；`uvlock: true` 让顶底面纹理随旋转锁定，避免朝向纹理乱转。

## 实体模型
- 原版：`ModelPart` 树 + `addBox(...)` 建盒，`setPos` 定枢轴，动画改 `setRotation`/`visible`；渲染器经 `RegisterRenderers`（Forge）/`EntityRenderers`（Fabric）注册，`ModelLayer` 定义层。
- GeckoLib：`.geo.json`（骨骼）、`animation.json`（动画）、`GeoModel`/`GeoEntityRenderer`；动画控制器只放客户端。

## 纹理规范
- **16×16**：物品、方块、简单实体，一像素一格；**32×32**：精细图标、GUI 元素、较大实体。
- 必须带透明通道（RGBA PNG）；物品主体占满 12×12 以上，地面/手中看得清。
- 方块纹理要四方连续，边缘颜色一致避免接缝；上下左右纹理编号对齐模型 `faces` 的 UV。
- 实体纹理按 UV 布局展开，区域不重叠、不越界、不拉伸；用 Blockbench 画布看 UV 对应关系。

## Blockbench 工作流
1. 新建 Java Block/Item 或 Modded Entity；方块单位设为 16，画布 UV 用 0–16 像素坐标（不是 0–1）。
2. 建元素盒拼轮廓（白模），再用 `pivot` 定旋转中心。
3. 分配 UV：每个面指定纹理区域，确保贴图分区对应。
4. 画贴图，或导出 UV 模板交给 `pixel_art` 手绘。
5. 导出：方块/物品选「Minecraft Java Block/Item」；实体选 GeckoLib 或原版格式。
6. **导出后必须人工核对 JSON**：Blockbench 偶发字段（`rotation` 的 angle、uv 顺序、`rescale`）与游戏实际读取不一致。

## 手写 JSON 要点（不用 Blockbench 时）
- `elements` 从 `from`（左下后角）到 `to`（右上前角），坐标 0–16 整数或 .5/.25 小数。
- 每个面 UV 顺序是 `[u1, v1, u2, v2]`（左下角到右上角），`texture` 引用 `#` 编号键。
- `cullface` 值：`down/up/north/south/west/east`，只有面向该方向才渲染，避免内面穿帮。
- 不要混用 1.21 的 `item_model` 语法和 1.20.1 的 `layer0`；以目标 MC 版本为准。

## 质量检查
- 无 z-fighting（共面）、无 UV 越界、纹理不拉伸、透明剔除正确。
- 物品 `display` 实际进游戏看手持角度；方块六面、掉落物、第三人称都验证一遍。
- 完整建模流程见 asset-3d-modeling。
