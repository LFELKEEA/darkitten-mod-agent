---
id: version-facts
name: 版本事实核对
description: 跨版本差异速查：Java、元数据、数据目录、物品定义、映射与 ID API；写代码前先核对目标版本。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 版本事实核对

## 先做
1. 确认 MC 版本、加载器、目标 Java。三者缺一不可。
2. 调 `version_facts` 看静态基线；工程已打开时调 `project_fact_check` 扫冲突。
3. 不确定的 API 一律 `dev_docs lookup` 查 sources，不靠记忆。

## 版本基线
| MC | Java | 元数据 | 数据目录 | 物品定义 | ID API |
| --- | --- | --- | --- | --- | --- |
| 1.20.1 | 17 | mods.toml | 复数 | models/item | new ResourceLocation |
| 1.20.5–1.21.1 | 21 | NeoForge 用 neoforge.mods.toml | 复数 | models/item | ResourceLocation.fromNamespaceAndPath |
| 1.21–1.21.8 | 21 | 同上 | 单数 | 1.21.4+ 用 assets/<ns>/items | ResourceLocation.fromNamespaceAndPath |
| 1.21.9+ / 26.x | 25 | 同上 | 单数 | assets/<ns>/items | Identifier.fromNamespaceAndPath |

## 构建插件
- Forge：ForgeGradle（1.20.1 用 6.x）。
- NeoForge 1.20.1：NeoGradle；1.20.2+：ModDevGradle 2.x。
- Fabric：fabric-loom；26.x 未混淆、非 remap。
- Quilt：org.quiltmc.loom。
- 多加载器：Architectury / MultiLoader。

## 高频错误
- 1.21+ 还写 `recipes` / `loot_tables` / `tags/blocks`。
- NeoForge 1.20.5+ 还写 `mods.toml`。
- 1.21.4+ 只有 `models/item` 没有 `assets/<ns>/items`。
- Java 17 编译 1.21.x，或 Java 21 编译 26.x。
- 26.x 还配 Yarn 映射或 `new ResourceLocation`。
- 混淆名残留：class_1234 / method_5678_。

## 输出
说明“基于哪个版本事实”，列出冲突文件和修复动作；不确定的写“需在线核实”，不要猜。
