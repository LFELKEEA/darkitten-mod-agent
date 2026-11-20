package com.mcdev.probe;

import com.mojang.authlib.GameProfile;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.MoverType;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.common.util.FakePlayer;
import net.minecraftforge.common.util.FakePlayerFactory;
import net.minecraftforge.registries.ForgeRegistries;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 服务端假玩家：不开客户端、不建窗口，也能让一个“人物”在真实服务端里
 * 移动、挖掘、放置、攻击、使用物品。用于纯后台玩法验证。
 *
 * 假玩家不是网络连接，不会收到客户端输入；这里直接调用 Vanilla 的
 * ServerPlayer / ServerPlayerGameMode 接口，所以方块交互、实体伤害和
 * 物品消耗都走正常服务端逻辑。
 */
public final class FakePlayers {
    private static final Map<String, FakePlayer> PLAYERS = new ConcurrentHashMap<>();

    private FakePlayers() {
    }

    public static String spawn(ServerLevel level, String name, BlockPos pos, String gameMode) {
        GameProfile profile = new GameProfile(
                UUID.nameUUIDFromBytes(name.getBytes(StandardCharsets.UTF_8)), name);
        FakePlayer player = FakePlayerFactory.get(level, profile);
        player.moveTo(pos.getX() + 0.5D, pos.getY(), pos.getZ() + 0.5D, 0.0F, 0.0F);
        player.setYHeadRot(0.0F);
        setGameMode(player, gameMode);
        // 保证出生点周围区块真的加载，否则交互会静默失败。
        level.getChunk(pos);
        PLAYERS.put(name, player);
        return "spawned fake player " + name + " at " + pos + " mode=" + gameMode;
    }

    public static FakePlayer get(String name) {
        return PLAYERS.get(name);
    }

    public static List<String> names() {
        return new ArrayList<>(PLAYERS.keySet());
    }

    public static boolean remove(String name) {
        FakePlayer player = PLAYERS.remove(name);
        if (player == null) {
            return false;
        }
        try {
            player.discard();
        } catch (Throwable ignored) {
            // 假玩家已经离开时 discard 可能重复抛出，忽略即可。
        }
        return true;
    }

    public static String setGameMode(FakePlayer player, String mode) {
        GameType type = GameType.byName(mode == null ? "creative" : mode.toLowerCase(), GameType.CREATIVE);
        if (type == GameType.SPECTATOR) {
            type = GameType.CREATIVE;
        }
        player.gameMode.changeGameModeForPlayer(type);
        boolean creative = type == GameType.CREATIVE;
        player.getAbilities().instabuild = creative;
        player.getAbilities().mayfly = creative;
        player.getAbilities().flying = false;
        if (creative) {
            player.getAbilities().flying = false;
        }
        return type.getName();
    }

    public static Map<String, Object> state(FakePlayer player, double radius) {
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("name", player.getName().getString());
        d.put("id", player.getId());
        d.put("x", round(player.getX()));
        d.put("y", round(player.getY()));
        d.put("z", round(player.getZ()));
        d.put("yaw", round(player.getYRot()));
        d.put("pitch", round(player.getXRot()));
        d.put("gameMode", player.gameMode.getGameModeForPlayer().getName());
        d.put("health", round(player.getHealth()));
        d.put("maxHealth", round(player.getMaxHealth()));
        d.put("food", player.getFoodData().getFoodLevel());
        d.put("onGround", player.onGround());
        d.put("alive", player.isAlive());
        d.put("dimension", player.level().dimension().location().toString());
        d.put("inventory", inventory(player));

        AABB box = player.getBoundingBox().inflate(Math.max(1.0, radius));
        List<Map<String, Object>> nearby = new ArrayList<>();
        for (Entity entity : player.level().getEntities(player, box, Entity::isAlive)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", entity.getId());
            item.put("type", String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(entity.getType())));
            item.put("name", entity.getName().getString());
            item.put("distance", round(Math.sqrt(entity.distanceToSqr(player))));
            if (entity instanceof LivingEntity living) {
                item.put("health", round(living.getHealth()));
            }
            nearby.add(item);
        }
        nearby.sort((a, b) -> Double.compare(
                ((Number) a.get("distance")).doubleValue(),
                ((Number) b.get("distance")).doubleValue()));
        d.put("nearby", nearby);
        return d;
    }

    public static List<Map<String, Object>> inventory(FakePlayer player) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (int i = 0; i < player.getInventory().items.size(); i++) {
            ItemStack stack = player.getInventory().items.get(i);
            if (stack.isEmpty()) {
                continue;
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("slot", i);
            row.put("item", String.valueOf(ForgeRegistries.ITEMS.getKey(stack.getItem())));
            row.put("count", stack.getCount());
            rows.add(row);
        }
        return rows;
    }

    public static String look(FakePlayer player, float yaw, float pitch) {
        player.setYRot(yaw % 360.0F);
        player.setXRot(Math.max(-90.0F, Math.min(90.0F, pitch)));
        player.setYHeadRot(player.getYRot());
        return "yaw=" + player.getYRot() + " pitch=" + player.getXRot();
    }

    public static String teleport(FakePlayer player, double x, double y, double z) {
        ServerLevel level = (ServerLevel) player.level();
        BlockPos pos = BlockPos.containing(x, y, z);
        level.getChunk(pos);
        player.teleportTo(x, y, z);
        return "teleported to " + round(player.getX()) + "," + round(player.getY()) + "," + round(player.getZ());
    }

    /** 沿直线做带碰撞的位移；不阻塞服务端 tick，分步在同一个 tick 内完成。 */
    public static String walk(FakePlayer player, double dx, double dy, double dz, int steps) {
        int count = Math.max(1, Math.min(steps, 200));
        double sx = dx / count;
        double sy = dy / count;
        double sz = dz / count;
        for (int i = 0; i < count; i++) {
            player.move(MoverType.PLAYER, new Vec3(sx, sy, sz));
        }
        return "walked to " + round(player.getX()) + "," + round(player.getY()) + "," + round(player.getZ());
    }

    public static Map<String, Object> mine(FakePlayer player, BlockPos pos, int timeoutMs) {
        ServerLevel level = (ServerLevel) player.level();
        level.getChunk(pos);
        BlockState before = level.getBlockState(pos);
        // 先转向目标方块，贴近真实玩家的操作姿态。
        Vec3 center = Vec3.atCenterOf(pos);
        double dx = center.x - player.getX();
        double dy = center.y - player.getEyeY();
        double dz = center.z - player.getZ();
        double flat = Math.sqrt(dx * dx + dz * dz);
        look(player,
                (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0D),
                (float) (-Math.toDegrees(Math.atan2(dy, flat))));
        // destroyBlock 走服务端权威逻辑：创造模式即时破坏，生存模式同样由服务端
        // 结算掉落与经验；不依赖网络连接，假玩家也能用。
        boolean destroyed = player.gameMode.destroyBlock(pos);
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("x", pos.getX());
        d.put("y", pos.getY());
        d.put("z", pos.getZ());
        d.put("before", String.valueOf(ForgeRegistries.BLOCKS.getKey(before.getBlock())));
        d.put("after", String.valueOf(ForgeRegistries.BLOCKS.getKey(level.getBlockState(pos).getBlock())));
        d.put("destroyed", destroyed);
        return d;
    }

    public static Map<String, Object> place(
            FakePlayer player, BlockPos pos, String faceName, String itemId, int count) {
        ServerLevel level = (ServerLevel) player.level();
        level.getChunk(pos);
        Direction face = Direction.byName(faceName == null ? "up" : faceName.toLowerCase());
        if (face == null) {
            face = Direction.UP;
        }
        if (itemId != null && !itemId.isBlank()) {
            ResourceLocation rl = ResourceLocation.tryParse(itemId);
            Item item = rl == null ? null : ForgeRegistries.ITEMS.getValue(rl);
            if (item != null) {
                player.setItemInHand(InteractionHand.MAIN_HAND,
                        new ItemStack(item, Math.max(1, Math.min(count, 64))));
            }
        }
        ItemStack stack = player.getMainHandItem();
        Vec3 hit = Vec3.atCenterOf(pos).add(
                face.getStepX() * 0.5D, face.getStepY() * 0.5D, face.getStepZ() * 0.5D);
        BlockHitResult result = new BlockHitResult(hit, face, pos, false);
        InteractionResult used = player.gameMode.useItemOn(
                player, level, stack, InteractionHand.MAIN_HAND, result);
        player.swing(InteractionHand.MAIN_HAND);
        BlockPos target = pos.relative(face);
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("target", pos.getX() + "," + pos.getY() + "," + pos.getZ());
        d.put("face", face.getName());
        d.put("result", String.valueOf(used));
        d.put("placed", String.valueOf(ForgeRegistries.BLOCKS.getKey(level.getBlockState(target).getBlock())));
        d.put("item", stack.isEmpty() ? null : String.valueOf(ForgeRegistries.ITEMS.getKey(stack.getItem())));
        return d;
    }

    public static Map<String, Object> useItem(FakePlayer player) {
        ItemStack stack = player.getMainHandItem();
        InteractionResult result = player.gameMode.useItem(
                player, (ServerLevel) player.level(), stack, InteractionHand.MAIN_HAND);
        player.swing(InteractionHand.MAIN_HAND);
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("item", stack.isEmpty() ? null : String.valueOf(ForgeRegistries.ITEMS.getKey(stack.getItem())));
        d.put("result", String.valueOf(result));
        d.put("count", stack.getCount());
        return d;
    }

    public static Map<String, Object> give(FakePlayer player, String itemId, int count) {
        ResourceLocation rl = ResourceLocation.tryParse(itemId == null ? "" : itemId);
        Item item = rl == null ? null : ForgeRegistries.ITEMS.getValue(rl);
        if (item == null) {
            item = Items.STONE;
        }
        ItemStack stack = new ItemStack(item, Math.max(1, Math.min(count, 64)));
        boolean added = player.getInventory().add(stack);
        if (!added) {
            player.drop(stack, false);
        }
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("item", String.valueOf(ForgeRegistries.ITEMS.getKey(item)));
        d.put("count", stack.getCount());
        d.put("added", added);
        return d;
    }

    public static Map<String, Object> attack(FakePlayer player, int entityId, String targetName, double radius) {
        Entity target = null;
        if (entityId >= 0) {
            target = ((ServerLevel) player.level()).getEntity(entityId);
        }
        if (target == null && targetName != null && !targetName.isBlank()) {
            AABB box = player.getBoundingBox().inflate(Math.max(1.0, radius));
            for (Entity entity : player.level().getEntities(player, box, Entity::isAlive)) {
                if (entity.getName().getString().equalsIgnoreCase(targetName)
                        || String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(entity.getType()))
                        .equalsIgnoreCase(targetName)) {
                    target = entity;
                    break;
                }
            }
        }
        if (target == null) {
            AABB box = player.getBoundingBox().inflate(Math.max(1.0, radius));
            LivingEntity nearest = null;
            double best = Double.MAX_VALUE;
            for (LivingEntity living : player.level().getEntitiesOfClass(
                    LivingEntity.class, box, e -> e != player && e.isAlive())) {
                double dist = living.distanceToSqr(player);
                if (dist < best) {
                    best = dist;
                    nearest = living;
                }
            }
            target = nearest;
        }
        Map<String, Object> d = new LinkedHashMap<>();
        if (target == null) {
            d.put("attacked", false);
            d.put("reason", "no target in range");
            return d;
        }
        float before = target instanceof LivingEntity living ? living.getHealth() : -1.0F;
        player.attack(target);
        player.swing(InteractionHand.MAIN_HAND);
        float after = target instanceof LivingEntity living && living.isAlive()
                ? living.getHealth() : 0.0F;
        d.put("attacked", true);
        d.put("id", target.getId());
        d.put("type", String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(target.getType())));
        d.put("name", target.getName().getString());
        d.put("distance", round(Math.sqrt(target.distanceToSqr(player))));
        d.put("healthBefore", round(before));
        d.put("healthAfter", round(after));
        d.put("killed", !target.isAlive());
        return d;
    }

    // 兼容旧调用：命令行 mcdev fakeplayer spawn/use/attack 仍可使用。
    public static String spawn(ServerLevel level, String name, BlockPos pos) {
        return spawn(level, name, pos, "creative");
    }

    public static String useOn(ServerLevel level, String name, BlockPos pos) {
        FakePlayer player = PLAYERS.get(name);
        if (player == null) {
            return "no fake player " + name;
        }
        Map<String, Object> result = place(player, pos, "up", null, 1);
        return "useOn " + pos + " -> " + result.get("result");
    }

    public static String attackNearest(ServerLevel level, String name) {
        FakePlayer player = PLAYERS.get(name);
        if (player == null) {
            return "no fake player " + name;
        }
        Map<String, Object> result = attack(player, -1, "", 6.0);
        return Boolean.TRUE.equals(result.get("attacked"))
                ? "attacked " + result.get("name")
                : String.valueOf(result.get("reason"));
    }

    private static double round(double value) {
        return Math.round(value * 100.0D) / 100.0D;
    }
}
