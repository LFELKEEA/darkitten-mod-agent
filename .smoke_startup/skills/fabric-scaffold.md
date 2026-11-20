---
id: fabric-scaffold
name: Fabric 工程搭建
description: 从零创建 Fabric 模组：fabric-loom、Yarn 映射、入口类、注册、资源与开发端运行。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# Fabric 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否需要 Datagen、是否允许 Mixin。缺任何一项先问，不要猜。

## 版本要点
- 1.20.1：Java 17，Yarn 映射，fabric-loom 1.7.x 可用。
- 1.20.5–1.21.1：Java 21，物品组件改用 `DataComponentType`。
- 1.21.2+：注册与模型 JSON 有调整，先核对当前 Yarn 映射的方法签名。
- 永远使用目标 MC 版本对应的 Yarn 映射，不写 `class_1234` 这类混淆名。

## 最小目录
```
gradle.properties
settings.gradle
build.gradle
src/main/java/<package>/<ModId>Mod.java
src/main/resources/fabric.mod.json
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## build.gradle 关键点
- 插件 `fabric-loom`，版本与 Gradle / MC 版本匹配。
- `minecraft`、`yarn_mappings`、`loader_version`、`fabric_version` 从 `gradle.properties` 读取。
- Java 版本必须与 MC 版本一致（1.20.1 用 17，1.20.5+ 用 21）。
- `processResources` 展开 `fabric.mod.json` 里的 `${version}`。
- Datagen 用 fabric-api 的 datagen 依赖和 `fabricApi { configureDataGeneration() }`。

## 代码结构
- 入口实现 `ModInitializer`，在 `onInitialize()` 注册物品、方块、事件、网络包。
- 客户端入口实现 `ClientModInitializer`，只放渲染、按键、GUI、粒子。
- `fabric.mod.json` 的 `entrypoints` 同时声明 `main` 与 `client`。

## 验证
- `./gradlew build`，再 `./gradlew runClient` / `runServer`。
- Fabric 没有本工坊的 Forge 探针：用开发端日志、GameTest（`./gradlew runGametest`）和单元测试验证。
- 不说“应该能跑”；以编译结果和真实日志为准。
