"""dma/arttools.py — 模组资源(纹理/模型/方块状态/配方/战利品/lang)的纯 Python 生成与校验工具库。

零第三方依赖:只使用标准库(zlib / struct / json / re / pathlib)。供 ``agent.py``
以工具形式接线给 AI 模型调用,也可被任何脚本直接 import。

与 v2 现有实现的边界:
- ``dma/agent.py`` 里的 ``_encode_pixel_png`` / ``_parse_pixel_color`` 只服务内部
  ``pixel_art`` 工具,且不支持 scale / outline / 元组与整数颜色;本模块的
  ``pixel_png`` / ``parse_color`` 是增强后的独立公开接口。
- ``dma/studio_engine/assets.py`` 里的 ``_recipe_body`` / ``_loot_for`` 与
  ``generate_assets`` 绑定 ModSpec 流程(依赖 mc 版本分支与 modid 上下文);本模块
  的 ``recipe_json`` / ``loot_table_json`` 是独立、按 1.20.1 规范直出的通用函数。
- ``dma/kernel/tools/texture.py`` 依赖 Pillow(第三方);本模块完全用标准库手写 PNG。
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

__all__ = [
    "TEMPLATE_NAMES",
    "TEMPLATE_PALETTE",
    "parse_color",
    "pixel_png",
    "template_rows",
    "model_json",
    "blockstate_json",
    "lang_json",
    "recipe_json",
    "loot_table_json",
    "validate_resources",
]

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

#: 内置模板的可用名称(与 ``template_rows`` 一一对应)。
TEMPLATE_NAMES: list[str] = [
    "sword", "pickaxe", "axe", "shovel", "hoe", "ingot",
    "gem", "dust", "stick", "block", "torch", "bowl",
]

#: 模板使用的 5 个调色板字符的默认 RGBA 配色(可被调用方覆盖)。
#: ``.`` 透明、``o`` 描边/深色、``a`` 主体、``b`` 高光、``h`` 木柄。
TEMPLATE_PALETTE: dict[str, tuple[int, int, int, int]] = {
    ".": (0, 0, 0, 0),
    "o": (30, 30, 34, 255),
    "a": (126, 130, 140, 255),
    "b": (200, 204, 212, 255),
    "h": (122, 82, 46, 255),
}


# --------------------------------------------------------------------------- #
# 颜色解析
# --------------------------------------------------------------------------- #

def parse_color(value: Any) -> tuple[int, int, int, int]:
    """把多种颜色表示解析成 ``(r, g, b, a)`` 元组,每分量 0..255。

    支持的输入:
    - ``'#rrggbb'`` / ``'#rrggbbaa'`` / ``'#rgb'`` 十六进制字符串(大小写均可)。
    - ``(r, g, b)`` 或 ``(r, g, b, a)`` 元组/列表,分量 0..255。
    - ``int``,按 ``0xRRGGBB`` 解释(alpha 默认 255)。
    - ``None``、空串、``'transparent'`` / ``'none'`` → ``(0, 0, 0, 0)`` 透明。

    非法输入抛出 :class:`ValueError`。
    """
    if value is None:
        return (0, 0, 0, 0)

    # 整数 0xRRGGBB。
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value <= 0xFFFFFF:
            raise ValueError(f"整数颜色越界:{value}(应为 0..0xFFFFFF)")
        return (value >> 16 & 0xFF, value >> 8 & 0xFF, value & 0xFF, 0xFF)

    # 元组 / 列表 (r, g, b[, a])。
    if isinstance(value, (tuple, list)):
        if len(value) not in (3, 4):
            raise ValueError(f"颜色元组应为 (r,g,b) 或 (r,g,b,a),收到 {len(value)} 项")
        channels: list[int] = []
        for component in value:
            if isinstance(component, bool) or not isinstance(component, int):
                raise ValueError(f"颜色分量必须是整数,收到 {component!r}")
            if not 0 <= component <= 255:
                raise ValueError(f"颜色分量越界:{component}(应为 0..255)")
            channels.append(component)
        if len(channels) == 3:
            channels.append(255)
        return tuple(channels)  # type: ignore[return-value]

    # 字符串。
    if not isinstance(value, str):
        raise ValueError(f"不支持的配色类型:{type(value).__name__}")

    text = value.strip().lower()
    if text in {"", "transparent", "none"}:
        return (0, 0, 0, 0)
    if text.startswith("#"):
        text = text[1:]
    # 简写 #rgb。
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    # 缺 alpha 时补不透明。
    if len(text) == 6:
        text += "ff"
    if len(text) != 8 or not re.fullmatch(r"[0-9a-f]{8}", text):
        raise ValueError(f"颜色格式不对:{value}(用 #RRGGBB / #RRGGBBAA / #RGB)")
    return (
        int(text[0:2], 16),
        int(text[2:4], 16),
        int(text[4:6], 16),
        int(text[6:8], 16),
    )


# --------------------------------------------------------------------------- #
# PNG 编码(纯标准库)
# --------------------------------------------------------------------------- #

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    """构造一个带长度与 CRC 校验的 PNG chunk。"""
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _encode_rgba_png(width: int, height: int, pixels: list[list[tuple[int, int, int, int]]]) -> bytes:
    """把 ``pixels``(行优先的 RGBA 网格)编码成 RGBA 无损 PNG 字节。"""
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # 每行前缀一个 filter type 0(None)。
        for r, g, b, a in row:
            raw.extend((r, g, b, a))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8bit / RGBA。
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + _png_chunk(b"IEND", b"")
    )


def _read_png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 字节读回 (width, height)。非法 PNG 抛 ValueError。"""
    if len(data) < 33 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是合法的 PNG 文件")
    if data[12:16] != b"IHDR":
        raise ValueError("PNG 缺少 IHDR 块")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def pixel_png(
    palette: dict[str, tuple[int, int, int, int]],
    rows: list[str],
    scale: int = 1,
    outline: tuple[int, int, int, int] | None = None,
) -> tuple[bytes, int, int]:
    """把字符画(``rows``)按 ``palette`` 上色,生成 RGBA PNG,返回 ``(bytes, w, h)``。

    - ``palette``:字符 → RGBA 元组。键即 ``rows`` 里出现的字符。
    - ``rows``:等长字符串列表,每字符对应一个像素。
    - ``scale``:最近邻放大倍数,1..32。
    - ``outline``:若给出,则给所有不透明像素的 8 连通邻域中透明的格子描边,
      描边在放大之前进行(即描边厚度 = ``scale`` 像素)。
    """
    if not isinstance(rows, list) or not rows:
        raise ValueError("rows 不能为空列表")
    if not 1 <= scale <= 32:
        raise ValueError(f"scale 必须在 1..32,收到 {scale}")

    height = len(rows)
    width = len(rows[0])
    if width < 1 or height < 1 or width > 512 or height > 512:
        raise ValueError("纹理尺寸必须在 1x1 到 512x512 之间")

    colors: dict[str, tuple[int, int, int, int]] = {
        str(key): parse_color(color) for key, color in palette.items()
    }

    # 基础网格(原生分辨率)。
    grid: list[list[tuple[int, int, int, int]]] = []
    for index, row in enumerate(rows):
        line = str(row)
        if len(line) != width:
            raise ValueError(f"第 {index + 1} 行长度 {len(line)} != {width}")
        built: list[tuple[int, int, int, int]] = []
        for char in line:
            if char not in colors:
                raise ValueError(f"第 {index + 1} 行出现未定义字符:{char!r}")
            built.append(colors[char])
        grid.append(built)

    # 描边:透明格且 8 连通邻域存在不透明格时,填入 outline 颜色。
    if outline is not None:
        edge = parse_color(outline)
        if edge[3] == 0:  # 描边不可见时强制不透明,避免静默失效。
            edge = (edge[0], edge[1], edge[2], 255)
        outlined = [row[:] for row in grid]
        for y in range(height):
            for x in range(width):
                if grid[y][x][3] > 0:
                    continue
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < width and 0 <= ny < height and grid[ny][nx][3] > 0:
                            outlined[y][x] = edge
                            break
                    else:
                        continue
                    break
        grid = outlined

    # 最近邻放大。
    scaled: list[list[tuple[int, int, int, int]]] = []
    for row in grid:
        out_row: list[tuple[int, int, int, int]] = []
        for px in row:
            out_row.extend([px] * scale)
        scaled.extend([out_row] * scale)

    data = _encode_rgba_png(width * scale, height * scale, scaled)
    return data, width * scale, height * scale


# --------------------------------------------------------------------------- #
# 内置 16x16 像素模板
# --------------------------------------------------------------------------- #

# 每个模板 16 行 x 16 列,字符集:{'.', 'o', 'a', 'b', 'h'}。
_TEMPLATES: dict[str, list[str]] = {
    "sword": [
        "....oooooooo....",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "....oabbbbao....",
        ".....oabbao.....",
        "......oao.......",
        "......oho.......",
        "......oho.......",
        ".....oooooo.....",
        "......ooho......",
        "......ooho......",
        "......ooho......",
        "......oooo......",
    ],
    "pickaxe": [
        "....oo....oo....",
        "...oabbo..oabo..",
        "...oabbo..oabo..",
        "....oo....oo....",
        ".....oabbo......",
        ".....oabbo......",
        "......oo........",
        "......oh........",
        ".....oahh.......",
        ".....oahh.......",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oo........",
    ],
    "axe": [
        ".....oooooo.....",
        "....oabbbbao....",
        "....oabbbbao....",
        "....oabbbbao....",
        ".....oabbao.....",
        "......oo........",
        "......oh........",
        ".....oahh.......",
        ".....oahh.......",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oo........",
    ],
    "shovel": [
        "......oooo......",
        ".....oabbao.....",
        ".....oabbao.....",
        ".....oabbao.....",
        "......oo........",
        "......oh........",
        "......oh........",
        ".....oahh.......",
        ".....oahh.......",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oh........",
        "......oo........",
    ],
    "hoe": [
        "....oooooo......",
        "...oabbbbao.....",
        "...oabbbbao.....",
        "....oabbao......",
        ".....oo.........",
        ".....oh.........",
        ".....oh.........",
        ".....oh.........",
        ".....oh.........",
        "....oahh........",
        "....oahh........",
        ".....oh.........",
        ".....oh.........",
        ".....oh.........",
        ".....oh.........",
        ".....oo.........",
    ],
    "ingot": [
        "................",
        "................",
        "..oooooooooo....",
        ".oabbbbbbbbao...",
        ".obaaaaaaaabo...",
        ".obabbbbbbabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".obaaaaaaaabo...",
        ".oabbbbbbbbao...",
        "..oooooooooo....",
        "................",
    ],
    "gem": [
        "................",
        "......oooo......",
        ".....oabbao.....",
        "....oabbbbao....",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "...oabbbbbbao...",
        "....oabbbbao....",
        "....oabbbbao....",
        ".....oabbao.....",
        ".....oabbao.....",
        "......oao.......",
        "......oao.......",
        ".......oo.......",
        "................",
        "................",
    ],
    "dust": [
        "................",
        "................",
        "................",
        "......oo........",
        ".....oabbo......",
        "....oabbao......",
        ".....oabbo......",
        "......oo........",
        "................",
        "........oo......",
        ".......oabbo....",
        "........oao.....",
        ".........oo.....",
        "................",
        "................",
        "................",
    ],
    "stick": [
        "................",
        "................",
        "......oo........",
        ".....oahho......",
        ".....oahho......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        ".....oahho......",
        ".....oahho......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oo........",
        "................",
    ],
    "block": [
        "................",
        ".ooooooooooooo..",
        "obbbbbbbbbbbbao.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obaaaaaaaaaaabo.",
        "obbbbbbbbbbbbao.",
        ".ooooooooooooo..",
        "................",
    ],
    "torch": [
        "......oooo......",
        ".....oabbao.....",
        ".....oabbao.....",
        ".....oabbao.....",
        ".....oabbao.....",
        "......oao.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        "......oho.......",
        ".....ohho.......",
        ".....oooo.......",
    ],
    "bowl": [
        "................",
        "................",
        "................",
        "................",
        "................",
        "................",
        "....oooooooo....",
        "...oabbbbbbao...",
        "..oabbbbbbbbao..",
        "..oabbbbbbbbao..",
        "...oabbbbbbao...",
        "....oooooooo....",
        ".....ohhhho.....",
        "......oooo......",
        "................",
        "................",
    ],
}


def template_rows(name: str) -> dict[str, list[str]]:
    """返回内置 16x16 像素模板 ``{"rows": [16 个等长字符串]}``。

    模板使用 5 个字符:``.`` 透明、``o`` 描边/深色、``a`` 主体、``b`` 高光、
    ``h`` 木柄。配合 :data:`TEMPLATE_PALETTE` 即可直接喂给 :func:`pixel_png`:
    ``pixel_png(TEMPLATE_PALETTE, template_rows("sword")["rows"])``。

    不认识的 ``name`` 抛 :class:`ValueError` 并列出所有可用名
    (见 :data:`TEMPLATE_NAMES`)。
    """
    key = str(name).strip().lower()
    if key not in _TEMPLATES:
        raise ValueError(
            f"未知模板:{name!r},可用:{', '.join(TEMPLATE_NAMES)}"
        )
    return {"rows": list(_TEMPLATES[key])}


# --------------------------------------------------------------------------- #
# 模型 / 方块状态 / lang
# --------------------------------------------------------------------------- #

def model_json(
    kind: str,
    parent: str,
    textures: dict[str, str],
    elements: list[dict] | None = None,
    display: dict | None = None,
) -> tuple[str, list[str]]:
    """生成 Minecraft JSON 模型文本,返回 ``(文本, 警告列表)``。

    - ``kind``:``'block'`` 或 ``'item'``(不合法只报警告,不影响输出结构)。
    - ``parent``:父模型,如 ``'minecraft:block/cube_all'``、``'minecraft:item/generated'``。
    - ``textures``:贴图变量 → 引用,如 ``{'all': 'modid:block/foo'}``。
    - ``elements``:可选,原样写入 ``elements`` 数组。
    - ``display``:可选,原样写入 ``display`` 对象。

    输出 ``ensure_ascii=False``、``indent=2``,可直接写入 ``models/<kind>/<name>.json``。
    """
    warnings: list[str] = []
    if kind not in {"block", "item"}:
        warnings.append(f"kind 应为 'block' 或 'item',收到 {kind!r}")
    if not parent:
        warnings.append("parent 为空,模型可能无法解析")
    if not textures:
        warnings.append("textures 为空")

    obj: dict[str, Any] = {"parent": parent, "textures": textures}
    if elements:
        obj["elements"] = elements
    if display:
        obj["display"] = display

    for key, value in textures.items():
        if isinstance(value, str) and value and not value.startswith("#") and ":" not in value:
            warnings.append(f"textures[{key!r}] = {value!r} 缺少命名空间(建议 'ns:block/...')")
    if parent and ":" not in parent:
        warnings.append(f"parent {parent!r} 缺少命名空间")

    return json.dumps(obj, ensure_ascii=False, indent=2), warnings


def blockstate_json(
    variants: dict[str, str | dict] | None = None,
    multipart: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """生成方块状态 JSON,返回 ``(文本, 警告列表)``。

    - ``variants``:变体名 → 模型字符串或模型对象。字符串会自动包装成
      ``{"model": ...}``(Minecraft 要求变体值必须是对象)。
    - ``multipart``:多部件定义列表,原样写入。

    二者同时给出会报警告(Minecraft 一般只允许其一);都为空也报警告。
    """
    warnings: list[str] = []
    normalized: dict[str, Any] | None = None
    if variants:
        normalized = {}
        for key, value in variants.items():
            if isinstance(value, str):
                normalized[key] = {"model": value}
            else:
                normalized[key] = value

    if variants and multipart:
        warnings.append("variants 与 multipart 同时给出,优先输出 multipart")
    if not variants and not multipart:
        warnings.append("variants 与 multipart 均为空,方块状态无内容")

    obj: dict[str, Any] = {}
    if multipart:
        obj["multipart"] = multipart
    elif normalized:
        obj["variants"] = normalized
    return json.dumps(obj, ensure_ascii=False, indent=2), warnings


def lang_json(entries: dict[str, str]) -> str:
    """把翻译条目字典序列化为 lang JSON(``ensure_ascii=False``, ``indent=2``)。

    键形如 ``'item.modid.foo'``,值为该语言文本。
    """
    return json.dumps(entries, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 配方(1.20.1)
# --------------------------------------------------------------------------- #

# kind → 完整配方 type。
_RECIPE_TYPES: dict[str, str] = {
    "shaped": "minecraft:crafting_shaped",
    "shapeless": "minecraft:crafting_shapeless",
    "smelting": "minecraft:smelting",
    "blasting": "minecraft:blasting",
    "smoking": "minecraft:smoking",
    "campfire_cooking": "minecraft:campfire_cooking",
    "stonecutting": "minecraft:stonecutting",
    "smithing_transform": "minecraft:smithing_transform",
}

# 每种 kind 的必填字段(缺失会报 warning)。
_RECIPE_REQUIRED: dict[str, list[str]] = {
    "shaped": ["pattern", "key", "result"],
    "shapeless": ["ingredients", "result"],
    "smelting": ["ingredient", "result"],
    "blasting": ["ingredient", "result"],
    "smoking": ["ingredient", "result"],
    "campfire_cooking": ["ingredient", "result"],
    "stonecutting": ["ingredient", "result"],
    "smithing_transform": ["template", "base", "addition", "result"],
}


def recipe_json(kind: str, **fields: Any) -> tuple[str, list[str]]:
    """按 Minecraft 1.20.1 配方规范生成配方 JSON,返回 ``(文本, 警告列表)``。

    不套 namespace 外层(除非调用方在 ``fields`` 里显式给 ``group`` 等字段)。
    ``type`` 由 ``kind`` 自动决定:

    - ``shaped``:有序合成。必填 ``pattern``(字符串列表)、``key``(字符→物品映射)、
      ``result``;``group`` / ``category`` 可选。
    - ``shapeless``:无序合成。必填 ``ingredients``(物品列表)、``result``。
    - ``smelting`` / ``blasting`` / ``smoking`` / ``campfire_cooking``:熔炼类。
      必填 ``ingredient``(单个物品对象)、``result``;``experience`` / ``cookingtime`` 可选。
    - ``stonecutting``:切石机。必填 ``ingredient``、``result``(``count`` 在 result 里)。
    - ``smithing_transform``:锻造升级。必填 ``template`` / ``base`` / ``addition`` / ``result``。

    ``result`` 若传字符串会自动包装成 ``{"item": ...}``(1.20.1 写法);
    传字典则原样保留。未知 ``kind`` 只报警告,不会抛异常。
    """
    warnings: list[str] = []
    if kind not in _RECIPE_TYPES:
        warnings.append(f"未知配方类型:{kind!r},可用:{', '.join(_RECIPE_TYPES)}")

    body: dict[str, Any] = dict(fields)
    body.pop("type", None)  # type 由 kind 决定,忽略调用方传入的 type。
    body["type"] = _RECIPE_TYPES.get(kind, f"minecraft:{kind}")

    if kind in _RECIPE_REQUIRED:
        for required in _RECIPE_REQUIRED[kind]:
            if required not in body:
                warnings.append(f"缺少必填字段 {required!r}")

    # result 字符串 → 对象。
    if "result" in body and isinstance(body["result"], str):
        body["result"] = {"item": body["result"]}

    return json.dumps(body, ensure_ascii=False, indent=2), warnings


# --------------------------------------------------------------------------- #
# 战利品表(1.20.1)
# --------------------------------------------------------------------------- #

def loot_table_json(
    block: str | None = None,
    item: str | None = None,
    silk_touch_self: bool = False,
) -> str:
    """生成战利品表 JSON(1.20.1 格式)。

    - ``block`` 模式(给 ``block``):生成 ``minecraft:block`` 表,含两个分支——
      带 ``minecraft:silk_touch`` 条件掉落方块自身;无 silk_touch 时掉落
      ``item``(未给 ``item`` 时也掉落方块自身)。
    - ``item`` 模式(只给 ``item``):生成 ``minecraft:generic`` 单池单条目表。
    - 两者都不给抛 :class:`ValueError`。

    ``silk_touch_self`` 为 ``False``(默认)时,silk_touch 分支照常掉落方块自身;
    为 ``True`` 时表示明确用「自身 + 替代」语义(与默认行为一致,保留以显式声明)。
    """
    if not block and not item:
        raise ValueError("loot_table_json 至少需要 block 或 item 之一")

    def item_entry(name: str, *, silk_touch: bool = False) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "minecraft:item", "name": name}
        if silk_touch:
            entry["conditions"] = [{
                "condition": "minecraft:match_tool",
                "predicate": {"enchantments": [
                    {"enchantments": "minecraft:silk_touch", "levels": {"min": 1}}
                ]},
            }]
        return entry

    if block:
        alternative = (item or block) if not silk_touch_self else block
        table: dict[str, Any] = {
            "type": "minecraft:block",
            "pools": [{
                "rolls": 1,
                "entries": [{
                    "type": "minecraft:alternatives",
                    "children": [
                        item_entry(block, silk_touch=True),
                        item_entry(alternative),
                    ],
                }],
            }],
        }
    else:
        table = {
            "type": "minecraft:generic",
            "pools": [{
                "rolls": 1,
                "entries": [item_entry(item or "")],
            }],
        }
    return json.dumps(table, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# 资源校验
# --------------------------------------------------------------------------- #

_NS_KEYS = {"type", "name", "item", "id", "parent", "tag"}


def _namespace_problems(obj: Any) -> list[str]:
    """递归检查命名空间类字段(如 ``type`` / ``name`` / ``item`` / ``id``)。"""
    problems: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _NS_KEYS and isinstance(value, str):
                    if value and not value.startswith("#") and ":" not in value:
                        problems.append(f"字段 {key!r} 值 {value!r} 缺少命名空间(ns:path)")
                walk(value)
        elif isinstance(node, list):
            for element in node:
                walk(element)

    walk(obj)
    return problems


def _split_ns(ref: str, default_ns: str) -> tuple[str, str] | None:
    """把 ``ns:path`` / ``path`` 解析成 ``(namespace, path)``;``#var`` 返回 None。"""
    if not ref or ref.startswith("#"):
        return None
    if ":" in ref:
        ns, path = ref.split(":", 1)
        return ns, path
    return default_ns, ref


def _model_file(ref: str, default_ns: str) -> str | None:
    """把模型引用解析成 ``assets/<ns>/models/<path>.json`` 相对路径。"""
    parsed = _split_ns(ref, default_ns)
    if parsed is None:
        return None
    ns, path = parsed
    return f"assets/{ns}/models/{path}.json"


def _texture_file(ref: str, default_ns: str) -> str | None:
    """把贴图引用解析成 ``assets/<ns>/textures/<path>.png`` 相对路径。"""
    parsed = _split_ns(ref, default_ns)
    if parsed is None:
        return None
    ns, path = parsed
    return f"assets/{ns}/textures/{path}.png"


def _png_size(path: Path) -> tuple[int, int] | None:
    """读取 PNG 的 (width, height);失败返回 None。"""
    try:
        with open(path, "rb") as handle:
            header = handle.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", header[16:24])
    except OSError:
        return None


def validate_resources(root: str) -> str:
    """递归校验一个 ``resources`` 目录,返回中文 Markdown 报告,绝不抛异常。

    覆盖检查:
    - 每个 ``.json`` 能否解析(解析失败则跳过该文件的后续检查)。
    - blockstate 引用的 model 文件是否存在(``assets/<ns>/models/<path>.json``)。
    - model 引用的 textures 是否存在(``assets/<ns>/textures/<path>.png``)。
    - lang 键是否非空。
    - PNG 宽高是否为 16 的倍数(读 IHDR 头)。
    - loot_table / recipe 里的命名空间引用格式(``ns:path``)。

    报告按文件分类列出问题,结尾给统计。
    """
    root_path = Path(root)
    problems: list[tuple[str, str]] = []  # (相对文件, 问题描述)。
    counters = {
        "json": 0, "blockstate": 0, "model": 0, "texture": 0,
        "lang": 0, "png": 0, "namespace": 0,
    }
    json_files = 0
    png_files = 0

    def add(relative: Path, message: str, category: str) -> None:
        problems.append((relative.as_posix(), message))
        counters[category] += 1

    try:
        # 收集所有文件(避免 root 不存在时抛异常)。
        all_files = sorted(root_path.rglob("*")) if root_path.is_dir() else []
    except OSError:
        all_files = []

    for path in all_files:
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            json_files += 1
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                add(path, "无法以 UTF-8 读取", "json")
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                add(path, f"JSON 解析失败:{exc}", "json")
                continue
            try:
                relative = path.relative_to(root_path)
            except ValueError:
                relative = path

            parts = path.parts
            try:
                idx = parts.index("assets")
                namespace = parts[idx + 1]
            except (ValueError, IndexError):
                namespace = ""

            # --- blockstate → model 存在性 ---
            if "/blockstates/" in relative.as_posix():
                for block in _iter_models(data):
                    target = _model_file(block, namespace)
                    if target:
                        candidate = root_path / target
                        if not candidate.is_file():
                            add(path, f"blockstate 引用的模型不存在:{block}(期望 {target})", "blockstate")

            # --- model → texture 存在性 ---
            if "/models/" in relative.as_posix():
                textures = data.get("textures") if isinstance(data, dict) else None
                if isinstance(textures, dict):
                    for key, value in textures.items():
                        if not isinstance(value, str) or value.startswith("#"):
                            continue
                        target = _texture_file(value, namespace)
                        if target:
                            candidate = root_path / target
                            if not candidate.is_file():
                                add(path, f"model 引用的贴图不存在:{value}(期望 {target})", "texture")

            # --- lang 键非空 ---
            if "/lang/" in relative.as_posix():
                if isinstance(data, dict):
                    for key, value in data.items():
                        if not isinstance(key, str) or not key.strip():
                            add(path, "lang 存在空键", "lang")
                        if not isinstance(value, str) or not value.strip():
                            add(path, f"lang 键 {key!r} 的值为空", "lang")

            # --- 命名空间格式(recipe / loot_table) ---
            if "/recipes/" in relative.as_posix() or "/recipe/" in relative.as_posix() \
                    or "/loot_tables/" in relative.as_posix() or "/loot_table/" in relative.as_posix():
                for problem in _namespace_problems(data):
                    add(path, problem, "namespace")

        elif suffix == ".png":
            png_files += 1
            size = _png_size(path)
            if size is None:
                add(path, "无法读取 PNG 尺寸(文件损坏或非 PNG)", "png")
            else:
                width, height = size
                if width % 16 != 0 or height % 16 != 0:
                    add(path, f"PNG 尺寸 {width}x{height} 不是 16 的倍数", "png")

    # 组装报告。
    lines: list[str] = [
        "# 资源校验报告",
        "",
        f"- **目录**: {root}",
        f"- **JSON 文件**: {json_files} 个 | **PNG 文件**: {png_files} 个",
        f"- **问题总数**: {len(problems)}",
        "",
    ]

    if not problems:
        lines.append("未发现问题。")
        return "\n".join(lines)

    lines.append("## 按文件列出问题")
    lines.append("")
    by_file: dict[str, list[str]] = {}
    for relative, message in problems:
        by_file.setdefault(relative, []).append(message)
    for relative in sorted(by_file):
        lines.append(f"### {relative}")
        for message in by_file[relative]:
            lines.append(f"- {message}")
        lines.append("")

    lines.append("## 统计")
    lines.append("")
    lines.append(f"- JSON 解析失败: {counters['json']}")
    lines.append(f"- blockstate 模型缺失: {counters['blockstate']}")
    lines.append(f"- model 贴图缺失: {counters['texture']}")
    lines.append(f"- lang 键/值问题: {counters['lang']}")
    lines.append(f"- PNG 尺寸问题: {counters['png']}")
    lines.append(f"- 命名空间格式问题: {counters['namespace']}")
    return "\n".join(lines)


def _iter_models(data: Any) -> list[str]:
    """从 blockstate JSON 提取所有 model 引用(variants 与 multipart)。"""
    found: list[str] = []
    if not isinstance(data, dict):
        return found

    variants = data.get("variants")
    if isinstance(variants, dict):
        for value in variants.values():
            found.extend(_collect_model(value))
    multipart = data.get("multipart")
    if isinstance(multipart, list):
        for part in multipart:
            if isinstance(part, dict):
                found.extend(_collect_model(part.get("apply")))
    return found


def _collect_model(value: Any) -> list[str]:
    """递归提取 ``{'model': '...'}`` 或字符串里的 model 引用。"""
    out: list[str] = []
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        if isinstance(value.get("model"), str):
            out.append(value["model"])
        for child in value.values():
            if isinstance(child, (dict, list)):
                out.extend(_collect_model(child))
    elif isinstance(value, list):
        for child in value:
            out.extend(_collect_model(child))
    return out


if __name__ == "__main__":  # pragma: no cover - 仅作为手动冒烟测试入口。
    print("arttools.py 直接运行(请通过自测脚本验证)。")
