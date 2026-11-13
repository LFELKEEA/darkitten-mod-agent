"""ModSpec: the single source of truth for a Mod Agent 2.0 run.

Every request is turned into a specification before any file is written.  A
feature owns a set of paths and a list of acceptance items.  The acceptance
gate never lets a feature be marked ``done`` while a required item is failing.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

SCHEMA = "dma-spec/2"
SPEC_RELATIVE = Path(".dma") / "spec.json"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def slug(value: str, fallback: str = "darkitten_mod") -> str:
    text = (value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        return fallback
    if text[0].isdigit():
        text = "mod_" + text
    return text[:48]


def _ascii_words(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", text or "")
    return [item.lower() for item in words]


def infer_modid(idea: str) -> str:
    words = _ascii_words(idea)
    if words:
        return slug("_".join(words[:3]))
    return "darkitten_mod"


@dataclass
class AcceptanceItem:
    id: str
    kind: str  # build | lint | file | test | runtime | manual
    description: str
    required: bool = True
    params: dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    evidence: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "description": self.description,
            "required": bool(self.required),
            "params": dict(self.params),
            "status": self.status,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "AcceptanceItem":
        return cls(
            id=str(raw.get("id") or ""),
            kind=str(raw.get("kind") or "manual"),
            description=str(raw.get("description") or ""),
            required=bool(raw.get("required", True)),
            params=dict(raw.get("params") or {}),
            status=str(raw.get("status") or "pending"),
            evidence=str(raw.get("evidence") or ""),
        )


@dataclass
class Feature:
    id: str
    title: str
    description: str
    files: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    acceptance: list[AcceptanceItem] = field(default_factory=list)
    status: str = "planned"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "files": list(self.files),
            "depends_on": list(self.depends_on),
            "acceptance": [item.to_dict() for item in self.acceptance],
            "status": self.status,
            "notes": list(self.notes),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Feature":
        return cls(
            id=str(raw.get("id") or ""),
            title=str(raw.get("title") or ""),
            description=str(raw.get("description") or ""),
            files=[str(item) for item in raw.get("files") or []],
            depends_on=[str(item) for item in raw.get("depends_on") or []],
            acceptance=[
                AcceptanceItem.from_dict(item)
                for item in raw.get("acceptance") or []
                if isinstance(item, dict)
            ],
            status=str(raw.get("status") or "planned"),
            notes=[str(item) for item in raw.get("notes") or []],
        )


@dataclass
class ModSpec:
    idea: str
    loader: str
    mc: str
    modid: str
    package: str
    features: list[Feature]
    scope: str = "medium"
    side: str = "both"
    constraints: list[str] = field(default_factory=list)
    schema: str = SCHEMA
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "idea": self.idea,
            "loader": self.loader,
            "mc": self.mc,
            "modid": self.modid,
            "package": self.package,
            "scope": self.scope,
            "side": self.side,
            "constraints": list(self.constraints),
            "features": [item.to_dict() for item in self.features],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ModSpec":
        return cls(
            idea=str(raw.get("idea") or ""),
            loader=str(raw.get("loader") or "NeoForge"),
            mc=str(raw.get("mc") or "1.21.1"),
            modid=str(raw.get("modid") or infer_modid(str(raw.get("idea") or ""))),
            package=str(raw.get("package") or ""),
            features=[
                Feature.from_dict(item)
                for item in raw.get("features") or []
                if isinstance(item, dict)
            ],
            scope=str(raw.get("scope") or "medium"),
            side=str(raw.get("side") or "both"),
            constraints=[str(item) for item in raw.get("constraints") or []],
            schema=str(raw.get("schema") or SCHEMA),
            created_at=str(raw.get("created_at") or now_iso()),
            updated_at=str(raw.get("updated_at") or now_iso()),
        )

    # ------------------------------------------------------------------
    def feature(self, feature_id: str) -> Feature | None:
        for item in self.features:
            if item.id == feature_id:
                return item
        return None

    def feature_ids(self) -> list[str]:
        return [item.id for item in self.features]

    def feature_for_path(self, relative: str) -> Feature | None:
        path = str(relative or "").replace("\\", "/").lstrip("./")
        best: tuple[int, Feature] | None = None
        for item in self.features:
            for pattern in item.files:
                score = _glob_match_score(pattern, path)
                if score and (best is None or score > best[0]):
                    best = (score, item)
        return best[1] if best else None

    def record_edit(self, relative: str) -> Feature | None:
        feature = self.feature_for_path(relative)
        if feature is not None and feature.status == "planned":
            feature.status = "in_progress"
        self.updated_at = now_iso()
        return feature

    def mark_feature(self, feature_id: str, status: str, note: str = "") -> None:
        item = self.feature(feature_id)
        if item is None:
            return
        item.status = status
        if note:
            item.notes.append(note)
        self.updated_at = now_iso()

    def required_acceptance(self) -> list[tuple[Feature, AcceptanceItem]]:
        rows: list[tuple[Feature, AcceptanceItem]] = []
        for feature in self.features:
            for item in feature.acceptance:
                if item.required:
                    rows.append((feature, item))
        return rows

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for feature in self.features:
            counts[feature.status] = counts.get(feature.status, 0) + 1
        return {
            "features": len(self.features),
            "status": counts,
            "acceptance": sum(len(item.acceptance) for item in self.features),
        }


def _glob_match_score(pattern: str, path: str) -> int:
    """Very small glob matcher that also understands the common ** rules."""
    pattern = str(pattern or "").replace("\\", "/").lstrip("./")
    if not pattern:
        return 0
    if pattern == path:
        return 100 + len(pattern)
    regex = re.escape(pattern)
    regex = regex.replace(r"\*\*/", "(?:.*/)?")
    regex = regex.replace(r"\*\*", ".*")
    regex = regex.replace(r"\*", "[^/]*")
    regex = regex.replace(r"\?", "[^/]")
    if re.fullmatch(regex, path):
        return 50 + len(pattern)
    return 0


# ----------------------------------------------------------------------
# Feature inference
# ----------------------------------------------------------------------

FEATURE_RULES: list[dict[str, Any]] = [
    {
        "id": "item",
        "title": "物品与材料",
        "keywords": ["物品", "材料", "药水", "食物", "工具", "武器", "盔甲", "item", "material", "food", "tool", "armor", "weapon"],
        "files": [
            "src/main/java/**/item/**", "src/main/java/**/items/**",
            "src/main/java/**/registry/**", "src/main/resources/assets/**/models/item/**",
            "src/main/resources/assets/**/textures/item/**",
        ],
        "rules": ["P1-27", "P1-28", "P1-29", "P1-30"],
    },
    {
        "id": "block",
        "title": "方块与方块实体",
        "keywords": ["方块", "block", "机器", "machine", "容器", "container", "矿石", "ore"],
        "files": [
            "src/main/java/**/block/**", "src/main/java/**/blocks/**",
            "src/main/java/**/machine/**", "src/main/resources/assets/**/blockstates/**",
            "src/main/resources/assets/**/models/block/**",
            "src/main/resources/assets/**/textures/block/**",
        ],
        "rules": ["P1-30", "P1-31", "P1-32", "P2-39", "P2-40", "P2-41", "P2-43"],
    },
    {
        "id": "entity",
        "title": "实体与 AI",
        "keywords": ["实体", "生物", "怪物", "boss", "entity", "mob", "creature", "npc", "寻路", "ai"],
        "files": [
            "src/main/java/**/entity/**", "src/main/java/**/entities/**",
            "src/main/java/**/client/render/**",
            "src/main/resources/assets/**/models/entity/**",
            "src/main/resources/assets/**/textures/entity/**",
        ],
        "rules": ["P1-34", "P2-34", "P2-35", "P2-36", "P2-37", "P2-38"],
    },
    {
        "id": "network",
        "title": "网络同步与安全",
        "keywords": ["网络", "联机", "同步", "数据包", "packet", "network", "payload", "sync", "客户端", "服务端"],
        "files": [
            "src/main/java/**/network/**", "src/main/java/**/net/**",
            "src/main/java/**/payload/**", "src/main/java/**/packet/**",
        ],
        "rules": ["P0-09", "P0-10", "P0-11", "P2-27", "P2-28", "P2-29", "P2-30", "P2-31", "P2-32", "P2-33"],
    },
    {
        "id": "gui",
        "title": "界面与容器菜单",
        "keywords": ["界面", "gui", "菜单", "menu", "容器", "screen", "hud", "tooltip"],
        "files": [
            "src/main/java/**/client/**", "src/main/java/**/gui/**",
            "src/main/java/**/menu/**", "src/main/resources/assets/**/lang/**",
        ],
        "rules": ["P2-20", "P2-21", "P2-22", "P2-23", "P2-24"],
    },
    {
        "id": "data",
        "title": "数据包内容",
        "keywords": ["配方", "战利品", "标签", "进度", "数据包", "recipe", "loot", "tag", "advancement", "datapack", "predicate"],
        "files": [
            "src/main/resources/data/**", "src/main/resources/**/data/**",
        ],
        "rules": ["P1-11", "P2-44", "P2-45", "P2-46", "P2-47", "P3-15"],
    },
    {
        "id": "assets",
        "title": "模型、贴图与音效",
        "keywords": ["模型", "贴图", "纹理", "音效", "声音", "动画", "model", "texture", "sound", "animation", "geckolib"],
        "files": [
            "src/main/resources/assets/**", "src/main/resources/**/sounds.json",
        ],
        "rules": ["P1-06", "P1-07", "P1-08", "P1-09", "P1-10", "P1-12", "P1-35", "P3-08", "P3-09"],
    },
    {
        "id": "dimension",
        "title": "维度与世界生成",
        "keywords": ["维度", "世界生成", "生物群系", "结构", "dimension", "biome", "worldgen", "structure"],
        "files": [
            "src/main/java/**/world/**", "src/main/java/**/worldgen/**",
            "src/main/resources/data/**/dimension/**", "src/main/resources/data/**/worldgen/**",
        ],
        "rules": ["P2-04", "P2-05", "P2-06", "P2-07", "P2-08", "P2-09", "P2-10"],
    },
    {
        "id": "effect",
        "title": "状态效果与属性",
        "keywords": ["效果", "药水", "属性", "buff", "effect", "potion", "attribute"],
        "files": ["src/main/java/**/effect/**", "src/main/java/**/potion/**"],
        "rules": ["P1-33"],
    },
    {
        "id": "enchant",
        "title": "附魔",
        "keywords": ["附魔", "enchant"],
        "files": ["src/main/java/**/enchant/**", "src/main/resources/data/**/enchantment/**"],
        "rules": ["P1-36"],
    },
    {
        "id": "command",
        "title": "命令与权限",
        "keywords": ["命令", "command", "权限", "permission"],
        "files": ["src/main/java/**/command/**", "src/main/java/**/commands/**"],
        "rules": ["P1-37", "P2-47", "P3-14", "P3-41"],
    },
    {
        "id": "config",
        "title": "配置系统",
        "keywords": ["配置", "config", "设置", "setting"],
        "files": ["src/main/java/**/config/**", "src/main/resources/**/*.toml"],
        "rules": ["P1-25", "P1-26", "P3-16", "P3-19"],
    },
    {
        "id": "compat",
        "title": "第三方兼容",
        "keywords": ["兼容", "联动", "jei", "emi", "rei", "jade", "curios", "create", "compat", "integration"],
        "files": ["src/main/java/**/compat/**", "src/main/java/**/integration/**"],
        "rules": ["P1-20", "P1-21", "P1-22", "P1-23", "P1-24", "P3-17", "P3-18"],
    },
    {
        "id": "tests",
        "title": "测试与验证",
        "keywords": ["测试", "验证", "test", "gametest", "scenario"],
        "files": ["src/test/**", "src/gametest/**", "src/main/java/**/test/**", "scenarios/**"],
        "rules": ["P1-01", "P1-02", "P1-03", "P1-04"],
    },
]


# 复杂机制规则：这些不是「内容」（物品/方块/实体），而是「跨实体的动态行为」
# （玩家变身/夺舍/换肤/社交聊天/物品框）。agent 极易用「spawn 实体 + 改
# customName + 发消息 + 存 List」冒充实现——每个命中的机制生成一个独立
# feature，acceptance 用 file 硬门槛强制产出 mixin / 渲染器 / GUI 等「真实现」
# 必需的文件，配合 prompt 里的机制门槛规则一起根除「占位冒充实现」。
MECHANISM_RULES: list[dict[str, Any]] = [
    {
        "id": "transform",
        "title": "变身与夺舍（玩家控制权切换）",
        "keywords": ["变身", "变成", "夺舍", "附身", "附体", "进入身体", "形态切换",
                     "灵魂", "transform", "possess", "possession", "inhabit", "morph", "soul"],
        "files": [
            "src/main/resources/*.mixins.json",
            "src/main/java/**/mixin/**",
            "src/main/java/**/client/render/**",
        ],
        "rules": ["P1-34"],
        "playable": "玩家/实体变身与夺舍通过状态机实现：触发后本体冻结（渲染成躺地空壳）、相机/控制权切换到目标实体、退出可逆还原；必须 mixin 切换控制权，而不是在玩家旁边 spawn 一个实体。",
    },
    {
        "id": "skin",
        "title": "皮肤与外观切换",
        "keywords": ["皮肤", "换肤", "换装", "外观", "littleskin", "skin", "appearance"],
        "files": [
            "src/main/java/**/client/render/**",
            "src/main/resources/assets/**/textures/entity/**",
        ],
        "rules": ["P1-08"],
        "playable": "皮肤纹理按 id 动态切换（从 littleskin 拉取或本地多套纹理），渲染器不是恒返回一张固定 default.png。",
    },
    {
        "id": "dialogue",
        "title": "对话/聊天框/社交互动",
        "keywords": ["聊天", "对话", "聊天框", "交谈", "社交", "关系", "好感", "村民",
                     "互动", "dialogue", "dialog", "chat", "social", "relationship",
                     "affinity", "villager", "interact"],
        "files": [
            "src/main/java/**/client/gui/**",
            "src/main/java/**/screen/**",
            "src/main/java/**/menu/**",
        ],
        "rules": ["P2-20", "P2-21"],
        "playable": "对话/聊天通过 Screen 打开可交互 GUI，AI 行为读取社交关系 NBT 数据，不是只发一条硬编码消息。",
    },
    {
        "id": "inventory_ui",
        "title": "物品框/背包交换界面",
        "keywords": ["物品框", "背包", "交换", "物品栏", "inventory", "item slot", "storage"],
        "files": [
            "src/main/java/**/menu/**",
            "src/main/java/**/client/gui/**",
        ],
        "rules": ["P2-20", "P2-21"],
        "playable": "物品框通过 Screen+ScreenHandler 打开可交换，不是直接 setStack 覆盖玩家背包且不暂存原背包。",
    },
]


def _mechanism_acceptance(rule: dict[str, Any], files: list[str]) -> list[AcceptanceItem]:
    """机制类功能的验收：除 build/lint 外，用 file 硬门槛强制产出真实现必需的
    文件（mixins.json / mixin 目录 / 渲染器 / GUI），并用 required=False 的 manual
    项提示「可玩性人工确认」但不阻塞交付（可玩性由 prompt 机制门槛规则把关）。"""
    items: list[AcceptanceItem] = [
        AcceptanceItem("build", "build", "Gradle 编译必须成功", True, {"task": "build"}),
        AcceptanceItem("lint-p0", "lint", "P0 静态检查不得有 error", True,
                       {"levels": ["P0"], "fail_on": "error"}),
    ]
    rule_ids = [item for item in (rule.get("rules") or []) if item]
    if rule_ids:
        items.append(AcceptanceItem(
            "lint-mechanism", "lint",
            f"机制相关检查通过：{', '.join(rule_ids)}",
            True, {"rule_ids": rule_ids, "fail_on": "error"},
        ))
    items.append(AcceptanceItem(
        "mechanism-files", "file",
        f"机制真实现必需的文件/目录至少存在一个（{rule['title']}）",
        True, {"patterns": files},
    ))
    items.append(AcceptanceItem(
        "playable", "manual", rule["playable"], False, {},
    ))
    return items


def _acceptance_for(feature_id: str, rules: Iterable[str], files: list[str]) -> list[AcceptanceItem]:
    items: list[AcceptanceItem] = [
        AcceptanceItem(
            "build", "build", "Gradle 编译必须成功", True,
            {"task": "build"},
        ),
        AcceptanceItem(
            "lint-p0", "lint", "P0 静态检查不得有 error", True,
            {"levels": ["P0"], "fail_on": "error"},
        ),
    ]
    rule_ids = [item for item in rules if item]
    if rule_ids:
        items.append(
            AcceptanceItem(
                "lint-feature", "lint",
                f"功能相关检查通过：{', '.join(rule_ids)}",
                True,
                {"rule_ids": rule_ids, "fail_on": "error"},
            )
        )
    if files:
        items.append(
            AcceptanceItem(
                "files", "file", "功能声明的文件/目录至少存在一个",
                True, {"patterns": files},
            )
        )
    if feature_id in {"network", "entity", "block", "dimension", "gui", "data", "assets"}:
        items.append(
            AcceptanceItem(
                "runtime", "runtime",
                "开发端/GameTest 运行时验证（缺失时只作为警告）",
                False,
                {"side": "server" if feature_id == "network" else "both"},
            )
        )
    return items


def derive_spec(
    idea: str,
    loader: str = "NeoForge",
    mc: str = "1.21.1",
    *,
    modid: str | None = None,
    package: str | None = None,
    scope: str = "medium",
) -> ModSpec:
    text = (idea or "").strip()
    lowered = text.lower()
    modid_value = slug(modid or infer_modid(text))
    package_value = (package or f"com.darkitten.{modid_value}").strip()

    features: list[Feature] = [
        Feature(
            "core", "工程骨架与构建", "Gradle、入口类、元数据与基础目录",
            ["build.gradle", "settings.gradle", "gradle.properties",
             "src/main/java/**/*.java", "src/main/resources/META-INF/**",
             "src/main/resources/fabric.mod.json"],
            [],
            [
                AcceptanceItem("build", "build", "Gradle 编译必须成功", True, {"task": "build"}),
                AcceptanceItem("lint-p0", "lint", "P0 静态检查不得有 error", True, {"levels": ["P0"], "fail_on": "error"}),
                AcceptanceItem("gradle", "lint", "Gradle 脚本与模板一致", True, {"rule_ids": ["P0-12"], "fail_on": "error"}),
            ],
        ),
        Feature(
            "registry", "注册表与生命周期", "所有注册项、ID、冻结与生命周期",
            ["src/main/java/**/registry/**", "src/main/java/**/Mod*.java",
             "src/main/java/**/Main*.java"],
            ["core"],
            [
                AcceptanceItem("build", "build", "Gradle 编译必须成功", True, {"task": "build"}),
                AcceptanceItem("lint-p0", "lint", "P0 静态检查不得有 error", True, {"levels": ["P0"], "fail_on": "error"}),
                AcceptanceItem("registry", "lint", "注册时机、ID/UUID 唯一性", True, {"rule_ids": ["P1-13", "P1-15"], "fail_on": "error"}),
            ],
        ),
    ]

    selected: list[str] = []
    for rule in FEATURE_RULES:
        keywords = [str(item).lower() for item in rule["keywords"]]
        if any(word in lowered for word in keywords):
            selected.append(rule["id"])
            features.append(
                Feature(
                    rule["id"], rule["title"],
                    f"需求中命中的能力：{rule['title']}",
                    list(rule["files"]),
                    ["registry"],
                    _acceptance_for(rule["id"], rule["rules"], list(rule["files"])),
                )
            )

    # 复杂机制：识别「变身/夺舍/换肤/社交聊天/物品框」这类跨实体动态行为，
    # 为每个命中机制生成独立 feature，验收强制产出 mixin/渲染器/GUI 等真实现
    # 必需文件——从任务拆分层根治「占位冒充实现」的能力天花板。
    entity_selected = "entity" in selected
    for rule in MECHANISM_RULES:
        keywords = [str(item).lower() for item in rule["keywords"]]
        if any(word in lowered for word in keywords):
            deps = ["registry"] + (["entity"] if entity_selected else [])
            features.append(
                Feature(
                    rule["id"], rule["title"],
                    f"复杂机制：{rule['title']}（非静态内容，需状态机/mixin/GUI）",
                    list(rule["files"]),
                    deps,
                    _mechanism_acceptance(rule, list(rule["files"])),
                )
            )

    if not selected:
        # A vague request still gets a generic content feature so the gate has
        # something concrete to verify.
        features.append(
            Feature(
                "content", "核心内容",
                "从需求中提炼的核心可玩内容",
                ["src/main/java/**/*.java", "src/main/resources/assets/**"],
                ["registry"],
                _acceptance_for("content", ["P1-27", "P1-28"], ["src/main/java/**/*.java"]),
            )
        )
    if "tests" not in selected:
        features.append(
            Feature(
                "tests", "测试与验收",
                "单元测试、GameTest 或场景断言",
                ["src/test/**", "src/gametest/**", "scenarios/**"],
                ["registry"],
                [
                    AcceptanceItem("test", "test", "测试必须存在且通过", True, {"kind": "unit"}),
                    AcceptanceItem("runtime", "runtime", "GameTest/场景断言（缺失时警告）", False, {"side": "both"}),
                ],
            )
        )
    features.append(
        Feature(
            "acceptance", "交付验收",
            "汇总编译、静态检查、资产审计和运行时证据",
            [],
            [item.id for item in features],
            [
                AcceptanceItem("build", "build", "Gradle 编译必须成功", True, {"task": "build"}),
                AcceptanceItem("lint-all", "lint", "所有 error 级问题清零", True, {"levels": ["P0", "P1", "P2", "P3"], "fail_on": "error"}),
                AcceptanceItem("audit", "lint", "资源与注册一致性审计", True, {"kind": "audit"}),
                AcceptanceItem("acceptance", "manual", "能力矩阵与未自动化项人工确认", True, {"kind": "manual"}),
            ],
        )
    )

    constraints = [
        f"target={loader} {mc}",
        "JDK 21",
        "side=both（客户端类不得进入 common 初始化路径）",
        "服务端权威：客户端包必须经过服务端校验",
        "所有生成资源必须可追溯到注册项或明确来源",
    ]
    return ModSpec(
        idea=text,
        loader=loader,
        mc=mc,
        modid=modid_value,
        package=package_value,
        features=features,
        scope=scope,
        constraints=constraints,
    )


# ----------------------------------------------------------------------
# Persistence and rendering
# ----------------------------------------------------------------------

def spec_path(root: Path) -> Path:
    return Path(root) / SPEC_RELATIVE


def save_spec(spec: ModSpec, root: Path) -> Path:
    target = spec_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    spec.updated_at = now_iso()
    target.write_text(
        json.dumps(spec.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_spec(root: Path) -> ModSpec | None:
    target = spec_path(root)
    if not target.is_file():
        return None
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return ModSpec.from_dict(raw)
    except Exception:
        return None


def spec_markdown(spec: ModSpec) -> str:
    lines = [
        f"# ModSpec · {spec.modid}",
        "",
        f"- 需求：{spec.idea or '（空）'}",
        f"- 目标：{spec.loader} {spec.mc}",
        f"- 包名：{spec.package}",
        f"- 规模：{spec.scope} / 侧别：{spec.side}",
        "",
        "## 约束",
    ]
    lines.extend(f"- {item}" for item in spec.constraints)
    lines.extend(["", "## 功能与验收"])
    for feature in spec.features:
        lines.append(f"### {feature.id} · {feature.title} [{feature.status}]")
        lines.append(feature.description)
        if feature.depends_on:
            lines.append(f"- 依赖：{', '.join(feature.depends_on)}")
        for item in feature.acceptance:
            flag = "必需" if item.required else "可选"
            lines.append(f"- [{flag}] {item.id}: {item.description} ({item.kind})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
