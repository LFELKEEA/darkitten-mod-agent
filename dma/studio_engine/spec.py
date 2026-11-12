"""Spec-first design engine.

The agent never starts from code. It starts from a machine-readable spec that
contains the concept, content graph, progression, balance, assets, acceptance
criteria and open questions. Code generation and auditing both consume this
file, which is what keeps a large mod coherent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from .util import now_iso, slug

THEMES: dict[str, dict[str, Any]] = {
    "tech": {
        "zh": "机械", "en": "Mechanica",
        "keywords": ["科技", "机械", "工业", "电力", "能源", "自动化", "机器", "蒸汽", "赛博", "tech", "machine", "industrial", "power", "automation", "energy"],
        "palette": ["#2f3437", "#8d99a6", "#d9a441", "#4fd1c5", "#e8e6e1"],
        "pillars": ["可读的自动化", "从手搓到工厂的清晰跃迁", "每台机器都有明确输入输出"],
        "loop": "采集原料 → 冶炼/加工 → 建造机器 → 扩产 → 挑战高能耗配方",
    },
    "magic": {
        "zh": "奥术", "en": "Arcanum",
        "keywords": ["魔法", "奥术", "符文", "法术", "祭坛", "法杖", "仪式", "元素", "magic", "arcane", "rune", "spell", "altar", "ritual"],
        "palette": ["#241b3a", "#6d4aff", "#c9a7ff", "#57e6c9", "#f4e9ff"],
        "pillars": ["仪式感强于数值堆叠", "资源是知识而非矿石", "每一条法术路径都有代价"],
        "loop": "收集素材 → 绘制符文 → 完成仪式 → 获得法术 → 解锁更深层仪式",
    },
    "dimension": {
        "zh": "界域", "en": "Realms",
        "keywords": ["维度", "世界", "位面", "传送门", "群系", "星球", "虚空", "dimension", "realm", "portal", "biome", "planet"],
        "palette": ["#101a2b", "#3f6fa8", "#8fd3ff", "#d7b46a", "#e9f4ff"],
        "pillars": ["进入异界必须有准备成本", "环境规则与主世界不同", "探索回报高于刷怪"],
        "loop": "准备装备 → 开启传送门 → 探索群系 → 获取异界材料 → 强化主世界基地",
    },
    "mob": {
        "zh": "生灵", "en": "Bestiary",
        "keywords": ["生物", "怪物", "boss", "首领", "召唤", "驯服", "宠物", "mob", "creature", "boss", "tame", "summon"],
        "palette": ["#2b1d1d", "#8c4a3c", "#d98b5f", "#7fbf6a", "#f2e3c9"],
        "pillars": ["每种生物有可学习的攻击语言", "掉落物驱动下一阶段", "战斗之外也有生态价值"],
        "loop": "发现踪迹 → 准备克制手段 → 战斗/驯服 → 获取材料 → 解锁新生物",
    },
    "adventure": {
        "zh": "远征", "en": "Expedition",
        "keywords": ["冒险", "地牢", "遗迹", "探索", "战利品", "任务", "adventure", "dungeon", "ruin", "explore", "loot", "quest"],
        "palette": ["#1f2a24", "#5c7a5a", "#c9a227", "#a85d3a", "#ece5d3"],
        "pillars": ["探索有明确目标感", "战利品改变玩法而不只是加数值", "每个遗迹讲一个故事"],
        "loop": "获得线索 → 寻找遗迹 → 解谜/战斗 → 取得神器 → 开启下一段线索",
    },
    "farming": {
        "zh": "丰饶", "en": "Harvest",
        "keywords": ["农业", "种植", "作物", "食物", "烹饪", "料理", "养殖", "farming", "crop", "food", "cooking", "agriculture"],
        "palette": ["#24301b", "#6f9b3f", "#d7c15a", "#c46b3a", "#f3ecd4"],
        "pillars": ["种植有节奏感", "食物提供差异化增益", "加工链短而可读"],
        "loop": "育种 → 种植 → 收获 → 烹饪 → 获得增益 → 扩大农场",
    },
    "decoration": {
        "zh": "栖居", "en": "Habitat",
        "keywords": ["装饰", "家具", "建筑", "灯", "椅子", "桌子", "美化", "decoration", "furniture", "building", "lamp"],
        "palette": ["#2b2420", "#8a6a4f", "#d9b382", "#6f8f8a", "#f5efe6"],
        "pillars": ["一套材质语言贯穿所有家具", "连接处自然", "不增加额外负担"],
        "loop": "收集木材/石料 → 制作家具 → 布置空间 → 解锁套系",
    },
    "storage": {
        "zh": "仓储", "en": "Vault",
        "keywords": ["存储", "仓库", "箱子", "整理", "物流", "storage", "vault", "crate", "sorting", "logistics"],
        "palette": ["#2a2f33", "#7b858c", "#c2a35c", "#5f8f7a", "#eef1f2"],
        "pillars": ["存取零思考", "容量升级可见", "破坏方块的代价可控"],
        "loop": "制作容器 → 分类存储 → 自动整理 → 远程访问",
    },
    "transport": {
        "zh": "通达", "en": "Transit",
        "keywords": ["交通", "运输", "铁路", "传送", "载具", "transport", "rail", "teleport", "vehicle"],
        "palette": ["#22282e", "#6d7d8c", "#e0a83e", "#3fa7a1", "#e9edf0"],
        "pillars": ["路线规划有决策", "速度与安全权衡", "与主世界地形互动"],
        "loop": "选址 → 铺设线路 → 建造站点 → 优化运力",
    },
    "utility": {
        "zh": "巧具", "en": "Gadgets",
        "keywords": ["工具", "实用", "背包", "磁铁", "便捷", "utility", "tool", "backpack", "gadget", "qol"],
        "palette": ["#272b2f", "#8a9096", "#e3b341", "#4aa3a2", "#f0f2f4"],
        "pillars": ["减少重复操作", "不破坏原版平衡", "每个道具只有一个核心用途"],
        "loop": "发现痛点 → 制作道具 → 简化流程 → 组合使用",
    },
    "twilight": {
        "zh": "暮色", "en": "Twilight",
        "keywords": ["暮色森林", "暮色", "巨树", "树屋", "迷宫", "萤火", "森林", "twilight", "canopy", "giant tree", "maze", "firefly"],
        "palette": ["#0e1a12", "#2f5233", "#8fbf6a", "#e0b84f", "#d9f2c8"],
        "pillars": ["每片森林都有独特的生态与危险", "探索顺序由地标与战利品自然引导", "Boss 是区域规则的化身"],
        "loop": "寻找地标 → 穿越生态区 → 收集区域材料 → 击败区域首领 → 解锁下一片森林",
    },
    "social": {
        "zh": "人烟", "en": "Homestead",
        "keywords": ["凡家物语", "村民", "npc", "对话", "婚姻", "结婚", "家庭", "恋爱", "社交", "声望", "城镇", "小镇", "mca", "villager", "social", "dialogue", "marriage", "family", "relationship", "reputation", "town"],
        "palette": ["#2b2118", "#8a6242", "#e0b88a", "#7fa38a", "#f6ead8"],
        "pillars": ["每个 NPC 都有可被记住的生活轨迹", "关系推进靠时间与选择，不靠刷数值", "城镇由玩家与 NPC 共同生长"],
        "loop": "认识居民 → 完成委托 → 建立关系 → 组建家庭/城镇 → 解锁共同事件",
    },
}

TYPE_LABEL_ZH = {
    "item": "物品", "block": "方块", "ore": "矿石", "storage_block": "存储方块",
    "machine": "机器", "food": "食物", "crop": "作物", "seed": "种子",
    "tool": "工具", "armor": "盔甲", "weapon": "武器", "material": "材料",
    "entity": "生物", "boss": "首领", "spawn_egg": "刷怪蛋",
    "dimension": "维度", "biome": "群系", "structure": "结构",
    "enchantment": "附魔", "effect": "状态效果", "ritual": "仪式",
    "upgrade": "升级模块", "artifact": "神器", "decor": "装饰",
    "config": "配置", "advancement": "进度",
    "tree": "树木", "npc": "NPC", "dialogue": "对话", "quest": "任务",
    "trade": "交易", "faction": "阵营", "schedule": "日程", "house": "住宅",
    "skill": "技能", "boss_arena": "首领战场", "loot_table": "战利品表",
    "menu": "界面", "block_entity": "方块实体", "dimension_type": "维度类型",
    "sound": "音效", "particle": "粒子", "energy": "能量", "network": "网络包",
    "fluid": "流体",
}

# Concrete brand words extracted from the idea. Used for item/block names so
# they read like a real mod instead of "<theme> Shard".
BRAND_MAP: dict[str, str] = {
    "星辉": "Starlight", "星辰": "Astral", "星界": "Astral", "深渊": "Abyss",
    "虚空": "Void", "机械": "Mechanica", "奥术": "Arcane", "符文": "Runed",
    "秘银": "Mithril", "精金": "Adamant", "水晶": "Crystal", "霜": "Frost",
    "冰": "Glacial", "炎": "Ember", "烈焰": "Pyre", "雷": "Storm",
    "风暴": "Storm", "大地": "Terra", "森林": "Verdant", "海": "Tidal",
    "月": "Lunar", "日": "Solar", "影": "Umbra", "灵魂": "Soul",
    "血": "Blood", "死": "Necro", "生命": "Vita", "时间": "Chrono",
    "空间": "Spatial", "龙": "Draconic", "炼金": "Alchemical", "蒸汽": "Steam",
    "齿轮": "Cog", "量子": "Quantum", "纳米": "Nano", "生化": "Bio",
    "遗迹": "Ruin", "远征": "Expedition", "丰饶": "Harvest", "仓储": "Vault",
    "暮色": "Twilight", "森林": "Verdant", "凡家": "Homestead", "村庄": "Village",
    "小镇": "Hamlet", "旅人": "Wanderer", "萤火": "Firefly", "树屋": "Canopy",
}

_BRAND_STOPWORDS = {"一个", "我的", "这个", "包含", "以及", "还有", "模组", "世界", "玩家"}

# A secondary theme contributes one bridge node instead of a generic "fused block".
CROSSOVER_NODES: dict[str, tuple[str, str, str, str]] = {
    "magic": ("rune_interface", "machine", "符文接口", "Runed Interface"),
    "mob": ("incubator", "machine", "培育槽", "Incubator"),
    "dimension": ("realm_anchor", "block", "界域锚点", "Realm Anchor"),
    "adventure": ("relic_altar", "machine", "遗物祭坛", "Relic Altar"),
    "farming": ("nutrient_vat", "machine", "营养槽", "Nutrient Vat"),
    "decoration": ("aesthetic_panel", "block", "饰面板", "Aesthetic Panel"),
    "storage": ("logistics_core", "block", "物流核心", "Logistics Core"),
    "transport": ("phase_rail", "block", "相位轨道", "Phase Rail"),
    "utility": ("field_tool", "tool", "场域工具", "Field Tool"),
    "tech": ("servo_arm", "block", "伺服臂", "Servo Arm"),
    "twilight": ("canopy_bridge", "block", "树冠栈桥", "Canopy Bridge"),
    "social": ("notice_board", "block", "委托板", "Notice Board"),
}

SIGNATURE_NAMES: dict[str, tuple[str, str]] = {
    "tech": ("谐振核心", "Resonance Core"),
    "magic": ("法杖", "Scepter"),
    "mob": ("驯兽哨", "Beast Whistle"),
    "adventure": ("远征徽记", "Expedition Sigil"),
    "dimension": ("界域罗盘", "Realm Compass"),
    "farming": ("丰饶之种", "Seed of Plenty"),
    "storage": ("虚空终端", "Void Terminal"),
    "transport": ("跃迁引擎", "Warp Engine"),
    "decoration": ("营造蓝图", "Architect's Blueprint"),
    "utility": ("万能工具", "Omni Tool"),
    "twilight": ("暮色地图", "Twilight Map"),
    "social": ("家族相册", "Family Album"),
}

BOSS_NAMES: dict[str, tuple[str, str]] = {
    "tech": ("湮灭核心", "Annihilation Core"),
    "magic": ("大法师残影", "Archmage Echo"),
    "mob": ("母巢主宰", "Hive Matriarch"),
    "adventure": ("遗迹守望者", "Ruin Warden"),
    "dimension": ("界域之主", "Realm Sovereign"),
    "farming": ("丰饶之影", "Harvest Shade"),
    "storage": ("贪婪吞噬者", "The Hoarder"),
    "transport": ("失控列车", "Runaway Express"),
    "decoration": ("蜃景建筑师", "Mirage Architect"),
    "utility": ("规则破坏者", "Rulebreaker"),
    "twilight": ("暮色巫妖", "Twilight Lich"),
    "social": ("镇长之影", "The Mayor's Shadow"),
}


def _brand_from_idea(idea: str, theme: str) -> tuple[str, str]:
    text = idea or ""
    # "以星辉能量为核心" -> 星辉
    for match in re.finditer(r"[\u4e00-\u9fa5]{2,4}(?:能量|核心|科技|魔法|之力|水晶|文明|纪元)", text):
        chunk = match.group(0)
        for suffix in ("能量", "核心", "科技", "魔法", "之力", "水晶", "文明", "纪元"):
            if chunk.endswith(suffix):
                candidate = chunk[: -len(suffix)]
                break
        else:
            candidate = ""
        if len(candidate) >= 2 and candidate not in _BRAND_STOPWORDS:
            candidate = re.sub(r"^[个以用的和与及在为是把将从向有个一这那]+", "", candidate)
            if len(candidate) >= 2:
                return candidate, BRAND_MAP.get(candidate, THEMES[theme]["en"])
    # Longest known brand word wins.
    for word in sorted(BRAND_MAP, key=len, reverse=True):
        if word in text:
            return word, BRAND_MAP[word]
    return THEMES[theme]["zh"], THEMES[theme]["en"]



@dataclass
class ContentNode:
    id: str
    type: str
    name_zh: str
    name_en: str
    role: str
    description: str
    stage: int = 1
    dependencies: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    mechanics: list[str] = field(default_factory=list)
    assets: dict[str, Any] = field(default_factory=dict)
    recipe: dict[str, Any] | None = None
    loot: dict[str, Any] | None = None
    acceptance: list[str] = field(default_factory=list)
    implementation: str = "auto"   # auto | stub | design-only

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect_themes(idea: str) -> list[str]:
    text = idea.lower()
    scored: list[tuple[int, str]] = []
    for key, theme in THEMES.items():
        score = sum(1 for kw in theme["keywords"] if kw.lower() in text)
        if score:
            scored.append((score, key))
    scored.sort(reverse=True)
    if not scored:
        return ["tech"]
    return [k for _, k in scored[:2]]


# epic 需要 ~64 个内容节点；单个主题只有 30~40 个。当构思只命中 1~2 个主题时，
# 按内容量从大到小补足第五个主题为止（social/twilight 各约 12-15 节点），
# 跨界桥会把这些主题与主主题连接起来，符合「接近完整模组」的承诺。
EPIC_FILL_THEMES = ("social", "twilight", "tech", "magic", "dimension", "farming")


def _theme_names(themes: list[str]) -> tuple[str, str, list[str]]:
    primary = THEMES[themes[0]]
    zh = primary["zh"]
    en = primary["en"]
    if len(themes) > 1:
        second = THEMES[themes[1]]
        zh = zh + second["zh"]
        en = en + second["en"]
    palette: list[str] = []
    for t in themes:
        for c in THEMES[t]["palette"]:
            if c not in palette:
                palette.append(c)
    return zh, en, palette


def _node(
    modid: str, node_id: str, ntype: str, zh: str, en: str, role: str,
    desc: str, stage: int = 1, deps: list[str] | None = None,
    tags: list[str] | None = None, mechanics: list[str] | None = None,
    recipe: dict[str, Any] | None = None, loot: dict[str, Any] | None = None,
    implementation: str = "auto",
) -> ContentNode:
    full_deps = [d if ":" in d else f"{modid}:{d}" for d in (deps or [])]
    return ContentNode(
        id=f"{modid}:{node_id}", type=ntype, name_zh=zh, name_en=en, role=role,
        description=desc, stage=stage, dependencies=full_deps, tags=tags or [],
        mechanics=mechanics or [], recipe=recipe, loot=loot,
        acceptance=[
            f"{zh}已注册且能在创造模式物品栏中找到",
            f"{zh}的模型/贴图/语言键完整，无紫黑缺失贴图",
            f"{zh}的配方或获取方式在生存模式可完成",
        ],
        implementation=implementation,
    )


def _base_nodes(modid: str, theme: str, brand_zh: str, brand_en: str, scope: str) -> list[ContentNode]:
    n: list[ContentNode] = []
    base = re.sub(r"[^a-z0-9]+", "_", brand_en.lower()).strip("_") or "core"
    raw = f"{base}_shard"
    ingot = f"{base}_ingot"
    sig_zh, sig_en = SIGNATURE_NAMES.get(theme, (f"{brand_zh}之心", f"{brand_en} Heart"))
    boss_zh, boss_en = BOSS_NAMES.get(theme, (f"{brand_zh}终焉", f"{brand_en} End"))
    n.append(_node(modid, raw, "material", f"{brand_zh}碎片", f"{brand_en} Shard",
                   "基础材料", "所有进度的起点，可在野外或矿洞获取。", 1,
                   tags=["c:shards"], loot={"type": "ore_drop", "min": 1, "max": 3}))
    n.append(_node(modid, ingot, "material", f"{brand_zh}锭", f"{brand_en} Ingot",
                   "加工材料", "由碎片冶炼而成，是工具与机器的通用材料。", 1,
                   deps=[raw], recipe={"type": "smelting", "input": raw, "output": ingot}))
    n.append(_node(modid, "core_block", "block", f"{brand_zh}核心", f"{brand_en} Core",
                   "核心方块", "模块化核心，作为多方块结构或工作台的主体。", 1,
                   deps=[ingot], recipe={"type": "shaped", "pattern": ["III", "ICI", "III"],
                                         "keys": {"I": ingot, "C": "minecraft:crafting_table"}}))
    n.append(_node(modid, "raw_ore", "ore", f"{brand_zh}矿石", f"{brand_en} Ore",
                   "世界生成", "生成在地下对应高度，需要合适工具开采。", 1,
                   tags=["c:ores"], loot={"type": "ore_drop", "min": 1, "max": 2, "drop": f"{modid}:{raw}"},
                   mechanics=["worldgen: 4 veins/chunk, y=-32..48"]))
    n.append(_node(modid, "storage_block", "storage_block", f"{brand_zh}块", f"{brand_en} Block",
                   "压缩存储", "9 个锭压缩为 1 个块，用于存储与装饰。", 1, deps=[ingot],
                   recipe={"type": "shaped", "pattern": ["III", "III", "III"], "keys": {"I": ingot}}))
    n.append(_node(modid, "core_tool", "tool", f"{brand_zh}工具", f"{brand_en} Tools",
                   "工具组", "剑、镐、斧、铲、锄，共享一套材料与耐久规则。", 2, deps=[ingot],
                   mechanics=["tier: 介于铁与钻石之间", "durability: 1200"]))
    n.append(_node(modid, "core_armor", "armor", f"{brand_zh}护甲", f"{brand_en} Armor",
                   "盔甲组", "四件套，提供中等防护与一个套装效果。", 2, deps=[ingot],
                   mechanics=["armor: 7/10", "set_bonus: 采集速度 +10%"]))
    n.append(_node(modid, "infused_food", "food", f"{brand_zh}能量餐", f"{brand_en} Meal",
                   "食物", "恢复饥饿值并给予短时增益。", 2, deps=[ingot],
                   mechanics=["nutrition: 8", "saturation: 0.8", "effect: speed 60s"]))
    n.append(_node(modid, "workstation", "machine", f"{brand_zh}工作台", f"{brand_en} Workbench",
                   "核心工作站", "本模组所有进阶配方的入口。", 2, deps=["core_block", ingot],
                   mechanics=["block_entity", "menu", "recipe_type", "energy_or_mana_buffer"]))
    n.append(_node(modid, "upgrade", "upgrade", f"{brand_zh}升级模块", f"{brand_en} Upgrade Module",
                   "升级系统", "插入工作站或机器，改变其行为。", 3, deps=["workstation"],
                   mechanics=["upgrade_slots: 3", "stackable: false"]))
    n.append(_node(modid, "signature_item", "artifact", sig_zh, sig_en,
                   "标志性物品", "模组的记忆点，拥有独特右键能力与冷却。", 3, deps=["upgrade"],
                   mechanics=["cooldown: 8s", "right_click_ability", "tooltip"]))
    n.append(_node(modid, "endgame_challenge", "boss", boss_zh, boss_en,
                   "终局内容", "消耗签名物品开启的终局战斗或仪式。", 4, deps=["signature_item"],
                   mechanics=["bossbar", "phases: 3", "unique_drop"]))
    n.append(_node(modid, f"{base}_config", "config", f"{brand_zh}设置", f"{brand_en} Config",
                   "运行配置", "首次启动自动生成 JSON 配置，可调能量、粒子和音效。", 1,
                   mechanics=["json", "auto_create", "hot_reload"]))
    n.append(_node(modid, f"{base}_soundscape", "sound", f"{brand_zh}声景", f"{brand_en} Soundscape",
                   "声音设计", "界面、机器与首领的反馈音，使用可替换的声音事件。", 2,
                   deps=[f"{base}_config"], mechanics=["ui_click", "machine_hum", "boss_roar"]))
    if scope == "small":
        keep = {raw, ingot, "core_block", "raw_ore", "storage_block", "core_tool",
                "core_armor", "infused_food", "workstation", "upgrade", "signature_item",
                "endgame_challenge", f"{base}_config", f"{base}_soundscape"}
        n = [x for x in n if x.id.split(":", 1)[1] in keep]
    return n


def _archetype_nodes(modid: str, theme: str, theme_zh: str, theme_en: str, scope: str) -> list[ContentNode]:
    n: list[ContentNode] = []
    if theme == "tech":
        n += [
            _node(modid, "generator", "machine", f"{theme_zh}发电机", f"{theme_en} Generator", "能源", "燃烧燃料产生能量，是自动化线的起点。", 2, deps=["workstation"], mechanics=["energy", "fuel_slot"]),
            _node(modid, "cable", "energy", f"{theme_zh}导线", f"{theme_en} Cable", "能量传输", "连接发电机与机器，相邻缓冲之间自动平衡能量。", 2, deps=["generator"], mechanics=["energy_transfer: 256/t", "auto_balance"]),
            _node(modid, "coolant", "fluid", f"{theme_zh}冷却液", f"{theme_en} Coolant", "流体", "机器散热与流体管道使用的自定义液体，可装桶搬运。", 2, deps=["generator"], mechanics=["fluid_source", "bucket", "flowing"]),
            _node(modid, "spark", "particle", f"{theme_zh}能量火花", f"{theme_en} Energy Spark", "粒子效果", "机器工作与导线过载时播放的自定义粒子。", 2, deps=["cable"], mechanics=["particle_type", "client_factory"]),
            _node(modid, "network_hub", "network", f"{theme_zh}网络中枢", f"{theme_en} Network Hub", "网络包", "客户端与服务端之间的轻量消息通道，用于同步状态。", 2, deps=["workstation"], mechanics=["payload", "client_to_server", "sync"]),
            _node(modid, "processor", "machine", f"{theme_zh}处理器", f"{theme_en} Processor", "加工", "将原料加工为中间产物，可插入升级模块。", 2, deps=["generator"], mechanics=["recipe_type", "energy_cost"]),
            _node(modid, "auto_farm", "machine", f"{theme_zh}自动农场", f"{theme_en} Auto Farm", "自动化", "自动种植与收获，需要工具与能量。", 3, deps=["processor"], mechanics=["area: 9x9", "tool_slot"]),
            _node(modid, "wrench", "tool", f"{theme_zh}扳手", f"{theme_en} Wrench", "调试工具", "旋转方块、拆卸机器并保留内容物。", 2, deps=["workstation"], mechanics=["rotate", "dismantle"]),
        ]
    elif theme == "magic":
        n += [
            _node(modid, "altar", "machine", f"{theme_zh}祭坛", f"{theme_en} Altar", "仪式核心", "多方块祭坛，消耗符文与材料完成仪式。", 2, deps=["workstation"], mechanics=["multiblock", "ritual", "mana"]),
            _node(modid, "aura", "particle", f"{theme_zh}魔力光尘", f"{theme_en} Mana Mote", "粒子效果", "仪式与法杖施法时漂浮的自定义粒子。", 2, deps=["altar"], mechanics=["particle_type", "client_factory"]),
            _node(modid, "mana_crystal", "material", f"{theme_zh}魔力晶体", f"{theme_en} Mana Crystal", "施法资源", "法术与仪式的通用能量。", 2, deps=["workstation"], mechanics=["mana_storage"]),
            _node(modid, "rune", "material", f"{theme_zh}符文", f"{theme_en} Rune", "仪式组件", "不同符文组合决定仪式结果。", 2, deps=["altar"], mechanics=["rune_socket"]),
            _node(modid, "wand", "tool", f"{theme_zh}法杖", f"{theme_en} Wand", "施法工具", "右键施法，潜行右键切换法术。", 3, deps=["mana_crystal"], mechanics=["spell_slots: 3", "cooldown"]),
            _node(modid, "ritual", "ritual", f"{theme_zh}进阶仪式", f"{theme_en} Greater Ritual", "进阶解锁", "需要特定结构与符文序列。", 3, deps=["rune"], mechanics=["structure_check", "recipe"]),
        ]
    elif theme == "dimension":
        n += [
            _node(modid, "portal_frame", "block", f"{theme_zh}传送门框架", f"{theme_en} Portal Frame", "传送门", "用特定方块围成框架并激活。", 2, deps=["core_block"], mechanics=["multiblock", "portal"]),
            _node(modid, "portal_activator", "item", f"{theme_zh}界域钥匙", f"{theme_en} Realm Key", "激活器", "右键框架完成激活，有耐久。", 2, deps=["core_block"], mechanics=["durability: 8"]),
            _node(modid, "realm", "dimension", f"{theme_zh}异界", f"{theme_en} Otherrealm", "维度", "独立维度，拥有特殊天空、地形与规则。", 2, deps=["portal_frame"], mechanics=["dimension_type", "no_bed", "custom_sky"]),
            _node(modid, "realm_biome", "biome", f"{theme_zh}异界群系", f"{theme_en} Realm Biome", "群系", "决定地形、植被与生物分布。", 2, deps=["realm"], mechanics=["biome_source", "features"]),
            _node(modid, "realm_structure", "structure", f"{theme_zh}异界遗迹", f"{theme_en} Realm Ruin", "结构", "包含战利品与线索的遗迹。", 3, deps=["realm_biome"], mechanics=["jigsaw", "loot"]),
            _node(modid, "realm_mob", "entity", f"{theme_zh}异界生物", f"{theme_en} Realm Dweller", "生物", "适应异界环境的敌对生物。", 3, deps=["realm_biome"], mechanics=["custom_ai", "drops"]),
        ]
    elif theme == "mob":
        n += [
            _node(modid, "critter", "entity", f"{theme_zh}小型生物", f"{theme_en} Critter", "基础生物", "中立生物，掉落基础材料。", 1, deps=["core_block"], mechanics=["neutral_ai", "drops"]),
            _node(modid, "predator", "entity", f"{theme_zh}猎手", f"{theme_en} Predator", "敌对生物", "夜间出现，会绕后与扑击。", 2, deps=["critter"], mechanics=["hostile_ai", "pack"]),
            _node(modid, "spawn_egg", "spawn_egg", f"{theme_zh}刷怪蛋", f"{theme_en} Spawn Egg", "生成", "创造模式测试与整合包配置用。", 1, deps=["critter"]),
            _node(modid, "arena", "structure", f"{theme_zh}巢穴", f"{theme_en} Lair", "结构", "首领战斗场地，含入场机制。", 3, deps=["predator"], mechanics=["structure", "boss_arena"]),
            _node(modid, "boss", "boss", f"{theme_zh}首领", f"{theme_en} Alpha", "首领", "三阶段战斗，掉落签名物品。", 4, deps=["arena", "signature_item"], mechanics=["bossbar", "phases: 3", "unique_drop"]),
        ]
    elif theme == "adventure":
        n += [
            _node(modid, "ruin", "structure", f"{theme_zh}遗迹", f"{theme_en} Ruin", "探索目标", "随机生成的遗迹，含解谜与战利品。", 2, deps=["core_block"], mechanics=["jigsaw", "loot"]),
            _node(modid, "key", "item", f"{theme_zh}遗迹钥匙", f"{theme_en} Ruin Key", "门槛", "开启遗迹核心房间。", 2, deps=["ruin"], mechanics=["durability: 1"]),
            _node(modid, "artifact", "artifact", f"{theme_zh}神器", f"{theme_en} Artifact", "战利品", "改变移动或战斗方式的独特物品。", 3, deps=["ruin"], mechanics=["unique_ability", "curio_or_trinket"]),
            _node(modid, "boss", "boss", f"{theme_zh}遗迹守卫", f"{theme_en} Ruin Warden", "首领", "守护核心房间的精英。", 4, deps=["artifact"], mechanics=["bossbar", "phases: 2"]),
        ]
    elif theme == "farming":
        n += [
            _node(modid, "crop", "crop", f"{theme_zh}作物", f"{theme_en} Crop", "种植", "四阶段作物，光照与水分影响生长。", 1, deps=["raw_ore"], mechanics=["crop_block", "bonemeal"]),
            _node(modid, "seed", "seed", f"{theme_zh}种子", f"{theme_en} Seeds", "种植", "可被鸟类或掉落物传播。", 1, deps=["crop"], mechanics=["plantable"]),
            _node(modid, "fertilizer", "item", f"{theme_zh}肥料", f"{theme_en} Fertilizer", "加速", "缩短生长时间，有副作用概率。", 2, deps=["crop"], mechanics=["growth_boost"]),
            _node(modid, "cooking_pot", "machine", f"{theme_zh}炖锅", f"{theme_en} Cooking Pot", "烹饪", "组合 3-5 种食材产出料理。", 2, deps=["crop"], mechanics=["block_entity", "recipe_type"]),
            _node(modid, "feast", "food", f"{theme_zh}盛宴", f"{theme_en} Feast", "高级食物", "多人共享，提供长时增益。", 3, deps=["cooking_pot"], mechanics=["shared_use", "buff"]),
        ]
    elif theme == "decoration":
        n += [
            _node(modid, "lamp", "decor", f"{theme_zh}灯具", f"{theme_en} Lamp", "照明", "提供 15 级光照，可切换开关。", 1, deps=["core_block"], mechanics=["light_level: 15", "toggle"]),
            _node(modid, "chair", "decor", f"{theme_zh}椅子", f"{theme_en} Chair", "座椅", "可坐下的装饰方块。", 1, deps=["core_block"], mechanics=["sit"]),
            _node(modid, "table", "decor", f"{theme_zh}桌子", f"{theme_en} Table", "家具", "可放置物品的桌面。", 1, deps=["core_block"], mechanics=["display"]),
            _node(modid, "shelf", "decor", f"{theme_zh}架子", f"{theme_en} Shelf", "收纳", "展示物品并 emit comparator 信号。", 2, deps=["table"], mechanics=["display", "comparator"]),
        ]
    elif theme == "twilight":
        n += [
            _node(modid, "twilight_map", "tool", f"{theme_zh}地图", f"{theme_en} Map", "导航", "记录已发现的地标与区域边界，右键展开。", 1, deps=["core_block"], mechanics=["right_click_ability", "tooltip"]),
            _node(modid, "twilight_berry", "food", f"{theme_zh}浆果", f"{theme_en} Berry", "野外补给", "在巨树根部采集，可恢复少量饥饿值。", 1, deps=["core_block"], mechanics=["nutrition: 3", "saturation: 0.4"]),
            _node(modid, "twilight_torch", "block", f"{theme_zh}萤火灯", f"{theme_en} Firefly Lamp", "照明", "使用区域材料制作的冷光光源。", 1, deps=["twilight_berry"], mechanics=["light_level: 14"]),
            _node(modid, "twilight_portal", "block", f"{theme_zh}暮色传送门", f"{theme_en} Twilight Portal", "入口", "用区域木材与宝石围成框架，激活后进入暮色森林。", 2, deps=["twilight_torch"], mechanics=["multiblock", "portal", "dimension_link"]),
            _node(modid, "twilight_dimension", "dimension", f"{theme_zh}暮色森林", f"{theme_en} Twilight Forest", "维度", "永远处于黄昏的森林维度，拥有独立天空与地形。", 2, deps=["twilight_portal"], mechanics=["dimension_type", "custom_sky", "no_bed"]),
            _node(modid, "canopy_biome", "biome", f"{theme_zh}树冠群系", f"{theme_en} Canopy Biome", "生态区", "巨型树木遮蔽天空，地表光照很低。", 2, deps=["twilight_dimension"], mechanics=["biome_source", "features", "custom_spawns"]),
            _node(modid, "hollow_biome", "biome", f"{theme_zh}空谷群系", f"{theme_en} Hollow Biome", "生态区", "雾气弥漫的低洼地带，适合建造据点。", 2, deps=["twilight_dimension"], mechanics=["biome_source", "features"]),
            _node(modid, "canopy_tree", "tree", f"{theme_zh}巨树", f"{theme_en} Giant Tree", "地形骨架", "可攀爬的巨型树木，树冠连接成天然道路。", 2, deps=["canopy_biome"], mechanics=["tree_feature", "custom_log", "canopy"]),
            _node(modid, "firefly", "entity", f"{theme_zh}萤火虫", f"{theme_en} Firefly", "环境生物", "夜间发光的被动生物，掉落荧光粉。", 2, deps=["canopy_biome"], mechanics=["passive_ai", "glow", "drops"]),
            _node(modid, "maze", "structure", f"{theme_zh}树篱迷宫", f"{theme_en} Hedge Maze", "探索目标", "由树篱围成的迷宫，中心有战利品与线索。", 3, deps=["canopy_tree"], mechanics=["jigsaw", "loot", "nav"]),
            _node(modid, "hollow_hill", "structure", f"{theme_zh}空谷土丘", f"{theme_en} Hollow Hill", "地下遗迹", "埋在地下的房间群，入口被植被遮挡。", 3, deps=["hollow_biome"], mechanics=["jigsaw", "loot", "underground"]),
            _node(modid, "knight", "entity", f"{theme_zh}暮色骑士", f"{theme_en} Twilight Knight", "精英敌人", "持盾的精英，会格挡与冲锋。", 3, deps=["maze"], mechanics=["hostile_ai", "shield", "pack"]),
            _node(modid, "hydra", "boss", f"{theme_zh}九头蛇", f"{theme_en} Hydra", "区域首领", "三阶段首领，每次断头会分裂并改变攻击模式。", 4, deps=["hollow_hill", "knight"], mechanics=["bossbar", "phases: 3", "unique_drop"]),
            _node(modid, "lich", "boss", f"{theme_zh}暮色巫妖", f"{theme_en} Twilight Lich", "终局首领", "在暮色高塔中等待的施法者，掉落维度钥匙。", 4, deps=["hydra", "signature_item"], mechanics=["bossbar", "phases: 4", "spell_caster", "unique_drop"]),
            _node(modid, "trophy", "artifact", f"{theme_zh}战利品陈列", f"{theme_en} Trophy", "进度证明", "把首领掉落物变为可展示的奖杯并给予被动增益。", 3, deps=["knight"], mechanics=["trophy", "display", "passive_buff"]),
        ]
    elif theme == "social":
        n += [
            _node(modid, "npc_guide", "npc", f"{theme_zh}向导", f"{theme_en} Guide", "引导者", "介绍聚落、发布第一条委托并记住玩家的选择。", 1, deps=["core_block"], mechanics=["dialogue", "quest_giver", "schedule"]),
            _node(modid, "npc_baker", "npc", f"{theme_zh}面包师", f"{theme_en} Baker", "居民", "每天清晨工作，出售食物并接受食材委托。", 1, deps=["core_block"], mechanics=["dialogue", "trade", "schedule"]),
            _node(modid, "notice_board", "block", f"{theme_zh}委托板", f"{theme_en} Notice Board", "任务入口", "放置后展示当前可接取的委托。", 1, deps=["npc_guide"], mechanics=["block_entity", "quest_board"]),
            _node(modid, "dialogue_tree", "dialogue", f"{theme_zh}对话树", f"{theme_en} Dialogue Tree", "社交骨架", "分支对话与条件选项，关系与任务状态会改变可用话题。", 1, deps=["npc_guide"], mechanics=["dialogue", "conditions", "click_actions"]),
            _node(modid, "quest_intro", "quest", f"{theme_zh}初来乍到", f"{theme_en} Newcomer", "引导任务", "认识三位居民并完成一次委托。", 2, deps=["dialogue_tree"], mechanics=["objectives", "rewards", "unlock_graph"]),
            _node(modid, "relationship", "faction", f"{theme_zh}关系网", f"{theme_en} Relationships", "核心系统", "记录每位居民对玩家的好感、信任与记忆。", 2, deps=["quest_intro"], mechanics=["scoreboard", "persistence", "gates"]),
            _node(modid, "trade", "trade", f"{theme_zh}市集交易", f"{theme_en} Market", "经济", "居民按日程与关系调整价格和货品。", 2, deps=["npc_baker"], mechanics=["trade", "reputation", "pricing"]),
            _node(modid, "house", "structure", f"{theme_zh}民居", f"{theme_en} Cottage", "建筑", "可被居民认领的住宅，决定其夜间日程。", 2, deps=["relationship"], mechanics=["jigsaw", "housing", "claimed_bed"]),
            _node(modid, "marriage", "quest", f"{theme_zh}相伴一生", f"{theme_en} Marriage", "进阶关系", "达到信任阈值后解锁的承诺事件。", 3, deps=["relationship", "house"], mechanics=["relationship_gate", "ritual"]),
            _node(modid, "family", "quest", f"{theme_zh}家族", f"{theme_en} Family", "长期目标", "家庭成员、子女与继承物，形成跨代叙事。", 4, deps=["marriage"], mechanics=["family_tree", "inheritance", "persistence"]),
            _node(modid, "town_hall", "structure", f"{theme_zh}镇公所", f"{theme_en} Town Hall", "聚落核心", "发布群体任务、举办节日并记录城镇声望。", 3, deps=["house"], mechanics=["jigsaw", "faction", "festival"]),
            _node(modid, "festival", "ritual", f"{theme_zh}丰收节", f"{theme_en} Harvest Festival", "群体事件", "按游戏内日历触发的全镇事件，提升关系。", 3, deps=["town_hall"], mechanics=["calendar", "group_event", "relationship"]),
            _node(modid, "reputation", "faction", f"{theme_zh}城镇声望", f"{theme_en} Town Reputation", "长期系统", "影响交易、任务与居民对话的公共评价。", 2, deps=["trade"], mechanics=["reputation", "scoreboard", "gates"]),
            _node(modid, "heirloom", "artifact", f"{theme_zh}传家宝", f"{theme_en} Heirloom", "终局纪念", "家族传承的独特物品，记录一段关系史。", 4, deps=["family"], mechanics=["unique_ability", "lore", "persistence"]),
        ]
    elif theme == "storage":
        n += [
            _node(modid, "crate", "storage_block", f"{theme_zh}板条箱", f"{theme_en} Crate", "存储", "可升级容量的单格存储。", 1, deps=["core_block"], mechanics=["inventory", "upgradeable"]),
            _node(modid, "sorting_hopper", "machine", f"{theme_zh}分拣漏斗", f"{theme_en} Sorting Hopper", "物流", "按过滤器分拣物品。", 2, deps=["crate"], mechanics=["filter", "transfer"]),
            _node(modid, "remote_terminal", "machine", f"{theme_zh}远程终端", f"{theme_en} Remote Terminal", "访问", "跨维度访问已绑定仓库。", 3, deps=["sorting_hopper"], mechanics=["wireless", "security"]),
        ]
    elif theme == "transport":
        n += [
            _node(modid, "rail", "block", f"{theme_zh}强化轨道", f"{theme_en} Reinforced Rail", "线路", "更高速度上限的轨道。", 1, deps=["core_block"], mechanics=["rail", "speed"]),
            _node(modid, "cart", "entity", f"{theme_zh}载具", f"{theme_en} Cart", "运载", "可装载物品或乘客。", 2, deps=["rail"], mechanics=["minecart", "inventory"]),
            _node(modid, "station", "machine", f"{theme_zh}站点控制器", f"{theme_en} Station Controller", "调度", "设置停靠、路线与红石信号。", 3, deps=["cart"], mechanics=["routing", "redstone"]),
        ]
    elif theme == "utility":
        n += [
            _node(modid, "backpack", "item", f"{theme_zh}背包", f"{theme_en} Backpack", "便携存储", "右键打开，潜行右键放置。", 1, deps=["core_block"], mechanics=["inventory", "curio"]),
            _node(modid, "magnet", "item", f"{theme_zh}磁铁", f"{theme_en} Magnet", "拾取", "吸引附近掉落物，可切换。", 2, deps=["backpack"], mechanics=["radius: 6", "toggle"]),
            _node(modid, "config_tool", "tool", f"{theme_zh}配置扳手", f"{theme_en} Config Tool", "调试", "查看并修改机器参数。", 2, deps=["core_block"], mechanics=["gui", "nbt"]),
        ]
    return n


def _expand(modid: str, theme: str, theme_zh: str, theme_en: str, scope: str, idea: str,
            brand_zh: str | None = None, brand_en: str | None = None) -> list[ContentNode]:
    brand_zh = brand_zh or theme_zh
    brand_en = brand_en or theme_en
    nodes = _base_nodes(modid, theme, brand_zh, brand_en, scope)
    themes = detect_themes(idea)
    if theme not in themes:
        themes.insert(0, theme)
    if scope == "epic":
        # epic 承诺 64 节点：单/双主题填不满，从兼容主题补足到 5 个主题。
        for extra in EPIC_FILL_THEMES:
            if len(themes) >= 5:
                break
            if extra not in themes:
                themes.append(extra)
    # Every detected theme contributes its full archetype set. A Twilight
    # Forest + MCA style idea therefore gets both world/adventure and
    # NPC/social systems instead of one theme plus a single bridge block.
    for each in themes:
        nodes += _archetype_nodes(modid, each, brand_zh, brand_en, scope)
    # Secondary theme gets one meaningful bridge node as well.
    for extra in themes[1:]:
        if extra == theme:
            continue
        node_id, ntype, cross_zh, cross_en = CROSSOVER_NODES.get(
            extra, (f"{extra}_bridge", "block", f"{THEMES[extra]['zh']}接口",
                    f"{THEMES[extra]['en']} Interface"))
        nodes.append(_node(modid, node_id, ntype,
                           f"{brand_zh}{cross_zh}", f"{brand_en} {cross_en}",
                           "主题融合", f"把{THEMES[theme]['zh']}与{THEMES[extra]['zh']}机制连接起来的桥梁。",
                           3, deps=["workstation"], mechanics=["crossover"]))
    target = {"small": 12, "medium": 22, "large": 40, "epic": 64}.get(scope, 22)
    # Deduplicate by id while preserving order.
    seen: set[str] = set()
    unique: list[ContentNode] = []
    for n in nodes:
        if n.id in seen:
            continue
        seen.add(n.id)
        unique.append(n)
    if len(unique) > target and scope != "epic":
        # Keep all stage 1-2 nodes, trim the tail of stage 3-4.
        must = [n for n in unique if n.stage <= 2]
        rest = [n for n in unique if n.stage > 2]
        unique = must + rest[: max(0, target - len(must))]
    # Reindex names to avoid duplicate generic labels.
    return unique


def build_spec(
    idea: str,
    loader: str,
    mc: str,
    *,
    name: str | None = None,
    modid: str | None = None,
    package: str | None = None,
    scope: str = "medium",
    author: str = "Your Name",
    license: str = "MIT",
) -> dict[str, Any]:
    themes = detect_themes(idea)
    theme = themes[0]
    theme_zh, theme_en, palette = _theme_names(themes)
    brand_zh, brand_en = _brand_from_idea(idea, theme)
    base_en = brand_en or THEMES[theme]["en"]
    mod_name_zh = name or f"{brand_zh}纪元"
    mod_name_en = name if (name and name.isascii()) else f"{base_en} Age"
    modid = slug(modid or base_en.lower() + "_age", "examplemod")
    package = package or f"com.example.{modid}"
    nodes = _expand(modid, theme, theme_zh, base_en, scope, idea, brand_zh, brand_en)
    stages = sorted({n.stage for n in nodes})
    dependencies = sorted({d for n in nodes for d in n.dependencies})
    spec: dict[str, Any] = {
        "schema": "mcmod-spec/1",
        "generated_at": now_iso(),
        "idea": idea.strip(),
        "identity": {
            "name_zh": mod_name_zh,
            "name_en": mod_name_en,
            "modid": modid,
            "package": package,
            "version": "0.1.0",
            "author": author,
            "license": license,
            "description_zh": f"{mod_name_zh}：{idea.strip()}",
            "description_en": f"{mod_name_en}: {idea.strip()}",
        },
        "target": {"loader": loader, "mc": mc, "side": "both"},
        "scope": scope,
        "themes": themes,
        "palette": palette,
        "pillars": [p for t in themes for p in THEMES[t]["pillars"]][:4],
        "core_loop": THEMES[theme]["loop"],
        "content": [n.to_dict() for n in nodes],
        "progression": {
            "stages": [
                {"stage": s, "label": f"阶段 {s}", "content": [n.id for n in nodes if n.stage == s]}
                for s in stages
            ],
            "dependencies": dependencies,
        },
        "balance": {
            "material_tier": "介于铁与钻石之间",
            "tool_durability": 1200,
            "armor_points": [7, 10],
            "machine_energy_per_tick": 32,
            "boss_hp": 240,
            "boss_phases": 3,
            "time_to_first_machine_minutes": 25,
            "time_to_endgame_hours": 6 if scope != "small" else 3,
        },
        "worldgen": {
            "ore_height": "-32..48",
            "veins_per_chunk": 4,
            "biome_filter": "overworld",
            "structures": [n.id for n in nodes if n.type == "structure"],
        },
        "art": {
            "resolution": "16x16",
            "palette": palette,
            "rules": [
                "1px 深色描边，内部不超过 5 个明度层",
                "禁用抗锯齿与半透明渐变",
                "光源在左上，暗部在右下",
                "每个物品轮廓必须能在 16x16 内一眼识别",
            ],
            "vfx": ["方块破坏粒子使用主题色", "机器工作时每 20 tick 产生一次粒子"],
        },
        "audio": {
            "events": ["block.workstation.use", "machine.loop", "boss.roar", "artifact.cast"],
            "style": "短促、颗粒感、低频有重量",
        },
        "localization": {"languages": ["zh_cn", "en_us"], "key_format": "{type}.{modid}.{id}"},
        "compatibility": {
            "required": [],
            "optional": ["jei", "jade", "curios", "trinkets"],
            "policy": "优先使用标签与通用 API，硬依赖越少越好",
        },
        "performance": {
            "tick_budget_ms": 2.0,
            "block_entity_tick_interval": 10,
            "max_entities_near_player": 40,
            "rules": ["机器空闲时不 tick", "避免每 tick new 对象", "缓存 capability 查找"],
        },
        "acceptance": {
            "gates": [
                "G0 规格完整：每个内容节点有类型、来源、用途、验收项",
                "G1 编译通过：gradlew build 无错误",
                "G2 资源完整：模型/贴图/语言/配方/战利品 100% 覆盖",
                "G3 数据生成通过：datagen 无崩溃、无缺失引用",
                "G4 服务端冒烟：加载世界无注册表/网络错误",
                "G5 客户端冒烟：进入世界、物品可见、无紫黑贴图",
                "G6 性能预算：新增 tick 开销 < 2ms",
                "G7 打包完整：jar 含元数据、资源、许可证",
            ],
            "blocking": ["G1", "G2", "G4"],
        },
        "risks": [
            {"risk": "版本 API 漂移", "mitigation": "recon 生成 API 卡，编译验证，禁止凭记忆写 API"},
            {"risk": "贴图/模型缺失", "mitigation": "asset audit 作为阻断门"},
            {"risk": "客户端/服务端类混用", "mitigation": "静态扫描 + 双端冒烟"},
            {"risk": "内容量过大导致半成品", "mitigation": "垂直切片，每片可编译可验证"},
        ],
        "milestones": [
            {"name": "M1 可运行骨架", "contents": [n.id for n in nodes if n.stage == 1][:6]},
            {"name": "M2 核心循环", "contents": [n.id for n in nodes if n.stage == 2][:8]},
            {"name": "M3 进阶与终局", "contents": [n.id for n in nodes if n.stage >= 3][:8]},
            {"name": "M4 打磨与兼容", "contents": ["assets", "lang", "config", "jei", "performance"]},
        ],
        "open_questions": [
            "模组是否需要能量系统，还是用燃料/冷却等更轻的机制？",
            "终局内容希望是 Boss、维度还是自动化巨型工程？",
            "是否必须兼容 JEI/Jade/Curios？",
            "目标整合包环境是否允许硬依赖其他模组？",
        ],
        "asset_prompts": _asset_prompts(modid, nodes, palette, theme_zh),
    }
    return spec


SHAPE_HINTS = {
    "material": "a small raw mineral cluster or refined ingot with a chunky readable silhouette",
    "ore": "a stone block with embedded glowing mineral veins, tileable on all sides",
    "block": "a tileable machine casing block with seamless edges and a subtle panel seam",
    "storage_block": "a compact storage block made of stacked ingots, tileable",
    "machine": "a front-facing machine block with vents, bolts and one glowing status light",
    "tool": "a handheld tool seen diagonally, clear grip, head and a small energy accent",
    "armor": "a single chestplate icon, front-facing, symmetrical",
    "food": "a plated meal or energy ration seen from above",
    "artifact": "a unique handheld artifact built around one glowing core",
    "upgrade": "a small socketed circuit module with visible contacts",
    "spawn_egg": "a spotted egg icon in the theme colors",
    "decor": "a small furnishing block, readable at 16x16, tileable",
    "crop": "a mature crop block with leaves and fruit, tileable",
    "seed": "a small handful of seeds",
}


def _asset_prompts(modid: str, nodes: list[ContentNode], palette: list[str], theme_zh: str) -> list[dict[str, str]]:
    prompts = []
    item_types = {"item", "material", "food", "tool", "armor", "artifact", "upgrade", "seed", "spawn_egg"}
    entity_types = {"entity", "boss"}
    for n in nodes:
        if n.type in {"dimension", "biome", "structure", "advancement", "config", "ritual"}:
            continue
        short = n.id.split(":", 1)[1]
        if n.type in entity_types:
            size = "128x128" if n.type == "boss" else "64x64"
            target = f"assets/{modid}/textures/entity/{short}.png"
            hint = (f"a {size} entity skin sheet for {n.name_en}, full body, front and side layout, "
                    "flat colors, 1px dark outline, no anti-aliasing, no gradients, no background")
        elif n.type in item_types:
            target = f"assets/{modid}/textures/item/{short}.png"
            hint = SHAPE_HINTS.get(n.type, "a single readable item icon")
        else:
            target = f"assets/{modid}/textures/block/{short}.png"
            hint = SHAPE_HINTS.get(n.type, "a tileable 16x16 block texture with seamless edges")
        prompts.append({
            "id": short,
            "target": target,
            "prompt": (
                f"Minecraft pixel art texture of {n.name_en} ({n.role}): {hint}. "
                f"Theme {theme_zh}, palette {' '.join(palette)}. "
                "Crisp 1px dark outline, no anti-aliasing, no gradients, transparent background, "
                "readable silhouette, centered, pixel-perfect, no text, no watermark"
            ),
        })
    return prompts


def spec_to_markdown(spec: dict[str, Any]) -> str:
    ident = spec["identity"]
    lines: list[str] = []
    add = lines.append
    add(f"# {ident['name_zh']} · {ident['name_en']}")
    add("")
    add(f"> {ident['description_zh']}")
    add("")
    add(f"- 模组 ID：`{ident['modid']}`")
    add(f"- 目标：`{spec['target']['loader']} / {spec['target']['mc']}`")
    add(f"- 包名：`{ident['package']}`")
    add(f"- 规模：`{spec['scope']}`，内容节点 `{len(spec['content'])}` 个")
    add(f"- 主题：{', '.join(spec['themes'])}")
    add("")
    add("## 1. 设计支柱")
    add("")
    for p in spec["pillars"]:
        add(f"- {p}")
    add("")
    add("## 2. 核心循环")
    add("")
    add(f"**{spec['core_loop']}**")
    add("")
    add("## 3. 内容总表")
    add("")
    add("| 阶段 | ID | 类型 | 中文名 | 英文名 | 作用 | 实现 |")
    add("|---|---|---|---|---|---|---|")
    for n in spec["content"]:
        add(f"| {n['stage']} | `{n['id']}` | {TYPE_LABEL_ZH.get(n['type'], n['type'])} | {n['name_zh']} | {n['name_en']} | {n['role']} | {n['implementation']} |")
    add("")
    add("## 4. 进度树")
    add("")
    add("```mermaid")
    add("flowchart TD")
    by_id = {n["id"]: n for n in spec["content"]}
    for n in spec["content"]:
        label = n["name_zh"].replace('"', "'")
        add(f'  {n["id"].split(":", 1)[1]}["{label}"]')
    for n in spec["content"]:
        for dep in n["dependencies"]:
            if dep in by_id:
                add(f'  {dep.split(":", 1)[1]} --> {n["id"].split(":", 1)[1]}')
    add("```")
    add("")
    add("## 5. 数值基线")
    add("")
    add("| 项目 | 基线 |")
    add("|---|---|")
    for k, v in spec["balance"].items():
        add(f"| {k} | {v} |")
    add("")
    add("## 6. 世界生成")
    add("")
    for k, v in spec["worldgen"].items():
        add(f"- **{k}**：{v}")
    add("")
    add("## 7. 美术方向")
    add("")
    add(f"- 分辨率：{spec['art']['resolution']}")
    add(f"- 调色板：{' '.join(f'`{c}`' for c in spec['art']['palette'])}")
    for r in spec["art"]["rules"]:
        add(f"- {r}")
    add("")
    add("## 8. 音效")
    add("")
    add(f"- 事件：{', '.join(spec['audio']['events'])}")
    add(f"- 风格：{spec['audio']['style']}")
    add("")
    add("## 9. 兼容与性能")
    add("")
    add(f"- 可选联动：{', '.join(spec['compatibility']['optional']) or '无'}")
    add(f"- 策略：{spec['compatibility']['policy']}")
    add(f"- tick 预算：{spec['performance']['tick_budget_ms']} ms")
    for r in spec["performance"]["rules"]:
        add(f"- {r}")
    add("")
    add("## 10. 验收门")
    add("")
    for g in spec["acceptance"]["gates"]:
        add(f"- [ ] {g}")
    add("")
    add("## 11. 风险")
    add("")
    for r in spec["risks"]:
        add(f"- **{r['risk']}** → {r['mitigation']}")
    add("")
    add("## 12. 里程碑")
    add("")
    for m in spec["milestones"]:
        add(f"- **{m['name']}**：{', '.join(m['contents']) or '待定'}")
    add("")
    add("## 13. 待确认问题")
    add("")
    for q in spec["open_questions"]:
        add(f"- {q}")
    add("")
    add("## 14. 素材提示词")
    add("")
    add("以下提示词可直接交给图像生成器，生成后再做 16x16 量化与描边校验。")
    add("")
    for p in spec["asset_prompts"][:12]:
        add(f"### {p['id']}")
        add("")
        add(f"`{p['prompt']}`")
        add("")
    if len(spec["asset_prompts"]) > 12:
        add(f"（其余 {len(spec['asset_prompts']) - 12} 条见 mod-spec.json 的 asset_prompts 字段）")
    add("")
    return "\n".join(lines)
