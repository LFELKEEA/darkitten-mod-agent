package com.mcdev.probe;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.ContainerEventHandler;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.ConnectScreen;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.TitleScreen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;
import net.minecraft.client.gui.screens.inventory.InventoryScreen;
import net.minecraft.client.multiplayer.ServerData;
import net.minecraft.client.multiplayer.resolver.ServerAddress;
import net.minecraft.client.player.LocalPlayer;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.RegistryAccess;
import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ServerboundMovePlayerPacket;
import net.minecraft.network.protocol.game.ServerboundPlayerActionPacket;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.Difficulty;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.inventory.ClickType;
import net.minecraft.world.inventory.Slot;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.GameRules;
import net.minecraft.world.level.GameType;
import net.minecraft.world.level.LevelSettings;
import net.minecraft.world.level.WorldDataConfiguration;
import net.minecraft.world.level.levelgen.WorldDimensions;
import net.minecraft.world.level.levelgen.WorldOptions;
import net.minecraft.world.level.levelgen.presets.WorldPreset;
import net.minecraft.world.level.levelgen.presets.WorldPresets;
import net.minecraft.world.phys.AABB;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;
import net.minecraftforge.registries.ForgeRegistries;
import org.lwjgl.glfw.GLFW;

import java.util.Comparator;
import java.util.function.Function;

import java.io.File;
import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Field;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Callable;

/**
 * Client-side probe: UI tree, mouse clicks, screenshots.
 * Runs on the client's main (render) thread for all game access.
 */
public final class ClientProbeServer {
    private static final Gson GSON = new Gson();
    private static HttpServer server;
    private static ExecutorService executor;
    private static String token = "mcdev";

    private ClientProbeServer() {
    }

    public static synchronized void start(int port, String tokenIn) {
        if (server != null) {
            return;
        }
        token = tokenIn == null ? "" : tokenIn;
        try {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
            server.createContext("/status", ClientProbeServer::handleStatus);
            server.createContext("/language", ClientProbeServer::handleLanguage);
            server.createContext("/world/create", ClientProbeServer::handleWorldCreate);
            server.createContext("/connect", ClientProbeServer::handleConnect);
            server.createContext("/log/tail", ClientProbeServer::handleLogTail);
            server.createContext("/ui/snapshot", ClientProbeServer::handleUiSnapshot);
            server.createContext("/ui/click", ClientProbeServer::handleUiClick);
            server.createContext("/ui/click_text", ClientProbeServer::handleUiClickText);
            server.createContext("/ui/set_text", ClientProbeServer::handleUiSetText);
            server.createContext("/ui/wait", ClientProbeServer::handleUiWait);
            server.createContext("/ui/close", ClientProbeServer::handleUiClose);
            server.createContext("/ui/hover", ClientProbeServer::handleUiHover);
            server.createContext("/ui/hover_slot", ClientProbeServer::handleUiHoverSlot);
            server.createContext("/player/state", ClientProbeServer::handlePlayerState);
            server.createContext("/player/look", ClientProbeServer::handlePlayerLook);
            server.createContext("/player/move", ClientProbeServer::handlePlayerMove);
            server.createContext("/player/key", ClientProbeServer::handlePlayerKey);
            server.createContext("/player/attack", ClientProbeServer::handlePlayerAttack);
            server.createContext("/chat/send", ClientProbeServer::handleChatSend);
            server.createContext("/player/inventory", ClientProbeServer::handlePlayerInventory);
            server.createContext("/command", ClientProbeServer::handleCommand);
            server.createContext("/player/use", ClientProbeServer::handlePlayerUse);
            server.createContext("/player/use_item", ClientProbeServer::handlePlayerUseItem);
            server.createContext("/player/break", ClientProbeServer::handlePlayerBreak);
            server.createContext("/player/mine", ClientProbeServer::handlePlayerMine);
            server.createContext("/item/mainhand", ClientProbeServer::handleMainhandItem);
            server.createContext("/screenshot", ClientProbeServer::handleScreenshot);
            server.createContext("/window", ClientProbeServer::handleWindow);
            server.createContext("/quit", ClientProbeServer::handleQuit);
            executor = Executors.newFixedThreadPool(2);
            server.setExecutor(executor);
            server.start();
            McdevProbe.LOGGER.info("Mcdev Client Probe listening on 127.0.0.1:{}", port);
        } catch (IOException e) {
            McdevProbe.LOGGER.error("Failed to start Mcdev Client Probe", e);
        }
    }

    public static synchronized void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
        if (executor != null) {
            executor.shutdownNow();
            executor = null;
        }
    }

    private static void handleConnect(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        String host = strQuery(ex, "host", "127.0.0.1");
        int port = intQuery(ex, "port", 25566);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                d.put("host", host);
                d.put("port", port);
                if (mc.player != null) {
                    d.put("alreadyInWorld", true);
                    return d;
                }
                if (mc.screen != null && mc.screen.getClass().getName().contains("AccessibilityOnboarding")) {
                    mc.setScreen(new TitleScreen());
                }
                ConnectScreen.startConnecting(new TitleScreen(), mc,
                        new ServerAddress(host, port),
                        new ServerData("mcdev", host + ":" + port, false), false);
                d.put("connecting", true);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "connect failed: " + e.getMessage());
        }
    }

    private static void handleStatus(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                d.put("side", "client");
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                d.put("title", mc.screen == null ? null : mc.screen.getTitle().getString());
                d.put("fps", mc.getFps());
                d.put("width", mc.getWindow().getGuiScaledWidth());
                d.put("height", mc.getWindow().getGuiScaledHeight());
                d.put("player", mc.player == null ? null : mc.player.getName().getString());
                d.put("inWorld", mc.player != null && mc.level != null);
                d.put("language", mc.options.languageCode);
                d.put("languageSelected", mc.getLanguageManager().getSelected());
                d.put("window", BackgroundWindow.status(mc));
                d.put("dimension", mc.level == null ? null
                        : mc.level.dimension().location().toString());
                if (mc.player != null) {
                    d.put("x", round(mc.player.getX()));
                    d.put("y", round(mc.player.getY()));
                    d.put("z", round(mc.player.getZ()));
                    d.put("yaw", round(mc.player.getYRot()));
                    d.put("pitch", round(mc.player.getXRot()));
                    d.put("health", round(mc.player.getHealth()));
                    d.put("gameMode", mc.gameMode == null ? null
                            : mc.gameMode.getPlayerMode().getName());
                }
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "status failed: " + e.getMessage());
        }
    }

    private static void handleLogTail(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int n = intQuery(ex, "n", 100);
        JsonObject data = new JsonObject();
        data.add("lines", ProbeLogs.tail(n));
        ok(ex, data);
    }

    // ------------------------------------------------------------------
    // AI 游戏控制：语言、新建存档、玩家状态/移动/按键/攻击、聊天、按文字点击
    // ------------------------------------------------------------------

    private static void handleLanguage(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            try {
                ok(ex, onClient(ClientProbeServer::languageData));
            } catch (Exception e) {
                error(ex, 500, "language query failed: " + e.getMessage());
            }
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String code = normalizeLanguage(optString(body, "code", "zh_cn"));
        final boolean reload = optBool(body, "reload", true);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                mc.options.languageCode = code;
                mc.options.save();
                mc.getLanguageManager().setSelected(code);
                Map<String, Object> d = languageData();
                if (reload) {
                    try {
                        mc.reloadResourcePacks();
                        d.put("reloading", true);
                    } catch (Throwable t) {
                        d.put("reloading", false);
                        d.put("reloadError", String.valueOf(t.getMessage()));
                    }
                }
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "language change failed: " + e.getMessage());
        }
    }

    private static Map<String, Object> languageData() {
        Minecraft mc = Minecraft.getInstance();
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("code", mc.options.languageCode);
        d.put("selected", mc.getLanguageManager().getSelected());
        d.put("javaLocale", String.valueOf(mc.getLanguageManager().getJavaLocale()));
        List<Map<String, Object>> languages = new ArrayList<>();
        mc.getLanguageManager().getLanguages().forEach((key, info) -> {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("code", key);
            item.put("name", info.name());
            item.put("region", info.region());
            languages.add(item);
        });
        d.put("available", languages);
        return d;
    }

    private static void handleWorldCreate(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String name = optString(body, "name", "AI Test " + System.currentTimeMillis());
        final String mode = optString(body, "gamemode", "creative");
        final String difficulty = optString(body, "difficulty", "peaceful");
        final String worldType = optString(body, "worldType", optString(body, "world_type", "normal"));
        final String seedText = optString(body, "seed", "");
        final boolean cheats = optBool(body, "cheats", true);
        final boolean structures = optBool(body, "structures", true);
        final boolean bonusChest = optBool(body, "bonusChest", false);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player != null || mc.level != null) {
                    d.put("created", false);
                    d.put("reason", "already in world");
                    d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                    return d;
                }
                long seed;
                if (seedText.isBlank()) {
                    seed = WorldOptions.randomSeed();
                } else {
                    seed = WorldOptions.parseSeed(seedText).orElseGet(WorldOptions::randomSeed);
                }
                boolean hardcore = "hardcore".equalsIgnoreCase(mode);
                GameType type = hardcore
                        ? GameType.SURVIVAL
                        : GameType.byName(mode, GameType.CREATIVE);
                Difficulty diff = Difficulty.byName(difficulty);
                if (diff == null) {
                    diff = Difficulty.PEACEFUL;
                }
                LevelSettings settings = new LevelSettings(
                        name, type, hardcore, diff, cheats,
                        new GameRules(), WorldDataConfiguration.DEFAULT);
                WorldOptions options = new WorldOptions(seed, structures, bonusChest);
                mc.createWorldOpenFlows().createFreshLevel(
                        name, settings, options, worldDimensions(worldType));
                d.put("created", true);
                d.put("name", name);
                d.put("seed", seed);
                d.put("gamemode", type.getName());
                d.put("difficulty", diff.getKey());
                d.put("hardcore", hardcore);
                d.put("cheats", cheats);
                d.put("worldType", worldType);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "world create failed: " + e.getMessage());
        }
    }

    private static Function<RegistryAccess, WorldDimensions> worldDimensions(String worldType) {
        String type = worldType == null ? "" : worldType.trim().toLowerCase();
        ResourceKey<WorldPreset> preset = null;
        if (type.equals("flat") || type.equals("superflat")) {
            preset = WorldPresets.FLAT;
        } else if (type.equals("amplified")) {
            preset = WorldPresets.AMPLIFIED;
        } else if (type.equals("large_biomes") || type.equals("largebiomes")) {
            preset = WorldPresets.LARGE_BIOMES;
        } else if (type.equals("single_biome") || type.equals("singlebiome")) {
            preset = WorldPresets.SINGLE_BIOME_SURFACE;
        }
        if (preset == null) {
            return WorldPresets::createNormalWorldDimensions;
        }
        final ResourceKey<WorldPreset> chosen = preset;
        return access -> access.registryOrThrow(Registries.WORLD_PRESET)
                .getHolderOrThrow(chosen).value().createWorldDimensions();
    }

    private static void handlePlayerState(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int radius = Math.max(1, Math.min(64, intQuery(ex, "radius", 8)));
        try {
            ok(ex, onClient(() -> playerState(radius)));
        } catch (Exception e) {
            error(ex, 500, "player state failed: " + e.getMessage());
        }
    }

    private static Map<String, Object> playerState(int radius) {
        Minecraft mc = Minecraft.getInstance();
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("inWorld", mc.player != null && mc.level != null);
        d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
        d.put("title", mc.screen == null ? null : mc.screen.getTitle().getString());
        d.put("language", mc.options.languageCode);
        d.put("languageSelected", mc.getLanguageManager().getSelected());
        d.put("singleplayer", mc.hasSingleplayerServer());
        LocalPlayer player = mc.player;
        if (player == null || mc.level == null) {
            return d;
        }
        d.put("name", player.getName().getString());
        d.put("x", round(player.getX()));
        d.put("y", round(player.getY()));
        d.put("z", round(player.getZ()));
        d.put("yaw", round(player.getYRot()));
        d.put("pitch", round(player.getXRot()));
        d.put("eyeY", round(player.getEyeY()));
        d.put("health", round(player.getHealth()));
        d.put("maxHealth", round(player.getMaxHealth()));
        d.put("food", player.getFoodData().getFoodLevel());
        d.put("saturation", round(player.getFoodData().getSaturationLevel()));
        d.put("xpLevel", player.experienceLevel);
        d.put("onGround", player.onGround());
        d.put("flying", player.getAbilities().flying);
        d.put("creative", player.isCreative());
        d.put("gameMode", mc.gameMode == null ? null : mc.gameMode.getPlayerMode().getName());
        d.put("dimension", mc.level.dimension().location().toString());
        d.put("difficulty", mc.level.getDifficulty().getKey());
        d.put("dayTime", mc.level.getDayTime());
        ItemStack main = player.getMainHandItem();
        d.put("mainHand", main.isEmpty() ? null
                : String.valueOf(ForgeRegistries.ITEMS.getKey(main.getItem())));
        d.put("mainHandCount", main.getCount());
        d.put("attackedRecently", mc.gameMode != null && mc.gameMode.hasMissTime());
        List<Map<String, Object>> nearby = new ArrayList<>();
        AABB area = player.getBoundingBox().inflate(radius);
        List<Entity> entities = new ArrayList<>(mc.level.getEntities(player, area));
        entities.sort(Comparator.comparingDouble(entity -> entity.distanceToSqr(player)));
        int limit = Math.min(entities.size(), 30);
        for (int i = 0; i < limit; i++) {
            Entity entity = entities.get(i);
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", entity.getId());
            item.put("type", String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(entity.getType())));
            item.put("name", entity.getName().getString());
            item.put("distance", round(Math.sqrt(entity.distanceToSqr(player))));
            item.put("x", round(entity.getX()));
            item.put("y", round(entity.getY()));
            item.put("z", round(entity.getZ()));
            if (entity instanceof LivingEntity living) {
                item.put("health", round(living.getHealth()));
                item.put("maxHealth", round(living.getMaxHealth()));
            }
            nearby.add(item);
        }
        d.put("nearby", nearby);
        return d;
    }

    private static void handlePlayerLook(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final double targetX = optDouble(body, "x", Double.NaN);
        final double targetY = optDouble(body, "y", Double.NaN);
        final double targetZ = optDouble(body, "z", Double.NaN);
        final float yawIn = (float) optDouble(body, "yaw", Double.NaN);
        final float pitchIn = (float) optDouble(body, "pitch", Double.NaN);
        final int entityId = optInt(body, "entity", -1);
        final String targetName = optString(body, "target", "");
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                LocalPlayer player = mc.player;
                if (player == null) {
                    d.put("looked", false);
                    d.put("reason", "player not in world");
                    return d;
                }
                float yaw = yawIn;
                float pitch = pitchIn;
                if (!Float.isNaN(yawIn) && !Float.isNaN(pitchIn)) {
                    // 直接使用
                } else if (!Double.isNaN(targetX) && !Double.isNaN(targetY) && !Double.isNaN(targetZ)) {
                    Vec3 eye = player.getEyePosition();
                    double dx = targetX - eye.x;
                    double dy = targetY - eye.y;
                    double dz = targetZ - eye.z;
                    double horizontal = Math.sqrt(dx * dx + dz * dz);
                    yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
                    pitch = (float) (-Math.toDegrees(Math.atan2(dy, horizontal)));
                } else if (entityId >= 0 || !targetName.isBlank()) {
                    Entity target = null;
                    if (entityId >= 0 && mc.level != null) {
                        target = mc.level.getEntity(entityId);
                    }
                    if (target == null && !targetName.isBlank()) {
                        target = findEntity(mc, targetName, 32);
                    }
                    if (target == null) {
                        d.put("looked", false);
                        d.put("reason", "target not found");
                        return d;
                    }
                    Vec3 eye = player.getEyePosition();
                    double dx = target.getX() - eye.x;
                    double dy = (target.getY() + target.getBbHeight() * 0.5) - eye.y;
                    double dz = target.getZ() - eye.z;
                    double horizontal = Math.sqrt(dx * dx + dz * dz);
                    yaw = (float) (Math.toDegrees(Math.atan2(dz, dx)) - 90.0);
                    pitch = (float) (-Math.toDegrees(Math.atan2(dy, horizontal)));
                } else {
                    d.put("looked", false);
                    d.put("reason", "yaw/pitch or x/y/z or entity required");
                    return d;
                }
                player.setYRot(yaw);
                player.setXRot(pitch);
                player.setYHeadRot(yaw);
                if (mc.getConnection() != null) {
                    mc.getConnection().send(new ServerboundMovePlayerPacket.Rot(
                            yaw, pitch, player.onGround()));
                }
                d.put("looked", true);
                d.put("yaw", round(player.getYRot()));
                d.put("pitch", round(player.getXRot()));
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "look failed: " + e.getMessage());
        }
    }

    private static void handlePlayerMove(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String mode = optString(body, "mode", "teleport").toLowerCase();
        final double x = optDouble(body, "x", Double.NaN);
        final double y = optDouble(body, "y", Double.NaN);
        final double z = optDouble(body, "z", Double.NaN);
        final double dx = optDouble(body, "dx", 0);
        final double dy = optDouble(body, "dy", 0);
        final double dz = optDouble(body, "dz", 0);
        final int ms = Math.max(0, Math.min(10_000, optInt(body, "ms", 1000)));
        final String keysRaw = optString(body, "keys", "");
        final boolean sprint = optBool(body, "sprint", false);
        final boolean sneak = optBool(body, "sneak", false);
        final boolean jump = optBool(body, "jump", false);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                LocalPlayer player = mc.player;
                if (player == null || mc.level == null) {
                    d.put("moved", false);
                    d.put("reason", "player not in world");
                    return d;
                }
                d.put("start", pos(player));
                if ("stop".equals(mode)) {
                    releaseMovementKeys(mc);
                    d.put("moved", true);
                    d.put("mode", "stop");
                } else if ("relative".equals(mode)) {
                    teleport(mc, player.getX() + dx, player.getY() + dy, player.getZ() + dz, d);
                } else if ("walk".equals(mode) || "keys".equals(mode)) {
                    List<String> keys = new ArrayList<>();
                    for (String part : keysRaw.split("[,\\s]+")) {
                        if (!part.isBlank()) {
                            keys.add(part.trim());
                        }
                    }
                    if (sprint) keys.add("sprint");
                    if (sneak) keys.add("sneak");
                    if (jump) keys.add("jump");
                    applyMovementKeys(mc, keys, true);
                    d.put("moved", true);
                    d.put("mode", "walk");
                    d.put("keys", keys);
                    d.put("ms", ms);
                    d.put("pendingRelease", ms > 0);
                    return d;
                } else {
                    double tx = Double.isNaN(x) ? player.getX() : x;
                    double ty = Double.isNaN(y) ? player.getY() : y;
                    double tz = Double.isNaN(z) ? player.getZ() : z;
                    teleport(mc, tx, ty, tz, d);
                }
                d.put("end", pos(mc.player));
                return d;
            });
            if (("walk".equals(mode) || "keys".equals(mode)) && ms > 0) {
                Thread.sleep(ms);
                onClient(() -> {
                    releaseMovementKeys(Minecraft.getInstance());
                    return true;
                });
                data.put("end", onClient(() -> pos(Minecraft.getInstance().player)));
            }
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "move failed: " + e.getMessage());
        }
    }

    private static void teleport(Minecraft mc, double x, double y, double z,
                                 Map<String, Object> d) {
        LocalPlayer player = mc.player;
        if (player == null) {
            d.put("moved", false);
            d.put("reason", "player not in world");
            return;
        }
        boolean command = player.hasPermissions(2) && mc.getConnection() != null;
        if (command) {
            mc.getConnection().sendCommand(
                    String.format(java.util.Locale.ROOT, "tp @s %.3f %.3f %.3f", x, y, z));
            d.put("method", "command");
        } else {
            player.teleportTo(x, y, z);
            if (mc.getConnection() != null) {
                mc.getConnection().send(new ServerboundMovePlayerPacket.PosRot(
                        x, y, z, player.getYRot(), player.getXRot(), player.onGround()));
            }
            d.put("method", "client");
        }
        d.put("moved", true);
        d.put("target", java.util.List.of(round(x), round(y), round(z)));
    }

    private static void handlePlayerKey(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String keyName = optString(body, "key", "");
        final String action = optString(body, "action", "tap").toLowerCase();
        final int ms = Math.max(0, Math.min(10_000, optInt(body, "ms", 100)));
        if (keyName.isBlank()) {
            error(ex, 400, "key required");
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                KeyMapping mapping = resolveKeyMapping(mc, keyName);
                if (mapping == null) {
                    d.put("pressed", false);
                    d.put("reason", "unknown key: " + keyName);
                    return d;
                }
                switch (action) {
                    case "press", "down" -> mapping.setDown(true);
                    case "release", "up" -> mapping.setDown(false);
                    case "hold" -> mapping.setDown(true);
                    default -> {
                        KeyMapping.click(mapping.getKey());
                        d.put("clicked", true);
                    }
                }
                d.put("pressed", true);
                d.put("key", keyName);
                d.put("action", action);
                d.put("isDown", mapping.isDown());
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            if ("hold".equals(action) && ms > 0) {
                Thread.sleep(ms);
                onClient(() -> {
                    KeyMapping mapping = resolveKeyMapping(Minecraft.getInstance(), keyName);
                    if (mapping != null) {
                        mapping.setDown(false);
                    }
                    return true;
                });
                data.put("released", true);
            }
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "key failed: " + e.getMessage());
        }
    }

    private static void handlePlayerAttack(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final int entityId = optInt(body, "entity", optInt(body, "id", -1));
        final String targetName = optString(body, "target", "");
        final double radius = optDouble(body, "radius", 4.0);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                LocalPlayer player = mc.player;
                if (player == null || mc.level == null || mc.gameMode == null) {
                    d.put("attacked", false);
                    d.put("reason", "player not in world");
                    return d;
                }
                Entity target = null;
                if (entityId >= 0) {
                    target = mc.level.getEntity(entityId);
                }
                if (target == null && !targetName.isBlank()) {
                    target = findEntity(mc, targetName, radius);
                }
                if (target == null && mc.crosshairPickEntity != null) {
                    target = mc.crosshairPickEntity;
                }
                if (target == null) {
                    target = findNearestLiving(mc, player, radius);
                }
                if (target == null) {
                    d.put("attacked", false);
                    d.put("reason", "no target in range");
                    return d;
                }
                float before = target instanceof LivingEntity living ? living.getHealth() : -1;
                mc.gameMode.attack(player, target);
                player.swing(InteractionHand.MAIN_HAND);
                float after = target instanceof LivingEntity living ? living.getHealth() : -1;
                d.put("attacked", true);
                d.put("id", target.getId());
                d.put("type", String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(target.getType())));
                d.put("name", target.getName().getString());
                d.put("distance", round(Math.sqrt(target.distanceToSqr(player))));
                d.put("healthBefore", round(before));
                d.put("healthAfter", round(after));
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "attack failed: " + e.getMessage());
        }
    }

    private static void handleChatSend(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String text = optString(body, "text", optString(body, "message", ""));
        if (text.isBlank()) {
            error(ex, 400, "text required");
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null || mc.getConnection() == null) {
                    d.put("sent", false);
                    d.put("reason", "not connected");
                    return d;
                }
                mc.getConnection().sendChat(text);
                d.put("sent", true);
                d.put("text", text);
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "chat failed: " + e.getMessage());
        }
    }

    private static void handleUiClickText(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String text = optString(body, "text", "");
        final String key = optString(body, "key", "");
        final String match = optString(body, "match", "contains");
        final int button = optInt(body, "button", 0);
        final int index = optInt(body, "index", -1);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                Screen screen = mc.screen;
                if (screen == null) {
                    d.put("clicked", false);
                    d.put("reason", "no screen");
                    return d;
                }
                AbstractWidget widget = findWidget(screen, text, key, match, index);
                if (widget == null) {
                    d.put("clicked", false);
                    d.put("reason", "widget not found");
                    d.put("screen", screen.getClass().getName());
                    d.put("title", screen.getTitle().getString());
                    return d;
                }
                int cx = widget.getX() + widget.getWidth() / 2;
                int cy = widget.getY() + widget.getHeight() / 2;
                screen.mouseClicked(cx, cy, button);
                screen.mouseReleased(cx, cy, button);
                d.put("clicked", true);
                d.put("message", widget.getMessage().getString());
                d.put("x", cx);
                d.put("y", cy);
                d.put("class", widget.getClass().getName());
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "click text failed: " + e.getMessage());
        }
    }

    private static void handleUiSetText(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final String text = optString(body, "text", optString(body, "value", ""));
        final int index = optInt(body, "index", 0);
        final boolean submit = optBool(body, "submit", false);
        final boolean clear = optBool(body, "clear", false);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                Screen screen = mc.screen;
                if (screen == null) {
                    d.put("typed", false);
                    d.put("reason", "no screen");
                    return d;
                }
                List<EditBox> boxes = new ArrayList<>();
                for (AbstractWidget widget : collectWidgets(screen)) {
                    if (widget instanceof EditBox box) {
                        boxes.add(box);
                    }
                }
                if (boxes.isEmpty()) {
                    d.put("typed", false);
                    d.put("reason", "no edit box");
                    d.put("screen", screen.getClass().getName());
                    return d;
                }
                int chosen = Math.max(0, Math.min(index, boxes.size() - 1));
                EditBox box = boxes.get(chosen);
                box.setValue(clear ? "" : text);
                box.setFocused(true);
                if (submit) {
                    screen.keyPressed(257, 0, 0);
                }
                d.put("typed", true);
                d.put("index", chosen);
                d.put("value", box.getValue());
                d.put("submitted", submit);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "set text failed: " + e.getMessage());
        }
    }

    private static void handleUiWait(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        String screen = strQuery(ex, "screen", "");
        String title = strQuery(ex, "title", "");
        String text = strQuery(ex, "text", "");
        String key = strQuery(ex, "key", "");
        int timeoutMs = Math.min(60_000, Math.max(500, intQuery(ex, "timeoutMs", 15_000)));
        String match = strQuery(ex, "match", "contains");
        long deadline = System.currentTimeMillis() + timeoutMs;
        try {
            while (System.currentTimeMillis() < deadline) {
                Map<String, Object> data = onClient(() -> {
                    Minecraft mc = Minecraft.getInstance();
                    Map<String, Object> d = new LinkedHashMap<>();
                    Screen current = mc.screen;
                    d.put("screen", current == null ? null : current.getClass().getName());
                    d.put("title", current == null ? null : current.getTitle().getString());
                    d.put("language", mc.options.languageCode);
                    d.put("matched", false);
                    if (current == null) {
                        return d;
                    }
                    if (!screen.isBlank() && !matches(current.getClass().getName(), screen, match)) {
                        return d;
                    }
                    if (!title.isBlank() && !matches(current.getTitle().getString(), title, match)) {
                        return d;
                    }
                    if (!text.isBlank() || !key.isBlank()) {
                        AbstractWidget widget = findWidget(current, text, key, match, -1);
                        if (widget == null) {
                            return d;
                        }
                        d.put("widget", widget.getMessage().getString());
                    }
                    d.put("matched", true);
                    return d;
                });
                if (Boolean.TRUE.equals(data.get("matched"))) {
                    ok(ex, data);
                    return;
                }
                Thread.sleep(200L);
            }
            error(ex, 408, "ui wait timeout");
        } catch (Exception e) {
            error(ex, 500, "ui wait failed: " + e.getMessage());
        }
    }

    private static JsonObject jsonBody(HttpExchange ex) throws IOException {
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        if (body.isBlank()) {
            return new JsonObject();
        }
        return JsonParser.parseString(body).getAsJsonObject();
    }

    private static String optString(JsonObject obj, String key, String def) {
        try {
            return obj.has(key) && !obj.get(key).isJsonNull()
                    ? obj.get(key).getAsString() : def;
        } catch (Exception e) {
            return def;
        }
    }

    private static boolean optBool(JsonObject obj, String key, boolean def) {
        try {
            return obj.has(key) && !obj.get(key).isJsonNull()
                    ? obj.get(key).getAsBoolean() : def;
        } catch (Exception e) {
            return def;
        }
    }

    private static int optInt(JsonObject obj, String key, int def) {
        try {
            return obj.has(key) && !obj.get(key).isJsonNull()
                    ? obj.get(key).getAsInt() : def;
        } catch (Exception e) {
            return def;
        }
    }

    private static double optDouble(JsonObject obj, String key, double def) {
        try {
            return obj.has(key) && !obj.get(key).isJsonNull()
                    ? obj.get(key).getAsDouble() : def;
        } catch (Exception e) {
            return def;
        }
    }

    private static String normalizeLanguage(String raw) {
        String value = raw == null ? "" : raw.trim().toLowerCase();
        if (value.isEmpty()) {
            return "zh_cn";
        }
        if (value.contains("中文") || value.equals("chinese") || value.equals("zh")
                || value.equals("zh-cn") || value.equals("zh_cn") || value.equals("cn")) {
            return "zh_cn";
        }
        if (value.equals("english") || value.equals("en") || value.equals("en-us")
                || value.equals("en_us") || value.contains("英文")) {
            return "en_us";
        }
        return value;
    }

    private static List<AbstractWidget> collectWidgets(Screen screen) {
        List<AbstractWidget> out = new ArrayList<>();
        collectWidgets(screen.children(), out);
        return out;
    }

    private static void collectWidgets(List<? extends GuiEventListener> listeners,
                                       List<AbstractWidget> out) {
        for (GuiEventListener listener : listeners) {
            if (listener instanceof AbstractWidget widget) {
                out.add(widget);
            }
            if (listener instanceof ContainerEventHandler container) {
                try {
                    collectWidgets(container.children(), out);
                } catch (Throwable ignored) {
                    // 某些容器在初始化过程中 children() 会抛错；跳过这一层即可。
                }
            }
        }
    }

    private static AbstractWidget findWidget(Screen screen, String text, String key,
                                             String match, int index) {
        List<AbstractWidget> widgets = collectWidgets(screen);
        if (key != null && !key.isBlank()) {
            String localized;
            try {
                localized = Component.translatable(key).getString();
            } catch (Throwable e) {
                localized = key;
            }
            for (AbstractWidget widget : widgets) {
                if (widget.visible && widget.isActive()
                        && matches(widget.getMessage().getString(), localized, "equals")) {
                    return widget;
                }
            }
            for (AbstractWidget widget : widgets) {
                if (widget.visible && widget.isActive()
                        && matches(widget.getMessage().getString(), localized, "contains")) {
                    return widget;
                }
            }
        }
        if (text != null && !text.isBlank()) {
            for (AbstractWidget widget : widgets) {
                if (widget.visible && widget.isActive()
                        && matches(widget.getMessage().getString(), text, match)) {
                    return widget;
                }
            }
        }
        if (index >= 0 && index < widgets.size()) {
            return widgets.get(index);
        }
        return null;
    }

    private static boolean matches(String actual, String expected, String mode) {
        if (actual == null || expected == null) {
            return false;
        }
        String kind = mode == null ? "contains" : mode.toLowerCase();
        return switch (kind) {
            case "equals", "eq", "exact" -> actual.equals(expected);
            case "starts", "startswith", "prefix" -> actual.startsWith(expected);
            case "ends", "endswith", "suffix" -> actual.endsWith(expected);
            case "regex" -> actual.matches(expected);
            default -> actual.contains(expected);
        };
    }

    private static KeyMapping resolveKeyMapping(Minecraft mc, String raw) {
        String key = raw == null ? "" : raw.trim().toLowerCase();
        if (key.matches("[1-9]")) {
            int index = Integer.parseInt(key) - 1;
            return index < mc.options.keyHotbarSlots.length
                    ? mc.options.keyHotbarSlots[index] : null;
        }
        return switch (key) {
            case "forward", "up", "w" -> mc.options.keyUp;
            case "back", "backward", "down", "s" -> mc.options.keyDown;
            case "left", "a" -> mc.options.keyLeft;
            case "right", "d" -> mc.options.keyRight;
            case "jump", "space" -> mc.options.keyJump;
            case "sneak", "shift" -> mc.options.keyShift;
            case "sprint" -> mc.options.keySprint;
            case "inventory", "e" -> mc.options.keyInventory;
            case "drop", "q" -> mc.options.keyDrop;
            case "use", "right_click" -> mc.options.keyUse;
            case "attack", "left_click" -> mc.options.keyAttack;
            case "chat", "t" -> mc.options.keyChat;
            case "command", "slash" -> mc.options.keyCommand;
            case "player_list", "tab" -> mc.options.keyPlayerList;
            case "swap_offhand", "f" -> mc.options.keySwapOffhand;
            case "perspective" -> mc.options.keyTogglePerspective;
            case "fullscreen" -> mc.options.keyFullscreen;
            case "screenshot" -> mc.options.keyScreenshot;
            case "advancements" -> mc.options.keyAdvancements;
            default -> null;
        };
    }

    private static void applyMovementKeys(Minecraft mc, List<String> keys, boolean down) {
        if (keys == null || keys.isEmpty()) {
            return;
        }
        for (String key : keys) {
            KeyMapping mapping = resolveKeyMapping(mc, key);
            if (mapping != null) {
                mapping.setDown(down);
            }
        }
    }

    private static void releaseMovementKeys(Minecraft mc) {
        for (String key : List.of("forward", "back", "left", "right", "jump", "sneak", "sprint")) {
            KeyMapping mapping = resolveKeyMapping(mc, key);
            if (mapping != null) {
                mapping.setDown(false);
            }
        }
    }

    private static Entity findEntity(Minecraft mc, String nameOrType, double radius) {
        if (mc.player == null || mc.level == null) {
            return null;
        }
        String needle = nameOrType == null ? "" : nameOrType.trim().toLowerCase();
        if (needle.isEmpty()) {
            return null;
        }
        AABB area = mc.player.getBoundingBox().inflate(radius);
        List<Entity> entities = new ArrayList<>(mc.level.getEntities(mc.player, area));
        entities.sort(Comparator.comparingDouble(entity -> entity.distanceToSqr(mc.player)));
        for (Entity entity : entities) {
            String type = String.valueOf(ForgeRegistries.ENTITY_TYPES.getKey(entity.getType()));
            String name = entity.getName().getString();
            if (type.toLowerCase().contains(needle) || name.toLowerCase().contains(needle)
                    || String.valueOf(entity.getId()).equals(needle)) {
                return entity;
            }
        }
        return null;
    }

    private static Entity findNearestLiving(Minecraft mc, LocalPlayer player, double radius) {
        if (mc.level == null) {
            return null;
        }
        AABB area = player.getBoundingBox().inflate(radius);
        List<Entity> entities = new ArrayList<>(mc.level.getEntities(player, area));
        entities.removeIf(entity -> entity == player || !(entity instanceof LivingEntity));
        entities.sort(Comparator.comparingDouble(entity -> entity.distanceToSqr(player)));
        return entities.isEmpty() ? null : entities.get(0);
    }

    private static Map<String, Object> pos(LocalPlayer player) {
        Map<String, Object> d = new LinkedHashMap<>();
        if (player == null) {
            return d;
        }
        d.put("x", round(player.getX()));
        d.put("y", round(player.getY()));
        d.put("z", round(player.getZ()));
        d.put("yaw", round(player.getYRot()));
        d.put("pitch", round(player.getXRot()));
        return d;
    }

    private static double round(double value) {
        return Math.round(value * 100.0) / 100.0;
    }

    private static void handleUiSnapshot(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Screen screen = mc.screen;
                Map<String, Object> d = new LinkedHashMap<>();
                if (screen == null) {
                    d.put("screen", null);
                    return d;
                }
                d.put("screen", screen.getClass().getName());
                d.put("title", screen.getTitle().getString());
                d.put("language", mc.options.languageCode);
                List<Map<String, Object>> widgets = new ArrayList<>();
                for (AbstractWidget w : collectWidgets(screen)) {
                    Map<String, Object> o = new LinkedHashMap<>();
                    o.put("class", w.getClass().getName());
                    o.put("x", w.getX());
                    o.put("y", w.getY());
                    o.put("width", w.getWidth());
                    o.put("height", w.getHeight());
                    o.put("message", w.getMessage().getString());
                    o.put("active", w.isActive());
                    o.put("visible", w.visible);
                    if (w instanceof EditBox box) {
                        o.put("editBox", true);
                        o.put("value", box.getValue());
                    }
                    widgets.add(o);
                }
                d.put("widgets", widgets);
                if (screen instanceof AbstractContainerScreen<?> c) {
                    List<Map<String, Object>> slots = new ArrayList<>();
                    for (Slot s : c.getMenu().slots) {
                        Map<String, Object> o = new LinkedHashMap<>();
                        o.put("index", s.index);
                        o.put("x", s.x);
                        o.put("y", s.y);
                        o.put("screenX", c.getGuiLeft() + s.x + 8);
                        o.put("screenY", c.getGuiTop() + s.y + 8);
                        o.put("item", s.getItem().isEmpty() ? null
                                : s.getItem().getHoverName().getString());
                        o.put("count", s.getItem().getCount());
                        slots.add(o);
                    }
                    d.put("slots", slots);
                    d.put("guiLeft", c.getGuiLeft());
                    d.put("guiTop", c.getGuiTop());
                }
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "ui snapshot failed: " + e.getMessage());
        }
    }

    private static void handleUiClick(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        final int x;
        final int y;
        final int button;
        final int slot;
        final boolean shift;
        try {
            JsonObject obj = JsonParser.parseString(body).getAsJsonObject();
            x = obj.has("x") ? obj.get("x").getAsInt() : 0;
            y = obj.has("y") ? obj.get("y").getAsInt() : 0;
            button = obj.has("button") ? obj.get("button").getAsInt() : 0;
            slot = obj.has("slot") ? obj.get("slot").getAsInt() : -1;
            shift = obj.has("shift") && obj.get("shift").getAsBoolean();
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Screen screen = mc.screen;
                Map<String, Object> d = new LinkedHashMap<>();
                if (screen == null) {
                    d.put("clicked", false);
                    d.put("reason", "no screen");
                    return d;
                }
                if (slot >= 0 && screen instanceof AbstractContainerScreen<?> c) {
                    if (slot >= c.getMenu().slots.size()) {
                        d.put("clicked", false);
                        d.put("reason", "slot out of range");
                        return d;
                    }
                    Slot s = c.getMenu().slots.get(slot);
                    int sx = c.getGuiLeft() + s.x + 8;
                    int sy = c.getGuiTop() + s.y + 8;
                    if (shift) {
                        mc.gameMode.handleInventoryMouseClick(
                                c.getMenu().containerId, slot, button,
                                ClickType.QUICK_MOVE, mc.player);
                    } else {
                        screen.mouseClicked(sx, sy, button);
                        screen.mouseReleased(sx, sy, button);
                    }
                    d.put("clicked", true);
                    d.put("slot", slot);
                    d.put("x", sx);
                    d.put("y", sy);
                    d.put("shift", shift);
                    return d;
                }
                screen.mouseClicked(x, y, button);
                screen.mouseReleased(x, y, button);
                d.put("clicked", true);
                d.put("x", x);
                d.put("y", y);
                d.put("button", button);
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "ui click failed: " + e.getMessage());
        }
    }

    private static void handleUiClose(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("closed", false);
                    d.put("reason", "no player");
                    return d;
                }
                mc.player.closeContainer();
                mc.setScreen(null);
                d.put("closed", true);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "ui close failed: " + e.getMessage());
        }
    }

    private static void handleUiHover(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int x = intQuery(ex, "x", 0);
        int y = intQuery(ex, "y", 0);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                d.put("x", x);
                d.put("y", y);
                if (mc.screen == null) {
                    d.put("hovered", false);
                    d.put("error", "no screen");
                    return d;
                }
                mc.screen.mouseMoved(x, y);
                d.put("hovered", true);
                d.put("screen", mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "ui hover failed: " + e.getMessage());
        }
    }

    private static void handleUiHoverSlot(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int slotIndex = intQuery(ex, "slot", 0);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                d.put("slot", slotIndex);
                if (!(mc.screen instanceof AbstractContainerScreen<?> screen)) {
                    d.put("hovered", false);
                    d.put("error", "not a container screen");
                    return d;
                }
                if (slotIndex < 0 || slotIndex >= screen.getMenu().slots.size()) {
                    d.put("hovered", false);
                    d.put("error", "slot out of range");
                    return d;
                }
                Slot slot = screen.getMenu().slots.get(slotIndex);
                Field field = AbstractContainerScreen.class.getDeclaredField("hoveredSlot");
                field.setAccessible(true);
                field.set(screen, slot);
                double guiX = screen.getGuiLeft() + slot.x + 8;
                double guiY = screen.getGuiTop() + slot.y + 8;
                double scale = mc.getWindow().getGuiScale();
                GLFW.glfwSetCursorPos(mc.getWindow().getWindow(), guiX * scale, guiY * scale);
                screen.mouseMoved(guiX, guiY);
                d.put("hovered", true);
                d.put("item", slot.getItem().isEmpty()
                        ? null : String.valueOf(ForgeRegistries.ITEMS.getKey(slot.getItem().getItem())));
                d.put("count", slot.getItem().getCount());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "hover slot failed: " + e.getMessage());
        }
    }

    private static void handlePlayerInventory(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("opened", false);
                    d.put("error", "player not in world");
                    return d;
                }
                mc.setScreen(new InventoryScreen(mc.player));
                d.put("opened", true);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "open inventory failed: " + e.getMessage());
        }
    }

    private static void handleCommand(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String cmd;
        try {
            JsonObject obj = JsonParser.parseString(body).getAsJsonObject();
            cmd = obj.get("cmd").getAsString();
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        if (cmd.startsWith("/")) {
            cmd = cmd.substring(1);
        }
        final String command = cmd;
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("sent", false);
                    d.put("reason", "no player");
                    return d;
                }
                mc.player.connection.sendCommand(command);
                d.put("sent", true);
                d.put("cmd", command);
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "command failed: " + e.getMessage());
        }
    }

    private static void handlePlayerUse(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        final int x;
        final int y;
        final int z;
        String faceName = "up";
        try {
            JsonObject obj = JsonParser.parseString(body).getAsJsonObject();
            x = obj.get("x").getAsInt();
            y = obj.get("y").getAsInt();
            z = obj.get("z").getAsInt();
            if (obj.has("face")) {
                faceName = obj.get("face").getAsString();
            }
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        Direction parsed = Direction.byName(faceName);
        final Direction face = parsed == null ? Direction.UP : parsed;
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("used", false);
                    d.put("reason", "no player");
                    return d;
                }
                BlockPos pos = new BlockPos(x, y, z);
                BlockHitResult hit = new BlockHitResult(
                        Vec3.atCenterOf(pos), face, pos, false);
                InteractionResult result = mc.gameMode.useItemOn(
                        mc.player, InteractionHand.MAIN_HAND, hit);
                d.put("used", true);
                d.put("result", result.toString());
                d.put("pos", x + "," + y + "," + z);
                d.put("face", face.getName());
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "use failed: " + e.getMessage());
        }
    }

    private static void handlePlayerUseItem(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            error(ex, 405, "POST required");
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("used", false);
                    d.put("reason", "no player");
                    return d;
                }
                InteractionResult result = mc.gameMode.useItem(
                        mc.player, InteractionHand.MAIN_HAND);
                d.put("used", true);
                d.put("result", result.toString());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "use item failed: " + e.getMessage());
        }
    }

    private static void handlePlayerBreak(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int x = intQuery(ex, "x", 0);
        int y = intQuery(ex, "y", 64);
        int z = intQuery(ex, "z", 0);
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null || mc.level == null || mc.gameMode == null) {
                    d.put("ok", false);
                    d.put("error", "player not in world");
                    return d;
                }
                BlockPos pos = new BlockPos(x, y, z);
                d.put("pos", x + "," + y + "," + z);
                d.put("before", String.valueOf(ForgeRegistries.BLOCKS.getKey(mc.level.getBlockState(pos).getBlock())));
                d.put("started", mc.gameMode.startDestroyBlock(pos, Direction.UP));
                boolean continued = false;
                for (int i = 0; i < 10; i++) {
                    continued = mc.gameMode.continueDestroyBlock(pos, Direction.UP);
                }
                mc.gameMode.stopDestroyBlock();
                d.put("continued", continued);
                d.put("after", String.valueOf(ForgeRegistries.BLOCKS.getKey(mc.level.getBlockState(pos).getBlock())));
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "player break failed: " + e.getMessage());
        }
    }

    private static void handlePlayerMine(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        int x = intQuery(ex, "x", 0);
        int y = intQuery(ex, "y", 64);
        int z = intQuery(ex, "z", 0);
        int timeoutMs = Math.min(15000, Math.max(1000, intQuery(ex, "timeoutMs", 6000)));
        Minecraft mc = Minecraft.getInstance();
        if (mc.player == null || mc.level == null || mc.gameMode == null) {
            error(ex, 409, "player not in world");
            return;
        }
        BlockPos pos = new BlockPos(x, y, z);
        Map<String, Object> data = new LinkedHashMap<>();
        data.put("pos", x + "," + y + "," + z);
        data.put("before", String.valueOf(ForgeRegistries.BLOCKS.getKey(mc.level.getBlockState(pos).getBlock())));
        data.put("connection", mc.getConnection() != null);
        if (mc.getConnection() != null) {
            mc.execute(() -> mc.getConnection().send(new ServerboundPlayerActionPacket(
                    ServerboundPlayerActionPacket.Action.START_DESTROY_BLOCK,
                    pos, Direction.UP, 0)));
        }
        boolean broken = false;
        int ticks = 0;
        long deadline = System.currentTimeMillis() + timeoutMs;
        try {
            while (System.currentTimeMillis() < deadline) {
                if (mc.level.getBlockState(pos).isAir()) {
                    broken = true;
                    break;
                }
                ticks += 1;
                Thread.sleep(50L);
            }
            if (mc.getConnection() != null) {
                mc.execute(() -> mc.getConnection().send(new ServerboundPlayerActionPacket(
                        ServerboundPlayerActionPacket.Action.STOP_DESTROY_BLOCK,
                        pos, Direction.UP, 0)));
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        }
        data.put("ticks", ticks);
        data.put("broken", broken);
        data.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
        data.put("playerCreative", mc.player.isCreative());
        data.put("localMode", mc.gameMode.getPlayerMode().getName());
        data.put("hitType", mc.hitResult == null ? null : mc.hitResult.getType().name());
        data.put("hitPos", mc.hitResult instanceof BlockHitResult hit ? hit.getBlockPos().toShortString() : null);
        data.put("playerPos", mc.player.blockPosition().toShortString());
        data.put("look", mc.player.getYRot() + "/" + mc.player.getXRot());
        data.put("after", String.valueOf(ForgeRegistries.BLOCKS.getKey(mc.level.getBlockState(pos).getBlock())));
        ok(ex, data);
    }

    private static void handleMainhandItem(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Map<String, Object> d = new LinkedHashMap<>();
                if (mc.player == null) {
                    d.put("found", false);
                    return d;
                }
                ItemStack stack = mc.player.getMainHandItem();
                d.put("found", true);
                d.put("item", stack.isEmpty() ? null
                        : String.valueOf(ForgeRegistries.ITEMS.getKey(stack.getItem())));
                d.put("nbt", stack.getTag() == null ? null : stack.getTag().toString());
                d.put("count", stack.getCount());
                d.put("barVisible", stack.isBarVisible());
                d.put("barWidth", stack.getBarWidth());
                d.put("barColor", stack.getBarColor());
                return d;
            });
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "mainhand item query failed: " + e.getMessage());
        }
    }

    private static void handleScreenshot(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        String rawLabel = strQuery(ex, "label", "");
        String label = rawLabel.replaceAll("[^A-Za-z0-9._-]+", "-");
        if (label.isBlank()) {
            label = "shot";
        }
        final String labelFinal = label;
        final String name = "mcdev-" + labelFinal + "-" + System.currentTimeMillis() + ".png";
        try {
            Map<String, Object> data = onClient(() -> {
                Minecraft mc = Minecraft.getInstance();
                Screenshot.grab(mc.gameDirectory, name, mc.getMainRenderTarget(), c -> {
                });
                java.nio.file.Path path = new File(
                        new File(mc.gameDirectory, "screenshots"), name)
                        .toPath().toAbsolutePath().normalize();
                Map<String, Object> d = new LinkedHashMap<>();
                d.put("path", path.toString());
                d.put("label", labelFinal);
                d.put("screen", mc.screen == null ? null : mc.screen.getClass().getName());
                d.put("title", mc.screen == null ? null : mc.screen.getTitle().getString());
                d.put("language", mc.options.languageCode);
                d.put("inWorld", mc.player != null && mc.level != null);
                d.put("width", mc.getWindow().getWidth());
                d.put("height", mc.getWindow().getHeight());
                return d;
            });
            java.nio.file.Path path = java.nio.file.Path.of((String) data.get("path"));
            // Screenshot.grab 是异步落盘的：只等到“文件存在”会读到 0 字节的半成品，
            // 后续画面体检会直接失败。这里等到文件大小连续两次一致且非空再返回。
            long lastSize = -1L;
            for (int i = 0; i < 80; i++) {
                if (java.nio.file.Files.isRegularFile(path)) {
                    long size = java.nio.file.Files.size(path);
                    if (size > 0L && size == lastSize) {
                        break;
                    }
                    lastSize = size;
                }
                Thread.sleep(50);
            }
            long finalSize = java.nio.file.Files.isRegularFile(path)
                    ? java.nio.file.Files.size(path) : 0L;
            data.put("exists", finalSize > 0L);
            data.put("size", finalSize);
            ok(ex, data);
        } catch (Exception e) {
            error(ex, 500, "screenshot failed: " + e.getMessage());
        }
    }

    private static void handleWindow(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        if (!"POST".equalsIgnoreCase(ex.getRequestMethod())) {
            try {
                ok(ex, onClient(() -> BackgroundWindow.status(Minecraft.getInstance())));
            } catch (Exception e) {
                error(ex, 500, "window query failed: " + e.getMessage());
            }
            return;
        }
        JsonObject body;
        try {
            body = jsonBody(ex);
        } catch (Exception e) {
            error(ex, 400, "invalid json: " + e.getMessage());
            return;
        }
        final boolean visible = optBool(body, "visible", true);
        final boolean focus = optBool(body, "focus", false);
        try {
            ok(ex, onClient(() -> BackgroundWindow.setVisible(
                    Minecraft.getInstance(), visible, focus)));
        } catch (Exception e) {
            error(ex, 500, "window change failed: " + e.getMessage());
        }
    }

    private static void handleQuit(HttpExchange ex) throws IOException {
        if (!authorized(ex)) {
            return;
        }
        ok(ex, Map.of("quitting", true));
        Minecraft.getInstance().execute(() -> Minecraft.getInstance().stop());
    }

    private static <T> T onClient(Callable<T> task) throws Exception {
        CompletableFuture<T> future = new CompletableFuture<>();
        Minecraft.getInstance().execute(() -> {
            try {
                future.complete(task.call());
            } catch (Throwable t) {
                future.completeExceptionally(t);
            }
        });
        return future.get(10, TimeUnit.SECONDS);
    }

    private static boolean authorized(HttpExchange ex) throws IOException {
        if (token.isEmpty()) {
            return true;
        }
        String got = ex.getRequestHeaders().getFirst("X-Mcdev-Token");
        if (token.equals(got)) {
            return true;
        }
        error(ex, 401, "invalid token");
        return false;
    }

    private static void ok(HttpExchange ex, Object data) throws IOException {
        JsonObject root = new JsonObject();
        root.addProperty("ok", true);
        root.add("data", GSON.toJsonTree(data));
        send(ex, 200, root);
    }

    private static void error(HttpExchange ex, int code, String message) throws IOException {
        JsonObject root = new JsonObject();
        root.addProperty("ok", false);
        JsonObject err = new JsonObject();
        err.addProperty("message", message);
        root.add("error", err);
        send(ex, code, root);
    }

    private static void send(HttpExchange ex, int code, JsonObject body) throws IOException {
        byte[] bytes = GSON.toJson(body).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        ex.sendResponseHeaders(code, bytes.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(bytes);
        }
    }

    private static int intQuery(HttpExchange ex, String key, int def) {
        String raw = strQuery(ex, key, null);
        if (raw == null) {
            return def;
        }
        try {
            return Integer.parseInt(raw);
        } catch (NumberFormatException e) {
            return def;
        }
    }

    private static String strQuery(HttpExchange ex, String key, String def) {
        String query = ex.getRequestURI().getQuery();
        if (query == null || query.isEmpty()) {
            return def;
        }
        for (String part : query.split("&")) {
            int eq = part.indexOf('=');
            if (eq < 0) {
                continue;
            }
            String k = URLDecoder.decode(part.substring(0, eq), StandardCharsets.UTF_8);
            if (k.equals(key)) {
                return URLDecoder.decode(part.substring(eq + 1), StandardCharsets.UTF_8);
            }
        }
        return def;
    }
}
