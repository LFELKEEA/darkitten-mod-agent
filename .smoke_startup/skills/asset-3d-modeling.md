---
id: asset-3d-modeling
name: 3D 模型与资产建模
description: 方块/物品/实体 3D 模型、Blockbench 流程、UV 与纹理、模型 JSON、GeckoLib 动画和游戏内预览。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 3D 模型与资产建模

## 适用
方块、物品、手持/穿戴、实体、方块实体和装饰件的模型与纹理；Blockbench 建模流程；GeckoLib 动画；3D 概念图与三视图。

## 先确认
模型类型（方块 / 物品 / 实体 / 方块实体 / 盔甲）、目标 MC 版本、加载器、尺寸基准、纹理分辨率、是否需要动画、是否使用 GeckoLib、是否已有贴图。缺关键项先问，不要猜。

## 流程
1. **参考**：正视、侧视、顶视、三视角，标出尺寸、比例和材质。
2. **白模**：只做体块，先定轮廓和比例，不急着画细节。
3. **低模**：Minecraft 用元素盒（elements）拼，不用高模；面数越少越稳。
4. **UV**：每个面用 0–16 的像素坐标，纹理分区不重叠、不越界。
5. **纹理**：16 / 32 / 64 像素，透明背景，像素对齐；方块/物品用 `texture_gen`，实体皮肤用 `entity_texture`（真实 UV 图集），`pixel_art` 只画小图标。
6. **导出**：Java 模型 JSON；GeckoLib 用 `.geo.json` + `animation.json`。
7. **入游戏**：blockstates / models/item 引用、渲染器注册、语言键。
8. **验证**：物品栏、地面、第三人称、方块六面、透明与剔除。

## 用 blockbench 工具建模（首选）
```tool
{"tool":"blockbench","action":"create","path":"src/main/resources/assets/modid/models/block/ruby_lamp.bbmodel","cubes":[{"name":"base","from":[0,0,0],"to":[16,4,16],"uv":[0,0]},{"name":"crystal","from":[4,4,4],"to":[12,14,12],"uv":[32,0]}],"size":[64,64]}
{"tool":"blockbench","action":"preview","path":"src/main/resources/assets/modid/models/block/ruby_lamp.bbmodel","texture":"src/main/resources/assets/modid/textures/block/ruby_lamp.png"}
{"tool":"blockbench","action":"export","path":"src/main/resources/assets/modid/models/block/ruby_lamp.bbmodel","texture":"src/main/resources/assets/modid/textures/block/ruby_lamp.png","namespace":"modid","kind":"block"}
```
- `cubes` 里 1 单位 = 1 像素，写 `from`/`to` 或 `from` + `size`；`uv` 是盒式 UV 原点，六个面按原版布局展开。
- 多个 cube 不要都写 `uv:[0,0]`，否则贴图互相覆盖；不写 `uv` 时每个面映射整张贴图，只适合单立方体方块。
- `preview` 输出 `<name>_preview.png`，会按 UV 采样真实贴图；`export` 同时写 Java 模型并复制 PNG。
- 手绘细节交给应用里的「绘图」页：可直接在 UV 图集、图标、方块和模型面上落笔，Shift 填整面、Ctrl+Shift 填整个立方体、支持左右镜像。

## Blockbench → JSON 导出规范
- 方块导出勾选「Texture Scale 16」；不勾会把 UV 写成 0–1 浮点，游戏读错。
- 元素盒的 `from`/`to` 保持整数或 .5/.25 小数，避免 15.9 这种导致缝隙。
- 旋转用 pivot 定中心：在 Blockbench 里把 pivot 移到旋转轴再设 rotation，导出才是 `origin` + `axis` + `angle`。
- 导出后检查：`textures` 键引用 `#0`；`particle` 键存在（掉落物粒子）；`cullface` 只在需要的面写；`display` 各姿势不空。
- GeckoLib 实体导出用「Export → Blockbench Project / Geo」专用格式，不要用方块导出器导实体。

## 旋转与 UV 坑（重点）
- **angle 符号**：绕 Y 轴 `-45` 和 `45` 方向相反；Blockbench 预览与游戏渲染可能镜像，导出后以游戏实际为准，不要照搬预览。
- **rescale**：`rescale: true` 让旋转后贴图随盒缩放；`false` 时贴图保持原像素尺寸，旋转的盒会被裁边。斜向元素（45° 旋转的杆/棱）务必开 `rescale`。
- **UV 顺序**：每个面 `uv: [u1, v1, u2, v2]` 是左下角→右上角；写反（`[u2,v2,u1,v1]`）贴图镜像。
- **UV 越界**：UV 坐标超过纹理尺寸（如 16×16 纹理写 `[0,0,20,20]`）会拉伸或报错；导出前在纹理面板确认每个面都在画布内。
- **共面 z-fighting**：两个盒贴在同一平面会闪烁；错开 0.01 或合并成一个盒。
- **cullface 漏写/多写**：透明方块漏 `cullface` 会看到内面；实心方块多写会让相邻面被剔除露缝。
- **方块纹理连续**：需要四方连续的方块（草、矿石、地板），上下左右纹理边缘颜色一致，否则方块并排有明显接缝。

## Java 模型 JSON
- `textures`：`{"0":"modid:block/name","particle":"modid:block/name"}`。
- `elements`：`from` / `to` 为 [x,y,z]，范围 0–16；可带 `rotation {origin, axis, angle, rescale}`。
- `faces`：`uv` 为 [u1,v1,u2,v2]，`texture` 用 `#0`，按需写 `cullface`、`rotation`、`tintindex`。
- `display`：`gui`、`ground`、`fixed`、`thirdperson_righthand` 等变换；物品模型常用 `item/generated` + `layer0/1`。
- 方块状态 JSON 用 `variants` 或 `multipart`，属性名必须和 BlockState 完全一致。
- 方块模型默认 16×16×16；超出部分要确认游戏是否允许，不要靠缩放硬塞。

## 实体模型
- 原版：`EntityModel<T>` + `ModelPart`，按状态调 `setRotation` / `visible`；渲染器通过 `EntityRenderers`（Fabric）或 `RegisterRenderers`（NeoForge）注册。
- GeckoLib：`GeoModel` / `GeoEntityRenderer` / `GeoAnimatable`，动画控制器只放客户端。
- 模型 pivot、碰撞箱、眼睛高度、骑乘位置要一起核对，不要只对齐视觉。

## 质量检查
- 无 z-fighting（面不共面）、无反面剔除错误、纹理不拉伸、UV 不越界。
- 方块 `cullface` 正确，避免相邻方块露缝；透明纹理按版本选 cutout / translucent。
- 物品 `display` 变换不要让模型出框；手持角度在游戏里实际看一遍。
- 动画骨骼命名稳定，不要用中文或空格。

## 3D 概念图提示词（给图像工具用）
> 四格视图：正面 / 侧面 / 顶面 / 三视角；纯色背景；无阴影；标注主色与材质；等距视角；方块化、像素化；16:9。

工坊不能直接生成 `.obj` / `.fbx`；Minecraft 用 JSON 模型。需要外部 3D 渲染图时，把上面的提示词交给图像工具，不要假装已经生成模型文件。

## 交付
模型 JSON 路径 + 完整内容；纹理规格或 `texture_gen` / `entity_texture` 参数；blockstates / 模型引用；渲染器与注册代码；游戏内验证步骤。
