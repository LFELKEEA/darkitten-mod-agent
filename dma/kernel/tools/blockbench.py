"""Blockbench model authoring and export (P1-06 / TOOL-13).

The agent should never hand-write ``elements/faces/uv`` JSON: it builds a
``.bbmodel`` (the same format Blockbench saves), validates it, and exports a
Java model plus the texture reference.  The source file stays editable by
humans, which is the whole point of the template-first workflow.
"""

from __future__ import annotations

import copy
import json
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

    def to_element(self, texture_size: tuple[int, int]) -> dict[str, Any]:
        uv = self.uv or (0, 0)
        u, v = uv
        w = max(1, int(texture_size[0]))
        h = max(1, int(texture_size[1]))
        faces = {
            face: {"uv": [u, v, u + w, v + h], "texture": self.texture}
            for face in FACES
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
                "box_uv": False,
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
    return create_bbmodel(name, [
        cube(f"{name}_a", (0, 0, 7.5), (16, 16, 8.5), texture=texture, uv=(0, 0)),
        cube(f"{name}_b", (7.5, 0, 0), (8.5, 16, 16), texture=texture, uv=(0, 0)),
    ])


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
        for face in FACES:
            value = faces.get(face)
            if isinstance(value, dict):
                texture = str(value.get("texture") or texture)
                if isinstance(value.get("uv"), list) and len(value["uv"]) >= 2:
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
        if item.texture != DEFAULT_TEXTURE and not re.fullmatch(r"\d+", item.texture):
            if ":" not in item.texture and not item.texture.startswith("#"):
                issues.append(f"{item.name} 的 texture 索引不是数字或资源名：{item.texture}")
    if model.texture_size[0] <= 0 or model.texture_size[1] <= 0:
        issues.append("texture_size 必须为正")
    if model.texture_size != model.resolution:
        issues.append("texture_size 与 resolution 不一致，导出后 UV 会错位")
    return issues


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
    data = export_java_model(model, namespace=namespace)
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


def preview(
    model: BlockbenchModel,
    path: Path,
    *,
    size: tuple[int, int] = (320, 320),
    background: tuple[int, int, int, int] = (26, 26, 32, 255),
    base: tuple[int, int, int] = (168, 168, 168),
) -> Path:
    """Render an isometric preview PNG (P1-06 / TOOL-13).

    Needs Pillow; raises ``RuntimeError`` when it is unavailable so callers can
    fall back to exporting the model and letting Blockbench render it.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # noqa: BLE001 - optional dependency
        raise RuntimeError("预览图需要 Pillow：pip install pillow") from exc
    target = Path(path)
    import math

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

    for item in cubes:
        x0, y0, z0 = item.from_
        x1, y1, z1 = item.to
        top = [
            project((x0, y1, z0)), project((x1, y1, z0)),
            project((x1, y1, z1)), project((x0, y1, z1)),
        ]
        east = [
            project((x1, y0, z0)), project((x1, y1, z0)),
            project((x1, y1, z1)), project((x1, y0, z1)),
        ]
        south = [
            project((x0, y0, z1)), project((x1, y0, z1)),
            project((x1, y1, z1)), project((x0, y1, z1)),
        ]
        for polygon, factor in ((south, 0.68), (east, 0.82), (top, 1.0)):
            draw.polygon([screen(point) for point in polygon], fill=shade(base, factor))

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
