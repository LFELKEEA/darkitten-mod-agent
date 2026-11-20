---
id: architectury-scaffold
name: Architectury 多加载器工程
description: 用 Architectury 组织 common + Fabric + NeoForge 多加载器工程，处理平台差异与构建脚本。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# Architectury 多加载器工程

## 结构
```
settings.gradle
build.gradle
gradle.properties
common/        # 通用代码
fabric/        # Fabric 入口与平台实现
neoforge/      # NeoForge 入口与平台实现
```

## 构建
- 使用 architectury-loom / architectury plugin，版本与目标 MC 对齐。
- `common` 只依赖 Architectury API 和原版，不直接依赖 Fabric API 或 NeoForge API。
- `fabric` / `neoforge` 各自声明对应加载器依赖和入口。
- 平台差异用 `@ExpectPlatform` 或接口 + 平台实现桥接。

## 代码边界
- common 放物品/方块注册抽象、事件抽象、网络包抽象。
- 平台子项目放加载器入口、客户端初始化、平台专属 API 调用。
- 不要在 common 里 import `net.fabricmc.*` 或 `net.neoforged.*`。

## 验证
- 分别构建 `:fabric:build` 与 `:neoforge:build`，再启动各自的开发端。
- 没有内置 Architectury 骨架时，按官方模板生成，并先核对插件与 MC 版本。
- 两个平台都要能编译；只通过一个平台不算完成。
