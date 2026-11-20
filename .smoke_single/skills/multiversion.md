---
id: multiversion
name: 多版本 / 多加载器
description: Stonecutter、Architectury、MultiLoader、Modstitch、Stonecraft 的选型、目录骨架、插件坐标与验证顺序。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 多版本 / 多加载器

## 选型
- 多 MC 版本、单加载器 → Stonecutter（预处理指令裁剪代码）。
- 单版本、Fabric + NeoForge → Architectury（common + 平台子项目）。
- 单版本、双加载器、想少一层抽象 → MultiLoader-Template。
- 多目标（含 Vanilla/Forge）、接受 KTS → Modstitch。
- 多版本 × 多加载器 → Stonecraft（Stonecutter + 加载器编排）。

## Stonecutter 要点
- `settings.gradle` 声明 `vers("<mc>-<loader>", "<mc>")`，目标名与 `versions/dependencies/<target>.properties` 必须一致。
- `stonecutter.gradle` 里 `stonecutter active "<target>"`。
- 差异代码用 `//? if >=1.21 { ... //?} else { ... //?}` 包裹。
- 插件 `dev.kikugie.stonecutter`，版本按官方文档。

## Architectury 要点
- 从官方模板生成器开始，保留同一套版本 pin。
- common 只依赖原版 + Architectury API。
- 平台差异用 `@ExpectPlatform`，实现类放 `<包>.fabric` / `<包>.neoforge`。
- 不要把 Fabric API 或 NeoForge API 写进 common。

## 验证顺序
1. 逐个目标 `build`。
2. 至少一个目标启动开发端，看注册与加载日志。
3. GameTest / 场景断言。
4. 再发布多版本产物。

只生成目录不算完成；没有逐目标构建和运行证据，必须写“未验证”。
