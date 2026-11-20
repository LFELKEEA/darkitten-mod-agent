---
id: datagen-assets
name: Datagen 与资源文件
description: 生成方块状态、模型、配方、战利品表、标签、语言文件、音效注册与纹理规格。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# Datagen 与资源文件

## 原则
- 目标版本支持 Datagen 时优先用 Datagen，不手写几十个重复 JSON。
- 仍要给出生成器类、`DataGeneratorEntrypoint`（Fabric）或 `GatherDataEvent`（NeoForge）的完整代码。
- 不支持 Datagen 的版本，逐个给出 JSON 的完整路径与内容。

## 必备资源
- `assets/<modid>/lang/en_us.json`
- `assets/<modid>/lang/zh_cn.json`
- `assets/<modid>/models/item/<name>.json`
- `assets/<modid>/models/block/<name>.json`
- `assets/<modid>/blockstates/<name>.json`
- `data/<modid>/recipes/<name>.json`
- `data/<modid>/loot_tables/blocks/<name>.json`（版本不同路径可能变化）
- `assets/<modid>/sounds.json`（有音效时）

## 纹理
- 物品/方块：16x16 或 32x32 PNG，必须带透明通道。
- 给出像素级描述：主色、描边、高光、阴影、图案位置。
- 纹理路径必须与模型引用一致。
- 复杂模型先给 Blockbench 设计描述，再导出 MC JSON。

## 音效
- `sounds.json` 注册名、字幕键、音频文件路径。
- 建议 OGG Vorbis，44.1kHz，单声道/立体声说明。
