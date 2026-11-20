---
id: fabric-to-neoforge-port
name: Fabric 源实现 → Forge 1.20.1
description: 读 Fabric 源码或反编译 JAR，按 1.20.1 Forge 的 Mojang 映射重写：注册、事件、网络、资源目录逐项对照。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# Fabric 源实现 → Forge 1.20.1

## 先做的事
1. 用 `decompile_jar` 或直接读源工程，确认注册项、事件钩子、网络包和客户端渲染类。
2. 在现有 Forge 1.20.1 工程里重写；不要生成 Fabric 工程，也不要整段复制反编译代码。
3. 按“注册 → 交互 → 网络 → 客户端 → 数据/资源”分批改，每批 `mcdev_build` 编译。

## 映射表（到 Forge 1.20.1 / Mojang 官方映射）
- 入口：`ModInitializer` → `@Mod` 构造器；`ClientModInitializer` → `Dist.CLIENT` 专用类 + `FMLClientSetupEvent`。
- 注册：`Registry.register(Registries.ITEM, ...)` → `DeferredRegister.create(ForgeRegistries.ITEMS, MODID)`。
- 标识符：`Identifier` → `new ResourceLocation(namespace, path)`。
- 世界/玩家：`World` → `Level`，`PlayerEntity` → `Player`，`ServerPlayerEntity` → `ServerPlayer`。
- 物品：`Item.Settings` → `new Item.Properties()`；`use(World, PlayerEntity, Hand)` → `use(Level, Player, InteractionHand)`。
- 交互：`useOnBlock` → `useOn(UseOnContext)`。
- 文本：`Text` → `Component`。
- 网络：Fabric `PayloadTypeRegistry` / `ServerPlayNetworking` → Forge 1.20.1 的 `SimpleChannel` + `NetworkRegistry.newSimpleChannel`。
- 事件：Fabric `ServerTickEvents` → `MinecraftForge.EVENT_BUS.addListener` 或 `@Mod.EventBusSubscriber`。
- 配置目录：`FabricLoader.getInstance().getConfigDir()` → `FMLPaths.CONFIGDIR.get()`。

## 资源（1.20.1）
- 物品模型：`assets/<ns>/models/item/<id>.json`，`layer0` 指向真实 PNG。
- 纹理：`assets/<ns>/textures/item/<id>.png`。
- 数据包目录用复数：`data/<ns>/recipes/`、`loot_tables/`、`tags/`（1.21 才改单数）。
- 配方格式是 1.20.1 的 `{"item": "...", "count": n}`，不是 1.20.5+ 的 `{"id": ...}`。
- 中文 lang 如果是 GBK，先转 UTF-8。

## 验收
- 源 JAR 的每个注册项在 Java 里都有对应注册，且资源路径一致。
- 客户端类不在公共包，专用服务端不引用 `net.minecraft.client`。
- 编译通过后用 `mcdev_prepare` 启动开发端，探针确认物品能拿到、界面能打开、交互生效。
