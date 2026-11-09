"""Structured pixel texture generation and validation (P1-08 / TOOL-14).

Textures are generated from a declared palette and pattern, never from a
free-form image model.  That keeps them deterministic, tiny, and reviewable
(16x16 / 32x32, limited palette, no accidental gradients).
"""

from __future__ import annotations

import math
import random
import re
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


def shade(color: str, factor: float) -> str:
    r, g, b = _rgb(color)
    return _hex((r * factor, g * factor, b * factor))


def palette(base: str, count: int = 5) -> list[str]:
    """A compact, ordered shade ramp derived from one base colour."""
    count = max(2, count)
    steps = [0.55 + (1.25 - 0.55) * index / (count - 1) for index in range(count)]
    return [shade(base, step) for step in steps]


def generate(path: Path, spec: TextureSpec | None = None, **overrides) -> Path:
    spec = spec or TextureSpec(**overrides)
    Image, ImageDraw = _pil()
    width, height = spec.size
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rng = random.Random(spec.seed or 1)
    colors = palette(spec.base, 5)
    accent = spec.accent

    if spec.style == "flat":
        draw.rectangle([0, 0, width - 1, height - 1], fill=_rgba(spec.base, 255))
    elif spec.style == "gradient":
        for y in range(height):
            ratio = y / max(1, height - 1)
            color = _blend(spec.base, accent, ratio)
            draw.line([(0, y), (width - 1, y)], fill=_rgba(color, 255))
    elif spec.style == "bricks":
        _bricks(draw, width, height, colors, rng, mortar=accent)
    elif spec.style == "ore":
        _noise(draw, width, height, colors, rng)
        _ore(draw, width, height, accent, rng)
    elif spec.style == "planks":
        _planks(draw, width, height, colors, rng, line=accent)
    elif spec.style == "metal":
        _metal(draw, width, height, colors, rng)
    else:  # noise
        _noise(draw, width, height, colors, rng)

    if not spec.transparent:
        background = Image.new("RGBA", (width, height), _rgba(spec.base, 255))
        background.alpha_composite(image)
        image = background

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=True)
    return target


def generate_item(path: Path, kind: str = "ingot", *, base: str = "#c9c9c9", size: int = 16, seed: int = 0) -> Path:
    """Draw a simple readable item silhouette on a transparent background."""
    Image, ImageDraw = _pil()
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    light = shade(base, 1.18)
    dark = shade(base, 0.62)

    if kind == "ingot":
        draw.polygon([(3, 10), (5, 6), (12, 6), (14, 10), (12, 13), (5, 13)], fill=_rgba(base, 255), outline=_rgba(dark, 255))
        draw.line([(5, 7), (12, 7)], fill=_rgba(light, 255))
    elif kind == "gem":
        draw.polygon([(8, 2), (14, 8), (8, 14), (2, 8)], fill=_rgba(base, 255), outline=_rgba(dark, 255))
        draw.line([(8, 3), (8, 13)], fill=_rgba(light, 255))
    elif kind == "stick":
        draw.line([(4, 13), (12, 3)], fill=_rgba(base, 255), width=2)
        draw.line([(4, 13), (12, 3)], fill=_rgba(light, 255), width=1)
    elif kind == "sword":
        draw.line([(4, 13), (12, 4)], fill=_rgba(base, 255), width=2)
        draw.line([(3, 11), (6, 14)], fill=_rgba(shade(base, 0.5), 255), width=2)
    elif kind == "dust":
        rng = random.Random(seed or 1)
        for _ in range(18):
            x = rng.randint(3, 12)
            y = rng.randint(3, 12)
            draw.point((x, y), fill=_rgba(rng.choice(palette(base, 4)), 255))
    else:
        draw.rectangle([4, 4, 11, 11], fill=_rgba(base, 255), outline=_rgba(dark, 255))

    image.save(Path(path), format="PNG", optimize=True)
    return Path(path)


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
    if len(colors) <= 1:
        add("error", "贴图只有一种颜色（或全透明），没有细节")
    if len(colors) > max_colors:
        add("info", f"颜色数 {len(colors)} 超过 {max_colors}，可能不是像素风")
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


def _noise(draw, width: int, height: int, colors: list[str], rng: random.Random) -> None:
    for y in range(height):
        for x in range(width):
            value = rng.choice(colors)
            draw.point((x, y), fill=_rgba(value, 255))


def _bricks(draw, width: int, height: int, colors: list[str], rng: random.Random, mortar: str) -> None:
    for y in range(height):
        offset = 0 if (y // 4) % 2 == 0 else 4
        for x in range(width):
            if y % 4 == 0 or (x + offset) % 8 == 0:
                draw.point((x, y), fill=_rgba(mortar, 255))
            else:
                draw.point((x, y), fill=_rgba(rng.choice(colors), 255))


def _planks(draw, width: int, height: int, colors: list[str], rng: random.Random, line: str) -> None:
    for y in range(height):
        for x in range(width):
            if y % 4 == 0:
                draw.point((x, y), fill=_rgba(line, 255))
            else:
                draw.point((x, y), fill=_rgba(rng.choice(colors), 255))


def _metal(draw, width: int, height: int, colors: list[str], rng: random.Random) -> None:
    for y in range(height):
        for x in range(width):
            index = min(len(colors) - 1, int((math.sin(x * 0.7) + math.cos(y * 0.5) + 2) / 4 * len(colors)))
            value = colors[index]
            if rng.random() < 0.06:
                value = colors[-1]
            draw.point((x, y), fill=_rgba(value, 255))


def _ore(draw, width: int, height: int, accent: str, rng: random.Random) -> None:
    for _ in range(max(3, width // 5)):
        x = rng.randint(2, max(2, width - 3))
        y = rng.randint(2, max(2, height - 3))
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            if rng.random() < 0.8:
                draw.point((x + dx, y + dy), fill=_rgba(accent, 255))
