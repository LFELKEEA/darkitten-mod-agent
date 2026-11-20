---
id: client-server-split
name: 客户端与服务端分离
description: 渲染、GUI、粒子、输入只放客户端；网络同步与方块实体逻辑正确分侧。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 客户端与服务端分离

## 必须放客户端
- 渲染器、模型、粒子、屏幕、HUD、按键绑定、客户端事件、客户端 Mixin。
- Fabric：`ClientModInitializer`、`ClientTickEvents`、`KeyBindingHelper`。
- NeoForge：`Dist.CLIENT`、`FMLClientSetupEvent`、`RegisterClientExtensionsEvent`。

## 必须放通用/服务端
- 物品/方块/实体注册、配方、战利品表、世界生成、命令、服务端逻辑。
- 不要在通用代码里直接 import 客户端类。

## 网络
- 自定义网络包必须标【⚠️人工复核】。
- 说明发送侧、接收侧、线程切换（客户端主线程/服务端主线程）。
- 大对象不要每 tick 同步；只同步必要字段。

## 检查
- 专用服务端能否启动（`runServer`）。
- 客户端代码是否被服务端类加载。
- 方块实体/库存/能量同步是否只在需要时触发。
