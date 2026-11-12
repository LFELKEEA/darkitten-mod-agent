"""Data-driven world generation.

Everything here is static JSON plus gzipped NBT, which is why it works on
Forge, NeoForge and Fabric without a single loader API call. The generator
covers the pieces a Twilight-Forest-scale mod actually needs:

* dimension types and dimensions with a fixed custom biome
* biomes with spawners, colours, carvers and an 11-slot feature pipeline
* configured + placed ore features, injected into the overworld per loader
* jigsaw dungeons with real ``.nbt`` rooms, template pools, structure sets
* chest loot tables that reference the mod's own materials

Minecraft 26.x changed the worldgen codecs substantially. For those targets we
emit a manifest note instead of guessed JSON, so a working mod never fails to
load because of speculative data.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import nbt
from .assets import data_dir, _result
from .util import write_json, write_text

# GenerationStep.Decoration order (11 slots).
STEP_RAW = 0
STEP_LAKES = 1
STEP_LOCAL = 2
STEP_UNDERGROUND_STRUCTURES = 3
STEP_SURFACE_STRUCTURES = 4
STEP_STRONGHOLDS = 5
STEP_UNDERGROUND_ORES = 6
STEP_UNDERGROUND_DECORATION = 7
STEP_FLUID_SPRINGS = 8
STEP_VEGETAL_DECORATION = 9
STEP_TOP_LAYER = 10
FEATURE_SLOTS = 11


def _key(mc: str) -> tuple[int, int, int]:
    if mc.startswith("26."):
        return (26, 0, 0)
    parts = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    return parts + (0,) * (3 - len(parts))


def _supported(mc: str) -> bool:
    # 26.x 的数据包/维度格式尚未确认，只发 worldgen-manifest.json 说明。
    return (1, 20, 0) <= _key(mc) < (26, 0, 0)


def _is_26(mc: str) -> bool:
    return _key(mc) >= (26, 0, 0)


def _path(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def _color(value: str, fallback: int) -> int:
    try:
        return int(value.lstrip("#"), 16) & 0xFFFFFF
    except (AttributeError, ValueError):
        return fallback


def _hex_color(value: str, fallback: int) -> str:
    return f"#{_color(value, fallback):06x}"


def _biome_effects(palette: list[str]) -> dict[str, Any]:
    sky = _color(palette[0], 0x78A7FF) if palette else 0x78A7FF
    fog = _color(palette[2], 0xC0D8FF) if len(palette) > 2 else 0xC0D8FF
    water = _color(palette[1], 0x3F76E4) if len(palette) > 1 else 0x3F76E4
    return {
        "sky_color": sky,
        "fog_color": fog,
        "water_color": water,
        "water_fog_color": water,
        "mood_sound": {
            "sound": "minecraft:ambient.cave",
            "tick_delay": 6000,
            "block_search_extent": 8,
            "offset": 2.0,
        },
    }


def _structure_folder(mc: str) -> str:
    # 1.21 renamed the structure template directory to singular.
    return "structure" if _key(mc) >= (1, 21, 0) else "structures"


def _loot_dir(mc: str) -> str:
    return data_dir(mc, "loot")


def _write_loot_table(res: Path, data_root: Path, mc: str, modid: str,
                      node: str, candidates: list[str]) -> str:
    path = f"chests/{_path(node)}"
    pool_entries = []
    for index, item in enumerate(candidates[:4]):
        pool_entries.append({
            "type": "minecraft:item",
            "name": item,
            "weight": max(1, 8 - index * 2),
            "functions": [
                {"function": "minecraft:set_count",
                 "count": {"min": 1, "max": max(1, 3 - index)}},
            ],
        })
    if not pool_entries:
        pool_entries = [{"type": "minecraft:item", "name": "minecraft:bone", "weight": 1}]
    table = {
        "type": "minecraft:chest",
        "pools": [
            {"rolls": 2, "entries": pool_entries},
            {"rolls": 1, "entries": [{
                "type": "minecraft:item", "name": "minecraft:golden_apple", "weight": 1,
                "functions": [{"function": "minecraft:set_count", "count": 1}],
            }]},
        ],
    }
    write_json(data_root / _loot_dir(mc) / (path + ".json"), table)
    return f"{modid}:{path}"


def _ore_feature(node: dict[str, Any], modid: str, mc: str,
                 biome_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    nid = _path(node["id"])
    cfg_id = f"{modid}:{nid}_ore"
    placed_id = f"{modid}:{nid}_ore_placed"
    mechanics = " ".join(node.get("mechanics", []))
    count = 4
    match = re.search(r"(\d+)\s*veins", mechanics)
    if match:
        count = max(1, min(12, int(match.group(1))))
    low, high = -32, 48
    height = re.search(r"(-?\d+)\s*\.\.\s*(-?\d+)", mechanics)
    if height:
        low, high = int(height.group(1)), int(height.group(2))
    targets = [{
        "target": {"predicate_type": "minecraft:tag_match",
                   "tag": "minecraft:stone_ore_replaceables"},
        "state": node["id"] if _is_26(mc) else {"Name": node["id"]},
    }, {
        "target": {"predicate_type": "minecraft:tag_match",
                   "tag": "minecraft:deepslate_ore_replaceables"},
        "state": node["id"] if _is_26(mc) else {"Name": node["id"]},
    }]
    if _is_26(mc):
        # 26.x flattened feature configs into the feature object itself.
        configured = {
            "type": "minecraft:ore",
            "size": 9,
            "discard_chance_on_air_exposure": 0.0,
            "targets": targets,
        }
    else:
        configured = {
            "type": "minecraft:ore",
            "config": {
                "size": 9,
                "discard_chance_on_air_exposure": 0.0,
                "targets": targets,
            },
        }
    placement = [
        {"type": "minecraft:count", "count": count},
        {"type": "minecraft:in_square"},
        {"type": "minecraft:height_range", "height": {
            "type": "minecraft:trapezoid",
            "min_inclusive": {"absolute": low},
            "max_inclusive": {"absolute": high},
        }},
        {"type": "minecraft:biome"},
    ]
    if biome_id:
        # A fixed custom dimension only contains this biome; the biome filter
        # is still harmless and keeps the feature reusable.
        pass
    return (
        {"_id": cfg_id, **configured},
        {"_id": placed_id, "feature": cfg_id, "placement": placement},
    )


def _tree_feature(node: dict[str, Any], modid: str,
                  all_ids: set[str] | None = None,
                  mc: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """A deterministic custom tree.

    Uses the mod's own log/leaves blocks only when the spec actually defines
    them; otherwise falls back to vanilla oak so a generated dimension can
    never reference a missing block and crash world load.
    """
    nid = _path(node["id"])
    all_ids = all_ids or set()
    log = f"{modid}:{nid}_log" if f"{modid}:{nid}_log" in all_ids else "minecraft:oak_log"
    leaves = f"{modid}:{nid}_leaves" if f"{modid}:{nid}_leaves" in all_ids else "minecraft:oak_leaves"
    cfg_id = f"{modid}:{nid}_tree"
    placed_id = f"{modid}:{nid}_tree_placed"
    if _is_26(mc):
        configured = {
            "_id": cfg_id,
            "type": "minecraft:tree",
            "ignore_vines": True,
            "minimum_size": {"type": "minecraft:two_layers_feature_size"},
            "below_trunk_provider": "minecraft:soil_beneath_tree",
            "trunk_provider": {"id": log, "properties": {"axis": "y"}},
            "foliage_provider": {"id": leaves,
                                 "properties": {"persistent": "false", "distance": "7"}},
            "trunk_placer": {"type": "minecraft:straight_trunk_placer",
                             "base_height": 7, "height_rand_a": 3, "height_rand_b": 0},
            "foliage_placer": {"type": "minecraft:blob_foliage_placer", "radius": 3,
                               "offset": 0, "height": 4},
            "decorators": [],
        }
    else:
        configured = {
            "_id": cfg_id,
            "type": "minecraft:tree",
            "config": {
                "ignore_vines": True,
                "force_dirt": False,
                "minimum_size": {"type": "minecraft:two_layers_feature_size",
                                 "limit": 1, "lower_size": 0, "upper_size": 1},
                "dirt_provider": {"type": "minecraft:simple_state_provider",
                                  "state": {"Name": "minecraft:dirt"}},
                "trunk_provider": {"type": "minecraft:simple_state_provider",
                                   "state": {"Name": log, "Properties": {"axis": "y"}}},
                "foliage_provider": {"type": "minecraft:simple_state_provider",
                                     "state": {"Name": leaves, "Properties": {"persistent": "false",
                                                                              "distance": "7"}}},
                "trunk_placer": {"type": "minecraft:straight_trunk_placer",
                                 "base_height": 7, "height_rand_a": 3, "height_rand_b": 0},
                "foliage_placer": {"type": "minecraft:blob_foliage_placer", "radius": 3,
                                   "offset": 0, "height": 4},
                "decorators": [],
            },
        }
    placed = {
        "_id": placed_id,
        "feature": cfg_id,
        "placement": [
            {"type": "minecraft:count", "count": 4},
            {"type": "minecraft:in_square"},
            {"type": "minecraft:surface_water_depth_filter", "max_water_depth": 0},
            {"type": "minecraft:heightmap", "heightmap": "OCEAN_FLOOR"},
            {"type": "minecraft:biome"},
        ],
    }
    return configured, placed


def _biome_json(spec: dict[str, Any], biome_node: dict[str, Any] | None,
                palette: list[str], features: list[str],
                entities: list[tuple[str, str]], modid: str,
                mc: str = "") -> dict[str, Any]:
    node = biome_node or {}
    slots: list[list[str]] = [[] for _ in range(FEATURE_SLOTS)]
    for feature_id, step in features:
        if 0 <= step < FEATURE_SLOTS:
            slots[step].append(feature_id)

    categories = (
        "monster", "creature", "ambient", "axolotls",
        "underground_water_creature", "water_creature",
        "water_ambient", "misc",
    )
    if _is_26(mc):
        # 26.x moved spawns and sky/fog colours into the attribute system.
        spawns: dict[str, list[dict[str, Any]]] = {key: [] for key in categories}
        for entity, category in entities[:6]:
            bucket = "creature" if category == "creature" else "monster"
            spawns[bucket].append({
                "type": entity, "weight": 8,
                "count": {"type": "minecraft:uniform",
                          "min_inclusive": 1, "max_inclusive": 2},
            })
        sky = _hex_color(palette[0], 0x78A7FF) if palette else "#78a7ff"
        fog = _hex_color(palette[2], 0xC0D8FF) if len(palette) > 2 else "#c0d8ff"
        water = _hex_color(palette[1], 0x3F76E4) if len(palette) > 1 else "#3f76e4"
        return {
            "attributes": {
                "minecraft:gameplay/natural_mob_spawns": {
                    "argument": {"spawn_costs": {}, "spawns_by_category": spawns},
                    "modifier": "overlay",
                },
                "minecraft:visual/sky_color": sky,
                "minecraft:visual/fog_color": fog,
                "minecraft:visual/water_fog_color": water,
            },
            "carvers": [],
            "downfall": 0.4,
            "effects": {"water_color": water},
            "features": slots,
            "has_precipitation": True,
            "temperature": 0.7,
        }

    spawners: dict[str, list[dict[str, Any]]] = {key: [] for key in categories}
    for entity, category in entities[:6]:
        bucket = "creature" if category == "creature" else "monster"
        spawners[bucket].append({
            "type": entity, "weight": 8, "minCount": 1, "maxCount": 2,
        })
    return {
        "temperature": 0.7,
        "downfall": 0.4,
        "has_precipitation": True,
        "effects": _biome_effects(palette),
        "spawners": spawners,
        "spawn_costs": {},
        "carvers": {"air": [], "liquid": []},
        "features": slots,
    }


def _dim_theme(dim: dict[str, Any]) -> str:
    """从维度节点的 id/名称/mechanics 探测下界/末地主题,决定维度类型 JSON 的形状。"""
    text = " ".join([
        str(dim.get("id", "")),
        str(dim.get("name_en", "")),
        str(dim.get("name_zh", "")),
        " ".join(str(m) for m in dim.get("mechanics", [])),
    ]).lower()
    if any(k in text for k in ("nether", "infernal", "下界", "地狱")):
        return "nether"
    if any(k in text for k in ("end", "void", "末地", "虚空")):
        return "end"
    return ""


def _dimension_json(modid: str, dim_id: str, biome_id: str) -> dict[str, Any]:
    return {
        "type": f"{modid}:{dim_id}_type",
        "generator": {
            "type": "minecraft:noise",
            "biome_source": {"type": "minecraft:fixed", "biome": biome_id},
            "settings": "minecraft:overworld",
        },
    }


def _dimension_type_json(theme: str, mc: str = "") -> dict[str, Any]:
    nether = theme in {"nether", "infernal"}
    end = theme == "end"
    if mc and _is_26(mc):
        # 26.x replaced the flat flags/effects with attributes + timelines.
        return {
            "ambient_light": 0.1 if nether else (0.25 if end else 0.0),
            "coordinate_scale": 8.0 if nether else 1.0,
            "default_clock": "minecraft:the_end" if end else "minecraft:overworld",
            "has_ceiling": nether,
            "has_ender_dragon_fight": end,
            "has_skylight": not nether,
            "height": 256 if nether else 384,
            "infiniburn": "#minecraft:infiniburn_nether" if nether else "#minecraft:infiniburn_overworld",
            "logical_height": 128 if nether else 384,
            "min_y": 0 if nether else -64,
            "monster_spawn_block_light_limit": 15 if nether else 0,
            "monster_spawn_light_level": 7 if nether else {
                "type": "minecraft:uniform", "min_inclusive": 0, "max_inclusive": 7,
            },
            "timelines": "#minecraft:in_end" if end else ("#minecraft:in_nether" if nether else "#minecraft:in_overworld"),
        }
    # 1.20.x expects a plain int here; 1.21+ expects an IntProvider codec.
    if mc and _key(mc) < (1, 21, 0):
        spawn_light: Any = 7
    else:
        spawn_light = {
            "type": "minecraft:uniform", "min_inclusive": 0, "max_inclusive": 7,
        }
    return {
        "ultrawarm": nether,
        "natural": not nether and not end,
        "piglin_safe": nether,
        "respawn_anchor_works": nether,
        "bed_works": not nether,
        "has_raids": not nether and not end,
        "has_skylight": not nether,
        "has_ceiling": nether,
        "coordinate_scale": 1.0,
        "ambient_light": 0.1 if nether else 0.0,
        "logical_height": 256 if nether else 384,
        "effects": "minecraft:the_nether" if nether else ("minecraft:the_end" if end else "minecraft:overworld"),
        "infiniburn": "#minecraft:infiniburn_overworld",
        "min_y": 0 if nether else -64,
        "height": 256 if nether else 384,
        "monster_spawn_light_level": spawn_light,
        "monster_spawn_block_light_limit": 0,
    }


def _template_pool(modid: str, node: str, pieces: list[str],
                   start: bool = False) -> dict[str, Any]:
    elements = []
    for index, piece in enumerate(pieces):
        elements.append({
            "weight": max(1, 6 - index),
            "element": {
                "element_type": "minecraft:single_pool_element",
                "location": piece,
                "processors": "minecraft:empty",
                "projection": "rigid",
            },
        })
    return {
        "fallback": "minecraft:empty",
        "elements": elements,
    }


def generate_worldgen(project: Path, spec: dict[str, Any], mc: str,
                      loader: str = "") -> dict[str, Any]:
    modid = spec["identity"]["modid"]
    palette = spec.get("palette", ["#445566", "#8899aa", "#223344"])
    res = project / "src" / "main" / "resources"
    data_root = res / "data" / modid
    stats: dict[str, Any] = {
        "dimensions": 0, "biomes": 0, "features": 0, "structures": 0,
        "nbt": 0, "loot": 0, "biome_modifiers": 0, "skipped": "",
    }
    if not _supported(mc):
        stats["skipped"] = (
            f"{mc} 的世界生成 JSON 结构尚未稳定取证；已跳过自动世界生成，"
            "仅保留游戏内可验证的注册内容。需要该版本时请先运行 recon 获取真实 codec。"
        )
        write_json(project / ".mcmod" / "worldgen-manifest.json", {
            "schema": "mcmod-worldgen/1", "mc": mc, "stats": stats,
        })
        return stats

    nodes = spec["content"]
    dims = [n for n in nodes if n["type"] == "dimension"]
    biomes = [n for n in nodes if n["type"] == "biome"]
    structures = [n for n in nodes if n["type"] == "structure"]
    ores = [n for n in nodes if n["type"] == "ore"]
    entities = [n for n in nodes if n["type"] in {"entity", "boss"}]
    trees = [n for n in nodes if n["type"] in {"tree", "crop"} and "tree" in " ".join(n.get("mechanics", [])).lower()]

    def spawn_category(node: dict[str, Any]) -> str:
        if node["type"] == "boss":
            return "monster"
        mechanics = " ".join(node.get("mechanics", [])).lower()
        if any(key in mechanics for key in ("passive", "neutral", "ambient", "glow")):
            return "creature"
        return "monster"

    entity_records = [(n["id"], spawn_category(n)) for n in entities if n["type"] == "entity"]

    worldgen_root = data_root / "worldgen"
    feature_root = worldgen_root / ("feature" if _is_26(mc) else "configured_feature")
    type_root = data_root / "dimension_type"
    dim_root = data_root / "dimension"

    # Biome list: one per biome node, otherwise synthesise one per dimension.
    biome_records: list[tuple[str, dict[str, Any] | None]] = []
    for node in biomes:
        biome_records.append((_path(node["id"]), node))
    if not biome_records:
        for dim in dims:
            biome_records.append((_path(dim["id"]) + "_biome", None))
    if not biome_records and not ores and not trees and not structures:
        return stats

    # Map each dimension to a biome.
    dim_biomes: dict[str, str] = {}
    for index, dim in enumerate(dims):
        dim_path = _path(dim["id"])
        linked = None
        for name, node in biome_records:
            if node and dim["id"] in node.get("dependencies", []):
                linked = name
                break
        if linked is None and biome_records:
            linked = biome_records[min(index, len(biome_records) - 1)][0]
        dim_biomes[dim["id"]] = f"{modid}:{linked}"
        write_json(type_root / f"{dim_path}_type.json",
                   _dimension_type_json(_dim_theme(dim), mc))
        write_json(dim_root / f"{dim_path}.json",
                   _dimension_json(modid, dim_path, f"{modid}:{linked}"))
        stats["dimensions"] += 1

    overworld_biome = dim_biomes.get(dims[0]["id"]) if dims else f"minecraft:plains"
    custom_biome_ids = {f"{modid}:{name}" for name, _ in biome_records}

    # Feature generation (ores + trees).
    feature_list: list[tuple[str, int]] = []
    configured_written: set[str] = set()
    for node in ores:
        configured, placed = _ore_feature(node, modid, mc, None)
        cfg_id = configured.pop("_id")
        placed_id = placed.pop("_id")
        write_json(feature_root / f"{_path(node['id'])}_ore.json", configured)
        write_json(worldgen_root / "placed_feature" / f"{_path(node['id'])}_ore_placed.json", placed)
        configured_written.add(cfg_id)
        feature_list.append((placed_id, STEP_UNDERGROUND_ORES))
        stats["features"] += 1
    for node in trees:
        configured, placed = _tree_feature(node, modid, set(n["id"] for n in nodes), mc)
        cfg_id = configured.pop("_id")
        placed_id = placed.pop("_id")
        write_json(feature_root / f"{_path(node['id'])}_tree.json", configured)
        write_json(worldgen_root / "placed_feature" / f"{_path(node['id'])}_tree_placed.json", placed)
        feature_list.append((placed_id, STEP_VEGETAL_DECORATION))
        stats["features"] += 1

    # Loot table used by every generated chest.
    loot_candidates = [n["id"] for n in nodes
                       if n["type"] in {"material", "artifact", "upgrade", "food", "tool"}]
    loot_table = "minecraft:chests/simple_dungeon"

    # Structures: real NBT rooms + pools + structure + structure set.
    for index, node in enumerate(structures):
        path = _path(node["id"])
        pool_id = f"{modid}:{path}/start_pool"
        is_arena = "boss" in " ".join(node.get("mechanics", [])).lower()
        loot_table = _write_loot_table(res, data_root, mc, modid, path, loot_candidates)
        stats["loot"] += 1
        pieces: list[str] = []
        if is_arena:
            arena = nbt.build_arena(pool_id, loot_table)
            nbt_path = f"{path}/arena"
            target = res / "data" / modid / _structure_folder(mc) / (nbt_path + ".nbt")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(arena.to_nbt(mc))
            pieces.append(f"{modid}:{nbt_path}")
            stats["nbt"] += 1
        else:
            for variant in range(3):
                room = nbt.build_room(pool_id, loot_table, variant=variant)
                nbt_path = f"{path}/room_{variant}"
                target = res / "data" / modid / _structure_folder(mc) / (nbt_path + ".nbt")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(room.to_nbt(mc))
                pieces.append(f"{modid}:{nbt_path}")
                stats["nbt"] += 1
        write_json(worldgen_root / "template_pool" / path / "start_pool.json",
                   _template_pool(modid, path, pieces, start=True))
        biome_ref = f"#{modid}:has_structure/{path}"
        write_json(data_root / "tags" / "worldgen" / "biome" / f"has_structure" / f"{path}.json", {
            "replace": False,
            "values": sorted(custom_biome_ids) or ["minecraft:plains"],
        })
        structure_json: dict[str, Any] = {
            "type": "minecraft:jigsaw",
            "biomes": biome_ref,
            "step": "surface_structures",
            "spawn_overrides": {},
            "terrain_adaptation": "beard_thin" if not is_arena else "beard_box",
            "start_pool": pool_id,
            "size": 2 if is_arena else 4,
            "start_height": {"absolute": 0},
            "project_start_to_heightmap": "WORLD_SURFACE_WG",
            "max_distance_from_center": 96,
            "use_expansion_hack": False,
        }
        write_json(worldgen_root / "structure" / f"{path}.json", structure_json)
        write_json(worldgen_root / "structure_set" / f"{path}.json", {
            "structures": [{"structure": f"{modid}:{path}", "weight": 1}],
            "placement": {
                "type": "minecraft:random_spread",
                "spacing": 32 if not is_arena else 48,
                "separation": 8,
                "salt": 1000003 + index * 7919,
            },
        })
        stats["structures"] += 1

    # Biomes.
    for name, node in biome_records:
        biome_id = f"{modid}:{name}"
        write_json(worldgen_root / "biome" / f"{name}.json",
                   _biome_json(spec, node, palette, feature_list, entity_records, modid, mc))
        stats["biomes"] += 1

    # Overworld ore injection. Custom dimensions already carry the feature in
    # their biome, so only add modifiers when the feature is not reachable.
    if ores and loader in {"forge", "neoforge"}:
        folder = "neoforge" if loader == "neoforge" else "forge"
        type_id = "neoforge:add_features" if loader == "neoforge" else "forge:add_features"
        for node in ores:
            path = _path(node["id"])
            write_json(data_root / folder / "biome_modifier" / f"{path}_ore.json", {
                "type": type_id,
                "biomes": "#minecraft:is_overworld",
                "features": f"{modid}:{path}_ore_placed",
                "step": "underground_ores",
            })
            stats["biome_modifiers"] += 1

    write_json(project / ".mcmod" / "worldgen-manifest.json", {
        "schema": "mcmod-worldgen/1",
        "mc": mc,
        "loader": loader,
        "stats": stats,
        "dimensions": [{"id": d["id"], "biome": dim_biomes.get(d["id"])} for d in dims],
        "biomes": sorted(custom_biome_ids),
        "features": [f for f, _ in feature_list],
    })
    return stats
