"""Mapping translation layer (checklist P0-06 / TOOL-05).

Supports the four formats a mod workspace actually sees:

* Mojang ProGuard mappings (``a -> net.minecraft...``)
* Yarn Tiny v2
* SRG / TSRG
* a built-in Yarn <-> Mojmap alias table for the most common classes/methods

All project code is normalised to the namespace declared by the project before
it reaches the symbol index or the compiler.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ----------------------------------------------------------------------
# Built-in aliases (no external mapping files required)
# ----------------------------------------------------------------------

YARN_TO_MOJMAP_CLASSES = {
    "net.minecraft.world.World": "net.minecraft.world.level.Level",
    "net.minecraft.entity.Entity": "net.minecraft.world.entity.Entity",
    "net.minecraft.entity.player.PlayerEntity": "net.minecraft.world.entity.player.Player",
    "net.minecraft.server.network.ServerPlayerEntity": "net.minecraft.server.level.ServerPlayer",
    "net.minecraft.item.ItemStack": "net.minecraft.world.item.ItemStack",
    "net.minecraft.item.Item": "net.minecraft.world.item.Item",
    "net.minecraft.item.BlockItem": "net.minecraft.world.item.BlockItem",
    "net.minecraft.item.ItemGroup": "net.minecraft.world.item.CreativeModeTab",
    "net.minecraft.block.Block": "net.minecraft.world.level.block.Block",
    "net.minecraft.block.BlockState": "net.minecraft.world.level.block.state.BlockState",
    "net.minecraft.block.entity.BlockEntity": "net.minecraft.world.level.block.entity.BlockEntity",
    "net.minecraft.recipe.Recipe": "net.minecraft.world.item.crafting.Recipe",
    "net.minecraft.util.math.BlockPos": "net.minecraft.core.BlockPos",
    "net.minecraft.util.math.Vec3d": "net.minecraft.world.phys.Vec3",
    "net.minecraft.util.math.Vec3i": "net.minecraft.core.Vec3i",
    "net.minecraft.text.Text": "net.minecraft.network.chat.Component",
    "net.minecraft.util.Identifier": "net.minecraft.resources.ResourceLocation",
    "net.minecraft.screen.ScreenHandler": "net.minecraft.world.inventory.AbstractContainerMenu",
    "net.minecraft.client.MinecraftClient": "net.minecraft.client.Minecraft",
    "net.minecraft.client.gui.screen.Screen": "net.minecraft.client.gui.screens.Screen",
    "net.minecraft.entity.effect.StatusEffect": "net.minecraft.world.effect.MobEffect",
    "net.minecraft.entity.effect.StatusEffectInstance": "net.minecraft.world.effect.MobEffectInstance",
    "net.minecraft.entity.LivingEntity": "net.minecraft.world.entity.LivingEntity",
    "net.minecraft.entity.damage.DamageSource": "net.minecraft.world.damagesource.DamageSource",
    "net.minecraft.sound.SoundEvent": "net.minecraft.sounds.SoundEvent",
    "net.minecraft.enchantment.Enchantment": "net.minecraft.world.item.enchantment.Enchantment",
    "net.minecraft.server.command.ServerCommandSource": "net.minecraft.commands.CommandSourceStack",
    "net.minecraft.world.WorldAccess": "net.minecraft.world.level.LevelAccessor",
    "net.minecraft.world.chunk.WorldChunk": "net.minecraft.world.level.chunk.LevelChunk",
    "net.minecraft.server.world.ServerWorld": "net.minecraft.server.level.ServerLevel",
    "net.minecraft.nbt.NbtCompound": "net.minecraft.nbt.CompoundTag",
    "net.minecraft.nbt.NbtElement": "net.minecraft.nbt.Tag",
    "net.minecraft.inventory.Inventory": "net.minecraft.world.Container",
    "net.minecraft.inventory.SimpleInventory": "net.minecraft.world.SimpleContainer",
    "net.minecraft.registry.Registry": "net.minecraft.core.Registry",
    "net.minecraft.entity.attribute.EntityAttribute": "net.minecraft.world.entity.ai.attributes.Attribute",
    "net.minecraft.entity.attribute.EntityAttributeInstance": "net.minecraft.world.entity.ai.attributes.AttributeInstance",
    "net.minecraft.entity.attribute.EntityAttributeModifier": "net.minecraft.world.entity.ai.attributes.AttributeModifier",
    "net.minecraft.fluid.Fluid": "net.minecraft.world.level.material.Fluid",
    "net.minecraft.fluid.FluidState": "net.minecraft.world.level.material.FluidState",
    "net.minecraft.world.biome.Biome": "net.minecraft.world.level.biome.Biome",
    "net.minecraft.world.gen.feature.Feature": "net.minecraft.world.level.levelgen.feature.Feature",
    "net.minecraft.world.PersistentState": "net.minecraft.world.level.saveddata.SavedData",
    "net.minecraft.entity.player.PlayerInventory": "net.minecraft.world.entity.player.Inventory",
    "net.minecraft.client.network.ClientPlayerEntity": "net.minecraft.client.player.LocalPlayer",
    "net.minecraft.entity.monster.MonsterEntity": "net.minecraft.world.entity.monster.Monster",
    "net.minecraft.entity.passive.AnimalEntity": "net.minecraft.world.entity.animal.Animal",
    "net.minecraft.entity.mob.MobEntity": "net.minecraft.world.entity.Mob",
    "net.minecraft.entity.projectile.ProjectileEntity": "net.minecraft.world.entity.projectile.Projectile",
    "net.minecraft.potion.Potion": "net.minecraft.world.item.alchemy.Potion",
    "net.minecraft.world.event.GameEvent": "net.minecraft.world.level.gameevent.GameEvent",
    "net.minecraft.entity.passive.VillagerEntity": "net.minecraft.world.entity.npc.Villager",
    "net.minecraft.entity.vehicle.BoatEntity": "net.minecraft.world.entity.vehicle.Boat",
    "net.minecraft.block.AbstractBlock": "net.minecraft.world.level.block.state.BlockBehaviour",
    "net.minecraft.util.Hand": "net.minecraft.world.InteractionHand",
    "net.minecraft.util.ActionResult": "net.minecraft.world.InteractionResult",
    "net.minecraft.util.TypedActionResult": "net.minecraft.util.TypedActionResult",
    "net.minecraft.util.hit.BlockHitResult": "net.minecraft.world.phys.BlockHitResult",
    "net.minecraft.util.hit.HitResult": "net.minecraft.world.phys.HitResult",
    "net.minecraft.server.MinecraftServer": "net.minecraft.server.MinecraftServer",
    "net.minecraft.client.gui.DrawContext": "net.minecraft.client.gui.GuiGraphics",
    "net.minecraft.client.font.TextRenderer": "net.minecraft.client.gui.Font",
    "net.minecraft.client.render.VertexConsumerProvider": "net.minecraft.client.renderer.MultiBufferSource",
}

MOJMAP_TO_YARN_CLASSES = {value: key for key, value in YARN_TO_MOJMAP_CLASSES.items()}

YARN_TO_MOJMAP_METHODS = {
    "getWorld": "level",
    "getEntityWorld": "level",
    "sendMessage": "sendSystemMessage",
    "spawnEntity": "addFreshEntity",
    "discard": "remove",
    "isRemoved": "isRemoved",
    "getBlockPos": "blockPosition",
    "getPos": "position",
    "isClient": "isClientSide",
    "getStackInHand": "getItemInHand",
    "setStackInHand": "setItemInHand",
    "getMainHandStack": "getMainHandItem",
    "getOffHandStack": "getOffhandItem",
    "getServer": "getServer",
    "getRandom": "getRandom",
    "getDamageSources": "damageSources",
    "damage": "hurt",
    "heal": "heal",
    "playSound": "playSound",
    "addStatusEffect": "addEffect",
    "removeStatusEffect": "removeEffect",
    "getStatusEffects": "getActiveEffects",
    "getHealth": "getHealth",
    "setHealth": "setHealth",
    "getMaxHealth": "getMaxHealth",
    "getArmor": "getArmorValue",
    "dropStack": "drop",
    "getInventory": "getInventory",
    "insertStack": "addItem",
    "getBlockEntity": "getBlockEntity",
    "getBlockState": "getBlockState",
    "setBlockState": "setBlock",
    "breakBlock": "destroyBlock",
    "updateNeighbors": "updateNeighborsAt",
    "scheduleBlockTick": "scheduleTick",
    "getWorldChunk": "getChunk",
}

MOJMAP_TO_YARN_METHODS = {value: key for key, value in YARN_TO_MOJMAP_METHODS.items()}

YARN_PACKAGE_HINTS = (
    "net.minecraft.entity.",
    "net.minecraft.item.",
    "net.minecraft.block.",
    "net.minecraft.recipe.",
    "net.minecraft.screen.",
    "net.minecraft.sound.",
    "net.minecraft.text.",
    "net.minecraft.util.math.",
    "net.minecraft.inventory.",
    "net.minecraft.enchantment.",
    "net.minecraft.server.world.",
    "net.minecraft.world.chunk.WorldChunk",
)

MOJMAP_PACKAGE_HINTS = (
    "net.minecraft.world.level.",
    "net.minecraft.world.item.",
    "net.minecraft.world.entity.",
    "net.minecraft.world.inventory.",
    "net.minecraft.world.damagesource.",
    "net.minecraft.commands.",
    "net.minecraft.core.",
)


@dataclass
class MappingInfo:
    namespace: str = "mojmap"
    evidence: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    mixed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "evidence": list(self.evidence),
            "files": list(self.files),
            "mixed": list(self.mixed),
        }


class MappingSet:
    """A pair-wise mapping set loaded from a real mapping file."""

    def __init__(self, namespaces: Iterable[str] = ("obf", "named")) -> None:
        self.namespaces = list(dict.fromkeys(namespaces))
        self._class_pairs: dict[tuple[str, str], dict[str, str]] = {}
        self._method_pairs: dict[tuple[str, str], dict[str, str]] = {}
        self._field_pairs: dict[tuple[str, str], dict[str, str]] = {}
        self._method_names: dict[tuple[str, str], dict[str, str]] = {}
        self._field_names: dict[tuple[str, str], dict[str, str]] = {}

    # ------------------------------------------------------------------
    def _add(self, table: dict, key: tuple[str, str], src: str, dst: str) -> None:
        table.setdefault(key, {})[src] = dst

    def _pair(
        self,
        table: dict,
        source: str,
        target: str,
        seen: set[tuple[str, str]] | None = None,
    ) -> dict[str, str]:
        if (source, target) in table:
            return table[(source, target)]
        if (target, source) in table:
            return {value: key for key, value in table[(target, source)].items()}
        visited = seen or set()
        if (source, target) in visited:
            return {}
        visited.add((source, target))
        # Try through a pivot namespace.
        for pivot in self.namespaces:
            if pivot in {source, target}:
                continue
            left = self._pair(table, source, pivot, visited)
            right = self._pair(table, pivot, target, visited)
            if left and right:
                return {src: right[dst] for src, dst in left.items() if dst in right}
        return {}

    def add_class(self, source: str, target: str, src: str, dst: str) -> None:
        self._add(self._class_pairs, (source, target), src.replace(".", "/"), dst.replace(".", "/"))

    def add_method(self, source: str, target: str, src: str, dst: str, *, src_name: str = "", dst_name: str = "") -> None:
        self._add(self._method_pairs, (source, target), src, dst)
        if src_name and dst_name:
            self._add(self._method_names, (source, target), src_name, dst_name)

    def add_field(self, source: str, target: str, src: str, dst: str, *, src_name: str = "", dst_name: str = "") -> None:
        self._add(self._field_pairs, (source, target), src, dst)
        if src_name and dst_name:
            self._add(self._field_names, (source, target), src_name, dst_name)

    # ------------------------------------------------------------------
    def translate_class(self, name: str, *, source: str, target: str) -> str:
        value = str(name or "").replace(".", "/")
        direct = self._pair(self._class_pairs, source, target)
        if value in direct:
            return direct[value].replace("/", ".")
        # Inner classes: translate the outer part and keep the suffix.
        if "$" in value:
            outer, _, inner = value.partition("$")
            if outer in direct:
                return direct[outer].replace("/", ".") + "$" + inner
        return str(name or "")

    def translate_method(self, owner: str, name: str, descriptor: str = "", *, source: str, target: str) -> str:
        owner_key = str(owner or "").replace(".", "/")
        full = f"{owner_key}/{name}"
        if descriptor:
            full += f" {descriptor}"
        direct = self._pair(self._method_pairs, source, target)
        if full in direct:
            return direct[full].split("/")[-1].split(" ")[0]
        names = self._pair(self._method_names, source, target)
        return names.get(name, name)

    def translate_field(self, owner: str, name: str, *, source: str, target: str) -> str:
        full = f"{str(owner or '').replace('.', '/')}/{name}"
        direct = self._pair(self._field_pairs, source, target)
        if full in direct:
            return direct[full].split("/")[-1]
        names = self._pair(self._field_names, source, target)
        return names.get(name, name)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "namespaces": list(self.namespaces),
            "classes": {
                f"{a}->{b}": dict(values) for (a, b), values in self._class_pairs.items()
            },
            "methods": {
                f"{a}->{b}": dict(values) for (a, b), values in self._method_pairs.items()
            },
            "fields": {
                f"{a}->{b}": dict(values) for (a, b), values in self._field_pairs.items()
            },
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MappingSet":
        mapping = cls(raw.get("namespaces") or ("obf", "named"))
        for key, values in (raw.get("classes") or {}).items():
            a, _, b = str(key).partition("->")
            for src, dst in (values or {}).items():
                mapping.add_class(a, b, src, dst)
        for key, values in (raw.get("methods") or {}).items():
            a, _, b = str(key).partition("->")
            for src, dst in (values or {}).items():
                mapping.add_method(a, b, src, dst)
        for key, values in (raw.get("fields") or {}).items():
            a, _, b = str(key).partition("->")
            for src, dst in (values or {}).items():
                mapping.add_field(a, b, src, dst)
        return mapping


# ----------------------------------------------------------------------
# File parsers
# ----------------------------------------------------------------------

def load_proguard(text: str, namespaces: tuple[str, str] = ("obf", "named")) -> MappingSet:
    mapping = MappingSet(namespaces)
    current_obf = ""
    current_named = ""
    for raw in text.splitlines():
        if not raw.strip():
            continue
        if not raw.startswith((" ", "\t")) and "->" in raw:
            left, _, right = raw.partition("->")
            current_obf = left.strip().rstrip(":")
            current_named = right.strip().rstrip(":")
            mapping.add_class(namespaces[0], namespaces[1], current_obf, current_named)
            continue
        if not current_obf or "->" not in raw:
            continue
        body = raw.strip()
        left, _, right = body.partition("->")
        member_name = right.strip()
        tokens = left.split()
        if not tokens:
            continue
        kind = "method" if "(" in left else "field"
        src_name = tokens[-1]
        descriptor = ""
        if kind == "method":
            descriptor = src_name[src_name.find("("):] if "(" in src_name else ""
            src_name = src_name.split("(")[0]
            mapping.add_method(
                namespaces[0], namespaces[1],
                f"{current_obf}/{src_name} {descriptor}",
                f"{current_named}/{member_name}",
                src_name=src_name, dst_name=member_name,
            )
        else:
            mapping.add_field(
                namespaces[0], namespaces[1],
                f"{current_obf}/{src_name}", f"{current_named}/{member_name}",
                src_name=src_name, dst_name=member_name,
            )
    return mapping


def load_tiny(text: str) -> MappingSet:
    lines = text.splitlines()
    if not lines:
        return MappingSet()
    header = lines[0].split("\t")
    namespaces = header[3:] if len(header) >= 4 else ["obf", "named"]
    mapping = MappingSet(namespaces)
    current: dict[str, str] = {}
    for raw in lines[1:]:
        if not raw.strip():
            continue
        if not raw.startswith("\t"):
            parts = raw.split("\t")
            if parts[0] != "c" or len(parts) < 1 + len(namespaces):
                continue
            names = parts[1:1 + len(namespaces)]
            current = dict(zip(namespaces, names))
            for index in range(len(namespaces)):
                for other in range(index + 1, len(namespaces)):
                    mapping.add_class(
                        namespaces[index], namespaces[other],
                        names[index], names[other],
                    )
                    mapping.add_class(
                        namespaces[other], namespaces[index],
                        names[other], names[index],
                    )
            continue
        parts = raw.lstrip("\t").split("\t")
        if not parts or not current:
            continue
        if parts[0] in {"m", "f"} and len(parts) >= 2 + len(namespaces):
            descriptor = parts[1]
            names = parts[2:2 + len(namespaces)]
            target = mapping.add_method if parts[0] == "m" else mapping.add_field
            for index in range(len(namespaces)):
                for other in range(index + 1, len(namespaces)):
                    target(
                        namespaces[index], namespaces[other],
                        f"{current[namespaces[index]]}/{names[index]} {descriptor}".strip(),
                        f"{current[namespaces[other]]}/{names[other]}",
                        src_name=names[index], dst_name=names[other],
                    )
    return mapping


_SRG_CLASS = re.compile(r"^CL:\s*(\S+)\s+(\S+)\s*$")
_SRG_METHOD = re.compile(r"^MD:\s*(\S+)/(\S+)\s*(\([^)]*\)\S*)?\s+(\S+)/(\S+)\s*(\([^)]*\)\S*)?\s*$")
_SRG_FIELD = re.compile(r"^FD:\s*(\S+)/(\S+)\s+(\S+)/(\S+)\s*$")


def load_srg(text: str, namespaces: tuple[str, str] = ("srg", "named")) -> MappingSet:
    mapping = MappingSet(namespaces)
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        class_match = _SRG_CLASS.match(line)
        if class_match:
            mapping.add_class(namespaces[0], namespaces[1], class_match.group(1), class_match.group(2))
            continue
        method_match = _SRG_METHOD.match(line)
        if method_match:
            mapping.add_method(
                namespaces[0], namespaces[1],
                f"{method_match.group(1)}/{method_match.group(2)} {method_match.group(3) or ''}".strip(),
                f"{method_match.group(4)}/{method_match.group(5)} {method_match.group(6) or ''}".strip(),
                src_name=method_match.group(2), dst_name=method_match.group(5),
            )
            continue
        field_match = _SRG_FIELD.match(line)
        if field_match:
            mapping.add_field(
                namespaces[0], namespaces[1],
                f"{field_match.group(1)}/{field_match.group(2)}",
                f"{field_match.group(3)}/{field_match.group(4)}",
                src_name=field_match.group(2), dst_name=field_match.group(4),
            )
    return mapping


def load_tsrg(text: str) -> MappingSet:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return MappingSet()
    header = lines[0].split()
    namespaces = header[1:] if len(header) > 1 else ["obf", "srg"]
    mapping = MappingSet(namespaces)
    current: list[str] = []
    for raw in lines[1:]:
        if raw.startswith("\t") or raw.startswith("    "):
            parts = raw.strip().split()
            if not current or len(parts) < 2:
                continue
            name = parts[0]
            descriptor = ""
            values = parts[1:]
            if "(" in name:
                descriptor = name[name.find("("):]
                name = name.split("(")[0]
            for index, value in enumerate(values[:len(namespaces)]):
                pass
            first = values[0] if values else name
            second = values[1] if len(values) > 1 else first
            mapping.add_method(
                namespaces[0], namespaces[1],
                f"{current[0]}/{name} {descriptor}".strip(),
                f"{current[1]}/{second}",
                src_name=name, dst_name=second,
            )
            continue
        parts = raw.split()
        if len(parts) >= 2:
            current = parts[:len(namespaces)]
            for index in range(len(namespaces)):
                for other in range(index + 1, len(namespaces)):
                    mapping.add_class(
                        namespaces[index], namespaces[other],
                        current[index], current[other],
                    )
    return mapping


def detect_format(text: str) -> str:
    head = text[:2000]
    if head.startswith("tiny"):
        return "tiny"
    if head.startswith("tsrg"):
        return "tsrg"
    if head.lstrip().startswith(("CL:", "MD:", "PK:")):
        return "srg"
    if "->" in head:
        return "proguard"
    return "unknown"


def load_mappings(path: Path) -> MappingSet:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    kind = detect_format(text)
    if kind == "tiny":
        return load_tiny(text)
    if kind == "tsrg":
        return load_tsrg(text)
    if kind == "srg":
        return load_srg(text)
    if kind == "proguard":
        return load_proguard(text)
    return MappingSet()


def load_mapping_dir(directory: Path) -> MappingSet:
    combined = MappingSet()
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".txt", ".tiny", ".tsrg", ".mapping"}:
            continue
        try:
            mapping = load_mappings(path)
        except OSError:
            continue
        for key, values in mapping._class_pairs.items():
            combined._class_pairs.setdefault(key, {}).update(values)
        for key, values in mapping._method_names.items():
            combined._method_names.setdefault(key, {}).update(values)
        for key, values in mapping._field_names.items():
            combined._field_names.setdefault(key, {}).update(values)
    return combined


# ----------------------------------------------------------------------
# Project detection and normalisation
# ----------------------------------------------------------------------

def classify_namespace(name: str) -> str:
    value = str(name or "")
    if value in {"mojmap", "official"}:
        return "mojmap"
    if value in {"yarn", "intermediary"}:
        return "yarn"
    if value in {"srg", "tsrg"}:
        return "srg"
    if value in {"mcp", "searge"}:
        return "mcp"
    if value.startswith("net.minecraft."):
        for hint in MOJMAP_PACKAGE_HINTS:
            if value.startswith(hint):
                return "mojmap"
        for hint in YARN_PACKAGE_HINTS:
            if value.startswith(hint):
                return "yarn"
        if value.startswith("net.minecraft.client.MinecraftClient"):
            return "yarn"
        if value.startswith("net.minecraft.client.Minecraft"):
            return "mojmap"
        if value in MOJMAP_TO_YARN_CLASSES:
            return "mojmap"
        if value in YARN_TO_MOJMAP_CLASSES:
            return "yarn"
    if re.match(r"net\.minecraft\.class_\d+", value) or re.match(r"[a-z]{1,3}$", value):
        return "obf"
    return "unknown"


def normalize_class(name: str, target: str = "mojmap") -> str:
    value = str(name or "")
    source = classify_namespace(value)
    if target == "mojmap":
        return YARN_TO_MOJMAP_CLASSES.get(value, value)
    if target == "yarn":
        return MOJMAP_TO_YARN_CLASSES.get(value, value)
    return value


def normalize_method(name: str, target: str = "mojmap") -> str:
    if target == "mojmap":
        return YARN_TO_MOJMAP_METHODS.get(name, name)
    if target == "yarn":
        return MOJMAP_TO_YARN_METHODS.get(name, name)
    return name


def detect_project(root: Path) -> MappingInfo:
    root = Path(root)
    info = MappingInfo()
    candidates = [
        root / "gradle.properties",
        root / "build.gradle",
        root / "build.gradle.kts",
        root / "settings.gradle",
        root / "settings.gradle.kts",
    ]
    combined = ""
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        combined += "\n" + text
        info.files.append(path.name)
        lowered = text.lower()
        if "officialmojangmappings" in lowered or "@mojmap" in lowered:
            info.namespace = "mojmap"
            info.evidence.append(f"{path.name}: officialMojangMappings")
        elif "net.fabricmc:yarn" in lowered or "yarn_mappings" in lowered:
            info.namespace = "yarn"
            info.evidence.append(f"{path.name}: yarn mappings")
        elif re.search(r"channel\s*[:=]\s*['\"]official['\"]", lowered):
            info.namespace = "mojmap"
            info.evidence.append(f"{path.name}: mappings channel official")
        elif re.search(r"channel\s*[:=]\s*['\"](snapshot|stable)['\"]", lowered):
            info.namespace = "mcp"
            info.evidence.append(f"{path.name}: mappings channel snapshot/stable")
        elif "parchment" in lowered:
            info.namespace = "mojmap"
            info.evidence.append(f"{path.name}: parchment on mojmap")
        elif "neo_version" in lowered:
            info.namespace = "mojmap"
            info.evidence.append(f"{path.name}: NeoForge")
    if not info.evidence:
        info.evidence.append("默认按 NeoForge/Mojmap 处理")
    # Detect mixed imports in source.
    seen: set[str] = set()
    for path in java_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.finditer(r"^\s*import\s+(net\.minecraft\.[\w.]+);", text, re.MULTILINE):
            namespace = classify_namespace(match.group(1))
            if namespace in {"yarn", "mojmap"} and namespace != info.namespace:
                key = f"{path.relative_to(root).as_posix()}:{namespace}"
                if key not in seen:
                    seen.add(key)
                    info.mixed.append(key)
    return info


def java_files(root: Path) -> Iterable[Path]:
    for path in Path(root).rglob("*.java"):
        relative = path.as_posix()
        if any(part in {"build", ".gradle", "out", "run", "target"} for part in relative.split("/")):
            continue
        yield path


class Translator:
    """Normalise any source name to the project's namespace."""

    def __init__(self, namespace: str = "mojmap", mapping: MappingSet | None = None) -> None:
        self.namespace = classify_namespace(namespace) or "mojmap"
        self.mapping = mapping

    def class_name(self, name: str) -> str:
        if self.mapping is not None:
            for source in self.mapping.namespaces:
                if source == self.namespace:
                    continue
                value = self.mapping.translate_class(name, source=source, target=self.namespace)
                if value != name:
                    return value
        return normalize_class(name, self.namespace)

    def method_name(self, owner: str, name: str, descriptor: str = "") -> str:
        if self.mapping is not None:
            for source in self.mapping.namespaces:
                if source == self.namespace:
                    continue
                value = self.mapping.translate_method(
                    owner, name, descriptor, source=source, target=self.namespace
                )
                if value != name:
                    return value
        return normalize_method(name, self.namespace)

    def field_name(self, owner: str, name: str) -> str:
        if self.mapping is not None:
            for source in self.mapping.namespaces:
                if source == self.namespace:
                    continue
                value = self.mapping.translate_field(
                    owner, name, source=source, target=self.namespace
                )
                if value != name:
                    return value
        return name


def alias_table(target: str = "mojmap") -> dict[str, str]:
    return dict(YARN_TO_MOJMAP_CLASSES if target == "mojmap" else MOJMAP_TO_YARN_CLASSES)


def mapping_summary(path: Path) -> dict[str, Any]:
    try:
        mapping = load_mappings(path)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "namespaces": mapping.namespaces,
        "classes": sum(len(item) for item in mapping._class_pairs.values()),
        "methods": sum(len(item) for item in mapping._method_names.values()),
        "fields": sum(len(item) for item in mapping._field_names.values()),
    }
