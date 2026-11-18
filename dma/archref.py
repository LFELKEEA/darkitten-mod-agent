"""大型模组架构参考：把成熟模组的结构提炼成可检索的工程范式。

工坊能生成“能编译的骨架”，但大型模组真正的难点在架构：
注册与内容分离、数据驱动配方、多方块控制器、能力/能量抽象、客户端渲染分层、
可扩展 API 包。没有这些，类一多就变成互相引用的泥球。

这里不复制任何模组源码，只记录公开可见的架构模式、目录职责与常见坑，
供 Agent 在 large_mod_plan / arch_ref 时检索。引用具体实现前必须遵守仓库许可证，
并在目标版本上重写，不能整段搬运。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Pattern:
    name: str
    idea: str
    files: tuple[str, ...]
    pitfalls: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "idea": self.idea, "files": list(self.files),
                "pitfalls": list(self.pitfalls)}


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    repo: str
    license: str
    scale: str
    tags: tuple[str, ...]
    patterns: tuple[Pattern, ...]
    lessons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "repo": self.repo,
            "license": self.license,
            "scale": self.scale,
            "tags": list(self.tags),
            "patterns": [item.to_dict() for item in self.patterns],
            "lessons": list(self.lessons),
        }


PROFILES: tuple[Profile, ...] = (
    Profile(
        id="create",
        name="Create（机械与可视化教学）",
        repo="Creators-of-Create/Create",
        license="MIT（以仓库 LICENSE 为准）",
        scale="大型：内容注册 + 动力学系统 + 可视化教程 + 蓝图",
        tags=("机械", "动力学", "可视化", "教程", "蓝图", "ponder", "registrate",
              "装置", "旋转", "应力", "kinetic", "contraption", "schematic"),
        patterns=(
            Pattern(
                "注册与内容分层",
                "用统一的注册辅助层集中声明方块/物品/方块实体/菜单，内容类只描述行为。"
                "注册层负责 id、属性、渲染类型、数据生成绑定，避免每个内容类各写一套。",
                ("registry/", "content/", "infrastructure/"),
                ("不要在内容类构造函数里做注册", "注册 id 必须唯一且稳定，改名会坏存档"),
            ),
            Pattern(
                "动力学/能量抽象",
                "把“产生/消耗/传递”抽象成接口与网络快照，方块实体只实现接口，"
                "网络负责每 tick 汇总与限流，渲染层只读快照。",
                ("kinetics/", "api/", "client/render/"),
                ("不要在渲染线程改世界状态", "跨区块网络必须处理卸载与重建"),
            ),
            Pattern(
                "可视化教程（Ponder）",
                "把玩法说明做成可播放的场景脚本：镜头、放置、交互、文字提示，"
                "与物品/方块注册绑定，而不是写一段静态说明书。",
                ("client/ponder/", "data/"),
                ("场景脚本会随方块 id 失效", "客户端专用，不能进服务端类"),
            ),
            Pattern(
                "装置/蓝图",
                "把一组方块快照成结构化数据（相对坐标 + 状态 + 方块实体 NBT），"
                "放置时按依赖顺序恢复，失败要回滚，避免半成品结构。",
                ("contraption/", "schematic/"),
                ("NBT 版本迁移", "实体/方块实体恢复顺序", "大结构要分片避免卡顿"),
            ),
            Pattern(
                "数据驱动配方",
                "自定义配方类型 + 序列化器 + 配方显示器；批量内容用 datagen 生成，"
                "不要手写几百个 JSON。",
                ("recipe/", "datagen/"),
                ("配方序列化器要在注册阶段登记", "客户端配方显示与服务端校验分开"),
            ),
        ),
        lessons=(
            "内容数量大时，先做注册/数据生成基础设施，再写玩法。",
            "客户端渲染与游戏逻辑分离，渲染层只读数据。",
            "公开 API 包与实现包分目录，别让第三方依赖内部类。",
        ),
    ),
    Profile(
        id="gregtech",
        name="GregTech Modern（材料与机器体系）",
        repo="GregTechCEu/GregTech-Modern",
        license="LGPL-3.0（以仓库 LICENSE 为准，参考实现需谨慎）",
        scale="超大型：材料注册、配方映射、多方块、能源等级、数据生成",
        tags=("材料", "material", "机器", "多方块", "multiblock", "配方", "recipe map",
              "能源", "电压", "tier", "化工", "矿物", "gregtech"),
        patterns=(
            Pattern(
                "材料注册表",
                "用 Material + 注册表统一生成锭/粉/板/流体/矿石等衍生内容；"
                "颜色、图标集、化学式、熔点和配方都挂在材料上，而不是逐个手写。",
                ("api/chemical/", "common/data/", "common/item/"),
                ("材料 id 与衍生 id 必须可逆推导", "不同形态的注册顺序会影响配方生成"),
            ),
            Pattern(
                "配方映射（RecipeMap）",
                "每种机器类型对应一个配方映射，统一处理输入/输出/概率/时长/能源；"
                "机器只声明使用哪个映射，不重复写匹配逻辑。",
                ("api/recipe/", "common/recipe/"),
                ("NEI/JEI 显示与执行必须同源", "概率输出要可复现"),
            ),
            Pattern(
                "多方块控制器",
                "控制器方块实体 + 结构定义 + 部件扫描 + 成型/失效状态机；"
                "结构匹配与业务逻辑分离，成型后缓存结构快照。",
                ("common/metatileentity/multiblock/",),
                ("区块卸载后的重建", "结构变更节流，别每 tick 全量扫描"),
            ),
            Pattern(
                "能源/等级体系",
                "电压等级、电流、损耗用统一类型表达；机器按 tier 校验，"
                "超压要爆炸或拒绝，而不是静默运行。",
                ("api/energy/",),
                ("能量单位混用", "跨机器传输的守恒与损耗"),
            ),
            Pattern(
                "数据生成",
                "材料 × 形态 × 配方组合爆炸，必须用 datagen / 运行时注册生成，"
                "并给生成结果做快照测试。",
                ("datagen/", "generated/"),
                ("生成器改动会导致全量 diff", "生成物要能重复构建"),
            ),
        ),
        lessons=(
            "先用数据模型描述内容，再由生成器产出注册与资源。",
            "配方、机器、显示三者共用同一份定义。",
            "超大型工程必须有生成物快照与确定性构建。",
        ),
    ),
    Profile(
        id="mekanism",
        name="Mekanism（能力、机器与 API 分层）",
        repo="mekanism/Mekanism",
        license="MIT（以仓库 LICENSE 为准）",
        scale="大型：能力 API、机器层级、多方块、配方类型、配置系统",
        tags=("能力", "capability", "能量", "机器", "多方块", "multiblock", "配置",
              "config", "升级", "upgrade", "物流", "mekanism"),
        patterns=(
            Pattern(
                "API 与实现分离",
                "独立 api 模块定义能力接口/能量单位/配方接口，实现模块只依赖 api；"
                "第三方可以编译期依赖 api，不拖入全部实现。",
                ("src/api/java/", "src/main/java/"),
                ("api 里不能引用实现类", "api 变更要兼容旧版本"),
            ),
            Pattern(
                "机器基类层级",
                "抽象出带能量/背包/升级/红石控制的机器基类，具体机器只实现"
                "配方查找与处理；避免每个机器复制 tick/保存/同步逻辑。",
                ("content/machine/",),
                ("基类过胖会变成上帝类", "继承层级要浅，能力用组合"),
            ),
            Pattern(
                "多方块数据",
                "多方块结构用独立数据对象管理位置、部件、成型状态；"
                "控制器只持有引用，结构失效时统一清理。",
                ("lib/multiblock/",),
                ("跨区块结构加载顺序", "保存/加载时的状态恢复"),
            ),
            Pattern(
                "配置系统",
                "机器/玩法参数集中到配置定义，带范围、默认值、注释与迁移；"
                "不要在代码里散落魔法数字。",
                ("config/",),
                ("旧配置迁移", "服务端/客户端配置分离"),
            ),
            Pattern(
                "容器同步",
                "菜单/容器只做槽位与数据槽声明，同步用统一容器同步器，"
                "避免每个 GUI 手写包。",
                ("common/container/", "client/gui/"),
                ("数据槽索引冲突", "大对象不要每 tick 全量同步"),
            ),
        ),
        lessons=(
            "能力接口优先于继承；机器行为用组合拼装。",
            "配置和同步是大型机器的隐形复杂度，先做基础设施。",
            "API 包从第一天就分出来，后期拆分成本极高。",
        ),
    ),
    Profile(
        id="botania",
        name="Botania（魔力、功能花与文档）",
        repo="VazkiiMods/Botania",
        license="自定义许可证（以仓库 LICENSE 为准，禁止直接搬运代码）",
        scale="大型：魔力网络、功能花体系、词典文档、炼金/符文配方",
        tags=("魔力", "mana", "花", "flower", "功能花", "生成花", "词典", "lexicon",
              "炼金", "符文", "botania", "自然"),
        patterns=(
            Pattern(
                "魔力网络",
                "魔力接收/发送用接口表达，魔力池作为缓冲；传输有上限与节流，"
                "避免每 tick 无限扫描。",
                ("api/", "common/block/"),
                ("网络扫描性能", "卸载区块的魔力归属"),
            ),
            Pattern(
                "功能花体系",
                "生成花/功能花共享基类与 NBT 持久化，行为用子类或组件实现；"
                "花与土壤/环境条件解耦。",
                ("common/block/flower/",),
                ("子类爆炸时改用组件", "掉落与数据保存"),
            ),
            Pattern(
                "游戏内词典",
                "把说明做成可翻页的书籍数据（章节/条目/配方页），"
                "内容与 UI 分离，支持本地化。",
                ("client/gui/", "common/lexicon/"),
                ("页面 id 与内容版本", "多人环境下的解锁状态"),
            ),
            Pattern(
                "仪式/多方块",
                "仪式用中心方块 + 结构检查 + 运行状态机；运行中记录参与者，"
                "中断时安全回滚。",
                ("common/block/",),
                ("结构扫描节流", "运行中卸载区块"),
            ),
        ),
        lessons=(
            "玩法系统的复杂度集中在“状态机 + 数据归属 + 清理”。",
            "游戏内文档是大型模组的可维护性基础设施，不是附属品。",
            "参考时只学结构与交互，不复制美术/文本/代码。",
        ),
    ),
)

TAGS = {profile.id: set(profile.tags) for profile in PROFILES}


def _score(query: str, profile: Profile) -> int:
    text = (query or "").lower().strip()
    if not text:
        return 0
    score = 0
    for tag in profile.tags:
        if tag.lower() in text:
            score += 3
    blob = f"{profile.name} {profile.repo} {profile.scale}".lower()
    for token in set(re.findall(r"[a-z0-9_+-]{3,}", text)):
        if token in blob:
            score += 1
    for pattern in profile.patterns:
        if pattern.name.lower() in text:
            score += 4
    return score


def lookup(query: str, limit: int = 3) -> list[Profile]:
    scored = [(_score(query, profile), profile) for profile in PROFILES]
    scored = [item for item in scored if item[0] > 0]
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [profile for _, profile in scored[:max(1, limit)]]


def profile(profile_id: str) -> Profile | None:
    key = (profile_id or "").strip().lower()
    return next((item for item in PROFILES if item.id == key), None)


def catalog() -> str:
    lines = ["大型模组架构参考（只读范式，不复制源码）："]
    for item in PROFILES:
        lines.append(f"- {item.id}｜{item.name}｜{item.scale}")
        lines.append(f"  标签：{', '.join(item.tags)}")
    lines.append("用 arch_ref action=lookup query=<玩法/机制> 检索具体模式。")
    return "\n".join(lines)


def explain(profile_id: str) -> str:
    item = profile(profile_id)
    if item is None:
        return f"没有 {profile_id} 这个架构参考；可用：{', '.join(row.id for row in PROFILES)}"
    lines = [
        f"# {item.name}",
        f"- 仓库：{item.repo}（{item.license}）",
        f"- 规模：{item.scale}",
        "",
        "## 可借鉴模式",
    ]
    for pattern in item.patterns:
        lines.append(f"### {pattern.name}")
        lines.append(pattern.idea)
        if pattern.files:
            lines.append(f"- 目录：{', '.join(pattern.files)}")
        for pitfall in pattern.pitfalls:
            lines.append(f"- 坑：{pitfall}")
        lines.append("")
    lines.append("## 工程结论")
    for lesson in item.lessons:
        lines.append(f"- {lesson}")
    lines.append("")
    lines.append("以上仅为架构参考；落地代码必须按目标 MC/加载器重写，并遵守原仓库许可证。")
    return "\n".join(lines).strip()


def search(query: str, limit: int = 3) -> str:
    hits = lookup(query, limit=limit)
    if not hits:
        return ("没有匹配的架构参考。改用 large_mod_plan 拆阶段，"
                "或用 mod_reference 搜索同玩法模组反编译参考实现。")
    lines = [f"按「{query}」匹配到 {len(hits)} 个架构参考："]
    for item in hits:
        lines.append("")
        lines.append(explain(item.id))
    return "\n".join(lines)


def brief(loader: str = "", mc: str = "") -> str:
    """系统提示词用的短提示。"""
    return (
        "【大型模组架构参考】\n"
        "做多机器/多方块/材料体系/能力网络/花式玩法时，先用 arch_ref 检索范式：\n"
        "- create：注册分层、动力学抽象、可视化教程、装置蓝图、数据驱动配方\n"
        "- gregtech：材料注册表、RecipeMap、多方块控制器、能源等级、生成器\n"
        "- mekanism：API/实现分离、机器基类、多方块数据、配置、容器同步\n"
        "- botania：魔力网络、功能花体系、游戏内词典、仪式状态机\n"
        "只学结构，不复制源码；落地按目标版本重写并遵守许可证。"
    )


def dump(query: str, limit: int = 3) -> str:
    hits = lookup(query, limit=limit)
    payload = [item.to_dict() for item in hits] if hits else [item.to_dict() for item in PROFILES]
    return json.dumps(payload, ensure_ascii=False, indent=2)