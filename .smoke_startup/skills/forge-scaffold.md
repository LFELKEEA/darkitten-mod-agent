---
id: forge-scaffold
name: Forge 工程搭建
description: Forge 模组：ForgeGradle、mods.toml、DeferredRegister、事件总线，以及 1.20.1 探针验证。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# Forge 工程搭建

## 1.20.1 基线
- Minecraft 1.20.1，Forge 47.4.28（`47.x`），Java 17。
- ForgeGradle `[6.0,6.2)`，Gradle 8.8，Mojang 官方映射（official）。
- 新建工程用 `scaffold_project`（loader=Forge），不要手工从零写构建脚本。

## 关键文件
```
build.gradle / settings.gradle / gradle.properties
gradlew, gradlew.bat, gradle/wrapper/*
src/main/java/<package>/ModMain.java
src/main/resources/META-INF/mods.toml
src/main/resources/pack.mcmeta
```

## build.gradle 要点
- `minecraft { mappings channel: 'official', version: '1.20.1' }`。
- `java.toolchain.languageVersion = JavaLanguageVersion.of(17)`。
- 依赖：`minecraft "net.minecraftforge:forge:1.20.1-47.4.28"`。
- `processResources` 用 `expand` 展开 `mods.toml` / `pack.mcmeta` 里的 `${...}`。
- runs 保留 `client`、`server`、`gameTestServer`、`data`，工作目录分别是 `run-client`、`run-server`、`run-gametest`、`run-data`。

## 代码结构
- 入口 `@Mod(MODID)` 构造器里创建 `DeferredRegister` 并挂到 `modEventBus`。
- 注册用 `DeferredRegister.Items` / `DeferredRegister.Blocks` / `DeferredRegister<EntityType<?>>`。
- 生命周期用 `FMLCommonSetupEvent`、`FMLClientSetupEvent`；游戏事件用 `MinecraftForge.EVENT_BUS`。
- 客户端专用类放 `client/` 包，并加 `@Mod.EventBusSubscriber(value = Dist.CLIENT, bus = Mod.EventBusSubscriber.Bus.MOD)` 或 `DistExecutor` 兜底。

## 资源（1.20.1 用复数目录）
- `assets/<mod_id>/models/item/<id>.json`
- `assets/<mod_id>/textures/item/<id>.png`
- `assets/<mod_id>/lang/en_us.json`、`zh_cn.json`
- `data/<mod_id>/recipes/`、`loot_tables/`、`tags/`（1.21 才改成单数）

## 验证
- `mcdev_build` 编译，`mcdev_prepare` 启动开发端，再用探针状态、命令、UI 和场景断言验证。
- Forge 1.20.1 是唯一支持游戏内探针的基线；其他 Forge 版本用日志和 GameTest。
