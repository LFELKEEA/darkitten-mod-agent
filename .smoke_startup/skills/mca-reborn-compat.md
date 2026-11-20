---
id: mca-reborn-compat
name: MCA Reborn 兼容
description: 让作用于玩家 / 村民的道具在 MCA 村民上同样生效，并让玩家接管村民身份后继承姓名、关系与外观，可以代替村民与他人结婚生育。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# MCA Reborn 兼容（Forge 1.20.1 参考）

> 下面是 MCA 的通用机制。实际类名和方法签名以当前工程依赖的 MCA 1.20.1 源码为准，写之前先用 `decompile_jar` 或 `github_file` 核对；1.21.x 的 `player.level()` 这类写法在 1.20.1 要换成 `player.level`。

## 判定目标
- 村民实体实现 `VillagerLike<?>`；判断必须写 `entity instanceof VillagerLike<?> && !(entity instanceof Player)`。
- 不要用 `VillagerLike.toVillager(entity)` 做“是不是村民”的判断：MCA 对任何 `ServerPlayer` 都会临时返回一个村民视图，会把玩家误判成村民。

## 道具对村民生效
- 只重写 `use` / `useOn` 不够，必须重写：
  `public InteractionResult interactLivingEntity(ItemStack stack, Player player, LivingEntity target, InteractionHand hand)`
- 服务端执行真实逻辑，客户端返回 `InteractionResult.sidedSuccess(player.level().isClientSide)`。
- MCA 村民的 `mobInteract` 会先消费右键（打开对话/交易），所以物品自己的 `interactLivingEntity` 不会被调用。必须在 `PlayerInteractEvent.EntityInteract` 里抢先处理：只接管本模组道具，调用 `item.interactLivingEntity`，结果 `consumesAction()` 时 `setCancellationResult` + `setCanceled(true)`。`event/ServerEvents` 里已有这份兜底，不要删。
- 对玩家和对村民共用同一套身份同步函数，避免两套逻辑行为不一致。
- 在现有工程里建议拆成：
  - `compat/McaCompat.java`：`possess(serverPlayer, target)`、`wear(serverPlayer, skinStack)`、`release(serverPlayer)`、`captureVillager(target)`、`tick(player)`。
  - 对应道具的 `item/*Item`：在 `interactLivingEntity` 里调用同一套同步函数。
  - `data/SkinData.java`、`client/ClientSkinCache.java`、`client/ClientEvents.java`、`event/ServerEvents.java`、`mixin/client/AbstractClientPlayerMixin.java`。
  - 快照存在玩家 `getPersistentData()`，服务器重启后仍能还原；每 20 tick 检查承载身份的道具是否还在装备栏，不在就自动还原。

## 身份同步
1. `PlayerSaveData data = PlayerSaveData.get(serverPlayer);`
2. 备份旧数据：`CompoundTag previous = data.getEntityData();`（退出时恢复）
3. 把村民数据写进去：
   ```
   CompoundTag tag = new CompoundTag();
   villager.addAdditionalSaveData(tag);
   data.setEntityData(tag);
   data.setEntityDataSet(true);
   player.refreshDimensions();
   ```
4. 广播给同维度所有玩家，否则别人看到的皮肤还是旧的：
   `Network.sendToPlayer(new PlayerDataMessage(player.getUUID(), data.getEntityData()), viewer)`
   （`net.conczin.mca.network.s2c.PlayerDataMessage` / `net.conczin.mca.network.Network`）
5. 皮肤/衣服/头发都在村民 tracked data 里；`addAdditionalSaveData` 已经覆盖，不要只改一个字符串。

## 姓名 / 关系 / 家庭
- 玩家节点：`FamilyTreeNode to = PlayerSaveData.get(player).getFamilyEntry();`
- 村民节点：`FamilyTreeNode from = EntityRelationship.of(villager).orElseThrow().getFamilyEntry();`
- 复制：`to.setName(from.getName())`、`to.setGender(from.gender())`、`to.setProfession(from.getProfession())`、`to.setRelationshipState(from.getRelationshipState())`。
- 配偶：`to.updatePartner(partnerEntity, from.getRelationshipState())`；如果对方还在，`partnerNode.updatePartner(player, state)`。对方 UUID 等于被占用的村民时，只改姓名/性别/职业，不要让他指向自己。
- 子女：`to.children().clear(); to.children().addAll(from.children());`，并在每个子女节点里把 father/mother 从村民 UUID 改成玩家 UUID。
- 父母：把父母节点 `children()` 里的村民 UUID 换成玩家 UUID。
- 结婚/生育：不要自己造关系表。只要玩家节点保留原村民的配偶、子女、关系状态，MCA 的村民 AI 与 FamilyTree 就会继续把玩家当作原村民。
- 顺序很重要：先恢复村民本体和家庭树，再写玩家节点，最后广播；否则配偶指针会被后写的空数据覆盖。

## 退出 / 恢复
- 保存进入前的 `PlayerSaveData` 快照和原村民 FamilyTree 引用。
- 退出时恢复 `setEntityData(previous)`、`setEntityDataSet(previousSet)`、`refreshDimensions()`，并把 FamilyTree 引用反向改回原村民 UUID。
- 恢复后再次广播 `PlayerDataMessage`。

## 风险
- MCA 是 GPL-3.0，只做兼容调用，不要复制它的源码进你的模组。
- 不要直接 `new VillagerEntityMCA`：会绕过注册和维度初始化。需要村民实体时从世界或 `VillagerLike` 视图里取。
- 关系改动只放服务端；客户端只接收 `PlayerDataMessage`。
