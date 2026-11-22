"""Structured pixel texture generation and validation (P1-08 / TOOL-14).

Textures are generated from a declared palette and pattern, never from a
free-form image model.  That keeps them deterministic, tiny, and reviewable
(16x16 / 32x32, limited palette, no accidental gradients).

Two generators live here:

``generate``         block / GUI-style tiles with layered value noise,
                     bevels, cracks and genre-specific motifs.
``generate_item``    readable 16x16 item sprites built from geometric
                     primitives, with bevel + outline pass.
``generate_entity``  real entity UV atlases (vanilla humanoid layout by
                     default, or arbitrary boxes) instead of front-view
                     illustrations.
"""

from __future__ import annotations

import colorsys
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

POWER_OF_TWO = {16, 32, 64, 128}


@dataclass
class TextureSpec:
    size: tuple[int, int] = (16, 16)
    style: str = "noise"
    base: str = "#7a7a7a"
    accent: str = "#4a4a4a"
    seed: int = 0
    transparent: bool = False
    max_colors: int = 64

    def to_dict(self) -> dict:
        return {
            "size": list(self.size),
            "style": self.style,
            "base": self.base,
            "accent": self.accent,
            "seed": self.seed,
            "transparent": self.transparent,
            "max_colors": self.max_colors,
        }


def _pil():
    try:
        from PIL import Image, ImageDraw  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise RuntimeError("需要 Pillow 才能生成/校验贴图") from exc
    return __import__("PIL.Image", fromlist=["Image"]), __import__("PIL.ImageDraw", fromlist=["ImageDraw"])


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    if len(value) != 6:
        raise ValueError(f"颜色格式不合法：{value}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _hex(rgb: Iterable[int]) -> str:
    values = [max(0, min(255, int(item))) for item in rgb]
    return "#" + "".join(f"{value:02x}" for value in values)


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _hash01(x: int, y: int, seed: int = 0) -> float:
    """Deterministic 0..1 hash.

    Repeating formulas such as ``(x * 7) % 11`` put the same grain every N
    pixels, which is exactly what made planks and clay look like wallpaper
    instead of materials.  A cheap integer hash keeps the pattern stable per
    seed but has no visible period.
    """
    value = (int(x) * 374761393 + int(y) * 668265263
             + int(seed) * 2246822519) & 0xFFFFFFFF
    value = (value ^ (value >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((value ^ (value >> 16)) & 0xFFFFFF) / float(0xFFFFFF)


def shade(color: str, factor: float) -> str:
    r, g, b = _rgb(color)
    return _hex((r * factor, g * factor, b * factor))


def ensure_luma(color: str, floor: float = 0.30) -> str:
    """把过暗的主色抬到能看清轮廓的亮度。

    纯黑/近黑的实体贴图进游戏就是一团黑影，模型结构完全看不出来。
    这里只抬亮度、不动色相，保证暗色主题仍然成立。
    """
    r, g, b = _rgb(color)
    hue, light, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    if light >= floor:
        return color
    sat = max(sat, 0.12)
    rr, gg, bb = colorsys.hls_to_rgb(hue, floor, sat)
    return _hex((rr * 255, gg * 255, bb * 255))


def palette(base: str, count: int = 6) -> list[str]:
    """Ordered dark→light ramp for pixel art.

    The ramp spans a fixed *window* around the base lightness instead of
    multiplying it.  Multiplying clipped pale materials (bone, snow, iron,
    lich cyan) into several identical pure-white steps, which is why those
    skins looked like blank paper.  Shadows drift a few degrees toward cool
    blue-violet and highlights toward warm yellow: that hue shift is what
    separates hand-drawn pixel art from a plain brightness slider.
    """
    count = max(2, count)
    r, g, b = _rgb(base)
    hue, light, sat = colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)
    low = max(0.045, light - 0.36)
    high = min(0.985, light + 0.30)
    # very dark or very pale bases need a guaranteed minimum spread, otherwise
    # one side of the window collapses and the ramp loses its light/dark ends.
    if high - low < 0.34:
        center = (high + low) / 2.0
        low = max(0.035, center - 0.17)
        high = min(0.995, center + 0.17)
    rows: list[str] = []
    for index in range(count):
        t = index / (count - 1)
        local_light = low + (high - low) * t
        local_sat = _clamp(sat * (1.22 - 0.40 * t), 0.0, 1.0)
        local_hue = (hue + (0.5 - t) * 0.06) % 1.0
        rr, gg, bb = colorsys.hls_to_rgb(local_hue, local_light, local_sat)
        rows.append(_hex((rr * 255, gg * 255, bb * 255)))
    return rows


# ----------------------------------------------------------------------
# block / tile generation
# ----------------------------------------------------------------------

def generate(path: Path, spec: TextureSpec | None = None, **overrides) -> Path:
    spec = spec or TextureSpec(**overrides)
    Image, _ = _pil()
    width, height = int(spec.size[0]), int(spec.size[1])
    rng = random.Random(spec.seed or 1)
    colors = palette(spec.base, 6)
    accent_colors = palette(spec.accent, 5)
    grid: list[list[str | None]] = [[None] * width for _ in range(height)]
    style = (spec.style or "noise").lower().replace("-", "_")

    handler = _STYLES.get(style)
    if handler is None:
        # aliases that describe the material rather than a pattern name
        for key, value in (
            ("wood", "planks"), ("tree", "log"), ("bark", "log"), ("foliage", "leaves"),
            ("grass_block", "grass_side"), ("grass_top", "grass"), ("stone_bricks", "bricks"),
            ("chlorite", "stone"), ("dirt_path", "dirt"), ("sandstone", "sand"),
        ):
            if key in style:
                handler = _STYLES[value]
                break
    if handler is None:
        # The old fallback painted generic noise for every typo, so a missing
        # ore/plant style looked like a plausible but wrong texture.  Fail
        # loudly instead and let the agent retry with a real style.
        raise ValueError(
            f"未知方块样式「{spec.style}」。可用样式："
            + "、".join(sorted(_STYLES))
        )
    if style in {"glass", "flower", "sapling", "mushroom"}:
        # Plants and panes are cut-outs; an opaque background would render as
        # a solid square in game.
        spec.transparent = True
    handler(grid, width, height, colors, accent_colors, rng, spec)
    if spec.transparent:
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    else:
        image = Image.new("RGBA", (width, height), _rgba(spec.base, 255))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            value = grid[y][x]
            if value is not None:
                pixels[x, y] = _rgba(value, 255)

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def _blank(width: int, height: int) -> list[list[str | None]]:
    return [[None] * width for _ in range(height)]


def _put(grid, x: int, y: int, color: str | None) -> None:
    height, width = len(grid), len(grid[0])
    if 0 <= x < width and 0 <= y < height:
        grid[y][x] = color


def _value_noise(width: int, height: int, rng: random.Random, cell: float) -> list[list[float]]:
    cell = max(1.0, float(cell))
    gw = max(2, int(width / cell) + 2)
    gh = max(2, int(height / cell) + 2)
    lattice = [[rng.random() for _ in range(gw)] for _ in range(gh)]

    def sample(x: float, y: float) -> float:
        gx, gy = x / cell, y / cell
        x0, y0 = int(gx), int(gy)
        fx, fy = gx - x0, gy - y0
        fx = fx * fx * (3 - 2 * fx)
        fy = fy * fy * (3 - 2 * fy)
        x1, y1 = min(gw - 1, x0 + 1), min(gh - 1, y0 + 1)
        a = lattice[y0][x0]
        b = lattice[y0][x1]
        c = lattice[y1][x0]
        d = lattice[y1][x1]
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy

    return [[sample(x, y) for x in range(width)] for y in range(height)]


def _fbm(width: int, height: int, rng: random.Random, cell: float = 6.0,
         octaves: int = 3) -> list[list[float]]:
    total = [[0.0] * width for _ in range(height)]
    amplitude = 1.0
    norm = 0.0
    step = float(cell)
    for _ in range(max(1, octaves)):
        layer = _value_noise(width, height, rng, step)
        for y in range(height):
            row = total[y]
            src = layer[y]
            for x in range(width):
                row[x] += src[x] * amplitude
        norm += amplitude
        amplitude *= 0.55
        step = max(1.0, step * 0.5)
    return [[value / norm for value in row] for row in total]


def _ramp(colors: list[str], value: float) -> str:
    index = int(_clamp(value) * (len(colors) - 1) + 0.5)
    return colors[max(0, min(len(colors) - 1, index))]


def _fill_noise(grid, width, height, colors, rng, cell=5.0, octaves=3,
                low=0.18, high=0.95) -> list[list[float]]:
    field = _fbm(width, height, rng, cell, octaves)
    span = max(1e-6, high - low)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, (field[y][x] - low) / span)
    return field


def _style_flat(grid, width, height, colors, accents, rng, spec):
    for y in range(height):
        for x in range(width):
            grid[y][x] = spec.base


def _style_gradient(grid, width, height, colors, accents, rng, spec):
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = _blend(spec.base, spec.accent, ratio)
        for x in range(width):
            grid[y][x] = color


def _style_noise(grid, width, height, colors, accents, rng, spec):
    field = _fill_noise(grid, width, height, colors, rng, cell=4.0, octaves=3)
    # sparse dark and light specks so the tile is not a flat cloud
    for _ in range(max(4, width * height // 40)):
        x, y = rng.randrange(width), rng.randrange(height)
        grid[y][x] = colors[0] if field[y][x] < 0.5 else colors[-1]


def _style_stone(grid, width, height, colors, accents, rng, spec):
    field = _fbm(width, height, rng, cell=5.0, octaves=4)
    for y in range(height):
        for x in range(width):
            value = 0.25 + 0.62 * field[y][x]
            grid[y][x] = _ramp(colors, value)
    # hairline cracks: random walks, darker than the surrounding rock
    for _ in range(max(2, width // 8)):
        x, y = rng.randrange(width), rng.randrange(height)
        direction = rng.choice([(1, 0), (0, 1), (1, 1), (1, -1)])
        for step in range(rng.randint(4, max(6, width // 2))):
            _put(grid, x, y, colors[1] if rng.random() < 0.75 else colors[0])
            if step % 2 == 0:
                y += rng.choice([-1, 0, 1])
            x += direction[0]
            y += direction[1] if direction[1] else 0
    # chipped highlights on the top-left of a few stones
    for _ in range(max(2, width // 6)):
        x, y = rng.randrange(width - 2), rng.randrange(height - 2)
        _put(grid, x, y, colors[-1])
        _put(grid, x + 1, y, colors[-2])


def _style_mossy(grid, width, height, colors, accents, rng, spec):
    """Stone with green growth creeping from the edges and gaps."""
    _style_stone(grid, width, height, colors, accents, rng, spec)
    _, _, sat = _hls(accents[3])
    moss = palette(accents[3], 5) if sat >= 0.15 else palette("#6f9a48", 5)
    field = _fbm(width, height, rng, cell=3.4, octaves=3)
    for y in range(height):
        for x in range(width):
            edge = min(x, y, width - 1 - x, height - 1 - y) <= 2
            value = field[y][x] + (0.30 if edge else 0.0)
            if value > 0.60:
                grid[y][x] = _ramp(moss, 0.25 + 0.6 * rng.random())
    for _ in range(max(4, width // 3)):
        x, y = rng.randrange(width), rng.randrange(height - 1)
        for dy in range(rng.randint(1, 3)):
            _put(grid, x, y + dy, moss[2] if rng.random() < 0.7 else moss[3])


def _style_deepslate(grid, width, height, colors, accents, rng, spec):
    """Darker, vertically banded rock; reads differently from plain stone."""
    field = _fbm(width, height, rng, cell=4.5, octaves=3)
    fine = _fbm(width, height, rng, cell=1.6, octaves=2)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, 0.24 + 0.5 * field[y][x])
    # Vertical clefts: per-column tone with a noisy edge, not a fixed stripe
    # every N pixels (the old loop repeated like a barcode).
    for x in range(width):
        tone = colors[1 if _hash01(x, 0, spec.seed) > 0.55 else 2]
        for y in range(height):
            if _hash01(x // 2, y, spec.seed + 7) > 0.48:
                _put(grid, x, y, tone)
            elif fine[y][x] > 0.72:
                _put(grid, x, y, colors[-1])
    for _ in range(max(2, width // 5)):
        x, y = rng.randrange(width - 1), rng.randrange(height - 2)
        _put(grid, x + 1, y, colors[-1])
        _put(grid, x, y + 1, colors[1])


def _style_gravel(grid, width, height, colors, accents, rng, spec):
    """Loose rounded pebbles with hard top-left highlights."""
    field = _fbm(width, height, rng, cell=1.8, octaves=2)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, 0.25 + 0.5 * field[y][x])
    for _ in range(width * 2):
        cx, cy = rng.randrange(width), rng.randrange(height)
        radius = 1 if rng.random() < 0.7 else 2
        tone = colors[rng.choice([2, 3, 4])]
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if abs(dx) + abs(dy) <= radius + (1 if radius > 1 else 0):
                    _put(grid, cx + dx, cy + dy, tone)
        _put(grid, cx - 1, cy - 1, colors[-1])
        _put(grid, cx + 1, cy + 1, colors[0])


def _style_clay(grid, width, height, colors, accents, rng, spec):
    """Smooth fired-clay surface with faint throwing marks."""
    field = _fbm(width, height, rng, cell=2.6, octaves=2)
    fine = _fbm(width, height, rng, cell=1.2, octaves=2)
    for y in range(height):
        for x in range(width):
            # Clay is not barcoded: the old ``(y // 3) % 2`` banding repeated
            # every three rows and read as corrugated cardboard.
            band = 0.10 * (fine[y][x] - 0.5)
            grid[y][x] = _ramp(colors, _clamp(0.18 + band + 0.72 * field[y][x]))
    for _ in range(max(1, height // 7)):
        y = rng.randrange(height)
        for x in range(width):
            if _hash01(x, y, spec.seed + 3) < 0.42:
                _put(grid, x, y, colors[2])
    for _ in range(max(3, width // 4)):
        _put(grid, rng.randrange(width), rng.randrange(height), colors[-1])


def _style_dirt(grid, width, height, colors, accents, rng, spec):
    field = _fbm(width, height, rng, cell=3.0, octaves=3)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, 0.18 + 0.66 * field[y][x])
    for _ in range(max(3, width // 4)):
        x, y = rng.randrange(width - 1), rng.randrange(height - 1)
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            _put(grid, x + dx, y + dy, colors[rng.choice([0, 1, 2])])
    for _ in range(max(3, width // 5)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, colors[-1])


def _style_grass(grid, width, height, colors, accents, rng, spec):
    _fill_noise(grid, width, height, colors, rng, cell=3.0, octaves=3, low=0.15, high=0.9)
    for _ in range(max(6, width // 2)):
        x, y = rng.randrange(width), rng.randrange(height)
        for dy in range(rng.randint(1, max(2, height // 4))):
            _put(grid, x, y + dy, colors[1] if rng.random() < 0.6 else colors[0])


def _style_grass_side(grid, width, height, colors, accents, rng, spec):
    dirt = palette("#6b4f32", 5)
    for y in range(height):
        for x in range(width):
            grid[y][x] = dirt[min(4, int(y / max(1, height - 1) * 5 + rng.random() * 1.4))]
    for x in range(width):
        depth = 3 + rng.randint(0, 2)
        if rng.random() < 0.22:
            depth += 1
        for y in range(max(0, depth)):
            grid[y][x] = _ramp(colors, 0.55 + 0.4 * rng.random())


def _style_leaves(grid, width, height, colors, accents, rng, spec):
    field = _fbm(width, height, rng, cell=3.5, octaves=3)
    for y in range(height):
        for x in range(width):
            value = field[y][x]
            if spec.transparent and value < 0.42:
                grid[y][x] = None
            elif value < 0.58:
                grid[y][x] = colors[2]
            elif value < 0.78:
                grid[y][x] = colors[3]
            elif value < 0.93:
                grid[y][x] = colors[4]
            else:
                grid[y][x] = colors[5]
    # a few bright leaf tips and deeper gaps give the canopy depth
    for _ in range(max(3, width // 5)):
        x, y = rng.randrange(width), rng.randrange(height)
        if grid[y][x] is not None:
            grid[y][x] = colors[6] if len(colors) > 6 else colors[5]
    for _ in range(max(2, width // 6)):
        x, y = rng.randrange(width), rng.randrange(height)
        if grid[y][x] is not None:
            grid[y][x] = colors[1]


def _style_log(grid, width, height, colors, accents, rng, spec):
    field = _fbm(width, height, rng, cell=1.6, octaves=2)
    for x in range(width):
        column = 0.22 + 0.62 * field[0][x] + 0.12 * rng.random()
        for y in range(height):
            value = column + 0.16 * (field[y][x] - 0.5)
            grid[y][x] = _ramp(colors, value)
    # vertical bark grooves
    for x in range(1, width, max(2, width // 4)):
        for y in range(height):
            if rng.random() < 0.58:
                grid[y][x] = colors[0]
    # knot
    if width >= 12:
        kx, ky = rng.randint(3, width - 4), rng.randint(3, height - 4)
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                if abs(dx) + abs(dy) <= 1:
                    _put(grid, kx + dx, ky + dy, colors[0])
        _put(grid, kx, ky, colors[1])


def _style_log_top(grid, width, height, colors, accents, rng, spec):
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    field = _fbm(width, height, rng, cell=2.0, octaves=2)
    max_r = math.hypot(cx, cy)
    for y in range(height):
        for x in range(width):
            distance = math.hypot(x - cx, y - cy) / max_r
            ring = 0.5 + 0.5 * math.sin(distance * math.pi * 6.0 + field[y][x] * 1.6)
            value = 0.35 + 0.45 * ring - 0.28 * distance
            grid[y][x] = _ramp(colors, value)
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        _put(grid, int(cx) + dx, int(cy) + dy, colors[0])


def _style_planks(grid, width, height, colors, accents, rng, spec):
    board = max(4, height // 4)
    for y in range(height):
        board_index = y // board
        tint = (board_index % 3) * 0.08
        if y % board == 0:
            color = colors[0]
            for x in range(width):
                grid[y][x] = color
            continue
        for x in range(width):
            # Grain follows the board, not an 11/13-pixel modulo lattice.
            grain = 0.0
            sample = _hash01(x, board_index * 17, spec.seed + 11)
            if sample > 0.90:
                grain = -0.18
            elif sample < 0.09:
                grain = 0.12
            wave = 0.04 * (_hash01(x // 2, y // 2, spec.seed + 5) - 0.5)
            grid[y][x] = _ramp(colors, _clamp(0.42 + tint + grain + wave
                                             + rng.uniform(-0.05, 0.05)))
    # board seams and nails
    for board_index in range(1, max(1, height // board)):
        y = board_index * board - 1
        for x in range(width):
            grid[y][x] = colors[1]
        for x in (2, width - 3):
            _put(grid, x, y, colors[0])
            _put(grid, x, max(0, y - 1), colors[-1])
    # highlight on the top row of each board
    for y in range(1, height, board):
        for x in range(width):
            if rng.random() < 0.35:
                grid[y][x] = colors[-1]


def _style_bricks(grid, width, height, colors, accents, rng, spec):
    course = max(3, height // 4)
    brick = max(6, width // 2)
    # Mortar is a dark neutral, never the caller's accent (a purple accent
    # used to turn a brick wall into a holiday sweater).
    mortar = colors[0]
    for y in range(height):
        offset = 0 if (y // course) % 2 == 0 else brick // 2
        for x in range(width):
            if y % course == 0 or (x + offset) % brick == 0:
                grid[y][x] = mortar
            else:
                local_x = (x + offset) % brick
                local_y = y % course
                bevel = 0.10 if local_y == 1 or local_x == 1 else 0.0
                grid[y][x] = _ramp(colors, _clamp(0.35 + bevel + rng.uniform(-0.08, 0.12)))
    for _ in range(max(2, width // 8)):
        x, y = rng.randrange(width), rng.randrange(height)
        if grid[y][x] != mortar:
            _put(grid, x, y, colors[0])


def _style_ore(grid, width, height, colors, accents, rng, spec):
    _style_stone(grid, width, height, colors, accents, rng, spec)
    clusters = max(3, width // 5)
    for _ in range(clusters):
        cx = rng.randrange(2, max(3, width - 2))
        cy = rng.randrange(2, max(3, height - 2))
        radius = 1 + (1 if rng.random() < 0.45 else 0)
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) + abs(dy) > radius + 1:
                    continue
                if rng.random() < 0.82:
                    _put(grid, cx + dx, cy + dy, accents[2])
        _put(grid, cx - 1, cy - 1, accents[-1])
        _put(grid, cx, cy - 1, accents[-1])
        _put(grid, cx + 1, cy + 1, accents[0])


def _style_metal(grid, width, height, colors, accents, rng, spec):
    # Hard-edged riveted plate.  The old sine sweep quantised into soft
    # vertical bands that read as brushed fabric, not metal.
    for y in range(height):
        for x in range(width):
            grid[y][x] = colors[3]
    mid = height // 2
    for x in range(width):
        _put(grid, x, mid, colors[1])
        _put(grid, x, mid - 1, colors[4])
        _put(grid, x, 0, colors[5])
        _put(grid, x, height - 1, colors[0])
    for y in range(height):
        _put(grid, 0, y, colors[4])
        _put(grid, width - 1, y, colors[0])
    # one diagonal brushed highlight, not a repeating stripe
    for step in range(6):
        _put(grid, 2 + step, 4 - step // 2, colors[5] if step < 2 else colors[4])
    for cx, cy in ((2, 2), (width - 3, 2), (2, height - 3), (width - 3, height - 3)):
        _put(grid, cx, cy, colors[5])
        _put(grid, cx + 1, cy, colors[1])
        _put(grid, cx, cy + 1, colors[1])


def _style_crystal(grid, width, height, colors, accents, rng, spec):
    # Faceted mineral: quantise a fractal field into hard tone planes, then
    # draw ridged seams.  The old ``(x + y) // 4`` diagonals were a perfect
    # repeating grid once the texture tiled.
    field = _fbm(width, height, rng, cell=3.2, octaves=3)
    fine = _fbm(width, height, rng, cell=1.4, octaves=2)
    tones = (accents[1], accents[2], accents[3], accents[4])
    edges = (accents[0], accents[-1])
    for y in range(height):
        for x in range(width):
            value = 0.62 * field[y][x] + 0.38 * fine[y][x]
            grid[y][x] = tones[min(len(tones) - 1, int(value * len(tones)))]
    for y in range(height):
        for x in range(width):
            ridge = abs(field[y][x] - 0.5) + abs(fine[y][x] - 0.5) * 0.4
            if ridge < 0.055:
                grid[y][x] = edges[0]
            elif ridge > 0.46:
                grid[y][x] = edges[1]
    for _ in range(max(2, width // 8)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, accents[-1])


def _style_fabric(grid, width, height, colors, accents, rng, spec):
    for y in range(height):
        for x in range(width):
            weave = 0.10 if (x + y) % 4 in (0, 1) else -0.06
            grid[y][x] = _ramp(colors, _clamp(0.46 + weave + rng.uniform(-0.05, 0.05)))
    for _ in range(max(2, width // 7)):
        y = rng.randrange(height)
        x = rng.randrange(width)
        color = accents[1]
        for dx in range(min(width - x, rng.randint(3, 6))):
            _put(grid, x + dx, y, color)


def _style_mushroom_cap(grid, width, height, colors, accents, rng, spec):
    _fill_noise(grid, width, height, accents, rng, cell=4.0, octaves=3, low=0.2, high=0.95)
    for _ in range(max(3, width // 5)):
        cx = rng.randrange(1, width - 1)
        cy = rng.randrange(1, height - 1)
        radius = 1 + (1 if rng.random() < 0.35 else 0)
        for dx in range(-radius, radius + 1):
            for dy in range(-radius, radius + 1):
                if abs(dx) + abs(dy) <= radius:
                    _put(grid, cx + dx, cy + dy, colors[-1] if dx <= 0 else colors[-2])
    for y in range(height):
        for x in range(width):
            distance = math.hypot(x - (width - 1) / 2, y - (height - 1) / 2)
            if distance > (width - 1) / 2 - 0.5:
                grid[y][x] = colors[0]


def _style_mushroom_stem(grid, width, height, colors, accents, rng, spec):
    for x in range(width):
        tint = 0.08 * (_hash01(x, 0, spec.seed + 13) - 0.5)
        for y in range(height):
            grid[y][x] = _ramp(colors, _clamp(0.48 + tint + rng.uniform(-0.10, 0.10)))
    for _ in range(max(2, width // 5)):
        y = rng.randrange(height)
        for x in range(width):
            if rng.random() < 0.6:
                grid[y][x] = colors[1]


def _style_portal(grid, width, height, colors, accents, rng, spec):
    field = _fbm(width, height, rng, cell=3.0, octaves=3)
    for y in range(height):
        for x in range(width):
            swirl = 0.5 + 0.5 * math.sin((x * 0.65 + y * 0.9) + field[y][x] * 5.0)
            grid[y][x] = _ramp(accents, 0.18 + 0.8 * swirl)
    for _ in range(max(4, width // 4)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, accents[-1])
        _put(grid, min(width - 1, x + 1), y, colors[-1])


def _style_water(grid, width, height, colors, accents, rng, spec):
    for y in range(height):
        wave = 0.5 + 0.5 * math.sin(y * 1.1 + rng.uniform(-0.4, 0.4))
        for x in range(width):
            ripple = 0.10 * math.sin(x * 0.9 + y * 0.7)
            grid[y][x] = _ramp(colors, _clamp(0.32 + 0.5 * wave + ripple))
    for _ in range(max(3, width // 5)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, colors[-1])


def _style_sand(grid, width, height, colors, accents, rng, spec):
    # Bias the field into the warm upper half of the ramp; feeding a raw
    # 0..1 field with a high ``low`` flipped most pixels to the olive darks.
    field = _fbm(width, height, rng, cell=2.2, octaves=3)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, 0.42 + 0.55 * field[y][x])
    for _ in range(max(2, height // 7)):
        y = rng.randrange(height)
        for x in range(width):
            if rng.random() < 0.6:
                grid[y][x] = colors[2]
    for _ in range(max(2, height // 8)):
        _put(grid, rng.randrange(width), rng.randrange(height), colors[1])


def _style_snow(grid, width, height, colors, accents, rng, spec):
    # Snow lives at the top of the ramp; map the field into 0.72..1.0
    # instead of feeding a high ``low`` to _fill_noise (which clamped most
    # pixels to the dark end and made blue ice).
    field = _fbm(width, height, rng, cell=3.0, octaves=2)
    for y in range(height):
        for x in range(width):
            grid[y][x] = _ramp(colors, 0.72 + 0.28 * field[y][x])
    # Wind-drift wedges: without a few hard edges the tile validates as a
    # flat white rectangle and disappears against snow biomes.
    for _ in range(max(3, width // 3)):
        x = rng.randrange(0, max(1, width - 3))
        y = rng.randrange(0, max(1, height - 2))
        tone = colors[1] if rng.random() < 0.6 else colors[2]
        for step in range(rng.randint(2, 4)):
            _put(grid, x + step, y, tone)
            _put(grid, x + step, y + 1, colors[2])
            _put(grid, x + step, y - 1, colors[5])
    for _ in range(max(2, width // 7)):
        _put(grid, rng.randrange(width), rng.randrange(height), colors[1])
    for _ in range(max(3, width // 5)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, colors[-1])


def _style_obsidian(grid, width, height, colors, accents, rng, spec):
    # Obsidian is dark, but it still needs a readable purple sheen.  The old
    # version only sampled the bottom third of an already-dark ramp, so the
    # tile exported as near-black mush.
    dark = palette(ensure_luma(spec.base, 0.24), 6)
    sheen = palette(ensure_luma(spec.accent, 0.34), 5)
    field = _fbm(width, height, rng, cell=3.5, octaves=3)
    fine = _fbm(width, height, rng, cell=1.5, octaves=2)
    for y in range(height):
        for x in range(width):
            value = 0.28 + 0.55 * field[y][x] + 0.12 * fine[y][x]
            color = _ramp(dark, _clamp(value))
            if field[y][x] > 0.74 and fine[y][x] > 0.48:
                color = _ramp(sheen, 0.55 + 0.3 * fine[y][x])
            grid[y][x] = color
    for _ in range(max(3, width // 6)):
        x, y = rng.randrange(width), rng.randrange(height)
        length = rng.randint(2, max(3, width // 3))
        for step in range(length):
            _put(grid, x + step, y - step, sheen[-1])
            _put(grid, x + step, y - step + 1, dark[0])


def _style_glow(grid, width, height, colors, accents, rng, spec):
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    max_distance = math.hypot(cx, cy) or 1.0
    for y in range(height):
        for x in range(width):
            distance = math.hypot(x - cx, y - cy) / max_distance
            halo = 1.0 - distance
            grid[y][x] = _ramp(accents, _clamp(0.12 + 0.85 * halo + rng.uniform(-0.06, 0.06)))
    for _ in range(max(3, width // 5)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, colors[-1])


def _style_glass(grid, width, height, colors, accents, rng, spec):
    # Open pane with a one-pixel frame and two short diagonal reflections.
    for y in range(height):
        for x in range(width):
            grid[y][x] = None
    for x in range(width):
        _put(grid, x, 0, colors[4])
        _put(grid, x, height - 1, colors[2])
    for y in range(height):
        _put(grid, 0, y, colors[4])
        _put(grid, width - 1, y, colors[2])
    for x, y in ((1, 1), (2, 1), (1, 2), (width - 2, height - 2), (width - 3, height - 2),
                 (width - 2, height - 3)):
        _put(grid, x, y, colors[5])
    for step in range(4, 11):
        _put(grid, step, step - 2, colors[5] if step % 3 == 0 else colors[3])
        _put(grid, min(width - 2, step + 4), min(height - 2, step + 1), colors[3])
        _put(grid, min(width - 2, step + 5), min(height - 2, step + 2), colors[4])


def _style_flower(grid, width, height, colors, accents, rng, spec):
    stem = palette("#3c6b32", 3)
    cx = width // 2
    for y in range(height // 2, height):
        _put(grid, cx, y, stem[1] if (y % 3) else stem[0])
    for dx, dy in ((0, 2), (1, 3), (2, 2), (1, 1)):
        _put(grid, cx - 3 + dx, height - 3 - dy, stem[1])
    for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1), (0, 0)):
        _put(grid, cx + dx, height // 2 - 3 + dy, accents[0] if (dx or dy) else accents[-1])
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
        _put(grid, cx + dx, height // 2 - 3 + dy, accents[1])


def _style_sapling(grid, width, height, colors, accents, rng, spec):
    stem = palette("#4d6b2f", 3)
    cx = width // 2
    for y in range(height // 2, height - 1):
        _put(grid, cx, y, stem[0])
    for _ in range(max(5, width // 2)):
        x = cx + rng.randint(-3, 3)
        y = height // 2 - 1 + rng.randint(-3, 2)
        if rng.random() < 0.8:
            _put(grid, x, y, colors[2])
            _put(grid, x + 1, y, colors[1])


def _style_mushroom(grid, width, height, colors, accents, rng, spec):
    stem = palette("#d8d0c0", 3)
    cx = width // 2
    for y in range(height // 2 + 1, height - 1):
        _put(grid, cx, y, stem[1])
        _put(grid, cx + 1, y, stem[0])
    for dy in range(3):
        for dx in range(-4, 5):
            if abs(dx) + max(0, dy - 1) <= 4:
                _put(grid, cx + dx, height // 2 - 2 + dy,
                     accents[-1] if (dx + dy) % 3 == 0 else accents[1])
    for _ in range(2):
        _put(grid, cx - 2 + rng.randint(0, 4), height // 2 - 2, colors[-1])


def _style_lantern(grid, width, height, colors, accents, rng, spec):
    for y in range(height):
        for x in range(width):
            if x in (0, width - 1) or y in (0, height - 1):
                grid[y][x] = colors[0]
            elif x in (1, width - 2) or y in (1, height - 2):
                grid[y][x] = accents[0]
            else:
                distance = math.hypot(x - (width - 1) / 2, y - (height - 1) / 2)
                grid[y][x] = _ramp(accents, _clamp(1.0 - distance / (width * 0.7)))
    for _ in range(max(2, width // 6)):
        x, y = rng.randrange(width), rng.randrange(height)
        _put(grid, x, y, accents[-1])


def _style_panel(grid, width, height, colors, accents, rng, spec):
    _style_metal(grid, width, height, colors, accents, rng, spec)
    for x in range(width):
        grid[height // 2][x] = colors[0]
    for y in range(height):
        grid[y][width // 2] = colors[0]
    for cx, cy in ((width // 4, height // 4), (3 * width // 4, height // 4),
                   (width // 4, 3 * height // 4), (3 * width // 4, 3 * height // 4)):
        _put(grid, cx, cy, accents[-1])


_STYLES = {
    "flat": _style_flat,
    "gradient": _style_gradient,
    "noise": _style_noise,
    "stone": _style_stone,
    "rock": _style_stone,
    "mossy": _style_mossy,
    "overgrown": _style_mossy,
    "deepslate": _style_deepslate,
    "gravel": _style_gravel,
    "clay": _style_clay,
    "dirt": _style_dirt,
    "grass": _style_grass,
    "grass_top": _style_grass,
    "grass_side": _style_grass_side,
    "leaves": _style_leaves,
    "log": _style_log,
    "log_side": _style_log,
    "log_top": _style_log_top,
    "stem": _style_mushroom_stem,
    "cap": _style_mushroom_cap,
    "planks": _style_planks,
    "bricks": _style_bricks,
    "ore": _style_ore,
    "metal": _style_metal,
    "crystal": _style_crystal,
    "fabric": _style_fabric,
    "mushroom_cap": _style_mushroom_cap,
    "mushroom_stem": _style_mushroom_stem,
    "portal": _style_portal,
    "water": _style_water,
    "sand": _style_sand,
    "snow": _style_snow,
    "obsidian": _style_obsidian,
    "glow": _style_glow,
    "glass": _style_glass,
    "flower": _style_flower,
    "sapling": _style_sapling,
    "mushroom": _style_mushroom,
    "lantern": _style_lantern,
    "panel": _style_panel,
}

BLOCK_STYLES = frozenset(_STYLES)


# ----------------------------------------------------------------------
# item generation
# ----------------------------------------------------------------------

_SLOT_OUTLINE = 1
_SLOT_DARK = 2
_SLOT_BASE = 3
_SLOT_LIGHT = 4
_SLOT_HIGHLIGHT = 5
_SLOT_ACCENT = 6
_SLOT_ACCENT_LIGHT = 7
_SLOT_ACCENT_DARK = 8


class _ItemCanvas:
    """16x16 logical sprite canvas with slot-based shading."""

    def __init__(self, size: int = 16) -> None:
        self.size = size
        self.grid = [[0] * size for _ in range(size)]

    def get(self, x: int, y: int) -> int:
        if 0 <= x < self.size and 0 <= y < self.size:
            return self.grid[y][x]
        return 0

    def put(self, x: int, y: int, slot: int) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            self.grid[y][x] = slot

    def rect(self, x0: int, y0: int, x1: int, y1: int, slot: int) -> None:
        for y in range(max(0, y0), min(self.size, y1 + 1)):
            for x in range(max(0, x0), min(self.size, x1 + 1)):
                self.grid[y][x] = slot

    def clear_rect(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.rect(x0, y0, x1, y1, 0)

    def line(self, x0: int, y0: int, x1: int, y1: int, slot: int, width: int = 1) -> None:
        dx, dy = abs(x1 - x0), abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        error = dx - dy
        x, y = x0, y0
        while True:
            if width <= 1:
                self.put(x, y, slot)
            else:
                half = width // 2
                for ox in range(-half, width - half):
                    for oy in range(-half, width - half):
                        self.put(x + ox, y + oy, slot)
            if x == x1 and y == y1:
                break
            doubled = error * 2
            if doubled > -dy:
                error -= dy
                x += sx
            if doubled < dx:
                error += dx
                y += sy

    def poly(self, points: list[tuple[float, float]], slot: int) -> None:
        if not points:
            return
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        for y in range(max(0, int(min(ys))), min(self.size, int(max(ys)) + 1)):
            for x in range(max(0, int(min(xs))), min(self.size, int(max(xs)) + 1)):
                if _point_in_poly(x + 0.5, y + 0.5, points):
                    self.grid[y][x] = slot

    def circle(self, cx: float, cy: float, radius: float, slot: int, fill: bool = True) -> None:
        for y in range(self.size):
            for x in range(self.size):
                distance = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                if (distance <= radius) if fill else (radius - 1.0 <= distance <= radius):
                    self.grid[y][x] = slot

    def bevel(self) -> None:
        """Light from the top-left, shadow bottom-right, for visible depth.

        Only shade an edge when the shape is actually thick enough: a 1-px
        blade or string has transparent pixels on both sides, and the old
        rule turned the whole stroke into highlight/shadow, which combined
        with :meth:`outline` into the "double ring" look.
        """
        source = [row[:] for row in self.grid]
        for y in range(self.size):
            for x in range(self.size):
                slot = source[y][x]
                if slot in (0, _SLOT_OUTLINE):
                    continue
                top = source[y - 1][x] if y > 0 else 0
                left = source[y][x - 1] if x > 0 else 0
                bottom = source[y + 1][x] if y + 1 < self.size else 0
                right = source[y][x + 1] if x + 1 < self.size else 0
                lit = (top == 0 and bottom != 0) or (left == 0 and right != 0)
                shaded = (bottom == 0 and top != 0) or (right == 0 and left != 0)
                if lit:
                    self.grid[y][x] = _SLOT_HIGHLIGHT if slot in (_SLOT_BASE, _SLOT_LIGHT) else _SLOT_ACCENT_LIGHT
                elif shaded:
                    self.grid[y][x] = _SLOT_DARK if slot in (_SLOT_BASE, _SLOT_DARK) else _SLOT_ACCENT_DARK

    def outline(self, slot: int = _SLOT_OUTLINE) -> None:
        source = [row[:] for row in self.grid]
        for y in range(self.size):
            for x in range(self.size):
                if source[y][x] != 0:
                    continue
                neighbours = (
                    source[y - 1][x] if y > 0 else 0,
                    source[y + 1][x] if y + 1 < self.size else 0,
                    source[y][x - 1] if x > 0 else 0,
                    source[y][x + 1] if x + 1 < self.size else 0,
                )
                if any(neighbours):
                    self.grid[y][x] = slot

    def render(self, base: str, accent: str) -> list[list[tuple[int, int, int, int]]]:
        # Use the same hue-shifted ramp as blocks/entities.  ``shade`` doubles
        # light colours into pure white (the old sword blade), and it keeps
        # the outline grey instead of a deep tinted edge.
        ramp = palette(base, 7)
        accent_ramp = palette(accent, 7)
        colors = {
            0: (0, 0, 0, 0),
            _SLOT_OUTLINE: _rgba(shade(ramp[0], 0.62), 255),
            _SLOT_DARK: _rgba(ramp[1], 255),
            _SLOT_BASE: _rgba(ramp[3], 255),
            _SLOT_LIGHT: _rgba(ramp[4], 255),
            _SLOT_HIGHLIGHT: _rgba(ramp[6], 255),
            _SLOT_ACCENT: _rgba(accent_ramp[3], 255),
            _SLOT_ACCENT_LIGHT: _rgba(accent_ramp[5], 255),
            _SLOT_ACCENT_DARK: _rgba(accent_ramp[1], 255),
        }
        return [[colors[self.grid[y][x]] for x in range(self.size)] for y in range(self.size)]


def _point_in_poly(px: float, py: float, points: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(points) - 1
    for i in range(len(points)):
        xi, yi = points[i]
        xj, yj = points[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-9) + xi):
            inside = not inside
        j = i
    return inside


_ITEM_KEYWORDS = (
    ("block", "block"),
    ("helmet", "helmet"), ("hood", "helmet"),
    ("chestplate", "chestplate"), ("robe", "chestplate"),
    ("leggings", "leggings"), ("pants", "leggings"),
    ("boots", "boots"),
    ("sword", "sword"), ("pickaxe", "pickaxe"), ("axe", "axe"),
    ("hammer", "axe"), ("shovel", "shovel"), ("hoe", "hoe"),
    ("ingot", "ingot"), ("gem", "gem"), ("crystal", "gem"), ("stone", "gem"),
    ("dust", "dust"), ("powder", "dust"), ("spore", "dust"),
    ("stick", "stick"), ("bone", "bone"), ("fang", "fang"), ("tooth", "fang"),
    ("scale", "scale"), ("shard", "shard"), ("cloth", "cloth"), ("silk", "cloth"),
    ("string", "cloth"),
    ("staff", "staff"), ("wand", "staff"), ("sigil", "sigil"), ("rune", "sigil"),
    ("trophy", "trophy"), ("eye", "eye"), ("berry", "berry"), ("map", "map"),
    ("book", "book"), ("guide", "book"), ("sac", "sac"), ("jelly", "jelly"),
    ("stew", "stew"), ("soup", "stew"), ("seed", "seed"), ("sapling", "seed"),
    # food / loot / magic props
    ("bowl", "bowl"), ("bow", "bow"), ("arrow", "arrow"),
    ("potion", "potion"), ("flask", "potion"), ("elixir", "potion"),
    ("brew", "potion"),
    ("scroll", "scroll"), ("tablet", "scroll"), ("parchment", "scroll"),
    ("spring", "shard"),
    ("ring", "ring"),
    ("amulet", "amulet"), ("charm", "amulet"), ("pendant", "amulet"),
    ("necklace", "amulet"), ("talisman", "amulet"),
    ("turkey", "meat"), ("key", "key"), ("lockpick", "key"),
    ("feather", "feather"), ("quill", "feather"),
    ("nugget", "nugget"), ("scrap", "nugget"),
    ("apple", "food"), ("fruit", "food"), ("pie", "food"), ("cake", "food"),
    ("bread", "bread"),
    ("meat", "meat"), ("ham", "meat"), ("chicken", "meat"), ("beef", "meat"),
    ("pork", "meat"), ("mutton", "meat"), ("drumstick", "meat"),
    ("hide", "hide"), ("leather", "hide"), ("pelt", "hide"),
    ("coin", "coin"), ("token", "coin"),
)


def _item_kind(kind: str) -> str:
    value = (kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    value = value.rsplit("/", 1)[-1]
    for key, mapped in _ITEM_KEYWORDS:
        if key in value:
            return mapped
    return value if value in _ITEM_DRAW else "box"


def infer_item_kind(name: str) -> str:
    """Resolve an item name/path to a drawable category (public wrapper)."""
    return _item_kind(name)


_MATERIAL_COLORS: tuple[tuple[str, str], ...] = (
    # Intrinsic item materials first: "twilight_stick" is still wood, not a
    # purple stick, and "lich_bone" is still bone-white.
    ("stick", "#9a7444"),
    ("bone", "#e8e0c8"),
    ("fang", "#e8e4d0"),
    ("tooth", "#e8e4d0"),
    ("berry", "#c04060"),
    ("jelly", "#d86a8a"),
    ("stew", "#a07840"),
    ("soup", "#a07840"),
    ("map", "#d8c8a0"),
    ("book", "#8a5a3a"),
    ("guide", "#8a5a3a"),
    ("trophy", "#e0c060"),
    ("eye", "#66e0d0"),
    ("seed", "#a8c060"),
    ("sapling", "#6f9a4a"),
    # food, loot and magic props: intrinsic colours before theme words
    ("golden", "#e8c34a"),
    ("feather", "#e8e6d8"),
    ("leather", "#8a5a3a"), ("hide", "#8a5a3a"), ("pelt", "#8a5a3a"),
    ("bread", "#c89050"), ("apple", "#c04a4a"), ("fruit", "#c04a4a"),
    ("meat", "#b06050"), ("ham", "#b06050"), ("beef", "#b05048"),
    ("pork", "#c87870"), ("mutton", "#b86050"), ("chicken", "#d8b880"),
    ("coin", "#e0c050"), ("potion", "#7a5ad0"), ("scroll", "#e0d0a0"),
    ("arrow", "#b8a888"), ("bow", "#8a6136"),
    # theme / material names
    ("ironwood", "#7f8f5e"),
    ("moonglass", "#9fd8e8"),
    ("moonstone", "#a8c8e8"),
    ("serpent", "#4f9a63"),
    ("scale", "#5aa06a"),
    ("mage", "#6b4fa0"),
    ("arcane", "#8a5cf0"),
    ("lich", "#a8e8dc"),
    ("wraith", "#9aa8c8"),
    ("spectral", "#b8d8e8"),
    ("shadow", "#5a5470"),
    ("twilight", "#8a6ad0"),
    ("firefly", "#f0d45c"),
    ("glow", "#e8d060"),
    ("spore", "#8fc44a"),
    ("moss", "#6fae4a"),
    ("iron", "#b8c0c8"),
    ("gold", "#e8c34a"),
    ("wood", "#9a7444"),
    ("nugget", "#c8c0b0"),
    # creature / faction names: without these, unknown mobs all fell back to
    # the same neutral grey and every entity looked like a mannequin.
    ("necromancer", "#6a4f9a"),
    ("undead", "#6a5a7a"),
    ("skeleton", "#c8c0a8"),
    ("ghost", "#9fb8c8"),
    ("phantom", "#8f9ac0"),
    ("specter", "#a8c0d8"),
    ("spectre", "#a8c0d8"),
    ("zombie", "#6a8a4a"),
    ("goblin", "#5a8a3a"),
    ("demon", "#8a3a3a"),
    ("ember", "#c05a2a"),
    ("frost", "#a8d8e8"),
    ("glacier", "#9fd0e8"),
    ("knight", "#7a889a"),
    ("sentinel", "#6a7a8a"),
    ("guard", "#7a889a"),
    ("hollow", "#5a6258"),
    ("stalker", "#54605a"),
    ("crawler", "#5a7a4a"),
    ("fungus", "#a0706a"),
    ("mushroom", "#b05a6a"),
    ("crystal", "#8fd0d8"),
    ("star", "#e8d88a"),
)

_CATEGORY_COLORS = {
    "dust": "#b898e0",
    "shard": "#9fd8e8",
    "gem": "#d05a9a",
    "bone": "#e8e0c8",
    "stick": "#9a7444",
    "sigil": "#c8a8f0",
    "cloth": "#8a6ab0",
    "fang": "#e8e4d0",
    "sack": "#9a7a4a",
    "sac": "#9a7a4a",
    "seed": "#a8c060",
    "eye": "#66e0d0",
    "map": "#d8c8a0",
    "book": "#8a5a3a",
    "trophy": "#e0c060",
    "staff": "#7a5aa8",
    "sword": "#c0c8d0",
    "pickaxe": "#b8c0c8",
    "axe": "#b8c0c8",
    "shovel": "#b8c0c8",
    "hoe": "#b8c0c8",
    "helmet": "#8a9ab0",
    "chestplate": "#8a9ab0",
    "leggings": "#8a9ab0",
    "boots": "#8a9ab0",
    "arrow": "#b8a888",
    "bow": "#8a6136",
    "bowl": "#9a7444",
    "potion": "#7a5ad0",
    "scroll": "#e0d0a0",
    "ring": "#e8c34a",
    "amulet": "#e8c34a",
    "key": "#d8b050",
    "feather": "#e8e6d8",
    "nugget": "#c8c0b0",
    "food": "#c04a4a",
    "bread": "#c89050",
    "meat": "#b06050",
    "hide": "#8a5a3a",
    "coin": "#e0c050",
}


_BLOCK_STYLE_HINTS: tuple[tuple[str, str], ...] = (
    ("leaves", "leaves"),
    ("log_top", "log_top"),
    ("log", "log_side"),
    ("planks", "planks"),
    ("brick", "bricks"),
    ("ore", "ore"),
    ("grass_top", "grass_top"),
    ("grass", "grass_side"),
    ("dirt", "dirt"),
    ("sand", "sand"),
    ("snow", "snow"),
    ("obsidian", "obsidian"),
    ("glass", "glass"),
    ("lantern", "lantern"),
    ("lamp", "lantern"),
    ("portal", "portal"),
    ("water", "water"),
    ("mushroom_cap", "mushroom_cap"),
    ("mushroom_stem", "mushroom_stem"),
    ("mushroom", "mushroom_cap"),
    ("crystal", "crystal"),
    ("metal", "metal"),
    ("mossy", "mossy"),
    ("overgrown", "mossy"),
    ("deepslate", "deepslate"),
    ("gravel", "gravel"),
    ("terra", "clay"),
    ("clay", "clay"),
    ("stone", "stone"),
)


def infer_block_style(name: str) -> str:
    value = (name or "").lower().replace("-", "_")
    for key, style in _BLOCK_STYLE_HINTS:
        if key in value:
            return style
    return "stone"


def material_color(name: str, default: str = "#b8b8c0") -> str:
    """按名字猜材质色：没显式给 base 时也不会生成一整排灰白图标。"""
    value = (name or "").lower().replace("-", "_")
    for key, color in _MATERIAL_COLORS:
        if key in value:
            return color
    return _CATEGORY_COLORS.get(_item_kind(value), default)


_BLOCK_BASE = {
    "dirt": "#8a6a48", "grass_side": "#6f9a4a", "grass_top": "#6f9a4a",
    "stone": "#8a8a90", "sand": "#d8c89a", "snow": "#e8eef2",
    "mossy": "#7a8a6a", "deepslate": "#5a5a66", "gravel": "#8a8580",
    "clay": "#a89a8a",
    "planks": "#9a7444", "log_side": "#7a5a38", "log_top": "#9a7a4a",
    "leaves": "#5f8a4a", "ore": "#8a8a90", "bricks": "#9a5a4a",
    "metal": "#b8c0c8", "crystal": "#9f8ad8", "glass": "#cfe8f0",
    "obsidian": "#2e2a44", "water": "#3a6ac8", "portal": "#8a4ad0",
    "lantern": "#e8c34a", "mushroom_cap": "#b05a6a", "mushroom_stem": "#d8c8a8",
    "glow": "#e8d060", "fabric": "#8a6ab0", "flower": "#7a9a4a",
    "sapling": "#6f9a4a", "mushroom": "#b05a6a", "panel": "#8a8a90",
}

_BLOCK_THEMES = (
    ("shadow", "#4a4a66"), ("dark", "#4a4a66"), ("dusk", "#6a5a86"),
    ("ironwood", "#7f8f5e"), ("twilight", "#7a5cc4"),
)


def block_color(name: str, style: str) -> str:
    """方块主色按“类型”取，主题词只影响树叶/木头/砖这类材质。"""
    value = (name or "").lower().replace("-", "_")
    base = _BLOCK_BASE.get(style, "#8a8a90")
    if style in {"leaves", "planks", "log_side", "log_top", "sapling",
                 "mushroom_cap", "flower"}:
        for key, color in _BLOCK_THEMES:
            if key in value:
                return color
    return base


def _contrast_color(base: str, *, min_light: float = 0.55) -> str:
    """A bright hue-rotated partner for eyes, ore specks and glow cores."""
    hue, light, sat = _hls(base)
    hue = (hue + 0.46) % 1.0
    sat = max(0.45, min(0.85, sat))
    light = max(min_light, min(0.80, light))
    rr, gg, bb = colorsys.hls_to_rgb(hue, light, sat)
    return _hex((rr * 255, gg * 255, bb * 255))


_MINERAL_COLORS: tuple[tuple[str, str], ...] = (
    ("diamond", "#5ee0e0"), ("moon", "#a8d8ff"), ("star", "#ffe28a"),
    ("gold", "#f0c040"), ("iron", "#d8d0c0"), ("copper", "#e08a50"),
    ("ember", "#ff8a4a"), ("fire", "#ff7a3a"), ("blood", "#d04848"),
    ("ruby", "#e04060"), ("spore", "#9fe05a"), ("moss", "#7ac050"),
    ("life", "#6ad06a"), ("arcane", "#b07aff"), ("twilight", "#a07aff"),
    ("void", "#7a5ab0"), ("shadow", "#6a5a90"), ("soul", "#7ae0d0"),
    ("frost", "#9fe8ff"), ("ice", "#9fe8ff"), ("crystal", "#8fd0d8"),
)


def mineral_color(name: str, base: str = "#8a8a90") -> str:
    """Pick the sparkle colour for an ore/crystal from its name.

    Without this the agent's generic ``accent = shade(base, 1.35)`` made
    every ore a grey stone with slightly lighter grey blobs.
    """
    value = (name or "").lower().replace("-", "_")
    for key, color in _MINERAL_COLORS:
        if key in value:
            return color
    return _contrast_color(base, min_light=0.50)


def block_accent(name: str, style: str, base: str) -> str:
    if style == "glow":
        return "#fff2b0"
    if style == "mossy":
        return "#6f9a48"
    if style == "portal":
        # A complementary hue turned the portal green; keep it in the violet
        # family and just lift the spiral tone.
        return shade(base, 1.5)
    if style in {"ore", "crystal"}:
        return mineral_color(name, base)
    if style == "bricks":
        return shade(base, 0.55)
    return shade(base, 1.35)


_ENTITY_ACCENTS: tuple[tuple[str, str], ...] = (
    ("lich", "#7ae0d0"), ("undead", "#7ae0d0"), ("necromancer", "#7ae0d0"),
    ("wraith", "#8ae8ff"), ("ghost", "#8ae8ff"), ("spirit", "#8ae8ff"),
    ("soul", "#8ae8ff"), ("specter", "#8ae8ff"), ("spectre", "#8ae8ff"),
    ("fire", "#ffb050"), ("ember", "#ffb050"), ("flame", "#ffb050"),
    ("demon", "#ff8a50"), ("spore", "#c0f070"), ("fungus", "#c0f070"),
    ("grove", "#c0f070"), ("moss", "#c0f070"), ("shadow", "#b07aff"),
    ("void", "#b07aff"), ("hollow", "#b07aff"), ("dark", "#b07aff"),
    ("frost", "#a8e8ff"), ("ice", "#a8e8ff"), ("glacier", "#a8e8ff"),
    ("serpent", "#ffd050"), ("dragon", "#ffd050"), ("scale", "#ffd050"),
)


def entity_accent(name: str, base: str) -> str:
    """Glowing eye / motif colour for an entity, contrasting with its skin."""
    value = (name or "").lower().replace("-", "_")
    for key, color in _ENTITY_ACCENTS:
        if key in value:
            return ensure_luma(color, 0.55)
    return _contrast_color(base, min_light=0.58)


_HANDLE_COLOR = "#8a6136"
_TOOL_CATEGORIES = {"sword", "pickaxe", "axe", "shovel", "hoe", "staff"}


def _hls(color: str) -> tuple[float, float, float]:
    r, g, b = _rgb(color)
    return colorsys.rgb_to_hls(r / 255.0, g / 255.0, b / 255.0)


def _is_light_tint(color: str, base: str) -> bool:
    """True when ``color`` is just a lighter version of ``base``.

    The agent historically passed ``accent=shade(base, 1.35)`` for every
    item.  That is right for a gem but wrong for a sword: it produced a
    white blade on a white handle.  Detecting the tint lets us restore the
    wooden-handle / metal-head split without changing the tool contract.
    """
    hue_a, light_a, _ = _hls(color)
    hue_b, light_b, _ = _hls(base)
    distance = abs(hue_a - hue_b)
    distance = min(distance, 1.0 - distance)
    return distance < 0.09 and light_a >= light_b + 0.07


def _item_accent(category: str, base: str) -> str:
    if category == "eye":
        return shade(base, 0.36)
    if category == "map":
        return shade(base, 0.42)
    return shade(base, 1.35)


def generate_item(path: Path, kind: str = "ingot", *, base: str | None = None,
                  accent: str | None = None, size: int = 16, seed: int = 0) -> Path:
    """Draw a readable item silhouette with bevel and outline on transparency."""
    Image, _ = _pil()
    size = max(8, min(64, int(size)))
    rng = random.Random(seed or 1)
    category = _item_kind(kind)
    if base is None:
        if category == "block":
            stem = str(kind or "").rsplit("/", 1)[-1]
            base = block_color(stem, infer_block_style(stem))
        else:
            base = material_color(kind)
    original = base
    tinted = not accent or _is_light_tint(str(accent), base)
    if category in _TOOL_CATEGORIES and tinted:
        metal = ensure_luma(base, 0.42)
        base = _HANDLE_COLOR
        accent = metal
    elif category == "book" and tinted:
        cover = ensure_luma(base, 0.30)
        base = "#e3d6b4"
        accent = cover
    elif category == "trophy" and tinted:
        base = "#7a5a2a"
        accent = "#e8c34a"
    elif category == "map" and tinted:
        base = ensure_luma(base, 0.55)
        accent = shade(base, 0.42)
    elif category == "eye" and tinted:
        accent = _item_accent(category, base)
    elif category == "bow" and tinted:
        base, accent = "#8a6136", "#e8e6d8"
    elif category == "arrow" and tinted:
        base, accent = "#9a7444", "#c8c8d0"
    elif category == "potion" and tinted:
        base, accent = "#cfe8f0", original
    elif category == "scroll" and tinted:
        base, accent = "#e3d6b4", "#8a5a3a"
    elif category in {"ring", "amulet"} and tinted:
        base = "#e8c34a"
        accent = mineral_color(kind, ensure_luma(original, 0.50))
    elif category == "feather" and tinted:
        base, accent = "#ece9dc", "#b8ac90"
    elif category == "food" and tinted:
        accent = "#5f9a4a"
    elif category == "bread" and tinted:
        base, accent = "#c89050", "#8a5430"
    elif category == "meat" and tinted:
        base, accent = "#b06050", "#e8dcc8"
    elif category == "hide" and tinted:
        base, accent = "#8a5a3a", "#c8a06a"
    elif category == "bowl" and tinted:
        base, accent = "#9a7444", "#6fae4a"
    else:
        accent = accent or _item_accent(category, base)
    accent = str(accent)
    canvas = _ItemCanvas(16)
    drawer = _ITEM_DRAW.get(category, _ITEM_DRAW["box"])
    drawer(canvas, rng)
    if category not in {"dust", "jelly", "stew", "berry"}:
        canvas.bevel()
    if category != "dust":
        # Outlining a powder pile picks out every internal gap and turns it
        # into a dark lattice; the mound already has its own bottom shadow.
        canvas.outline()
    rows = canvas.render(base, accent)
    image = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    pixels = image.load()
    for y in range(16):
        for x in range(16):
            pixels[x, y] = rows[y][x]
    if size != 16:
        image = image.resize((size, size), Image.Resampling.NEAREST)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def _draw_ingot(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Three visible planes: lit top, mid front, shadowed right side.
    canvas.poly([(3, 7), (7, 4), (13, 6), (9, 9)], _SLOT_LIGHT)
    canvas.poly([(3, 7), (9, 9), (9, 13), (3, 11)], _SLOT_BASE)
    canvas.poly([(9, 9), (13, 6), (13, 10), (9, 13)], _SLOT_DARK)
    canvas.line(4, 7, 8, 4, _SLOT_HIGHLIGHT)
    canvas.line(3, 7, 9, 9, _SLOT_DARK)
    canvas.line(9, 9, 13, 6, _SLOT_DARK)
    canvas.line(9, 13, 13, 10, _SLOT_DARK)
    canvas.line(5, 5, 8, 6, _SLOT_HIGHLIGHT)


def _draw_gem(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(8, 2), (13, 7), (8, 14), (3, 7)], _SLOT_BASE)
    canvas.poly([(8, 2), (13, 7), (8, 7)], _SLOT_LIGHT)
    canvas.poly([(8, 14), (13, 7), (8, 7)], _SLOT_DARK)
    canvas.line(8, 2, 8, 14, _SLOT_LIGHT)
    canvas.put(7, 4, _SLOT_HIGHLIGHT)
    canvas.put(6, 5, _SLOT_HIGHLIGHT)


def _draw_dust(canvas: _ItemCanvas, rng: random.Random) -> None:
    # A compact mound instead of isolated specks: at 16 px a scatter of
    # single pixels reads as dirt on the icon, not as a powder.
    for x in range(3, 13):
        depth = 3 + int(2.2 * math.sin((x - 3) / 9 * math.pi))
        for y in range(12 - depth, 13):
            canvas.put(x, y, _SLOT_BASE)
    for x in range(4, 12):
        canvas.put(x, 12, _SLOT_DARK)
    for _ in range(20):
        x = rng.randint(2, 13)
        y = rng.randint(4, 12)
        roll = rng.random()
        if roll < 0.48:
            slot = _SLOT_ACCENT
        elif roll < 0.74:
            slot = _SLOT_ACCENT_LIGHT
        elif roll < 0.9:
            slot = _SLOT_BASE
        else:
            slot = _SLOT_ACCENT_DARK
        canvas.put(x, y, slot)
    for _ in range(5):
        canvas.put(rng.randint(3, 12), rng.randint(3, 11), _SLOT_HIGHLIGHT)


def _draw_stick(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 13, 12, 3, _SLOT_BASE, width=2)
    canvas.line(5, 13, 12, 4, _SLOT_LIGHT, width=1)
    canvas.put(8, 8, _SLOT_DARK)
    canvas.put(3, 14, _SLOT_DARK)


def _draw_sword(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Blade uses the accent (metal) and the grip uses the base (wood), so
    # the role swap in generate_item does not turn the blade brown.
    canvas.line(12, 2, 6, 9, _SLOT_ACCENT, width=3)
    canvas.line(13, 2, 7, 9, _SLOT_ACCENT_LIGHT, width=1)
    canvas.line(12, 3, 6, 10, _SLOT_ACCENT_DARK, width=1)
    canvas.put(13, 1, _SLOT_HIGHLIGHT)
    canvas.line(4, 11, 9, 14, _SLOT_ACCENT_DARK, width=2)
    canvas.line(5, 11, 9, 13, _SLOT_ACCENT_LIGHT, width=1)
    canvas.line(6, 13, 3, 15, _SLOT_BASE, width=2)
    canvas.put(2, 15, _SLOT_DARK)
    canvas.put(2, 14, _SLOT_BASE)


def _draw_pickaxe(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 15, 11, 5, _SLOT_BASE, width=2)
    canvas.line(5, 14, 11, 6, _SLOT_LIGHT, width=1)
    canvas.poly([(3, 7), (5, 4), (9, 2), (13, 4), (14, 7), (11, 6), (9, 5), (6, 6)], _SLOT_ACCENT)
    canvas.line(4, 6, 9, 3, _SLOT_ACCENT_LIGHT)
    canvas.line(10, 5, 13, 6, _SLOT_ACCENT_DARK)
    canvas.put(3, 6, _SLOT_HIGHLIGHT)


def _draw_axe(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 15, 10, 5, _SLOT_BASE, width=2)
    canvas.line(5, 14, 10, 6, _SLOT_LIGHT, width=1)
    canvas.poly([(9, 2), (13, 3), (14, 8), (11, 10), (8, 7), (8, 4)], _SLOT_ACCENT)
    canvas.line(12, 3, 13, 8, _SLOT_ACCENT_LIGHT, width=1)
    canvas.line(9, 8, 12, 9, _SLOT_ACCENT_DARK)
    canvas.put(10, 3, _SLOT_HIGHLIGHT)


def _draw_shovel(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(9, 2, 4, 8, _SLOT_BASE, width=2)
    canvas.line(9, 3, 5, 8, _SLOT_LIGHT, width=1)
    canvas.poly([(3, 9), (8, 9), (9, 14), (4, 15)], _SLOT_ACCENT)
    canvas.line(4, 10, 7, 10, _SLOT_ACCENT_LIGHT)
    canvas.line(4, 13, 8, 13, _SLOT_ACCENT_DARK)
    canvas.put(3, 9, _SLOT_HIGHLIGHT)


def _draw_hoe(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 15, 10, 5, _SLOT_BASE, width=2)
    canvas.line(5, 14, 10, 6, _SLOT_LIGHT, width=1)
    canvas.rect(9, 2, 13, 3, _SLOT_ACCENT)
    canvas.rect(12, 2, 13, 7, _SLOT_ACCENT)
    canvas.line(9, 2, 13, 2, _SLOT_ACCENT_LIGHT)
    canvas.put(12, 6, _SLOT_ACCENT_DARK)


def _draw_helmet(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 10), (4, 5), (7, 2), (11, 2), (14, 5), (14, 10), (12, 11), (4, 11)], _SLOT_BASE)
    canvas.clear_rect(5, 7, 10, 11)
    # cheek guards and a nose bar keep the face opening from reading as a bucket
    canvas.rect(5, 7, 5, 11, _SLOT_BASE)
    canvas.rect(10, 7, 10, 11, _SLOT_BASE)
    canvas.rect(8, 7, 8, 10, _SLOT_BASE)
    canvas.line(6, 7, 9, 7, _SLOT_DARK)
    canvas.put(6, 8, _SLOT_DARK)
    canvas.put(9, 8, _SLOT_DARK)
    canvas.put(8, 7, _SLOT_HIGHLIGHT)
    canvas.line(4, 5, 8, 3, _SLOT_LIGHT)
    canvas.line(4, 6, 4, 10, _SLOT_LIGHT)
    canvas.line(12, 4, 13, 10, _SLOT_DARK)
    canvas.rect(3, 11, 13, 11, _SLOT_DARK)
    canvas.put(9, 2, _SLOT_HIGHLIGHT)


def _draw_chestplate(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(4, 5, 11, 13, _SLOT_BASE)
    canvas.rect(1, 5, 4, 9, _SLOT_BASE)
    canvas.rect(11, 5, 14, 9, _SLOT_BASE)
    canvas.clear_rect(6, 4, 9, 5)
    canvas.line(4, 6, 4, 13, _SLOT_LIGHT)
    canvas.line(1, 6, 1, 9, _SLOT_LIGHT)
    canvas.line(11, 12, 14, 12, _SLOT_DARK)
    canvas.rect(4, 12, 11, 13, _SLOT_DARK)
    canvas.put(6, 8, _SLOT_ACCENT)
    canvas.put(9, 8, _SLOT_ACCENT)
    canvas.put(7, 2, _SLOT_HIGHLIGHT)


def _draw_leggings(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(4, 3, 11, 6, _SLOT_BASE)
    canvas.rect(4, 6, 7, 14, _SLOT_BASE)
    canvas.rect(9, 6, 12, 14, _SLOT_BASE)
    canvas.clear_rect(8, 6, 8, 14)
    canvas.line(4, 4, 4, 14, _SLOT_LIGHT)
    canvas.line(6, 13, 7, 13, _SLOT_DARK)
    canvas.line(10, 13, 11, 13, _SLOT_DARK)
    canvas.rect(4, 3, 11, 3, _SLOT_LIGHT)
    canvas.put(8, 3, _SLOT_ACCENT)


def _draw_boots(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(2, 6, 6, 12, _SLOT_BASE)
    canvas.rect(9, 6, 13, 12, _SLOT_BASE)
    canvas.rect(2, 12, 7, 13, _SLOT_BASE)
    canvas.rect(9, 12, 14, 13, _SLOT_BASE)
    canvas.line(2, 7, 2, 12, _SLOT_LIGHT)
    canvas.line(9, 7, 9, 12, _SLOT_LIGHT)
    canvas.rect(2, 13, 7, 13, _SLOT_DARK)
    canvas.rect(9, 13, 14, 13, _SLOT_DARK)
    canvas.put(4, 5, _SLOT_ACCENT)
    canvas.put(11, 5, _SLOT_ACCENT)


def _draw_sigil(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Rune medallion: ring + branching rune + center diamond.  A plain ring
    # with a plus inside looked like a wagon wheel.
    canvas.circle(8, 8, 6.0, _SLOT_ACCENT, fill=False)
    canvas.line(8, 2, 8, 14, _SLOT_ACCENT)
    canvas.line(4, 5, 8, 8, _SLOT_ACCENT_LIGHT)
    canvas.line(12, 5, 8, 8, _SLOT_ACCENT_LIGHT)
    canvas.line(4, 11, 8, 8, _SLOT_ACCENT_LIGHT)
    canvas.line(12, 11, 8, 8, _SLOT_ACCENT_LIGHT)
    canvas.poly([(8, 5), (11, 8), (8, 11), (5, 8)], _SLOT_ACCENT_LIGHT)
    canvas.put(8, 7, _SLOT_HIGHLIGHT)
    canvas.put(8, 8, _SLOT_ACCENT_DARK)
    # Break the ring at the cardinal points so it does not read as a wheel.
    for x, y in ((8, 1), (8, 15), (1, 8), (15, 8)):
        canvas.put(x, y, 0)


def _draw_cloth(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 5), (12, 4), (13, 11), (4, 13)], _SLOT_BASE)
    canvas.line(3, 6, 12, 5, _SLOT_LIGHT)
    canvas.line(4, 8, 11, 8, _SLOT_DARK)
    canvas.line(4, 10, 12, 10, _SLOT_DARK)
    for x in range(5, 11, 2):
        canvas.put(x, 7, _SLOT_ACCENT_LIGHT)
    canvas.put(3, 5, _SLOT_HIGHLIGHT)


def _draw_fang(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(6, 2), (10, 3), (11, 7), (10, 11), (8, 14), (7, 11), (5, 7)], _SLOT_BASE)
    canvas.line(6, 3, 8, 12, _SLOT_LIGHT)
    canvas.line(9, 4, 10, 9, _SLOT_DARK)
    canvas.rect(6, 2, 9, 3, _SLOT_DARK)
    canvas.put(7, 3, _SLOT_HIGHLIGHT)


def _draw_scale(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Shield-shaped scale with growth ridges; the old two long veins made
    # it read as a green arrow.
    canvas.poly([(8, 2), (12, 5), (11, 10), (8, 14), (5, 10), (4, 5)], _SLOT_ACCENT)
    canvas.line(6, 6, 10, 6, _SLOT_ACCENT_DARK)
    canvas.line(5, 8, 11, 8, _SLOT_ACCENT_DARK)
    canvas.line(6, 10, 10, 10, _SLOT_ACCENT_DARK)
    canvas.line(8, 3, 8, 13, _SLOT_ACCENT_LIGHT)
    canvas.put(7, 3, _SLOT_HIGHLIGHT)
    canvas.put(6, 4, _SLOT_LIGHT)


def _draw_shard(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(4, 3), (8, 6), (7, 13), (4, 10)], _SLOT_ACCENT)
    canvas.poly([(10, 4), (13, 7), (11, 14), (9, 10)], _SLOT_ACCENT_DARK)
    canvas.line(4, 4, 6, 12, _SLOT_ACCENT_LIGHT)
    canvas.line(11, 5, 12, 12, _SLOT_ACCENT_LIGHT)
    canvas.put(5, 4, _SLOT_HIGHLIGHT)


def _draw_staff(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(3, 15, 10, 7, _SLOT_BASE, width=2)
    canvas.line(4, 14, 10, 8, _SLOT_LIGHT, width=1)
    canvas.circle(11, 4, 3.4, _SLOT_ACCENT)
    canvas.circle(10, 3, 1.2, _SLOT_ACCENT_LIGHT)
    canvas.put(12, 5, _SLOT_ACCENT_DARK)
    canvas.put(8, 2, _SLOT_HIGHLIGHT)
    canvas.put(13, 1, _SLOT_HIGHLIGHT)


def _draw_eye(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 8, 6.2, _SLOT_BASE)
    canvas.circle(8, 8, 3.8, _SLOT_ACCENT)
    canvas.circle(8, 8, 2.0, _SLOT_ACCENT_DARK)
    canvas.put(6, 6, _SLOT_HIGHLIGHT)
    canvas.put(7, 5, _SLOT_HIGHLIGHT)
    canvas.line(3, 8, 13, 8, _SLOT_DARK)


def _draw_berry(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Three overlapping berries packed in a triangle read as a cluster;
    # the old four-circle cross looked like a plus sign.
    for cx, cy, radius in ((6.4, 10.6, 2.5), (10.4, 10.4, 2.3), (8.4, 7.2, 2.6)):
        canvas.circle(cx, cy, radius, _SLOT_ACCENT)
        canvas.put(int(cx) - 1, int(cy) - 1, _SLOT_ACCENT_LIGHT)
        canvas.put(int(cx) + 1, int(cy) + 1, _SLOT_ACCENT_DARK)
    canvas.line(8, 5, 8, 3, _SLOT_DARK)
    canvas.line(9, 3, 12, 2, _SLOT_ACCENT_LIGHT)
    canvas.put(12, 3, _SLOT_ACCENT_LIGHT)


def _draw_map(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(2, 3, 13, 12, _SLOT_BASE)
    canvas.line(2, 3, 13, 3, _SLOT_LIGHT)
    canvas.line(2, 12, 13, 12, _SLOT_DARK)
    canvas.line(4, 5, 11, 5, _SLOT_DARK)
    canvas.line(4, 7, 11, 7, _SLOT_DARK)
    canvas.line(4, 5, 7, 5, _SLOT_LIGHT)
    canvas.line(9, 8, 12, 11, _SLOT_ACCENT)
    canvas.line(12, 8, 9, 11, _SLOT_ACCENT)
    canvas.put(3, 3, _SLOT_HIGHLIGHT)


def _draw_book(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(3, 3, 13, 13, _SLOT_ACCENT_DARK)
    canvas.rect(4, 4, 12, 12, _SLOT_ACCENT)
    canvas.rect(2, 3, 4, 13, _SLOT_DARK)
    canvas.line(5, 6, 11, 6, _SLOT_ACCENT_LIGHT)
    canvas.line(5, 8, 11, 8, _SLOT_ACCENT_LIGHT)
    canvas.rect(5, 10, 10, 11, _SLOT_HIGHLIGHT)
    canvas.put(3, 3, _SLOT_LIGHT)


def _draw_trophy(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(5, 3), (11, 3), (10, 9), (6, 9)], _SLOT_ACCENT)
    canvas.line(6, 4, 5, 8, _SLOT_ACCENT_LIGHT)
    canvas.circle(3.0, 5.0, 2.0, _SLOT_ACCENT, fill=False)
    canvas.circle(13.0, 5.0, 2.0, _SLOT_ACCENT, fill=False)
    canvas.rect(7, 9, 9, 11, _SLOT_ACCENT_DARK)
    canvas.rect(5, 12, 11, 14, _SLOT_ACCENT_DARK)
    canvas.rect(6, 12, 10, 12, _SLOT_ACCENT_LIGHT)
    canvas.put(7, 4, _SLOT_HIGHLIGHT)


def _draw_bone(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 12, 11, 4, _SLOT_BASE, width=2)
    canvas.line(5, 12, 11, 5, _SLOT_LIGHT, width=1)
    for cx, cy in ((3, 12), (12, 4)):
        canvas.circle(cx + 0.5, cy + 0.5, 1.8, _SLOT_BASE)
        canvas.put(cx, cy, _SLOT_LIGHT)
    canvas.put(6, 10, _SLOT_DARK)


def _draw_sac(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Pear-shaped body with a tied neck; a plain circle read as a green ball.
    canvas.poly([(6, 4), (10, 4), (12, 7), (13, 10), (11, 13), (8, 14),
                 (4, 12), (3, 8), (4, 5)], _SLOT_BASE)
    canvas.line(4, 6, 5, 11, _SLOT_LIGHT)
    canvas.line(10, 5, 11, 8, _SLOT_DARK)
    canvas.rect(6, 2, 10, 3, _SLOT_ACCENT_DARK)
    canvas.line(7, 2, 9, 2, _SLOT_ACCENT)
    for _ in range(4):
        canvas.put(rng.randint(6, 10), rng.randint(7, 12), _SLOT_ACCENT_LIGHT)
    canvas.put(9, 12, _SLOT_ACCENT)


def _draw_jelly(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 9, 5.2, _SLOT_ACCENT)
    canvas.circle(6.6, 7.2, 2.2, _SLOT_ACCENT_LIGHT)
    canvas.put(6, 6, _SLOT_HIGHLIGHT)
    canvas.put(7, 6, _SLOT_HIGHLIGHT)
    for x, y in ((10, 8), (8, 12), (11, 11)):
        canvas.put(x, y, _SLOT_ACCENT_DARK)
    canvas.put(9, 10, _SLOT_ACCENT_LIGHT)
    canvas.put(5, 11, _SLOT_ACCENT_LIGHT)


def _draw_stew(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 8), (13, 8), (11, 14), (5, 14)], _SLOT_BASE)
    canvas.line(3, 8, 13, 8, _SLOT_LIGHT)
    canvas.rect(4, 6, 12, 8, _SLOT_ACCENT)
    for x in (6, 9, 11):
        canvas.put(x, 7, _SLOT_ACCENT_LIGHT)
    canvas.line(4, 12, 12, 12, _SLOT_DARK)


def _draw_seed(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 10, 3.0, _SLOT_BASE)
    canvas.put(7, 9, _SLOT_LIGHT)
    canvas.line(8, 7, 10, 4, _SLOT_DARK)
    canvas.poly([(10, 4), (13, 3), (12, 6)], _SLOT_ACCENT)


def _draw_box(canvas: _ItemCanvas, rng: random.Random) -> None:
    """Isometric cube for block items.

    The old fallback was a flat 8x8 square with a highlight, so every unknown
    block item in the inventory looked like the same grey button.  Three
    faces read as a block even at 16 px.
    """
    canvas.poly([(8, 1), (15, 5), (8, 9), (1, 5)], _SLOT_LIGHT)
    canvas.poly([(1, 5), (8, 9), (8, 15), (1, 11)], _SLOT_BASE)
    canvas.poly([(15, 5), (15, 11), (8, 15), (8, 9)], _SLOT_DARK)
    canvas.line(1, 5, 8, 9, _SLOT_OUTLINE)
    canvas.line(15, 5, 8, 9, _SLOT_OUTLINE)
    canvas.line(8, 9, 8, 15, _SLOT_OUTLINE)
    for _ in range(5):
        x = rng.randint(2, 14)
        y = rng.randint(3, 13)
        if canvas.get(x, y) not in (0, _SLOT_OUTLINE):
            canvas.put(x, y, _SLOT_ACCENT_DARK if rng.random() < 0.5 else _SLOT_BASE)


def _draw_arrow(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(4, 13, 9, 8, _SLOT_LIGHT)
    canvas.line(5, 13, 10, 8, _SLOT_BASE)
    canvas.poly([(9, 8), (14, 3), (13, 8), (10, 10)], _SLOT_ACCENT)
    canvas.line(12, 4, 13, 5, _SLOT_ACCENT_LIGHT)
    canvas.poly([(1, 11), (5, 12), (3, 15)], _SLOT_ACCENT_DARK)
    canvas.poly([(2, 15), (5, 13), (4, 15)], _SLOT_ACCENT)


def _draw_bow(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Limb bulges left, grip sits in the middle, string runs straight between
    # the tips; without the string the sprite reads as a banana.
    canvas.line(7, 1, 5, 3, _SLOT_BASE, width=2)
    canvas.line(5, 3, 3, 6, _SLOT_BASE, width=2)
    canvas.line(3, 6, 2, 8, _SLOT_BASE, width=2)
    canvas.line(2, 8, 3, 10, _SLOT_BASE, width=2)
    canvas.line(3, 10, 5, 13, _SLOT_BASE, width=2)
    canvas.line(5, 13, 7, 15, _SLOT_BASE, width=2)
    canvas.line(6, 2, 4, 5, _SLOT_LIGHT)
    canvas.line(4, 11, 6, 14, _SLOT_LIGHT)
    canvas.line(8, 1, 8, 15, _SLOT_ACCENT_LIGHT)
    canvas.rect(3, 7, 4, 9, _SLOT_ACCENT_DARK)
    canvas.put(3, 7, _SLOT_ACCENT)
    canvas.put(2, 8, _SLOT_DARK)


def _draw_bowl(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 7), (13, 7), (11, 13), (5, 13)], _SLOT_BASE)
    canvas.line(3, 7, 13, 7, _SLOT_LIGHT)
    canvas.rect(5, 8, 11, 8, _SLOT_ACCENT)
    canvas.put(6, 6, _SLOT_ACCENT_LIGHT)
    canvas.put(7, 5, _SLOT_ACCENT)
    canvas.put(9, 5, _SLOT_ACCENT)
    canvas.put(10, 6, _SLOT_ACCENT_LIGHT)
    canvas.line(5, 12, 11, 12, _SLOT_DARK)


def _draw_potion(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 10, 5.2, _SLOT_LIGHT)
    canvas.rect(7, 2, 9, 5, _SLOT_LIGHT)
    canvas.rect(6, 1, 10, 2, _SLOT_ACCENT_DARK)
    for y in range(9, 15):
        for x in range(3, 14):
            if canvas.get(x, y) != 0:
                canvas.put(x, y, _SLOT_ACCENT)
    canvas.line(4, 8, 11, 8, _SLOT_ACCENT_LIGHT)
    canvas.put(5, 6, _SLOT_HIGHLIGHT)
    canvas.put(6, 5, _SLOT_HIGHLIGHT)


def _draw_scroll(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.rect(4, 3, 11, 12, _SLOT_LIGHT)
    canvas.rect(2, 1, 13, 3, _SLOT_ACCENT)
    canvas.rect(2, 12, 13, 14, _SLOT_ACCENT)
    canvas.line(2, 2, 13, 2, _SLOT_ACCENT_LIGHT)
    canvas.line(2, 13, 13, 13, _SLOT_ACCENT_DARK)
    for y in (5, 7, 9, 11):
        canvas.line(5, y, 10, y, _SLOT_DARK)
    canvas.put(11, 5, _SLOT_BASE)
    canvas.put(11, 11, _SLOT_BASE)


def _draw_ring(canvas: _ItemCanvas, rng: random.Random) -> None:
    # Octagonal band instead of two naively stroked circles (which read as a
    # square picture frame at 16 px).
    outer = [(5, 3), (10, 3), (13, 6), (13, 10), (10, 13), (5, 13),
             (2, 10), (2, 6)]
    inner = [(6, 5), (9, 5), (11, 7), (11, 9), (9, 11), (6, 11),
             (4, 9), (4, 7)]
    canvas.poly(outer, _SLOT_BASE)
    canvas.poly(inner, 0)
    canvas.line(3, 6, 5, 4, _SLOT_LIGHT)
    canvas.line(4, 10, 5, 12, _SLOT_LIGHT)
    canvas.line(10, 12, 12, 10, _SLOT_DARK)
    canvas.poly([(6, 3), (8, 0), (10, 3), (8, 5)], _SLOT_ACCENT)
    canvas.put(7, 2, _SLOT_ACCENT_LIGHT)
    canvas.put(8, 4, _SLOT_ACCENT_DARK)
    canvas.put(3, 8, _SLOT_HIGHLIGHT)


def _draw_amulet(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(3, 1, 8, 5, _SLOT_BASE)
    canvas.line(13, 1, 8, 5, _SLOT_BASE)
    canvas.put(3, 1, _SLOT_LIGHT)
    canvas.put(13, 1, _SLOT_LIGHT)
    canvas.circle(8, 9, 4.0, _SLOT_BASE, fill=False)
    canvas.circle(8, 9, 3.0, _SLOT_ACCENT)
    canvas.put(7, 8, _SLOT_ACCENT_LIGHT)
    canvas.put(9, 10, _SLOT_ACCENT_DARK)


def _draw_key(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(5, 5, 3.0, _SLOT_BASE, fill=False)
    canvas.circle(5, 5, 1.8, _SLOT_BASE, fill=False)
    canvas.line(7, 7, 12, 12, _SLOT_BASE, width=2)
    canvas.rect(10, 9, 12, 10, _SLOT_BASE)
    canvas.rect(12, 11, 13, 12, _SLOT_BASE)
    canvas.put(4, 3, _SLOT_HIGHLIGHT)
    canvas.put(11, 12, _SLOT_DARK)


def _draw_feather(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.line(3, 14, 12, 3, _SLOT_BASE)
    canvas.poly([(8, 2), (13, 5), (10, 10), (5, 12), (6, 8)], _SLOT_ACCENT_LIGHT)
    canvas.line(6, 9, 11, 4, _SLOT_ACCENT)
    for x, y in ((7, 4), (10, 6), (8, 8), (6, 10)):
        canvas.put(x, y, _SLOT_ACCENT_DARK)
    canvas.put(3, 14, _SLOT_DARK)


def _draw_nugget(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(5, 8), (8, 5), (12, 7), (11, 11), (7, 12), (4, 10)], _SLOT_BASE)
    canvas.poly([(5, 8), (8, 5), (10, 7), (7, 9)], _SLOT_LIGHT)
    canvas.poly([(9, 9), (12, 7), (11, 11), (8, 11)], _SLOT_DARK)
    canvas.put(6, 7, _SLOT_HIGHLIGHT)


def _draw_food(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 9, 5.0, _SLOT_BASE)
    canvas.poly([(8, 4), (11, 1), (13, 2), (11, 5)], _SLOT_ACCENT)
    canvas.line(8, 4, 8, 2, _SLOT_DARK)
    canvas.put(5, 6, _SLOT_HIGHLIGHT)
    canvas.put(6, 6, _SLOT_LIGHT)
    canvas.put(9, 12, _SLOT_DARK)


def _draw_bread(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 9), (5, 5), (11, 4), (13, 8), (12, 12), (5, 12)], _SLOT_BASE)
    canvas.line(5, 6, 11, 5, _SLOT_LIGHT)
    for x, y in ((6, 8), (8, 9), (10, 8)):
        canvas.line(x, y, x + 2, y - 2, _SLOT_DARK)
    canvas.put(4, 10, _SLOT_LIGHT)


def _draw_meat(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(10, 6, 3.8, _SLOT_BASE)
    canvas.line(4, 13, 7, 10, _SLOT_ACCENT_LIGHT, width=2)
    canvas.circle(3.5, 13.5, 1.4, _SLOT_ACCENT_LIGHT)
    canvas.put(9, 4, _SLOT_LIGHT)
    canvas.put(12, 7, _SLOT_DARK)


def _draw_hide(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.poly([(3, 4), (7, 2), (12, 3), (14, 7), (12, 12), (8, 14), (4, 12), (2, 8)],
                _SLOT_BASE)
    canvas.poly([(4, 5), (7, 4), (10, 5), (8, 7)], _SLOT_LIGHT)
    canvas.line(5, 9, 10, 11, _SLOT_DARK)
    canvas.put(11, 9, _SLOT_ACCENT)
    canvas.put(8, 12, _SLOT_ACCENT_DARK)


def _draw_coin(canvas: _ItemCanvas, rng: random.Random) -> None:
    canvas.circle(8, 8, 5.0, _SLOT_BASE)
    canvas.circle(8, 8, 3.2, _SLOT_ACCENT_LIGHT, fill=False)
    canvas.line(8, 5, 8, 11, _SLOT_ACCENT_DARK)
    canvas.put(5, 5, _SLOT_HIGHLIGHT)


_ITEM_DRAW = {
    "ingot": _draw_ingot,
    "gem": _draw_gem,
    "dust": _draw_dust,
    "stick": _draw_stick,
    "sword": _draw_sword,
    "pickaxe": _draw_pickaxe,
    "axe": _draw_axe,
    "shovel": _draw_shovel,
    "hoe": _draw_hoe,
    "helmet": _draw_helmet,
    "chestplate": _draw_chestplate,
    "leggings": _draw_leggings,
    "boots": _draw_boots,
    "sigil": _draw_sigil,
    "cloth": _draw_cloth,
    "fang": _draw_fang,
    "scale": _draw_scale,
    "shard": _draw_shard,
    "staff": _draw_staff,
    "eye": _draw_eye,
    "berry": _draw_berry,
    "map": _draw_map,
    "book": _draw_book,
    "trophy": _draw_trophy,
    "bone": _draw_bone,
    "sac": _draw_sac,
    "jelly": _draw_jelly,
    "stew": _draw_stew,
    "seed": _draw_seed,
    "arrow": _draw_arrow,
    "bow": _draw_bow,
    "bowl": _draw_bowl,
    "potion": _draw_potion,
    "scroll": _draw_scroll,
    "ring": _draw_ring,
    "amulet": _draw_amulet,
    "key": _draw_key,
    "feather": _draw_feather,
    "nugget": _draw_nugget,
    "food": _draw_food,
    "bread": _draw_bread,
    "meat": _draw_meat,
    "hide": _draw_hide,
    "coin": _draw_coin,
    "block": _draw_box,
    "box": _draw_box,
}


# ----------------------------------------------------------------------
# entity UV atlases
# ----------------------------------------------------------------------

# vanilla 64x64 humanoid UV layout
_HUMANOID_BOXES = [
    {"part": "head", "uv": (0, 0), "size": (8, 8, 8)},
    {"part": "hat", "uv": (32, 0), "size": (8, 8, 8)},
    {"part": "body", "uv": (16, 16), "size": (8, 12, 4)},
    {"part": "right_arm", "uv": (40, 16), "size": (4, 12, 4)},
    {"part": "left_arm", "uv": (32, 48), "size": (4, 12, 4)},
    {"part": "right_leg", "uv": (0, 16), "size": (4, 12, 4)},
    {"part": "left_leg", "uv": (16, 48), "size": (4, 12, 4)},
    {"part": "jacket", "uv": (16, 32), "size": (8, 12, 4)},
    {"part": "right_sleeve", "uv": (40, 32), "size": (4, 12, 4)},
    {"part": "left_sleeve", "uv": (48, 48), "size": (4, 12, 4)},
    {"part": "right_pants", "uv": (0, 32), "size": (4, 12, 4)},
    {"part": "left_pants", "uv": (0, 48), "size": (4, 12, 4)},
]

# 64x64 quadruped atlas.  Every box footprint fits inside the canvas:
#   head 0..32 x 0..16, body 16..56 x 20..38, legs 0..24 x 20..49,
#   tail 40..60 x 40..50, wings 38..56 x 0..19.
_QUADRUPED_BOXES = [
    {"part": "head", "uv": (0, 0), "size": (8, 8, 8)},
    {"part": "body", "uv": (16, 20), "size": (8, 6, 12)},
    {"part": "tail", "uv": (40, 40), "size": (2, 2, 8)},
    {"part": "leg_fl", "uv": (0, 20), "size": (3, 10, 3)},
    {"part": "leg_fr", "uv": (12, 20), "size": (3, 10, 3)},
    {"part": "leg_bl", "uv": (0, 36), "size": (3, 10, 3)},
    {"part": "leg_br", "uv": (12, 36), "size": (3, 10, 3)},
    {"part": "ear_l", "uv": (32, 0), "size": (2, 2, 1)},
    {"part": "ear_r", "uv": (32, 4), "size": (2, 2, 1)},
    {"part": "wing_l", "uv": (38, 0), "size": (8, 8, 1)},
    {"part": "wing_r", "uv": (38, 10), "size": (8, 8, 1)},
]

ENTITY_LAYOUTS = {
    "humanoid": _HUMANOID_BOXES,
    "biped": _HUMANOID_BOXES,
    "quadruped": _QUADRUPED_BOXES,
}


def entity_boxes(layout: str = "humanoid", *, variant: str = "") -> list[dict]:
    """Return a copy of the UV table for ``layout``.

    ``winged`` quadruped variants drop the wing boxes only if they are not
    requested; the default keeps them so a caller can paint wings without
    rewriting the atlas layout.
    """
    key = (layout or "humanoid").lower()
    if key not in ENTITY_LAYOUTS:
        raise ValueError(
            f"未知实体布局「{layout}」。可用布局：humanoid、biped、quadruped，"
            "或使用 boxes 自定义部件。"
        )
    boxes = [dict(box) for box in ENTITY_LAYOUTS[key]]
    if key == "quadruped" and variant and "wing" not in variant.lower() \
            and "winged" not in variant.lower():
        boxes = [box for box in boxes if not str(box.get("part", "")).startswith("wing")]
    return boxes


def _box_fits(box: dict, width: int, height: int) -> bool:
    try:
        u, v = int(box["uv"][0]), int(box["uv"][1])
        w, h, d = (int(item) for item in box["size"])
    except (KeyError, IndexError, TypeError, ValueError):
        return False
    return 0 <= u and 0 <= v and u + 2 * d + 2 * w <= width and v + d + h <= height


def generate_entity(path: Path, *, layout: str = "humanoid", base: str = "#8a8a8a",
                    accent: str = "#5a3fbf", seed: int = 0, size: tuple[int, int] = (64, 64),
                    variant: str = "", boxes: list[dict] | None = None,
                    transparent: bool = True) -> Path:
    """Render an entity UV atlas, not a front-view illustration.

    ``layout="humanoid"`` uses the vanilla 64x64 player skin layout.
    ``layout="quadruped"`` uses the built-in four-legged layout.
    ``layout="boxes"`` paints the caller's boxes with the vanilla cube UV
    convention: top/bottom/right/front/left/back.

    The atlas is always painted at the layout's native resolution (64x64 for
    the built-in layouts) and then nearest-neighbour scaled, so asking for a
    128x128 skin gives a crisp 2x skin instead of sparse single pixels.
    """
    Image, _ = _pil()
    requested = (max(16, int(size[0])), max(16, int(size[1])))
    key = (layout or "humanoid").lower()
    if boxes:
        chosen = boxes
        native = requested
    else:
        chosen = entity_boxes(key, variant=variant)
        native = (64, 64) if key in {"humanoid", "biped", "quadruped"} else requested
    width, height = native
    rng = random.Random(seed or 1)
    grid: list[list[str | None]] = _blank(width, height)
    for box in chosen:
        # A custom box that runs past the canvas used to be silently clipped,
        # producing half-painted limbs.  Skip it; callers get a usable atlas
        # instead of a corrupt one.
        if not _box_fits(box, width, height):
            continue
        _paint_box(grid, width, height, box, base, accent, rng, variant)

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0) if transparent else _rgba("#1b1b24", 255))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            if grid[y][x] is not None:
                pixels[x, y] = _rgba(grid[y][x], 255)
    if requested != native:
        image = image.resize(requested, Image.Resampling.NEAREST)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


# Outer clothing layers are not a second solid skin: in vanilla they are
# mostly transparent cut-outs (cuffs, hems, shoulder pads).  Painting them
# fully with random holes produced the "乱码色块" look, so they are drawn as
# deliberate bands instead.
_OUTER_PARTS = {"hat", "jacket", "right_sleeve", "left_sleeve", "right_pants", "left_pants"}


def _paint_box(grid, width, height, box: dict, base: str, accent: str,
               rng: random.Random, variant: str) -> None:
    try:
        u, v = int(box["uv"][0]), int(box["uv"][1])
        w, h, d = (int(item) for item in box["size"])
    except (KeyError, IndexError, TypeError, ValueError):
        return
    if w <= 0 or h <= 0 or d <= 0:
        return
    part = str(box.get("part") or "").lower()
    color = ensure_luma(str(box.get("color") or base), 0.38)
    accent_color = ensure_luma(str(box.get("accent") or accent), 0.42)
    colors = palette(color, 7)
    accents = palette(accent_color, 6)
    is_outer = any(key in part for key in _OUTER_PARTS)
    # face -> (x, y, w, h, palette level).  Top is lit, bottom is in shadow;
    # back faces sit one step darker than the front.
    faces = [
        ("top", u + d, v, w, d, 5.4),
        ("bottom", u + d + w, v, w, d, 1.2),
        ("right", u, v + d, d, h, 4.0),
        ("front", u + d, v + d, w, h, 4.8),
        ("left", u + d + w, v + d, d, h, 4.3),
        ("back", u + 2 * d + w, v + d, w, h, 3.2),
    ]
    field = _fbm(max(1, w + 2 * d + w), max(1, h + d), rng, cell=3.2, octaves=2)
    for name, fx, fy, fw, fh, level in faces:
        if is_outer:
            _paint_overlay_face(grid, width, height, part, name,
                                fx, fy, fw, fh, colors, accents)
        else:
            _paint_base_face(grid, width, height, name, fx, fy, fw, fh,
                             colors, level, field, d)
        if part.startswith("head") and name == "front":
            _face_details(grid, fx, fy, fw, fh, accents, variant)
        if part == "body" and name in ("front", "back", "left", "right"):
            _body_details(grid, fx, fy, fw, fh, colors, accents)
        if part.startswith(("right_arm", "left_arm")) and name in ("front", "right", "left"):
            _cuff_details(grid, fx, fy, fw, fh, colors, accents)
        if part.startswith(("right_leg", "left_leg")) and name in ("front", "right", "left"):
            _boot_details(grid, fx, fy, fw, fh, colors)
    _motif_details(grid, width, height, part, faces, colors, accents, variant)


def _paint_base_face(grid, width, height, face, x0, y0, fw, fh, colors, level,
                     field, d) -> None:
    """Fill one cube face with at most three hard-edged value bands.

    Per-pixel smooth noise is what made skins look like random mush.  Pixel
    art uses 2-3 flat tones per surface, so the noise is quantized to -1/0/+1
    palette steps and a bottom-edge ambient-occlusion row is added.
    """
    last = len(colors) - 1
    for ly in range(fh):
        shade = -0.05 * (ly / max(1, fh - 1)) if face not in ("bottom",) else 0.0
        for lx in range(fw):
            x, y = x0 + lx, y0 + ly
            if not (0 <= x < width and 0 <= y < height):
                continue
            sample_x = min(len(field[0]) - 1, lx + (0 if face in ("front", "back") else d))
            sample_y = min(len(field) - 1, ly + (0 if face in ("top", "front") else d // 2))
            noise = field[sample_y][sample_x]
            band = 0.0
            if noise > 0.63:
                band = 0.8
            elif noise < 0.34:
                band = -0.8
            index = int(round(level + band + shade))
            if face in ("front", "back", "left", "right") and ly == fh - 1:
                index -= 1
            grid[y][x] = colors[max(0, min(last, index))]


def _paint_overlay_face(grid, width, height, part, face, x0, y0, fw, fh,
                        colors, accents) -> None:
    """Paint only the intentional parts of an outer clothing layer."""
    if part.startswith("hat"):
        # Hood / helmet crown: cover the skull, never the face.
        if face in ("top", "bottom"):
            bands = [(0, fh)]
        else:
            bands = [(0, max(2, fh // 3))]
        edges = range(0)
        tail = range(0)
    elif part.startswith("jacket"):
        if face in ("top", "bottom"):
            bands = [(0, fh)]
        elif face in ("front", "back"):
            bands = [(0, 1), (fh - 2, fh)]
            edges = range(0, min(2, fw))
            tail = range(max(0, fw - 2), fw)
        else:
            bands = [(0, 1), (fh - 2, fh)]
            edges = range(0, 1)
            tail = range(max(0, fw - 1), fw)
    elif "sleeve" in part:
        if face in ("top", "bottom"):
            bands = [(0, fh)]
        else:
            bands = [(0, 2), (fh - 3, fh)]
        edges = range(0)
        tail = range(0)
    else:  # pants
        if face in ("top", "bottom"):
            bands = [(0, fh)]
        else:
            bands = [(0, 2), (fh - 3, fh)]
        edges = range(0)
        tail = range(0)

    def put(x, y, color) -> None:
        if 0 <= x < width and 0 <= y < height:
            grid[y][x] = color

    base_index = 4 if face in ("top", "front", "left") else 3
    trim = accents[2]
    for start, stop in bands:
        for ly in range(start, min(fh, stop)):
            if ly == start:
                color = trim
            elif ly == stop - 1 and part.startswith("hat") and face not in ("top", "bottom"):
                # bright hood rim instead of a second dark row
                color = accents[-1]
            else:
                color = colors[max(0, base_index - (1 if ly == stop - 1 else 0))]
            for lx in range(fw):
                put(x0 + lx, y0 + ly, color)
    if face in ("front", "back", "left", "right"):
        for lx in edges:
            for ly in range(fh):
                put(x0 + lx, y0 + ly, colors[max(0, base_index - 1)])
        for lx in tail:
            for ly in range(fh):
                put(x0 + lx, y0 + ly, colors[max(0, base_index - 1)])


def _motif_details(grid, width, height, part, faces, colors, accents, variant) -> None:
    """Add material motifs (scales / ribs / spots / robe trim) from the name."""
    name = (variant or "").lower()
    if not name or not part.startswith(("head", "body", "right_arm", "left_arm",
                                        "right_leg", "left_leg")):
        return
    front = next((item for item in faces if item[0] == "front"), None)
    if not front:
        return
    _, x0, y0, fw, fh, _level = front
    dark = colors[1]
    light = colors[-1]
    accent = accents[-1]

    def put(x, y, color) -> None:
        if 0 <= x < width and 0 <= y < height:
            grid[y][x] = color

    body = part == "body"
    if any(key in name for key in ("bone", "lich", "skeleton", "necromancer")):
        if body:
            # Sternum + short paired ribs, not full-width stripes.
            cx = x0 + fw // 2
            for ly in range(2, fh - 1, 2):
                put(cx, y0 + ly, dark)
                for lx in (cx - 3, cx - 2, cx + 2, cx + 3):
                    if x0 < lx < x0 + fw - 1:
                        put(lx, y0 + ly, dark)
        elif part.startswith("head"):
            for lx in range(x0 + 2, x0 + fw - 2):
                put(lx, y0 + fh - 2, dark)
    elif any(key in name for key in ("scale", "serpent", "dragon", "snake")):
        if body:
            for ly in range(1, fh - 1, 2):
                offset = 1 if (ly // 2) % 2 else 0
                for lx in range(1 + offset, fw - 1, 3):
                    put(x0 + lx, y0 + ly, dark)
                    put(x0 + lx, y0 + ly + 1, dark)
    elif any(key in name for key in ("moss", "spore", "grove", "forest", "crawler")):
        if body or part.startswith("head"):
            for ly in range(1, fh - 1, 4):
                for lx in range(1 + (ly % 3), fw - 1, 4):
                    put(x0 + lx, y0 + ly, accent)
    elif any(key in name for key in ("fire", "glow", "ember", "light", "wisp")):
        if body:
            for ly in range(2, fh - 1, 3):
                put(x0 + 1 + (ly % max(1, fw - 2)), y0 + ly, light)
    elif any(key in name for key in ("robed", "robe", "mage", "priest")):
        if body:
            for ly in range(1, fh - 1):
                put(x0 + fw // 2, y0 + ly, accents[1])
                put(x0 + fw // 2 - 1, y0 + ly, accents[2])
    elif any(key in name for key in ("wraith", "ghost", "spirit", "shadow")):
        if body:
            for ly in range(2, fh - 1, 3):
                for lx in range(fw // 3, fw - fw // 3):
                    put(x0 + lx, y0 + ly, colors[max(0, len(colors) - 2)])


def _face_details(grid, x0, y0, w, h, accents, variant) -> None:
    variant = (variant or "").lower()
    eye_color = accents[-1]
    if any(key in variant for key in ("mage", "lich", "wraith", "undead", "spirit")):
        eye_color = accents[-1]
    eye_h = max(1, h // 12)
    eye_w = max(1, w // 8)
    left_x = x0 + max(1, w // 4) - eye_w // 2
    right_x = x0 + w - max(1, w // 4) - eye_w
    eye_y = y0 + max(2, h // 2 - 1)
    for ex in (left_x, right_x):
        for dx in range(eye_w):
            for dy in range(eye_h):
                _put(grid, ex + dx, eye_y + dy, eye_color)
        _put(grid, ex, eye_y - 1 if eye_y - 1 > y0 else eye_y, "#2a2230")
    brow_y = max(y0, eye_y - 2)
    for bx in range(left_x - 1, right_x + eye_w + 1):
        _put(grid, bx, brow_y, "#2a2230")
    mouth_y = min(y0 + h - 2, eye_y + max(2, h // 4))
    for mx in range(x0 + w // 2 - max(1, w // 8), x0 + w // 2 + max(1, w // 8)):
        _put(grid, mx, mouth_y, "#2a2230")


def _hat_details(grid, face, x0, y0, w, h, colors, accents) -> None:
    if face == "front":
        for x in range(x0, x0 + w):
            _put(grid, x, y0 + h - 1, colors[0])
            _put(grid, x, y0 + h - 2, accents[1])
        for x in range(x0 + w // 3, x0 + w - w // 3):
            _put(grid, x, y0 + h - 3, accents[-1])
    elif face == "top":
        cx, cy = x0 + w / 2.0, y0 + h / 2.0
        for y in range(y0, y0 + h):
            for x in range(x0, x0 + w):
                if math.hypot(x - cx, y - cy) < min(w, h) / 4:
                    _put(grid, x, y, accents[-1])
    elif face in ("right", "left"):
        for y in range(y0, y0 + h):
            _put(grid, x0, y, colors[0])
            _put(grid, x0 + w - 1, y, accents[1])


def _body_details(grid, x0, y0, w, h, colors, accents) -> None:
    for x in range(x0, x0 + w):
        _put(grid, x, y0, colors[0])
        _put(grid, x, y0 + 1, accents[1])
        _put(grid, x, y0 + h - 2, accents[1])
        _put(grid, x, y0 + h - 1, colors[0])
    cx = x0 + w // 2
    _put(grid, cx, y0 + h // 2, accents[-1])
    _put(grid, cx - 2, y0 + h // 2, accents[1])
    _put(grid, cx + 2, y0 + h // 2, accents[1])


def _cuff_details(grid, x0, y0, w, h, colors, accents) -> None:
    for x in range(x0, x0 + w):
        _put(grid, x, y0 + h - 1, colors[0])
        _put(grid, x, y0 + h - 2, accents[1])


def _boot_details(grid, x0, y0, w, h, colors) -> None:
    for x in range(x0, x0 + w):
        _put(grid, x, y0 + h - 1, colors[0])
        _put(grid, x, y0 + h - 2, colors[1])


# ----------------------------------------------------------------------
# validation / utilities
# ----------------------------------------------------------------------

def validate(path: Path, *, expected_size: tuple[int, int] | None = None, opaque: bool = False,
             max_colors: int = 128, name: str = "") -> dict:
    Image, _ = _pil()
    target = Path(path)
    issues: list[dict[str, str]] = []

    def add(level: str, message: str, suggestion: str = "") -> None:
        issues.append({"level": level, "message": message, "suggestion": suggestion})

    if not target.is_file():
        add("error", f"贴图不存在：{target}")
        return {"ok": False, "issues": issues, "size": None, "colors": 0}
    if target.suffix.lower() != ".png":
        add("warning", "贴图不是 PNG 格式")
    try:
        image = Image.open(target).convert("RGBA")
    except Exception as exc:
        add("error", f"无法读取贴图：{exc}")
        return {"ok": False, "issues": issues, "size": None, "colors": 0}

    width, height = image.size
    if width not in POWER_OF_TWO or height not in POWER_OF_TWO:
        add("warning", f"尺寸 {width}x{height} 不是 16/32/64/128，缩放后会糊")
    if width != height:
        add("warning", f"贴图不是正方形：{width}x{height}")
    if expected_size and (width, height) != tuple(expected_size):
        add("warning", f"尺寸 {width}x{height} 与声明 {expected_size} 不一致")
    if width > 128 or height > 128:
        add("warning", "贴图大于 128px，不符合像素风规范")

    pixels = list(image.getdata())
    colors = {item for item in pixels}
    opaque_pixels = [item for item in pixels if item[3] > 0]
    if len(colors) <= 1:
        add("error", "贴图只有一种颜色（或全透明），没有细节")
    elif len([item for item in colors if item[3] > 0]) <= 2:
        add("warning", "有效颜色不超过 2 种，缺少明暗层次")
    if len(colors) > max_colors:
        add("info", f"颜色数 {len(colors)} 超过 {max_colors}，可能不是像素风")

    # deterministic "detail" metric: share of adjacent opaque pixels with a
    # different colour.  Flat bands score near zero and look like plastic.
    edge = 0
    pairs = 0
    for y in range(height):
        for x in range(width - 1):
            a = pixels[y * width + x]
            b = pixels[y * width + x + 1]
            if a[3] and b[3]:
                pairs += 1
                if a[:3] != b[:3]:
                    edge += 1
    for y in range(height - 1):
        for x in range(width):
            a = pixels[y * width + x]
            b = pixels[(y + 1) * width + x]
            if a[3] and b[3]:
                pairs += 1
                if a[:3] != b[:3]:
                    edge += 1
    edge_ratio = edge / max(1, pairs)
    if pairs and edge_ratio < 0.06:
        add("warning", f"贴图颜色过渡过于平坦（细节率 {edge_ratio:.2f}），看起来会像纯色块",
            "换用 stone/dirt/planks/ore 等结构化样式，或提高 base/accent 的明度差")

    # A tile can have many colours and still be unreadable if they all sit in
    # the same narrow brightness band (the old obsidian was the extreme case).
    luma = [0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]
            for pixel in opaque_pixels]
    if luma:
        spread = max(luma) - min(luma)
        if spread < 18:
            add("warning", f"明暗差只有 {spread:.0f}/255，模型上会糊成一块",
                "提高 base 与 accent 的亮度差，或改用带高光/阴影的结构化样式")

    if opaque:
        translucent = sum(1 for item in pixels if item[3] != 255)
        if translucent:
            add("warning", f"方块贴图包含 {translucent} 个半透明/透明像素")
    else:
        if all(item[3] == 0 for item in pixels):
            add("error", "贴图完全透明")
    name = name or target.name
    if name != name.lower() or " " in name:
        add("warning", "文件名包含大写或空格，资源路径会失配")

    return {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": issues,
        "size": [width, height],
        "colors": len(colors),
        "opaque_pixels": len(opaque_pixels),
        "edge_ratio": round(edge_ratio, 4),
        "transparent": any(item[3] < 255 for item in pixels),
    }


def palette_of(path: Path, count: int = 8) -> list[str]:
    Image, _ = _pil()
    image = Image.open(path).convert("RGBA")
    quantized = image.convert("RGB").quantize(colors=count)
    raw = quantized.getpalette() or []
    used = sorted(set(quantized.getdata()))
    return [_hex(raw[index * 3:index * 3 + 3]) for index in used if len(raw) >= index * 3 + 3]


def compare(left: Path, right: Path) -> dict:
    """Mean absolute difference, useful for screenshot regression."""
    Image, _ = _pil()
    a = Image.open(left).convert("RGB")
    b = Image.open(right).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size)
    diff = 0
    total = 0
    for pixel_a, pixel_b in zip(a.getdata(), b.getdata()):
        diff += sum(abs(x - y) for x, y in zip(pixel_a, pixel_b))
        total += 3 * 255
    ratio = diff / max(1, total)
    return {"difference": round(ratio, 6), "similar": ratio < 0.02, "size": list(a.size)}


def resize(path: Path, size: tuple[int, int], out: Path | None = None) -> Path:
    Image, _ = _pil()
    image = Image.open(path).convert("RGBA").resize(size, Image.Resampling.NEAREST)
    target = Path(out) if out else Path(path)
    image.save(target, format="PNG", optimize=True)
    return target


def spec_summary(spec: TextureSpec) -> dict:
    return spec.to_dict()


# ----------------------------------------------------------------------
def _rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    r, g, b = _rgb(color)
    return r, g, b, alpha


def _blend(left: str, right: str, ratio: float) -> str:
    a = _rgb(left)
    b = _rgb(right)
    return _hex(tuple(a[index] + (b[index] - a[index]) * ratio for index in range(3)))