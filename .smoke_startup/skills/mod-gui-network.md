---
id: mod-gui-network
name: GUI 网络与方块实体同步
description: GUI（Screen/Menu/AbstractContainerMenu）、网络包（SimpleChannel/自定义 payload）与方块实体同步。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# GUI / 网络 / 方块实体同步

## GUI 组成（服务端容器 + 客户端屏幕）
- 服务端：`MenuType` + `AbstractContainerMenu`（原版）子类；客户端：`Screen<Menu>` 子类。
- Forge 1.20.1：`DeferredRegister.create(ForgeRegistries.MENU_TYPES, MODID)`，`IContainerFactory` 构造菜单；`MenuScreens.register(menuType, MyScreen::new)` 在客户端。
- Fabric：`Registries.SCREEN_HANDLER` + `ScreenHandler`（`ScreenHandlerType`），`HandledScreens.register` 在客户端。

## AbstractContainerMenu（Forge）/ ScreenHandler（Fabric）
- 构造器保存容器引用、槽位；`quickMoveStack`（快速移动）逻辑：先容器内槽位，再玩家背包，返回 `ItemStack.EMPTY` 或剩余物。
- `stillValid(Player)` 判断玩家距离容器是否太远，远则关闭。
- `addSlot` 逐个加槽位；槽位索引要与客户端 `Screen` 的槽位渲染顺序一致，否则物品错位。
- 方块实体菜单：方块实体实现 `MenuProvider`（`createMenu` 返回菜单），方块交互用 `NetworkHooks.openScreen`（Forge）/ `ServerPlayerEntity.openHandledScreen`（Fabric）。

## Screen 客户端
- `Screen` 子类绑定纹理（`getBackgroundTexture`），`renderBg`/`render` 画背景和文字；槽位用 `Slot` 渲染。
- GUI 纹理 256×256 九宫格；槽位 18×18，按钮三态（normal/hover/disabled）；文字对比度足够（见 visual-design）。
- 客户端只读菜单里的数据（`containerData`/`getSlot`），不要直接在 `Screen` 里改服务端状态。

## 网络包
### Forge 1.20.1：SimpleChannel
```java
SimpleChannel CHANNEL = NetworkRegistry.newSimpleChannel(
    new ResourceLocation(MODID, "main"),
    () -> PROTOCOL_VERSION, PROTOCOL_VERSION::equals, PROTOCOL_VERSION::equals);
CHANNEL.registerMessage(0, MyPacket.class, MyPacket::encode, MyPacket::decode, MyPacket::handle);
```
- 每个包一个唯一 index；`encode`/`decode` 写读 `FriendlyByteBuf`；`handle` 里 `context.get().enqueueWork(...)` 切回主线程再改游戏状态，`setPacketHandled(true)` 收尾。
- 发送：`CHANNEL.send(PacketDistributor.SERVER.noArg(), packet)`（客户端→服务端）、`send(PacketDistributor.PLAYER.with(() -> serverPlayer), packet)`（服务端→某玩家）。
- 版本常量（`PROTOCOL_VERSION`）升级时同步改，否则联机报协议不匹配。

### NeoForge / Fabric：自定义 Payload
- NeoForge：`CustomPacketPayload` + `FriendlyByteBuf` codec，`RegisterPayloadHandlersEvent` 注册 play 双向处理器，`PayloadRegistrar.playToClient/playToServer`。
- Fabric：`PayloadTypeRegistry.playC2S/playS2C` 注册，`CustomPayload` 实现 `getId`，`ServerPlayNetworking`/`ClientPlayNetworking` 收发；`FabricPacket` + `PacketType` 是更简封装。
- 网络包一律标【⚠️人工复核】：说明发送侧、接收侧、线程切换、字段含义。

## 方块实体同步
- 方块实体继承 `BlockEntity`，构造器传 `BlockEntityType` 和 `pos`/`state`；注册 `BlockEntityType.Builder.of(MyBE::new, block).build()`（Forge）/ `BlockEntityType.Builder.create(...)`（Fabric）。
- 读写 NBT：1.20.1 `load`/`saveAdditional`（`CompoundTag`），1.20.5+ `loadAdditional`/`saveAdditional`（`Tag`）；同步客户端用 `getUpdateTag`/`handleUpdateTag` 或 `ClientboundBlockEntityDataPacket`。
- `markDirty`/`setChanged` 通知保存；`getUpdatePacket` 返回同步包让客户端看到变化。
- 只在状态变化时同步，不要每 tick 全量发（性能红线，见 code-engineering）。
- 库存型方块实体实现 `Container`（原版）或自定义，配合菜单同步槽位。

## 检查
- 菜单/屏幕/网络包注册时机正确；客户端类不进服务端。
- `quickMoveStack` 边界不越界；槽位索引一致；`stillValid` 生效。
- 网络包版本号一致、线程切换到位；方块实体 NBT 键稳定不漂移。
- GUI、网络、方块实体同步全部标【⚠️人工复核】。
