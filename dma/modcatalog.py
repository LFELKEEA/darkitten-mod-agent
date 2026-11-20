"""模组模型库：扫描工程里的实体、方块、物品、群系与维度。

这个模块只读工程源码和资源，把散落在 Java 注册表、语言文件、模型、贴图与
数据包 JSON 里的信息整理成可编辑的条目。用户的修改（描述、标准字段、自定义
字段、替换的模型/贴图）保存在 ``.dma/mod_library.json``，不会覆盖扫描结果。

界面上「让 AI 修改」时，:func:`entry_instruction` 会把条目信息整理成一段
可直接发给 Agent 的中文需求。
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import project as project_mod

KINDS = ("entity", "block", "item", "biome", "dimension")

KIND_LABELS = {
    "entity": "实体",
    "block": "方块",
    "item": "物品",
    "biome": "群系",
    "dimension": "维度",
}

LIBRARY_FILE = "mod_library.json"

_ENTITY_CACHE: dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# 基础读取
# --------------------------------------------------------------------------- #
def _read_text(path: Path, limit: int = 240_000) -> str:
    try:
        return path.read_text(encoding="utf-8-sig", errors="replace")[:limit]
    except OSError:
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _exists(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def _first_file(paths: Iterable[Path]) -> Path | None:
    for path in paths:
        if _exists(path):
            return path
    return None


def _snake(name: str) -> str:
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(name or ""))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text or "entry"


def _camel(name: str) -> str:
    return "".join(part.capitalize() for part in _snake(name).split("_"))


def _clean_doc(text: str) -> str:
    match = re.findall(r"/\*\*(.*?)\*/", text or "", re.S)
    if not match:
        return ""
    doc = match[-1]
    doc = re.sub(r"\{@link\s+([^}]+)\}", r"\1", doc)
    doc = re.sub(r"<[^>]+>", "", doc)
    lines = []
    for line in doc.splitlines():
        line = re.sub(r"^\s*\*\s?", "", line).strip()
        if line:
            lines.append(line)
    return html.unescape(" ".join(lines).strip())


def _hex(value: Any, fallback: str = "#000000") -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return "#{:06x}".format(max(0, min(0xFFFFFF, number)))


def _span_width(value: Any) -> float:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return max(0.0, float(value[1]) - float(value[0]))
        except (TypeError, ValueError):
            return 1.0
    return 1.0


# --------------------------------------------------------------------------- #
# 工程结构
# --------------------------------------------------------------------------- #
def _asset_dirs(root: Path, modid: str) -> list[Path]:
    dirs = [
        Path(root) / "src/generated/resources/assets" / modid,
        Path(root) / "src/main/resources/assets" / modid,
    ]
    return [item for item in dirs if item.is_dir()]


def _data_dirs(root: Path, modid: str) -> list[Path]:
    dirs = [
        Path(root) / "src/generated/resources/data" / modid,
        Path(root) / "src/main/resources/data" / modid,
    ]
    return [item for item in dirs if item.is_dir()]


def _lang_map(root: Path, modid: str) -> dict[str, str]:
    result: dict[str, str] = {}
    # 生成目录先读、主资源目录后读；英文先读、中文后读。
    for folder in _asset_dirs(root, modid):
        for name in ("en_us.json", "zh_cn.json"):
            path = folder / "lang" / name
            if not _exists(path):
                continue
            for key, value in _read_json(path).items():
                if isinstance(value, str):
                    result[str(key)] = value
    # 语言文件使用 block.<modid>.<name>，扫描时更常用 block.<name>。
    for key, value in list(result.items()):
        parts = key.split(".")
        if len(parts) >= 3:
            result.setdefault(f"{parts[0]}.{parts[-1]}", value)
    return result


def _java_files(root: Path, limit: int = 4000) -> list[Path]:
    base = Path(root)
    if not base.is_dir():
        return []
    result: list[Path] = []
    try:
        for path in base.rglob("*.java"):
            parts = {part.lower() for part in path.parts}
            if parts & {"build", "out", ".gradle", "run", "runs"}:
                continue
            result.append(path)
            if len(result) >= limit:
                break
    except OSError:
        return result
    return result


def _class_map(files: Iterable[Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in files:
        result.setdefault(path.stem, path)
    return result


def _registry_block(text: str, key: str, size: int = 900) -> str:
    """取注册表里某个注册语句的完整文本。

    注册语句经常跨多行、内部还有嵌套括号。只截「附近 N 个字符」会把相邻
    条目的属性和类名混进来，所以这里按分号收尾，并从上一行往前留一点
    赋值语句的开头。
    """
    for match in re.finditer(re.escape(f'"{key}"'), text):
        # 从注册语句所在行开始，避免把上一个条目的属性带进来。
        start = max(0, text.rfind("\n", 0, match.start()))
        end = text.find(";", match.end())
        if end != -1:
            return text[start : end + 1]
        return text[start : match.end() + size]
    return ""


def _statement_after(text: str, token: str, size: int = 1200) -> str:
    """取 ``token`` 所在语句，用于 event.put(...) 这类没有注册名的块。"""
    start = text.find(token)
    if start < 0:
        return ""
    start = max(0, text.rfind("\n", 0, start) - 200)
    end = text.find(";", start)
    if end != -1:
        return text[start : end + 1]
    return text[start : start + size]


def _registered_names(text: str, patterns: Iterable[str]) -> list[str]:
    names: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.S):
            name = str(match.group(1) or "").strip()
            if name and name not in names:
                names.append(name)
    return names


def _asset_files(asset_dirs: Iterable[Path], relative: str, suffix: str) -> list[Path]:
    result: list[Path] = []
    for base in asset_dirs:
        folder = base / relative
        if not folder.is_dir():
            continue
        try:
            result.extend(sorted(folder.rglob(f"*{suffix}")))
        except OSError:
            continue
    return result


def _asset_model_files(asset_dirs: Iterable[Path], relative: str) -> list[Path]:
    """收集目录下的 Java 模型、.geo.json 和 .bbmodel。"""
    result: list[Path] = []
    for suffix in (".json", ".geo.json", ".bbmodel"):
        result.extend(_asset_files(asset_dirs, relative, suffix))
    return result


def _model_key(path: Path) -> str:
    name = path.name
    for suffix in (".geo.json", ".bbmodel", ".json"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


# --------------------------------------------------------------------------- #
# Java 语义
# --------------------------------------------------------------------------- #
_EXTENDS_CACHE: dict[tuple[str, str], str] = {}


def _class_chain(class_name: str, classes: dict[str, Path], depth: int = 0) -> str:
    """把继承链拼成一个字符串，用于判断敌对/中立等。"""
    if not class_name or depth > 6:
        return class_name
    path = classes.get(class_name)
    if path is None:
        return class_name
    key = (str(path), class_name)
    if key in _EXTENDS_CACHE:
        return _EXTENDS_CACHE[key]
    text = _read_text(path, 120_000)
    match = re.search(
        r"\bclass\s+" + re.escape(class_name) + r"\b[^{]*?\bextends\s+([A-Za-z0-9_.$]+)",
        text,
    )
    if match is None:
        _EXTENDS_CACHE[key] = class_name
        return class_name
    parent = match.group(1).split(".")[-1].split("<")[0]
    chain = class_name + " " + _class_chain(parent, classes, depth + 1)
    _EXTENDS_CACHE[key] = chain
    return chain


def _disposition(chain: str, category: str, key: str) -> str:
    if "Boss" in chain or "boss" in key:
        return "Boss（敌对）"
    if any(word in chain for word in ("Projectile", "Arrow", "Throwable")):
        return "投射物"
    if "Monster" in chain or category.upper() == "MONSTER":
        return "敌对"
    if any(word in chain for word in ("Animal", "TamableAnimal", "AbstractHorse", "Villager")):
        return "友善 / 中立"
    if "AmbientCreature" in chain or category.upper() in {"AMBIENT", "WATER_AMBIENT"}:
        return "环境生物"
    if category.upper() == "MISC":
        return "特殊实体"
    if category.upper() == "CREATURE":
        return "中立"
    return "未判定"


_GOAL_NAMES = {
    "FloatGoal": "水中漂浮",
    "MeleeAttackGoal": "近战追击",
    "RangedAttackGoal": "远程射击",
    "NearestAttackableTargetGoal": "主动索敌",
    "HurtByTargetGoal": "受击反击",
    "AvoidEntityGoal": "躲避目标",
    "PanicGoal": "受惊逃跑",
    "FollowOwnerGoal": "跟随主人",
    "BreedGoal": "繁殖",
    "TemptGoal": "被食物吸引",
    "WaterAvoidingRandomStrollGoal": "随机游荡",
    "RandomStrollGoal": "随机游荡",
    "MoveTowardsRestrictionGoal": "回到活动区域",
    "LookAtPlayerGoal": "注视玩家",
    "RandomLookAroundGoal": "四处张望",
    "RestrictSunGoal": "躲避阳光",
    "FleeSunGoal": "逃离阳光",
    "OpenDoorGoal": "开门",
    "LeapAtTargetGoal": "扑跃攻击",
    "SwellGoal": "膨胀爆炸",
}

_METHOD_LABELS = {
    "use": "右键交互",
    "useWithoutItem": "右键交互",
    "useOn": "对另一方块使用",
    "entityInside": "实体接触触发",
    "randomTick": "随机刻更新",
    "animateTick": "粒子/音效动画",
    "getShape": "自定义碰撞箱",
    "neighborChanged": "相邻方块变化时更新",
    "updateShape": "连接状态更新",
    "isPathfindable": "可被生物穿过",
    "getLightBlock": "遮挡光照",
    "getSignal": "输出红石信号",
    "hasAnalogOutputSignal": "支持比较器",
    "onPlace": "放置时触发",
    "entityInside": "实体接触触发",
    "stepOn": "踩踏触发",
    "fallOn": "摔落触发",
    "playerWillDestroy": "破坏前触发",
}


def _method_labels(text: str) -> list[str]:
    labels: list[str] = []
    for method, label in _METHOD_LABELS.items():
        if re.search(r"\b" + re.escape(method) + r"\s*\(", text) and label not in labels:
            labels.append(label)
    return labels


def _attributes_window(text: str, key: str) -> str:
    """从 ``event.put(KEY.get(), ... build())`` 里取属性注册块。"""
    token = f"{str(key).upper()}.get(),"
    for match in re.finditer(re.escape(token), text):
        start = text.rfind("event.put(", 0, match.start())
        if start < 0:
            continue
        end = text.find(");", match.end())
        if end != -1:
            return text[start : end + 2]
    return ""


def _parse_attributes(window: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(
        r"Attributes\.([A-Z_]+)\s*,\s*([-+]?[0-9]*\.?[0-9]+)", window
    ):
        result[match.group(1)] = match.group(2)
    return result


_ATTRIBUTE_LABELS = {
    "MAX_HEALTH": "生命值",
    "ATTACK_DAMAGE": "攻击力",
    "ARMOR": "护甲",
    "ARMOR_TOUGHNESS": "护甲韧性",
    "MOVEMENT_SPEED": "移动速度",
    "FLYING_SPEED": "飞行速度",
    "FOLLOW_RANGE": "跟随范围",
    "KNOCKBACK_RESISTANCE": "击退抗性",
    "ATTACK_KNOCKBACK": "攻击击退",
    "ATTACK_SPEED": "攻击速度",
    "MAX_ABSORPTION": "伤害吸收",
    "LUCK": "幸运",
}


# --------------------------------------------------------------------------- #
# 世界生成
# --------------------------------------------------------------------------- #
def _dimension_data(root: Path, modid: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for base in _data_dirs(root, modid):
        folder = base / "dimension"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read_json(path)
            if data:
                result[path.stem] = data
    return result


def _dimension_types(root: Path, modid: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for base in _data_dirs(root, modid):
        folder = base / "dimension_type"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read_json(path)
            if data:
                result[path.stem] = data
    return result


def _biome_json(root: Path, modid: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for base in _data_dirs(root, modid):
        folder = base / "worldgen" / "biome"
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            data = _read_json(path)
            if data:
                result[path.stem] = data
    return result


def _biome_dimensions(
    dimensions: dict[str, dict[str, Any]], modid: str
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    result: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for dim_key, data in dimensions.items():
        generator = data.get("generator") if isinstance(data.get("generator"), dict) else {}
        source = (
            generator.get("biome_source")
            if isinstance(generator.get("biome_source"), dict)
            else {}
        )
        for item in source.get("biomes") or []:
            if not isinstance(item, dict):
                continue
            biome = str(item.get("biome") or "")
            if not biome:
                continue
            key = biome.split(":")[-1]
            params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            result.setdefault(key, []).append((dim_key, params))
    return result


def _biome_spawns(
    biomes: dict[str, dict[str, Any]], modid: str
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for biome_key, data in biomes.items():
        spawners = data.get("spawners") if isinstance(data.get("spawners"), dict) else {}
        for category, entries in spawners.items():
            for item in entries or []:
                if not isinstance(item, dict):
                    continue
                entity = str(item.get("type") or "")
                if not entity.startswith(f"{modid}:"):
                    continue
                key = entity.split(":")[-1]
                result.setdefault(key, []).append(
                    {
                        "biome": biome_key,
                        "category": category,
                        "weight": item.get("weight"),
                        "min": item.get("minCount"),
                        "max": item.get("maxCount"),
                    }
                )
    return result


def _spawn_conditions(root: Path, key: str) -> str:
    """从 SpawnPlacementEvents / 实体类里提取自然生成条件。"""
    token = f"ModEntities.{_snake(key).upper()}"
    for path in _java_files(root):
        text = _read_text(path, 140_000)
        if token not in text or "SpawnPlacement" not in text:
            continue
        start = text.find(token)
        window = text[start : start + 900]
        bits: list[str] = []
        placement = re.search(r"SpawnPlacements\.Type\.([A-Z_]+)", window)
        if placement:
            bits.append(
                {
                    "ON_GROUND": "地面生成",
                    "NO_RESTRICTIONS": "无位置限制",
                    "IN_WATER": "水中生成",
                    "IN_LAVA": "岩浆中生成",
                }.get(placement.group(1), placement.group(1))
            )
        heightmap = re.search(r"Heightmap\.Types\.([A-Z_]+)", window)
        if heightmap:
            bits.append(f"高度图 {heightmap.group(1)}")
        light = re.search(r"getRawBrightness\([^)]*\)\s*<\s*(\d+)", window)
        if light:
            bits.append(f"光照 < {light.group(1)}")
        block = re.search(r"\.is\(ModBlocks\.([A-Z_]+)\.get\(\)\)", window)
        if block:
            bits.append(f"站在 {block.group(1).lower()} 上")
        if bits:
            return "；".join(bits)
    return ""


def _dimension_biome_share(
    biomes: dict[str, dict[str, Any]],
    dimensions: dict[str, dict[str, Any]],
    modid: str,
) -> dict[str, float]:
    """按气候参数跨度估算群系在维度中的占比（只用于界面提示）。"""
    shares: dict[str, float] = {}
    for dim_key, data in dimensions.items():
        generator = data.get("generator") if isinstance(data.get("generator"), dict) else {}
        source = (
            generator.get("biome_source")
            if isinstance(generator.get("biome_source"), dict)
            else {}
        )
        products: list[tuple[str, float]] = []
        for item in source.get("biomes") or []:
            if not isinstance(item, dict):
                continue
            biome = str(item.get("biome") or "").split(":")[-1]
            params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            product = 1.0
            for name in ("temperature", "humidity", "continentalness", "erosion", "depth", "weirdness"):
                product *= _span_width(params.get(name))
            products.append((biome, max(1e-9, product)))
        total = sum(value for _, value in products) or 1.0
        for biome, value in products:
            shares[biome] = max(shares.get(biome, 0.0), value / total * 100.0)
    return shares


# --------------------------------------------------------------------------- #
# 条目构建
# --------------------------------------------------------------------------- #
def _field(key: str, value: Any) -> dict[str, str] | None:
    text = str(value or "").strip()
    if not text or text in {"-", "0", "None", "null"}:
        return None
    return {"key": key, "value": text}


def _fields(*items: dict[str, str] | None) -> list[dict[str, str]]:
    return [item for item in items if item]


def _entry(
    kind: str,
    key: str,
    title: str,
    *,
    description: str = "",
    fields: list[dict[str, str]] | None = None,
    model: str = "",
    texture: str = "",
    model_kind: str = "",
    source: str = "",
    **extra: Any,
) -> dict[str, Any]:
    return {
        "id": f"{kind}:{key}",
        "kind": kind,
        "key": key,
        "title": title or key,
        "description": description or "",
        "fields": fields or [],
        "custom_fields": [],
        "model": model,
        "texture": texture,
        "model_kind": model_kind or ("entity" if kind == "entity" else "custom"),
        "source": source,
        "custom": False,
        **extra,
    }


def _rel(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _pretty(key: str) -> str:
    return str(key or "").replace("_", " ").strip() or str(key or "")


def _title(lang: dict[str, str], kind: str, key: str) -> str:
    for prefix in (f"{kind}.", "entity.", "block.", "item.", "biome.", "dimension."):
        value = lang.get(prefix + key)
        if value:
            return value
    return _pretty(key)


def _asset_model(root: Path, asset_dirs: list[Path], kind: str, key: str) -> Path | None:
    if kind == "entity":
        candidates: list[Path] = []
        for base in asset_dirs:
            candidates.extend(
                [
                    base / "geo" / f"{key}.geo.json",
                    base / "geo" / f"{key}.bbmodel",
                    base / "models" / "entity" / f"{key}.geo.json",
                    base / "models" / "entity" / f"{key}.bbmodel",
                    base / "models" / "entity" / f"{key}.json",
                ]
            )
        return _first_file(candidates)
    folder = "block" if kind == "block" else "item"
    candidates = []
    for base in asset_dirs:
        candidates.extend(
            [
                base / "models" / folder / f"{key}.json",
                base / "models" / folder / f"{key}.bbmodel",
                base / "models" / folder / f"{key}.geo.json",
                base / "geo" / f"{key}.bbmodel",
                base / "geo" / f"{key}.geo.json",
            ]
        )
    return _first_file(candidates)


def _asset_texture(root: Path, asset_dirs: list[Path], kind: str, key: str) -> Path | None:
    folder = {"entity": "entity", "block": "block", "item": "item"}.get(kind, kind)
    names = [f"{key}.png"]
    if kind == "block":
        names.extend([f"{key}_top.png", f"{key}_front.png", f"{key}_side.png"])
    for name in names:
        for base in asset_dirs:
            path = base / "textures" / folder / name
            if _exists(path):
                return path
    return None


# --------------------------------------------------------------------------- #
# 实体
# --------------------------------------------------------------------------- #
def _scan_entities(
    root: Path,
    modid: str,
    asset_dirs: list[Path],
    lang: dict[str, str],
    classes: dict[str, Path],
    biomes: dict[str, dict[str, Any]],
    dimensions: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    registry_path = root / "src/main/java" / Path(*modid.split(".")) / "registry" / "ModEntities.java"
    candidates = [
        root / "src/main/java" / Path(*modid.split(".")) / "registry" / "ModEntities.java",
    ]
    module_roots = list((root / "src/main/java").glob("**/ModEntities.java"))
    registry = _first_file(candidates + module_roots)
    registry_text = _read_text(registry) if registry else ""

    names: list[str] = []
    key_to_class: dict[str, str] = {}
    for match in re.finditer(
        r"(\w+)\s*=\s*ENTITIES\.register\(\s*\"([^\"]+)\"[\s\S]{0,260}?"
        r"EntityType\.Builder\.<(\w+)>",
        registry_text,
    ):
        key_to_class[match.group(2)] = match.group(3)
        names.append(match.group(2))
    for match in re.finditer(r"ENTITIES\.register\(\s*\"([^\"]+)\"", registry_text):
        if match.group(1) not in names:
            names.append(match.group(1))

    if not names:
        for path in _asset_files(asset_dirs, "textures/entity", ".png"):
            names.append(path.stem)
    # .geo.json / .bbmodel 可能是模型库独有的资源，即使注册表里没有也收进来。
    for path in _asset_model_files(asset_dirs, "geo"):
        key = _model_key(path)
        if key not in names:
            names.append(key)
    for path in _asset_model_files(asset_dirs, "models/entity"):
        key = _model_key(path)
        if key not in names:
            names.append(key)
    for path in _java_files(root):
        lowered = path.as_posix().lower()
        if "/entity/" not in lowered or "/block/entity/" in lowered:
            continue
        stem = path.stem
        if not stem.endswith("Entity") or stem.endswith("BlockEntity"):
            continue
        if "projectile" in lowered:
            continue
        key = _snake(stem[:-6])
        if key and key not in names:
            names.append(key)

    spawns = _biome_spawns(biomes, modid)
    biome_dims = _biome_dimensions(dimensions, modid)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in names:
        if key in seen:
            continue
        seen.add(key)
        class_name = key_to_class.get(key) or f"{_camel(key)}Entity"
        path = classes.get(class_name)
        text = _read_text(path) if path else ""
        window = _registry_block(registry_text, key)
        size = re.search(r"\.sized\(\s*([\d.]+)F?\s*,\s*([\d.]+)F?\s*\)", window)
        category = re.search(r"MobCategory\.([A-Z_]+)", window)
        category_name = category.group(1) if category else ""
        class_chain = _class_chain(class_name, classes) if path else class_name
        disposition = _disposition(class_chain, category_name, key)

        attrs = _parse_attributes(_attributes_window(registry_text, key))
        if not attrs and text:
            create = re.search(r"createAttributes\(\)([\s\S]{0,900}?)\n\s*}", text)
            if create:
                attrs = _parse_attributes(create.group(1))

        goal_text = ""
        goal_match = re.search(r"registerGoals\(\)([\s\S]{0,1600})", text)
        if goal_match:
            goal_text = goal_match.group(1)
        goals: list[str] = []
        for match in re.finditer(r"new\s+(\w+Goal)", goal_text):
            label = _GOAL_NAMES.get(match.group(1), match.group(1))
            if label not in goals:
                goals.append(label)

        logic_bits: list[str] = []
        if goals:
            logic_bits.append("、".join(goals[:7]))
        if "shoot(" in text or "Projectile" in text:
            logic_bits.append("会发射投射物")
        if "explode" in text.lower() or "SwellGoal" in goal_text:
            logic_bits.append("会爆炸")
        if "changeDimension" in text or "teleport" in text.lower():
            logic_bits.append("会传送")
        if "heal(" in text or "setHealth" in text:
            logic_bits.append("有治疗/回血行为")
        if "summon" in text:
            logic_bits.append("会召唤僕从")
        if "setTarget" in text and "NearestAttackableTargetGoal" not in goal_text:
            logic_bits.append("会主动锁定目标")

        spawn_list = spawns.get(key, [])
        biome_names: list[str] = []
        dim_names: list[str] = []
        spawn_detail: list[str] = []
        for item in spawn_list:
            biome = item.get("biome") or ""
            biome_title = _title(lang, "biome", biome)
            if biome_title not in biome_names:
                biome_names.append(biome_title)
            for dim_key, _params in biome_dims.get(biome, []):
                dim_title = _title(lang, "dimension", dim_key)
                if dim_title not in dim_names:
                    dim_names.append(dim_title)
            weight = item.get("weight")
            low = item.get("min")
            high = item.get("max")
            piece = f"{biome_title}（权重 {weight}，{low}-{high} 只）"
            if piece not in spawn_detail:
                spawn_detail.append(piece)

        condition = _spawn_conditions(root, key)
        spawn_method = "；".join(spawn_detail) if spawn_detail else ""
        if condition:
            spawn_method = (spawn_method + "；" if spawn_method else "") + condition
        if not spawn_method:
            spawn_method = "未配置自然生成，可用生成蛋或命令召唤"

        drops = sorted(
            set(
                re.findall(r"spawnAtLocation\(ModItems\.([A-Z0-9_]+)", text)
                + re.findall(r"dropCustomDeathLoot[\s\S]{0,500}?ModItems\.([A-Z0-9_]+)", text)
            )
        )
        drop_titles = [_title(lang, "item", item.lower()) for item in drops]

        fields = _fields(
            _field("类别", "Boss" if "Boss" in class_chain else (category_name or "生物")),
            _field("阵营", disposition),
            _field("生成维度", "、".join(dim_names) or "见生成条件"),
            _field("生成群系", "、".join(biome_names) or "未配置"),
            _field("生成方式", spawn_method),
            _field("生命值", attrs.get("MAX_HEALTH")),
            _field("攻击力", attrs.get("ATTACK_DAMAGE")),
            _field("护甲", attrs.get("ARMOR")),
            _field("移动速度", attrs.get("MOVEMENT_SPEED")),
            _field("飞行速度", attrs.get("FLYING_SPEED")),
            _field("跟随范围", attrs.get("FOLLOW_RANGE")),
            _field("体型", f"{size.group(1)} × {size.group(2)}" if size else ""),
            _field("AI 逻辑", "；".join(logic_bits)),
            _field("掉落物", "、".join(drop_titles)),
            _field("特殊", "火焰免疫" if ".fireImmune()" in window else ""),
        )
        description = _clean_doc(text)
        if not description:
            description = (
                f"{_title(lang, 'entity', key)}，{disposition}。"
                f"{('生成于' + '、'.join(biome_names) + '。') if biome_names else ''}"
            )
        model = _asset_model(root, asset_dirs, "entity", key)
        texture = _asset_texture(root, asset_dirs, "entity", key)
        result.append(
            _entry(
                "entity",
                key,
                _title(lang, "entity", key),
                description=description,
                fields=fields,
                model=_rel(root, model),
                texture=_rel(root, texture),
                model_kind="entity",
                source=_rel(root, path),
                disposition=disposition,
                category=category_name,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# 方块
# --------------------------------------------------------------------------- #
def _scan_blocks(
    root: Path,
    modid: str,
    asset_dirs: list[Path],
    lang: dict[str, str],
    classes: dict[str, Path],
) -> list[dict[str, Any]]:
    registry = _first_file(
        [root / "src/main/java" / Path(*modid.split(".")) / "registry" / "ModBlocks.java"]
        + list((root / "src/main/java").glob("**/ModBlocks.java"))
    )
    text = _read_text(registry) if registry else ""
    names = _registered_names(
        text,
        [
            r'register(?:NoItem)?\(\s*"([^"]+)"',
        ],
    )
    if not names:
        for path in _asset_files(asset_dirs, "blockstates", ".json"):
            names.append(path.stem)
    for path in _asset_model_files(asset_dirs, "models/block"):
        stem = _model_key(path)
        if any(stem.endswith(suffix) for suffix in ("_horizontal", "_inventory", "_hanging")):
            continue
        if stem not in names:
            names.append(stem)

    key_to_class: dict[str, str] = {}
    for match in re.finditer(
        r'RegistryObject<Block>\s+\w+\s*=\s*register(?:NoItem)?\(\s*"([^"]+)"[\s\S]{0,260}?new\s+(\w+)\(',
        text,
    ):
        key_to_class[match.group(1)] = match.group(2)

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in names:
        if key in seen:
            continue
        seen.add(key)
        window = _registry_block(text, key)
        class_name = key_to_class.get(key, "")
        path = classes.get(class_name)
        class_text = _read_text(path) if path else ""
        light = re.search(r"\.lightLevel\([^)]*?(\d+)\s*\)", window)
        strength = re.search(
            r"\.strength\(\s*(-?[\d.]+)F?\s*(?:,\s*(-?[\d.]+)F?\s*)?\)", window
        )
        copy_block = re.search(r"\.copy\(Blocks\.([A-Z0-9_]+)\)", window)
        map_color = re.search(r"\.mapColor\(MapColor\.([A-Z0-9_]+)\)", window)
        sound = re.search(r"\.sound\(SoundType\.([A-Z0-9_]+)\)", window)

        interaction = _method_labels(class_text)
        if "changeDimension" in class_text or "ITeleporter" in class_text:
            interaction.append("进入后传送")
        if "event.register" in class_text or "addSpawn" in class_text:
            interaction.append("生成相关")
        if not interaction:
            interaction = ["装饰/建筑方块"]

        block_entity = ""
        if class_name:
            be_path = classes.get(class_name + "Entity")
            if be_path is not None:
                block_entity = "有方块实体（可保存库存/状态）"

        solid = "否（可穿过）" if ".noCollission()" in window else "是"
        hardness = ""
        if strength:
            hardness = strength.group(1)
            if strength.group(2):
                hardness += f" / 抗爆 {strength.group(2)}"
        light_value = light.group(1) if light else "0"
        features = list(interaction)
        if int(light_value or 0) > 0:
            features.append(f"发光等级 {light_value}")
        if ".randomTicks()" in window:
            features.append("随机刻更新")
        if block_entity:
            features.append(block_entity)
        title = _title(lang, "block", key)
        description = _clean_doc(class_text) or f"{title}，{'、'.join(interaction)}。"

        model = _asset_model(root, asset_dirs, "block", key)
        texture = _asset_texture(root, asset_dirs, "block", key)
        fields = _fields(
            _field("交互", "、".join(interaction)),
            _field("功能 / 特性", "；".join(features)),
            _field("硬度 / 抗爆", hardness),
            _field("发光等级", light_value if light_value != "0" else ""),
            _field("碰撞", solid),
            _field("材质", copy_block.group(1).lower().replace("_", " ") if copy_block else ""),
            _field("地图颜色", map_color.group(1) if map_color else ""),
            _field("音效", sound.group(1).lower() if sound else ""),
            _field("方块实体", block_entity),
        )
        result.append(
            _entry(
                "block",
                key,
                title,
                description=description,
                fields=fields,
                model=_rel(root, model),
                texture=_rel(root, texture),
                model_kind="custom",
                source=_rel(root, path),
            )
        )
    return result


# --------------------------------------------------------------------------- #
# 物品
# --------------------------------------------------------------------------- #
def _item_category(key: str, window: str, class_name: str) -> str:
    if key.endswith("_spawn_egg") or "SpawnEgg" in window:
        return "生成蛋"
    if "SwordItem" in window or "SwordItem" in class_name:
        return "武器（剑）"
    if "PickaxeItem" in window:
        return "工具（镐）"
    if "AxeItem" in window:
        return "工具（斧）"
    if "ShovelItem" in window:
        return "工具（锹）"
    if "HoeItem" in window:
        return "工具（锄）"
    if "ArmorItem" in window or "armor(" in window:
        return "装备（护甲）"
    if "FoodProperties" in window or ".food(" in window:
        return "食物"
    if "BossSigil" in window or "Sigil" in class_name:
        return "Boss 徽记"
    if "Trophy" in window or "Trophy" in class_name:
        return "奖杯"
    if "MazeMap" in class_name or "MapItem" in class_name:
        return "地图"
    if "Staff" in class_name:
        return "法杖"
    if "Igniter" in class_name or "Activator" in class_name:
        return "传送门激活工具"
    if "BlockItem" in window:
        return "方块物品"
    return "材料"


def _scan_items(
    root: Path,
    modid: str,
    asset_dirs: list[Path],
    lang: dict[str, str],
    classes: dict[str, Path],
    block_names: set[str],
) -> list[dict[str, Any]]:
    registry = _first_file(
        [root / "src/main/java" / Path(*modid.split(".")) / "registry" / "ModItems.java"]
        + list((root / "src/main/java").glob("**/ModItems.java"))
    )
    text = _read_text(registry) if registry else ""
    names = _registered_names(
        text,
        [
            r'ITEMS\.register\(\s*"([^"]+)"',
            r'\bsimple\(\s*"([^"]+)"',
            r'\barmor\(\s*"([^"]+)"',
        ],
    )
    if not names:
        for path in _asset_model_files(asset_dirs, "models/item"):
            names.append(_model_key(path))
        for path in _asset_files(asset_dirs, "textures/item", ".png"):
            names.append(path.stem)
    for path in _asset_model_files(asset_dirs, "models/item"):
        key = _model_key(path)
        if key not in names:
            names.append(key)

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in names:
        if key in seen:
            continue
        seen.add(key)
        window = _registry_block(text, key, size=420)
        class_match = re.search(r"new\s+(\w+)\(", window)
        class_name = class_match.group(1) if class_match else ""
        path = classes.get(class_name)
        class_text = _read_text(path) if path else ""
        category = _item_category(key, window, class_name)
        if key in block_names and category == "材料":
            # 方块物品通常只有 models/item 里的父模型，没有独立的注册类。
            category = "方块物品"

        nutrition = re.search(r"nutrition\(\s*(\d+)", window)
        saturation = re.search(r"saturationMod\(\s*([\d.]+)F?\s*\)", window)
        stacks = re.search(r"stacksTo\(\s*(\d+)\s*\)", window)
        durability = re.search(r"durability\(\s*(\d+)\s*\)", window)
        rarity = re.search(r"rarity\(Rarity\.([A-Z]+)\)", window)
        attack = re.search(r"new\s+\w+Item\([^,]+,\s*([\d.]+)F?\s*,\s*(-?[\d.]+)F?", window)
        effects = sorted(
            set(
                re.findall(r"ModEffects\.([A-Z0-9_]+)", class_text)
                + re.findall(r"MobEffects\.([A-Z0-9_]+)", class_text)
            )
        )
        use_bits: list[str] = []
        if re.search(r"\buse\s*\(", class_text):
            use_bits.append("右键使用")
        if "appendHoverText" in class_text:
            use_bits.append("带说明 Tooltip")
        if "FoodProperties" in window:
            use_bits.append("可食用")
        if "getUseDuration" in class_text:
            use_bits.append("蓄力使用")

        title = _title(lang, "item", key)
        if title == key and key in block_names:
            title = _title(lang, "block", key)
        description = _clean_doc(class_text) or f"{title}，类别：{category}。"
        fields = _fields(
            _field("类别", category),
            _field("作用 / 效果", "、".join(use_bits)),
            _field("营养值", nutrition.group(1) if nutrition else ""),
            _field("饱和度", saturation.group(1) if saturation else ""),
            _field("堆叠上限", stacks.group(1) if stacks else ""),
            _field("耐久", durability.group(1) if durability else ""),
            _field("稀有度", rarity.group(1) if rarity else ""),
            _field("攻击 / 攻速", f"{attack.group(1)} / {attack.group(2)}" if attack else ""),
            _field("状态效果", "、".join(effects)),
            _field("注册类", class_name),
        )
        model = _asset_model(root, asset_dirs, "item", key)
        texture = _asset_texture(root, asset_dirs, "item", key)
        result.append(
            _entry(
                "item",
                key,
                title,
                description=description,
                fields=fields,
                model=_rel(root, model),
                texture=_rel(root, texture),
                model_kind="item",
                source=_rel(root, path),
                category=category,
                is_block=key in block_names,
            )
        )
    return result


# --------------------------------------------------------------------------- #
# 群系
# --------------------------------------------------------------------------- #
def _scan_biomes(
    root: Path,
    modid: str,
    lang: dict[str, str],
    biomes: dict[str, dict[str, Any]],
    dimensions: dict[str, dict[str, Any]],
    dim_types: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    names = list(biomes.keys())
    for path in list((root / "src/main/java").glob("**/ModBiomes.java")) + list(
        (root / "src/main/java").glob("**/TwilightBiomes.java")
    ):
        text = _read_text(path)
        for match in re.finditer(r"ResourceKey<Biome>\s+(\w+)\s*=", text):
            key = _snake(match.group(1))
            if key not in names:
                names.append(key)
    biome_dims = _biome_dimensions(dimensions, modid)
    shares = _dimension_biome_share(biomes, dimensions, modid)
    result: list[dict[str, Any]] = []
    for key in names:
        data = biomes.get(key, {})
        effects = data.get("effects") if isinstance(data.get("effects"), dict) else {}
        sky = _hex(effects.get("sky_color"), "#000000")
        fog = _hex(effects.get("fog_color"), "#000000")
        grass = _hex(effects.get("grass_color"), "")
        foliage = _hex(effects.get("foliage_color"), "")
        water = _hex(effects.get("water_color"), "")
        dim_names: list[str] = []
        params_text: list[str] = []
        for dim_key, params in biome_dims.get(key, []):
            title = _title(lang, "dimension", dim_key)
            if title not in dim_names:
                dim_names.append(title)
            temp = params.get("temperature")
            humidity = params.get("humidity")
            if isinstance(temp, (list, tuple)) and len(temp) >= 2:
                params_text.append(f"温度 {temp[0]}~{temp[1]}")
            if isinstance(humidity, (list, tuple)) and len(humidity) >= 2:
                params_text.append(f"湿度 {humidity[0]}~{humidity[1]}")

        features: list[str] = []
        for group in data.get("features") or []:
            for item in group or []:
                name = str(item or "").split(":")[-1]
                if name and name not in features and not name.startswith("ore_") and not name.startswith("lake_"):
                    features.append(name)
        spawns: list[str] = []
        spawners = data.get("spawners") if isinstance(data.get("spawners"), dict) else {}
        for category, entries in spawners.items():
            for item in entries or []:
                if not isinstance(item, dict):
                    continue
                entity = str(item.get("type") or "").split(":")[-1]
                if entity:
                    spawns.append(entity)

        range_text = ""
        light_text = ""
        for dim_key in dim_names:
            type_data = dim_types.get(dim_key, {})
            if not type_data:
                continue
            min_y = type_data.get("min_y")
            height = type_data.get("height")
            if min_y is not None and height is not None:
                try:
                    range_text = f"Y {int(min_y)} ~ {int(min_y) + int(height)}"
                except (TypeError, ValueError):
                    range_text = f"Y {min_y} ~ {height}"
            if type_data.get("ambient_light") is not None:
                light_text = str(type_data.get("ambient_light"))
            break

        biome_title = _title(lang, "biome", key)
        if data:
            description = (
                f"{biome_title}：天空 {sky}，雾 {fog}。"
                + (f"位于 {'、'.join(dim_names)}。" if dim_names else "")
            )
        else:
            description = f"{biome_title}：还没有找到群系 JSON，可能只声明在代码里。"
        fields = _fields(
            _field("所在维度", "、".join(dim_names) or "未配置"),
            _field("生成概率", f"约 {shares.get(key, 0):.1f}%（按气候参数跨度估算）" if key in shares else ""),
            _field("气候参数", "；".join(params_text)),
            _field("天空模样", f"天空 {sky}，雾 {fog}" + (f"，草地 {grass}" if grass else "") + (f"，树叶 {foliage}" if foliage else "")),
            _field("范围", range_text),
            _field("光照强度", f"环境光 {light_text}" if light_text else ""),
            _field("温度 / 降水", f"温度 {data.get('temperature', '-')}，降水 {data.get('downfall', '-')}"),
            _field("特性", "、".join(features[:10])),
            _field("生成生物", "、".join(spawns[:12])),
            _field("水色", water),
            _field("数据状态", "" if data else "未找到 worldgen/biome JSON"),
        )
        result.append(
            _entry(
                "biome",
                key,
                biome_title,
                description=description,
                fields=fields,
                model_kind="sky",
                sky={"sky": sky, "fog": fog, "grass": grass, "foliage": foliage, "water": water},
            )
        )
    return result


# --------------------------------------------------------------------------- #
# 维度
# --------------------------------------------------------------------------- #
def _scan_dimensions(
    root: Path,
    modid: str,
    lang: dict[str, str],
    dimensions: dict[str, dict[str, Any]],
    dim_types: dict[str, dict[str, Any]],
    biomes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    names = list(dimensions.keys())
    for path in list((root / "src/main/java").glob("**/ModDimensions.java")):
        text = _read_text(path)
        for match in re.finditer(r"ResourceKey<Level>\s+(\w+)\s*=", text):
            key = _snake(match.group(1))
            if key not in names:
                names.append(key)
    # 维度类型有时没有对应 dimension JSON，也保留下来。
    for key in dim_types:
        if key not in names:
            names.append(key)

    portal_items = [
        _title(lang, "item", key)
        for key in lang
        if key.startswith("item.") and "portal" in key and ("igniter" in key or "activator" in key)
    ]
    portal_blocks = [
        _title(lang, "block", key)
        for key in lang
        if key.startswith("block.") and "portal" in key
    ]
    result: list[dict[str, Any]] = []
    for key in names:
        data = dimensions.get(key, {})
        type_data = dim_types.get(key, {})
        generator = data.get("generator") if isinstance(data.get("generator"), dict) else {}
        source = (
            generator.get("biome_source")
            if isinstance(generator.get("biome_source"), dict)
            else {}
        )
        biome_list = []
        for item in source.get("biomes") or []:
            if isinstance(item, dict):
                biome = str(item.get("biome") or "").split(":")[-1]
                if biome and biome not in biome_list:
                    biome_list.append(biome)

        min_y = type_data.get("min_y")
        height = type_data.get("height")
        size = ""
        if min_y is not None and height is not None:
            try:
                size = f"Y {int(min_y)} ~ {int(min_y) + int(height)}（高度 {int(height)}）"
            except (TypeError, ValueError):
                size = f"Y {min_y} ~ {height}"

        fixed_time = type_data.get("fixed_time")
        time_text = ""
        if fixed_time is not None:
            try:
                ticks = int(fixed_time) % 24000
                label = "永久黄昏" if 11000 <= ticks <= 14000 else ("永久夜晚" if ticks >= 13000 or ticks < 1000 else "固定时间")
                time_text = f"{label}（{ticks} tick）"
            except (TypeError, ValueError):
                time_text = str(fixed_time)

        sky_colors = []
        for biome in biome_list[:3]:
            effects = biomes.get(biome, {}).get("effects")
            if isinstance(effects, dict) and effects.get("sky_color") is not None:
                sky_colors.append(_hex(effects.get("sky_color")))
        sky_text = " / ".join(sky_colors[:3]) if sky_colors else "见维度效果设置"

        entry_bits: list[str] = []
        if portal_items and portal_blocks:
            entry_bits.append(f"使用「{portal_items[0]}」点燃/激活「{portal_blocks[0]}」")
        elif portal_blocks:
            entry_bits.append(f"进入「{portal_blocks[0]}」")
        else:
            entry_bits.append("通过传送门或命令进入")

        features: list[str] = []
        if type_data.get("has_skylight") is False:
            features.append("无天空光")
        if type_data.get("has_ceiling"):
            features.append("有基岩顶")
        if type_data.get("ultrawarm"):
            features.append("高温维度")
        if type_data.get("natural") is False:
            features.append("非自然维度")
        if type_data.get("coordinate_scale") not in (None, 1, 1.0):
            features.append(f"坐标缩放 {type_data.get('coordinate_scale')}")
        if type_data.get("respawn_anchor_works") is False:
            features.append("重生锚不可用")
        if type_data.get("bed_works") is False:
            features.append("床会爆炸")
        if type_data.get("ambient_light") is not None:
            features.append(f"环境光 {type_data.get('ambient_light')}")
        structures = data.get("structure_sets") or []
        if structures:
            features.append("结构：" + "、".join(str(item).split(":")[-1] for item in structures))

        dimension_title = _title(lang, "dimension", key)
        if data or type_data:
            description = (
                f"{dimension_title}："
                + (f"{time_text}，" if time_text else "")
                + f"天空 {sky_text}。"
            )
        else:
            description = f"{dimension_title}：还没有找到维度 JSON，可能只声明在代码里。"
        fields = _fields(
            _field("维度大小", size),
            _field("进入方式", "；".join(entry_bits)),
            _field("天空模样", f"{sky_text}；{time_text}" if time_text else sky_text),
            _field("群系", "、".join(_title(lang, 'biome', item) for item in biome_list) or "未配置"),
            _field("特性", "、".join(features)),
            _field("坐标缩放", type_data.get("coordinate_scale")),
            _field("环境光", type_data.get("ambient_light")),
            _field("怪物生成光照上限", type_data.get("monster_spawn_light_level")),
            _field("结构", "、".join(str(item).split(":")[-1] for item in structures)),
            _field("数据状态", "" if (data or type_data) else "未找到 dimension JSON"),
        )
        result.append(
            _entry(
                "dimension",
                key,
                dimension_title,
                description=description,
                fields=fields,
                model_kind="sky",
                sky={"sky": sky_colors[0] if sky_colors else "#161226", "fog": sky_colors[-1] if sky_colors else "#0b0913"},
            )
        )
    return result


# --------------------------------------------------------------------------- #
# 扫描入口
# --------------------------------------------------------------------------- #
def _project_meta(root: Path) -> dict[str, str]:
    try:
        project = project_mod.load_project(root)
    except Exception:  # noqa: BLE001 - 没有元数据也要能扫
        project = None
    if project is not None:
        meta = project.meta
        modid = meta.mod_id or root.name
        return {
            "root": str(root),
            "name": meta.name or modid,
            "modid": modid,
            "loader": meta.loader or "",
            "mc": meta.mc_version or "",
        }
    modid = re.sub(r"[^a-z0-9_]", "_", root.name.lower()) or "mod"
    return {"root": str(root), "name": root.name, "modid": modid, "loader": "", "mc": ""}


def scan(root: str | Path) -> dict[str, Any]:
    """扫描工程并返回 ``{project, entries, counts}``。"""
    base = Path(root).expanduser().resolve()
    meta = _project_meta(base)
    modid = meta["modid"]
    asset_dirs = _asset_dirs(base, modid)
    lang = _lang_map(base, modid)
    files = _java_files(base)
    classes = _class_map(files)
    biomes = _biome_json(base, modid)
    dimensions = _dimension_data(base, modid)
    dim_types = _dimension_types(base, modid)

    entities = _scan_entities(base, modid, asset_dirs, lang, classes, biomes, dimensions)
    blocks = _scan_blocks(base, modid, asset_dirs, lang, classes)
    block_names = {item["key"] for item in blocks}
    items = _scan_items(base, modid, asset_dirs, lang, classes, block_names)
    biome_entries = _scan_biomes(base, modid, lang, biomes, dimensions, dim_types)
    dimension_entries = _scan_dimensions(base, modid, lang, dimensions, dim_types, biomes)

    entries: list[dict[str, Any]] = []
    for bucket in (entities, blocks, items, biome_entries, dimension_entries):
        entries.extend(bucket)
    counts = {kind: sum(1 for item in entries if item["kind"] == kind) for kind in KINDS}
    return {"project": meta, "entries": entries, "counts": counts}


# --------------------------------------------------------------------------- #
# 覆盖层
# --------------------------------------------------------------------------- #
def library_path(root: str | Path) -> Path:
    return Path(root) / ".dma" / LIBRARY_FILE


def _read_library(root: Path) -> dict[str, Any]:
    path = library_path(root)
    if not _exists(path):
        return {"schema": 1, "entries": {}, "custom_entries": []}
    data = _read_json(path)
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    if not isinstance(data.get("custom_entries"), list):
        data["custom_entries"] = []
    return data


def _write_library(root: Path, data: dict[str, Any]) -> None:
    path = library_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data["schema"] = 1
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _apply_override(entry: dict[str, Any], patch: dict[str, Any]) -> None:
    if not isinstance(patch, dict):
        return
    for key in ("title", "description", "model", "texture"):
        if key in patch and str(patch.get(key) or "").strip():
            entry[key] = str(patch[key])
    if isinstance(patch.get("fields"), dict):
        fields = list(entry.get("fields") or [])
        index = {item["key"]: pos for pos, item in enumerate(fields)}
        for name, value in patch["fields"].items():
            text = str(value or "").strip()
            if name in index:
                fields[index[name]]["value"] = text
            elif text:
                fields.append({"key": str(name), "value": text})
        entry["fields"] = fields
    if isinstance(patch.get("custom_fields"), list):
        entry["custom_fields"] = [
            {"key": str(item.get("key") or ""), "value": str(item.get("value") or "")}
            for item in patch["custom_fields"]
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ]


def _normalize_custom(raw: dict[str, Any]) -> dict[str, Any]:
    kind = str(raw.get("kind") or "entity")
    if kind not in KINDS:
        kind = "entity"
    key = _snake(str(raw.get("key") or "new_entry"))
    fields: list[dict[str, str]] = []
    for item in raw.get("fields") or []:
        if isinstance(item, dict) and str(item.get("key") or "").strip():
            fields.append({"key": str(item["key"]), "value": str(item.get("value") or "")})
    return {
        "id": f"{kind}:{key}",
        "kind": kind,
        "key": key,
        "title": str(raw.get("title") or key),
        "description": str(raw.get("description") or ""),
        "fields": fields,
        "custom_fields": [],
        "model": str(raw.get("model") or ""),
        "texture": str(raw.get("texture") or ""),
        "model_kind": str(raw.get("model_kind") or ("entity" if kind == "entity" else ("sky" if kind in {"biome", "dimension"} else "custom"))),
        "source": "",
        "custom": True,
        "created_at": str(raw.get("created_at") or ""),
    }


def load(root: str | Path) -> dict[str, Any]:
    """扫描 + 应用 ``.dma/mod_library.json`` 里的用户修改。"""
    base = Path(root).expanduser().resolve()
    data = scan(base)
    library = _read_library(base)
    patches = library.get("entries") or {}
    for entry in data["entries"]:
        _apply_override(entry, patches.get(entry["id"], {}))
    custom = [_normalize_custom(item) for item in library.get("custom_entries") or [] if isinstance(item, dict)]
    existing = {entry["id"] for entry in data["entries"]}
    for entry in custom:
        if entry["id"] not in existing:
            data["entries"].append(entry)
    data["entries"].sort(key=lambda item: (KINDS.index(item["kind"]), item.get("title") or item["key"]))
    data["counts"] = {kind: sum(1 for item in data["entries"] if item["kind"] == kind) for kind in KINDS}
    return data


def save_entry(
    root: str | Path,
    entry_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    fields: dict[str, Any] | None = None,
    custom_fields: list[dict[str, Any]] | None = None,
    model: str | None = None,
    texture: str | None = None,
) -> None:
    base = Path(root).expanduser().resolve()
    library = _read_library(base)
    # 自定义条目直接改完整记录。
    for item in library.get("custom_entries") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "") == entry_id:
            if title is not None:
                item["title"] = str(title)
            if description is not None:
                item["description"] = str(description)
            if model is not None:
                item["model"] = str(model)
            if texture is not None:
                item["texture"] = str(texture)
            updates: dict[str, str] = {}
            if fields is not None:
                updates.update({str(key): str(value or "") for key, value in fields.items()})
            for entry_field in custom_fields or []:
                if isinstance(entry_field, dict) and str(entry_field.get("key") or "").strip():
                    updates[str(entry_field["key"])] = str(entry_field.get("value") or "")
            if updates:
                merged = list(item.get("fields") or [])
                index = {
                    str(field.get("key")): pos
                    for pos, field in enumerate(merged)
                    if isinstance(field, dict)
                }
                for name, value in updates.items():
                    text = str(value or "").strip()
                    if name in index and index[name] < len(merged):
                        merged[index[name]]["value"] = text
                    elif text:
                        merged.append({"key": str(name), "value": text})
                item["fields"] = merged
            _write_library(base, library)
            return

    patch = dict(library["entries"].get(entry_id) or {})
    if title is not None:
        patch["title"] = str(title)
    if description is not None:
        patch["description"] = str(description)
    if model is not None:
        patch["model"] = str(model)
    if texture is not None:
        patch["texture"] = str(texture)
    if fields is not None:
        patch["fields"] = {str(key): str(value or "") for key, value in fields.items()}
    if custom_fields is not None:
        patch["custom_fields"] = [
            {"key": str(item.get("key") or ""), "value": str(item.get("value") or "")}
            for item in custom_fields
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ]
    library["entries"][entry_id] = patch
    _write_library(base, library)


def create_custom_entry(
    root: str | Path,
    *,
    kind: str,
    key: str,
    title: str = "",
    description: str = "",
    fields: list[dict[str, Any]] | None = None,
    model: str = "",
    texture: str = "",
) -> dict[str, Any]:
    base = Path(root).expanduser().resolve()
    data = load(base)
    existing = {item["id"] for item in data["entries"]}
    clean_key = _snake(key)
    entry_id = f"{kind}:{clean_key}"
    if entry_id in existing:
        raise ValueError(f"已经存在同名条目：{entry_id}")
    if kind not in KINDS:
        raise ValueError("未知的条目类别。")
    library = _read_library(base)
    record = {
        "id": entry_id,
        "kind": kind,
        "key": clean_key,
        "title": title.strip() or clean_key,
        "description": description.strip(),
        "fields": [
            {"key": str(item.get("key")), "value": str(item.get("value") or "")}
            for item in fields or []
            if isinstance(item, dict) and str(item.get("key") or "").strip()
        ],
        "model": str(model or ""),
        "texture": str(texture or ""),
        "model_kind": "entity" if kind == "entity" else ("sky" if kind in {"biome", "dimension"} else "custom"),
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    library["custom_entries"].append(record)
    _write_library(base, library)
    return _normalize_custom(record)


def delete_custom_entry(root: str | Path, entry_id: str) -> bool:
    base = Path(root).expanduser().resolve()
    library = _read_library(base)
    before = len(library.get("custom_entries") or [])
    library["custom_entries"] = [
        item
        for item in library.get("custom_entries") or []
        if not (isinstance(item, dict) and str(item.get("id") or "") == entry_id)
    ]
    if len(library["custom_entries"]) == before:
        return False
    _write_library(base, library)
    return True


# --------------------------------------------------------------------------- #
# 给 Agent 的修改需求
# --------------------------------------------------------------------------- #
def _all_fields(entry: dict[str, Any]) -> list[dict[str, str]]:
    fields = [
        {"key": str(item.get("key") or ""), "value": str(item.get("value") or "")}
        for item in entry.get("fields") or []
        if isinstance(item, dict) and str(item.get("key") or "").strip()
    ]
    for item in entry.get("custom_fields") or []:
        if isinstance(item, dict) and str(item.get("key") or "").strip():
            fields.append({"key": str(item["key"]), "value": str(item.get("value") or "")})
    return fields


def entry_instruction(entry: dict[str, Any], note: str = "") -> str:
    """把条目整理成一段可直接发给 Agent 的中文需求。"""
    kind = KIND_LABELS.get(str(entry.get("kind")), str(entry.get("kind")))
    lines = [
        f"请更新这个工程里的{kind}「{entry.get('title') or entry.get('key')}」"
        f"（注册名 `{entry.get('key')}`）：",
        "",
        f"- 当前介绍：{str(entry.get('description') or '（空）').strip()}",
    ]
    for item in _all_fields(entry):
        lines.append(f"- {item['key']}：{item['value'] or '（空）'}")
    for field, label in (
        ("model", "模型文件"),
        ("texture", "贴图文件"),
        ("source", "主要源码"),
    ):
        value = str(entry.get(field) or "").strip()
        if value:
            lines.append(f"- {label}：{value}")
    if note.strip():
        lines.extend(["", "用户的补充要求：", note.strip()])
    lines.extend(
        [
            "",
            "请把这些设定真正落到代码、数据、资源和语言文件里，"
            "不要只改文档；如果新增了字段（例如自定义介绍里的条目），"
            "要同步在实体/方块/物品/群系/维度的逻辑和介绍中体现。"
            "完成后更新 `.dma/mod_library.json` 里的对应条目"
            "（保持现有 JSON 结构，只改这个条目的介绍、字段和自定义字段），"
            "并说明改了哪些文件。",
        ]
    )
    return "\n".join(lines)


def summary(entry: dict[str, Any]) -> str:
    """条目的一行摘要，供列表和提示使用。"""
    fields = {item["key"]: item["value"] for item in _all_fields(entry)}
    bits = [str(entry.get("title") or entry.get("key"))]
    for key in ("类别", "阵营", "所在维度", "生成概率", "交互", "维度大小"):
        if fields.get(key):
            bits.append(fields[key])
    return " · ".join(bits[:3])
