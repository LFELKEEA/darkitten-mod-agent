"""MCA Reborn 兼容、身份道具与客户端外观的 Java 模板。

这些模板与实机验证过、可编译可运行的实现保持一致：
- 身份同步会同步姓名、性别、职业、配偶、父母、子女；
- 村民本体随身份道具保存，解除时原样放回世界；
- 客户端通过 AbstractClientPlayer#getSkin 覆盖玩家皮肤，MCA 玩家由 MCA 自己渲染。
占位符：@PACKAGE@ @MOD_ID@ @CLASS@。
"""
from __future__ import annotations

MCA_COMPAT = r'''package @PACKAGE@.compat;

import net.conczin.mca.entity.VillagerEntityMCA;
import net.conczin.mca.entity.VillagerLike;
import net.conczin.mca.entity.ai.relationship.EntityRelationship;
import net.conczin.mca.entity.ai.relationship.Gender;
import net.conczin.mca.entity.ai.relationship.RelationshipState;
import net.conczin.mca.network.Network;
import net.conczin.mca.network.s2c.PlayerDataMessage;
import net.conczin.mca.server.world.data.FamilyTree;
import net.conczin.mca.server.world.data.FamilyTreeNode;
import net.conczin.mca.server.world.data.PlayerSaveData;
import net.minecraft.Util;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.StringTag;
import net.minecraft.network.FriendlyByteBuf;
import net.minecraft.network.chat.Component;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.resources.ResourceKey;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.EntityType;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.npc.VillagerProfession;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.network.PacketDistributor;
import net.neoforged.neoforge.network.event.RegisterPayloadHandlersEvent;
import net.neoforged.neoforge.network.registration.PayloadRegistrar;
import @PACKAGE@.data.SkinData;

import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/**
 * MCA Reborn 兼容层：把凡家村民的外观、身份和家庭关系同步到玩家，
 * 以及退出时完整还原。
 *
 * <p>两条入口：</p>
 * <ul>
 *     <li>道具右键直接接管身份：{@link #possess(ServerPlayer, Entity)} / {@link #release(ServerPlayer)}</li>
 *     <li>穿戴身份道具：{@link #wear(ServerPlayer, ItemStack)} / {@link #release(ServerPlayer)}</li>
 * </ul>
 *
 * <p>写身份前先把玩家原本的资料快照到持久化数据里；同时把被接管村民的本体
 * （身体 NBT、坐标、维度、实体类型）一起存进去，解除时原样放回世界，
 * 不会凭空少一个村民。</p>
 */
public final class McaCompat {
    private static final String SNAPSHOT_KEY = "mca_identity_snapshot";
    private static final String MODE_KEY = "Mode";
    private static final String TOKEN_KEY = "Token";
    private static final String VILLAGER_KEY = "Villager";

    public static final String MODE_PHONE = "phone";
    public static final String MODE_ITEM = "item";

    private McaCompat() {
    }

    /** 解除身份同步后通知客户端丢掉缓存的村民外观；否则皮肤会停留在村民状态。 */
    public record ClearPayload(UUID player) implements CustomPacketPayload {
        public static final CustomPacketPayload.Type<ClearPayload> TYPE =
                new CustomPacketPayload.Type<>(
                        ResourceLocation.fromNamespaceAndPath("@MOD_ID@", "mca_clear")
                );
        public static final StreamCodec<FriendlyByteBuf, ClearPayload> STREAM_CODEC =
                CustomPacketPayload.codec(ClearPayload::write, ClearPayload::new);

        public ClearPayload(FriendlyByteBuf buffer) {
            this(buffer.readUUID());
        }

        public void write(FriendlyByteBuf buffer) {
            buffer.writeUUID(player);
        }

        @Override
        public CustomPacketPayload.Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    public static void register(IEventBus modEventBus) {
        modEventBus.addListener(McaCompat::registerPayloads);
    }

    private static void registerPayloads(RegisterPayloadHandlersEvent event) {
        PayloadRegistrar registrar = event.registrar("1");
        registrar.playToClient(ClearPayload.TYPE, ClearPayload.STREAM_CODEC, McaSkinHandler::handle);
    }

    public record Snapshot(
            CompoundTag entityData,
            boolean entityDataSet,
            String name,
            Gender gender,
            VillagerProfession profession,
            RelationshipState relationshipState,
            UUID partner,
            UUID father,
            UUID mother,
            Set<UUID> children,
            CompoundTag villager
    ) {
    }

    public static boolean isMcaVillager(Entity entity) {
        // 注意：VillagerLike.toVillager(Entity) 对任意 ServerPlayer 都会返回一个临时村民，
        // 不能用来判断“这是不是一个村民实体”。
        return entity instanceof VillagerLike<?> && !(entity instanceof Player);
    }

    public static boolean isPossessed(ServerPlayer player) {
        return player != null && player.getPersistentData().contains(SNAPSHOT_KEY);
    }

    public static String modeOf(ServerPlayer player) {
        return player.getPersistentData().contains(SNAPSHOT_KEY)
                ? player.getPersistentData().getCompound(SNAPSHOT_KEY).getString(MODE_KEY)
                : "";
    }

    public static String tokenOf(ServerPlayer player) {
        return player.getPersistentData().contains(SNAPSHOT_KEY)
                ? player.getPersistentData().getCompound(SNAPSHOT_KEY).getString(TOKEN_KEY)
                : "";
    }

    public static Snapshot snapshot(ServerPlayer player) {
        PlayerSaveData data = PlayerSaveData.get(player);
        FamilyTreeNode node = data.getFamilyEntry();
        CompoundTag body = data.getEntityData();
        return new Snapshot(
                body == null ? new CompoundTag() : body.copy(),
                data.isEntityDataSet(),
                node.getName(),
                node.gender(),
                node.getProfession(),
                node.getRelationshipState(),
                node.partner(),
                node.father(),
                node.mother(),
                new HashSet<>(node.children()),
                null
        );
    }

    /** 用玩家自己的数据覆盖当前身份，用于解除身份同步/脱下身份道具。 */
    public static void restore(ServerPlayer player, Snapshot snapshot) {
        if (snapshot == null) {
            return;
        }
        PlayerSaveData data = PlayerSaveData.get(player);
        FamilyTree tree = data.getFamilyTree();
        FamilyTreeNode node = data.getFamilyEntry();
        CompoundTag villagerInfo = snapshot.villager();

        // 先把“被穿走的村民”的配偶从玩家身上摘下来，避免下面 updatePartner 反向清空玩家自己的关系。
        UUID villagerId = villagerInfo == null ? null : uuid(villagerInfo, "UUID");
        UUID bodyPartner = villagerInfo == null ? null : identityPartner(villagerInfo);
        if (valid(bodyPartner)) {
            tree.getOrEmpty(bodyPartner).ifPresent(other -> other.updatePartner(null, RelationshipState.SINGLE));
        }

        node.setName(snapshot.name());
        node.setGender(snapshot.gender());
        node.setProfession(snapshot.profession());

        node.updatePartner(null, RelationshipState.SINGLE);
        if (valid(snapshot.partner())) {
            linkPartner(player.serverLevel(), node, player, snapshot.partner(), snapshot.relationshipState(), tree);
        } else {
            node.setRelationshipState(snapshot.relationshipState());
        }

        node.removeFather();
        node.removeMother();
        tree.getOrEmpty(snapshot.father()).ifPresent(node::setFather);
        tree.getOrEmpty(snapshot.mother()).ifPresent(node::setMother);

        node.children().clear();
        for (UUID child : snapshot.children()) {
            node.addChild(child);
        }

        data.setEntityData(snapshot.entityData().copy());
        data.setEntityDataSet(snapshot.entityDataSet());
        player.setCustomName(null);
        player.refreshDisplayName();
        player.refreshDimensions();

        // 把村民本体放回世界并恢复族谱链接。
        if (villagerInfo != null) {
            respawnVillager(player, villagerInfo, tree, villagerId, snapshot.partner());
        }

        if (snapshot.entityDataSet()) {
            broadcast(player);
        } else {
            clearClientData(player);
        }
    }

    /** 把 MCA 村民的身体数据、姓名、性别、职业、配偶、子女、父母整体复制给玩家。 */
    public static boolean possess(ServerPlayer player, Entity target) {
        if (player == null || !(target instanceof VillagerEntityMCA villager)) {
            return false;
        }
        if (isPossessed(player)) {
            // 已经同步着别的身体：先完整归还，再接管新的。
            release(player);
        }
        CompoundTag info = villagerTag(villager);
        if (!info.contains("Body", 10) || !info.contains("Identity", 10)) {
            return false;
        }
        CompoundTag body = info.getCompound("Body").copy();
        CompoundTag identity = info.getCompound("Identity").copy();

        Snapshot before = snapshot(player);
        if (!applyBody(player, body, identity)) {
            return false;
        }
        info.putBoolean("Removed", true);
        storeSnapshot(player, before, MODE_PHONE, "", info);
        // 身体已经被玩家接管，原实体从世界里移除，解除时再放回。
        villager.discard();
        return true;
    }

    /**
     * 穿上身份道具时调用。
     *
     * <p>村民身份会连同姓名、职业、配偶、子女、父母一起覆盖；
     * 普通玩家皮肤只改外观和显示名，不动 MCA 关系。</p>
     */
    public static boolean wear(ServerPlayer player, ItemStack skin) {
        if (player == null || !SkinData.isSkin(skin)) {
            return false;
        }
        if (isPossessed(player)) {
            // 换皮等同于先脱掉上一张皮，保证同一时间只有一具身体被占用。
            release(player);
        }
        String name = SkinData.name(skin);
        CompoundTag info = SkinData.villager(skin);
        Snapshot before = snapshot(player);

        if (info != null) {
            CompoundTag body = info.getCompound("Body");
            CompoundTag identity = info.getCompound("Identity");
            if (!body.isEmpty() && !identity.isEmpty() && applyBody(player, body.copy(), identity.copy())) {
                info.putBoolean("Removed", true);
                storeSnapshot(player, before, MODE_ITEM, SkinData.token(skin), info);
                return true;
            }
        }

        // 玩家皮肤：只做外观和名字伪装。
        if (!name.isBlank()) {
            player.setCustomName(Component.literal(name));
            player.refreshDisplayName();
        }
        storeSnapshot(player, before, MODE_ITEM, SkinData.token(skin), null);
        return true;
    }

    private static boolean applyBody(ServerPlayer player, CompoundTag body, CompoundTag identity) {
        PlayerSaveData data = PlayerSaveData.get(player);
        FamilyTreeNode to = data.getFamilyEntry();
        FamilyTree tree = data.getFamilyTree();

        // 先把原村民本体从族谱上摘下来，否则配偶/子女会同时挂两个人。
        UUID bodyId = uuid(identity, SkinData.OWNER_UUID);
        if (valid(bodyId)) {
            tree.getOrEmpty(bodyId).ifPresent(v -> {
                v.updatePartner(null, RelationshipState.SINGLE);
                v.removeFather();
                v.removeMother();
                v.children().clear();
            });
        }

        data.setEntityData(body.copy());
        data.setEntityDataSet(true);

        applyIdentity(player.serverLevel(), to, player, tree, identity);
        player.refreshDimensions();
        broadcast(player);
        return true;
    }

    /** 把 identity 里的姓名/性别/职业/亲属关系写到指定族谱节点上。 */
    private static void applyIdentity(
            ServerLevel level, FamilyTreeNode node, Entity link, FamilyTree tree, CompoundTag identity) {
        String newName = identity.getString(SkinData.MCA_NAME);
        if (newName.isBlank()) {
            newName = identity.getString("Name");
        }
        if (!newName.isBlank()) {
            node.setName(newName);
            if (link instanceof ServerPlayer player) {
                // 名牌、聊天和第三人称显示都要跟着变，否则外观换了名字还是原来的。
                player.setCustomName(Component.literal(newName));
                player.refreshDisplayName();
            }
        }
        node.setGender(parseGender(identity.getString(SkinData.MCA_GENDER)));
        node.setProfession(parseProfession(identity.getString(SkinData.MCA_PROFESSION)));

        node.updatePartner(null, RelationshipState.SINGLE);
        UUID partner = uuid(identity, SkinData.MCA_PARTNER);
        RelationshipState state = parseRelationship(identity.getString(SkinData.MCA_RELATION));
        if (valid(partner)) {
            linkPartner(level, node, link, partner, state, tree);
        } else {
            node.setRelationshipState(state);
        }

        node.removeFather();
        node.removeMother();
        tree.getOrEmpty(uuid(identity, SkinData.MCA_FATHER)).ifPresent(node::setFather);
        tree.getOrEmpty(uuid(identity, SkinData.MCA_MOTHER)).ifPresent(node::setMother);

        node.children().clear();
        ListTag children = identity.getList(SkinData.MCA_CHILDREN, 8);
        String owner = identity.getString(SkinData.OWNER_UUID);
        for (int i = 0; i < children.size(); i++) {
            UUID childId = parse(children.getString(i));
            if (childId == null) {
                continue;
            }
            node.addChild(childId);
            tree.getOrEmpty(childId).ifPresent(child -> {
                if (isSame(child.father(), owner)) {
                    child.setFather(node);
                } else if (isSame(child.mother(), owner)) {
                    child.setMother(node);
                }
            });
        }
    }

    private static void linkPartner(
            ServerLevel level,
            FamilyTreeNode node,
            Entity link,
            UUID partnerId,
            RelationshipState state,
            FamilyTree tree) {
        Entity partnerEntity = findEntity(level, partnerId);
        RelationshipState mine = relativeState(partnerEntity, state);
        if (partnerEntity != null) {
            node.updatePartner(partnerEntity, mine);
            FamilyTreeNode other = tree.getOrEmpty(partnerId).orElse(null);
            if (other != null) {
                other.updatePartner(link, relativeState(link, mine));
            }
        } else {
            tree.getOrEmpty(partnerId).ifPresent(other -> {
                node.updatePartner(other);
                other.updatePartner(link, relativeState(link, mine));
            });
            node.setRelationshipState(mine);
        }
    }

    /** 把“我是谁的配偶”换成对方视角的状态。 */
    private static RelationshipState relativeState(Entity partner, RelationshipState fallback) {
        if (fallback != RelationshipState.MARRIED_TO_PLAYER
                && fallback != RelationshipState.MARRIED_TO_VILLAGER) {
            return fallback;
        }
        if (partner == null) {
            return fallback;
        }
        return partner instanceof Player
                ? RelationshipState.MARRIED_TO_PLAYER
                : RelationshipState.MARRIED_TO_VILLAGER;
    }

    public static CompoundTag identityOf(FamilyTreeNode node) {
        CompoundTag identity = new CompoundTag();
        identity.putString(SkinData.MCA_NAME, node.getName());
        identity.putString(SkinData.MCA_GENDER, node.gender() == null ? "" : node.gender().name());
        identity.putString(SkinData.MCA_PROFESSION, SkinData.professionId(node.getProfession()));
        identity.putString(SkinData.MCA_RELATION,
                node.getRelationshipState() == null ? "" : node.getRelationshipState().name());
        identity.putString(SkinData.MCA_PARTNER, uuidString(node.partner()));
        identity.putString(SkinData.MCA_FATHER, uuidString(node.father()));
        identity.putString(SkinData.MCA_MOTHER, uuidString(node.mother()));
        ListTag children = new ListTag();
        for (UUID child : node.children()) {
            children.add(StringTag.valueOf(child.toString()));
        }
        identity.put(SkinData.MCA_CHILDREN, children);
        identity.putString(SkinData.OWNER_UUID, node.id().toString());
        return identity;
    }

    /** 收集一只村民的完整本体信息：身体 NBT + 坐标 + 维度 + 类型 + 家庭关系。 */
    public static CompoundTag villagerTag(VillagerEntityMCA villager) {
        CompoundTag info = new CompoundTag();
        info.putUUID("UUID", villager.getUUID());
        ResourceLocation typeId = BuiltInRegistries.ENTITY_TYPE.getKey(villager.getType());
        info.putString("Type", typeId == null ? "" : typeId.toString());
        info.putString("Dim", villager.level().dimension().location().toString());
        info.putDouble("X", villager.getX());
        info.putDouble("Y", villager.getY());
        info.putDouble("Z", villager.getZ());
        info.putFloat("Yaw", villager.getYRot());
        info.putFloat("Pitch", villager.getXRot());
        info.putBoolean("Removed", false);

        CompoundTag body = new CompoundTag();
        villager.addAdditionalSaveData(body);
        info.put("Body", body);

        EntityRelationship relationship = EntityRelationship.of(villager).orElse(null);
        if (relationship != null) {
            info.put("Identity", identityOf(relationship.getFamilyEntry()));
        }
        return info;
    }

    /** 用道具捕获一只 MCA 村民，返回可直接穿戴的身份道具。 */
    public static ItemStack captureVillager(Entity target) {
        if (!(target instanceof VillagerEntityMCA villager)) {
            return ItemStack.EMPTY;
        }
        EntityRelationship relationship = EntityRelationship.of(villager).orElse(null);
        if (relationship == null) {
            return ItemStack.EMPTY;
        }
        CompoundTag info = villagerTag(villager);
        FamilyTreeNode node = relationship.getFamilyEntry();
        ItemStack skin = SkinData.createVillagerSkin(node.getName(), node.id(), info);
        // 这份道具就是这具身体；原实体先离开世界，穿戴/解除时再放回。
        villager.discard();
        return skin;
    }

    /** 按快照还原玩家并放回被占用的村民。 */
    public static boolean release(ServerPlayer player) {
        if (player == null || !isPossessed(player)) {
            return false;
        }
        CompoundTag box = player.getPersistentData().getCompound(SNAPSHOT_KEY);
        Set<UUID> children = new HashSet<>();
        ListTag list = box.getList("Children", 8);
        for (int i = 0; i < list.size(); i++) {
            UUID id = parse(list.getString(i));
            if (id != null) {
                children.add(id);
            }
        }
        Snapshot snapshot = new Snapshot(
                box.getCompound("EntityData"),
                box.getBoolean("EntityDataSet"),
                box.getString("Name"),
                parseGender(box.getString("Gender")),
                parseProfession(box.getString("Profession")),
                parseRelationship(box.getString("Relationship")),
                parse(box.getString("Partner")),
                parse(box.getString("Father")),
                parse(box.getString("Mother")),
                children,
                box.contains(VILLAGER_KEY, 10) ? box.getCompound(VILLAGER_KEY).copy() : null
        );
        restore(player, snapshot);
        player.getPersistentData().remove(SNAPSHOT_KEY);
        return true;
    }

    /** 每 20 tick 检查一次：穿戴状态下道具不在头盔栏就自动还原，避免永久锁死身份。 */
    public static void tick(ServerPlayer player) {
        if (!isPossessed(player) || !MODE_ITEM.equals(modeOf(player))) {
            return;
        }
        String token = tokenOf(player);
        ItemStack helmet = player.getItemBySlot(EquipmentSlot.HEAD);
        if (!SkinData.isSkin(helmet) || !token.equals(SkinData.token(helmet))) {
            release(player);
        }
    }

    private static void respawnVillager(
            ServerPlayer player, CompoundTag info, FamilyTree tree, UUID villagerId, UUID playerPartner) {
        if (villagerId == null) {
            return;
        }
        CompoundTag identity = info.getCompound("Identity");
        ServerLevel level = levelOf(player, info.getString("Dim"));
        Entity entity = level.getEntity(villagerId);

        if (entity == null && info.getBoolean("Removed")) {
            ResourceLocation typeId = ResourceLocation.tryParse(info.getString("Type"));
            EntityType<?> type = typeId == null ? null : BuiltInRegistries.ENTITY_TYPE.get(typeId);
            if (type != null) {
                Entity spawned = type.create(level);
                if (spawned != null) {
                    if (info.contains("Body", 10)) {
                        spawned.load(info.getCompound("Body").copy());
                    }
                    spawned.setUUID(villagerId);
                    spawned.moveTo(
                            info.getDouble("X"),
                            info.getDouble("Y"),
                            info.getDouble("Z"),
                            info.getFloat("Yaw"),
                            info.getFloat("Pitch"));
                    if (level.addFreshEntity(spawned)) {
                        entity = spawned;
                    }
                }
            }
        }

        FamilyTreeNode node = tree.getOrEmpty(villagerId).orElseGet(() -> tree.getOrCreate(
                villagerId,
                identity.getString(SkinData.MCA_NAME),
                parseGender(identity.getString(SkinData.MCA_GENDER))));

        // 如果玩家原本的配偶就是这具身体，解除身份同步后两个人应该继续是配偶，不能把村民指向自己。
        if (villagerId.equals(playerPartner)) {
            node.setName(identity.getString(SkinData.MCA_NAME));
            node.setGender(parseGender(identity.getString(SkinData.MCA_GENDER)));
            node.setProfession(parseProfession(identity.getString(SkinData.MCA_PROFESSION)));
            return;
        }
        applyIdentity(level, node, entity, tree, identity);
    }

    private static void storeSnapshot(
            ServerPlayer player, Snapshot before, String mode, String token, CompoundTag villagerInfo) {
        CompoundTag box = new CompoundTag();
        box.putString(MODE_KEY, mode);
        box.putString(TOKEN_KEY, token);
        box.put("EntityData", before.entityData().copy());
        box.putBoolean("EntityDataSet", before.entityDataSet());
        box.putString("Name", before.name() == null ? "" : before.name());
        box.putString("Gender", before.gender() == null ? "" : before.gender().name());
        box.putString("Profession", SkinData.professionId(before.profession()));
        box.putString("Relationship",
                before.relationshipState() == null ? "" : before.relationshipState().name());
        box.putString("Partner", uuidString(before.partner()));
        box.putString("Father", uuidString(before.father()));
        box.putString("Mother", uuidString(before.mother()));
        box.put("Children", SkinData.childrenTag(before.children()));
        if (villagerInfo != null) {
            box.put(VILLAGER_KEY, villagerInfo.copy());
        }
        player.getPersistentData().put(SNAPSHOT_KEY, box);
    }

    private static VillagerProfession parseProfession(String value) {
        if (value == null || value.isBlank()) {
            return VillagerProfession.NONE;
        }
        ResourceLocation id = ResourceLocation.tryParse(value);
        if (id == null) {
            return VillagerProfession.NONE;
        }
        VillagerProfession profession = BuiltInRegistries.VILLAGER_PROFESSION.get(id);
        return profession == null ? VillagerProfession.NONE : profession;
    }

    private static Gender parseGender(String value) {
        if (value == null || value.isBlank()) {
            return Gender.UNASSIGNED;
        }
        try {
            return Gender.valueOf(value);
        } catch (IllegalArgumentException ignored) {
            return Gender.UNASSIGNED;
        }
    }

    private static RelationshipState parseRelationship(String value) {
        if (value == null || value.isBlank()) {
            return RelationshipState.SINGLE;
        }
        try {
            return RelationshipState.valueOf(value);
        } catch (IllegalArgumentException ignored) {
            return RelationshipState.SINGLE;
        }
    }

    private static UUID parse(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            return UUID.fromString(value);
        } catch (IllegalArgumentException ignored) {
            return null;
        }
    }

    private static UUID uuid(CompoundTag tag, String key) {
        if (tag == null || !tag.contains(key)) {
            return null;
        }
        try {
            return tag.getUUID(key);
        } catch (Exception ignored) {
            return parse(tag.getString(key));
        }
    }

    private static UUID identityPartner(CompoundTag info) {
        CompoundTag identity = info.getCompound("Identity");
        return uuid(identity, SkinData.MCA_PARTNER);
    }

    private static String uuidString(UUID id) {
        return id == null ? "" : id.toString();
    }

    private static boolean isSame(UUID id, String raw) {
        return id != null && raw != null && id.toString().equalsIgnoreCase(raw);
    }

    /** 通知同维度客户端丢掉指定玩家的村民外观缓存。 */
    private static void clearClientData(ServerPlayer player) {
        ClearPayload payload = new ClearPayload(player.getUUID());
        for (ServerPlayer other : player.serverLevel().players()) {
            PacketDistributor.sendToPlayer(other, payload);
        }
    }

    /** 把玩家当前村民外观广播给同维度客户端，否则第三人称看不到皮肤变化。 */
    public static void broadcast(ServerPlayer player) {
        PlayerSaveData data = PlayerSaveData.get(player);
        CompoundTag nbt = data.getEntityData();
        if (nbt == null) {
            return;
        }
        for (ServerPlayer other : player.serverLevel().players()) {
            Network.sendToPlayer(new PlayerDataMessage(player.getUUID(), nbt), other);
        }
    }

    private static boolean valid(UUID id) {
        return id != null && !id.equals(Util.NIL_UUID);
    }

    private static Entity findEntity(ServerLevel level, UUID id) {
        if (id == null) {
            return null;
        }
        Entity entity = level.getEntity(id);
        if (entity == null && level.getServer() != null) {
            entity = level.getServer().getPlayerList().getPlayer(id);
        }
        return entity;
    }

    private static ServerLevel levelOf(ServerPlayer player, String dimension) {
        MinecraftServer server = player.getServer();
        ResourceLocation id = ResourceLocation.tryParse(dimension);
        if (server != null && id != null) {
            ServerLevel level = server.getLevel(ResourceKey.create(Registries.DIMENSION, id));
            if (level != null) {
                return level;
            }
        }
        return player.serverLevel();
    }

    public static List<String> debugSummary(ServerPlayer player) {
        if (!isPossessed(player)) {
            return List.of();
        }
        CompoundTag box = player.getPersistentData().getCompound(SNAPSHOT_KEY);
        return List.of(
                "mode=" + box.getString(MODE_KEY),
                "name=" + box.getString("Name"),
                "entityDataSet=" + box.getBoolean("EntityDataSet"),
                "villager=" + box.contains(VILLAGER_KEY, 10)
        );
    }
}
'''

SKIN_DATA = r'''package @PACKAGE@.data;

import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.nbt.ListTag;
import net.minecraft.nbt.StringTag;
import net.minecraft.nbt.Tag;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.npc.VillagerProfession;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.item.component.ResolvableProfile;
import @PACKAGE@.ModItems;

import java.util.ArrayList;
import java.util.List;
import java.util.UUID;

/**
 * 身份道具的持久化数据。
 *
 * <p>所有字段都写在 CUSTOM_DATA 里，不依赖任何客户端状态，服务器重启后仍可还原。
 * 玩家皮肤本身由 Minecraft 的 PROFILE 组件承载，客户端据此下载并渲染；
 * 村民数据则用 mca_body / mca_* 系列字段保存，穿戴时整体写进 MCA 的 PlayerSaveData。</p>
 */
public final class SkinData {
    public static final String OWNER_UUID = "owner_uuid";
    public static final String OWNER_NAME = "owner_name";
    public static final String TOKEN = "skin_token";
    public static final String MODEL = "skin_model";
    public static final String MCA_BODY = "mca_body";
    public static final String MCA_NAME = "mca_name";
    public static final String MCA_GENDER = "mca_gender";
    public static final String MCA_PROFESSION = "mca_profession";
    public static final String MCA_RELATION = "mca_relation";
    public static final String MCA_PARTNER = "mca_partner";
    public static final String MCA_FATHER = "mca_father";
    public static final String MCA_MOTHER = "mca_mother";
    public static final String MCA_CHILDREN = "mca_children";
    /** 村民本体信息：身体 NBT、坐标、维度、实体类型、家庭关系。 */
    public static final String MCA_VILLAGER = "mca_villager";

    private SkinData() {
    }

    public static CompoundTag tag(ItemStack stack) {
        return stack.getOrDefault(DataComponents.CUSTOM_DATA, CustomData.EMPTY).copyTag();
    }

    public static void setTag(ItemStack stack, CompoundTag tag) {
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
    }

    public static boolean isSkin(ItemStack stack) {
        return !stack.isEmpty() && stack.is(ModItems.PLAYER_SKIN.get()) && !token(stack).isEmpty();
    }

    public static String token(ItemStack stack) {
        return tag(stack).getString(TOKEN);
    }

    public static String name(ItemStack stack) {
        String name = tag(stack).getString(OWNER_NAME);
        return name.isBlank() ? "未知玩家" : name;
    }

    public static UUID owner(ItemStack stack) {
        String raw = tag(stack).getString(OWNER_UUID);
        if (raw.isBlank()) {
            return null;
        }
        try {
            return UUID.fromString(raw);
        } catch (IllegalArgumentException ignored) {
            return null;
        }
    }

    public static CompoundTag mcaBody(ItemStack stack) {
        CompoundTag tag = tag(stack);
        return tag.contains(MCA_BODY, Tag.TAG_COMPOUND) ? tag.getCompound(MCA_BODY).copy() : null;
    }

    public static boolean isVillagerSkin(ItemStack stack) {
        return mcaBody(stack) != null;
    }

    public static ResolvableProfile profile(ItemStack stack) {
        return stack.get(DataComponents.PROFILE);
    }

    /** 从玩家快照生成一份空的身份道具；调用方负责放进背包或掉落。 */
    public static ItemStack createPlayerSkin(ServerPlayer target) {
        ItemStack stack = new ItemStack(ModItems.PLAYER_SKIN.get());
        CompoundTag tag = new CompoundTag();
        tag.putString(OWNER_UUID, target.getUUID().toString());
        tag.putString(OWNER_NAME, target.getGameProfile().getName());
        tag.putString(TOKEN, UUID.randomUUID().toString());
        tag.putString(MODEL, "default");
        setTag(stack, tag);
        stack.set(DataComponents.PROFILE, new ResolvableProfile(target.getGameProfile()));
        return stack;
    }

    /**
     * 从 MCA 村民生成身份道具：保存完整身体 NBT + 家庭关系 + 原始位置。
     *
     * @param name     村民姓名，用于物品显示
     * @param id       村民 UUID
     * @param villager 由 {@code McaCompat.villagerTag} 生成的完整村民信息
     */
    public static ItemStack createVillagerSkin(String name, UUID id, CompoundTag villager) {
        ItemStack stack = new ItemStack(ModItems.PLAYER_SKIN.get());
        CompoundTag tag = new CompoundTag();
        tag.putString(OWNER_UUID, id == null ? "" : id.toString());
        tag.putString(OWNER_NAME, name == null || name.isBlank() ? "凡家村民" : name);
        tag.putString(TOKEN, UUID.randomUUID().toString());
        tag.putString(MODEL, "default");
        tag.putString(MCA_NAME, name == null ? "" : name);
        CompoundTag info = villager == null ? new CompoundTag() : villager.copy();
        tag.put(MCA_VILLAGER, info);
        if (info.contains("Body", Tag.TAG_COMPOUND)) {
            tag.put(MCA_BODY, info.getCompound("Body").copy());
        }
        CompoundTag identity = info.contains("Identity", Tag.TAG_COMPOUND)
                ? info.getCompound("Identity")
                : null;
        if (identity != null) {
            for (String key : List.of(MCA_GENDER, MCA_PROFESSION, MCA_RELATION, MCA_PARTNER, MCA_FATHER, MCA_MOTHER)) {
                if (identity.contains(key)) {
                    tag.put(key, identity.get(key).copy());
                }
            }
            if (identity.contains(MCA_CHILDREN, Tag.TAG_LIST)) {
                tag.put(MCA_CHILDREN, identity.getList(MCA_CHILDREN, Tag.TAG_STRING).copy());
            }
        }
        setTag(stack, tag);
        return stack;
    }

    /** 身份道具里保存的村民本体信息；玩家皮肤返回 null。 */
    public static CompoundTag villager(ItemStack stack) {
        CompoundTag tag = tag(stack);
        return tag.contains(MCA_VILLAGER, Tag.TAG_COMPOUND)
                ? tag.getCompound(MCA_VILLAGER).copy()
                : null;
    }

    public static CompoundTag identityOf(ItemStack stack) {
        CompoundTag tag = tag(stack);
        CompoundTag identity = new CompoundTag();
        for (String key : List.of(MCA_NAME, OWNER_UUID, MCA_GENDER, MCA_PROFESSION, MCA_RELATION,
                MCA_PARTNER, MCA_FATHER, MCA_MOTHER, MCA_CHILDREN)) {
            if (tag.contains(key)) {
                identity.put(key, tag.get(key).copy());
            }
        }
        return identity;
    }

    public static List<UUID> children(ItemStack stack) {
        List<UUID> result = new ArrayList<>();
        ListTag list = tag(stack).getList(MCA_CHILDREN, Tag.TAG_STRING);
        for (int i = 0; i < list.size(); i++) {
            try {
                result.add(UUID.fromString(list.getString(i)));
            } catch (IllegalArgumentException ignored) {
                // 损坏的单个 UUID 不应该让整张皮失效。
            }
        }
        return result;
    }

    public static ListTag childrenTag(Iterable<UUID> ids) {
        ListTag list = new ListTag();
        for (UUID id : ids) {
            list.add(StringTag.valueOf(id.toString()));
        }
        return list;
    }

    public static String professionId(VillagerProfession profession) {
        if (profession == null) {
            return "";
        }
        ResourceLocation id = net.minecraft.core.registries.BuiltInRegistries.VILLAGER_PROFESSION.getKey(profession);
        return id == null ? "" : id.toString();
    }
}
'''

SKIN_BLADE = r'''package @PACKAGE@.item;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionResultHolder;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.level.Level;
import @PACKAGE@.compat.McaCompat;
import @PACKAGE@.data.SkinData;

import java.util.List;

/**
 * 皮肤道具：潜行右键玩家/凡家村民，复制对方外观生成可穿戴道具。
 * 非潜行右键自己可以解除当前同步。
 */
public class SkinBladeItem extends Item {
    public SkinBladeItem(Properties properties) {
        super(properties);
    }

    @Override
    public InteractionResult interactLivingEntity(
            ItemStack stack, Player player, LivingEntity target, InteractionHand hand) {
        if (target == player && player.level().isClientSide()) {
            return InteractionResult.SUCCESS;
        }
        if (player.level().isClientSide()) {
            return player.isShiftKeyDown() && (McaCompat.isMcaVillager(target) || target instanceof Player)
                    ? InteractionResult.SUCCESS
                    : InteractionResult.PASS;
        }
        if (!(player instanceof ServerPlayer serverPlayer)) {
            return InteractionResult.PASS;
        }
        if (target == player) {
            return McaCompat.release(serverPlayer) ? InteractionResult.SUCCESS : InteractionResult.PASS;
        }
        if (!player.isShiftKeyDown()) {
            return InteractionResult.PASS;
        }

        ItemStack skin;
        if (McaCompat.isMcaVillager(target)) {
            skin = McaCompat.captureVillager(target);
        } else if (target instanceof ServerPlayer victim) {
            skin = SkinData.createPlayerSkin(victim);
        } else {
            return InteractionResult.PASS;
        }
        if (skin.isEmpty()) {
            return InteractionResult.PASS;
        }

        if (!player.getInventory().add(skin)) {
            player.drop(skin, false);
        }
        stack.hurtAndBreak(1, player, EquipmentSlot.MAINHAND);
        serverPlayer.displayClientMessage(Component.literal("已剥下 " + SkinData.name(skin) + " 的身份道具"), false);
        return InteractionResult.SUCCESS;
    }

    @Override
    public InteractionResultHolder<ItemStack> use(Level level, Player player, InteractionHand hand) {
        ItemStack stack = player.getItemInHand(hand);
        if (!level.isClientSide
                && player.isShiftKeyDown()
                && player instanceof ServerPlayer serverPlayer
                && McaCompat.release(serverPlayer)) {
            return InteractionResultHolder.success(stack);
        }
        return InteractionResultHolder.pass(stack);
    }

    @Override
    public void appendHoverText(ItemStack stack, Item.TooltipContext context, List<Component> lines, TooltipFlag flag) {
        lines.add(Component.literal("潜行右键玩家或凡家村民：取得身份道具").withStyle(ChatFormatting.GRAY));
        lines.add(Component.literal("潜行右键空气：解除当前同步").withStyle(ChatFormatting.DARK_GRAY));
    }
}
'''

HYPNOSIS_PHONE = r'''package @PACKAGE@.item;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionResultHolder;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.level.Level;
import @PACKAGE@.compat.McaCompat;
import @PACKAGE@.data.SkinData;

import java.util.List;

/**
 * 身份道具：右键凡家村民直接接管身份，姓名、职业、关系和外观全部同步。
 * 潜行右键自己解除同步；对普通玩家使用时取得对方的皮肤并立即穿上。
 */
public class HypnosisPhoneItem extends Item {
    public HypnosisPhoneItem(Properties properties) {
        super(properties);
    }

    @Override
    public InteractionResult interactLivingEntity(
            ItemStack stack, Player player, LivingEntity target, InteractionHand hand) {
        if (target == player) {
            return InteractionResult.PASS;
        }
        if (player.level().isClientSide()) {
            // 客户端也要给出成功结果，否则会被判定成“道具无法使用”。
            return McaCompat.isMcaVillager(target) || target instanceof Player
                    ? InteractionResult.SUCCESS
                    : InteractionResult.PASS;
        }
        if (!(player instanceof ServerPlayer serverPlayer)) {
            return InteractionResult.PASS;
        }
        if (player.isShiftKeyDown()) {
            return McaCompat.release(serverPlayer) ? InteractionResult.SUCCESS : InteractionResult.PASS;
        }
        if (McaCompat.isMcaVillager(target)) {
            return McaCompat.possess(serverPlayer, target)
                    ? InteractionResult.SUCCESS
                    : InteractionResult.PASS;
        }
        if (target instanceof ServerPlayer victim) {
            ItemStack skin = SkinData.createPlayerSkin(victim);
            ItemStack old = serverPlayer.getItemBySlot(EquipmentSlot.HEAD);
            if (!old.isEmpty()) {
                if (!serverPlayer.getInventory().add(old.copy())) {
                    serverPlayer.drop(old.copy(), false);
                }
            }
            ItemStack equipped = skin.copy();
            serverPlayer.setItemSlot(EquipmentSlot.HEAD, equipped);
            McaCompat.wear(serverPlayer, equipped);
            serverPlayer.displayClientMessage(
                    Component.literal("已套上 " + SkinData.name(equipped) + " 的皮肤"), false);
            return InteractionResult.SUCCESS;
        }
        return InteractionResult.PASS;
    }

    @Override
    public InteractionResultHolder<ItemStack> use(Level level, Player player, InteractionHand hand) {
        ItemStack stack = player.getItemInHand(hand);
        if (!level.isClientSide
                && player.isShiftKeyDown()
                && player instanceof ServerPlayer serverPlayer
                && McaCompat.release(serverPlayer)) {
            return InteractionResultHolder.success(stack);
        }
        return InteractionResultHolder.pass(stack);
    }

    @Override
    public void appendHoverText(ItemStack stack, Item.TooltipContext context, List<Component> lines, TooltipFlag flag) {
        lines.add(Component.literal("右键凡家村民：接管身份并继承关系").withStyle(ChatFormatting.GRAY));
        lines.add(Component.literal("右键玩家：穿上对方皮肤").withStyle(ChatFormatting.GRAY));
        lines.add(Component.literal("潜行右键自己：解除同步").withStyle(ChatFormatting.DARK_GRAY));
    }
}
'''

PLAYER_SKIN = r'''package @PACKAGE@.item;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResultHolder;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Equipable;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.level.Level;
import @PACKAGE@.compat.McaCompat;
import @PACKAGE@.data.SkinData;

import java.util.List;

/**
 * 身份道具：右键穿到头部，外观、姓名和凡家关系一起换成目标村民的。
 * 潜行右键脱下并还原；也可放在头盔栏后由服务端自动检测。
 */
public class PlayerSkinItem extends Item implements Equipable {
    public PlayerSkinItem(Properties properties) {
        super(properties);
    }

    @Override
    public EquipmentSlot getEquipmentSlot() {
        return EquipmentSlot.HEAD;
    }

    @Override
    public InteractionResultHolder<ItemStack> use(Level level, Player player, InteractionHand hand) {
        ItemStack stack = player.getItemInHand(hand);
        if (!SkinData.isSkin(stack)) {
            return InteractionResultHolder.pass(stack);
        }
        if (player.isShiftKeyDown()) {
            if (!level.isClientSide && player instanceof ServerPlayer serverPlayer) {
                // 解除优先级最高：先还原身份，再把道具还回背包。
                McaCompat.release(serverPlayer);
                ItemStack head = serverPlayer.getItemBySlot(EquipmentSlot.HEAD);
                if (ItemStack.isSameItemSameComponents(head, stack)) {
                    serverPlayer.setItemSlot(EquipmentSlot.HEAD, ItemStack.EMPTY);
                    if (!serverPlayer.getInventory().add(stack.copy())) {
                        serverPlayer.drop(stack.copy(), false);
                    }
                }
            }
            return InteractionResultHolder.sidedSuccess(stack, level.isClientSide);
        }

        ItemStack oldHead = player.getItemBySlot(EquipmentSlot.HEAD);
        InteractionResultHolder<ItemStack> result = swapWithEquipmentSlot(this, level, player, hand);
        if (!level.isClientSide && player instanceof ServerPlayer serverPlayer) {
            ItemStack worn = serverPlayer.getItemBySlot(EquipmentSlot.HEAD);
            if (SkinData.isSkin(worn)) {
                McaCompat.wear(serverPlayer, worn);
            } else if (SkinData.isSkin(oldHead)) {
                // 道具被换下来又没有新道具穿上：恢复原身份。
                McaCompat.release(serverPlayer);
            }
        }
        return result;
    }

    @Override
    public Component getName(ItemStack stack) {
        String name = SkinData.name(stack);
        return Component.literal(SkinData.isVillagerSkin(stack) ? name + "的身份道具" : name + "的皮肤");
    }

    @Override
    public void appendHoverText(ItemStack stack, Item.TooltipContext context, List<Component> lines, TooltipFlag flag) {
        lines.add(Component.literal("主人：" + SkinData.name(stack)).withStyle(ChatFormatting.GRAY));
        lines.add(Component.literal("右键穿戴，潜行右键脱下").withStyle(ChatFormatting.DARK_GRAY));
        if (SkinData.isVillagerSkin(stack)) {
            lines.add(Component.literal("同步后姓名、职业和家庭关系与村民一致")
                    .withStyle(ChatFormatting.LIGHT_PURPLE));
        }
    }
}
'''

CLIENT_SKIN_CACHE = r'''package @PACKAGE@.client;

import net.conczin.mca.MCAClient;
import net.minecraft.client.Minecraft;
import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.resources.PlayerSkin;
import net.minecraft.world.entity.EquipmentSlot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.ResolvableProfile;
import @PACKAGE@.data.SkinData;

import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 客户端皮肤缓存。
 *
 * <p>玩家穿戴的道具会随头部装备同步到所有客户端，因此这里不需要额外的网络包：
 * 读取头盔栏道具里的 PROFILE，交给原版 SkinManager 下载纹理即可。
 * MCA 村民形态优先，正在被 MCA 渲染的玩家不覆盖，避免两套外观打架。</p>
 */
public final class ClientSkinCache {
    private static final Map<UUID, Entry> CACHE = new ConcurrentHashMap<>();

    private ClientSkinCache() {
    }

    private record Entry(String token, CompletableFuture<PlayerSkin> future) {
    }

    public static PlayerSkin skinFor(AbstractClientPlayer player) {
        ItemStack head = player.getItemBySlot(EquipmentSlot.HEAD);
        if (!SkinData.isSkin(head)) {
            return null;
        }
        // 被 MCA 接管身份的玩家由 MCA 渲染，别抢它的外观。
        if (MCAClient.getPlayerData(player.getUUID()).isPresent()) {
            return null;
        }
        ResolvableProfile profile = SkinData.profile(head);
        if (profile == null || profile.gameProfile().getProperties().isEmpty()) {
            return null;
        }
        String token = SkinData.token(head);
        UUID id = player.getUUID();
        Entry entry = CACHE.get(id);
        if (entry == null || !entry.token().equals(token)) {
            entry = new Entry(
                    token,
                    Minecraft.getInstance().getSkinManager().getOrLoad(profile.gameProfile())
            );
            CACHE.put(id, entry);
        }
        // 纹理是异步下载的，没下载好时返回 null 让原版先用默认皮肤。
        return entry.future().getNow(null);
    }

    public static void clear() {
        CACHE.clear();
    }
}
'''

CLIENT_EVENTS = r'''package @PACKAGE@.client;

import net.neoforged.api.distmarker.Dist;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.client.event.ClientPlayerNetworkEvent;
import @PACKAGE@.@CLASS@;

/**
 * 客户端生命周期：退出世界时清掉皮肤缓存，避免换服/重进后残留旧纹理。
 */
@EventBusSubscriber(modid = @CLASS@.MOD_ID, value = Dist.CLIENT)
public final class ClientEvents {
    private ClientEvents() {
    }

    @SubscribeEvent
    public static void onLoggingOut(ClientPlayerNetworkEvent.LoggingOut event) {
        ClientSkinCache.clear();
    }
}
'''

ABSTRACT_CLIENT_PLAYER_MIXIN = r'''package @PACKAGE@.mixin.client;

import net.minecraft.client.player.AbstractClientPlayer;
import net.minecraft.client.resources.PlayerSkin;
import @PACKAGE@.client.ClientSkinCache;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

/**
 * 让穿戴身份道具的玩家在客户端使用目标村民的皮肤。
 * 只拦截 getSkin，纹理和 slim/wide 模型会一起生效，不需要额外改渲染器。
 */
@Mixin(AbstractClientPlayer.class)
public abstract class AbstractClientPlayerMixin {
    @Inject(method = "getSkin", at = @At("HEAD"), cancellable = true)
    private void poh$skin(CallbackInfoReturnable<PlayerSkin> cir) {
        PlayerSkin skin = ClientSkinCache.skinFor((AbstractClientPlayer) (Object) this);
        if (skin != null) {
            cir.setReturnValue(skin);
        }
    }
}
'''

SERVER_EVENTS = r'''package @PACKAGE@.event;

import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.neoforged.bus.api.SubscribeEvent;
import net.neoforged.fml.common.EventBusSubscriber;
import net.neoforged.neoforge.event.entity.player.PlayerEvent;
import net.neoforged.neoforge.event.entity.player.PlayerInteractEvent;
import net.neoforged.neoforge.event.tick.ServerTickEvent;
import @PACKAGE@.@CLASS@;
import @PACKAGE@.compat.McaCompat;

/**
 * 服务端周期检查与道具交互兜底。
 *
 * 身份道具从头盔栏被拿走、被替换或玩家死亡掉落时，必须自动还原身份，
 * 否则玩家会永久顶着村民的名字和关系。这里每 20 tick 检查一次。
 *
 * <p>MCA 村民的 {@code mobInteract} 会先消费右键（打开对话/交易），
 * 导致物品自己的 {@code interactLivingEntity} 根本不会被调用。
 * 所以必须在 {@link PlayerInteractEvent.EntityInteract} 阶段抢先处理，
 * 处理成功时取消事件，避免 MCA 再弹一次 GUI。</p>
 */
@EventBusSubscriber(modid = @CLASS@.MOD_ID)
public final class ServerEvents {
    private ServerEvents() {
    }

    @SubscribeEvent
    public static void onEntityInteract(PlayerInteractEvent.EntityInteract event) {
        if (!(event.getTarget() instanceof LivingEntity target)) {
            return;
        }
        ItemStack stack = event.getItemStack();
        Player player = event.getEntity();
        ResourceLocation itemId = BuiltInRegistries.ITEM.getKey(stack.getItem());
        // 只接管本模组的道具，避免拦截原版物品（命名牌、桶等）与 MCA 的交互。
        if (!@CLASS@.MOD_ID.equals(itemId.getNamespace())) {
            return;
        }
        InteractionResult result = stack.getItem()
                .interactLivingEntity(stack, player, target, event.getHand());
        if (result.consumesAction()) {
            event.setCancellationResult(result);
            event.setCanceled(true);
        }
    }

    @SubscribeEvent
    public static void onServerTick(ServerTickEvent.Post event) {
        if (event.getServer().getTickCount() % 20 != 0) {
            return;
        }
        for (ServerPlayer player : event.getServer().getPlayerList().getPlayers()) {
            McaCompat.tick(player);
        }
    }

    @SubscribeEvent
    public static void onLogout(PlayerEvent.PlayerLoggedOutEvent event) {
        if (event.getEntity() instanceof ServerPlayer player) {
            McaCompat.tick(player);
        }
    }
}
'''

def render(template: str, package: str, mod_id: str, class_name: str) -> str:
    return (
        template.replace("@PACKAGE@", package)
        .replace("@MOD_ID@", mod_id)
        .replace("@CLASS@", class_name)
    )
