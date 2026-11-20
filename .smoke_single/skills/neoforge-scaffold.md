---
id: neoforge-scaffold
name: NeoForge 工程搭建
description: 从零创建 NeoForge 模组：NeoGradle / ModDevGradle、mods.toml、DeferredRegister 与事件总线。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# NeoForge 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否允许 Mixin。先确认目标 MC 版本有对应 NeoForge。

## 版本要点
- 1.20.1：用 ModDevGradle 的 `net.neoforged.moddev.legacyforge`（2.0.91），
  构件坐标是 `net.neoforged:forge:1.20.1-47.1.106`，**不是** `net.neoforged:neoforge`；
  代码仍用 `net.minecraftforge.*` 包，`mods.toml` 的依赖 modId 也写 `forge`。
  NeoGradle 7（`net.neoforged.gradle.userdev`）从 1.20.2 起才适用，1.20.1 用了会解析失败。
- 1.20.2+：ModDevGradle `net.neoforged.moddev` + `net.neoforged:neoforge:<版本>`。
- 1.20.5+：元数据改为 `META-INF/neoforge.mods.toml`。
- 构建文件一律用 `scaffold_project` 生成，不要凭记忆手写插件版本和坐标。
- 不写 SRG 名（`m_12345_`）或混淆名。

## 最小目录
```
build.gradle
gradle.properties
settings.gradle
src/main/java/<package>/<ModId>Mod.java
src/main/resources/META-INF/mods.toml
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## 注册与事件
- 用 `DeferredRegister` / `Registries` 注册，挂到构造器注入的 `IEventBus`。
- 游戏逻辑事件走 `NeoForge.EVENT_BUS`；模组生命周期走 mod event bus。
- 方块实体、菜单、网络包用对应的 DeferredRegister 或 `RegisterPayloadHandlersEvent`。
- 客户端内容放 `@Mod(value = MODID, dist = Dist.CLIENT)` 或客户端事件，不要混进服务端。

## 验证
- `./gradlew build`、`./gradlew runClient`、`./gradlew runServer`。
- GameTest 任务用 `runGameTestServer`；本工坊没有 NeoForge 游戏内探针，验证以日志和 GameTest 为准。
- 网络包、方块实体、库存同步必须标【⚠️人工复核】。
