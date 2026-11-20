---
id: access-widener-transformer
name: Access Widener / Transformer
description: Fabric Access Widener 与 Forge/NeoForge Access Transformer 的写法、声明位置、命名空间和常见崩溃。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# Access Widener / Transformer

## Fabric：Access Widener
- 文件扩展名 `.accesswidener`，首行 `accessWidener v2 named`（命名空间按工程映射）。
- 条目：`accessible class net/minecraft/...`、`accessible method ... (...)...V`、`mutable field ...`。
- 必须在 `fabric.mod.json` 声明：`"accessWidener": "<modid>.accesswidener"`。
- 构建里用 loom 的 `accessWidenerPath` 指向文件。

## Forge / NeoForge：Access Transformer
- 文件 `META-INF/accesstransformer.cfg`。
- 条目：`public net.minecraft.world.level.Level field_12345`（Mojang 映射用官方名）。
- ForgeGradle / ModDevGradle 里声明 accessTransformer 文件；NeoForge 用 `accessTransformers` 配置。
- 1.20.1 与 1.21.x 的映射名不同，AT 条目必须按目标版本重写。

## 常见崩溃
- `Invalid access transformer`：行格式错、类/成员不存在、命名空间不匹配。
- `AccessWidenerFormatException`：版本头缺失或 v1/v2 混用。
- 改完没生效：忘了在元数据/构建里声明，或缓存未刷新。

## 原则
- 能用官方 API 就不要拓宽访问；拓宽面越小越好。
- 每条都要写清为什么需要，交付时列出。
