"""Minimal, dependency-free NBT writer and structure-template builder.

Minecraft structure templates are gzipped NBT. Generating them lets a mod ship
real, explorable dungeons instead of a structure JSON that points at a missing
file. The writer covers every tag a structure needs (compound, list, numeric
types, string, arrays) and the builder emits the exact
``DataVersion/size/palette/blocks/entities`` layout used by
``StructureTemplateManager``.

Only the binary format is implemented. Reading is intentionally out of scope:
the agent writes templates, it does not need to parse arbitrary NBT.
"""
from __future__ import annotations

import gzip
import io
import math
import struct
from dataclasses import dataclass, field
from typing import Any

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12


@dataclass(frozen=True)
class Byte:
    value: int


@dataclass(frozen=True)
class Short:
    value: int


@dataclass(frozen=True)
class Long:
    value: int


@dataclass(frozen=True)
class Float:
    value: float


@dataclass(frozen=True)
class Double:
    value: float


@dataclass(frozen=True)
class Strings:
    values: list[str]


@dataclass(frozen=True)
class Ints:
    values: list[int]


@dataclass(frozen=True)
class Longs:
    values: list[int]


@dataclass(frozen=True)
class ByteArray:
    values: bytes


@dataclass(frozen=True)
class RawList:
    """Force a homogeneous list with an explicit element tag."""

    tag: int
    values: list[Any]


def _write_payload(out: io.BytesIO, tag: int, value: Any) -> None:
    if tag == TAG_BYTE:
        out.write(struct.pack(">b", int(value.value if isinstance(value, Byte) else value)))
    elif tag == TAG_SHORT:
        out.write(struct.pack(">h", int(value.value if isinstance(value, Short) else value)))
    elif tag == TAG_INT:
        out.write(struct.pack(">i", int(value)))
    elif tag == TAG_LONG:
        out.write(struct.pack(">q", int(value.value if isinstance(value, Long) else value)))
    elif tag == TAG_FLOAT:
        out.write(struct.pack(">f", float(value.value if isinstance(value, Float) else value)))
    elif tag == TAG_DOUBLE:
        out.write(struct.pack(">d", float(value.value if isinstance(value, Double) else value)))
    elif tag == TAG_BYTE_ARRAY:
        data = value.values if isinstance(value, ByteArray) else bytes(value)
        out.write(struct.pack(">i", len(data)))
        out.write(data)
    elif tag == TAG_STRING:
        data = str(value).encode("utf-8")
        out.write(struct.pack(">H", len(data)))
        out.write(data)
    elif tag == TAG_LIST:
        if isinstance(value, RawList):
            child_tag, values = value.tag, value.values
        else:
            values = list(value)
            child_tag = _infer_tag(values[0]) if values else TAG_END
        out.write(struct.pack(">b", child_tag))
        out.write(struct.pack(">i", len(values)))
        for item in values:
            _write_payload(out, child_tag, item)
    elif tag == TAG_COMPOUND:
        for key, child in value.items():
            _write_named_payload(out, str(key), child)
        out.write(struct.pack(">b", TAG_END))
    elif tag == TAG_INT_ARRAY:
        values = list(value)
        out.write(struct.pack(">i", len(values)))
        for item in values:
            out.write(struct.pack(">i", int(item)))
    elif tag == TAG_LONG_ARRAY:
        values = list(value)
        out.write(struct.pack(">i", len(values)))
        for item in values:
            out.write(struct.pack(">q", int(item)))
    else:
        raise ValueError(f"unsupported NBT tag: {tag}")


def _infer_tag(value: Any) -> int:
    if isinstance(value, RawList):
        return TAG_LIST
    if isinstance(value, Byte):
        return TAG_BYTE
    if isinstance(value, Short):
        return TAG_SHORT
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, Long):
        return TAG_LONG
    if isinstance(value, Float):
        return TAG_FLOAT
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, dict):
        return TAG_COMPOUND
    if isinstance(value, (bytes, bytearray, ByteArray)):
        return TAG_BYTE_ARRAY
    if isinstance(value, (Strings,)):
        return TAG_LIST
    if isinstance(value, (Ints, Longs)):
        return TAG_LIST
    if isinstance(value, (list, tuple)):
        return TAG_LIST
    raise TypeError(f"cannot infer NBT tag for {type(value)!r}")


def _write_named_payload(out: io.BytesIO, name: str, value: Any) -> None:
    tag = _infer_tag(value)
    out.write(struct.pack(">b", tag))
    data = name.encode("utf-8")
    out.write(struct.pack(">H", len(data)))
    out.write(data)
    _write_payload(out, tag, value)


def encode(value: dict[str, Any], *, root_name: str = "", gzip_output: bool = True) -> bytes:
    """Encode a compound as NBT. Returns gzipped bytes by default."""
    out = io.BytesIO()
    out.write(struct.pack(">b", TAG_COMPOUND))
    name = root_name.encode("utf-8")
    out.write(struct.pack(">H", len(name)))
    out.write(name)
    _write_payload(out, TAG_COMPOUND, value)
    raw = out.getvalue()
    if not gzip_output:
        return raw
    return gzip.compress(raw, compresslevel=9, mtime=0)


# ---------------------------------------------------------------------------
# Structure templates
# ---------------------------------------------------------------------------

DATA_VERSIONS: list[tuple[tuple[int, int, int], int]] = [
    ((1, 16, 5), 2724),
    ((1, 17, 1), 2730),
    ((1, 18, 2), 2975),
    ((1, 19, 2), 3120),
    ((1, 19, 4), 3337),
    ((1, 20, 1), 3465),
    ((1, 20, 4), 3700),
    ((1, 20, 6), 3839),
    ((1, 21, 1), 3955),
    ((1, 21, 4), 4189),
    ((1, 21, 5), 4325),
    ((1, 21, 8), 4440),
    ((26, 0, 0), 5023),
]


def data_version(mc: str) -> int:
    import re

    if mc.startswith("26."):
        return DATA_VERSIONS[-1][1]
    key = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    key = key + (0,) * (3 - len(key))
    value = DATA_VERSIONS[0][1]
    for version, number in DATA_VERSIONS:
        if key >= version:
            value = number
    return value


@dataclass
class Structure:
    size: tuple[int, int, int]
    palette: list[dict[str, Any]] = field(default_factory=list)
    blocks: list[dict[str, Any]] = field(default_factory=list)
    entities: list[dict[str, Any]] = field(default_factory=list)

    def state(self, name: str, props: dict[str, str] | None = None) -> int:
        entry: dict[str, Any] = {"Name": name}
        if props:
            entry["Properties"] = dict(sorted(props.items()))
        for index, existing in enumerate(self.palette):
            if existing == entry:
                return index
        self.palette.append(entry)
        return len(self.palette) - 1

    def put(self, x: int, y: int, z: int, name: str,
            props: dict[str, str] | None = None,
            nbt: dict[str, Any] | None = None) -> None:
        if not (0 <= x < self.size[0] and 0 <= y < self.size[1] and 0 <= z < self.size[2]):
            return
        block: dict[str, Any] = {"pos": [x, y, z], "state": self.state(name, props)}
        if nbt:
            block["nbt"] = nbt
        self.blocks.append(block)

    def fill(self, x0: int, y0: int, z0: int, x1: int, y1: int, z1: int,
             name: str, props: dict[str, str] | None = None) -> None:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for z in range(min(z0, z1), max(z0, z1) + 1):
                for x in range(min(x0, x1), max(x0, x1) + 1):
                    self.put(x, y, z, name, props)

    def to_nbt(self, mc: str) -> bytes:
        # Stable de-duplication keeps generated files reproducible.
        blocks: list[dict[str, Any]] = []
        seen: set[tuple[int, int, int]] = set()
        for block in self.blocks:
            key = tuple(block["pos"])
            if key in seen:
                continue
            seen.add(key)
            blocks.append(block)
        payload = {
            "DataVersion": data_version(mc),
            "size": [int(v) for v in self.size],
            "palette": self.palette or [{"Name": "minecraft:air"}],
            "blocks": blocks,
            "entities": RawList(TAG_COMPOUND, self.entities),
        }
        return encode(payload)


def _jigsaw(pool: str, name: str, target: str = "", final_state: str = "minecraft:air",
            joint: str = "aligned") -> dict[str, Any]:
    return {
        "id": "minecraft:jigsaw",
        "name": name,
        "target": target or name,
        "pool": pool,
        "final_state": final_state,
        "joint": joint,
    }


def _chest(loot_table: str, facing: str = "north") -> dict[str, Any]:
    return {"id": "minecraft:chest", "LootTable": loot_table, "facing": facing}


def build_room(pool: str, loot_table: str, *, variant: int = 0) -> Structure:
    """A small, sealed jigsaw room with one entrance and one exit.

    variant 0: stone-brick vault, variant 1: corridor, variant 2: treasure
    chamber with a raised plinth. The palette stays vanilla so the template is
    valid in every supported version; the mod's own blocks are added by the
    caller through ``add_accent`` if desired.
    """
    w, h, d = (11, 7, 11) if variant != 1 else (7, 5, 15)
    s = Structure(size=(w, h, d))
    floor = ["minecraft:stone_bricks", "minecraft:deepslate_tiles", "minecraft:polished_blackstone_bricks"][variant % 3]
    brick = ["minecraft:stone_bricks", "minecraft:deepslate_bricks", "minecraft:polished_blackstone_bricks"][variant % 3]
    s.fill(0, 0, 0, w - 1, 0, d - 1, floor)
    s.fill(0, h - 1, 0, w - 1, h - 1, d - 1, brick)
    s.fill(0, 1, 0, 0, h - 2, d - 1, brick)
    s.fill(w - 1, 1, 0, w - 1, h - 2, d - 1, brick)
    s.fill(1, 1, 0, w - 2, h - 2, 0, brick)
    s.fill(1, 1, d - 1, w - 2, h - 2, d - 1, brick)
    # Doorways front and back.
    mid = w // 2
    s.fill(mid - 1, 1, 0, mid + 1, 3, 0, "minecraft:air")
    s.fill(mid - 1, 1, d - 1, mid + 1, 3, d - 1, "minecraft:air")
    # jigsaw connectors: front looks back into the pool, back expands it.
    s.put(mid, 1, 0, "minecraft:jigsaw",
          nbt=_jigsaw(pool, f"{pool}/door", f"{pool}/door"))
    s.put(mid, 1, d - 1, "minecraft:jigsaw",
          nbt=_jigsaw(pool, f"{pool}/door", f"{pool}/door"))
    # Pillars and lights.
    for x, z in ((1, 1), (w - 2, 1), (1, d - 2), (w - 2, d - 2)):
        s.fill(x, 1, z, x, h - 2, z, "minecraft:chiseled_stone_bricks")
    s.put(2, h - 2, 2, "minecraft:lantern", {"hanging": "true"})
    s.put(w - 3, h - 2, d - 3, "minecraft:lantern", {"hanging": "true"})
    # Loot.
    s.put(2, 1, d - 3, "minecraft:chest", {"facing": "south", "type": "single"},
          {"id": "minecraft:chest", "LootTable": loot_table})
    if variant == 2:
        s.fill(mid - 1, 1, d // 2 - 1, mid + 1, 2, d // 2 + 1, "minecraft:polished_blackstone")
        s.put(mid, 3, d // 2, "minecraft:chest", {"facing": "south", "type": "single"},
              {"id": "minecraft:chest", "LootTable": loot_table})
        s.put(mid - 2, 1, d // 2, "minecraft:soul_lantern", {"hanging": "false"})
        s.put(mid + 2, 1, d // 2, "minecraft:soul_lantern", {"hanging": "false"})
    # Cobwebs / cracked bricks for age.
    s.put(1, 2, 1, "minecraft:cracked_stone_bricks")
    s.put(w - 2, 2, d - 2, "minecraft:cracked_stone_bricks")
    return s


def build_arena(pool: str, loot_table: str, *, radius: int = 9) -> Structure:
    """A circular-ish boss arena with a central dais and four pillars."""
    size = radius * 2 + 1
    s = Structure(size=(size, 9, size))
    s.fill(0, 0, 0, size - 1, 0, size - 1, "minecraft:polished_deepslate")
    c = radius
    for y in range(1, 8):
        for x in range(size):
            for z in range(size):
                dx, dz = x - c, z - c
                if dx * dx + dz * dz >= (radius - 1) ** 2:
                    s.put(x, y, z, "minecraft:deepslate_bricks")
    for x, z in ((c - radius + 2, c - radius + 2), (c + radius - 2, c - radius + 2),
                 (c - radius + 2, c + radius - 2), (c + radius - 2, c + radius - 2)):
        s.fill(x, 1, z, x, 6, z, "minecraft:chiseled_deepslate")
        s.put(x, 7, z, "minecraft:soul_lantern", {"hanging": "false"})
    s.fill(c - 2, 1, c - 2, c + 2, 1, c + 2, "minecraft:polished_blackstone")
    s.put(c, 1, c, "minecraft:chest", {"facing": "south", "type": "single"},
          {"id": "minecraft:chest", "LootTable": loot_table})
    # Four entrances as jigsaw connectors.
    for x, z in ((c, 0), (c, size - 1), (0, c), (size - 1, c)):
        s.put(x, 1, z, "minecraft:jigsaw", nbt=_jigsaw(pool, f"{pool}/door", f"{pool}/door"))
    return s


def build_corridor(pool: str, loot_table: str, length: int = 15) -> Structure:
    return build_room(pool, loot_table, variant=1)


def summarize_structure(path: str, size: tuple[int, int, int], palette: int, blocks: int) -> dict[str, Any]:
    return {"path": path, "size": list(size), "palette": palette, "blocks": blocks}
