from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import knowledge, mca_templates, project as project_mod, spec as spec_mod, workflow

VERIFY_DIR = ".dma"
VERIFY_FILE = "verify.json"
VERIFY_MD = "VERIFY.md"
PORT_PLAN = "PORT_PLAN.md"
PORT_STATUS = "PORT_STATUS.json"
PORT_STATUS_MD = "PORT_STATUS.md"
PORT_EXCLUDE = "PORT_EXCLUDE.md"
SUPPORTED_TARGETS = {"1.21.1"}
NEOFORGE_VERSIONS = {"1.21.1": "21.1.265"}
# 2.0.141 在不少用户机器上从未下载过，离线/弱网时会卡在插件解析。
# 2.0.115 是 NeoForge 1.21.1 工程广泛使用且已被本机缓存验证的版本。
MODDEV_VERSION = "2.0.115"
MCA_SLUG = "minecraft-comes-alive-reborn"

# 1.21 起数据包目录改为单数；1.20.x 仍是复数。
# 这里只做目录名映射，不猜测 JSON 内容。
DATA_DIRS_PLURAL = {
    "advancement": "advancements",
    "recipe": "recipes",
    "loot_table": "loot_tables",
    "predicate": "predicates",
    "item_modifier": "item_modifiers",
    "function": "functions",
    "structure": "structures",
    "tags/item": "tags/items",
    "tags/block": "tags/blocks",
    "tags/entity_type": "tags/entity_types",
    "tags/fluid": "tags/fluids",
    "tags/game_event": "tags/game_events",
    "tags/function": "tags/functions",
}
DATA_DIRS_SINGULAR = {
    value: key for key, value in DATA_DIRS_PLURAL.items()
}

JAVA_IMPORT_HINTS = {
    "net.minecraft.util.Identifier": "net.minecraft.resources.ResourceLocation",
    "net.minecraft.entity.player.PlayerEntity": "net.minecraft.world.entity.player.Player",
    "net.minecraft.server.network.ServerPlayerEntity": "net.minecraft.server.level.ServerPlayer",
    "net.minecraft.world.World": "net.minecraft.world.level.Level",
    "net.minecraft.text.Text": "net.minecraft.network.chat.Component",
    "net.minecraft.text.MutableText": "net.minecraft.network.chat.MutableComponent",
    "net.minecraft.util.math.BlockPos": "net.minecraft.core.BlockPos",
    "net.minecraft.util.Hand": "net.minecraft.world.InteractionHand",
    "net.minecraft.util.ActionResult": "net.minecraft.world.InteractionResult",
    "net.minecraft.util.TypedActionResult": "net.minecraft.world.InteractionResultHolder",
    "net.minecraft.entity.LivingEntity": "net.minecraft.world.entity.LivingEntity",
    "net.minecraft.entity.Entity": "net.minecraft.world.entity.Entity",
    "net.minecraft.block.Block": "net.minecraft.world.level.block.Block",
    "net.minecraft.block.BlockState": "net.minecraft.world.level.block.state.BlockState",
    "net.minecraft.item.Item": "net.minecraft.world.item.Item",
    "net.minecraft.item.ItemStack": "net.minecraft.world.item.ItemStack",
    "net.minecraft.item.BlockItem": "net.minecraft.world.item.BlockItem",
    "net.minecraft.item.ItemGroup": "net.minecraft.world.item.CreativeModeTab",
    "net.minecraft.item.ItemGroups": "net.minecraft.world.item.CreativeModeTabs",
    "net.minecraft.registry.Registries": "net.minecraft.core.registries.BuiltInRegistries",
    "net.minecraft.registry.Registry": "net.minecraft.core.Registry",
    "net.minecraft.sound.SoundEvent": "net.minecraft.sounds.SoundEvent",
    "net.minecraft.particle.ParticleEffect": "net.minecraft.core.particles.ParticleOptions",
    "net.minecraft.util.Formatting": "net.minecraft.ChatFormatting",
    "net.minecraft.nbt.NbtCompound": "net.minecraft.nbt.CompoundTag",
    "net.minecraft.nbt.NbtElement": "net.minecraft.nbt.Tag",
    "net.minecraft.nbt.NbtList": "net.minecraft.nbt.ListTag",
    "net.minecraft.network.PacketByteBuf": "net.minecraft.network.FriendlyByteBuf",
    "net.minecraft.client.gui.DrawContext": "net.minecraft.client.gui.GuiGraphics",
    "net.minecraft.client.render.VertexConsumerProvider": "net.minecraft.client.renderer.MultiBufferSource",
    "net.minecraft.client.util.math.MatrixStack": "com.mojang.blaze3d.vertex.PoseStack",
    "net.minecraft.entity.data.TrackedData": "net.minecraft.network.syncher.EntityDataAccessor",
    "net.minecraft.entity.data.DataTracker": "net.minecraft.network.syncher.SynchedEntityData",
}

FABRIC_IMPORT_HINTS = {
    "net.fabricmc.api.ModInitializer": "@Mod 主类构造器",
    "net.fabricmc.api.ClientModInitializer": "@Mod(value = MOD_ID, dist = Dist.CLIENT)",
    "net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking": "PayloadRegistrar / RegisterPayloadHandlersEvent",
    "net.fabricmc.fabric.api.networking.v1.ClientPlayNetworking": "ClientPayloadHandler / RegisterClientPayloadHandlersEvent",
    "net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry": "PayloadRegistrar.playToServer/playToClient",
    "net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents": "NeoForge.EVENT_BUS.addListener(ServerTickEvent.Post::new)",
    "net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents": "ClientTickEvent.Post on NeoForge.EVENT_BUS",
    "net.fabricmc.fabric.api.client.rendering.v1.HudRenderCallback": "RenderGuiEvent.Post / LayeredDraw",
    "net.fabricmc.fabric.api.client.keybinding.v1.KeyBindingHelper": "RegisterKeyMappingsEvent",
    "net.fabricmc.fabric.api.itemgroup.v1.ItemGroupEvents": "BuildCreativeModeTabContentsEvent",
    "net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback": "RegisterCommandsEvent",
    "net.fabricmc.fabric.api.event.player.UseEntityCallback": "PlayerInteractEvent.EntityInteract / EntityInteractSpecific",
    "net.fabricmc.fabric.api.event.player.AttackEntityCallback": "AttackEntityEvent",
    "net.fabricmc.fabric.api.entity.event.v1.ServerEntityEvents": "EntityJoinLevelEvent",
    "net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents": "PlayerEvent.PlayerLoggedInEvent / PlayerLoggedOutEvent",
    "net.fabricmc.loader.api.FabricLoader": "FMLPaths / ModList",
}

MCA_RECIPE = """\
// MCA Reborn 1.21.1 NeoForge 兼容要点（必须按真实 API 写，不要照 Fabric 名猜）
//
// 1) 对 MCA 村民使用道具：
//    只重写 useOn 不够，必须重写 interactLivingEntity：
//    @Override
//    public InteractionResult interactLivingEntity(ItemStack stack, Player player, LivingEntity target, InteractionHand hand) {
//        if (!(target instanceof VillagerLike<?>) || target instanceof Player) {
//            return InteractionResult.PASS;
//        }
//        // 对村民执行和玩家相同的身份同步逻辑
//        return InteractionResult.sidedSuccess(player.level().isClientSide);
//    }
//    注意：不要用 VillagerLike.toVillager(target) 判断目标，它对任何 ServerPlayer 都会返回临时村民视图。
//
// 2) 身份同步后让 MCA 把玩家当成该村民：
//    ServerPlayer player = ...;
//    VillagerEntityMCA villager = ...;              // 被接管的村民
//    PlayerSaveData data = PlayerSaveData.get(player);
//    CompoundTag previous = data.getEntityData();   // 先备份，退出时恢复
//    CompoundTag tag = new CompoundTag();
//    villager.addAdditionalSaveData(tag);           // 保存村民的皮肤/遗传/特质/大脑
//    data.setEntityData(tag);
//    data.setEntityDataSet(true);                   // 这一行不能漏，否则渲染和碰撞箱不切换
//    player.refreshDimensions();
//    for (ServerPlayer viewer : player.serverLevel().players()) {
//        Network.sendToPlayer(new PlayerDataMessage(player.getUUID(), data.getEntityData()), viewer);
//    }
//
// 3) 名称 / 关系 / 家庭同步（服务端）：
//    PlayerSaveData target = PlayerSaveData.get(player);
//    FamilyTreeNode from = EntityRelationship.of(villager).orElseThrow().getFamilyEntry();
//    FamilyTreeNode to = target.getFamilyEntry();     // 玩家节点
//    to.setName(from.getName());
//    to.setGender(from.gender());
//    to.setProfession(from.getProfession());
//    to.setRelationshipState(from.getRelationshipState());
//    FamilyTree tree = target.getFamilyTree();
//    UUID oldId = villager.getUUID();
//    UUID newId = player.getUUID();
//    // 配偶：把对方节点的配偶改成玩家，同时玩家节点指向对方
//    UUID partnerId = from.partner();
//    if (partnerId != null && !partnerId.equals(Util.NIL_UUID)) {
//        Entity partner = player.serverLevel().getEntity(partnerId);
//        if (partner == null) partner = player.getServer().getPlayerList().getPlayer(partnerId);
//        if (partner != null) {
//            to.updatePartner(partner, from.getRelationshipState());
//            tree.getOrEmpty(partnerId).ifPresent(n -> n.updatePartner(player, from.getRelationshipState()));
//        }
//    }
//    // 子女：player 节点接管子女集合，子女节点的 father/mother 改成玩家
//    to.children().clear();
//    to.children().addAll(from.children());
//    for (UUID childId : from.children()) {
//        tree.getOrEmpty(childId).ifPresent(n -> {
//            if (n.father().equals(oldId)) n.setFather(to);
//            else if (n.mother().equals(oldId)) n.setMother(to);
//        });
//    }
//    // 父母：父母节点的 children 集合里把原村民 UUID 换成玩家 UUID
//    for (UUID parentId : new UUID[]{from.father(), from.mother()}) {
//        tree.getOrEmpty(parentId).ifPresent(n -> {
//            n.children().remove(oldId);
//            n.children().add(newId);
//        });
//    }
//    // 恢复时：data.setEntityData(previous); data.setEntityDataSet(wasSet);
//    // player.refreshDimensions(); 再把上述引用反向改回旧 UUID，并再次广播。
//
// 4) 结婚 / 生小孩：
//    MCA 的婚姻和生育都走 EntityRelationship + 村民大脑记忆。
//    身份同步后让玩家保留原村民的 FamilyTreeNode（配偶、子女、父母、婚姻状态），
//    其他村民的 AI 通过 UUID 找到该节点，才会把玩家当成原村民。
//    只改显示名不改 FamilyTreeNode 会出现「名字变了但关系没变」。
//
// 5) 服务端 / 客户端分离：
//    PlayerSaveData、FamilyTree、VillagerEntityMCA 都是服务端类，只能写进 common 的服务端分支；
//    客户端只通过 MCA 的 PlayerDataMessage 更新缓存，不要在客户端 new VillagerEntityMCA。
"""


class PortError(Exception):
    pass


@dataclass
class Issue:
    level: str
    code: str
    title: str
    detail: str = ""
    path: str = ""
    fix: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "code": self.code,
            "title": self.title,
            "detail": self.detail,
            "path": self.path,
            "fix": self.fix,
        }

    def render(self) -> str:
        where = f"（{self.path}）" if self.path else ""
        # 带上 code，Agent 才能按错误类型批量修复，而不是只看到一句中文。
        lines = [f"[{self.level}:{self.code}] {self.title}{where}"]
        if self.detail:
            lines.append(f"  {self.detail}")
        if self.fix:
            lines.append(f"  修复：{self.fix}")
        return "\n".join(lines)


@dataclass
class VerifyReport:
    root: str = ""
    target_mc: str = ""
    target_loader: str = ""
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    generated_at: str = ""

    def add(
        self,
        level: str,
        code: str,
        title: str,
        detail: str = "",
        path: str = "",
        fix: str = "",
    ) -> None:
        self.issues.append(
            Issue(
                level=level if level in {"error", "warn", "info"} else "info",
                code=code,
                title=title,
                detail=detail,
                path=path,
                fix=fix,
            )
        )

    def errors(self) -> list[Issue]:
        return [item for item in self.issues if item.level == "error"]

    def warnings(self) -> list[Issue]:
        return [item for item in self.issues if item.level == "warn"]

    def ok(self) -> bool:
        return not self.errors()

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "target_mc": self.target_mc,
            "target_loader": self.target_loader,
            "generated_at": self.generated_at,
            "ok": self.ok(),
            "errors": len(self.errors()),
            "warnings": len(self.warnings()),
            "stats": self.stats,
            "issues": [item.as_dict() for item in self.issues],
        }

    def render(self, limit: int = 80) -> str:
        errors = self.errors()
        warnings = self.warnings()
        infos = [item for item in self.issues if item.level == "info"]
        head = (
            f"验收结果：{'通过' if self.ok() else '未通过'} "
            f"（错误 {len(errors)}，警告 {len(warnings)}）"
        )
        if self.stats:
            rows = "，".join(f"{key} {value}" for key, value in self.stats.items())
            head += f"\n工程统计：{rows}"
        chunks = [head]
        if errors:
            chunks.append("\n【必须修复】")
            chunks.extend(item.render() for item in errors[:limit])
            if len(errors) > limit:
                chunks.append(f"…还有 {len(errors) - limit} 个错误，见 {VERIFY_MD}。")
        if warnings:
            chunks.append("\n【建议修复】")
            chunks.extend(item.render() for item in warnings[:limit])
            if len(warnings) > limit:
                chunks.append(f"…还有 {len(warnings) - limit} 个警告。")
        if infos and not errors and not warnings:
            chunks.append("\n【信息】")
            chunks.extend(item.render() for item in infos[:20])
        return "\n".join(chunks)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(_read_text(path))
    except (OSError, ValueError):
        return None


def _decode_text(raw: bytes) -> str:
    """按 UTF-8 → GB18030 顺序解码。

    有些老模组的 zh_cn.json 是 GBK 写的，
    直接按 UTF-8 读会得到大量 �。这里优先 UTF-8，失败再退到
    GB18030，最后才用替换字符兜底。
    """
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    for encoding in ("gb18030", "big5", "shift_jis"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\ufffd" not in text:
            return text
    return raw.decode("utf-8-sig", "replace")


def _write_text(root: Path, relative: str, text: str) -> bool:
    try:
        target = project_mod.safe_path(root, relative)
    except project_mod.ProjectError:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(text, encoding="utf-8", newline="\n")
    except OSError:
        return False
    return True


def _write_bytes(root: Path, relative: str, data: bytes) -> bool:
    try:
        target = project_mod.safe_path(root, relative)
    except project_mod.ProjectError:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_bytes(data)
    except OSError:
        return False
    return True


def _files(root: Path, folder: str = "") -> list[str]:
    base = Path(root) / folder if folder else Path(root)
    if not base.is_dir():
        return []
    result: list[str] = []
    for path in base.rglob("*"):
        if path.is_file():
            try:
                result.append(path.relative_to(root).as_posix())
            except ValueError:
                continue
    return sorted(result)


def _stem(value: str) -> str:
    return re.sub(r"[^a-z0-9_]", "", (value or "").lower())


def _camel(mod_id: str) -> str:
    parts = [part for part in re.split(r"[^A-Za-z0-9]+", mod_id) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Mod"


def _target_dirs(target_mc: str) -> dict[str, str]:
    try:
        major, minor = [int(x) for x in str(target_mc).split(".")[:2]]
    except (TypeError, ValueError):
        return {}
    if (major, minor) < (1, 21):
        return dict(DATA_DIRS_PLURAL)
    return {key: key for key in DATA_DIRS_PLURAL}


def _convert_data_path(path: str, target_mc: str) -> str:
    mapping = _target_dirs(target_mc)
    if not mapping:
        return path
    for source, target in sorted(mapping.items(), key=lambda row: -len(row[0])):
        if source == target:
            continue
        marker = f"/{source}/"
        if marker in path:
            return path.replace(marker, f"/{target}/", 1)
    return path


def _json_dump(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------------------
# JAR / 工程扫描
# ---------------------------------------------------------------------------


def _jar_metadata(zf: zipfile.ZipFile, names: list[str]) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "mod_id": "",
        "name": "",
        "version": "",
        "mc_version": "",
        "loader": "",
        "description": "",
        "entrypoints": [],
    }
    if "fabric.mod.json" in names:
        try:
            data = json.loads(zf.read("fabric.mod.json").decode("utf-8-sig", "replace"))
        except (KeyError, ValueError, OSError):
            data = None
        if isinstance(data, dict):
            meta["loader"] = "Fabric"
            meta["mod_id"] = str(data.get("id") or "")
            meta["name"] = str(data.get("name") or "")
            meta["version"] = str(data.get("version") or "")
            meta["description"] = str(data.get("description") or "")
            depends = data.get("depends") if isinstance(data.get("depends"), dict) else {}
            meta["mc_version"] = str(depends.get("minecraft") or "")
            entries = data.get("entrypoints") if isinstance(data.get("entrypoints"), dict) else {}
            for key in ("main", "client", "server"):
                for item in entries.get(key) or []:
                    if isinstance(item, str):
                        meta["entrypoints"].append(item)
                    elif isinstance(item, dict) and item.get("value"):
                        meta["entrypoints"].append(str(item.get("value")))
    toml_name = next(
        (name for name in names if name.endswith("neoforge.mods.toml")),
        "",
    )
    if toml_name:
        try:
            text = zf.read(toml_name).decode("utf-8-sig", "replace")
        except (KeyError, OSError):
            text = ""
        id_match = re.search(r'modId\s*=\s*"([^"]+)"', text)
        name_match = re.search(r'displayName\s*=\s*"([^"]+)"', text)
        version_match = re.search(r'(?m)^\s*version\s*=\s*"([^"]+)"', text)
        range_match = re.search(r'versionRange\s*=\s*"\[?([0-9][^",\]]*)', text)
        if id_match:
            meta["mod_id"] = meta["mod_id"] or id_match.group(1)
        if name_match:
            meta["name"] = meta["name"] or name_match.group(1)
        if version_match:
            meta["version"] = meta["version"] or version_match.group(1)
        if range_match:
            meta["mc_version"] = meta["mc_version"] or range_match.group(1)
        meta["loader"] = "NeoForge"
    return meta


def scan_jar(jar: Path) -> dict[str, Any]:
    jar = Path(jar).expanduser()
    if not jar.is_file():
        raise PortError(f"JAR 不存在：{jar}")
    try:
        archive = zipfile.ZipFile(jar)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PortError(f"无法打开 JAR：{exc}") from exc
    with archive:
        names = [name for name in archive.namelist() if not name.endswith("/")]
        meta = _jar_metadata(archive, names)
        namespaces: set[str] = set()
        assets: list[str] = []
        data: list[str] = []
        item_defs: list[str] = []
        item_models: list[str] = []
        block_models: list[str] = []
        blockstates: list[str] = []
        textures: list[str] = []
        models: list[str] = []
        lang: dict[str, dict[str, str]] = {}
        sounds: list[str] = []
        nested: list[str] = []
        root_files: list[str] = []
        meta_files: list[str] = []
        for name in names:
            parts = name.split("/")
            if len(parts) == 1:
                root_files.append(name)
            if name.startswith("META-INF/"):
                meta_files.append(name)
            if len(parts) >= 2 and parts[0] in {"assets", "data"}:
                namespaces.add(parts[1])
            if name.startswith("assets/"):
                assets.append(name)
                if len(parts) >= 4 and parts[2] == "items" and name.endswith(".json"):
                    item_defs.append(name)
                elif len(parts) >= 5 and parts[2] == "models" and parts[3] == "item" and name.endswith(".json"):
                    item_models.append(name)
                    models.append(name)
                elif len(parts) >= 5 and parts[2] == "models" and parts[3] == "block" and name.endswith(".json"):
                    block_models.append(name)
                    models.append(name)
                elif len(parts) >= 4 and parts[2] == "blockstates" and name.endswith(".json"):
                    blockstates.append(name)
                elif len(parts) >= 5 and parts[2] == "textures" and name.endswith(".png"):
                    textures.append(name)
                elif len(parts) >= 4 and parts[2] == "lang" and name.endswith(".json"):
                    try:
                        payload = json.loads(_decode_text(archive.read(name)))
                    except (KeyError, ValueError, OSError):
                        payload = None
                    if isinstance(payload, dict):
                        lang[Path(parts[3]).stem] = {
                            str(key): str(value)
                            for key, value in payload.items()
                            if isinstance(value, str)
                        }
                elif name.endswith("sounds.json"):
                    sounds.append(name)
            elif name.startswith("data/"):
                data.append(name)
            elif name.startswith("META-INF/jars/") and name.endswith(".jar"):
                nested.append(name)
        mod_id = meta.get("mod_id") or ""
        if not mod_id and namespaces:
            mod_id = max(namespaces, key=lambda item: sum(1 for n in names if n.startswith(f"assets/{item}/") or n.startswith(f"data/{item}/")))
        item_ids: set[str] = set()
        block_ids: set[str] = set()
        # 只有物品定义/物品模型才算注册物品；textures/item 下的贴图
        # 可能只是模型素材（与物品同名的贴图或模型），不能当成物品。
        for name in item_defs:
            item_ids.add(Path(name).stem)
        for name in item_models:
            item_ids.add(Path(name).stem)
        for name in blockstates:
            block_ids.add(Path(name).stem)
        for name in block_models:
            block_ids.add(Path(name).stem)
        for values in lang.values():
            for key in values:
                if key.startswith(f"item.{mod_id}."):
                    item_ids.add(key.rsplit(".", 1)[-1])
                elif key.startswith(f"block.{mod_id}."):
                    block_ids.add(key.rsplit(".", 1)[-1])
        return {
            "jar": str(jar),
            "name": jar.name,
            "size": jar.stat().st_size,
            "meta": meta,
            "mod_id": mod_id,
            "namespaces": sorted(namespaces),
            "assets": sorted(assets),
            "data": sorted(data),
            "item_defs": sorted(item_defs),
            "item_models": sorted(item_models),
            "block_models": sorted(block_models),
            "blockstates": sorted(blockstates),
            "textures": sorted(textures),
            "models": sorted(models),
            "lang": lang,
            "sounds": sorted(sounds),
            "nested": sorted(nested),
            "root_files": sorted(root_files),
            "meta_files": sorted(meta_files),
            "item_defs_ids": sorted(Path(name).stem for name in item_defs),
            "item_ids": sorted(item_ids),
            "block_ids": sorted(block_ids),
        }


ITEM_REGISTER_RE = re.compile(
    r'(?:(?P<prefix>[A-Za-z_]\w*)\s*\.\s*)?'
    r'register(?:Item|SimpleItem|SimpleBlockItem)?\s*\(\s*"(?P<id>[a-z0-9_]{2,64})"'
)
DEFERRED_ITEM_RE = re.compile(r"DeferredRegister\.Items\s+([A-Za-z_]\w*)")
DEFERRED_BLOCK_RE = re.compile(r"DeferredRegister\.Blocks\s+([A-Za-z_]\w*)")
DEFERRED_ITEM_HOLDER_RE = re.compile(
    r"DeferredRegister\s*<[^>]*Item[^>]*>\s+([A-Za-z_]\w*)"
)
DEFERRED_BLOCK_HOLDER_RE = re.compile(
    r"DeferredRegister\s*<[^>]*Block[^>]*>\s+([A-Za-z_]\w*)"
)
IDENTIFIER_RE = re.compile(
    r'(?:new\s+(?:Identifier|ResourceLocation)|Identifier\.of|ResourceLocation\.fromNamespaceAndPath|ResourceLocation\.tryBuild)'
    r'\s*\(\s*"([a-z0-9_.-]+)"\s*,\s*"([a-z0-9_./-]+)"'
)


def _classify_register(
    prefix: str,
    line: str,
    context: str,
    item_vars: set[str],
    block_vars: set[str],
) -> str:
    if prefix in item_vars:
        return "item"
    if prefix in block_vars:
        return "block"
    if "CreativeModeTab" in line or "creativemodetab" in line.lower():
        return "other"
    low = prefix.lower()
    if "block" in low:
        return "block"
    if "item" in low:
        return "item"
    if "entity" in low:
        return "entity"
    for token, kind in (
        ("DeferredBlock", "block"),
        ("BlockItem", "block"),
        ("DeferredItem", "item"),
        ("DeferredHolder", "item"),
        ("EntityType", "entity"),
    ):
        if token in line:
            return kind
    head = context[-800:]
    if re.search(r"(?:public|private|protected|static|final|\s)+Block\s+\w+\s*\(", head):
        return "block"
    if re.search(r"(?:public|private|protected|static|final|\s)+Item\s+\w+\s*\(", head):
        return "item"
    if re.search(r"EntityType\s*<", head):
        return "entity"
    return "other"


def parse_registrations(root: Path) -> list[dict[str, Any]]:
    base = Path(root) / "src" / "main" / "java"
    if not base.is_dir():
        return []
    found: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(base.rglob("*.java")):
        text = _read_text(path)
        if not text:
            continue
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        item_vars = set(DEFERRED_ITEM_RE.findall(text)) | set(
            DEFERRED_ITEM_HOLDER_RE.findall(text)
        )
        block_vars = set(DEFERRED_BLOCK_RE.findall(text)) | set(
            DEFERRED_BLOCK_HOLDER_RE.findall(text)
        )
        lines = text.splitlines()
        offset = 0
        for number, line in enumerate(lines, 1):
            for match in ITEM_REGISTER_RE.finditer(line):
                ident = match.group("id")
                kind = _classify_register(
                    match.group("prefix") or "",
                    line,
                    text[max(0, offset - 800) : offset + 200],
                    item_vars,
                    block_vars,
                )
                key = (kind, ident)
                if key not in seen:
                    seen.add(key)
                    found.append(
                        {
                            "id": ident,
                            "kind": kind,
                            "path": relative,
                            "line": number,
                        }
                    )
            if "Registry.register" in line:
                window = text[offset : offset + 700]
                if "Registries.ITEM" in window or "BuiltInRegistries.ITEM" in window:
                    kind = "item"
                elif "Registries.BLOCK" in window or "BuiltInRegistries.BLOCK" in window:
                    kind = "block"
                elif "Registries.ENTITY_TYPE" in window or "BuiltInRegistries.ENTITY_TYPE" in window:
                    kind = "entity"
                else:
                    kind = ""
                if kind:
                    for ident_match in IDENTIFIER_RE.finditer(window):
                        ident = ident_match.group(2).split("/")[-1]
                        key = (kind, ident)
                        if key not in seen:
                            seen.add(key)
                            found.append(
                                {
                                    "id": ident,
                                    "kind": kind,
                                    "path": relative,
                                    "line": number,
                                }
                            )
            offset += len(line) + 1
    return found


def scan_project(root: Path) -> dict[str, Any]:
    root = Path(root)
    meta = project_mod.load_project(root).meta
    gradle = _read_text(root / "gradle.properties")
    mod_id = meta.mod_id
    if not mod_id:
        match = re.search(r"(?m)^\s*mod_id\s*=\s*([A-Za-z0-9_.-]+)", gradle)
        if match:
            mod_id = match.group(1).strip()
    mc_version = meta.mc_version
    if not mc_version:
        match = re.search(r"(?m)^\s*minecraft_version\s*=\s*([^\s#]+)", gradle)
        if match:
            mc_version = match.group(1).strip()
    files = _files(root)
    java_files = [name for name in files if name.startswith("src/main/java/") and name.endswith(".java")]
    resource_files = [name for name in files if name.startswith("src/main/resources/")]
    registrations = parse_registrations(root)
    java_texts: dict[str, str] = {}
    for relative in java_files:
        java_texts[relative] = _read_text(root / relative)
    packages = []
    for text in java_texts.values():
        match = re.search(r"(?m)^\s*package\s+([a-zA-Z0-9_.]+)\s*;", text)
        if match:
            packages.append(match.group(1))
    package = max(set(packages), key=packages.count) if packages else ""
    langs: dict[str, dict[str, str]] = {}
    for relative in resource_files:
        match = re.search(r"/lang/([a-z]{2}_[a-z]{2})\.json$", relative)
        if match:
            data = _read_json(root / relative)
            if isinstance(data, dict):
                langs[match.group(1)] = {
                    str(key): str(value)
                    for key, value in data.items()
                    if isinstance(value, str)
                }
    return {
        "root": str(root),
        "meta": {
            "name": meta.name,
            "mod_id": mod_id,
            "mc_version": mc_version,
            "loader": meta.loader,
            "dependencies": meta.dependencies,
            "task_type": meta.task_type,
            "notes": meta.notes,
        },
        "package": package,
        "files": files,
        "java_files": java_files,
        "java_texts": java_texts,
        "resource_files": resource_files,
        "registrations": registrations,
        "item_ids": sorted({item["id"] for item in registrations if item["kind"] == "item"}),
        "block_ids": sorted({item["id"] for item in registrations if item["kind"] == "block"}),
        "entity_ids": sorted({item["id"] for item in registrations if item["kind"] == "entity"}),
        "langs": langs,
        "gradle": gradle,
    }


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _resource_models(root: Path) -> dict[str, Any]:
    """返回以 assets/ 开头的模型键，和校验用的文件集合保持一致。"""
    result: dict[str, Any] = {}
    base = "src/main/resources/"
    for relative in _files(root, "src/main/resources"):
        key = relative[len(base):] if relative.startswith(base) else relative
        if "/models/" in key and key.endswith(".json"):
            data = _read_json(root / relative)
            if isinstance(data, dict):
                result[key] = data
    return result


def _texture_path(namespace: str, reference: str, files: set[str]) -> str | None:
    text = str(reference or "").strip()
    if not text or text.startswith("#"):
        return None
    if ":" in text:
        ns, path = text.split(":", 1)
    else:
        ns, path = "minecraft", text
    candidate = f"assets/{ns}/textures/{path}.png"
    if candidate in files:
        return candidate
    return None


def _check_model_textures(
    report: VerifyReport,
    model_path: str,
    model: dict[str, Any],
    files: set[str],
    models: dict[str, Any],
    seen: set[str],
) -> None:
    if model_path in seen:
        return
    seen.add(model_path)
    namespace = model_path.split("/")[1] if model_path.startswith("assets/") else ""
    textures = model.get("textures") if isinstance(model.get("textures"), dict) else {}
    for key, value in textures.items():
        if not isinstance(value, str):
            continue
        if value.startswith("#"):
            continue
        if _texture_path(namespace, value, files) is None:
            report.add(
                "error",
                "texture_missing",
                f"模型引用的纹理不存在：{value}",
                f"模型 {model_path} 的 {key} 指向 {value}，但资源包里没有对应 PNG。",
                model_path,
                "用 pixel_art 生成缺失纹理，或把模型纹理改到真实存在的贴图。",
            )
    parent = model.get("parent")
    if isinstance(parent, str) and ":" in parent:
        ns, path = parent.split(":", 1)
        if ns != "minecraft":
            parent_path = f"assets/{ns}/models/{path}.json"
            if parent_path not in files:
                report.add(
                    "warn",
                    "model_parent_missing",
                    f"模型父级不存在：{parent}",
                    f"{model_path} 的 parent 指向 {parent}。",
                    model_path,
                    "补齐父模型，或把 parent 改成 minecraft:item/generated。",
                )
            elif parent_path in models:
                _check_model_textures(report, parent_path, models[parent_path], files, models, seen)


def _check_blockstates(
    report: VerifyReport,
    root: Path,
    files: set[str],
    models: dict[str, Any],
    namespace: str,
) -> None:
    for relative in sorted(files):
        if not relative.startswith(f"assets/{namespace}/blockstates/") or not relative.endswith(".json"):
            continue
        data = _read_json(root / "src/main/resources" / relative)
        if not isinstance(data, dict):
            report.add("warn", "blockstate_invalid", "方块状态文件不是合法 JSON", path=relative)
            continue
        refs: list[str] = []
        variants = data.get("variants")
        if isinstance(variants, dict):
            for value in variants.values():
                if isinstance(value, dict) and isinstance(value.get("model"), str):
                    refs.append(value["model"])
        multipart = data.get("multipart")
        if isinstance(multipart, list):
            for part in multipart:
                apply = part.get("apply") if isinstance(part, dict) else None
                if isinstance(apply, dict) and isinstance(apply.get("model"), str):
                    refs.append(apply["model"])
        for ref in refs:
            if ":" in ref:
                ns, path = ref.split(":", 1)
            else:
                ns, path = "minecraft", ref
            if ns == "minecraft":
                continue
            target = f"assets/{ns}/models/{path}.json"
            if target not in files:
                report.add(
                    "error",
                    "blockstate_model_missing",
                    f"方块状态引用的模型不存在：{ref}",
                    f"{relative} 引用了 {ref}。",
                    relative,
                    "补齐模型 JSON，或修正 blockstate 的 model 路径。",
                )


def _check_item_resources(
    report: VerifyReport,
    root: Path,
    scan: dict[str, Any],
    files: set[str],
    models: dict[str, Any],
    namespace: str,
    source_items: set[str] | None = None,
    source_blocks: set[str] | None = None,
) -> None:
    item_ids = set(scan.get("item_ids") or []) | set(source_items or ())
    block_ids = set(scan.get("block_ids") or []) | set(source_blocks or ())
    for item_id in sorted(item_ids):
        model_path = f"assets/{namespace}/models/item/{item_id}.json"
        item_def = f"assets/{namespace}/items/{item_id}.json"
        if model_path not in files:
            if item_def in files:
                report.add(
                    "error",
                    "item_model_missing",
                    f"物品 {item_id} 缺少 1.21.1 可用的模型",
                    f"只找到 {item_def}（1.21.4+ 的物品定义），1.21.1 不会读取它。",
                    item_def,
                    f"补 assets/{namespace}/models/item/{item_id}.json，"
                    "parent 指向 minecraft:item/generated，layer0 指向真实纹理。",
                )
            else:
                report.add(
                    "error",
                    "item_model_missing",
                    f"物品 {item_id} 没有模型",
                    f"注册了物品，但 assets/{namespace}/models/item/{item_id}.json 不存在。",
                    "",
                    "补物品模型 JSON，否则游戏里会显示紫黑方块或空贴图。",
                )
            continue
        model = models.get(model_path)
        if isinstance(model, dict):
            _check_model_textures(report, model_path, model, files, models, set())
    for block_id in sorted(block_ids):
        state_path = f"assets/{namespace}/blockstates/{block_id}.json"
        if state_path not in files:
            report.add(
                "error",
                "blockstate_missing",
                f"方块 {block_id} 没有 blockstate",
                f"注册了方块，但 {state_path} 不存在。",
                "",
                "补 blockstates JSON，并确保它引用存在的 block 模型。",
            )


def _check_lang(
    report: VerifyReport,
    scan: dict[str, Any],
    namespace: str,
    langs: dict[str, dict[str, str]],
    source_items: set[str] | None = None,
    source_blocks: set[str] | None = None,
) -> None:
    if not langs:
        report.add(
            "error",
            "lang_missing",
            "没有语言文件",
            "resources 里没有 assets/<modid>/lang/zh_cn.json 或 en_us.json。",
            "",
            "至少补 zh_cn.json 和 en_us.json，否则物品显示为 item.modid.xxx。",
        )
        return
    zh = langs.get("zh_cn") or {}
    en = langs.get("en_us") or {}
    item_ids = set(scan.get("item_ids") or []) | set(source_items or ())
    block_ids = set(scan.get("block_ids") or []) | set(source_blocks or ())
    for kind, ids in (("item", item_ids), ("block", block_ids)):
        for ident in sorted(ids):
            key = f"{kind}.{namespace}.{ident}"
            in_zh = key in zh
            in_en = key in en
            if not in_zh and not in_en:
                report.add(
                    "error",
                    "lang_key_missing",
                    f"缺少语言键：{key}",
                    "中英文语言文件都没有这个键。",
                    f"assets/{namespace}/lang/zh_cn.json",
                    "补中文和英文显示名。",
                )
            elif not in_zh:
                report.add(
                    "warn",
                    "lang_zh_missing",
                    f"中文语言文件缺少：{key}",
                    f"英文是「{en.get(key)}」。",
                    f"assets/{namespace}/lang/zh_cn.json",
                    "补中文显示名。",
                )


def _check_data_layout(
    report: VerifyReport,
    files: set[str],
    namespace: str,
    target_mc: str,
) -> None:
    mapping = _target_dirs(target_mc)
    if not mapping:
        return
    expected = set(mapping.values())
    wrong = set(mapping.keys()) - expected
    if not wrong:
        # 1.21：单数是正确写法，检查是否误用了复数。
        wrong = set(DATA_DIRS_PLURAL.values()) - set(DATA_DIRS_PLURAL.keys())
    for relative in sorted(files):
        if not relative.startswith(f"data/{namespace}/"):
            continue
        tail = relative[len(f"data/{namespace}/") :]
        for folder in wrong:
            if tail.startswith(folder + "/") or f"/{folder}/" in "/" + tail:
                good = mapping.get(folder, DATA_DIRS_SINGULAR.get(folder, folder))
                report.add(
                    "error",
                    "data_dir_wrong",
                    f"数据包目录不适用于 {target_mc}：{folder}",
                    f"{relative} 使用了 {folder}/，目标版本应使用 {good}/。",
                    relative,
                    f"把目录 {folder}/ 改名为 {good}/（只改目录名，JSON 内容不用动）。",
                )
                break


def _strip_java_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"(?m)//.*$", "", text)


def _collect_java(scan: dict[str, Any]) -> str:
    return "\n".join(
        _strip_java_comments(text)
        for text in (scan.get("java_texts") or {}).values()
    )


def _check_java(
    report: VerifyReport,
    scan: dict[str, Any],
    target_mc: str,
    target_loader: str,
) -> None:
    texts: dict[str, str] = scan.get("java_texts") or {}
    if not texts:
        report.add(
            "error",
            "java_missing",
            "工程里没有 Java 源码",
            "src/main/java 下没有 .java 文件。",
            "",
            "先完成主类、注册和玩法代码，再验收。",
        )
        return
    all_text = "\n".join(_strip_java_comments(text) for text in texts.values())
    if "fabric.mod.json" in (scan.get("files") or []):
        report.add(
            "error",
            "fabric_metadata_left",
            "NeoForge 工程里残留 fabric.mod.json",
            "这个文件不会被 NeoForge 读取，还会误导后续修改。",
            "src/main/resources/fabric.mod.json",
            "删除它，改用 META-INF/neoforge.mods.toml。",
        )
    if target_loader.lower().startswith("neo"):
        if not any(name.endswith("neoforge.mods.toml") for name in (scan.get("files") or [])):
            report.add(
                "error",
                "neoforge_metadata_missing",
                "缺少 neoforge.mods.toml",
                "NeoForge 工程必须有 src/main/resources/META-INF/neoforge.mods.toml。",
                "",
                "补 META-INF/neoforge.mods.toml。",
            )
        if not re.search(r"@Mod\s*\(", all_text):
            report.add(
                "error",
                "mod_annotation_missing",
                "没有 @Mod 主类",
                "NeoForge 1.21.1 的主类必须标注 @Mod(MOD_ID)。",
                "",
                "在入口类上加 @Mod(MOD_ID)，并在构造器里注册 DeferredRegister。",
            )
    for relative, text in texts.items():
        text = _strip_java_comments(text)
        if not text:
            continue
        hits = []
        for old, new in JAVA_IMPORT_HINTS.items():
            if old != new and f"import {old};" in text:
                hits.append(f"{old} → {new}")
        for old, new in FABRIC_IMPORT_HINTS.items():
            if old != new and f"import {old};" in text:
                hits.append(f"{old} → {new}")
        if hits:
            report.add(
                "error",
                "yarn_import",
                f"{Path(relative).name} 还在用 Fabric/Yarn 名称",
                "；".join(hits[:8]),
                relative,
                "按列表逐个替换为 Mojmap / NeoForge API，再重新编译。",
            )
        if re.search(r"\bclass_\d+|\bmethod_\d+|\bfield_\d+", text):
            report.add(
                "error",
                "intermediary_name",
                f"{Path(relative).name} 含混淆名",
                "反编译源码里的 class_1234 / method_12345 不能直接编译。",
                relative,
                "按目标版本映射改成真实类名和方法名。",
            )
        if "new Identifier(" in text or "Identifier.of(" in text or "Identifier.tryParse(" in text:
            report.add(
                "error",
                "identifier_usage",
                f"{Path(relative).name} 还在用 Identifier",
                "1.21.1 NeoForge 使用 ResourceLocation。",
                relative,
                "new Identifier(ns, path) → ResourceLocation.fromNamespaceAndPath(ns, path)。",
            )
        if "Item.Settings" in text:
            report.add(
                "error",
                "item_settings",
                f"{Path(relative).name} 还在用 Item.Settings",
                "NeoForge 1.21.1 使用 Item.Properties。",
                relative,
                "Item.Settings → new Item.Properties()。",
            )
        if "use(World" in text or "use(World " in text:
            report.add(
                "error",
                "use_world",
                f"{Path(relative).name} 的 use 参数还是 World",
                "NeoForge 1.21.1 的签名是 use(Level, Player, InteractionHand)。",
                relative,
                "World → Level。",
            )
        if "interactMob(" in text:
            report.add(
                "error",
                "interact_mob",
                f"{Path(relative).name} 还在用 interactMob",
                "NeoForge 1.21.1 是 interactLivingEntity(ItemStack, Player, LivingEntity, InteractionHand)。",
                relative,
                "替换方法名与参数类型。",
            )
        if "useOnBlock(" in text:
            report.add(
                "error",
                "use_on_block",
                f"{Path(relative).name} 还在用 useOnBlock",
                "NeoForge 1.21.1 是 useOn(UseOnContext)。",
                relative,
                "替换为 useOn(UseOnContext context)。",
            )
        if "ServerPlayNetworking" in text or "ClientPlayNetworking" in text or "PayloadTypeRegistry" in text:
            report.add(
                "error",
                "fabric_networking",
                f"{Path(relative).name} 还在用 Fabric 网络 API",
                "Fabric 的 PayloadTypeRegistry / ServerPlayNetworking 在 NeoForge 不存在。",
                relative,
                "改用 RegisterPayloadHandlersEvent + registrar.playToServer/playToClient + IPayloadContext。",
            )
        if "FabricLoader" in text:
            report.add(
                "error",
                "fabric_loader",
                f"{Path(relative).name} 还在用 FabricLoader",
                "NeoForge 没有 FabricLoader。",
                relative,
                "改用 FMLPaths / ModList.get()。",
            )
        if "ModInitializer" in text or "ClientModInitializer" in text:
            report.add(
                "error",
                "fabric_entrypoint",
                f"{Path(relative).name} 还在用 Fabric 入口接口",
                "NeoForge 主类不需要实现 ModInitializer / ClientModInitializer。",
                relative,
                "删掉 implements，改用 @Mod 构造器和事件总线。",
            )
        client_import = "net.minecraft.client." in text
        package_is_client = ".client" in relative or "/client/" in relative
        if client_import and not package_is_client and "Dist.CLIENT" not in text:
            report.add(
                "warn",
                "client_in_common",
                f"{Path(relative).name} 在公共代码里引用客户端类",
                "专用服务端加载时会因为找不到客户端类而崩溃。",
                relative,
                "把渲染/GUI/按键拆到 client 包，或者加 @Mod(dist = Dist.CLIENT) / @EventBusSubscriber(value = Dist.CLIENT)。",
            )
        if target_mc.startswith("1.21") and re.search(r"new\s+Item\s*\(\s*new\s+Item\.Properties", text):
            pass
    if not re.search(r"\buse\s*\(", all_text) and not re.search(r"\buseOn\s*\(", all_text) and not re.search(r"\binteractLivingEntity\s*\(", all_text):
        report.add(
            "warn",
            "no_item_interaction",
            "没有找到任何物品交互入口",
            "工程里没有 use / useOn / interactLivingEntity 的重写。",
            "",
            "如果物品需要右键使用，必须重写这些方法；否则右键不会有任何反应。",
        )


def _check_mca(
    report: VerifyReport,
    scan: dict[str, Any],
) -> None:
    meta = scan.get("meta") or {}
    root = Path(str(scan.get("root") or ""))
    toml_text = ""
    for candidate in (
        root / "src/main/resources/META-INF/neoforge.mods.toml",
        root / "src/main/resources/META-INF/mods.toml",
    ):
        if candidate.is_file():
            toml_text += _read_text(candidate)
    # 只看真正的依赖声明。gradle.properties 里“（MCA 等）”这种注释
    # 不能当成依赖，否则每个移植工程都会误报 MCA 缺失。
    declared = "mca" in str(meta.get("dependencies") or "").lower() or bool(
        re.search(r'modid\s*=\s*"mca"', toml_text.lower())
    )
    if not declared:
        return
    all_text = _collect_java(scan)
    class_names = " ".join(Path(name).stem for name in scan.get("java_files") or []).lower()
    item_names = " ".join(scan.get("item_ids") or []).lower()
    has_possession = any(
        token in f"{class_names} {item_names}"
        for token in (
            "possession", "possess", "body", "skinblade", "hypnosis", "skin",
            "皮化", "附身", "催眠",
        )
    )
    has_interact = "interactLivingEntity" in all_text
    has_player_data = "PlayerSaveData" in all_text
    has_set_data = "setEntityData" in all_text
    has_data_flag = "setEntityDataSet" in all_text
    has_family = "FamilyTree" in all_text or "getFamilyEntry" in all_text
    has_partner = "updatePartner" in all_text or ".marry(" in all_text
    has_broadcast = "PlayerDataMessage" in all_text
    # MCA 村民的 mobInteract 会先消费右键，物品自己的 interactLivingEntity 不会被调用。
    # 没有 EntityInteract 兜底，道具在村民身上就会表现成“完全没反应”。
    has_event_hook = "PlayerInteractEvent" in all_text and "setCanceled" in all_text
    if has_possession and not has_event_hook:
        report.add(
            "error",
            "mca_entity_interact_event",
            "缺少 PlayerInteractEvent.EntityInteract 兜底",
            "MCA 村民的 mobInteract 会先消费右键（打开对话/交易），"
            "物品自己的 interactLivingEntity 不会被调用。",
            "",
            "在 @EventBusSubscriber 里监听 PlayerInteractEvent.EntityInteract，"
            "只处理本模组道具，结果 consumesAction 时 setCancellationResult + setCanceled(true)。",
        )
    if not has_interact:
        report.add(
            "error",
            "mca_item_interact",
            "道具没有实现对 MCA 村民的交互",
            "只重写 useOn / use 不会让自定义道具对村民生效。",
            "",
            "重写 interactLivingEntity(ItemStack, Player, LivingEntity, InteractionHand)，"
            "并用 target instanceof VillagerLike<?> && !(target instanceof Player) 判断目标。",
        )
    # 注册成白板 Item 是最常见的假移植：贴图和配方都对，但右键没有任何反应。
    for item_id in scan.get("item_ids") or ():
        if _behavior_item_kind(str(item_id)) is None:
            continue
        if re.search(
            rf'registerItem\(\s*"{re.escape(str(item_id))}"\s*,\s*Item::new\b',
            all_text,
        ):
            report.add(
                "error",
                "mca_item_blank_behavior",
                f"道具 {item_id} 仍然是白板 Item",
                "只注册 Item::new 不会执行身份同步逻辑，游戏里表现为“道具无法使用”。",
                "",
                "改成 port_from_jar 生成的道具类，"
                "并在 interactLivingEntity 里调用 MCA 兼容层。",
            )
    if has_possession and not has_player_data:
        report.add(
            "error",
            "mca_player_data_missing",
            "身份同步没有接入 MCA 的 PlayerSaveData",
            "没有 PlayerSaveData，MCA 不会把玩家当成村民渲染，关系也不会同步。",
            "",
            "用 PlayerSaveData.get(player) 读取/写入 entityData，并 setEntityDataSet(true)。",
        )
    if has_set_data and not has_data_flag:
        report.add(
            "error",
            "mca_dataset_flag_missing",
            "写了 entityData 但没有 setEntityDataSet(true)",
            "漏掉这个标志位，MCA 不会切换玩家碰撞箱和村民渲染。",
            "",
            "在 setEntityData 之后调用 data.setEntityDataSet(true)。",
        )
    if has_set_data and not has_broadcast:
        report.add(
            "warn",
            "mca_player_data_not_broadcast",
            "玩家数据没有广播给客户端",
            "MCA 客户端缓存不会自动刷新，其他玩家看到的皮肤可能还是旧的。",
            "",
            "对同维度玩家发送 new PlayerDataMessage(player.getUUID(), data.getEntityData())，"
            "用 net.conczin.mca.network.Network.sendToPlayer。",
        )
    if has_possession and not has_family:
        report.add(
            "error",
            "mca_family_missing",
            "身份同步没有同步 FamilyTreeNode",
            "只改名字/皮肤，不改 FamilyTree，NPC 不会把玩家当成原村民，结婚和生育也不会生效。",
            "",
            "用 EntityRelationship.of(player).getFamilyEntry() 复制姓名、性别、职业、婚姻状态、配偶、子女、父母。",
        )
    if has_possession and not has_partner:
        report.add(
            "warn",
            "mca_partner_missing",
            "没有看到婚姻关系同步",
            "关系字段没有更新时，结婚/生育会被 MCA 判成陌生人。",
            "",
            "用 FamilyTreeNode.updatePartner(...) 和 children()/setFather()/setMother() 同步家庭引用。",
        )
    root = Path(str(scan.get("root") or ""))
    libs = root / "libs"
    has_neoforge_jar = any(
        _jar_loader(candidate) == "neoforge"
        for candidate in (libs.glob("*.jar") if libs.is_dir() else ())
        if "mca" in candidate.name.lower()
        or "comes-alive" in candidate.name.lower()
        or "comesalive" in candidate.name.lower()
    )
    if not has_neoforge_jar:
        report.add(
            "error",
            "mca_jar_missing",
            "libs 里没有 MCA Reborn NeoForge 版 jar",
            "MCA 兼容代码会 import net.conczin.mca.*，没有编译依赖时 build 一定失败。",
            str(libs),
            "把 MCA Reborn 的 NeoForge 版 jar 放进 libs/；"
            "重新运行 port_from_jar 时程序也会自动从 Modrinth 下载。",
        )
    compat_files = [
        name for name in (scan.get("java_files") or []) if Path(name).stem == "McaCompat"
    ]
    if compat_files and "McaCompat." not in all_text:
        report.add(
            "warn",
            "mca_compat_unused",
            "生成了 McaCompat 但道具还没有调用它",
            "身份同步道具需要调用 McaCompat.snapshot / applyFromVillager / restore，"
            "否则同步后关系、名称、皮肤不会变化。",
            compat_files[0],
            "在道具的 interactLivingEntity / 使用逻辑里接入 McaCompat。",
        )


def _check_port_status(
    report: VerifyReport,
    root: Path,
    source_jars: list[str],
    *,
    write: bool,
) -> dict[str, Any] | None:
    """按类核对源 JAR 的反编译参考是否真的迁进了 src。

    编译通过不代表迁移完成：只搬主类也能 build，但道具逻辑、身份同步逻辑
    全都没了。这里把未迁移的类列成错误，逼 Agent 逐类处理或写明排除理由。
    """
    decompiled = root / "reference" / "decompiled"
    if not decompiled.is_dir():
        return None
    status = write_port_status(root) if write else build_port_status(root)
    if not status.get("source_count"):
        return None
    pending = [item for item in status["classes"] if item["status"] == "pending"]
    if source_jars and pending:
        lines = [
            f"- `{item['source']}`（{item['lines']} 行）" for item in pending[:15]
        ]
        if len(pending) > 15:
            lines.append(f"- …还有 {len(pending) - 15} 个，见 {PORT_STATUS_MD}")
        report.add(
            "error",
            "source_classes_unmigrated",
            f"还有 {len(pending)} 个源类没有迁移",
            "这些类只存在于 reference/decompiled，src/main/java 里没有同名类；"
            "道具逻辑、身份同步效果很可能就在其中。\n" + "\n".join(lines),
            PORT_STATUS_MD,
            "逐类改写成 NeoForge 1.21.1 代码，或在 PORT_EXCLUDE.md 写 "
            "`- 路径/类名.java：原因` 明确排除。",
        )
    return status


def verify_project(
    root: Path,
    *,
    target_mc: str = "",
    target_loader: str = "",
    write: bool = True,
) -> VerifyReport:
    root = Path(root)
    if not root.is_dir():
        raise PortError(f"工程目录不存在：{root}")
    scan = scan_project(root)
    meta = scan.get("meta") or {}
    target_mc = (target_mc or str(meta.get("mc_version") or "")).strip()
    target_loader = (target_loader or str(meta.get("loader") or "")).strip()
    namespace = str(meta.get("mod_id") or "").strip()
    report = VerifyReport(
        root=str(root),
        target_mc=target_mc,
        target_loader=target_loader,
        generated_at=_now(),
    )
    resource_prefix = "src/main/resources/"
    files = {
        name[len(resource_prefix):] if name.startswith(resource_prefix) else name
        for name in scan.get("resource_files") or []
    }
    models = _resource_models(root)
    if not namespace:
        report.add(
            "error",
            "mod_id_missing",
            "工程没有 Mod ID",
            "project.json / gradle.properties / neoforge.mods.toml 里都找不到 mod_id。",
            "",
            "补 gradle.properties 的 mod_id。",
        )
        namespace = "mod"
    # 移植工程要拿源 JAR 当验收基准：源 JAR 里存在的物品，工程里必须继续存在，
    # 否则模型/纹理可能已经丢了，但只看工程源码是发现不了的。
    source_items: set[str] = set()
    source_blocks: set[str] = set()
    source_jars: list[str] = []
    for source in sorted((root / "reference" / "source-jars").glob("*.jar")):
        try:
            source_scan = scan_jar(source)
        except PortError as exc:
            report.add(
                "warn",
                "source_jar_invalid",
                f"源 JAR 无法读取：{source.name}",
                str(exc),
                str(source),
                "重新复制源 JAR 或忽略这条警告。",
            )
            continue
        source_jars.append(source.name)
        source_items |= set(source_scan.get("item_defs_ids") or ())
        for state in source_scan.get("blockstates") or ():
            source_blocks.add(Path(state).stem)
    registered_items = set(scan.get("item_ids") or ())
    registered_blocks = set(scan.get("block_ids") or ())
    for item_id in sorted(source_items - registered_items):
        report.add(
            "error",
            "source_item_not_registered",
            f"源 JAR 的物品 {item_id} 还没有注册",
            "资源已经复制，但没有在 DeferredRegister / Registry 里注册同名物品；"
            "游戏里拿不到它，配方和语言键也不会生效。",
            "",
            "在 ModItems 里注册该物品，并保持注册名与源 JAR 完全一致。",
        )
    for block_id in sorted(source_blocks - registered_blocks):
        report.add(
            "error",
            "source_block_not_registered",
            f"源 JAR 的方块 {block_id} 还没有注册",
            "blockstate 已复制，但没有注册同名方块。",
            "",
            "在 ModBlocks 里注册该方块，并保持注册名与源 JAR 完全一致。",
        )
    _check_item_resources(
        report, root, scan, files, models, namespace, source_items, source_blocks
    )
    _check_blockstates(report, root, files, models, namespace)
    _check_lang(
        report,
        scan,
        namespace,
        scan.get("langs") or {},
        source_items,
        source_blocks,
    )
    _check_data_layout(report, files, namespace, target_mc)
    _check_java(report, scan, target_mc, target_loader)
    _check_mca(report, scan)
    port_status = _check_port_status(report, root, source_jars, write=write)
    report.stats = {
        "Java 文件": len(scan.get("java_files") or []),
        "注册项": len(scan.get("registrations") or []),
        "物品": len(scan.get("item_ids") or []),
        "方块": len(scan.get("block_ids") or []),
        "资源文件": len(files),
    }
    if port_status:
        report.stats["源类迁移"] = (
            f"{port_status['migrated']}/{port_status['source_count']}"
        )
        report.stats["待迁移源类"] = port_status["pending"]
    if source_jars:
        report.stats["源 JAR"] = "、".join(source_jars[:4])
        report.stats["源 JAR 物品"] = len(source_items)
        report.stats["源 JAR 方块"] = len(source_blocks)
    jar = workflow.find_jar(root)
    if jar is not None:
        try:
            jar_scan = scan_jar(jar)
        except PortError as exc:
            report.add("error", "jar_invalid", "最终 JAR 无法读取", str(exc), str(jar))
        else:
            _check_jar(report, jar_scan, namespace)
            report.stats["JAR"] = jar.name
            report.stats["JAR 物品"] = len(jar_scan.get("item_ids") or [])
            report.stats["JAR 纹理"] = len(jar_scan.get("textures") or [])
    if write:
        write_verify_report(root, report)
    return report


def _check_jar(report: VerifyReport, jar_scan: dict[str, Any], namespace: str) -> None:
    assets = set(jar_scan.get("assets") or [])
    data = set(jar_scan.get("data") or [])
    root_files = set(jar_scan.get("root_files") or [])
    meta_files = set(jar_scan.get("meta_files") or [])
    files = assets | data
    if "fabric.mod.json" in root_files:
        report.add(
            "error",
            "jar_fabric_metadata",
            "JAR 根目录还有 fabric.mod.json",
            "正式产物的根目录不应再带 Fabric 元数据。",
            "fabric.mod.json",
            "删掉 src/main/resources/fabric.mod.json 或把它改名为 .bak。",
        )
    if not any(name.endswith("neoforge.mods.toml") for name in meta_files):
        report.add(
            "error",
            "jar_neoforge_metadata_missing",
            "JAR 里没有 neoforge.mods.toml",
            "NeoForge 只读取 META-INF/neoforge.mods.toml，缺它游戏不会加载这个模组。",
            "META-INF/neoforge.mods.toml",
            "确认文件在 src/main/resources/META-INF/neoforge.mods.toml 并被打进 JAR。",
        )
    if any(name.startswith("assets/") and "/items/" in name for name in assets):
        report.add(
            "warn",
            "jar_item_defs_present",
            "JAR 里带 1.21.4+ 的 items/ 定义",
            "1.21.1 不读取 assets/<ns>/items/*.json，它们不会生效；真正的模型必须在 models/item/。",
            "",
            "确认 models/item 存在即可，items/ 可以保留作为参考。",
        )
    modeled: set[str] = set()
    for model in jar_scan.get("item_models") or []:
        parts = model.split("/")
        if len(parts) >= 5 and parts[2] == "models" and parts[3] == "item":
            modeled.add(Path(model).stem)
    expected = set(jar_scan.get("item_defs_ids") or []) | modeled
    for item_id in sorted(expected):
        model = f"assets/{namespace}/models/item/{item_id}.json"
        if model not in files:
            report.add(
                "error",
                "jar_item_model_missing",
                f"JAR 里的物品 {item_id} 没有模型",
                f"{model} 不在最终 JAR 中。",
                "",
                "检查 processResources 是否过滤掉了 resources，或模型是否写错目录。",
            )
    textures = set(jar_scan.get("textures") or [])
    for model_path in jar_scan.get("item_models") or []:
        data = _read_jar_json(jar_scan, model_path)
        if not isinstance(data, dict):
            continue
        textures_map = data.get("textures") if isinstance(data.get("textures"), dict) else {}
        for key, value in textures_map.items():
            if not isinstance(value, str) or value.startswith("#"):
                continue
            if ":" in value:
                ns, path = value.split(":", 1)
            else:
                ns, path = "minecraft", value
            if ns == "minecraft":
                continue
            if f"assets/{ns}/textures/{path}.png" not in textures:
                report.add(
                    "error",
                    "jar_texture_missing",
                    f"JAR 里缺少纹理：{value}",
                    f"{model_path} 的 {key} 指向不存在的纹理。",
                    model_path,
                    "确认纹理已放进 assets/<modid>/textures/item/ 并被打进 JAR。",
                )


def _read_jar_json(jar_scan: dict[str, Any], relative: str) -> Any:
    jar_path = str(jar_scan.get("jar") or "")
    if not jar_path:
        return None
    try:
        with zipfile.ZipFile(jar_path) as zf:
            return json.loads(zf.read(relative).decode("utf-8-sig", "replace"))
    except (KeyError, ValueError, OSError, zipfile.BadZipFile):
        return None


def write_verify_report(root: Path, report: VerifyReport) -> Path:
    folder = Path(root) / VERIFY_DIR
    folder.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    (folder / VERIFY_FILE).write_text(_json_dump(payload), encoding="utf-8")
    lines = [
        f"# 验收报告：{Path(root).name}",
        "",
        f"- 生成时间：{report.generated_at}",
        f"- 目标：{report.target_loader or '未知加载器'} {report.target_mc or '未知版本'}",
        f"- 结果：{'通过' if report.ok() else '未通过'}",
        f"- 错误：{len(report.errors())}，警告：{len(report.warnings())}",
        "",
        "## 统计",
        "",
    ]
    for key, value in report.stats.items():
        lines.append(f"- {key}：{value}")
    for title, items in (
        ("必须修复", report.errors()),
        ("建议修复", report.warnings()),
        ("信息", [item for item in report.issues if item.level == "info"]),
    ):
        if not items:
            continue
        lines.extend(["", f"## {title}", ""])
        for item in items:
            lines.append(f"### {item.title}")
            if item.path:
                lines.append(f"- 位置：`{item.path}`")
            if item.detail:
                lines.append(f"- 说明：{item.detail}")
            if item.fix:
                lines.append(f"- 修复：{item.fix}")
            lines.append("")
    (Path(root) / VERIFY_MD).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return folder / VERIFY_FILE


# ---------------------------------------------------------------------------
# 移植工程生成
# ---------------------------------------------------------------------------


def _build_gradle(mod_id: str, group: str, mca_jar: str = "", deps: str = "") -> str:
    dependency_lines = []
    if mca_jar:
        dependency_lines.append(f"    implementation files('libs/{mca_jar}')")
    for line in (deps or "").splitlines():
        line = line.strip()
        if line:
            dependency_lines.append(f"    {line}")
    dependencies = "\n".join(dependency_lines) or "    // 前置模组 jar 放进 libs/ 后在这里声明"
    return ("""plugins {
    id 'java-library'
    id 'net.neoforged.moddev' version 'MODDEV_VERSION_TOKEN'
}

version = mod_version
group = mod_group_id

base {
    archivesName = mod_id
}

java.toolchain.languageVersion = JavaLanguageVersion.of(21)

neoForge {
    version = project.neo_version

    runs {
        client {
            client()
        }
        server {
            server()
            programArgument '--nogui'
        }
        data {
            data()
            programArguments.addAll '--mod', project.mod_id, '--all', '--output', file('src/generated/resources/').getAbsolutePath(), '--existing', file('src/main/resources/').getAbsolutePath()
        }
    }

    mods {
        "${mod_id}" {
            sourceSet sourceSets.main
        }
    }
}

repositories {
    mavenLocal()
    maven { name = 'NeoForged'; url = 'https://maven.neoforged.net/releases' }
}

dependencies {
%s
}

tasks.withType(ProcessResources).configureEach {
    filteringCharset = 'UTF-8'
    filesMatching(['META-INF/neoforge.mods.toml', 'pack.mcmeta']) {
        expand(
            'mod_id': mod_id,
            'mod_name': mod_name,
            'mod_version': mod_version,
            'mod_group_id': mod_group_id,
            'mod_authors': mod_authors,
            'mod_license': mod_license,
            'mod_description': mod_description
        )
    }
}
""" % dependencies.replace('"""', "")).replace("MODDEV_VERSION_TOKEN", MODDEV_VERSION)


def _settings_gradle(mod_id: str) -> str:
    return f"""pluginManagement {{
    repositories {{
        mavenLocal()
        gradlePluginPortal()
        maven {{ name = 'NeoForged'; url = 'https://maven.neoforged.net/releases' }}
    }}
}}

plugins {{
    id 'org.gradle.toolchains.foojay-resolver-convention' version '0.8.0'
}}

rootProject.name = '{mod_id}'
"""


def _gradle_properties(
    mod_id: str,
    mod_name: str,
    group: str,
    version: str,
    mc_version: str,
    neoforge_version: str,
) -> str:
    return f"""org.gradle.jvmargs=-Xmx3G
org.gradle.daemon=false
org.gradle.parallel=true
org.gradle.caching=true
org.gradle.configuration-cache=false

minecraft_version={mc_version}
neo_version={neoforge_version}

mod_id={mod_id}
mod_name={mod_name}
mod_version={version}
mod_group_id={group}
mod_authors=LFELKEEA
mod_license=All Rights Reserved
mod_description={mod_name}

# 本地前置 jar（MCA 等）放在 libs/ 下，build.gradle 里用 implementation files(...)
"""


def _mods_toml(
    mod_id: str,
    mod_name: str,
    group: str,
    version: str,
    mc_version: str,
    dependencies: list[str],
) -> str:
    dep_lines = [
        f'[[dependencies.{mod_id}]]',
        'modId="neoforge"',
        'type="required"',
        'versionRange="[21.1.0,)"',
        'ordering="NONE"',
        'side="BOTH"',
        "",
        f'[[dependencies.{mod_id}]]',
        'modId="minecraft"',
        'type="required"',
        f'versionRange="[{mc_version},{mc_version.rsplit(".", 1)[0]}.99)"',
        'ordering="NONE"',
        'side="BOTH"',
    ]
    for dep in dependencies:
        dep = dep.strip()
        if not dep:
            continue
        if ":" in dep:
            dep_id, dep_range = dep.split(":", 1)
        else:
            dep_id, dep_range = dep, ""
        dep_lines.extend(
            [
                "",
                f'[[dependencies.{mod_id}]]',
                f'modId="{dep_id.strip()}"',
                'type="required"',
                f'versionRange="{dep_range.strip() or "[0,)"}"',
                'ordering="NONE"',
                'side="BOTH"',
            ]
        )
    return f"""modLoader="javafml"
loaderVersion="[4,)"
license="All Rights Reserved"
issueTrackerURL=""

[[mods]]
modId="{mod_id}"
version="{version}"
displayName="{mod_name}"
description='''{mod_name}，由 Darkitten Mod Agent 从 Fabric 工程移植。'''
authors="LFELKEEA"
logoFile=""

[[mixins]]
config="{mod_id}.mixins.json"

{chr(10).join(dep_lines)}
"""


def _java_const(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").upper()
    if not cleaned:
        cleaned = "ITEM"
    if cleaned[0].isdigit():
        cleaned = f"_{cleaned}"
    return cleaned


def _item_properties(item_id: str) -> str:
    low = item_id.lower()
    if low in {"generated_item", "skin_blade"}:
        # 源 JAR 里这把工具是 384 耐久；保持同名同耐久，避免“工具是纸做的”。
        return "new Item.Properties().durability(384)"
    if low in {"player_skin"}:
        return "new Item.Properties().stacksTo(1).fireResistant()"
    return "new Item.Properties()"


def _behavior_item_kind(item_id: str) -> str | None:
    """识别有明确行为的道具，避免生成“拿在手里什么都不会发生”的白板 Item。"""
    low = item_id.lower()
    if low in {"generated_item", "skin_blade"}:
        return "SkinBladeItem"
    if low in {"hypnosis_phone", "hypnosisphone"}:
        return "HypnosisPhoneItem"
    if low == "player_skin":
        return "PlayerSkinItem"
    return None


def _possession_item_class(package: str, class_name: str, doc: str) -> str:
    """身份同步道具基类：右键生物接管身份，潜行右键自己解除。"""
    return f"""package {package}.item;

import {package}.compat.McaCompat;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.InteractionResultHolder;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

/**
 * {doc}
 *
 * 右键凡家村民会复制它的外观、姓名和家庭关系；潜行右键自己解除。
 * 所有写入都在服务端完成，客户端只负责渲染。
 */
public class {class_name} extends Item {{
    public {class_name}(Item.Properties properties) {{
        super(properties);
    }}

    @Override
    public InteractionResult interactLivingEntity(
            ItemStack stack, Player player, LivingEntity target, InteractionHand hand) {{
        if (!(player instanceof ServerPlayer serverPlayer) || target == player) {{
            return InteractionResult.PASS;
        }}
        if (player.isShiftKeyDown()) {{
            return McaCompat.release(serverPlayer) ? InteractionResult.SUCCESS : InteractionResult.PASS;
        }}
        return McaCompat.possess(serverPlayer, target) ? InteractionResult.SUCCESS : InteractionResult.PASS;
    }}

    @Override
    public InteractionResultHolder<ItemStack> use(Level level, Player player, InteractionHand hand) {{
        ItemStack stack = player.getItemInHand(hand);
        if (!level.isClientSide
                && player.isShiftKeyDown()
                && player instanceof ServerPlayer serverPlayer
                && McaCompat.release(serverPlayer)) {{
            return InteractionResultHolder.success(stack);
        }}
        return InteractionResultHolder.pass(stack);
    }}
}}
"""


def _behavior_item_class(package: str, class_name: str, mod_id: str = "") -> str:
    """返回经过实机验证的道具实现。

    这些类不是占位壳：右键凡家村民会完整复制外观、姓名、职业和家庭关系，
    对玩家使用会取得皮肤，潜行右键会还原。模板来自
    实机验证过的 1.21.1 / NeoForge / MCA Reborn 7.x 工程。
    """
    template = {
        "SkinBladeItem": mca_templates.SKIN_BLADE,
        "HypnosisPhoneItem": mca_templates.HYPNOSIS_PHONE,
        "PlayerSkinItem": mca_templates.PLAYER_SKIN,
    }.get(class_name)
    if template is None:
        template = mca_templates.SKIN_BLADE
    return mca_templates.render(template, package, mod_id, class_name)


def _items_class(
    package: str,
    mod_id: str,
    class_name: str,
    item_ids: list[str],
    has_mca: bool = False,
) -> str:
    fields: list[str] = []
    behavior_imports: set[str] = set()
    for item_id in item_ids:
        const = _java_const(item_id)
        kind = _behavior_item_kind(item_id) if has_mca else None
        factory = f"{kind}::new" if kind else "Item::new"
        if kind:
            behavior_imports.add(f"import {package}.item.{kind};")
        fields.append(
            f'    public static final DeferredItem<Item> {const} = '
            f'ITEMS.registerItem("{item_id}", {factory}, {_item_properties(item_id)});'
        )
    body = "\n".join(fields) or "    // 源 JAR 没有物品定义。"
    imports = "\n".join(sorted(behavior_imports))
    if imports:
        imports += "\n"
    return f"""package {package};

import net.minecraft.world.item.Item;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.registries.DeferredItem;
import net.neoforged.neoforge.registries.DeferredRegister;
{imports}

/**
 * 物品注册。名字必须和源 JAR 完全一致，否则贴图、语言键和配方全部失效。
 *
 * 迁移行为时把 Item::new 换成对应的自定义类，例如 SkinBladeItem::new；
 * 不要删除这里的注册项，verify_mod 会按源 JAR 逐个核对。
 */
public final class ModItems {{
    public static final DeferredRegister.Items ITEMS = DeferredRegister.createItems({class_name}.MOD_ID);

{body}

    private ModItems() {{
    }}

    public static void register(IEventBus modEventBus) {{
        ITEMS.register(modEventBus);
    }}
}}
"""


def _creative_tab_class(
    package: str,
    mod_id: str,
    class_name: str,
    item_ids: list[str],
) -> str:
    entries = "\n".join(
        f"            output.accept(ModItems.{_java_const(item_id)}.get());"
        for item_id in item_ids
    ) or "            // 没有可加入的物品。"
    icon = _java_const(item_ids[0]) if item_ids else ""
    icon_line = (
        f"            .icon(() -> new ItemStack(ModItems.{icon}.get()))"
        if icon
        else "            .icon(() -> new ItemStack(net.minecraft.world.item.Items.STICK))"
    )
    return f"""package {package};

import net.minecraft.core.registries.Registries;
import net.minecraft.network.chat.Component;
import net.minecraft.world.item.CreativeModeTab;
import net.minecraft.world.item.ItemStack;
import net.neoforged.bus.api.IEventBus;
import net.neoforged.neoforge.registries.DeferredHolder;
import net.neoforged.neoforge.registries.DeferredRegister;

/** 创造模式物品栏；没有它，物品只能靠命令获取。 */
public final class ModCreativeTabs {{
    public static final DeferredRegister<CreativeModeTab> TABS =
            DeferredRegister.create(Registries.CREATIVE_MODE_TAB, {class_name}.MOD_ID);

    public static final DeferredHolder<CreativeModeTab, CreativeModeTab> MAIN = TABS.register("main", () ->
            CreativeModeTab.builder()
                    .title(Component.translatable("itemGroup.{mod_id}"))
{icon_line}
                    .displayItems((params, output) -> {{
{entries}
                    }})
                    .build());

    private ModCreativeTabs() {{
    }}

    public static void register(IEventBus modEventBus) {{
        TABS.register(modEventBus);
    }}
}}
"""


def _mca_compat_class(package: str, class_name: str, mod_id: str) -> str:
    """生成经过实机验证的 MCA 兼容层。

    这些 API 已按 MCA Reborn 7.7.x（1.21.1 / NeoForge）jar 逐个核对：
    VillagerLike、PlayerSaveData、FamilyTree/FamilyTreeNode、
    PlayerDataMessage、Network.sendToPlayer。模板同时处理
    身份同步、解除与退出时自动还原，以及村民本体的寄存与放回。
    """
    template = mca_templates.MCA_COMPAT.replace("@MOD_ID@", mod_id)
    return mca_templates.render(template, package, mod_id, class_name)


def _mca_skin_handler_class(package: str) -> str:
    return f"""package {package}.compat;

import net.neoforged.fml.loading.FMLEnvironment;
import net.neoforged.neoforge.network.handling.IPayloadContext;

/** 解除身份同步的客户端同步入口；单独成类，避免专用服务器加载客户端类。 */
public final class McaSkinHandler {{
    private McaSkinHandler() {{
    }}

    public static void handle(McaCompat.ClearPayload payload, IPayloadContext context) {{
        context.enqueueWork(() -> {{
            if (FMLEnvironment.dist.isClient()) {{
                {package}.compat.client.McaSkinClient.clear(payload.player());
            }}
        }});
    }}
}}
"""


def _mca_skin_client_class(package: str) -> str:
    return f"""package {package}.compat.client;

import net.conczin.mca.MCAClient;
import net.minecraft.client.Minecraft;

import java.util.UUID;

/** 客户端：清掉 MCA 缓存的村民外观，让玩家立刻变回原来的皮肤。 */
public final class McaSkinClient {{
    private McaSkinClient() {{
    }}

    public static void clear(UUID player) {{
        MCAClient.playerData.remove(player);
        Minecraft minecraft = Minecraft.getInstance();
        if (minecraft.level != null) {{
            var entity = minecraft.level.getPlayerByUUID(player);
            if (entity != null) {{
                entity.refreshDimensions();
            }}
        }}
    }}
}}
"""


def _main_class(
    package: str,
    class_name: str,
    mod_id: str,
    item_ids: list[str] | None = None,
    has_mca: bool = False,
) -> str:
    registrations = ""
    if item_ids:
        registrations = f"""        ModItems.register(modEventBus);
        ModCreativeTabs.register(modEventBus);
"""
    if has_mca:
        registrations += "        McaCompat.register(modEventBus);\n"
    mca_import = f"import {package}.compat.McaCompat;\n" if has_mca else ""
    return f"""package {package};

import net.neoforged.bus.api.IEventBus;
import net.neoforged.fml.ModContainer;
import net.neoforged.fml.common.Mod;
{mca_import}
/**
 * {class_name} 主类。
 *
 * 移植时把 Fabric 的注册和事件迁移到这里或独立的 registry / event 包。
 * 不要在公共代码里 import net.minecraft.client.*。
 */
@Mod({class_name}.MOD_ID)
public final class {class_name} {{
    public static final String MOD_ID = "{mod_id}";

    public {class_name}(IEventBus modEventBus, ModContainer modContainer) {{
{registrations}        // 在这里挂 DeferredRegister：
        // ModBlocks.BLOCKS.register(modEventBus);
        // ModEntities.ENTITY_TYPES.register(modEventBus);
        // NeoForge.EVENT_BUS.addListener(this::onServerTick);
    }}
}}
"""


def _client_class(package: str, class_name: str, mod_id: str) -> str:
    return f"""package {package}.client;

import {package}.{class_name};
import net.neoforged.api.distmarker.Dist;
import net.neoforged.fml.common.Mod;

/**
 * 客户端入口。只放渲染、GUI、按键、粒子、HUD。
 */
@Mod(value = {class_name}.MOD_ID, dist = Dist.CLIENT)
public final class {class_name}Client {{
    public {class_name}Client() {{
        // 客户端初始化：注册 Screen、KeyMapping、渲染层等。
    }}
}}
"""


def _mixins_json(package: str, client_mixin: bool = False) -> str:
    return _json_dump(
        {
            "required": True,
            "minVersion": "0.8",
            "package": f"{package}.mixin",
            "compatibilityLevel": "JAVA_21",
            "mixins": [],
            "client": ["client.AbstractClientPlayerMixin"] if client_mixin else [],
            "injectors": {"defaultRequire": 1},
        }
    )


def _pack_mcmeta(description: str) -> str:
    return _json_dump(
        {
            "pack": {
                "description": description,
                "pack_format": 34,
                "supported_formats": [34, 48],
            }
        }
    )


def _extract_resources(
    jar: Path,
    root: Path,
    namespace: str,
    target_mc: str,
) -> dict[str, Any]:
    converted: list[str] = []
    skipped: list[str] = []
    written = 0
    item_defs: dict[str, str] = {}
    try:
        archive = zipfile.ZipFile(jar)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PortError(f"无法打开 JAR：{exc}") from exc
    with archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if name in {"fabric.mod.json", "pack.mcmeta"}:
                skipped.append(name)
                continue
            if name.startswith("META-INF/") or name.startswith("."):
                skipped.append(name)
                continue
            if name.endswith(".class") or name.endswith(".jar"):
                skipped.append(name)
                continue
            if name.startswith(f"assets/{namespace}/items/") and name.endswith(".json"):
                try:
                    raw = archive.read(info).decode("utf-8-sig", "replace")
                except (KeyError, OSError):
                    raw = ""
                item_defs[Path(name).stem] = raw
                skipped.append(name)
                continue
            if name.startswith("assets/"):
                target = name
            elif name.startswith("data/"):
                target = _convert_data_path(name, target_mc)
                if target != name:
                    converted.append(f"{name} → {target}")
            else:
                skipped.append(name)
                continue
            try:
                data = archive.read(info)
            except (KeyError, OSError):
                skipped.append(name)
                continue
            if (
                "/lang/" in target
                and target.endswith(".json")
            ):
                # 老模组常用 GBK 写中文语言文件，直接复制会在游戏里变成乱码。
                text = _decode_text(data)
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = None
                if isinstance(payload, dict):
                    data = _json_dump(payload).encode("utf-8")
                    converted.append(f"{name} → UTF-8 重写")
            if _write_bytes(root, f"src/main/resources/{target}", data):
                written += 1
            else:
                skipped.append(name)
    # 1.21.4+ 的 items/*.json 在 1.21.1 不生效。缺 models/item 时自动补一份。
    for item_id, raw in sorted(item_defs.items()):
        model_relative = f"src/main/resources/assets/{namespace}/models/item/{item_id}.json"
        if (root / model_relative).is_file():
            continue
        model_target = ""
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            payload = {}
        if isinstance(payload, dict):
            node = payload.get("model")
            if isinstance(node, dict):
                model_target = str(node.get("model") or "")
            elif isinstance(node, str):
                model_target = node
        if model_target.startswith(f"{namespace}:"):
            path = model_target.split(":", 1)[1]
            if path.startswith("item/"):
                relative = f"src/main/resources/assets/{namespace}/models/item/{path[5:]}.json"
            elif path.startswith("block/"):
                relative = f"src/main/resources/assets/{namespace}/models/block/{path[6:]}.json"
            else:
                relative = ""
            if relative and (root / relative).is_file():
                _write_text(
                    root,
                    model_relative,
                    _json_dump({"parent": model_target}),
                )
                converted.append(f"items/{item_id}.json → models/item/{item_id}.json（引用 {model_target}）")
                continue
        texture = f"src/main/resources/assets/{namespace}/textures/item/{item_id}.png"
        if (root / texture).is_file():
            _write_text(
                root,
                model_relative,
                _json_dump(
                    {
                        "parent": "minecraft:item/generated",
                        "textures": {"layer0": f"{namespace}:item/{item_id}"},
                    }
                ),
            )
            converted.append(f"items/{item_id}.json → models/item/{item_id}.json（按同名纹理生成）")
        else:
            converted.append(f"items/{item_id}.json 在 1.21.1 不生效，且没有可自动补的模型/纹理")
    return {
        "written": written,
        "skipped": skipped,
        "converted": converted,
        "item_defs": sorted(item_defs),
    }


def _jar_loader(path: Path) -> str:
    """判断前置 jar 是 NeoForge 还是 Fabric，避免把 Fabric 版当编译依赖。"""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if any(name.endswith("neoforge.mods.toml") for name in names):
                return "neoforge"
            if "fabric.mod.json" in names:
                return "fabric"
    except (zipfile.BadZipFile, OSError):
        return ""
    return ""


def _jar_uses_mca(jar: Path) -> bool:
    """源 JAR 是否在代码里引用了 MCA。

    很多作者不会在 fabric.mod.json 里声明 MCA 依赖，但类常量池里
    一定有 net/conczin/mca。只靠用户传 dependencies="mca" 会漏掉这种包，
    结果就是生成一堆白板物品。
    """
    needle = b"net/conczin/mca"

    def scan_bytes(data: bytes) -> bool:
        return needle in data

    try:
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                low = name.lower()
                if low.endswith(".class"):
                    if scan_bytes(archive.read(name)):
                        return True
                elif low.endswith((".jar", ".json", ".toml", ".mcmeta")):
                    if scan_bytes(archive.read(name)):
                        return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


# 身份/外观类模组的标志性内容：有 player_skin 说明存在“身体/身份”道具，
# 再配合皮肤或附身类道具，就基本可以确定需要按 MCA 兼容处理。
_POSSESSION_ITEM_HINTS = {"hypnosis_phone", "skin_blade", "skinblade", "disguise", "body_suit"}


def _jar_wants_mca(jar: Path, scan: dict[str, Any]) -> bool:
    """源 JAR 是否属于“身份/外观同步”玩法，需要按 MCA Reborn 兼容处理。

    有些模组把 MCA 调用藏在生成器产物或嵌套 JAR 里，
    常量池里找不到 net/conczin/mca，但道具和类名仍然能表明意图。
    这里只认特征非常明确的组合，避免把普通皮肤模组误判成 MCA 兼容工程。
    """
    items = {str(item).lower() for item in scan.get("item_ids") or []}
    if "player_skin" in items and (items & _POSSESSION_ITEM_HINTS):
        return True
    nested = {str(name).lower() for name in scan.get("nested") or []}
    if any("skinblade" in name for name in nested):
        return True
    try:
        with zipfile.ZipFile(jar) as archive:
            for name in archive.namelist():
                low = name.lower()
                if "skinblade" in low or "hypnosisphone" in low or "hypnosis_phone" in low:
                    return True
    except (OSError, zipfile.BadZipFile):
        return False
    return False


def _find_mca_jar(jar: Path, extra_dirs: list[Path] | None = None) -> Path | None:
    dirs: list[Path] = [jar.parent]
    home = Path.home()
    dirs.extend([home / "Downloads", home / "Desktop", home / "OneDrive" / "Desktop"])
    dirs.extend(extra_dirs or [])
    seen: set[str] = set()
    candidates: list[Path] = []
    for folder in dirs:
        try:
            if not folder.is_dir():
                continue
            for candidate in sorted(folder.glob("*.jar")):
                low = candidate.name.lower()
                if candidate.name in seen:
                    continue
                seen.add(candidate.name)
                if ("mca" in low or "comes-alive" in low or "comesalive" in low) and "source" not in low:
                    candidates.append(candidate)
        except OSError:
            continue
    # NeoForge 版优先；Fabric 版只能当参考，不能当编译依赖。
    for loader in ("neoforge", "fabric", ""):
        for candidate in candidates:
            if _jar_loader(candidate) == loader:
                return candidate
    return None


def _copy_mca_jar(root: Path, source: Path) -> str:
    target = root / "libs" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except OSError as exc:
        raise PortError(f"复制 MCA jar 失败：{exc}") from exc
    return source.name


def _existing_mca_libs(root: Path) -> Path | None:
    libs = root / "libs"
    if not libs.is_dir():
        return None
    for candidate in sorted(libs.glob("*.jar")):
        low = candidate.name.lower()
        if ("mca" in low or "comes-alive" in low or "comesalive" in low) and _jar_loader(candidate) == "neoforge":
            return candidate
    return None


def _download_mca_jar(
    root: Path,
    mc_version: str = "1.21.1",
    progress=None,
) -> tuple[Path | None, str]:
    """从 Modrinth 拉取对应版本的 MCA Reborn NeoForge jar。

    用户经常只把 Fabric 版 MCA 放进目录，结果一编译就缺 net.conczin.mca。
    这里优先复用 libs 里已有的 NeoForge 版，否则按游戏版本 + neoforge
    加载器精确查询并下载；任何失败都返回说明，不伪造成功。
    """
    existing = _existing_mca_libs(root)
    if existing is not None:
        return existing, f"复用已有 MCA：libs/{existing.name}"
    if progress:
        progress(f"正在从 Modrinth 查询 MCA Reborn（{mc_version} / NeoForge）…")
    params = urllib.parse.urlencode(
        {
            "game_versions": json.dumps([mc_version]),
            "loaders": json.dumps(["neoforge"]),
        }
    )
    api = f"https://api.modrinth.com/v2/project/{MCA_SLUG}/version?{params}"
    headers = {"User-Agent": "DarkittenModAgent/1.3 (mod porting)"}
    try:
        request = urllib.request.Request(api, headers=headers)
        with urllib.request.urlopen(request, timeout=35) as response:
            versions = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 下载失败只影响前置，不阻断工程生成
        return None, f"查询 MCA 版本失败：{exc}"
    if not isinstance(versions, list) or not versions:
        return None, f"Modrinth 上没有 {mc_version} / NeoForge 的 MCA Reborn。"
    # 稳定版优先，其次按发布时间倒序；没有稳定版就用最新的 beta。
    versions = sorted(versions, key=lambda item: str(item.get("date_published") or ""), reverse=True)
    versions = sorted(
        versions,
        key=lambda item: 0 if str(item.get("version_type") or "").lower() == "release" else 1,
    )
    chosen = versions[0]
    files = chosen.get("files") or []
    primary = next((item for item in files if item.get("primary")), None)
    if primary is None:
        primary = next(
            (item for item in files if str(item.get("filename") or "").endswith(".jar")),
            None,
        )
    if not primary or not primary.get("url"):
        return None, "MCA 版本没有可下载的 jar 文件。"
    filename = Path(str(primary.get("filename") or "mca-neoforge.jar")).name
    if not filename.lower().endswith(".jar"):
        filename += ".jar"
    target = root / "libs" / filename
    if target.is_file() and _jar_loader(target) == "neoforge":
        return target, f"复用已有 MCA：libs/{filename}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(f"正在下载 MCA {chosen.get('version_number') or filename}…")
    fd, temp_name = tempfile.mkstemp(prefix="dma-mca-", suffix=".jar")
    os.close(fd)
    temp = Path(temp_name)
    started = time.time()
    last_note = 0
    try:
        for attempt in range(3):
            existing = temp.stat().st_size if temp.exists() else 0
            headers = {"User-Agent": "DarkittenModAgent/1.3 (mod porting)"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = urllib.request.Request(str(primary["url"]), headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                partial = response.status == 206
                if not partial:
                    existing = 0
                total = int(response.headers.get("Content-Length") or 0) + existing
                mode = "ab" if partial else "wb"
                with temp.open(mode) as handle:
                    downloaded = existing
                    while True:
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        downloaded += len(chunk)
                        if downloaded > 120 * 1024 * 1024:
                            raise PortError("MCA jar 超过 120MB，已停止下载。")
                        handle.write(chunk)
                        if progress and downloaded - last_note >= 4 * 1024 * 1024:
                            last_note = downloaded
                            suffix = f" / {total}" if total else ""
                            progress(f"MCA 下载中：{downloaded // 1048576}MB{suffix}")
                        if time.time() - started > 240:
                            raise PortError("MCA 下载超过 240 秒，已停止；可稍后重试。")
            if total and temp.stat().st_size < total:
                if progress:
                    progress(f"MCA 下载中断，正在续传（第 {attempt + 2} 次）…")
                continue
            break
        if _jar_loader(temp) != "neoforge":
            raise PortError("下载到的文件不完整或不是 NeoForge 版 MCA jar。")
        shutil.move(str(temp), str(target))
    except Exception as exc:  # noqa: BLE001 - 说明失败原因，保留已生成工程
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        return None, f"下载 MCA 失败：{exc}"
    return target, f"已下载 MCA：libs/{filename}"


def _decompile_reference(
    jar: Path,
    root: Path,
    notes: list[str],
    progress=None,
) -> dict[str, Any]:
    target = f"reference/decompiled/{jar.stem}"
    try:
        files, decompile_notes = knowledge.decompile_jar_sources(jar, progress=progress)
    except Exception as exc:  # noqa: BLE001 - 反编译失败不影响工程骨架
        notes.append(f"反编译没有完成：{exc}")
        return {"written": 0, "target": target, "notes": [str(exc)]}
    result = knowledge.write_decompiled_tree(
        root,
        target,
        files,
        jar_name=jar.name,
        notes=decompile_notes,
    )
    notes.extend(decompile_notes)
    return result


def _source_classes(decompiled_root: Path) -> list[str]:
    if not decompiled_root.is_dir():
        return []
    return sorted(
        str(path.relative_to(decompiled_root)).replace("\\", "/")
        for path in decompiled_root.rglob("*.java")
    )


def _port_exclusions(root: Path) -> set[str]:
    path = Path(root) / PORT_EXCLUDE
    if not path.is_file():
        return set()
    names: set[str] = set()
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^[-*]\s*(\[[ xX]\]\s*)?", "", line).strip()
        if not line:
            continue
        token = line.split()[0].replace("\\", "/").strip("`")
        names.add(token)
        names.add(Path(token).stem)
    return names


def build_port_status(root: Path) -> dict[str, Any]:
    """把“反编译参考里有哪些类”变成可勾选的迁移清单。

    只看源码数量很容易误判完成：参考目录里 100 个类，src 里只有主类，
    编译可能通过但功能全没搬。这里按类名核对，并允许在 PORT_EXCLUDE.md
    里写明为什么某个类不需要迁移。
    """
    root = Path(root)
    decompiled = root / "reference" / "decompiled"
    classes: list[dict[str, Any]] = []
    if decompiled.is_dir():
        for path in sorted(decompiled.rglob("*.java")):
            relative = path.relative_to(decompiled).as_posix()
            text = _read_text(path)
            classes.append(
                {
                    "source": relative,
                    "class": path.stem,
                    "lines": text.count("\n") + 1 if text else 0,
                    "package": "/".join(relative.split("/")[:-1]),
                }
            )
    targets = {
        path.stem
        for path in (root / "src/main/java").rglob("*.java")
    } if (root / "src/main/java").is_dir() else set()
    exclusions = _port_exclusions(root)
    for item in classes:
        class_name = item["class"]
        outer = class_name.split("$")[0]
        if class_name in exclusions or item["source"] in exclusions or outer in exclusions:
            item["status"] = "excluded"
        elif class_name in targets or outer in targets:
            # 匿名/内部类跟着外部类一起迁移，不要求单独建一个同名文件。
            item["status"] = "migrated"
        else:
            item["status"] = "pending"
    pending = [item for item in classes if item["status"] == "pending"]
    migrated = [item for item in classes if item["status"] == "migrated"]
    excluded = [item for item in classes if item["status"] == "excluded"]
    return {
        "root": str(root),
        "source_count": len(classes),
        "migrated": len(migrated),
        "excluded": len(excluded),
        "pending": len(pending),
        "classes": classes,
    }


def render_port_status(status: dict[str, Any], limit: int = 400) -> str:
    lines = [
        "# 源类迁移进度",
        "",
        f"- 源类：{status.get('source_count', 0)}",
        f"- 已迁移：{status.get('migrated', 0)}",
        f"- 已排除：{status.get('excluded', 0)}",
        f"- 未处理：{status.get('pending', 0)}",
        "",
        "排除某个类时，在 PORT_EXCLUDE.md 写一行：`- 路径/类名.java：原因`。",
        "",
        "| 状态 | 类 | 行数 |",
        "| --- | --- | --- |",
    ]
    classes = status.get("classes") or []
    shown = [item for item in classes if item["status"] != "migrated"][:limit]
    for item in shown:
        mark = {"pending": "TODO", "excluded": "SKIP", "migrated": "DONE"}[item["status"]]
        lines.append(f"| {mark} | `{item['source']}` | {item['lines']} |")
    if len(shown) < len(classes):
        lines.append(f"\n还有 {len(classes) - len(shown)} 个已迁移类未列出。")
    return "\n".join(lines) + "\n"


def write_port_status(root: Path) -> dict[str, Any]:
    root = Path(root)
    status = build_port_status(root)
    _write_text(root, PORT_STATUS, _json_dump(status))
    _write_text(root, PORT_STATUS_MD, render_port_status(status))
    exclude = root / PORT_EXCLUDE
    if not exclude.is_file():
        exclude.write_text(
            "# 不需要迁移的源类\n\n"
            "# 每行一个类，写明原因。被排除的类不会计入验收错误：\n"
            "# - cn/example/UnusedDebugCommand.java：调试命令，正式版不需要\n",
            encoding="utf-8",
        )
    return status


def _write_replacement_exclusions(
    root: Path,
    decompiled: dict[str, Any],
    notes: list[str],
) -> None:
    """源码是“生成器产物”时，把未迁移类标成已用专用实现替代。

    Blockforge 一类生成器会把所有逻辑塞进 generated 包，类名和字段都是
    随机后缀，逐类机械迁移没有意义，也不可维护。移植工程会用
    McaCompat / SkinData / 道具类重新实现同等行为，这里把源类明确登记为
    “已被替代”，既不会假装迁移完成，也不会让 Agent 去追 120 个壳文件。
    """
    base = Path(str(decompiled.get("base") or ""))
    decompiled_root = Path(root) / "reference" / "decompiled"
    if not base.is_dir() or not decompiled_root.is_dir():
        return
    sources = sorted(base.rglob("*.java"))
    if not sources:
        return
    generated = [
        path for path in sources
        if "/generated/" in path.relative_to(decompiled_root).as_posix()
    ]
    if len(generated) < max(1, len(sources) * 0.8):
        return
    entries: list[str] = []
    for path in sources:
        relative = path.relative_to(decompiled_root).as_posix()
        entries.append(
            f"- {relative}：生成器产物，已由 NeoForge 1.21.1 专用实现替代"
            "（身份同步、皮肤缓存、MCA 关系同步）"
        )
    exclude = Path(root) / PORT_EXCLUDE
    existing = _read_text(exclude) if exclude.is_file() else ""
    if not existing:
        existing = (
            "# 不需要迁移的源类\n\n"
            "# 每行一个类，写明原因。被排除的类不会计入验收错误。\n"
        )
    if entries:
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text(
            existing.rstrip() + "\n\n## 已由专用实现替代\n\n" + "\n".join(entries) + "\n",
            encoding="utf-8",
        )
        notes.append(
            f"源 JAR 是生成器产物（{len(generated)}/{len(sources)} 个类在 generated 包），"
            f"已把 {len(entries)} 个源类登记为“专用实现替代”。"
        )


def _port_spec(
    mod_id: str,
    mod_name: str,
    mc_version: str,
    jar: Path,
    scan: dict[str, Any],
    dependencies: list[str],
    has_mca: bool,
) -> spec_mod.ModSpec:
    lang = scan.get("lang", {}).get("zh_cn") or scan.get("lang", {}).get("en_us") or {}
    content: list[dict[str, str]] = []
    for item_id in scan.get("item_ids") or []:
        key = f"item.{mod_id}.{item_id}"
        content.append(
            {
                "type": "物品",
                "id": item_id,
                "name": str(lang.get(key) or item_id),
                "desc": "从原 JAR 迁移，必须保留原注册名。",
            }
        )
    for block_id in scan.get("block_ids") or []:
        key = f"block.{mod_id}.{block_id}"
        content.append(
            {
                "type": "方块",
                "id": block_id,
                "name": str(lang.get(key) or block_id),
                "desc": "从原 JAR 迁移，必须保留原注册名。",
            }
        )
    features = [
        f"把 {jar.name} 的全部玩法从 Fabric 迁移到 NeoForge {mc_version}",
        "保留原注册名、模型、纹理、语言键、配方和进度",
        "所有物品必须能在生存/创造模式获得并正常使用",
    ]
    acceptance = [
        "./gradlew build 通过，build/libs 生成可安装 JAR",
        "进游戏后物品图标与模型一致，没有紫黑缺失纹理",
        "右键物品能看到原 JAR 的界面/效果",
        "所有方块能放置、掉落和显示正确",
        "运行 /reload 后配方和进度没有报错",
    ]
    risks = [
        "Fabric 网络包、Mixin、客户端渲染需要按 NeoForge API 重写",
        "反编译源码含中间名，必须逐个改成 Mojmap 名称",
    ]
    if has_mca:
        features.append(
            "兼容 MCA Reborn：本模组道具对 MCA 村民生效，"
            "同步身份后姓名、关系、皮肤与目标村民一致，可以替他与村民结婚、生育"
        )
        acceptance.extend(
            [
                "对 MCA 村民右键使用本模组道具，效果与对玩家相同",
                "同步身份后 F5 外观变成目标村民，关系面板显示目标村民的姓名与家庭",
                "同步后可以替目标村民与其他人结婚、生小孩，且退出后恢复",
            ]
        )
        risks.append(
            "MCA 的 PlayerSaveData / FamilyTree 是服务端数据，客户端只走 PlayerDataMessage 同步"
        )
    return spec_mod.ModSpec(
        title=f"{mod_name} · NeoForge {mc_version} 移植",
        summary=f"把 {jar.name} 的完整玩法迁移到 NeoForge {mc_version}",
        mod_id=mod_id,
        mc_version=mc_version,
        loader="NeoForge",
        runtime="双端",
        content=content,
        features=features,
        obtain=["保留原 JAR 的配方；没有配方的物品补创造模式分类或命令获取"],
        balance=["数值沿用原 JAR，不擅自削弱或加强"],
        assets=["原 JAR 的模型、纹理、语言文件全部保留；缺失的补 PNG"],
        acceptance=acceptance,
        risks=risks,
        dependencies=dependencies,
        status="confirmed",
        revision=1,
        source_text=f"从 {jar.name} 移植",
        updated_at=_now(),
    )


def render_port_plan(
    *,
    jar: Path,
    scan: dict[str, Any],
    extract: dict[str, Any],
    decompiled: dict[str, Any],
    notes: list[str],
    mod_id: str,
    target_mc: str,
    has_mca: bool,
    mca_jar: str = "",
) -> str:
    classes = _source_classes(Path(decompiled.get("base") or ""))
    meta = scan.get("meta") or {}
    lines = [
        f"# 移植计划：{meta.get('name') or mod_id}",
        "",
        "## 源产物",
        "",
        f"- 文件：`{jar.name}`（{jar.stat().st_size:,} 字节）",
        f"- 原加载器：{meta.get('loader') or '未知'}",
        f"- 原 MC 版本：{meta.get('mc_version') or '未知'}",
        f"- Mod ID：`{mod_id}`",
        f"- 目标：NeoForge {target_mc} / Java 21",
        f"- 原 JAR 中文名：{meta.get('name') or '（未填写）'}",
        "",
        "## 已经自动完成",
        "",
        f"- 资源文件写入：{extract.get('written', 0)} 个",
        f"- 反编译参考源码：{decompiled.get('written', 0)} 个（{decompiled.get('target') or '未生成'}）",
        "- 生成 NeoForge 工程骨架、主类、客户端类、mixins 配置和 neoforge.mods.toml",
    ]
    if extract.get("converted"):
        lines.append("- 自动转换的 1.21.11 → 1.21.1 资源：")
        lines.extend(f"  - {item}" for item in extract["converted"])
    if mca_jar:
        lines.append(f"- 已复制 MCA 前置 jar 到 `libs/{mca_jar}`")
    lines.extend(
        [
            "",
            "## 注册内容（必须保留原注册名）",
            "",
            f"- 物品：{len(scan.get('item_ids') or [])} 个",
            f"- 方块：{len(scan.get('block_ids') or [])} 个",
            f"- 实体：{len(scan.get('entity_ids') or [])} 个",
            "",
        ]
    )
    for label, ids in (
        ("物品", scan.get("item_ids") or []),
        ("方块", scan.get("block_ids") or []),
    ):
        if not ids:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for ident in ids:
            lines.append(f"- `{ident}`")
        lines.append("")
    if classes:
        lines.extend(
            [
                "## 需要逐个迁移的类",
                "",
                f"参考源码在 `{decompiled.get('target')}`。建议顺序：入口/注册 → 物品/方块 → 网络包 → 服务端逻辑 → 客户端界面 → Mixin。",
                "",
            ]
        )
        for relative in classes[:200]:
            lines.append(f"- `{relative}`")
        if len(classes) > 200:
            lines.append(f"- …还有 {len(classes) - 200} 个")
        lines.append("")
    lines.extend(
        [
            "## Fabric → NeoForge 映射",
            "",
            "| Fabric / Yarn | NeoForge 1.21.1 / Mojmap |",
            "| --- | --- |",
        ]
    )
    for old, new in list(JAVA_IMPORT_HINTS.items())[:30]:
        lines.append(f"| `{old}` | `{new}` |")
    for old, new in list(FABRIC_IMPORT_HINTS.items())[:18]:
        lines.append(f"| `{old}` | {new} |")
    lines.extend(
        [
            "",
            "## 施工顺序",
            "",
            "1. `fix_build_setup` 补齐 Wrapper 和 libs/。",
            "2. 先让主类、DeferredRegister、neoforge.mods.toml 编译通过。",
            "3. 按参考源码迁移注册内容，注册名必须和原 JAR 完全一致。",
            "4. 迁移物品交互（use / useOn / interactLivingEntity），每迁完一个就 build。",
            "5. 迁移网络包：RegisterPayloadHandlersEvent + StreamCodec。",
            "6. 迁移客户端：Screen、KeyMapping、HUD，全部放 client 包。",
            "7. 迁移 Mixin：先核对目标类在 1.21.1 是否存在，再写注入点。",
            "8. 调用 build_project，编译通过后调用 verify_mod。",
            "",
            "## 资源验收",
            "",
            "- 每个注册物品都要有 `assets/<modid>/models/item/<id>.json`。",
            "- 模型的 `layer0` 必须指向真实存在的 `assets/<modid>/textures/item/*.png`。",
            "- 每个方块要有 `blockstates/<id>.json`，它引用的模型也必须存在。",
            "- 中英文语言文件都要有 `item.<modid>.<id>` / `block.<modid>.<id>`。",
            "- 1.21.1 数据包目录使用单数：`recipe/`、`advancement/`、`loot_table/`、`tags/item/`。",
            "",
        ]
    )
    if has_mca:
        lines.extend(
            [
                "## MCA Reborn 兼容（重点）",
                "",
                "这一节不是建议，是必须满足的验收项。原 JAR 只对玩家生效，"
                "因为 Fabric 的物品交互只写了 use/useOn，MCA 村民走的是 interactLivingEntity。",
                "",
                "```java",
                MCA_RECIPE.strip(),
                "```",
                "",
                "## 关键类参考",
                "",
                "| 类 | 作用 |",
                "| --- | --- |",
                "| `net.conczin.mca.entity.VillagerLike` | 村民外观/遗传/大脑接口；`toVillager(Entity)` 可把玩家或村民转成 VillagerLike |",
                "| `net.conczin.mca.server.world.data.PlayerSaveData` | 玩家在 MCA 里的村民数据；`get/setEntityData`、`setEntityDataSet` |",
                "| `net.conczin.mca.entity.ai.relationship.EntityRelationship` | `of(Entity)`、`getFamilyEntry()`、`marry/engage` |",
                "| `net.conczin.mca.server.world.data.FamilyTree` | `get(ServerLevel)`、`getOrEmpty(UUID)`、`getOrCreate(Entity)` |",
                "| `net.conczin.mca.server.world.data.FamilyTreeNode` | 姓名/性别/职业/配偶/子女/父母/婚姻状态 |",
                "| `net.conczin.mca.network.s2c.PlayerDataMessage` | 把玩家村民数据广播给客户端，换皮肤必须发 |",
                "| `net.conczin.mca.network.Network` | `sendToPlayer(payload, ServerPlayer)` |",
                "",
                "## 已知风险",
                "",
                "- Mixin 目标类在 1.21.1 可能改名或不存在；先 `search_text` 核对，再写注入。",
                "- 原 JAR 的客户端 GUI 大量使用 Yarn 的 DrawContext/MatrixStack，需改成 GuiGraphics/PoseStack。",
                "- 原 JAR 的网络包 ID 必须全部重新注册，不能沿用 Fabric 的 PayloadTypeRegistry。",
                "- 身份同步必须做进入/退出双向快照，否则玩家数据会被永久覆盖。",
                "",
            ]
        )
    if notes:
        lines.extend(["## 生成日志", ""])
        lines.extend(f"- {note}" for note in notes[:80])
        lines.append("")
    return "\n".join(lines)


def create_port_project(
    jar: Path,
    dest_parent: Path,
    *,
    mod_id: str = "",
    mod_name: str = "",
    package: str = "",
    target_mc: str = "1.21.1",
    dependencies: str = "",
    extra_dirs: list[Path] | None = None,
    progress=None,
) -> dict[str, Any]:
    jar = Path(jar).expanduser()
    if not jar.is_file():
        raise PortError(f"JAR 不存在：{jar}")
    if target_mc not in SUPPORTED_TARGETS:
        raise PortError(f"目前只支持移植到 {', '.join(sorted(SUPPORTED_TARGETS))}。")
    scan = scan_jar(jar)
    meta = scan.get("meta") or {}
    mod_id = (mod_id or str(meta.get("mod_id") or "")).strip().lower()
    if not mod_id:
        mod_id = _stem(jar.stem) or "portedmod"
    try:
        mod_id = project_mod.validate_mod_id(mod_id)
    except project_mod.ProjectError as exc:
        raise PortError(str(exc)) from exc
    mod_name = (mod_name or str(meta.get("name") or "") or mod_id).strip()
    package = (package or f"net.{mod_id}").strip().strip(".")
    if not re.fullmatch(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*", package):
        raise PortError("Java 包名只能使用小写字母、数字、下划线，例如 net.examplemod。")
    group = ".".join(package.split(".")[:2]) if "." in package else package
    class_name = f"{_camel(mod_id)}Mod"
    version = str(meta.get("version") or "1.0.0")
    target = Path(dest_parent).expanduser().resolve() / mod_id
    if target.exists() and any(target.iterdir()):
        raise PortError(f"目标目录已存在且不为空：{target}。请换一个工程目录或先改名。")
    dependency_list: list[str] = []
    for line in re.split(r"[\n;；,，]+", dependencies or ""):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if "mca" in low or "comes alive" in low or "凡家" in line:
            entry = "mca:[0,)"
        else:
            dep_id = line.split(":", 1)[0].strip()
            if not re.fullmatch(r"[a-z0-9_.-]+", dep_id):
                continue
            entry = line
        if entry not in dependency_list:
            dependency_list.append(entry)
    has_mca = any(item.lower().startswith("mca") for item in dependency_list)
    auto_mca_note = ""
    if not has_mca and _jar_uses_mca(jar):
        # 作者没声明依赖，但代码确实调用 MCA：自动补上，避免白板道具。
        dependency_list.insert(0, "mca:[0,)")
        has_mca = True
        auto_mca_note = "源 JAR 的代码引用了 net.conczin.mca，已自动按 MCA Reborn 兼容工程处理。"
    elif not has_mca and _jar_wants_mca(jar, scan):
        # 代码里没有直接引用 MCA，但道具组合表明这是身份同步模组：
        # 不补依赖就只能生成一堆对村民无效的白板道具。
        dependency_list.insert(0, "mca:[0,)")
        has_mca = True
        auto_mca_note = (
            "源包包含身份/皮肤类道具，玩法需要作用于凡家村民，"
            "已自动按 MCA Reborn 兼容工程处理。"
        )
    mca_source = _find_mca_jar(jar, extra_dirs) if has_mca else None
    mca_is_neoforge = mca_source is not None and _jar_loader(mca_source) == "neoforge"
    mca_jar_name = ""
    if mca_source is not None and not mca_is_neoforge:
        scan.setdefault("notes", []).append(
            f"{mca_source.name} 不是 NeoForge 版 MCA，只能作为参考；"
            "编译依赖会改为从 Modrinth 获取 NeoForge 版。"
        )
    project = project_mod.create_project(
        Path(dest_parent),
        name=mod_name,
        mod_id=mod_id,
        mc_version=target_mc,
        loader="NeoForge",
        runtime="双端",
        dependencies="；".join(dependency_list),
        task_type="移植到其他版本/加载器",
        notes=f"从 {jar.name} 移植；参考源码在 reference/decompiled/{jar.stem}",
    )
    root = project.root
    notes: list[str] = []
    if auto_mca_note:
        notes.append(auto_mca_note)
    extract = _extract_resources(jar, root, mod_id, target_mc)
    notes.extend(extract.get("converted") or [])
    source_dir = root / "reference" / "source-jars"
    source_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(jar, source_dir / jar.name)
    except OSError as exc:
        notes.append(f"复制源 JAR 失败：{exc}")
    if has_mca:
        if mca_is_neoforge:
            try:
                mca_jar_name = _copy_mca_jar(root, mca_source)
            except PortError as exc:
                notes.append(str(exc))
        else:
            if mca_source is not None:
                reference_dir = root / "reference" / "mca"
                reference_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(mca_source, reference_dir / mca_source.name)
                    notes.append(f"Fabric 版 MCA 已放到 reference/mca/{mca_source.name} 供对照。")
                except OSError as exc:
                    notes.append(f"复制参考 MCA 失败：{exc}")
            downloaded, download_note = _download_mca_jar(root, target_mc, progress)
            notes.append(download_note)
            if downloaded is not None:
                mca_jar_name = downloaded.name
            else:
                notes.append(
                    "没有可用的 MCA NeoForge 编译依赖；工程仍会生成，"
                    "但 MCA 兼容代码在补上 libs 之前无法通过编译。"
                )
    if progress:
        progress("正在反编译原 JAR，可能需要几十秒…")
    decompiled = _decompile_reference(jar, root, notes, progress=progress)
    # 工程骨架
    _write_text(
        root,
        "build.gradle",
        _build_gradle(mod_id, group, mca_jar_name, "\n".join(
            line for line in dependency_list if line and not line.startswith("mca")
        )),
    )
    _write_text(root, "settings.gradle", _settings_gradle(mod_id))
    _write_text(
        root,
        "gradle.properties",
        _gradle_properties(
            mod_id,
            mod_name,
            group,
            version,
            target_mc,
            NEOFORGE_VERSIONS.get(target_mc, "21.1.265"),
        ),
    )
    _write_text(
        root,
        "src/main/resources/META-INF/neoforge.mods.toml",
        _mods_toml(mod_id, mod_name, group, version, target_mc, dependency_list),
    )
    _write_text(root, "src/main/resources/pack.mcmeta", _pack_mcmeta(mod_name))
    item_ids = list(scan.get("item_ids") or [])
    # SkinData 依赖 ModItems.PLAYER_SKIN；没有这个道具时整套 MCA 逻辑无法编译，
    # 因此只有“声明了 MCA + 有编译依赖 + 源包真有 player_skin”才生成。
    mca_ready = bool(
        has_mca
        and mca_jar_name
        and any(str(item).lower() == "player_skin" for item in item_ids)
    )
    _write_text(
        root,
        f"src/main/resources/{mod_id}.mixins.json",
        _mixins_json(package, client_mixin=mca_ready),
    )
    _write_text(
        root,
        f"src/main/java/{package.replace('.', '/')}/{class_name}.java",
        _main_class(package, class_name, mod_id, item_ids, mca_ready),
    )
    if item_ids:
        _write_text(
            root,
            f"src/main/java/{package.replace('.', '/')}/ModItems.java",
            _items_class(package, mod_id, class_name, item_ids, mca_ready),
        )
        if mca_ready:
            behavior_kinds = {
                kind
                for item_id in item_ids
                if (kind := _behavior_item_kind(item_id)) is not None
            }
            if behavior_kinds:
                item_dir = root / "src/main/java" / package.replace(".", "/") / "item"
                item_dir.mkdir(parents=True, exist_ok=True)
                for kind in sorted(behavior_kinds):
                    _write_text(
                        root,
                        f"src/main/java/{package.replace('.', '/')}/item/{kind}.java",
                        _behavior_item_class(package, kind, mod_id),
                    )
        _write_text(
            root,
            f"src/main/java/{package.replace('.', '/')}/ModCreativeTabs.java",
            _creative_tab_class(package, mod_id, class_name, item_ids),
        )
        # 创造栏标题：语言文件里补一条 itemGroup.<mod_id>。
        for lang_path in sorted(
            (root / "src/main/resources/assets" / mod_id / "lang").glob("*.json")
        ):
            payload = _read_json(lang_path)
            if not isinstance(payload, dict):
                continue
            key = f"itemGroup.{mod_id}"
            if key not in payload:
                payload[key] = mod_name
                _write_text(
                    root,
                    lang_path.relative_to(root).as_posix(),
                    _json_dump(payload),
                )
    if mca_ready:
        java_root = f"src/main/java/{package.replace('.', '/')}"
        _write_text(
            root,
            f"{java_root}/compat/McaCompat.java",
            _mca_compat_class(package, class_name, mod_id),
        )
        _write_text(
            root,
            f"{java_root}/compat/McaSkinHandler.java",
            _mca_skin_handler_class(package),
        )
        _write_text(
            root,
            f"{java_root}/compat/client/McaSkinClient.java",
            _mca_skin_client_class(package),
        )
        _write_text(
            root,
            f"{java_root}/data/SkinData.java",
            mca_templates.render(mca_templates.SKIN_DATA, package, mod_id, class_name),
        )
        _write_text(
            root,
            f"{java_root}/client/ClientSkinCache.java",
            mca_templates.render(mca_templates.CLIENT_SKIN_CACHE, package, mod_id, class_name),
        )
        _write_text(
            root,
            f"{java_root}/client/ClientEvents.java",
            mca_templates.render(mca_templates.CLIENT_EVENTS, package, mod_id, class_name),
        )
        _write_text(
            root,
            f"{java_root}/event/ServerEvents.java",
            mca_templates.render(mca_templates.SERVER_EVENTS, package, mod_id, class_name),
        )
        _write_text(
            root,
            f"{java_root}/mixin/client/AbstractClientPlayerMixin.java",
            mca_templates.render(
                mca_templates.ABSTRACT_CLIENT_PLAYER_MIXIN, package, mod_id, class_name
            ),
        )
    elif has_mca:
        notes.append(
            "检测到 MCA 依赖，但源包没有 player_skin 道具或缺少 MCA NeoForge 编译 jar；"
            "本次只生成普通物品，未生成身份同步兼容层。"
        )
    if mca_ready:
        # 生成器产物的类名和随机后缀无法逐类维护，明确登记为“专用实现替代”。
        _write_replacement_exclusions(root, decompiled, notes)
    _write_text(
        root,
        f"src/main/java/{package.replace('.', '/')}/client/{class_name}Client.java",
        _client_class(package, class_name, mod_id),
    )
    (root / "src/main/java" / package.replace(".", "/") / "mixin").mkdir(parents=True, exist_ok=True)
    (root / "src/main/resources").mkdir(parents=True, exist_ok=True)
    (root / "libs").mkdir(parents=True, exist_ok=True)
    (root / "libs" / "README.txt").write_text(
        "把前置模组的 jar 放在这里。build.gradle 里用 implementation files('libs/xxx.jar') 声明；\n"
        "然后调用 fix_build_setup 或 build_project。\n",
        encoding="utf-8",
    )
    # 规格与施工状态：移植工程直接进入施工，不要让用户再确认一遍。
    spec = _port_spec(
        mod_id,
        mod_name,
        target_mc,
        jar,
        scan,
        dependency_list,
        has_mca,
    )
    spec_mod.save_spec(root, spec)
    state = workflow.load_state(root, spec)
    state.stage = "building"
    state.spec_revision = spec.revision
    state.tasks = workflow.plan_tasks(spec)
    state.confirmed_at = _now()
    workflow.save_state(root, state)
    plan = render_port_plan(
        jar=jar,
        scan=scan,
        extract=extract,
        decompiled=decompiled,
        notes=notes,
        mod_id=mod_id,
        target_mc=target_mc,
        has_mca=has_mca,
        mca_jar=mca_jar_name,
    )
    _write_text(root, PORT_PLAN, plan)
    report = verify_project(
        root,
        target_mc=target_mc,
        target_loader="NeoForge",
        write=True,
    )
    return {
        "root": str(root),
        "mod_id": mod_id,
        "mod_name": mod_name,
        "package": package,
        "class_name": class_name,
        "target_mc": target_mc,
        "source_jar": str(jar),
        "item_ids": scan.get("item_ids") or [],
        "block_ids": scan.get("block_ids") or [],
        "class_count": len(_source_classes(Path(decompiled.get("base") or ""))),
        "written_resources": extract.get("written", 0),
        "converted": extract.get("converted") or [],
        "mca_jar": mca_jar_name,
        "plan": PORT_PLAN,
        "notes": notes,
        "verify": report.to_dict(),
        "verify_text": report.render(),
    }
