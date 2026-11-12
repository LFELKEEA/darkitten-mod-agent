"""Static audit: spec ↔ code ↔ resources consistency.

This is the cheapest and most effective quality gate for AI-generated mods:
most broken mods are not subtle logic bugs, they are missing models, wrong
language keys, misspelled resource locations and client classes loaded on the
server. All of those are detectable without launching the game.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .assets import data_dir, tag_dir
from .util import iter_files, read_json, write_json
from .versions import VersionProfile

SAFE_RL = re.compile(r"^[a-z0-9_.\-/]+$")


def _node_path(node_id: str) -> str:
    return node_id.split(":", 1)[-1]


def _needs_item_def(mc: str) -> bool:
    """1.21.4+ requires assets/<ns>/items/<id>.json next to models/item."""
    if not mc:
        return False
    if mc.startswith("26."):
        return True
    key = tuple(int(x) for x in re.findall(r"\d+", mc)[:3])
    return key >= (1, 21, 4)


def _load_lang(root: Path, modid: str) -> dict[str, str]:
    base = root / "src" / "main" / "resources" / "assets" / modid / "lang"
    return {
        "en_us": read_json(base / "en_us.json", {}) or {},
        "zh_cn": read_json(base / "zh_cn.json", {}) or {},
    }


def _texture_refs(model: dict[str, Any]) -> list[str]:
    refs = []
    for value in (model.get("textures") or {}).values():
        if isinstance(value, str) and not value.startswith("#"):
            refs.append(value)
    return refs


def audit(project: Path, profile: VersionProfile | None = None) -> dict[str, Any]:
    project = project.resolve()
    spec = read_json(project / "mod-spec.json")
    if not spec:
        return {
            "ok": False, "score": 0, "errors": 1, "warnings": 0,
            "blocking": ["缺少 mod-spec.json"], "issues": [],
            "stats": {"json_files": 0, "nodes": 0},
        }
    modid = spec["identity"]["modid"]
    mc = profile.mc if profile else (read_json(project / ".mcmod" / "state.json", {}) or {}).get("profile", {}).get("mc", "")
    loader = profile.loader if profile else (read_json(project / ".mcmod" / "state.json", {}) or {}).get("profile", {}).get("loader", "")
    res_root = project / "src" / "main" / "resources"
    asset_root = res_root / "assets" / modid
    data_root = res_root / "data" / modid
    lang = _load_lang(project, modid)
    issues: list[dict[str, Any]] = []
    blocking: list[str] = []

    def add(level: str, code: str, message: str, path: str = "") -> None:
        issues.append({"level": level, "code": code, "message": message, "path": path})
        if level == "error":
            blocking.append(message)

    # 1. JSON validity
    json_count = 0
    for path in iter_files(res_root, (".json",)):
        json_count += 1
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            add("error", "json-invalid", f"JSON 无法解析：{exc}", str(path))

    # 2. Per-node resource coverage
    for node in spec["content"]:
        nid = _node_path(node["id"])
        ntype = node["type"]
        en = lang["en_us"]
        zh = lang["zh_cn"]
        if ntype in {"item", "material", "food", "tool", "armor", "weapon", "artifact", "upgrade", "seed", "spawn_egg", "key", "fertilizer"}:
            key = f"item.{modid}.{nid}"
            if key not in en:
                add("error", "lang-missing", f"缺少英文语言键 {key}", nid)
            if key not in zh:
                add("error", "lang-missing", f"缺少中文语言键 {key}", nid)
            model = asset_root / "models" / "item" / f"{nid}.json"
            if not model.exists():
                add("error", "model-missing", f"缺少物品模型 {nid}", str(model))
            texture = asset_root / "textures" / "item" / f"{nid}.png"
            if not texture.exists() and model.exists():
                data = read_json(model, {}) or {}
                refs = _texture_refs(data)
                if refs:
                    ns, _, path = refs[0].partition(":")
                    if ns == modid:
                        texture = asset_root / "textures" / (path + ".png")
                if not texture.exists():
                    add("error", "texture-missing", f"缺少物品贴图 {nid}", str(texture))
            if _needs_item_def(mc):
                item_def = asset_root / "items" / f"{nid}.json"
                if not item_def.exists():
                    add("error", "item-def-missing", f"缺少物品定义（1.21.4+）{nid}", str(item_def))
        elif ntype in {"block", "ore", "storage_block", "machine", "energy", "decor", "portal_frame", "rail"}:
            for key in (f"block.{modid}.{nid}", f"item.{modid}.{nid}"):
                if key not in en:
                    add("error", "lang-missing", f"缺少英文语言键 {key}", nid)
                if key not in zh:
                    add("error", "lang-missing", f"缺少中文语言键 {key}", nid)
            blockstate = asset_root / "blockstates" / f"{nid}.json"
            block_model = asset_root / "models" / "block" / f"{nid}.json"
            item_model = asset_root / "models" / "item" / f"{nid}.json"
            texture = asset_root / "textures" / "block" / f"{nid}.png"
            if not blockstate.exists():
                add("error", "blockstate-missing", f"缺少方块状态 {nid}", str(blockstate))
            if not block_model.exists():
                add("error", "model-missing", f"缺少方块模型 {nid}", str(block_model))
            if not item_model.exists():
                add("error", "model-missing", f"缺少方块物品模型 {nid}", str(item_model))
            if _needs_item_def(mc) and not (asset_root / "items" / f"{nid}.json").exists():
                add("error", "item-def-missing", f"缺少方块物品定义（1.21.4+）{nid}",
                    str(asset_root / "items" / f"{nid}.json"))
            if not texture.exists():
                add("error", "texture-missing", f"缺少方块贴图 {nid}", str(texture))
        elif ntype == "crop":
            for age in range(4):
                if not (asset_root / "textures" / "block" / f"{nid}_stage{age}.png").exists():
                    add("error", "texture-missing", f"缺少作物阶段贴图 {nid} stage {age}")
        elif ntype in {"entity", "boss"}:
            key = f"entity.{modid}.{nid}"
            if key not in en or key not in zh:
                add("warning", "lang-missing", f"实体语言键缺失 {key}", nid)
        elif ntype == "sound":
            key = f"sound.{modid}.{nid}"
            if key not in en or key not in zh:
                add("warning", "lang-missing", f"音效语言键缺失 {key}", nid)
        elif ntype == "particle":
            if f"particle.{modid}.{nid}" not in en:
                add("warning", "lang-missing", f"粒子语言键缺失 particle.{modid}.{nid}", nid)
            particle_def = asset_root / "particles" / f"{nid}.json"
            if not particle_def.exists():
                add("error", "particle-missing", f"缺少粒子定义 {nid}", str(particle_def))
        elif ntype == "fluid":
            for key in (f"fluid.{modid}.{nid}", f"block.{modid}.{nid}", f"item.{modid}.{nid}_bucket"):
                if key not in en or key not in zh:
                    add("error", "lang-missing", f"缺少语言键 {key}", nid)
            for path in (
                asset_root / "blockstates" / f"{nid}.json",
                asset_root / "models" / "block" / f"{nid}.json",
                asset_root / "models" / "item" / f"{nid}_bucket.json",
                asset_root / "textures" / "block" / f"{nid}_still.png",
                asset_root / "textures" / "block" / f"{nid}_flow.png",
                asset_root / "textures" / "item" / f"{nid}_bucket.png",
            ):
                if not path.exists():
                    add("error", "fluid-asset-missing", f"流体资源缺失 {path.name}", str(path))

    if any(n["type"] == "sound" for n in spec["content"]):
        sounds_file = asset_root / "sounds.json"
        if not sounds_file.exists():
            add("error", "sound-missing", "缺少 sounds.json", str(sounds_file))
        else:
            sounds_data = read_json(sounds_file, {}) or {}
            for event in ("ui_click", "machine_hum", "boss_roar"):
                if event not in sounds_data:
                    add("warning", "sound-event-missing", f"sounds.json 缺少事件 {event}", str(sounds_file))

    # 3. Model texture references
    for path in iter_files(asset_root / "models", (".json",)):
        data = read_json(path, {}) or {}
        for ref in _texture_refs(data):
            ns, _, rel = ref.partition(":")
            if not _:
                ns, rel = "minecraft", ns
            if ns == "minecraft":
                continue
            target = res_root / "assets" / ns / "textures" / (rel + ".png")
            if not target.exists():
                add("error", "texture-ref", f"模型引用了不存在的贴图 {ref}", str(path))

    # 4. Resource location casing
    for path in iter_files(res_root, (".json",)):
        rel = str(path.relative_to(res_root)).replace("\\", "/")
        parts = rel.split("/")
        if parts[:1] == ["assets"] or parts[:1] == ["data"]:
            namespace = parts[1] if len(parts) > 1 else ""
            if not SAFE_RL.match(namespace):
                add("error", "rl-invalid", f"命名空间不是合法资源位置：{namespace}", rel)
            for part in parts[2:]:
                stem = part.rsplit(".", 1)[0] if "." in part else part
                if stem and not SAFE_RL.match(stem):
                    add("error", "rl-invalid", f"资源名含非法字符：{part}", rel)

    # 5. Registry coverage
    registry_file = next(iter((project / "src" / "main" / "java").rglob("ModRegistry.java")), None)
    registry_text = registry_file.read_text(encoding="utf-8", errors="replace") if registry_file else ""
    if not registry_file:
        add("error", "registry-missing", "找不到 ModRegistry.java")
    for node in spec["content"]:
        ntype = node["type"]
        nid = _node_path(node["id"])
        if ntype in {"block", "ore", "storage_block", "machine", "energy", "decor", "portal_frame", "rail",
                     "item", "material", "food", "tool", "armor", "weapon", "artifact",
                     "upgrade", "seed", "spawn_egg", "key", "fertilizer"} or (
                ntype == "fluid" and loader in {"forge", "neoforge"}):
            if f'"{nid}"' not in registry_text:
                add("error", "registry-missing", f"注册表缺少内容 {nid}", "ModRegistry.java")

    # 5b. Generated Java coverage for the common and advanced layers
    java_root = project / "src" / "main" / "java"
    top_java = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in java_root.rglob("*.java")
    )
    advanced_ok = True
    if mc:
        advanced_ok = tuple(int(x) for x in re.findall(r"\d+", mc)[:3]) < (1, 21, 2) \
            if not mc.startswith("26.") else False
    # 生成器可能因为版本 / 映射能力主动跳过高级 Java。跳过是已知边界，
    # 不应再报成“文件缺失”；把原因作为警告暴露出来即可。
    state = read_json(project / ".mcmod" / "state.json", {}) or {}
    advanced_skip = str(
        (state.get("assets") or {}).get("advanced", {}).get("skipped") or ""
    ).strip()
    if advanced_skip:
        advanced_ok = False
        add("warning", "java-skipped", advanced_skip)
    configs = [n for n in spec["content"] if n["type"] == "config"]
    sounds = [n for n in spec["content"] if n["type"] == "sound"]
    energies = [n for n in spec["content"] if n["type"] == "energy"]
    particles = [n for n in spec["content"] if n["type"] == "particle"]
    networks = [n for n in spec["content"] if n["type"] == "network"]
    fluids = [n for n in spec["content"] if n["type"] == "fluid"]
    if configs and not any(java_root.rglob("Config.java")):
        add("error", "java-missing", "配置节点缺少 Config.java")
    if configs and "Config.load()" not in top_java:
        add("error", "java-wiring", "主类没有调用 Config.load()")
    if sounds and not any(java_root.rglob("ModSounds.java")):
        add("error", "java-missing", "音效节点缺少 ModSounds.java")
    if sounds and "ModSounds.register" not in top_java:
        add("error", "java-wiring", "主类没有注册 ModSounds")
    if advanced_ok and energies:
        if not any(java_root.rglob("EnergyCableBlock.java")):
            add("error", "java-missing", "能量节点缺少 EnergyCableBlock.java")
        if not any(java_root.rglob("EnergyCableBlockEntity.java")):
            add("error", "java-missing", "能量节点缺少 EnergyCableBlockEntity.java")
        if not any(java_root.rglob("EnergyStorage.java")):
            add("error", "java-missing", "能量节点缺少 EnergyStorage.java")
    if advanced_ok and particles:
        if not any(java_root.rglob("ModParticles.java")):
            add("error", "java-missing", "粒子节点缺少 ModParticles.java")
        if "ModParticles.register" not in top_java:
            add("error", "java-wiring", "主类没有注册 ModParticles")
    if advanced_ok and networks:
        if not any(java_root.rglob("ModNetwork.java")):
            add("error", "java-missing", "网络节点缺少 ModNetwork.java")
        if "ModNetwork.register" not in top_java and "RegisterPayloadHandlersEvent" not in top_java:
            add("error", "java-wiring", "网络包没有接线到主类或事件总线")
    if advanced_ok and fluids and loader in {"forge", "neoforge"}:
        if not any(java_root.rglob("ModFluids.java")):
            add("error", "java-missing", "流体节点缺少 ModFluids.java")
        if "ModFluids.register" not in top_java:
            add("error", "java-wiring", "主类没有注册 ModFluids")
    if advanced_ok:
        tools = [n for n in spec["content"] if n["type"] in {"tool", "weapon"}]
        armors = [n for n in spec["content"] if n["type"] == "armor"]
        foods = [n for n in spec["content"] if n["type"] == "food"]
        if tools and "Tiers." not in top_java:
            add("error", "java-item", "工具节点没有生成带 Tier 的工具类")
        if armors and "ArmorItem" not in top_java:
            add("error", "java-item", "护甲节点没有生成 ArmorItem")
        if foods and "FoodProperties" not in top_java:
            add("error", "java-item", "食物节点没有生成 FoodProperties")

    # 6. Client/server separation
    for path in iter_files(project / "src" / "main" / "java", (".java",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "net.minecraft.client." in text and "Client" not in path.name and "/client/" not in str(path).replace("\\", "/"):
            add("warning", "client-import", "公共代码引用了客户端类，可能导致服务端崩溃", str(path))

    # 7. Metadata presence
    meta = res_root / "META-INF"
    if not any(meta.glob("*.toml")) and not (res_root / "fabric.mod.json").exists():
        add("error", "metadata-missing", "缺少 mods.toml / neoforge.mods.toml / fabric.mod.json")

    # 8. World generation integrity
    def data_path(identifier: str) -> Path | None:
        ns, _, rel = identifier.partition(":")
        if not _:
            ns, rel = modid, identifier
        if ns != modid:
            return None
        return data_root / rel

    dims = [n for n in spec["content"] if n["type"] == "dimension"]
    for node in dims:
        nid = _node_path(node["id"])
        dim_file = data_root / "dimension" / f"{nid}.json"
        type_file = data_root / "dimension_type" / f"{nid}_type.json"
        if not dim_file.exists():
            add("error", "worldgen-missing", f"缺少维度定义 {nid}", str(dim_file))
        if not type_file.exists():
            add("error", "worldgen-missing", f"缺少维度类型 {nid}_type", str(type_file))

    for node in spec["content"]:
        ntype = node["type"]
        nid = _node_path(node["id"])
        if ntype == "biome":
            path = data_root / "worldgen" / "biome" / f"{nid}.json"
            if not path.exists():
                add("error", "worldgen-missing", f"缺少群系 {nid}", str(path))

    feature_folder = "feature" if mc.startswith("26.") else "configured_feature"
    if any(n["type"] in {"ore", "tree", "crop"} for n in spec["content"]):
        if not (data_root / "worldgen" / feature_folder).exists():
            add("error", "worldgen-missing",
                f"缺少世界生成特征目录 {feature_folder}", str(data_root / "worldgen" / feature_folder))

    structure_folder = "structure" if (mc.startswith("26.") or (
        not mc.startswith("1.20") and tuple(int(x) for x in re.findall(r"\d+", mc)[:2] or [0, 0]) >= (1, 21))
    ) else "structures"
    for node in [n for n in spec["content"] if n["type"] == "structure"]:
        nid = _node_path(node["id"])
        structure = data_root / "worldgen" / "structure" / f"{nid}.json"
        structure_set = data_root / "worldgen" / "structure_set" / f"{nid}.json"
        pool = data_root / "worldgen" / "template_pool" / nid / "start_pool.json"
        for path in (structure, structure_set, pool):
            if not path.exists():
                add("error", "worldgen-missing", f"缺少结构文件 {nid}", str(path))
        if not structure.exists():
            continue
        data = read_json(structure, {}) or {}
        start_pool = data.get("start_pool")
        if isinstance(start_pool, str):
            namespace, _, rel = start_pool.partition(":")
            if not _:
                namespace, rel = modid, start_pool
            if namespace == modid and not (data_root / "worldgen" / "template_pool" / (rel + ".json")).exists():
                add("error", "worldgen-ref", f"结构 {nid} 的 start_pool 不存在：{start_pool}")
        pool_data = read_json(pool, {}) or {}
        for element in pool_data.get("elements", []):
            inner = element.get("element", {}) if isinstance(element, dict) else {}
            location = inner.get("location")
            if not isinstance(location, str):
                continue
            target = data_path(location)
            if target is None:
                continue
            nbt_file = data_root / structure_folder / (str(target.relative_to(data_root)) + ".nbt")
            if not nbt_file.exists():
                add("error", "worldgen-ref", f"结构模板不存在：{location}", str(nbt_file))
                continue
            try:
                raw = nbt_file.read_bytes()
                if len(raw) < 8 or raw[:2] != b"\x1f\x8b":
                    add("error", "nbt-invalid", f"结构模板不是 gzip：{nbt_file.name}", str(nbt_file))
                else:
                    import gzip
                    payload = gzip.decompress(raw)
                    for marker in (b"DataVersion", b"palette", b"blocks"):
                        if marker not in payload:
                            add("error", "nbt-invalid",
                                f"结构模板缺少 {marker.decode()}：{nbt_file.name}", str(nbt_file))
            except (OSError, ValueError) as exc:
                add("error", "nbt-invalid", f"结构模板无法读取：{exc}", str(nbt_file))

    # 9. Social data integrity
    social_nodes = [n for n in spec["content"] if n["type"] in {"npc", "quest", "dialogue", "faction", "ritual"}]
    if social_nodes:
        manifest = read_json(project / ".mcmod" / "social-manifest.json")
        if not manifest:
            add("error", "social-missing", "缺少 social-manifest.json")
        function_root = data_root / "function" / "social"
        for node in [n for n in spec["content"] if n["type"] == "npc"]:
            nid = _node_path(node["id"])
            if not (function_root / "npc" / nid / "root.mcfunction").exists():
                add("error", "social-missing", f"NPC 对话入口不存在：{nid}",
                    str(function_root / "npc" / nid / "root.mcfunction"))
        for node in [n for n in spec["content"] if n["type"] == "quest"]:
            nid = _node_path(node["id"])
            if not (function_root / "quest" / nid / "reward.mcfunction").exists():
                add("error", "social-missing", f"任务奖励函数不存在：{nid}",
                    str(function_root / "quest" / nid / "reward.mcfunction"))
        load_tag = res_root / "data" / "minecraft" / tag_dir(mc, "function") / "load.json"
        if not load_tag.exists():
            add("error", "social-missing", "缺少 datapack load 函数标签", str(load_tag))
        root_adv = data_root / data_dir(mc, "advancement") / "social" / "root.json"
        if not root_adv.exists():
            add("error", "social-missing", "缺少社交进度根节点", str(root_adv))

    # 10. Generated header safety
    if registry_file and "Generated by mcmod-studio" not in registry_text:
        add("warning", "registry-hand-edited", "ModRegistry.java 可能被手工修改，重新生成会覆盖", str(registry_file))

    errors = sum(1 for i in issues if i["level"] == "error")
    warnings = sum(1 for i in issues if i["level"] == "warning")
    score = max(0, 100 - errors * 8 - warnings * 2)
    report = {
        "ok": errors == 0,
        "score": score,
        "errors": errors,
        "warnings": warnings,
        "blocking": sorted(set(blocking)),
        "issues": issues,
        "stats": {"json_files": json_count, "nodes": len(spec["content"])},
        "mc": mc,
    }
    write_json(project / ".mcmod" / "audit-report.json", report)
    return report


def summarize(report: dict[str, Any]) -> str:
    lines = [
        f"审计结果：{'通过' if report.get('ok') else '未通过'}  分数 {report.get('score', 0)}/100",
        f"错误 {report.get('errors', 0)}，警告 {report.get('warnings', 0)}，"
        f"节点 {report.get('stats', {}).get('nodes', 0)}",
    ]
    if report.get("blocking"):
        lines.append("阻断项：")
        lines += [f"  - {b}" for b in report["blocking"][:20]]
    if report.get("issues"):
        lines.append("问题明细：")
        for issue in report["issues"][:30]:
            lines.append(f"  [{issue['level']}] {issue['code']}: {issue['message']}")
    return "\n".join(lines)
