"""Models, textures, sounds, animations and registry-asset checks (P1-10..36)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding


def run(ctx: ProjectContext) -> Iterable[Finding]:
    namespaces = _namespaces(ctx)
    rows: list[Finding] = []
    rows.extend(_models(ctx, namespaces))
    rows.extend(_blockstates(ctx, namespaces))
    rows.extend(_registered_assets(ctx, namespaces))
    rows.extend(_sounds(ctx, namespaces))
    rows.extend(_geckolib(ctx, namespaces))
    rows.extend(_atlases(ctx, namespaces))
    rows.extend(_java_textures(ctx, namespaces))
    rows.extend(_enchantments(ctx))
    return rows


# ----------------------------------------------------------------------
def _modid(ctx: ProjectContext) -> str:
    for name in ("gradle.properties", "build.gradle", "build.gradle.kts"):
        if not ctx.exists(name):
            continue
        match = re.search(r"(?:mod_id|archivesBaseName)\s*[=:]\s*['\"]?([\w-]+)", ctx.read_rel(name))
        if match:
            return match.group(1)
    path = ctx.resource_root() / "META-INF"
    for name in ("neoforge.mods.toml", "mods.toml"):
        if (path / name).is_file():
            match = re.search(r"modId\s*=\s*\"([^\"]+)\"", ctx.read(path / name))
            if match:
                return match.group(1)
    return ""


def _namespaces(ctx: ProjectContext) -> set[str]:
    values: set[str] = set()
    modid = _modid(ctx)
    if modid:
        values.add(modid)
    assets = ctx.resource_root() / "assets"
    if assets.is_dir():
        for directory in assets.iterdir():
            if directory.is_dir() and directory.name != "minecraft":
                values.add(directory.name)
    return values


def _safe_relid(value: str, namespace: str) -> str:
    if ":" in value:
        return value.replace(":", "/", 1)
    return f"{namespace}/{value}"


def _exists(ctx: ProjectContext, relative: str) -> bool:
    return (ctx.resource_root() / relative).is_file()


def _model_path(namespace: str, model: str) -> str:
    value = model
    if value.startswith(("#", "builtin/")):
        return ""
    if ":" in value:
        ns, _, path = value.partition(":")
    else:
        ns, path = namespace, value
    return f"assets/{ns}/models/{path}.json"


def _texture_path(namespace: str, texture: str) -> str:
    value = texture
    if value.startswith("#"):
        return ""
    if ":" in value:
        ns, _, path = value.partition(":")
    else:
        ns, path = namespace, value
    return f"assets/{ns}/textures/{path}.png"


# ----------------------------------------------------------------------
def _models(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/assets/**/models/**/*.json"):
        if not path.is_file():
            continue
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        parts = rel.split("/")
        try:
            namespace = parts[parts.index("assets") + 1]
        except (ValueError, IndexError):
            continue
        if namespace not in namespaces:
            continue
        parent = data.get("parent")
        if isinstance(parent, str) and parent:
            target = _model_path(namespace, parent)
            if target and target.startswith(f"assets/{namespace}/") and not _exists(ctx, target):
                rows.append(finding(
                    "P1-12", f"模型 parent 不存在：{parent}",
                    path=rel, severity="warning",
                    suggestion="补齐父模型，或改为 minecraft:block/...。",
                ))
        elif not data.get("elements"):
            rows.append(finding(
                "P3-40", "模型既没有 parent 也没有 elements，会渲染为空气",
                path=rel, severity="warning",
                suggestion="补 parent 或 elements；缺失资源时应回退到安全默认模型。",
            ))
        textures = data.get("textures")
        if isinstance(textures, dict):
            for key, value in textures.items():
                if not isinstance(value, str):
                    continue
                target = _texture_path(namespace, value)
                if target and target.startswith(f"assets/{namespace}/") and "${" not in value and not _exists(ctx, target):
                    rows.append(finding(
                        "P1-12", f"模型贴图不存在：{key}={value}",
                        path=rel, severity="warning",
                        suggestion="补 png 或修正路径；缺失贴图会显示紫黑格。",
                    ))
        elements = data.get("elements")
        if isinstance(elements, list):
            for index, element in enumerate(elements):
                if not isinstance(element, dict):
                    continue
                for key in ("from", "to"):
                    coord = element.get(key)
                    if isinstance(coord, list) and len(coord) != 3:
                        rows.append(finding(
                            "P1-12", f"elements[{index}].{key} 不是 3 维坐标",
                            path=rel, severity="warning",
                        ))
                faces = element.get("faces") or {}
                if isinstance(faces, dict):
                    for face, value in faces.items():
                        if not isinstance(value, dict):
                            continue
                        texture = value.get("texture")
                        if isinstance(texture, str) and texture.startswith("#"):
                            name = texture[1:]
                            if isinstance(textures, dict) and name not in textures:
                                rows.append(finding(
                                    "P1-12", f"face {face} 引用了未定义的贴图变量 {texture}",
                                    path=rel, severity="warning",
                                    suggestion="在 textures 中定义该变量。",
                                ))
                        uv = value.get("uv")
                        if isinstance(uv, list) and len(uv) not in {2, 4}:
                            rows.append(finding(
                                "P1-12", f"face {face} 的 uv 长度不合法",
                                path=rel, severity="warning",
                            ))
    return rows


def _blockstates(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/assets/**/blockstates/*.json"):
        if not path.is_file():
            continue
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        parts = rel.split("/")
        try:
            namespace = parts[parts.index("assets") + 1]
        except (ValueError, IndexError):
            continue
        if namespace not in namespaces:
            continue
        variants = data.get("variants")
        if isinstance(variants, dict):
            for key, value in variants.items():
                values = value if isinstance(value, list) else [value]
                for item in values:
                    if not isinstance(item, dict) or not item.get("model"):
                        rows.append(finding(
                            "P1-12", f"blockstate 变体 {key} 缺少 model",
                            path=rel, severity="error",
                        ))
                        continue
                    target = _model_path(namespace, str(item["model"]))
                    if target and target.startswith(f"assets/{namespace}/") and not _exists(ctx, target):
                        rows.append(finding(
                            "P1-12", f"blockstate 引用的模型不存在：{item['model']}",
                            path=rel, severity="warning",
                        ))
        multipart = data.get("multipart")
        if isinstance(multipart, list):
            for index, part in enumerate(multipart):
                if not isinstance(part, dict):
                    continue
                apply = part.get("apply")
                if apply is None:
                    rows.append(finding(
                        "P1-12", f"multipart[{index}] 缺少 apply",
                        path=rel, severity="warning",
                    ))
                    continue
                values = apply if isinstance(apply, list) else [apply]
                for item in values:
                    if isinstance(item, dict) and item.get("model"):
                        target = _model_path(namespace, str(item["model"]))
                        if target and target.startswith(f"assets/{namespace}/") and not _exists(ctx, target):
                            rows.append(finding(
                                "P1-12", f"multipart 引用的模型不存在：{item['model']}",
                                path=rel, severity="warning",
                            ))
        if not variants and not multipart:
            rows.append(finding(
                "P1-12", "blockstate 既没有 variants 也没有 multipart",
                path=rel, severity="warning",
            ))
    return rows


def _registered_assets(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    if not namespaces:
        return rows
    namespace = _modid(ctx) or sorted(namespaces)[0]
    blocks = _register_names(ctx, ("Registries.BLOCK", "ForgeRegistries.BLOCKS", "BlockRegistry"))
    items = _register_names(ctx, ("Registries.ITEM", "ForgeRegistries.ITEMS", "ItemRegistry"))
    for name in sorted(blocks):
        blockstate = f"assets/{namespace}/blockstates/{name}.json"
        if not _exists(ctx, blockstate):
            rows.append(finding(
                "P1-12", f"方块 {name} 缺少 blockstate",
                path=blockstate, severity="warning",
                suggestion="生成 blockstates/<name>.json，否则方块不可见。",
            ))
        loot_paths = (
            f"data/{namespace}/loot_table/blocks/{name}.json",
            f"data/{namespace}/loot_tables/blocks/{name}.json",
        )
        if not any(_exists(ctx, item) for item in loot_paths):
            rows.append(finding(
                "P1-30", f"方块 {name} 没有战利品表，破坏后不掉落",
                path=f"data/{namespace}/loot_table/blocks/{name}.json", severity="info",
                suggestion="补方块掉落表，或显式设置 noLootTable 并自行掉落。",
            ))
        if not (_exists(ctx, f"assets/{namespace}/models/item/{name}.json") or _exists(ctx, f"assets/{namespace}/items/{name}.json")):
            rows.append(finding(
                "P1-12", f"方块 {name} 缺少物品模型，物品栏显示异常",
                path=f"assets/{namespace}/models/item/{name}.json", severity="info",
            ))
    for name in sorted(items - blocks):
        if not (_exists(ctx, f"assets/{namespace}/models/item/{name}.json") or _exists(ctx, f"assets/{namespace}/items/{name}.json")):
            rows.append(finding(
                "P1-12", f"物品 {name} 缺少物品模型",
                path=f"assets/{namespace}/models/item/{name}.json", severity="warning",
                suggestion="添加 models/item/<name>.json 或 1.21.4+ 的 items/<name>.json。",
            ))
    for path in ctx.glob("src/main/resources/assets/**/models/item/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        if not data.get("parent") and "elements" not in data:
            rows.append(finding(
                "P2-18", "物品模型没有 parent/elements，GUI 与手持表现会异常",
                path=ctx.rel(path), severity="warning",
                suggestion="使用 item/generated 或 handheld 作为父模型。",
            ))
    return rows


def _register_names(ctx: ProjectContext, markers: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for java in ctx.java_files:
        text = java.text
        if not any(marker in text for marker in markers):
            continue
        for match in re.finditer(r"\.register\s*\(\s*\"([\w./-]+)\"", text):
            names.add(match.group(1))
    return names


def _sound_names(ctx: ProjectContext) -> set[str]:
    names: set[str] = set()
    for java in ctx.java_files:
        if "SoundEvent" not in java.text:
            continue
        for match in re.finditer(r"\.register\s*\(\s*\"([\w./-]+)\"", java.text):
            names.add(match.group(1).replace("/", "."))
    return names


def _sounds(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    namespace = _modid(ctx) or (sorted(namespaces)[0] if namespaces else "")
    path = ctx.resource_root() / "assets" / namespace / "sounds.json"
    defined: set[str] = set()
    if path.is_file():
        data = ctx.load_json(path)
        if isinstance(data, dict):
            for key, value in data.items():
                defined.add(key)
                entries = value.get("sounds") if isinstance(value, dict) else value
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if isinstance(entry, str):
                        sound = entry
                    elif isinstance(entry, dict):
                        sound = str(entry.get("name") or "")
                    else:
                        continue
                    if not sound or sound.startswith("${"):
                        continue
                    sound_ns = namespace
                    if ":" in sound:
                        sound_ns, _, sound = sound.partition(":")
                    if sound_ns != namespace:
                        continue
                    target = ctx.resource_root() / "assets" / sound_ns / "sounds" / (sound.removesuffix(".ogg") + ".ogg")
                    if not target.is_file():
                        rows.append(finding(
                            "P1-35", f"sounds.json 引用的音频不存在：{sound}",
                            path=ctx.rel(path), severity="warning",
                            suggestion="补 ogg 文件或修正路径；缺失音效会静默不播放。",
                        ))
    for name in sorted(_sound_names(ctx)):
        if name not in defined:
            rows.append(finding(
                "P1-35", f"SoundEvent {name} 没有在 sounds.json 注册",
                path=f"assets/{namespace}/sounds.json", severity="info",
                suggestion="补 sounds.json 条目，或确认该音效由其它命名空间提供。",
            ))
    return rows


def _geckolib(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    geo_files = [p for p in ctx.glob("src/main/resources/assets/**/geo/*.json") if p.is_file()]
    if not geo_files:
        return rows
    bones: set[str] = set()
    for path in geo_files:
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        geometries = data.get("minecraft:geometry")
        if not isinstance(geometries, list):
            rows.append(finding(
                "P1-10", "GeckoLib 模型缺少 minecraft:geometry",
                path=ctx.rel(path), severity="error",
            ))
            continue
        for geometry in geometries:
            if not isinstance(geometry, dict):
                continue
            for bone in geometry.get("bones") or []:
                if isinstance(bone, dict) and bone.get("name"):
                    bones.add(str(bone["name"]))
    animations: dict[str, set[str]] = {}
    for path in ctx.glob("src/main/resources/assets/**/animations/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        mapping = data.get("animations")
        if not isinstance(mapping, dict):
            rows.append(finding(
                "P1-10", "动画文件缺少 animations 根节点",
                path=ctx.rel(path), severity="warning",
            ))
            continue
        for name, animation in mapping.items():
            if not isinstance(animation, dict):
                continue
            animation_bones = animation.get("bones") or {}
            if not isinstance(animation_bones, dict):
                continue
            animations[name] = {str(key) for key in animation_bones}
    for name, used in sorted(animations.items()):
        missing = used - bones
        if missing and bones:
            rows.append(finding(
                "P1-10", f"动画 {name} 引用了不存在的骨骼：{', '.join(sorted(missing)[:3])}",
                path="", severity="warning",
                suggestion="骨骼名必须与 .geo.json 完全一致（区分大小写）。",
            ))
    used_names = set()
    for java in ctx.java_files:
        if "RawAnimation" not in java.text and "AnimationController" not in java.text:
            continue
        for match in re.finditer(r"(?:then|thenLoop|playAnimation|setAnimation)\s*\(\s*\"([^\"]+)\"", java.text):
            used_names.add(match.group(1))
    for name in sorted(used_names - set(animations)):
        rows.append(finding(
            "P1-10", f"Java 播放的动画不存在：{name}",
            path="", severity="warning",
            suggestion="在 animations/*.json 中定义该动画，或修正名称。",
        ))
    return rows


def _atlases(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/assets/**/atlases/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        sources = data.get("sources")
        if not isinstance(sources, list):
            rows.append(finding(
                "P2-15", "图集文件缺少 sources",
                path=ctx.rel(path), severity="warning",
            ))
            continue
        seen: set[str] = set()
        for entry in sources:
            if not isinstance(entry, dict):
                continue
            resource = entry.get("resource")
            if not isinstance(resource, str):
                continue
            if resource in seen:
                rows.append(finding(
                    "P2-15", f"图集重复引用 {resource}",
                    path=ctx.rel(path), severity="info",
                ))
            seen.add(resource)
            if resource.startswith("#") or resource.startswith("${"):
                continue
            target = ctx.resource_root() / "assets" / _safe_relid(resource, _modid(ctx) or "")
            if target.suffix != ".png":
                target = target.with_suffix(".png")
            if not target.is_file() and not resource.startswith(("minecraft:", "neoforge:", "forge:", "fabric:")):
                rows.append(finding(
                    "P2-15", f"图集引用的贴图不存在：{resource}",
                    path=ctx.rel(path), severity="warning",
                ))
    return rows


def _java_textures(ctx: ProjectContext, namespaces: set[str]) -> Iterable[Finding]:
    rows: list[Finding] = []
    namespace = _modid(ctx) or (sorted(namespaces)[0] if namespaces else "")
    pattern = re.compile(r"\"textures/([\w/.\-]+)\.png\"")
    for java in ctx.java_files:
        text = java.text
        for match in pattern.finditer(text):
            value = match.group(1)
            target = ctx.resource_root() / "assets" / namespace / "textures" / (value + ".png")
            if not target.is_file():
                rows.append(finding(
                    "P1-12", f"代码引用的贴图不存在：textures/{value}.png",
                    path=ctx.rel(java.path), line=text[:match.start()].count("\n") + 1,
                    severity="warning",
                    suggestion="补齐资源或改用已存在的贴图。",
                ))
    return rows


def _enchantments(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for path in ctx.glob("src/main/resources/data/**/enchantment/*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        rel = ctx.rel(path)
        for key in ("weight", "max_level", "anvil_cost", "supported_items", "primary_items"):
            if key not in data:
                rows.append(finding(
                    "P1-36", f"附魔缺少字段 {key}",
                    path=rel, severity="warning",
                    suggestion="补全附魔数据，避免铁砧/砂轮/权重计算异常。",
                ))
        weight = data.get("weight")
        if isinstance(weight, int) and not 1 <= weight <= 1024:
            rows.append(finding("P1-36", f"附魔 weight={weight} 超出 1..1024", path=rel))
        level = data.get("max_level")
        if isinstance(level, int) and level < 1:
            rows.append(finding("P1-36", "附魔 max_level 必须至少为 1", path=rel))
        exclusive = data.get("exclusive_set")
        if not exclusive:
            rows.append(finding(
                "P1-36", "附魔没有 exclusive_set，可能与同类附魔叠加",
                path=rel, severity="info",
                suggestion="用标签声明互斥组，并检查铁砧/砂轮行为。",
            ))
    return rows
