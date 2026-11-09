"""JSON-first asset and data generation.

We deliberately generate static resources instead of relying on each loader's
data-generation API. Static JSON works across Fabric/Forge/NeoForge and across
most Minecraft eras, which is essential for a multi-version tool. Datagen can be
added later for dynamic content, but it is not required for a runnable mod.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import struct
import zlib
from pathlib import Path
from typing import Any, Iterable

from .util import slug, write_json, write_text

BLOCK_TYPES = {"block", "ore", "storage_block", "machine", "decor", "crop", "portal_frame", "rail", "arena", "energy"}
ITEM_TYPES = {"item", "material", "food", "tool", "armor", "weapon", "artifact", "upgrade", "seed", "spawn_egg", "key", "fertilizer"}
ENTITY_TYPES = {"entity", "boss", "npc", "cart"}


# ---------------------------------------------------------------------------
# Minimal PNG writer (no Pillow dependency)
# ---------------------------------------------------------------------------

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def write_png(path: Path, pixels: list[list[tuple[int, int, int, int]]]) -> None:
    height = len(pixels)
    width = len(pixels[0]) if height else 0
    raw = bytearray()
    for row in pixels:
        raw.append(0)
        for r, g, b, a in row:
            raw.extend((r, g, b, a))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + _png_chunk(b"IEND", b"")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _shade(rgb: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * factor))) for c in rgb)  # type: ignore[return-value]


def _noise(seed: str, x: int, y: int) -> int:
    h = hashlib.sha256(f"{seed}:{x}:{y}".encode()).digest()
    return h[0]


def _stable_int(seed: str) -> int:
    """Deterministic across processes (Python's hash() is salted)."""
    return int.from_bytes(hashlib.sha256(seed.encode("utf-8")).digest()[:8], "big")


def make_texture(path: Path, seed: str, palette: list[str], kind: str = "item") -> None:
    """Generate a deterministic 16x16 placeholder sprite with a readable silhouette."""
    colors = [_hex_rgb(c) for c in palette] or [(120, 120, 120)]
    base = colors[_stable_int(seed) % len(colors)]
    light = _shade(base, 1.28)
    dark = _shade(base, 0.55)
    outline = _shade(base, 0.30)
    accent = colors[(_stable_int(seed) + 1) % len(colors)]
    grid = [[(0, 0, 0, 0) for _ in range(16)] for _ in range(16)]

    def put(x: int, y: int, c: tuple[int, int, int], a: int = 255) -> None:
        if 0 <= x < 16 and 0 <= y < 16:
            grid[y][x] = (c[0], c[1], c[2], a)

    if kind == "block":
        for y in range(16):
            for x in range(16):
                if x in (0, 15) or y in (0, 15):
                    put(x, y, outline)
                elif x < 3 or y < 3:
                    put(x, y, light)
                elif x > 12 or y > 12:
                    put(x, y, dark)
                else:
                    n = _noise(seed, x, y) % 18
                    put(x, y, _shade(base, 0.9 + n / 100))
        # ore specks / machine light
        for i in range(6):
            x = 3 + (_noise(seed + "x", i, 0) % 10)
            y = 3 + (_noise(seed + "y", i, 0) % 10)
            put(x, y, accent)
            put(x + 1, y, _shade(accent, 0.75))
    else:
        # Rounded diamond-ish item silhouette with a motif.
        cx, cy = 7.5, 7.5
        for y in range(2, 14):
            for x in range(2, 14):
                dx, dy = abs(x - cx), abs(y - cy)
                if dx + dy <= 7.2:
                    edge = dx + dy > 6.1
                    if edge:
                        put(x, y, outline)
                    elif x < cx and y < cy:
                        put(x, y, light)
                    elif x > cx or y > cy:
                        put(x, y, dark)
                    else:
                        put(x, y, base)
        # Motif: plus / diagonal / dot depending on hash.
        motif = _stable_int(seed) % 3
        for i in range(5, 11):
            if motif == 0:
                put(i, 8, accent); put(8, i, accent)
            elif motif == 1:
                put(i, i, accent)
            else:
                put(8, i, accent)
        # small sparkle
        put(11, 4, light if light[0] > 60 else accent)
    write_png(path, grid)


def make_skin(path: Path, seed: str, palette: list[str]) -> None:
    """Generate a 64x64 player-format skin.

    Vanilla ``HumanoidModel`` samples the classic 64x64 layout, so a generated
    skin is all a visible NPC needs -- no GeckoLib, no custom model, no shader.
    """
    colors = [_hex_rgb(c) for c in palette] or [(140, 110, 90)]
    skin = colors[_stable_int(seed) % len(colors)]
    skin = _shade(skin, 1.25)
    shirt = colors[(_stable_int(seed) + 1) % len(colors)]
    pants = _shade(colors[(_stable_int(seed) + 2) % len(colors)], 0.8)
    hair = _shade(skin, 0.45)
    eye = _shade(skin, 0.25)

    grid = [[(0, 0, 0, 0) for _ in range(64)] for _ in range(64)]

    def rect(x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int]) -> None:
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                grid[y][x] = (color[0], color[1], color[2], 255)

    def put(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < 64 and 0 <= y < 64:
            grid[y][x] = (color[0], color[1], color[2], 255)

    # Base layer boxes: head, body, arms, legs.
    rect(0, 0, 31, 15, skin)
    rect(16, 16, 39, 31, shirt)
    rect(40, 16, 55, 31, skin)
    rect(32, 48, 47, 63, skin)
    rect(0, 16, 15, 31, pants)
    rect(16, 48, 31, 63, pants)

    # Face front is the 8x8 block at (8,8). Hair cap on the top face.
    rect(8, 0, 23, 7, hair)
    rect(8, 8, 15, 15, _shade(skin, 1.05))
    put(10, 11, eye)
    put(13, 11, eye)
    # Simple collar and belt so the silhouette reads at a distance.
    rect(16, 16, 39, 18, _shade(shirt, 0.75))
    rect(0, 29, 15, 30, _shade(pants, 0.6))
    rect(16, 61, 31, 62, _shade(pants, 0.6))

    write_png(path, grid)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _path(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def _is_1_21_plus(mc: str) -> bool:
    parts = mc.split(".")
    try:
        if mc.startswith("26."):
            return True
        major, minor = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        return (major, minor) >= (1, 21)
    except (ValueError, IndexError):
        return True


def _is_1_20_5_plus(mc: str) -> bool:
    if mc.startswith("26."):
        return True
    key = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    return key >= (1, 20, 5)


def _is_1_21_4_plus(mc: str) -> bool:
    """1.21.4 moved item models into assets/<ns>/items/<id>.json."""
    if mc.startswith("26."):
        return True
    key = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    return key >= (1, 21, 4)


def _is_26_plus(mc: str) -> bool:
    """26.x data formats changed again: flat ingredients and modifier-based loot."""
    return mc.startswith("26.")


# Resource-pack format numbers from the vanilla version manifest history.
_PACK_FORMATS: list[tuple[tuple[int, int, int], int]] = [
    ((1, 6, 1), 1),
    ((1, 9, 0), 2),
    ((1, 11, 0), 3),
    ((1, 13, 0), 4),
    ((1, 15, 0), 5),
    ((1, 16, 2), 6),
    ((1, 17, 0), 7),
    ((1, 18, 0), 8),
    ((1, 19, 0), 9),
    ((1, 19, 3), 12),
    ((1, 19, 4), 13),
    ((1, 20, 0), 15),
    ((1, 20, 2), 18),
    ((1, 20, 3), 22),
    ((1, 20, 5), 32),
    ((1, 21, 0), 34),
    ((1, 21, 2), 42),
    ((1, 21, 4), 46),
    ((1, 21, 5), 55),
    ((1, 21, 6), 63),
    ((1, 21, 7), 64),
    ((1, 21, 9), 69),
]


def _pack_format(mc: str) -> int:
    if mc.startswith("26."):
        return _PACK_FORMATS[-1][1]
    key = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    key = key + (0,) * (3 - len(key))
    fmt = _PACK_FORMATS[0][1]
    for version, value in _PACK_FORMATS:
        if key >= version:
            fmt = value
    return fmt


def _result(mc: str, item_id: str, count: int = 1) -> dict[str, Any]:
    key = "id" if _is_1_20_5_plus(mc) else "item"
    return {key: item_id, "count": count}


def _ingredient(mc: str, item_id: str) -> Any:
    """26.x accepts an item id / #tag string directly; older versions need {"item": ...}."""
    if _is_26_plus(mc):
        return item_id
    return {"item": item_id}


def tag_dir(mc: str, kind: str) -> str:
    """1.21+ uses singular tag directories; older versions use plural."""
    if _is_1_21_plus(mc):
        return f"tags/{kind}"
    return f"tags/{kind}s"


def data_dir(mc: str, kind: str) -> str:
    """1.21+ renamed several data directories to singular form."""
    modern = _is_1_21_plus(mc)
    return {
        "recipe": "recipe" if modern else "recipes",
        "loot": "loot_table" if modern else "loot_tables",
        "advancement": "advancement" if modern else "advancements",
    }.get(kind, kind if modern else kind + "s")


def _recipe_for(node: dict[str, Any], modid: str, mc: str) -> dict[str, Any] | None:
    recipe = _recipe_body(node, modid, mc)
    if recipe and _is_26_plus(mc) and str(recipe.get("type", "")).startswith("minecraft:crafting"):
        # 26.x crafting categories only allow building/equipment/redstone.
        if recipe.get("category") not in {"building", "equipment", "redstone"}:
            recipe.pop("category", None)
    return recipe


def _recipe_body(node: dict[str, Any], modid: str, mc: str) -> dict[str, Any] | None:
    nid = _path(node["id"])
    ntype = node["type"]
    base_mat = None
    for candidate in ("ingot", "material", "shard"):
        for other in node.get("_all_ids", []):
            if other != node["id"] and other.endswith(candidate):
                base_mat = other
                break
        if base_mat:
            break
    base_mat = base_mat or f"{modid}:core_block"
    if ntype in {"material"} and nid.endswith("ingot"):
        return {
            "type": "minecraft:smelting",
            "category": "misc",
            "ingredient": _ingredient(mc, f"{modid}:{nid.replace('_ingot', '_shard')}"),
            "result": _result(mc, node["id"]),
            "experience": 0.7,
            "cookingtime": 200,
        }
    if ntype == "storage_block":
        return {
            "type": "minecraft:crafting_shaped",
            "category": "building",
            "pattern": ["MMM", "MMM", "MMM"],
            "key": {"M": _ingredient(mc, base_mat)},
            "result": _result(mc, node["id"]),
        }
    if ntype == "block":
        return {
            "type": "minecraft:crafting_shaped",
            "category": "building",
            "pattern": ["MM", "MM"],
            "key": {"M": _ingredient(mc, base_mat)},
            "result": _result(mc, node["id"], 4),
        }
    if ntype == "ore":
        return None
    if ntype == "machine":
        return {
            "type": "minecraft:crafting_shaped",
            "category": "redstone",
            "pattern": ["III", "ICI", "III"],
            "key": {"I": _ingredient(mc, base_mat), "C": _ingredient(mc, "minecraft:crafting_table")},
            "result": _result(mc, node["id"]),
        }
    if ntype == "tool":
        return {
            "type": "minecraft:crafting_shaped",
            "category": "equipment",
            "pattern": ["M", "M", "S"],
            "key": {"M": _ingredient(mc, base_mat), "S": _ingredient(mc, "minecraft:stick")},
            "result": _result(mc, node["id"]),
        }
    if ntype == "armor":
        return {
            "type": "minecraft:crafting_shaped",
            "category": "equipment",
            "pattern": ["MMM", "M M"],
            "key": {"M": _ingredient(mc, base_mat)},
            "result": _result(mc, node["id"]),
        }
    if ntype == "food":
        return {
            "type": "minecraft:crafting_shapeless",
            "category": "food",
            "ingredients": [_ingredient(mc, base_mat), _ingredient(mc, "minecraft:wheat")],
            "result": _result(mc, node["id"]),
        }
    if ntype in {"artifact", "upgrade"}:
        return {
            "type": "minecraft:crafting_shaped",
            "category": "misc",
            "pattern": [" D ", "DMD", " D "],
            "key": {"D": _ingredient(mc, base_mat), "M": _ingredient(mc, f"{modid}:core_block")},
            "result": _result(mc, node["id"]),
        }
    if ntype in {"decor", "rail", "portal_frame"}:
        return {
            "type": "minecraft:crafting_shaped",
            "category": "building",
            "pattern": ["MM", "MM"],
            "key": {"M": _ingredient(mc, base_mat)},
            "result": _result(mc, node["id"], 4),
        }
    if ntype in {"item", "material", "seed", "key", "fertilizer"}:
        return {
            "type": "minecraft:crafting_shapeless",
            "category": "misc",
            "ingredients": [_ingredient(mc, base_mat)],
            "result": _result(mc, node["id"]),
        }
    return None


def _loot_for(node: dict[str, Any], modid: str, mc: str) -> dict[str, Any] | None:
    """Generate a block loot table.

    26.x renamed entry functions to `modifier`, made conditions single-valued
    and added `random_sequence`. Older versions keep `functions`/`conditions`.
    """
    nid = _path(node["id"])
    ntype = node["type"]
    modern = _is_26_plus(mc)
    loot_cfg = node.get("loot") or {}

    def entry(name: str, cond: Any = None, funcs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "minecraft:item", "name": name}
        if cond is not None:
            if modern:
                out["condition"] = cond
            else:
                out["conditions"] = [cond]
        if funcs:
            if modern:
                out["modifier"] = funcs
            else:
                out["functions"] = funcs
        return out

    def fn(kind: str, **kwargs: Any) -> dict[str, Any]:
        return {"type": kind, **kwargs} if modern else {"function": kind, **kwargs}

    def table(pools: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "minecraft:block", "pools": pools}
        if modern:
            out["random_sequence"] = f"{modid}:blocks/{nid}"
        return out

    if ntype in {"machine", "decor", "storage_block", "portal_frame", "rail"}:
        # 26.x conditions are object codecs; only named predicates (tool/can_*)
        # may be referenced by bare string.
        cond = ({"type": "minecraft:survives_explosion"} if modern
                else {"condition": "minecraft:survives_explosion"})
        return table([{"rolls": 1, "entries": [entry(node["id"], cond)]}])

    if ntype == "ore":
        drop = loot_cfg.get("drop")
        if not drop:
            candidates = [i for i in node.get("_all_ids", [])
                          if i.endswith("_shard") or i.endswith("_ingot")]
            drop = candidates[0] if candidates else node["id"]
        silk = "minecraft:tool/can_silk_touch" if modern else {
            "condition": "minecraft:match_tool",
            "predicate": {"predicates": {"minecraft:enchantments": [
                {"enchantments": "minecraft:silk_touch", "levels": {"min": 1}}]}},
        }
        funcs = [fn("minecraft:apply_bonus", enchantment="minecraft:fortune",
                    formula="minecraft:ore_drops"),
                 fn("minecraft:explosion_decay")]
        low, high = loot_cfg.get("min"), loot_cfg.get("max")
        if low is not None and high is not None and (low != 1 or high != 1):
            count = {"type": "minecraft:uniform", "min": low, "max": high} if modern else {"min": low, "max": high}
            funcs.insert(0, fn("minecraft:set_count", count=count))
        return table([{
            "rolls": 1,
            "entries": [{"type": "minecraft:alternatives", "children": [
                entry(node["id"], silk),
                entry(drop, None, funcs),
            ]}],
        }])

    if ntype == "crop":
        cond = ({"type": "minecraft:block_state_property", "block": node["id"], "properties": {"age": "3"}}
                if modern else
                {"condition": "minecraft:block_state_property", "block": node["id"], "properties": {"age": "3"}})
        count = {"type": "minecraft:uniform", "min": 1, "max": 3} if modern else {"min": 1, "max": 3}
        return table([{
            "rolls": 1,
            "entries": [entry(f"{modid}:{nid}_produce", cond, [fn("minecraft:set_count", count=count)])],
        }])
    return None


def generate_assets(project: Path, spec: dict[str, Any], mc: str,
                    loader: str = "") -> dict[str, Any]:
    modid = spec["identity"]["modid"]
    palette = spec.get("palette", ["#888888", "#cccccc", "#444444"])
    res = project / "src" / "main" / "resources"
    # Forge/NeoForge wrap an exploded mod directory in PathPackResources, which
    # reads pack.mcmeta from disk. Without it Forge 1.20.1 logs "Missing
    # metadata in pack mod:<id>" and every model lookup throws
    # FileNotFoundException even though the JSON exists.
    if loader in {"forge", "neoforge"}:
        write_json(res / "pack.mcmeta", {
            "pack": {
                "description": f"{spec['identity']['name_en']} resources",
                "pack_format": _pack_format(mc),
            },
        })
    asset_root = res / "assets" / modid
    data_root = res / "data" / modid
    all_ids = [n["id"] for n in spec["content"]]
    lang_en: dict[str, str] = {}
    lang_zh: dict[str, str] = {}
    generated: dict[str, Any] = {"models": 0, "textures": 0, "recipes": 0, "loot": 0, "tags": 0}

    lang_en[f"itemGroup.{modid}.{modid}"] = spec["identity"]["name_en"]
    lang_zh[f"itemGroup.{modid}.{modid}"] = spec["identity"]["name_zh"]

    for node in spec["content"]:
        node = dict(node)
        node["_all_ids"] = all_ids
        nid = _path(node["id"])
        ntype = node["type"]
        zh, en = node["name_zh"], node["name_en"]

        if ntype in BLOCK_TYPES:
            lang_en[f"block.{modid}.{nid}"] = en
            lang_zh[f"block.{modid}.{nid}"] = zh
            if ntype == "machine":
                lang_en[f"container.{modid}.machine"] = f"{en} Inventory"
                lang_zh[f"container.{modid}.machine"] = f"{zh}容器"
            block_model = {"parent": "minecraft:block/cube_all", "textures": {"all": f"{modid}:block/{nid}"}}
            if ntype == "crop":
                for age in range(4):
                    write_json(asset_root / "models" / "block" / f"{nid}_stage{age}.json",
                               {"parent": "minecraft:block/crop", "textures": {"crop": f"{modid}:block/{nid}_stage{age}"}})
                    make_texture(asset_root / "textures" / "block" / f"{nid}_stage{age}.png", nid + str(age), palette, "block")
                write_json(asset_root / "blockstates" / f"{nid}.json", {
                    "variants": {f"age={a}": {"model": f"{modid}:block/{nid}_stage{a}"} for a in range(4)}
                })
                generated["models"] += 4
                generated["textures"] += 4
            else:
                write_json(asset_root / "models" / "block" / f"{nid}.json", block_model)
                write_json(asset_root / "blockstates" / f"{nid}.json", {
                    "variants": {"": {"model": f"{modid}:block/{nid}"}}
                })
                make_texture(asset_root / "textures" / "block" / f"{nid}.png", nid, palette, "block")
                generated["models"] += 1
                generated["textures"] += 1
                # Block item model + item texture.
                write_json(asset_root / "models" / "item" / f"{nid}.json", {"parent": f"{modid}:block/{nid}"})
                if _is_1_21_4_plus(mc):
                    write_json(asset_root / "items" / f"{nid}.json",
                               {"model": {"type": "minecraft:model", "model": f"{modid}:block/{nid}"}})
                generated["models"] += 1
            loot = _loot_for(node, modid, mc)
            if loot:
                write_json(data_root / data_dir(mc, "loot") / "blocks" / f"{nid}.json", loot)
                generated["loot"] += 1
            # Block item lang key is the same as block key in modern versions,
            # but older versions use item.<modid>.<id>; include both.
            lang_en[f"item.{modid}.{nid}"] = en
            lang_zh[f"item.{modid}.{nid}"] = zh
        elif ntype in ITEM_TYPES:
            lang_en[f"item.{modid}.{nid}"] = en
            lang_zh[f"item.{modid}.{nid}"] = zh
            write_json(asset_root / "models" / "item" / f"{nid}.json",
                       {"parent": "minecraft:item/generated", "textures": {"layer0": f"{modid}:item/{nid}"}})
            if _is_1_21_4_plus(mc):
                write_json(asset_root / "items" / f"{nid}.json",
                           {"model": {"type": "minecraft:model", "model": f"{modid}:item/{nid}"}})
            make_texture(asset_root / "textures" / "item" / f"{nid}.png", nid, palette, "item")
            generated["models"] += 1
            generated["textures"] += 1
        elif ntype in ENTITY_TYPES:
            lang_en[f"entity.{modid}.{nid}"] = en
            lang_zh[f"entity.{modid}.{nid}"] = zh
            if ntype == "boss":
                lang_en[f"entity.{modid}.{nid}.bossbar"] = en
                lang_zh[f"entity.{modid}.{nid}.bossbar"] = zh
            make_skin(asset_root / "textures" / "entity" / f"{nid}.png", nid, palette)
            generated["textures"] += 1
            # Spawn egg item model. The vanilla template supplies the egg art,
            # so no extra texture is needed.
            egg = f"{nid}_spawn_egg"
            lang_en[f"item.{modid}.{egg}"] = f"{en} Spawn Egg"
            lang_zh[f"item.{modid}.{egg}"] = f"{zh}刷怪蛋"
            write_json(asset_root / "models" / "item" / f"{egg}.json",
                       {"parent": "minecraft:item/template_spawn_egg"})
            if _is_1_21_4_plus(mc):
                write_json(asset_root / "items" / f"{egg}.json",
                           {"model": {"type": "minecraft:model", "model": f"{modid}:item/{egg}"}})
            generated["models"] += 1
        elif ntype in {"dimension", "biome", "structure", "enchantment", "effect", "ritual", "advancement", "config"}:
            key = f"{ntype}.{modid}.{nid}"
            lang_en[key] = en
            lang_zh[key] = zh
        elif ntype == "sound":
            lang_en[f"sound.{modid}.{nid}"] = en
            lang_zh[f"sound.{modid}.{nid}"] = zh
        elif ntype == "particle":
            lang_en[f"particle.{modid}.{nid}"] = en
            lang_zh[f"particle.{modid}.{nid}"] = zh
            write_json(asset_root / "particles" / f"{nid}.json", {
                "textures": [f"{modid}:particle/{nid}"],
            })
            make_texture(asset_root / "textures" / "particle" / f"{nid}.png", nid, palette, "particle")
            generated["textures"] += 1
        elif ntype == "fluid":
            lang_en[f"fluid.{modid}.{nid}"] = en
            lang_zh[f"fluid.{modid}.{nid}"] = zh
            lang_en[f"block.{modid}.{nid}"] = en
            lang_zh[f"block.{modid}.{nid}"] = zh
            lang_en[f"item.{modid}.{nid}_bucket"] = f"{en} Bucket"
            lang_zh[f"item.{modid}.{nid}_bucket"] = f"{zh}桶"
            write_json(asset_root / "blockstates" / f"{nid}.json", {
                "variants": {
                    f"level={level}": {"model": f"{modid}:block/{nid}"}
                    for level in range(16)
                },
            })
            write_json(asset_root / "models" / "block" / f"{nid}.json", {
                "parent": "minecraft:block/block",
                "textures": {"particle": f"{modid}:block/{nid}_still"},
            })
            write_json(asset_root / "models" / "item" / f"{nid}_bucket.json", {
                "parent": "minecraft:item/generated",
                "textures": {"layer0": f"{modid}:item/{nid}_bucket"},
            })
            make_texture(asset_root / "textures" / "block" / f"{nid}_still.png", nid + "still", palette, "block")
            make_texture(asset_root / "textures" / "block" / f"{nid}_flow.png", nid + "flow", palette, "block")
            make_texture(asset_root / "textures" / "item" / f"{nid}_bucket.png", nid + "bucket", palette, "item")
            generated["models"] += 2
            generated["textures"] += 3
        elif ntype == "network":
            lang_en[f"network.{modid}.{nid}"] = en
            lang_zh[f"network.{modid}.{nid}"] = zh

        recipe = _recipe_for(node, modid, mc)
        if recipe:
            write_json(data_root / data_dir(mc, "recipe") / f"{nid}.json", recipe)
            generated["recipes"] += 1

    # Item group icon is represented by the first signature item.

    # Tags: keep them minimal and version-aware.
    tag_root = data_root / tag_dir(mc, "block")
    mineable = sorted({_path(n["id"]) for n in spec["content"] if n["type"] in BLOCK_TYPES})
    if mineable:
        write_json(tag_root / "mineable" / "pickaxe.json", {"replace": False, "values": [f"{modid}:{x}" for x in mineable]})
        write_json(tag_root / "needs_iron_tool.json", {"replace": False, "values": [f"{modid}:{x}" for x in mineable]})
        generated["tags"] += 2
    item_tag_root = data_root / tag_dir(mc, "item")
    write_json(item_tag_root / "tools.json", {"replace": False, "values": [n["id"] for n in spec["content"] if n["type"] == "tool"]})
    generated["tags"] += 1

    # Advancements: a root plus one per stage.
    adv_root = data_root / data_dir(mc, "advancement")
    icon_key = "id" if _is_1_20_5_plus(mc) else "item"
    write_json(adv_root / "root.json", {
        "display": {
            "icon": {icon_key: spec["content"][0]["id"] if spec["content"] else "minecraft:stone"},
            "title": {"translate": f"advancements.{modid}.root.title"},
            "description": {"translate": f"advancements.{modid}.root.description"},
            "background": "minecraft:textures/gui/advancements/backgrounds/stone.png",
            "frame": "task",
            "show_toast": True,
            "announce_to_chat": False,
        },
        "criteria": {"tick": {"trigger": "minecraft:tick"}},
    })
    lang_en[f"advancements.{modid}.root.title"] = spec["identity"]["name_en"]
    lang_zh[f"advancements.{modid}.root.title"] = spec["identity"]["name_zh"]
    lang_en[f"advancements.{modid}.root.description"] = "Begin your journey."
    lang_zh[f"advancements.{modid}.root.description"] = "开始你的旅程。"

    # Sound events. The default entries point at vanilla audio files so a
    # generated mod never logs a missing .ogg; drop your own file at the same
    # path and replace the name to use custom audio.
    if any(n["type"] == "sound" for n in spec["content"]):
        sounds = {
            "ui_click": ("minecraft:ui/button/click", 0.8, "界面点击", "UI Click"),
            "machine_hum": ("minecraft:block/beacon/ambient", 0.6, "机器运转", "Machine Hum"),
            "boss_roar": ("minecraft:entity/ender_dragon/growl1", 1.0, "首领咆哮", "Boss Roar"),
        }
        manifest_sounds: dict[str, Any] = {}
        for key, (file, volume, zh, en) in sounds.items():
            manifest_sounds[key] = {
                "sounds": [{"name": file, "volume": volume, "pitch": 1.0}],
            }
            lang_en[f"subtitles.{modid}.{key}"] = en
            lang_zh[f"subtitles.{modid}.{key}"] = zh
        write_json(asset_root / "sounds.json", manifest_sounds)
        generated["sounds"] = len(sounds)

    # Stable ordering for reproducible output.
    write_json(asset_root / "lang" / "en_us.json", dict(sorted(lang_en.items())))
    write_json(asset_root / "lang" / "zh_cn.json", dict(sorted(lang_zh.items())))
    write_json(project / ".mcmod" / "asset-manifest.json", {
        "modid": modid,
        "mc": mc,
        "generated": generated,
        "lang_keys": len(lang_en),
        "nodes": all_ids,
    })
    return generated
