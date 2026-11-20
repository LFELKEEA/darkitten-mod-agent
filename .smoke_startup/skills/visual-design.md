---
id: visual-design
name: 绘图、纹理与界面视觉
description: 像素纹理、物品/方块图标、模组图标、GUI、HUD、SVG 图表与宣传图；可直接生成 PNG 像素图。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 绘图、纹理与界面视觉

## 能做什么
像素纹理、物品/方块图标、模组图标、GUI、HUD、SVG 架构图/流程图、宣传图提示词。方块/物品/实体皮肤优先用结构化生成器，`pixel_art` 只用于 logo、GUI 小图标；矢量图用 `write_file` 写 SVG 或 draw.io XML。

## texture_gen 用法（方块/物品首选）
```tool
{"tool":"texture_gen","path":"src/main/resources/assets/modid/textures/block/twilight_stone.png","style":"stone","base":"#6f6a63","accent":"#3d3a36","size":16,"seed":7}
{"tool":"texture_gen","path":"src/main/resources/assets/modid/textures/item/serpent_fang.png","style":"serpent_fang","base":"#d9d2b8","accent":"#8f2f3f","size":16,"seed":3}
```
- 方块样式：`stone` `mossy` `deepslate` `gravel` `clay` `dirt` `grass` `grass_side` `leaves` `log` `log_top` `planks` `bricks` `ore` `metal` `crystal` `fabric` `mushroom_cap` `mushroom_stem` `portal` `water` `sand` `snow` `obsidian` `glow` `glass` `flower` `sapling` `mushroom` `lantern` `panel`。
  名字里带 `mossy/overgrown`、`deepslate`、`gravel`、`clay/terracotta` 时也会自动选中对应样式。
- 物品直接传物品名（`ironwood_sword`、`serpent_fang`、`lich_sigil`、`arcane_dust`、`scale_shard`、`spectral_cloth`、`ironwood_bow`、`twilight_arrow`、`twilight_potion`、`grove_scroll`、`silver_ring`、`moon_amulet`、`raven_feather`、`gold_nugget`、`twilight_apple`、`honey_bread`、`raw_meat`、`wolf_hide`、`old_coin`…），自动匹配轮廓、明暗和描边。
- 建议给 `base`（主色）与 `accent`（宝石/纹样/发光色）；省略时会按名字推断材质色。固定 `seed` 保证可复现。
- 生成器自带多阶明度、噪声明暗、裂纹/木纹/砖缝/矿石簇等细节；不要再用两三个纯色块代替。

## entity_texture 用法（生物皮肤首选）
```tool
{"tool":"entity_texture","path":"src/main/resources/assets/modid/textures/entity/hollow_stalker.png","layout":"humanoid","base":"#b9b3a1","accent":"#b53b3b","variant":"undead","seed":7}
{"tool":"entity_texture","path":"src/main/resources/assets/modid/textures/entity/moss_crawler.png","layout":"boxes","boxes":[{"part":"body","uv":[0,0],"size":[8,8,12]},{"part":"head","uv":[0,17],"size":[6,4,6]}],"base":"#6f7b45","accent":"#d8c25a","seed":3}
```
- `humanoid` 使用原版 64×64 玩家皮肤 UV：头/帽/身体/右臂/左臂/右腿/左腿/外套/袖/裤，面部的眼睛、领口、腰带、袖口、靴子会自动画出。
- 自定义模型用 `boxes`，每项 `uv:[u,v]`、`size:[w,h,d]`、`part`，按原版立方体六面（上/下/右/前/左/后）展开，和 `CubeListBuilder.texOffs` 完全对应。
- 不要用正面立绘充当实体皮肤：UV 不对会把整张图撕成乱码色块。
- 主色亮度不要低于 `#4a4a5a`：生成器会把过暗的主色自动抬亮，但手写贴图时也要遵守，
  否则生物进游戏就是一团黑影；lint 的 P2-15 报“接近纯黑/颜色太少”时必须重新生成覆盖。
- `variant` 写材质关键词会自动追加纹样：`bone/lich` 肋骨、`scale/serpent` 鳞片、
  `moss/crawler` 苔点、`fire/wisp` 发光点、`robe/mage` 袍边、`wraith/shadow` 灵纹。
  同一只生物的主色与点缀色要有明度差（至少 3 阶），否则纹样会看不见。
- 外层（hat/jacket/sleeve/pants）生成器只画帽檐、袖口、下摆、腰带，其余保持透明；
  不要为了让外层“厚”而整块涂满，整块涂满加随机镂空就是外层乱码色块的来源。
- 像素风每个面只用 2~3 阶硬边明暗，按朝向定基调（顶亮、底暗、背面比正面暗一阶），
  不要给每个像素铺连续噪声——那会糊成杂色而不是材质。
- 验收标准是“1x 下认得出”：生成后调 `vision_review` 或看 1x 预览，问自己
  “不看文件名能说出这是骷髅/蛇/蘑菇吗”；认不出就改轮廓或主色重画，不要只加噪点。
- 物品图标保持 4~8 色：外描边 + 左上受光面 + 右下阴影面 + 1~2 处点缀色；
  不要用抗锯齿/模糊/缩放插值，像素边缘必须硬。

## pixel_art 用法（仅小图标）
```tool
{"tool":"pixel_art","path":"src/main/resources/assets/modid/textures/item/gem.png","palette":{".":"transparent","o":"#1b1b1f","a":"#7fd6ff","h":"#d8f6ff"},"rows":["................","................","......oooo......",".....oaaaao.....","....oaaaaaao....","...oaahhaaaao...","...oaahhaaaao...","...oaaaaaaaao...","...oaaaaaaaao...","....oaaaaaao....",".....oaaaao.....","......oooo......","................","................","................","................"]}
```
- `rows` 每行长度必须一致；调色板建议 8–20 色；输出 RGBA PNG。
- 16×16 用于物品/方块；32×32 用于精细图标；GUI 建议 256×256。
- 动画纹理竖向排列帧，旁边写 `.png.mcmeta`：`{"animation":{"frametime":2}}`。

## 16×16 纹理绘制技巧
- **先画形状后上色**：先用 1–2 个色块定主体轮廓（占 10×10 以上），再分层上色、描边、高光。
- **外轮廓**：用比主体深一档的同色相描 1px 边，不要用纯黑 `#000`（会脏、不自然）。
- **光源左上**：高光放左上/上侧 1px，阴影放右下/下侧 1px；高光用比基色亮 2–3 档的颜色。
- **像素对齐**：斜线用 1:1 / 2:1 / 2:2 的阶梯，不要用抗锯齿半透明过渡；保持硬边。
- **对称**：左右对称的物品先画一半再镜像，保证轮廓工整。
- **主体够大**：物品主体至少 12×12，否则在地面/手中看不清。

## 调色板
- 先定 3–5 个主色（基色 + 亮部 + 暗部 + 描边 + 点缀色），再各加 1 个高光/阴影档；不要超过 20 色。
- 明暗用**同色相、不同明度**（HSL 调 L），不要只调 RGB 三通道；饱和度随明度一起微调更自然。
- 金属/宝石：亮部更亮、暗部更深、加 1 个纯白高光点；木头/布料：明暗对比低，柔和过渡。
- 冷色做阴影、暖色做高光更通透（如蓝色物体阴影偏紫、高光偏青白）。
- 透明区域用 `.`，不要在透明区放半透明色（除非版本支持 translucent）。

## 体积感
- **两明一暗**：主体最少「基色 + 1 亮 + 1 暗」三层，才有立体感；只有基色就是平片。
- **渐变阶梯**：从亮到暗 3–4 阶，每阶 1px，做斜面/球面体积。
- **轮廓转折**：在边缘内侧加一圈暗色，中心加亮色，做「圆润」感（如宝石、药水）。
- **投影**：地面物品可在地面纹理上画 1px 暗影，增加落地感。
- 参考：先看原版同类物品（如钻石、金锭）的明暗分布，再套用自己的配色。

## 动画帧
- 动画纹理竖向排列帧：`png.mcmeta` 写 `{"animation":{"frametime":2}}`；`frametime` 是每帧 tick（20 tick=1 秒）。
- 每帧尺寸一致，帧之间只改「动的部分」（火焰、闪光、呼吸），静态部分复用。
- 火焰/水：3–8 帧循环；旋转动画用 `{"animation":{"interpolate":true}}`（平滑插值）或逐帧画旋转。
- 帧顺序从上到下；`frames` 数组可指定播放顺序和每帧时长（`{"frames":[{"index":0,"time":4},1,2]}`）。
- 画帧时先画第一帧，复制后微调像素，不要每帧重画。

## 像素画规则（总则）
- 先用 3–5 个主色定调，再加 1–2 个高光/阴影；不要用纯黑描边，用深色同色相。
- 光源左上；高光 1px、阴影 1px；外轮廓闭合。
- 透明区域用 `.`；不要抗锯齿，保持硬边。
- 物品轮廓要在地面和手中看得清：主体至少占 12×12。
- 方块纹理要能四方连续：边缘颜色一致，避免明显接缝。

## 模型与纹理对应
- 每个注册名都要有模型和纹理；路径 `assets/<modid>/textures/...`。
- 物品模型 `layer0` 指向物品纹理；方块模型 `texture` 键与 blockstates 引用一致。
- GUI：九宫格背景、按钮三态（normal / hover / disabled）、槽位 18×18、文字对比度足够。

## 图表
- 架构图/流程图/类图优先 SVG：`viewBox="0 0 1200 800"`，系统无衬线字体，颜色 5 种以内。
- draw.io XML 用 `mxGraphModel`，节点 id 唯一，连线带箭头。
- 图里不要塞大段代码；节点用类名/模块名，边标调用或依赖关系。

## 封面与宣传图提示词
主体 + 风格 + 构图 + 光照 + 色调 + 分辨率；Minecraft 风格要写明方块化、像素化、等距视角。不要生成受版权保护的角色或直接复制他人美术。

## 检查
像素对齐、透明通道、路径与引用一致、GUI 状态齐全、文字可读、没有模糊缩放。
