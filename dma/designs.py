"""设计方案候选：一次性生成多个生物/物品/方块外观方案。

工作流：
1. Agent 调用 ``design_studio generate``，本模块在 ``.dma/designs/<会话>/`` 下
   生成多套方案（预览图、贴图、模型）。
2. 桌面端弹出选择窗口，用户可以选一套、补描述后重新生成、上传自己的文件，
   或者保留模型只换图案。
3. 用户选定后，``apply_variant`` 把贴图/模型/方块状态写进工程资源目录。

只使用标准库；PNG 由 :mod:`dma.arttools` 手写编码，不依赖 Pillow。
"""

from __future__ import annotations

import colorsys
import hashlib
import json
import random
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any

from . import arttools
from .kernel.tools import texture as texgen

KINDS = ("entity", "item", "block")
_LETTERS = "ABCDEFGH"


# --------------------------------------------------------------------------- #
# 基础工具
# --------------------------------------------------------------------------- #
def _slug(text: str, fallback: str = "design") -> str:
    value = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(text or "").strip().lower())
    value = value.strip(".-")
    return value or fallback


def _seed_of(*parts: Any) -> int:
    raw = "|".join(str(item) for item in parts).encode("utf-8", errors="replace")
    return int(hashlib.sha1(raw).hexdigest()[:12], 16)


def _hsv(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))


_COLOR_WORDS: list[tuple[tuple[str, ...], float]] = [
    (("红", "red", "scarlet", "crimson"), 0.0),
    (("橙", "orange", "amber"), 0.08),
    (("黄", "yellow", "gold", "金"), 0.14),
    (("绿", "green", "emerald", "翠"), 0.33),
    (("青", "cyan", "teal", "冰", "ice"), 0.48),
    (("蓝", "blue", "azure", "海"), 0.58),
    (("紫", "purple", "violet", "魔", "arcane"), 0.75),
    (("粉", "pink", "rose"), 0.93),
]


def _hue(text: str, default: float) -> float:
    low = str(text or "").lower()
    for words, hue in _COLOR_WORDS:
        if any(word in low for word in words):
            return hue
    return default


def _palette(seed: int, text: str, shift: float = 0.0) -> dict[str, str]:
    hue = (_hue(text, (seed % 360) / 360.0) + shift) % 1.0
    return {
        ".": "#00000000",
        "o": _hsv(hue, 0.45, 0.16),
        "a": _hsv(hue, 0.55, 0.74),
        "b": _hsv(hue, 0.22, 0.96),
        "c": _hsv(hue + 0.08, 0.68, 0.58),
        "d": _hsv(hue, 0.62, 0.40),
        "e": "#16161a",
        "w": "#f7f7fb",
        # 内置物品模板使用 h 表示木柄/握把。
        "h": _hsv(hue + 0.05, 0.60, 0.45),
    }


def _blank(width: int, height: int, fill: str = ".") -> list[list[str]]:
    return [[fill for _ in range(width)] for _ in range(height)]


def _rect(rows: list[list[str]], x0: int, y0: int, x1: int, y1: int, ch: str) -> None:
    height = len(rows)
    width = len(rows[0])
    for y in range(max(0, y0), min(height, y1 + 1)):
        for x in range(max(0, x0), min(width, x1 + 1)):
            rows[y][x] = ch


def _line(rows: list[list[str]], points: list[tuple[int, int]], ch: str) -> None:
    for x, y in points:
        if 0 <= y < len(rows) and 0 <= x < len(rows[0]):
            rows[y][x] = ch


def _rows_text(rows: list[list[str]]) -> list[str]:
    return ["".join(row) for row in rows]


def _entity_shape(text: str) -> str:
    low = str(text or "").lower()
    if any(word in low for word in ("鸟", "鹰", "蝠", "蝶", "蜂", "bird", "eagle", "bat", "butterfly", "bee")):
        return "winged"
    if any(word in low for word in ("龙", "dragon", "飞", "wing", "翅膀")):
        return "winged"
    if any(
        word in low
        for word in (
            "猫", "狗", "狼", "狐", "马", "牛", "羊", "猪", "熊", "豹", "虎", "狮",
            "兔", "鹿", "鼠", "兽", "cat", "dog", "wolf", "fox", "horse", "cow",
            "sheep", "pig", "bear", "tiger", "lion", "rabbit", "deer", "mouse",
            "quadruped", "beast",
        )
    ):
        return "quadruped"
    if any(
        word in low
        for word in (
            "人", "哥布林", "骷髅", "僵尸", "机器人", "守卫", "骑士", "法师",
            "humanoid", "goblin", "skeleton", "zombie", "robot", "guard", "knight", "mage",
        )
    ):
        return "biped"
    return "quadruped" if float(_seed_of(text) % 100) / 100.0 < 0.6 else "biped"


def _item_shape(text: str, explicit: str = "") -> str:
    value = str(explicit or "").lower()
    low = str(text or "").lower()
    table = {
        "sword": ("剑", "sword", "blade"),
        "pickaxe": ("镐", "pickaxe"),
        "axe": ("斧", "axe"),
        "shovel": ("锹", "铲", "shovel", "spade"),
        "hoe": ("锄", "hoe"),
        "ingot": ("锭", "ingot"),
        "gem": ("宝石", "水晶", "晶体", "gem", "crystal", "jewel"),
        "dust": ("粉", "尘", "dust", "powder"),
        "stick": ("棍", "棒", "杖", "stick", "rod", "wand"),
        "food": ("食物", "肉", "面包", "果", "food", "meat", "bread", "apple", "fruit"),
        "block": ("方块", "block"),
        "torch": ("火把", "torch"),
        "bowl": ("碗", "bowl"),
    }
    if value in table:
        return value
    for name, words in table.items():
        if any(word in low for word in words):
            return name
    return "gem"


def _block_style(text: str) -> str:
    low = str(text or "").lower()
    for style, words in (
        ("wood", ("木", "wood", "plank", "log")),
        ("metal", ("金属", "铁", "钢", "铜", "metal", "iron", "steel", "copper")),
        ("crystal", ("水晶", "晶", "crystal", "gem", "glass", "玻璃")),
        ("plant", ("草", "叶", "植物", "苔", "grass", "leaf", "plant", "moss")),
        ("stone", ("石", "岩", "stone", "rock", "brick")),
    ):
        if any(word in low for word in words):
            return style
    return "stone"


# --------------------------------------------------------------------------- #
# 图像生成
# --------------------------------------------------------------------------- #
def _item_rows(shape: str, seed: int) -> list[list[str]]:
    if shape in {"food"}:
        rows = _blank(16, 16)
        _rect(rows, 4, 5, 11, 13, "a")
        _rect(rows, 5, 4, 10, 5, "a")
        _rect(rows, 6, 2, 7, 4, "d")
        _rect(rows, 8, 2, 10, 3, "c")
        _rect(rows, 4, 6, 4, 11, "c")
        _rect(rows, 6, 7, 7, 8, "b")
        return rows
    if shape in arttools.TEMPLATE_NAMES:
        template = arttools.template_rows(shape)["rows"]
        return [list(row) for row in template]
    rows = _blank(16, 16)
    for index in range(6):
        width = 4 + index
        _rect(rows, 7 - width // 2, 3 + index, 7 + width // 2, 3 + index, "a")
    for index in range(6):
        width = 10 - index
        _rect(rows, 7 - width // 2, 9 + index, 7 + width // 2, 9 + index, "d")
    _rect(rows, 5, 4, 6, 5, "b")
    return rows


def _noise16(seed: int, cell: int = 4) -> list[list[float]]:
    """Small deterministic value-noise field for the design-studio previews.

    The old block rows used ``y % 4`` and ``(x + y) % 7`` lattice formulas,
    which tiled visibly.  This keeps the same hand-written PNG pipeline but
    removes the obvious periods.
    """
    rng = random.Random(seed)
    cell = max(1, int(cell))
    gw = 16 // cell + 2
    lattice = [[rng.random() for _ in range(gw)] for _ in range(gw)]

    def sample(x: int, y: int) -> float:
        gx, gy = x / cell, y / cell
        x0, y0 = int(gx), int(gy)
        fx, fy = gx - x0, gy - y0
        fx = fx * fx * (3 - 2 * fx)
        fy = fy * fy * (3 - 2 * fy)
        x1, y1 = min(gw - 1, x0 + 1), min(gw - 1, y0 + 1)
        a = lattice[y0][x0]
        b = lattice[y0][x1]
        c = lattice[y1][x0]
        d = lattice[y1][x1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    return [[sample(x, y) for x in range(16)] for y in range(16)]


def _block_rows(style: str, seed: int) -> list[list[str]]:
    rng = random.Random(seed)
    rows = _blank(16, 16, "a")
    field = _noise16(seed + 1, 4)
    fine = _noise16(seed + 2, 2)
    if style == "wood":
        board = 4
        for y in range(16):
            if y % board == 0:
                _rect(rows, 0, y, 15, y, "d")
                continue
            for x in range(16):
                grain = 0.7 * field[y][x] + 0.3 * fine[y][x]
                if grain > 0.76:
                    rows[y][x] = "b"
                elif grain < 0.22:
                    rows[y][x] = "d"
        seam = 5 + seed % 6
        _rect(rows, seam, 0, seam, 15, "d")
        _rect(rows, seam + 1, 1, seam + 1, 6, "c")
    elif style == "metal":
        for y in range(16):
            for x in range(16):
                if x in (0, 15) or y in (0, 15):
                    rows[y][x] = "d"
                else:
                    value = 0.75 * field[y][x] + 0.25 * fine[y][x]
                    rows[y][x] = "b" if value > 0.78 else ("c" if value < 0.22 else "a")
        for cx, cy in ((2, 2), (13, 2), (2, 13), (13, 13), (7, 7)):
            rows[cy][cx] = "b"
            if cx + 1 < 16:
                rows[cy][cx + 1] = "d"
            if cy + 1 < 16:
                rows[cy + 1][cx] = "d"
    elif style == "crystal":
        for y in range(16):
            for x in range(16):
                value = 0.65 * field[y][x] + 0.35 * fine[y][x]
                if abs(field[y][x] - 0.5) + abs(fine[y][x] - 0.5) * 0.4 < 0.05:
                    rows[y][x] = "c"
                elif value > 0.72:
                    rows[y][x] = "b"
                elif value < 0.36:
                    rows[y][x] = "d"
    elif style == "plant":
        for y in range(16):
            for x in range(16):
                value = 0.6 * field[y][x] + 0.4 * fine[y][x]
                if value > 0.62:
                    rows[y][x] = "b" if value > 0.78 else "c"
                elif value < 0.28:
                    rows[y][x] = "d"
        for x in (4, 10):
            _rect(rows, x, 8, x + 1, 14, "d")
    else:
        for y in range(16):
            for x in range(16):
                value = 0.7 * field[y][x] + 0.3 * fine[y][x]
                if value > 0.74:
                    rows[y][x] = "b"
                elif value < 0.26:
                    rows[y][x] = "d"
                elif value < 0.34:
                    rows[y][x] = "c"
        # a couple of hairline cracks, no repeating grid
        for _ in range(3):
            x = rng.randrange(2, 14)
            y = rng.randrange(2, 14)
            for step in range(rng.randint(3, 6)):
                if 0 <= x + step < 16 and 0 <= y + step // 2 < 16:
                    rows[y + step // 2][x + step] = "d"
    return rows


def _entity_rows(shape: str, seed: int) -> list[list[str]]:
    rows = _blank(32, 32)
    if shape == "biped":
        _rect(rows, 11, 3, 21, 12, "a")
        _rect(rows, 11, 2, 21, 4, "c")
        _rect(rows, 13, 7, 13, 8, "e")
        _rect(rows, 19, 7, 19, 8, "e")
        _rect(rows, 14, 10, 18, 10, "d")
        _rect(rows, 11, 13, 21, 23, "a")
        _rect(rows, 13, 19, 19, 23, "b")
        _rect(rows, 11, 13, 21, 14, "c")
        _rect(rows, 6, 13, 9, 22, "a")
        _rect(rows, 23, 13, 26, 22, "a")
        _rect(rows, 6, 21, 9, 22, "b")
        _rect(rows, 23, 21, 26, 22, "b")
        _rect(rows, 12, 23, 15, 29, "a")
        _rect(rows, 17, 23, 20, 29, "a")
        _rect(rows, 12, 28, 15, 29, "d")
        _rect(rows, 17, 28, 20, 29, "d")
    else:
        _rect(rows, 7, 13, 25, 22, "a")
        _rect(rows, 9, 20, 23, 22, "d")
        _rect(rows, 9, 13, 22, 14, "c")
        _rect(rows, 20, 5, 29, 15, "a")
        _rect(rows, 21, 3, 23, 6, "c")
        _rect(rows, 26, 3, 28, 6, "c")
        _rect(rows, 24, 10, 29, 13, "b")
        _rect(rows, 23, 8, 23, 9, "e")
        _rect(rows, 27, 8, 27, 9, "e")
        _rect(rows, 4, 12, 7, 17, "c")
        _rect(rows, 3, 11, 4, 13, "c")
        for x0 in (8, 13, 19, 24):
            _rect(rows, x0, 22, x0 + 3, 28, "a")
            _rect(rows, x0, 27, x0 + 3, 28, "d")
        if shape == "winged":
            _rect(rows, 1, 9, 6, 20, "c")
            _rect(rows, 26, 9, 31, 20, "c")
            _line(rows, [(1, 9), (2, 8), (3, 7)], "b")
            _line(rows, [(31, 9), (30, 8), (29, 7)], "b")
    return rows


def _entity_atlas_rows(shape: str, seed: int) -> list[list[str]]:
    """Deprecated preview-only fallback; kept for old saved sessions."""
    rng = random.Random(seed + 17)
    rows = _blank(64, 64, "a")
    for y in range(64):
        for x in range(64):
            if (x // 8 + y // 8) % 2 == 0 and rng.random() < 0.18:
                rows[y][x] = "b"
            elif rng.random() < 0.06:
                rows[y][x] = "d"
    return rows


def _write_entity_atlas(path: Path, shape: str, seed: int, palette: dict[str, str]) -> None:
    layout = "humanoid" if shape == "biped" else "quadruped"
    texgen.generate_entity(
        path,
        layout=layout,
        base=palette.get("a") or "#8a8a8a",
        accent=palette.get("c") or "#5a3fbf",
        seed=seed,
        size=(64, 64),
        variant=shape,
        transparent=True,
    )


def _png(path: Path, rows: list[list[str]], palette: dict[str, str], scale: int = 1) -> None:
    data, _width, _height = arttools.pixel_png(palette, _rows_text(rows), scale=scale)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _placeholder_rows() -> list[list[str]]:
    rows = _blank(32, 32, "d")
    for index in range(32):
        rows[index][index] = "b"
        rows[index][31 - index] = "b"
    _rect(rows, 4, 4, 27, 27, "a")
    for index in range(8, 24):
        rows[index][index] = "b"
        rows[index][31 - index] = "b"
    return rows


# --------------------------------------------------------------------------- #
# 模型
# --------------------------------------------------------------------------- #
def _item_model(namespace: str, name: str) -> dict[str, Any]:
    return {"parent": "minecraft:item/generated", "textures": {"layer0": f"{namespace}:item/{name}"}}


def _block_model(namespace: str, name: str) -> dict[str, Any]:
    return {"parent": "minecraft:block/cube_all", "textures": {"all": f"{namespace}:block/{name}"}}


def _blockstate(name: str, namespace: str) -> dict[str, Any]:
    return {"variants": {"": {"model": f"{namespace}:block/{name}"}}}


def _uv_for(layout: str, part: str, variant: str = "") -> list[int]:
    for box in texgen.entity_boxes(layout, variant=variant):
        if str(box.get("part")) == part:
            return [int(box["uv"][0]), int(box["uv"][1])]
    return [0, 0]


def _entity_model(name: str, shape: str) -> dict[str, Any]:
    """Build a GeckoLib geometry that shares its UV table with the atlas.

    The previous model used hand-written UVs that ran past 64 (body, tail,
    wings) and did not match the texture at all, so the game sampled empty
    pixels.  Sizes and UV origins now come from the same table used by
    :func:`texture.generate_entity`.
    """
    if shape == "biped":
        layout = "humanoid"
        variant = ""
        spec = [
            ("body", (-4, 12, -2), (8, 12, 4), (0, 12, 0)),
            ("head", (-4, 24, -4), (8, 8, 8), (0, 24, 0)),
            ("right_arm", (-8, 12, -2), (4, 12, 4), (-4, 12, 0)),
            ("left_arm", (4, 12, -2), (4, 12, 4), (4, 12, 0)),
            ("right_leg", (-4, 0, -2), (4, 12, 4), (-2, 12, 0)),
            ("left_leg", (0, 0, -2), (4, 12, 4), (2, 12, 0)),
        ]
        bounds = [1.0, 2.0, 0.6]
    else:
        layout = "quadruped"
        variant = "winged" if shape == "winged" else ""
        spec = [
            ("body", (-4, 9, -6), (8, 6, 12), (0, 12, 0)),
            ("head", (-4, 10, -14), (8, 8, 8), (0, 14, -6)),
            ("tail", (-1, 10, 6), (2, 2, 8), (0, 12, 6)),
            ("leg_fl", (-4, 0, -8), (3, 10, 3), (-2.5, 10, -6.5)),
            ("leg_fr", (1, 0, -8), (3, 10, 3), (2.5, 10, -6.5)),
            ("leg_bl", (-4, 0, 5), (3, 10, 3), (-2.5, 10, 6.5)),
            ("leg_br", (1, 0, 5), (3, 10, 3), (2.5, 10, 6.5)),
            ("ear_l", (-3, 17, -13), (2, 2, 1), (-2, 18, -13)),
            ("ear_r", (1, 17, -13), (2, 2, 1), (2, 18, -13)),
        ]
        if shape == "winged":
            spec.extend(
                [
                    ("wing_l", (-13, 10, -2), (8, 8, 1), (-5, 14, -2)),
                    ("wing_r", (5, 10, -2), (8, 8, 1), (5, 14, -2)),
                ]
            )
        bounds = [2.2, 2.0, 1.6]

    bones: list[dict[str, Any]] = []
    for part, origin, size, pivot in spec:
        bones.append(
            {
                "name": part,
                "pivot": list(pivot),
                "cubes": [
                    {
                        "origin": list(origin),
                        "size": list(size),
                        "uv": _uv_for(layout, part, variant),
                    }
                ],
            }
        )
    return {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": f"geometry.{name}",
                    "texture_width": 64,
                    "texture_height": 64,
                    "visible_bounds_width": bounds[0],
                    "visible_bounds_height": bounds[1],
                    "visible_bounds_offset": [0, bounds[1] / 2, 0],
                },
                "bones": bones,
            }
        ],
    }


# --------------------------------------------------------------------------- #
# 会话
# --------------------------------------------------------------------------- #
class DesignSession:
    """一次设计选择会话，可多次重新生成并保留历史文件。"""

    def __init__(
        self,
        root: Path,
        *,
        kind: str,
        name: str,
        description: str,
        namespace: str,
        shape: str,
        count: int,
        seed: int,
        session_id: str,
        generation: int = 0,
        variants: list[dict[str, Any]] | None = None,
    ) -> None:
        self.root = Path(root)
        self.kind = kind
        self.name = name
        self.description = description
        self.namespace = namespace or "modid"
        self.shape = shape
        self.count = count
        self.seed = seed
        self.id = session_id
        self.generation = generation
        self.variants: list[dict[str, Any]] = list(variants or [])
        self.dir = self.root / ".dma" / "designs" / self.id
        self.dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    @property
    def folder(self) -> str:
        return {"entity": "entity", "item": "item", "block": "block"}[self.kind]

    def summary(self) -> dict[str, Any]:
        return {
            "session": self.id,
            "kind": self.kind,
            "name": self.name,
            "namespace": self.namespace,
            "shape": self.shape,
            "generation": self.generation,
            "variants": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "preview": str((self.dir / item["preview"]).as_posix()),
                    "texture": str((self.dir / item["texture"]).as_posix())
                    if item.get("texture")
                    else "",
                    "model": str((self.dir / item["model"]).as_posix())
                    if item.get("model")
                    else "",
                    "uploaded": bool(item.get("uploaded")),
                    "edited": bool(item.get("edited")),
                    "description": item.get("description", ""),
                }
                for item in self.variants
            ],
        }

    def save(self) -> None:
        payload = {
            "schema": 1,
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "namespace": self.namespace,
            "shape": self.shape,
            "count": self.count,
            "seed": self.seed,
            "generation": self.generation,
            "variants": self.variants,
        }
        (self.dir / "session.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------
    def generate(self, *, extra: str = "", count: int | None = None) -> list[dict[str, Any]]:
        """生成一批新方案，替换当前候选（旧文件留在磁盘上）。"""
        self.generation += 1
        self.count = max(2, min(int(count or self.count or 3), len(_LETTERS)))
        self.variants = []
        for index in range(self.count):
            self.variants.append(self._make_variant(index, extra=extra))
        self.save()
        return self.variants

    def _make_variant(self, index: int, *, extra: str = "") -> dict[str, Any]:
        letter = _LETTERS[index]
        folder = self.dir / f"g{self.generation}-{letter.lower()}"
        folder.mkdir(parents=True, exist_ok=True)
        seed = _seed_of(self.seed, self.description, extra, self.generation, letter)
        shift = (index * 0.09) % 1.0
        palette = _palette(seed, f"{self.description} {extra}", shift)
        if self.kind == "entity":
            concept = _entity_rows(self.shape, seed)
            _png(folder / "preview.png", concept, palette, scale=8)
            _write_entity_atlas(folder / "texture.png", self.shape, seed, palette)
            model = _entity_model(self.name, self.shape)
            (folder / "model.geo.json").write_text(
                json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            model_path = "model.geo.json"
        elif self.kind == "block":
            style = _block_style(f"{self.description} {extra}")
            texture = _block_rows(style, seed)
            _png(folder / "texture.png", texture, palette, scale=1)
            _png(folder / "preview.png", texture, palette, scale=8)
            (folder / "model.json").write_text(
                json.dumps(_block_model(self.namespace, self.name), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (folder / "blockstate.json").write_text(
                json.dumps(_blockstate(self.name, self.namespace), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            model_path = "model.json"
        else:
            shape = _item_shape(f"{self.description} {extra}", self.shape)
            texture = _item_rows(shape, seed)
            _png(folder / "texture.png", texture, palette, scale=1)
            _png(folder / "preview.png", texture, palette, scale=8)
            (folder / "model.json").write_text(
                json.dumps(_item_model(self.namespace, self.name), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            model_path = "model.json"
        meta = {
            "id": letter,
            "label": f"方案 {letter}",
            "seed": seed,
            "description": (extra or self.description or "").strip(),
            "uploaded": False,
            "source": "",
            "preview": f"{folder.name}/preview.png",
            "texture": f"{folder.name}/texture.png",
            "model": f"{folder.name}/{model_path}",
            "palette": palette,
        }
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return meta

    # ------------------------------------------------------------------
    def variant(self, variant_id: str) -> dict[str, Any]:
        wanted = str(variant_id or "").strip().upper()
        for item in self.variants:
            if str(item["id"]).upper() == wanted:
                return item
        if self.variants:
            return self.variants[0]
        raise ValueError("当前没有可用的设计方案。")

    def retexture(self, variant_id: str, *, extra: str = "") -> dict[str, Any]:
        """保留选中的模型，只重新生成贴图和预览。"""
        item = self.variant(variant_id)
        folder = self.dir / str(item["preview"]).split("/")[0]
        seed = _seed_of(item.get("seed"), extra, time.time_ns())
        palette = _palette(seed, f"{self.description} {extra}", 0.0)
        if self.kind == "entity":
            concept = _entity_rows(self.shape, seed)
            _png(folder / "preview.png", concept, palette, scale=8)
            _write_entity_atlas(folder / "texture.png", self.shape, seed, palette)
        elif self.kind == "block":
            style = _block_style(f"{self.description} {extra}")
            texture = _block_rows(style, seed)
            _png(folder / "texture.png", texture, palette, scale=1)
            _png(folder / "preview.png", texture, palette, scale=8)
        else:
            shape = _item_shape(f"{self.description} {extra}", self.shape)
            texture = _item_rows(shape, seed)
            _png(folder / "texture.png", texture, palette, scale=1)
            _png(folder / "preview.png", texture, palette, scale=8)
        item["seed"] = seed
        item["palette"] = palette
        item["description"] = (extra or item.get("description") or self.description).strip()
        item["uploaded"] = False
        meta = dict(item)
        meta.pop("label", None)
        (folder / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.save()
        return item

    # ------------------------------------------------------------------
    def adopt_upload(self, source: str | Path, *, label: str = "") -> dict[str, Any]:
        """把用户自己的文件加入候选；PNG 直接用，JSON/bbmodel 作为模型。"""
        origin = Path(str(source)).expanduser()
        if not origin.is_file():
            raise FileNotFoundError(f"文件不存在：{origin}")
        index = len(self.variants)
        if index >= len(_LETTERS):
            raise ValueError("候选方案已满，无法继续上传。")
        letter = _LETTERS[index]
        folder = self.dir / f"upload-{letter.lower()}"
        folder.mkdir(parents=True, exist_ok=True)
        suffix = origin.suffix.lower()
        copied = folder / f"source{ suffix or '.bin' }".replace(" ", "")
        shutil.copy2(origin, copied)
        palette = _palette(_seed_of(origin.name, time.time_ns()), origin.stem, 0.0)
        texture = ""
        model = ""
        if suffix == ".png":
            shutil.copy2(origin, folder / "texture.png")
            shutil.copy2(origin, folder / "preview.png")
            texture = f"{folder.name}/texture.png"
        elif suffix in {".json", ".bbmodel", ".geo"}:
            model = f"{folder.name}/source{suffix}"
            _png(folder / "preview.png", _placeholder_rows(), palette, scale=8)
        else:
            raise ValueError("只支持上传 .png / .json / .bbmodel 文件。")
        item = {
            "id": letter,
            "label": label.strip() or f"我的方案 {letter}",
            "seed": _seed_of(origin.name),
            "description": label.strip() or f"用户上传：{origin.name}",
            "uploaded": True,
            "source": copied.name,
            "preview": f"{folder.name}/preview.png",
            "texture": texture,
            "model": model,
            "palette": palette,
        }
        self.variants.append(item)
        self.save()
        return item

    # ------------------------------------------------------------------
    def update_texture(self, variant_id: str, source: str | Path) -> dict[str, Any]:
        """把手动绘制的贴图写回候选方案，并让预览立即反映这次修改。"""
        item = self.variant(variant_id)
        rel = str(item.get("texture") or "").strip()
        if not rel:
            raise ValueError("这个方案没有可绘制的贴图。")
        target = self.dir / rel
        origin = Path(str(source)).expanduser()
        if not origin.is_file():
            raise FileNotFoundError(f"文件不存在：{origin}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.resolve() != target.resolve():
            shutil.copy2(origin, target)
        preview_rel = str(item.get("preview") or f"{target.parent.name}/preview.png")
        preview = self.dir / preview_rel
        if preview.resolve() != target.resolve():
            shutil.copy2(target, preview)
        item["edited"] = True
        self.save()
        return item


def create_session(
    root: str | Path,
    *,
    kind: str,
    name: str,
    description: str = "",
    namespace: str = "",
    shape: str = "",
    count: int = 3,
    seed: int = 0,
) -> DesignSession:
    kind = str(kind or "entity").strip().lower()
    if kind not in KINDS:
        raise ValueError("kind 只能是 entity、item 或 block。")
    clean_name = _slug(name, "design").replace("-", "_")
    resolved_shape = (
        _entity_shape(f"{name} {description}")
        if kind == "entity"
        else (_block_style(f"{name} {description}") if kind == "block" else _item_shape(description, shape))
    )
    if shape.strip():
        resolved_shape = shape.strip().lower()
    session_id = (
        time.strftime("%Y%m%d-%H%M%S")
        + "-"
        + clean_name
        + "-"
        + hashlib.sha1(f"{description}{time.time_ns()}".encode("utf-8")).hexdigest()[:6]
    )
    session = DesignSession(
        Path(root),
        kind=kind,
        name=clean_name,
        description=str(description or ""),
        namespace=str(namespace or "modid"),
        shape=resolved_shape,
        count=count,
        seed=seed or _seed_of(name, description),
        session_id=session_id,
    )
    session.generate()
    return session


def load_session(root: str | Path, session_id: str) -> DesignSession:
    clean = str(session_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", clean):
        raise ValueError("session 参数不合法。")
    path = Path(root) / ".dma" / "designs" / clean / "session.json"
    if not path.is_file():
        raise FileNotFoundError(f"找不到设计方案会话：{clean}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DesignSession(
        Path(root),
        kind=str(payload.get("kind") or "entity"),
        name=str(payload.get("name") or "design"),
        description=str(payload.get("description") or ""),
        namespace=str(payload.get("namespace") or "modid"),
        shape=str(payload.get("shape") or ""),
        count=int(payload.get("count") or 3),
        seed=int(payload.get("seed") or 0),
        session_id=clean,
        generation=int(payload.get("generation") or 0),
        variants=list(payload.get("variants") or []),
    )


def latest_session(root: str | Path) -> DesignSession | None:
    base = Path(root) / ".dma" / "designs"
    if not base.is_dir():
        return None
    folders = sorted(
        (item for item in base.iterdir() if (item / "session.json").is_file()),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not folders:
        return None
    return load_session(root, folders[0].name)


# --------------------------------------------------------------------------- #
# 落地到工程
# --------------------------------------------------------------------------- #
def _write(root: Path, relative: str, data: bytes) -> str:
    target = (root / relative).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise ValueError(f"路径越界：{relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return relative.replace("\\", "/")


def apply_variant(
    root: str | Path,
    session: DesignSession,
    variant_id: str,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    item = session.variant(variant_id)
    written: list[str] = []
    notes: list[str] = []
    namespace = session.namespace or "modid"
    name = session.name
    if item.get("texture"):
        source = (session.dir / item["texture"]).resolve()
        if source.is_file():
            relative = (
                f"src/main/resources/assets/{namespace}/textures/"
                f"{session.folder}/{name}.png"
            )
            written.append(_write(root_path, relative, source.read_bytes()))
    if item.get("model"):
        source = (session.dir / item["model"]).resolve()
        if source.is_file():
            suffix = source.suffix.lower()
            if session.kind == "entity":
                if suffix == ".bbmodel":
                    relative = f"src/main/resources/assets/{namespace}/geo/{name}.bbmodel"
                    notes.append("上传的是 Blockbench 模型；游戏不会直接加载 .bbmodel，需先导出为 .geo.json。")
                else:
                    relative = f"src/main/resources/assets/{namespace}/geo/{name}.geo.json"
            elif suffix == ".bbmodel":
                relative = (
                    f"src/main/resources/assets/{namespace}/models/"
                    f"{session.folder}/{name}.bbmodel"
                )
                notes.append("上传的是 Blockbench 模型；Minecraft 不能直接加载 .bbmodel，需要先导出为模型 JSON。")
            else:
                relative = (
                    f"src/main/resources/assets/{namespace}/models/"
                    f"{session.folder}/{name}.json"
                )
            written.append(_write(root_path, relative, source.read_bytes()))
    if session.kind == "block":
        folder = Path(str(item.get("preview") or "")).parent
        state = session.dir / folder / "blockstate.json"
        if state.is_file():
            relative = f"src/main/resources/assets/{namespace}/blockstates/{name}.json"
            written.append(_write(root_path, relative, state.read_bytes()))
    if not written:
        raise ValueError("这个方案没有可写入的贴图或模型文件。")
    return {
        "ok": True,
        "session": session.id,
        "variant": item["id"],
        "label": item.get("label") or item["id"],
        "uploaded": bool(item.get("uploaded")),
        "written": written,
        "notes": notes,
        "modelType": "geckolib" if session.kind == "entity" else "minecraft",
    }


# --------------------------------------------------------------------------- #
# 线程等待
# --------------------------------------------------------------------------- #
class DesignRequest:
    """从 Agent 线程交给界面线程的选择请求。"""

    def __init__(self, session: DesignSession, *, cancel: threading.Event | None = None) -> None:
        self.session = session
        self.cancel = cancel
        self.event = threading.Event()
        self.result: dict[str, Any] | None = None

    def submit(self, result: dict[str, Any]) -> None:
        self.result = dict(result or {})
        self.event.set()

    def cancelled(self) -> bool:
        return self.cancel is not None and self.cancel.is_set()

    def wait(self, timeout: float = 1800.0) -> dict[str, Any]:
        deadline = time.monotonic() + max(1.0, float(timeout))
        while not self.event.wait(0.4):
            if self.cancelled():
                return {"action": "cancel", "reason": "已取消"}
            if time.monotonic() >= deadline:
                return {"action": "timeout"}
        return self.result or {"action": "cancel"}
