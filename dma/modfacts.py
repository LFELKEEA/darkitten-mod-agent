"""版本与加载器事实层：把跨版本差异固化成可查询、可校验的数据。

模型最容易犯的错误不是写不出 Java，而是把某个版本的写法套到另一个版本：
1.20.1 的 ``mods.toml`` 塞进 NeoForge 1.21.1；把 1.21 的单数数据目录写成
``recipes``；Java 21 的工程用 Java 17 编译；26.x 还在写 ``new ResourceLocation``。
这些错误编译期未必报，运行期直接崩，靠提示词里一句“注意版本”压不住。

本模块做三件事：
- ``brief``：给系统提示词注入当前工程的版本事实（Java、元数据、数据目录、插件、ID API）；
- ``check_project``：静态扫描工程，找出与目标版本冲突的文件/配置，输出可执行修复建议；
- ``matrix``：给工具调用返回一份加载器 × 版本的已知基线，缺资料时明确要求在线核实。

事实来源：Mojang 版本清单、NeoForge/Forge/Fabric 官方文档与示例工程。
不确定的条目一律标 ``verify``，禁止把猜测写成事实。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 版本号解析：只接受数字开头的正式版本；快照（24w14a）不猜特性。
_VERSION_RE = re.compile(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[.-].*)?$")
_SNAPSHOT_RE = re.compile(r"^\d{2}w\d{2}[a-z]$", re.IGNORECASE)


def version_tuple(text: str) -> tuple[int, ...]:
    """把版本串转成可比较元组；无法识别返回空元组（按未知处理）。"""
    value = (text or "").strip()
    if not value or _SNAPSHOT_RE.match(value):
        return ()
    match = _VERSION_RE.match(value)
    if not match:
        return ()
    parts = [int(item) for item in match.groups() if item is not None]
    return tuple(parts) if parts else ()


def _at_least(mc: str, target: tuple[int, ...]) -> bool:
    current = version_tuple(mc)
    return bool(current) and current >= target


def _numeric(text: str) -> str:
    return ".".join(str(item) for item in version_tuple(text))


def family(mc: str) -> str:
    """版本家族，用于选择资源目录/元数据/构建插件。"""
    parsed = version_tuple(mc)
    if not parsed:
        return "unknown"
    if parsed[0] >= 26:
        return "26.x"
    if parsed < (1, 20, 1):
        return "legacy"
    if parsed < (1, 20, 2):
        return "1.20.1"
    if parsed < (1, 20, 5):
        return "1.20.2-1.20.4"
    if parsed < (1, 21):
        return "1.20.5-1.20.6"
    if parsed < (1, 21, 2):
        return "1.21-1.21.1"
    if parsed < (1, 21, 4):
        return "1.21.2-1.21.3"
    if parsed < (1, 21, 5):
        return "1.21.4"
    if parsed < (26,):
        return "1.21.5+"
    return "26.x"


@dataclass(frozen=True)
class VersionFacts:
    mc: str
    family: str
    java: int
    mappings: str
    metadata: str
    data_singular: bool
    item_definitions: bool
    id_api: str
    loader_plugins: dict[str, str]
    notes: tuple[str, ...] = ()
    confidence: str = "official"

    def to_dict(self) -> dict[str, Any]:
        return {
            "mc": self.mc,
            "family": self.family,
            "java": self.java,
            "mappings": self.mappings,
            "metadata": self.metadata,
            "data_singular": self.data_singular,
            "item_definitions": self.item_definitions,
            "id_api": self.id_api,
            "loader_plugins": dict(self.loader_plugins),
            "notes": list(self.notes),
            "confidence": self.confidence,
        }


JAVA_BY_CLASS_MAJOR = {52: 8, 53: 9, 54: 10, 55: 11, 56: 12, 57: 13, 58: 14,
                       59: 15, 60: 16, 61: 17, 62: 18, 63: 19, 64: 20, 65: 21,
                       66: 22, 67: 23, 68: 24, 69: 25}


def java_for(mc: str) -> int:
    """Minecraft 版本对应的 Java 主版本。"""
    if _at_least(mc, (26,)):
        return 25
    if _at_least(mc, (1, 20, 5)):
        return 21
    return 17


def metadata_for(loader: str, mc: str) -> str:
    name = (loader or "").strip().lower()
    if name.startswith("neo") and _at_least(mc, (1, 20, 5)):
        return "META-INF/neoforge.mods.toml"
    return "META-INF/mods.toml"


def data_dirs(mc: str) -> dict[str, str]:
    """数据包顶层目录：1.21 起从复数改为单数。"""
    if _at_least(mc, (1, 21)):
        return {
            "recipe": "recipe",
            "loot": "loot_table",
            "advancement": "advancement",
            "block_tag": "tags/block",
            "item_tag": "tags/item",
            "function": "function",
            "structure": "structure",
            "predicate": "predicate",
        }
    return {
        "recipe": "recipes",
        "loot": "loot_tables",
        "advancement": "advancements",
        "block_tag": "tags/blocks",
        "item_tag": "tags/items",
        "function": "functions",
        "structure": "structures",
        "predicate": "predicates",
    }


def mappings_for(loader: str, mc: str) -> str:
    name = (loader or "").strip().lower()
    if name.startswith("fabric") or name.startswith("quilt"):
        # 26.x 官方映射、未混淆；Loom 走非 remap 路径。
        return "official（26.x 未混淆）" if _at_least(mc, (26,)) else "yarn（或 mojang/official）"
    return "official（Mojang）"


def id_api_for(mc: str) -> str:
    """注册 ID 的构造方式，随官方映射改名而变化。"""
    if _at_least(mc, (1, 21, 9)) or _at_least(mc, (26,)):
        return "Identifier.fromNamespaceAndPath(ns, path)"
    if _at_least(mc, (1, 21, 2)):
        return "ResourceLocation.fromNamespaceAndPath(ns, path)"
    return "new ResourceLocation(ns, path) / ResourceLocation.tryBuild(ns, path)"


def loader_plugins(loader: str, mc: str) -> dict[str, str]:
    name = (loader or "").strip().lower()
    if name.startswith("neo"):
        if _at_least(mc, (1, 20, 2)):
            return {"primary": "net.neoforged.moddev（ModDevGradle 2.x）",
                    "legacy": "net.neoforged.gradle.userdev（NeoGradle，1.20.1 常用）"}
        return {"primary": "net.neoforged.gradle.userdev（NeoGradle）"}
    if name.startswith("forge"):
        return {"primary": "net.minecraftforge.gradle（ForgeGradle）"}
    if name.startswith("quilt"):
        return {"primary": "org.quiltmc.loom"}
    if name.startswith("fabric"):
        return {"primary": "fabric-loom" + ("（26.x 非 remap）" if _at_least(mc, (26,)) else "")}
    return {}


def facts_for(loader: str, mc: str) -> VersionFacts:
    notes: list[str] = []
    parsed = version_tuple(mc)
    confidence = "official"
    if not parsed:
        confidence = "unknown"
        notes.append("版本号无法解析（可能是快照）；所有差异必须在线核对官方文档/源码。")
    elif parsed[0] >= 26:
        notes.append("26.x 使用 Java 25、未混淆官方名与非 remap Loom；不要套用 1.21.x 的映射/remap 配置。")
    if _at_least(mc, (1, 21, 4)):
        notes.append("1.21.4+ 物品模型需要 assets/<ns>/items/<id>.json 物品定义，指向 models/item 模型。")
    if _at_least(mc, (1, 21)):
        notes.append("1.21+ 数据包目录改为单数（recipe/loot_table/tags/block/advancement）。")
    if _at_least(mc, (1, 20, 5)):
        notes.append("1.20.5+ NeoForge 元数据改名 META-INF/neoforge.mods.toml。")
    if (loader or "").lower().startswith("neo") and parsed and parsed < (1, 20, 2):
        notes.append("NeoForge 1.20.1 走 net.neoforged:forge 旧线，按 Forge 兼容写法处理。")
    return VersionFacts(
        mc=_numeric(mc) or (mc or "").strip(),
        family=family(mc),
        java=java_for(mc),
        mappings=mappings_for(loader, mc),
        metadata=metadata_for(loader, mc),
        data_singular=_at_least(mc, (1, 21)),
        item_definitions=_at_least(mc, (1, 21, 4)),
        id_api=id_api_for(mc),
        loader_plugins=loader_plugins(loader, mc),
        notes=tuple(notes),
        confidence=confidence,
    )


def brief(loader: str, mc: str) -> str:
    """系统提示词用的紧凑事实块。"""
    if not (loader or mc):
        return ""
    item = facts_for(loader, mc)
    dirs = data_dirs(mc)
    plugins = "；".join(f"{key}={value}" for key, value in item.loader_plugins.items())
    lines = [
        "【版本事实（静态基线，冲突时以官方 sources 为准）】",
        f"- MC {item.mc or '未填写'}（{item.family}）→ Java {item.java}；映射 {item.mappings}",
        f"- 元数据：{item.metadata}",
        f"- 数据目录：recipe={dirs['recipe']}，loot={dirs['loot']}，"
        f"block_tag={dirs['block_tag']}，item_tag={dirs['item_tag']}",
        f"- 物品定义目录：{'assets/<ns>/items（必需）' if item.item_definitions else '不需要（用 models/item）'}",
        f"- ID API：{item.id_api}",
    ]
    if plugins:
        lines.append(f"- 构建插件：{plugins}")
    if item.confidence != "official":
        lines.append("- 置信度：需在线核实（版本未知或快照）")
    for note in item.notes:
        lines.append(f"- 注意：{note}")
    return "\n".join(lines)


def matrix() -> list[dict[str, Any]]:
    """加载器 × 版本基线。用于工具输出；不确定的明确写“在线核实”。"""
    return [
        {"loader": "fabric", "lanes": "1.14 → 26.x", "java": "17 / 21 / 25 按版本",
         "plugin": "fabric-loom", "metadata": "fabric.mod.json",
         "note": "26.x 未混淆、非 remap；旧版本用 Yarn 或 Mojang 映射。"},
        {"loader": "quilt", "lanes": "1.14 → 1.21.x（26.x 视 Quilt 进度）",
         "java": "17 / 21", "plugin": "org.quiltmc.loom", "metadata": "quilt.mod.json",
         "note": "26.x 支持情况在线核实 Quilt 官方。"},
        {"loader": "forge", "lanes": "1.20.1 与 1.21.x 主线稳定；更新版本在线核实",
         "java": "17（1.20.1）/ 21（1.21.x）", "plugin": "net.minecraftforge.gradle",
         "metadata": "META-INF/mods.toml",
         "note": "ForgeGradle 6.x 对应 1.20.1；新版本核对其插件版本。"},
        {"loader": "neoforge", "lanes": "1.20.2 → 26.x；1.20.1 为旧线",
         "java": "21（1.20.5+）/ 25（26.x）", "plugin": "ModDevGradle（1.20.2+）/ NeoGradle（1.20.1）",
         "metadata": "mods.toml（≤1.20.4）/ neoforge.mods.toml（1.20.5+）",
         "note": "NeoForge 版本号与 MC 对应（如 21.1.x ↔ 1.21.1）。"},
        {"loader": "architectury", "lanes": "跟随 Fabric + NeoForge 支持的版本",
         "java": "按目标 MC", "plugin": "architectury-loom + architectury-plugin",
         "metadata": "各平台子项目各自维护",
         "note": "版本必须与目标 MC 对齐，从官方模板生成器取同一套 pin。"},
        {"loader": "stonecutter", "lanes": "单代码库跨多个 MC 版本",
         "java": "按各目标 MC", "plugin": "dev.kikugie.stonecutter",
         "metadata": "各目标子项目维护",
         "note": "预处理指令按目标版本裁剪代码；不是加载器。"},
    ]


# ---------------------------------------------------------------------------
# 工程静态核查
# ---------------------------------------------------------------------------

@dataclass
class FactIssue:
    severity: str  # error / warn / info
    rule: str
    message: str
    fix: str
    path: str = ""

    def line(self) -> str:
        tag = {"error": "错误", "warn": "警告", "info": "提示"}.get(self.severity, self.severity)
        where = f"（{self.path}）" if self.path else ""
        return f"- [{tag}] {self.message}{where}\n  → {self.fix}"


def _read(path: Path, limit: int = 400_000) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _scan_java(root: Path, needles: tuple[str, ...], limit: int = 600) -> list[str]:
    hits: list[str] = []
    base = root / "src"
    if not base.is_dir():
        return hits
    count = 0
    for path in base.rglob("*.java"):
        if "reference" in path.parts or "build" in path.parts:
            continue
        count += 1
        if count > limit:
            break
        text = _read(path, 200_000)
        for needle in needles:
            if needle in text:
                hits.append(str(path.relative_to(root)).replace("\\", "/"))
                break
    return hits


_JAVA_PATTERNS = (
    re.compile(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)"),
    re.compile(r"(?:source|target)Compatibility\s*=\s*['\"]?(?:JavaVersion\.VERSION_)?(\d+)"),
    re.compile(r"JavaVersion\.VERSION_(\d+)"),
    re.compile(r"jvmToolchain\(\s*(\d+)\s*\)"),
    re.compile(r"(?:^|\n)\s*java_version\s*=\s*(\d+)", re.IGNORECASE),
)


def _declared_java(text: str) -> int | None:
    for pattern in _JAVA_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1))
            except (TypeError, ValueError):
                continue
    return None


def _subdirs(path: Path) -> list[str]:
    if not path.is_dir():
        return []
    return sorted(item.name for item in path.iterdir() if item.is_dir())


def _resource_namespaces(root: Path) -> list[str]:
    data = root / "src" / "main" / "resources" / "data"
    return _subdirs(data)


def _version_in_range(mc: str, raw: str) -> bool | None:
    """解析 Maven 风格区间，只支持单区间；无法解析返回 None。"""
    current = version_tuple(mc)
    if not current or not raw:
        return None
    match = re.match(r"^\s*([\[(])\s*([^,\]\)]*)\s*,\s*([^\]\)]*)\s*([\])])\s*$", raw)
    if not match:
        return None
    low_open, low_raw, high_raw, high_close = match.groups()
    low = version_tuple(low_raw) if low_raw else ()
    high = version_tuple(high_raw) if high_raw else ()
    if low:
        if current < low or (current == low and low_open == "("):
            return False
    if high:
        if current > high or (current == high and high_close == ")"):
            return False
    return True


def check_project(root: Path | str, loader: str = "", mc: str = "") -> list[FactIssue]:
    """扫描工程，返回与目标版本/加载器冲突的静态问题。"""
    base = Path(root)
    issues: list[FactIssue] = []
    if not base.is_dir():
        return [FactIssue("error", "project.missing", "工程目录不存在。", "确认工程路径后重试。")]
    name = (loader or "").strip().lower()
    facts = facts_for(name, mc)
    build_text = "\n".join(
        _read(base / item) for item in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
    )
    props_text = _read(base / "gradle.properties")
    combined = build_text + "\n" + props_text

    # 1. Java 版本
    declared = _declared_java(combined)
    if declared is not None and facts.confidence == "official" and declared != facts.java:
        issues.append(FactIssue(
            "error", "java.mismatch",
            f"工程声明 Java {declared}，但 MC {facts.mc} 需要 Java {facts.java}。",
            f"把 toolchain / sourceCompatibility 改为 {facts.java}；"
            f"并在 gradle.properties 指向 JDK {facts.java}。",
            "build.gradle / gradle.properties",
        ))

    # 2. 元数据文件名
    meta_root = base / "src" / "main" / "resources" / "META-INF"
    expected = facts.metadata
    if (name.startswith("neo") or name.startswith("forge")) and meta_root.is_dir():
        expected_name = Path(expected).name
        present = {item.name for item in meta_root.glob("*.toml")}
        if expected_name not in present and present:
            issues.append(FactIssue(
                "error", "metadata.name",
                f"MC {facts.mc} 的 {name} 元数据应为 {expected_name}，当前是 {', '.join(sorted(present))}。",
                f"重命名为 {expected_name}，并同步 processResources 里的展开规则。",
                "src/main/resources/META-INF",
            ))
        elif not present:
            issues.append(FactIssue(
                "warn", "metadata.missing",
                f"没有找到 {expected_name}，加载器不会识别这个模组。",
                f"补上 {expected_name}，写清 modId、version、依赖版本区间。",
                "src/main/resources/META-INF",
            ))

    # 3. 数据目录单复数
    namespaces = _resource_namespaces(base)
    plural = {"recipes", "loot_tables", "advancements", "functions", "structures", "predicates"}
    singular = {"recipe", "loot_table", "advancement", "function", "structure", "predicate"}
    for namespace in namespaces:
        data_dir = base / "src" / "main" / "resources" / "data" / namespace
        dirs = set(_subdirs(data_dir))
        if facts.data_singular:
            wrong = sorted(dirs & plural)
            if wrong:
                issues.append(FactIssue(
                    "error", "data.plural",
                    f"MC {facts.mc} 使用单数数据目录，但 {namespace} 下出现 {', '.join(wrong)}。",
                    "改为 recipe / loot_table / advancement / function / structure / predicate。",
                    f"data/{namespace}",
                ))
            for old, new in (("tags", None),):
                tags = data_dir / old
                if tags.is_dir():
                    sub = set(_subdirs(tags))
                    wrong_tags = sorted(sub & {"blocks", "items", "entity_types", "fluids", "functions"})
                    if wrong_tags:
                        issues.append(FactIssue(
                            "error", "data.tags",
                            f"MC {facts.mc} 的标签目录应为 block/item/entity_type/fluid/function，"
                            f"但出现 {', '.join(wrong_tags)}。",
                            "把 tags/blocks → tags/block，tags/items → tags/item。",
                            f"data/{namespace}/tags",
                        ))
        else:
            wrong = sorted(dirs & singular)
            if wrong:
                issues.append(FactIssue(
                    "error", "data.singular",
                    f"MC {facts.mc} 使用复数数据目录，但 {namespace} 下出现 {', '.join(wrong)}。",
                    "改为 recipes / loot_tables / advancements / functions / structures / predicates。",
                    f"data/{namespace}",
                ))

    # 4. 1.21.4+ 物品定义
    if facts.item_definitions:
        assets = base / "src" / "main" / "resources" / "assets"
        for namespace in _subdirs(assets):
            ns_dir = assets / namespace
            has_models = (ns_dir / "models" / "item").is_dir() and any(
                (ns_dir / "models" / "item").glob("*.json")
            )
            has_defs = (ns_dir / "items").is_dir() and any((ns_dir / "items").glob("*.json"))
            if has_models and not has_defs:
                issues.append(FactIssue(
                    "warn", "assets.item_definitions",
                    f"{namespace} 有 models/item，但缺少 assets/{namespace}/items 物品定义。",
                    "为每个物品补 assets/<ns>/items/<id>.json：{\"model\": {\"type\": \"minecraft:model\", "
                    "\"model\": \"<ns>:item/<id>\"}}。",
                    f"assets/{namespace}",
                ))
                break

    # 5. 加载器 API 混用
    if name.startswith("fabric") or name.startswith("quilt"):
        hits = _scan_java(base, ("net.minecraftforge", "net.neoforged"))
        if hits:
            issues.append(FactIssue(
                "error", "loader.api_mix",
                f"{name} 工程里出现 Forge/NeoForge API。",
                "把平台代码隔离到对应平台子项目；公共代码只依赖原版与 Fabric API。",
                hits[0],
            ))
    elif name.startswith("forge"):
        hits = _scan_java(base, ("net.fabricmc", "net.neoforged"))
        if hits:
            issues.append(FactIssue(
                "error", "loader.api_mix",
                "Forge 工程里出现 Fabric/NeoForge API。",
                "Forge 工程只能使用 net.minecraftforge；NeoForge 代码放到独立工程或平台模块。",
                hits[0],
            ))
    elif name.startswith("neo"):
        hits = _scan_java(base, ("net.fabricmc", "net.minecraftforge"))
        if hits:
            issues.append(FactIssue(
                "error", "loader.api_mix",
                "NeoForge 工程里出现 Fabric/Forge API。",
                "NeoForge 使用 net.neoforged；跨加载器请用 Architectury/MultiLoader 分层。",
                hits[0],
            ))

    # 6. 混淆名
    obf_hits: list[str] = []
    for path in (base / "src").rglob("*.java") if (base / "src").is_dir() else []:
        if "reference" in path.parts or "build" in path.parts:
            continue
        text = _read(path, 200_000)
        if re.search(r"\b(?:class|method|field)_\d+_?\b", text):
            obf_hits.append(str(path.relative_to(base)).replace("\\", "/"))
            if len(obf_hits) >= 3:
                break
    if obf_hits:
        issues.append(FactIssue(
            "warn", "mapping.obfuscated",
            "源码里出现 Yarn 混淆名（class_/method_/field_）。",
            "改用目标版本的官方名；先 dev_docs lookup 或读 sources.jar 确认签名。",
            obf_hits[0],
        ))

    # 7. 26.x 专项
    parsed = version_tuple(mc)
    if parsed and parsed[0] >= 26:
        if re.search(r"new\s+ResourceLocation\s*\(", build_text):
            pass
        hits = _scan_java(base, ("new ResourceLocation(",))
        if hits:
            issues.append(FactIssue(
                "error", "26x.identifier",
                "26.x 已使用 Identifier，不能再 new ResourceLocation。",
                f"改为 {facts.id_api}；同时核对注册表与数据组件 API。",
                hits[0],
            ))
        if "yarn_mappings" in combined or re.search(r"mappings\s+loom\.officialMojangMappings", combined):
            issues.append(FactIssue(
                "error", "26x.mappings",
                "26.x 未混淆，不应再配置 Yarn 映射或 remap 映射链。",
                "按官方 26.x 模板使用非 remap Loom 与 official 名称。",
                "build.gradle / gradle.properties",
            ))

    # 8. Mixin 配置
    mixin_hits = _scan_java(base, ("org.spongepowered.asm.mixin",))
    mixin_configs = list((base / "src" / "main" / "resources").glob("*.mixins.json")) if (
        base / "src" / "main" / "resources"
    ).is_dir() else []
    if mixin_hits and not mixin_configs:
        issues.append(FactIssue(
            "error", "mixin.config",
            "源码使用了 Mixin，但 resources 下没有 *.mixins.json。",
            "新建 mixins.json 并在加载器元数据/构建配置里注册；空配置等于没注入。",
            mixin_hits[0],
        ))

    # 9. Access Widener / Transformer
    src_root = base / "src"
    aw_files = list(src_root.rglob("*.accesswidener")) if src_root.is_dir() else []
    if aw_files:
        fabric_meta = base / "src" / "main" / "resources" / "fabric.mod.json"
        text = _read(fabric_meta)
        if fabric_meta.is_file() and "accessWidener" not in text:
            issues.append(FactIssue(
                "error", "fabric.accesswidener",
                "存在 .accesswidener，但 fabric.mod.json 没有声明 accessWidener。",
                "在 fabric.mod.json 加 \"accessWidener\": \"<modid>.accesswidener\"。",
                "src/main/resources/fabric.mod.json",
            ))
    at_files = list(src_root.rglob("accesstransformer.cfg")) if src_root.is_dir() else []
    if at_files and not re.search(r"accessTransformer|accesstransformer", combined, re.IGNORECASE):
        issues.append(FactIssue(
            "warn", "forge.accesstransformer",
            "存在 accesstransformer.cfg，但构建脚本没有注册 Access Transformer。",
            "在 ForgeGradle/ModDevGradle 配置里声明 accessTransformer 文件。",
            str(at_files[0].relative_to(base)).replace("\\", "/"),
        ))

    # 10. 元数据里的 Minecraft 版本区间
    for meta_name in ("mods.toml", "neoforge.mods.toml"):
        path = meta_root / meta_name
        if not path.is_file():
            continue
        text = _read(path)
        for block in re.findall(r"\[\[dependencies[^\]]*\]\]([\s\S]*?)(?=\n\[\[|\Z)", text):
            if "modId" in block and "minecraft" in block:
                match = re.search(r'versionRange\s*=\s*"([^"]+)"', block)
                if match:
                    ok = _version_in_range(facts.mc, match.group(1))
                    if ok is False:
                        issues.append(FactIssue(
                            "warn", "metadata.version_range",
                            f"{meta_name} 的 minecraft versionRange={match.group(1)} 不包含 {facts.mc}。",
                            f"把区间改为包含 {facts.mc}（如 \"[{facts.mc},)\"），或确认目标版本。",
                            f"src/main/resources/META-INF/{meta_name}",
                        ))
                break

    # 11. pack.mcmeta
    pack = base / "src" / "main" / "resources" / "pack.mcmeta"
    if (base / "src" / "main" / "resources").is_dir() and not pack.is_file():
        issues.append(FactIssue(
            "info", "resources.pack_mcmeta",
            "缺少 pack.mcmeta。",
            "补上 pack.mcmeta（pack_format 按目标版本设置），否则资源包/数据包元数据可能不被识别。",
            "src/main/resources/pack.mcmeta",
        ))

    return issues


def report(root: Path | str, loader: str = "", mc: str = "") -> str:
    """工程事实核查报告（Markdown）。"""
    facts = facts_for(loader, mc)
    issues = check_project(root, loader, mc)
    lines = [
        f"# 版本事实核查：{loader or '未指定加载器'} / {facts.mc or '未指定版本'}",
        "",
        brief(loader, mc),
        "",
    ]
    if not issues:
        lines.append("未发现版本/加载器冲突。")
        return "\n".join(lines)
    order = {"error": 0, "warn": 1, "info": 2}
    issues.sort(key=lambda item: order.get(item.severity, 9))
    counts = {key: sum(1 for item in issues if item.severity == key) for key in order}
    lines.append(f"共 {len(issues)} 项：错误 {counts['error']}，警告 {counts['warn']}，提示 {counts['info']}。")
    lines.append("")
    for issue in issues:
        lines.append(issue.line())
    return "\n".join(lines)


def dump(loader: str, mc: str) -> str:
    """工具输出：单版本事实 JSON + 加载器矩阵。"""
    payload = {"facts": facts_for(loader, mc).to_dict(), "matrix": matrix()}
    return json.dumps(payload, ensure_ascii=False, indent=2)