"""Blockbench model authoring and export (P1-06 / TOOL-13).

The agent should never hand-write ``elements/faces/uv`` JSON: it builds a
``.bbmodel`` (the same format Blockbench saves), validates it, and exports a
Java model plus the texture reference.  The source file stays editable by
humans, which is the whole point of the template-first workflow.
"""

from __future__ import annotations

import base64
import copy
import io
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

FACES = ("north", "east", "south", "west", "up", "down")
DEFAULT_TEXTURE = "0"


@dataclass
class Cube:
    name: str
    from_: tuple[float, float, float]
    to: tuple[float, float, float]
    texture: str = DEFAULT_TEXTURE
    uv: tuple[int, int] | None = None
    origin: tuple[float, float, float] | None = None
    rotation: tuple[float, float, float] | None = None

    def size_px(self) -> tuple[int, int, int]:
        """Cube size in texture pixels (1 model unit == 1 pixel)."""
        return tuple(
            max(1, int(round(abs(self.to[axis] - self.from_[axis]))))
            for axis in range(3)
        )  # type: ignore[return-value]

    def face_uvs(self, texture_size: tuple[int, int]) -> dict[str, tuple[int, int, int, int]]:
        """Return the UV rectangle per face.

        Without an explicit UV origin the whole texture is mapped onto every
        face, which is the normal ``cube_all`` behaviour for a single block.
        With a UV origin the standard Minecraft box-UV layout is used so a
        multi-cube model can share one atlas without stretching.
        """
        if not self.uv:
            width = max(1, int(texture_size[0]))
            height = max(1, int(texture_size[1]))
            return {face: (0, 0, width, height) for face in FACES}
        u, v = int(self.uv[0]), int(self.uv[1])
        w, h, d = self.size_px()
        return {
            "up": (u + d, v, u + d + w, v + d),
            "down": (u + d + w, v, u + d + 2 * w, v + d),
            "east": (u, v + d, u + d, v + d + h),
            "north": (u + d, v + d, u + d + w, v + d + h),
            "west": (u + d + w, v + d, u + 2 * d + w, v + d + h),
            "south": (u + 2 * d + w, v + d, u + 2 * d + 2 * w, v + d + h),
        }

    def to_element(self, texture_size: tuple[int, int]) -> dict[str, Any]:
        faces = {
            face: {"uv": list(rect), "texture": self.texture}
            for face, rect in self.face_uvs(texture_size).items()
        }
        element: dict[str, Any] = {
            "name": self.name,
            "from": list(self.from_),
            "to": list(self.to),
            "origin": list(self.origin or self.from_),
            "faces": faces,
            "type": "cube",
            "uuid": str(uuid.uuid4()),
        }
        if self.uv:
            # Keep the origin so load → edit → save keeps the same atlas layout.
            element["uv"] = [int(self.uv[0]), int(self.uv[1])]
            element["box_uv"] = True
        if self.rotation:
            element["rotation"] = list(self.rotation)
        return element


@dataclass
class BlockbenchModel:
    name: str
    cubes: list[Cube] = field(default_factory=list)
    texture_size: tuple[int, int] = (16, 16)
    resolution: tuple[int, int] = (16, 16)
    texture_name: str = ""
    author: str = ""
    model_format: str = "java_block"

    def to_dict(self) -> dict[str, Any]:
        textures: dict[str, Any] = {}
        if self.texture_name:
            textures["0"] = _data_uri(self.texture_name)
        return {
            "meta": {
                "format_version": "4.10",
                "model_format": self.model_format,
                "box_uv": any(cube.uv for cube in self.cubes),
            },
            "name": self.name,
            "model_identifier": self.name,
            "variable": "",
            "resolution": {"width": self.resolution[0], "height": self.resolution[1]},
            "elements": [cube.to_element(self.texture_size) for cube in self.cubes],
            "outliner": [cube.name for cube in self.cubes],
            "textures": textures,
            "display": {},
            "author": self.author,
        }


# ----------------------------------------------------------------------
def cube(
    name: str,
    from_: Iterable[float],
    to: Iterable[float],
    *,
    texture: str = DEFAULT_TEXTURE,
    uv: tuple[int, int] | None = None,
    origin: Iterable[float] | None = None,
    rotation: Iterable[float] | None = None,
) -> Cube:
    return Cube(
        name=name,
        from_=tuple(float(value) for value in from_),  # type: ignore[arg-type]
        to=tuple(float(value) for value in to),  # type: ignore[arg-type]
        texture=texture,
        uv=uv,
        origin=tuple(float(value) for value in origin) if origin else None,  # type: ignore[arg-type]
        rotation=tuple(float(value) for value in rotation) if rotation else None,  # type: ignore[arg-type]
    )


def create_bbmodel(
    name: str,
    cubes: Iterable[Cube] | None = None,
    *,
    texture_name: str = "",
    texture_size: tuple[int, int] = (16, 16),
    author: str = "",
) -> BlockbenchModel:
    return BlockbenchModel(
        name=name,
        cubes=list(cubes or []),
        texture_size=texture_size,
        resolution=texture_size,
        texture_name=texture_name,
        author=author,
    )


def full_block(name: str = "block", *, texture: str = DEFAULT_TEXTURE) -> BlockbenchModel:
    return create_bbmodel(name, [cube(name, (0, 0, 0), (16, 16, 16), texture=texture)])


def cross_model(name: str = "plant", *, texture: str = DEFAULT_TEXTURE) -> BlockbenchModel:
    """Two crossed planes, the usual plant/crop shape."""
    # No box UV here: a 1px-thick plane should map the whole texture on each
    # face, exactly like the vanilla ``cross`` plant model.
    return create_bbmodel(name, [
        cube(f"{name}_a", (0, 0, 7.5), (16, 16, 8.5), texture=texture),
        cube(f"{name}_b", (7.5, 0, 0), (8.5, 16, 16), texture=texture),
    ])


def model_from_spec(
    name: str,
    specs: Iterable[dict[str, Any]],
    *,
    texture: str = DEFAULT_TEXTURE,
    texture_name: str = "",
    texture_size: tuple[int, int] = (16, 16),
    author: str = "",
) -> BlockbenchModel:
    """Build a model from the ``cubes`` array the model tool accepts.

    Each entry supports ``name``, ``from``/``to`` (model units, 1 unit = 1 px)
    or ``from`` + ``size``, ``uv`` (box-UV origin), ``rotation`` and
    ``texture``.  Keeping this parser here means the agent never has to write
    ``.bbmodel`` JSON by hand.
    """
    cubes: list[Cube] = []
    for index, spec in enumerate(specs):
        if not isinstance(spec, dict):
            continue
        start = spec.get("from") or spec.get("origin") or (0, 0, 0)
        start = tuple(float(value) for value in start)
        end = spec.get("to")
        if end is None:
            size = spec.get("size") or (16, 16, 16)
            end = tuple(start[axis] + float(size[axis]) for axis in range(3))
        else:
            end = tuple(float(value) for value in end)
        raw_uv = spec.get("uv")
        uv = None
        if isinstance(raw_uv, list) and len(raw_uv) >= 2:
            uv = (int(raw_uv[0]), int(raw_uv[1]))
        rotation = spec.get("rotation")
        cubes.append(Cube(
            name=str(spec.get("name") or f"cube_{index}"),
            from_=start,  # type: ignore[arg-type]
            to=end,  # type: ignore[arg-type]
            texture=str(spec.get("texture") or texture),
            uv=uv,
            origin=tuple(float(v) for v in spec["origin"]) if spec.get("origin") else None,  # type: ignore[arg-type]
            rotation=tuple(float(v) for v in rotation) if rotation else None,  # type: ignore[arg-type]
        ))
    return create_bbmodel(
        name,
        cubes,
        texture_name=texture_name,
        texture_size=texture_size,
        author=author,
    )


def texture_data_uri(path: Path) -> str:
    """Embed a PNG as a data URI so the .bbmodel previews without side files."""
    source = Path(path)
    raw = source.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def save_bbmodel(model: BlockbenchModel, path: Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(model.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target


def load_bbmodel(path: Path) -> BlockbenchModel:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("不是有效的 .bbmodel 对象")
    resolution = raw.get("resolution") or {}
    width = int(resolution.get("width") or 16)
    height = int(resolution.get("height") or 16)
    cubes: list[Cube] = []
    for element in raw.get("elements") or []:
        if not isinstance(element, dict):
            continue
        faces = element.get("faces") or {}
        texture = DEFAULT_TEXTURE
        uv = None
        origin_uv = element.get("uv")
        if isinstance(origin_uv, list) and len(origin_uv) >= 2:
            uv = (int(origin_uv[0]), int(origin_uv[1]))
        for face in FACES:
            value = faces.get(face)
            if isinstance(value, dict):
                texture = str(value.get("texture") or texture)
                if uv is None and isinstance(value.get("uv"), list) and len(value["uv"]) >= 2:
                    uv = (int(value["uv"][0]), int(value["uv"][1]))
                break
        cubes.append(Cube(
            name=str(element.get("name") or f"cube_{len(cubes)}"),
            from_=tuple(element.get("from") or (0, 0, 0)),  # type: ignore[arg-type]
            to=tuple(element.get("to") or (16, 16, 16)),  # type: ignore[arg-type]
            texture=texture,
            uv=uv,
            origin=tuple(element["origin"]) if element.get("origin") else None,  # type: ignore[arg-type]
            rotation=tuple(element["rotation"]) if element.get("rotation") else None,  # type: ignore[arg-type]
        ))
    textures = raw.get("textures") or {}
    texture_name = ""
    if isinstance(textures, dict) and textures:
        texture_name = str(next(iter(textures.values())) or "")
    return BlockbenchModel(
        name=str(raw.get("name") or Path(path).stem),
        cubes=cubes,
        texture_size=(width, height),
        resolution=(width, height),
        texture_name=texture_name,
        author=str(raw.get("author") or ""),
    )


def validate(model: BlockbenchModel) -> list[str]:
    issues: list[str] = []
    names: set[str] = set()
    if not model.cubes:
        issues.append("模型没有 cube")
    for index, item in enumerate(model.cubes):
        if item.name in names:
            issues.append(f"cube 名称重复：{item.name}")
        names.add(item.name)
        for axis, (low, high) in enumerate(zip(item.from_, item.to)):
            if high <= low:
                issues.append(f"{item.name} 的 {'xyz'[axis]} 尺寸不是正数（{low}..{high}）")
        for value in (*item.from_, *item.to):
            if value < -64 or value > 64:
                issues.append(f"{item.name} 坐标 {value} 超出常见范围 -64..64")
                break
        if item.uv:
            u, v = item.uv
            if u < 0 or v < 0 or u >= model.texture_size[0] or v >= model.texture_size[1]:
                issues.append(f"{item.name} 的 uv 起点 ({u},{v}) 超出贴图尺寸 {model.texture_size}")
            else:
                w, h, d = item.size_px()
                right = u + 2 * d + 2 * w
                bottom = v + d + h
                if right > model.texture_size[0] or bottom > model.texture_size[1]:
                    issues.append(
                        f"{item.name} 的盒式 UV 需要到 ({right},{bottom})，"
                        f"超出贴图尺寸 {model.texture_size}；换大图集或缩小 cube"
                    )
        if item.texture != DEFAULT_TEXTURE and not re.fullmatch(r"\d+", item.texture):
            if ":" not in item.texture and not item.texture.startswith("#"):
                issues.append(f"{item.name} 的 texture 索引不是数字或资源名：{item.texture}")
        if item.rotation:
            active = [axis for axis, angle in enumerate(item.rotation) if abs(angle) > 1e-6]
            if len(active) > 1:
                issues.append(
                    f"{item.name} 的旋转同时设了多个轴 {item.rotation}；"
                    "Java 方块模型只支持单轴旋转"
                )
            for angle in item.rotation:
                if abs(angle) > 45.0001:
                    issues.append(
                        f"{item.name} 的旋转角 {angle} 超出 -45..45；Java 模型会拒绝或渲染错误"
                    )
                    break
    if model.texture_size[0] <= 0 or model.texture_size[1] <= 0:
        issues.append("texture_size 必须为正")
    if model.texture_size != model.resolution:
        issues.append("texture_size 与 resolution 不一致，导出后 UV 会错位")
    return issues


def _java_element_rotation(item: Cube) -> dict[str, Any] | None:
    """Convert Blockbench Euler degrees to a Java single-axis element rotation.

    Java block models accept ``{origin, axis, angle, rescale}`` only; a raw
    ``[x, y, z]`` rotation array makes the resource loader throw.
    """
    if not item.rotation:
        return None
    angles = [float(value) for value in item.rotation]
    axis = max(range(3), key=lambda index: abs(angles[index]))
    angle = angles[axis]
    if abs(angle) < 1e-6:
        return None
    origin = item.origin or tuple(
        (item.from_[index] + item.to[index]) / 2.0 for index in range(3)
    )
    return {
        "origin": [round(float(value), 4) for value in origin],
        "axis": "xyz"[axis],
        "angle": round(max(-45.0, min(45.0, angle)), 3),
        "rescale": False,
    }


def export_java_model(
    model: BlockbenchModel,
    *,
    namespace: str,
    texture: str | None = None,
    texture_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """Convert the model into a Minecraft Java block model JSON."""
    tex = texture or (model.texture_name or f"{namespace}:block/{model.name}")
    if ":" not in tex:
        tex = f"{namespace}:{tex}"
    size = list(texture_size or model.texture_size)
    elements = []
    for item in model.cubes:
        element = item.to_element(tuple(size))
        element.pop("name", None)
        element.pop("uuid", None)
        element.pop("type", None)
        element.pop("uv", None)
        element.pop("box_uv", None)
        element.pop("origin", None)
        rotation = _java_element_rotation(item)
        if rotation is None:
            element.pop("rotation", None)
        else:
            element["rotation"] = rotation
        elements.append(element)
    return {
        "credit": model.author or "Darkitten Mod Agent",
        "texture_size": size,
        "textures": {"0": tex, "particle": tex},
        "elements": elements,
        "display": {
            "gui": {"rotation": [30, 225, 0], "scale": [0.625, 0.625, 0.625]},
            "ground": {"translation": [0, 3, 0], "scale": [0.25, 0.25, 0.25]},
            "fixed": {"rotation": [0, 0, 0], "scale": [0.5, 0.5, 0.5]},
            "thirdperson_righthand": {"rotation": [75, 45, 0], "scale": [0.375, 0.375, 0.375]},
            "firstperson_righthand": {"rotation": [0, 45, 0], "scale": [0.4, 0.4, 0.4]},
        },
    }


def export(
    model: BlockbenchModel,
    root: Path,
    *,
    namespace: str,
    name: str | None = None,
    texture_png: Path | None = None,
    kind: str = "block",
) -> list[Path]:
    """Write model JSON (and optionally copy the texture) into a mod project."""
    root = Path(root)
    name = name or model.name
    model_path = root / "src/main/resources/assets" / namespace / "models" / kind / f"{name}.json"
    model_path.parent.mkdir(parents=True, exist_ok=True)
    # The .bbmodel embeds a data URI for previews; Java models need a resource id.
    hint = model.texture_name.strip()
    texture_ref = hint if hint and not hint.startswith("data:") and ":" in hint else None
    data = export_java_model(
        model,
        namespace=namespace,
        texture=texture_ref or f"{namespace}:{kind}/{name}",
    )
    model_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    written = [model_path]
    if texture_png:
        target = root / "src/main/resources/assets" / namespace / "textures" / kind / f"{name}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(texture_png).read_bytes())
        written.append(target)
    return written


def clone(model: BlockbenchModel) -> BlockbenchModel:
    return copy.deepcopy(model)


def _preview_texture(source: Any):
    """Load a preview texture from a path or a data URI. Returns None if absent."""
    if not source:
        return None
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001 - optional dependency
        return None
    text = str(source).strip()
    if not text:
        return None
    try:
        if text.startswith("data:"):
            _header, _, payload = text.partition(",")
            raw = base64.b64decode(payload)
            return Image.open(io.BytesIO(raw)).convert("RGBA")
        candidate = Path(text)
        if candidate.is_file():
            return Image.open(candidate).convert("RGBA")
    except Exception:  # noqa: BLE001 - broken texture should not break the preview
        return None
    return None


def _paint_textured_quad(
    image,
    texture,
    quad: list[tuple[float, float]],
    uv_rect: tuple[int, int, int, int],
    factor: float,
    fallback: tuple[int, int, int],
) -> None:
    """Rasterise a projected quad with nearest-neighbour texture sampling."""
    pixels = image.load()
    width, height = image.size
    tex = texture.load()
    tex_w, tex_h = texture.size
    u0, v0, u1, v1 = uv_rect
    if u1 <= u0 or v1 <= v0:
        u0, v0, u1, v1 = 0, 0, tex_w, tex_h
    corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]

    def blend(px: int, py: int, color: tuple[int, int, int], alpha: float) -> None:
        if alpha >= 0.999:
            pixels[px, py] = (color[0], color[1], color[2], 255)
            return
        dr, dg, db, da = pixels[px, py]
        dst_a = da / 255.0
        out_a = alpha + dst_a * (1.0 - alpha)
        if out_a <= 0.001:
            return
        inv = dst_a * (1.0 - alpha)
        pixels[px, py] = (
            int((color[0] * alpha + dr * inv) / out_a),
            int((color[1] * alpha + dg * inv) / out_a),
            int((color[2] * alpha + db * inv) / out_a),
            int(out_a * 255),
        )

    def sample(u: float, v: float) -> tuple[int, int, int, int]:
        sx = max(0, min(tex_w - 1, int(u)))
        sy = max(0, min(tex_h - 1, int(v)))
        return tex[sx, sy]

    for a, b, c in ((0, 1, 2), (0, 2, 3)):
        p0, p1, p2 = quad[a], quad[b], quad[c]
        t0, t1, t2 = corners[a], corners[b], corners[c]
        den = ((p1[1] - p2[1]) * (p0[0] - p2[0])
               + (p2[0] - p1[0]) * (p0[1] - p2[1]))
        if abs(den) < 1e-9:
            continue
        min_x = max(0, int(math.floor(min(p0[0], p1[0], p2[0]))))
        max_x = min(width - 1, int(math.ceil(max(p0[0], p1[0], p2[0]))))
        min_y = max(0, int(math.floor(min(p0[1], p1[1], p2[1]))))
        max_y = min(height - 1, int(math.ceil(max(p0[1], p1[1], p2[1]))))
        for py in range(min_y, max_y + 1):
            cy = py + 0.5
            for px in range(min_x, max_x + 1):
                cx = px + 0.5
                l0 = ((p1[1] - p2[1]) * (cx - p2[0])
                      + (p2[0] - p1[0]) * (cy - p2[1])) / den
                l1 = ((p2[1] - p0[1]) * (cx - p2[0])
                      + (p0[0] - p2[0]) * (cy - p2[1])) / den
                l2 = 1.0 - l0 - l1
                if l0 < -0.004 or l1 < -0.004 or l2 < -0.004:
                    continue
                u = l0 * t0[0] + l1 * t1[0] + l2 * t2[0]
                v = l0 * t0[1] + l1 * t1[1] + l2 * t2[1]
                r, g, b, alpha = sample(u, v)
                if alpha <= 0:
                    continue
                shade_r = max(0, min(255, int(r * factor)))
                shade_g = max(0, min(255, int(g * factor)))
                shade_b = max(0, min(255, int(b * factor)))
                blend(px, py, (shade_r, shade_g, shade_b), alpha / 255.0)


def preview(
    model: BlockbenchModel,
    path: Path,
    *,
    size: tuple[int, int] = (320, 320),
    background: tuple[int, int, int, int] = (26, 26, 32, 255),
    base: tuple[int, int, int] = (168, 168, 168),
    texture: str | Path | None = None,
) -> Path:
    """Render an isometric preview PNG (P1-06 / TOOL-13).

    When a texture is available (explicit path, or the ``texture_name`` stored
    in the model) each visible face is sampled with nearest-neighbour mapping,
    so the preview looks like the in-game model instead of a grey dummy.

    Needs Pillow; raises ``RuntimeError`` when it is unavailable so callers can
    fall back to exporting the model and letting Blockbench render it.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # noqa: BLE001 - optional dependency
        raise RuntimeError("预览图需要 Pillow：pip install pillow") from exc
    target = Path(path)

    tex_image = _preview_texture(texture if texture is not None else model.texture_name)

    cos30 = math.cos(math.radians(30))
    sin30 = math.sin(math.radians(30))

    def project(point: tuple[float, float, float]) -> tuple[float, float]:
        x, y, z = point
        return ((x - z) * cos30, (x + z) * sin30 - y)

    cubes = sorted(
        model.cubes,
        key=lambda item: (item.from_[0] + item.to[0] + item.from_[2] + item.to[2]),
    )
    if not cubes:
        raise ValueError("模型没有 cube，无法预览")
    corners = []
    for item in cubes:
        for x in (item.from_[0], item.to[0]):
            for y in (item.from_[1], item.to[1]):
                for z in (item.from_[2], item.to[2]):
                    corners.append(project((x, y, z)))
    min_x = min(item[0] for item in corners)
    max_x = max(item[0] for item in corners)
    min_y = min(item[1] for item in corners)
    max_y = max(item[1] for item in corners)
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    margin = 16
    scale = min((size[0] - margin * 2) / width, (size[1] - margin * 2) / height)

    def screen(point: tuple[float, float]) -> tuple[float, float]:
        return (
            margin + (point[0] - min_x) * scale,
            margin + (point[1] - min_y) * scale,
        )

    image = Image.new("RGBA", size, background)
    draw = ImageDraw.Draw(image)

    def shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int, int]:
        return (
            max(0, min(255, int(color[0] * factor))),
            max(0, min(255, int(color[1] * factor))),
            max(0, min(255, int(color[2] * factor))),
            255,
        )

    # Collect every visible face first, then paint far-to-near.  Per-face depth
    # (x + y + z is the view axis for this isometric projection) keeps legs and
    # skirts from poking through a tabletop.
    visible: list[tuple[float, tuple[int, int, int, int] | None, list[tuple[float, float, float]], float]] = []
    for item in cubes:
        x0, y0, z0 = item.from_
        x1, y1, z1 = item.to
        top = [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)]
        east = [(x1, y1, z0), (x1, y1, z1), (x1, y0, z1), (x1, y0, z0)]
        south = [(x1, y1, z1), (x0, y1, z1), (x0, y0, z1), (x1, y0, z1)]
        if tex_image is not None:
            tex_w, tex_h = tex_image.size
            rects = (
                item.face_uvs((tex_w, tex_h))
                if item.uv
                else {name: (0, 0, tex_w, tex_h) for name in FACES}
            )
        else:
            rects = {}
        for quad, face, factor in (
            (south, "south", 0.68),
            (east, "east", 0.84),
            (top, "up", 1.0),
        ):
            depth = sum(point[0] + point[1] + point[2] for point in quad) / 4.0
            visible.append((depth, rects.get(face), quad, factor))
    visible.sort(key=lambda entry: entry[0])
    for _depth, rect, quad, factor in visible:
        polygon = [screen(project(point)) for point in quad]
        if tex_image is not None and rect is not None:
            _paint_textured_quad(image, tex_image, polygon, rect, factor, base)
        else:
            draw.polygon(polygon, fill=shade(base, factor))

    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target)
    return target


def _data_uri(texture: str) -> str:
    value = texture.strip()
    if value.startswith("data:"):
        return value
    return value


def summary(model: BlockbenchModel) -> dict[str, Any]:
    return {
        "name": model.name,
        "cubes": len(model.cubes),
        "texture_size": list(model.texture_size),
        "bones": [item.name for item in model.cubes],
        "issues": validate(model),
    }
