---
id: gametest
name: GameTest 自动化验证
description: Fabric / Forge / NeoForge 的 GameTest 结构、注册、运行任务与断言写法。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# GameTest 自动化验证

## 通用结构
- 测试类放 `src/main/java/.../gametest/` 或 `src/test/java`（按加载器约定）。
- 测试方法带 `@GameTest`，参数 `GameTestHelper`；用 `helper.assert*` 做断言。
- 结构模板：`@GameTest(template = "<ns>:empty")` 或代码生成平台。
- 注册：Fabric 用 `fabric-gametest-api-v1` 的入口；Forge/NeoForge 用 `@GameTestHolder` / `@PrefixGameTestTemplate` 并注册到 mod bus。

## 运行
- Fabric：`./gradlew runGametest`。
- Forge 1.20.1：`./gradlew runGameTestServer`。
- NeoForge：`./gradlew runGameTestServer` 或 `runClientGameTest`（按模板）。
- 本工坊也可用 `mcdev_gametest` 包装执行并返回汇总。

## 断言原则
- 测行为，不测实现：方块是否出现、实体是否生成、容器是否有物品、状态是否切换。
- 每个测试独立清理现场；不要依赖上一个测试的世界状态。
- 失败要能定位：断言消息写清期望值、实际值、坐标。

## 交付
写明运行了哪些测试、通过数量、失败项与日志路径。没有运行就不要说“测试通过”。
