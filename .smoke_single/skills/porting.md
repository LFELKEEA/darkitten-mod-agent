---
id: porting
name: 从现有 JAR 参考实现
description: 把源 JAR 反编译成可读源码，按功能拆解后，用当前工程加载器的官方写法重写，不直接搬运混淆代码。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 从现有 JAR 参考实现

## 目标
目标加载器、MC 版本、映射以当前工程为准：Fabric 用 Yarn，Forge / NeoForge 用 Mojang 官方映射。参考 JAR 只用来确认行为、注册项和资源结构，最终代码必须能按当前工程编译。

## 流程
1. `decompile_jar` 反编译源 JAR，读索引和目标类；不要凭记忆猜实现。
2. 列出源 JAR 的注册项（物品/方块/实体/命令）、事件钩子、网络包、资源目录。
3. 在现有工程里按“注册 → 交互 → 网络 → 客户端 → 数据/资源”顺序重写，每批都编译。
4. 用 `build_project` 编译；Forge 1.20.1 再 `mcdev_prepare` 启动真实开发端并用探针验证，其他加载器用运行日志和 GameTest 验证。
5. 涉及贴图、模型、语言文件时，逐项核对 `assets/<mod_id>/` 下的路径与注册名。

## 映射要点（按目标加载器选择）
- 标识符：Fabric Yarn `Identifier`；Forge / NeoForge `ResourceLocation`（1.20.1 用 `new ResourceLocation(namespace, path)`）。
- 注册：Fabric `Registry.register` + `Registries.*`；Forge / NeoForge `DeferredRegister` 挂到对应 event bus；Architectury 把公共注册放 common，平台差异用 `@ExpectPlatform`。
- 事件：Fabric 回调 / `ServerLifecycleEvents`；Forge `MinecraftForge.EVENT_BUS` + mod event bus；NeoForge `NeoForge.EVENT_BUS` + `IEventBus`。
- 文本：`Text` / `Component` 统一成目标版本的组件类型；1.20.1 是 `Component`。
- 物品属性：Fabric `Item.Settings` → 目标加载器的 `Item.Properties`（1.20.1）。
- 客户端：`net.minecraft.client` 相关代码只能放客户端入口或平台客户端子工程，专用服务端不能加载。

## 风险
- 反编译代码只作参考，不要整段复制；许可证不兼容的代码不能用。
- 编译通过不等于运行正常；能启动开发端就启动，不能就明确写“仅编译通过”。
- 世界生成、方块实体、网络同步、Mixin 改动标【⚠️人工复核】。
