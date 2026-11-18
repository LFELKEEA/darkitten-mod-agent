"""大型模组规划器：把一句需求拆成可执行的阶段、包结构与工具链。

这里的“2G 标准”指大型应用能力，不是安装包体积：
一个大型模组至少要覆盖 8 个阶段、40+ 个类、注册/资源/网络/客户端/测试
多条链路，并且每个阶段都有真实工具可以执行和验证。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import reference

SCOPE_TARGET = {
    "small": {"classes": 8, "packages": 3, "phases": 5, "tests": 3},
    "medium": {"classes": 20, "packages": 6, "phases": 7, "tests": 8},
    "large": {"classes": 40, "packages": 10, "phases": 8, "tests": 16},
    "epic": {"classes": 80, "packages": 16, "phases": 10, "tests": 30},
}

FEATURE_PACKAGES: dict[str, tuple[str, ...]] = {
    "entity": ("entity", "client/render", "registry"),
    "block": ("block", "block/entity", "registry"),
    "item": ("item", "registry"),
    "gui": ("menu", "client/screen", "network"),
    "worldgen": ("worldgen", "worldgen/feature", "data"),
    "rendering": ("client/render", "client/model", "client/particle"),
    "mixin": ("mixin", "mixin/client"),
    "network": ("network", "network/payload"),
    "data": ("data", "data/attachment"),
    "recipe": ("data", "recipe", "loot"),
    "command": ("command",),
    "config": ("config", "client/config"),
    "effect": ("effect", "potion"),
    "villager": ("entity/villager", "trade"),
    "quest": ("quest", "data"),
    "skin": ("mixin/client", "client/render"),
    "social": ("social", "client/screen", "data"),
    "vehicle": ("entity/vehicle", "client/render"),
    "storage": ("menu", "block/entity", "network"),
}

FEATURE_TASKS: dict[str, tuple[str, ...]] = {
    "entity": (
        "注册 EntityType、属性与生成蛋",
        "实现 AI Goal、移动控制与服务端同步",
        "实现客户端渲染器、模型层与动画",
    ),
    "block": (
        "注册 Block / BlockItem 与方块状态",
        "实现 BlockEntity、刻逻辑与容器交互",
        "生成模型、blockstate、掉落与挖掘标签",
    ),
    "item": (
        "注册 Item、创造模式标签与数据组件",
        "实现使用逻辑、冷却、耐久或装备效果",
        "生成模型、纹理与语言键",
    ),
    "gui": (
        "实现 Menu / ScreenHandler 与服务端槽位校验",
        "实现 Screen、按钮与输入处理",
        "网络同步容器数据与打开界面",
    ),
    "worldgen": (
        "注册维度 / 群系 / 结构的数据包 JSON",
        "实现 Feature、Placement 与生物群系修改",
        "验证传送、出生点与区块生成",
    ),
    "rendering": (
        "实现 Renderer / RenderLayer / 模型加载",
        "实现动画状态机与插值",
        "验证粒子、光照与第一/第三人称表现",
    ),
    "mixin": (
        "确定注入目标、注入点与访问器",
        "编写 Mixin 并在 mixins.json 注册",
        "验证客户端/服务端各自的注入范围",
    ),
    "network": (
        "定义 Payload / Packet 与 Codec",
        "实现双向注册、发送与线程切换",
        "验证连接断开、重连与大包边界",
    ),
    "data": (
        "定义持久化数据结构与 NBT / Codec",
        "实现 Capability 或 DataAttachment 挂载",
        "验证存档、死亡、维度切换后的数据",
    ),
    "recipe": (
        "生成配方 / 战利品 / 标签 JSON",
        "实现自定义 Recipe 或 LootModifier",
        "验证 JEI/REI 与数据包重载",
    ),
    "command": (
        "构建 Brigadier 命令树与参数",
        "实现权限、建议与错误提示",
        "验证单人 / 服务端命令注册",
    ),
    "config": (
        "定义配置模型、默认值与校验",
        "实现配置文件读写",
        "实现配置界面或命令入口",
    ),
    "effect": (
        "注册 MobEffect 与属性修饰",
        "实现药水、喷溅与持续时间逻辑",
        "验证客户端图标与服务端结算",
    ),
    "villager": (
        "注册职业、POI 与交易表",
        "实现交易生成与村民 AI 行为",
        "验证繁殖、工作站与等级",
    ),
    "quest": (
        "定义进度树与判据",
        "实现触发器、奖励与任务链",
        "验证多人进度与存档",
    ),
    "skin": (
        "定位玩家/实体渲染层与纹理来源",
        "用 Mixin 切换纹理与模型层",
        "验证多人、盔甲层与第一人称",
    ),
    "social": (
        "定义关系数据与持久化",
        "实现对话界面与选项",
        "让 AI 行为读取关系并改变决策",
    ),
    "vehicle": (
        "注册载具实体与碰撞箱",
        "实现骑乘、输入与客户端控制",
        "验证多人同步与下车还原",
    ),
    "storage": (
        "实现容器菜单与槽位规则",
        "实现物品栏持久化与快速移动",
        "验证跨端同步与漏斗交互",
    ),
}


@dataclass
class Phase:
    id: str
    title: str
    goal: str
    tasks: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)


def _target(scope: str) -> dict[str, int]:
    return SCOPE_TARGET.get(str(scope or "large").lower(), SCOPE_TARGET["large"])


def _entry_files(loader: str, package: str, modid: str) -> list[str]:
    base = f"src/main/java/{package.replace('.', '/')}"
    key = str(loader or "").lower()
    files = [f"{base}/ExampleMod.java", f"{base}/registry/ModRegistries.java"]
    if key.startswith("fabric"):
        files += [
            "src/main/resources/fabric.mod.json",
            f"{base}/ExampleModClient.java",
        ]
    elif key.startswith("quilt"):
        files += [
            "src/main/resources/quilt.mod.json",
            f"{base}/ExampleModClient.java",
        ]
    elif key.startswith("neo"):
        files += ["src/main/resources/META-INF/neoforge.mods.toml"]
    else:
        files += ["src/main/resources/META-INF/mods.toml"]
    return files


def build_plan(
    idea: str,
    *,
    loader: str = "",
    mc: str = "",
    scope: str = "large",
    modid: str = "",
    package: str = "",
) -> dict[str, Any]:
    """生成大型模组的结构化计划。"""
    text = str(idea or "").strip()
    features = reference.detect_features(text)
    target = _target(scope)
    loader_name = str(loader or "NeoForge").strip()
    mc_version = str(mc or "").strip()
    modid = str(modid or "examplemod").strip() or "examplemod"
    package = str(package or f"com.example.{modid}").strip()

    packages: list[str] = []
    for feature in features:
        for item in FEATURE_PACKAGES.get(feature.id, ()):
            if item not in packages:
                packages.append(item)

    phases: list[Phase] = []
    phases.append(
        Phase(
            "bootstrap",
            "在线环境与工程骨架",
            "不依赖内置包：先拉 JDK / Gradle / 版本目录，再生成或打开工程。",
            [
                "resource_ensure kind=status 检查缺什么",
                "resource_ensure kind=all 在线准备 JDK 与版本目录",
                "打开工程或 scaffold_project / studio_generate 生成骨架",
            ],
            ["resource_ensure", "catalog_sync", "scaffold_project", "studio_generate"],
            ["data/tools/jdk-21", "data/gradle", "data/catalog", "build.gradle"],
        )
    )
    phases.append(
        Phase(
            "facts",
            "版本事实与工程契约核对",
            "先确认目标版本的 Java、元数据、数据目录、ID API 与构建插件，"
            "避免把别的版本写法套进来。",
            [
                "version_facts 查目标版本事实",
                "project_fact_check 扫描现有工程的版本冲突",
                "与官方 sources 冲突时以 dev_docs 为准",
            ],
            ["version_facts", "project_fact_check", "dev_docs"],
            ["build.gradle", "gradle.properties", "src/main/resources/META-INF"],
        )
    )
    phases.append(
        Phase(
            "spec",
            "规格与任务图",
            "把需求写成 mod-spec / GDD，再按功能拆成有依赖的任务。",
            [
                "large_mod_plan 输出阶段与包结构（本工具）",
                "studio_plan 生成 mod-spec.json 与 GDD.md",
                "按任务依赖排序，注册和骨架先于业务逻辑",
                "需要多版本/多加载器时先 multiversion_plan 定结构",
            ],
            ["large_mod_plan", "studio_plan", "multiversion_plan"],
            [".dma/spec.json", "GDD.md"],
        )
    )
    phases.append(
        Phase(
            "reference",
            "在线参考与 API 核对",
            "实现前先看目标版本的官方源码和成熟模组怎么写的。",
            [
                "dev_docs lookup 查目标版本 API 与签名",
                "mod_reference auto 自动搜索、下载、反编译相关模组",
                "arch_ref 检索大型模组架构范式（多方块/材料/能力网络）",
                "只读参考，不把第三方源码复制进工程",
            ],
            ["dev_docs", "mod_reference", "arch_ref", "search_code", "github_file"],
            ["data/online/docs", "data/library/sources"],
        )
    )
    phases.append(
        Phase(
            "registry",
            "注册中心与工程契约",
            "所有内容先集中到注册中心，避免类名、包名、注册 id 前后不一致。",
            [
                "建立 ModRegistries / DeferredRegister 或 Fabric Registry",
                "先写注册契约，再写每个内容的实现",
                "生成资源路径常量与语言键约定",
            ],
            ["write_file", "edit_file", "symbol_query", "lint_project"],
            _entry_files(loader_name, package, modid),
        )
    )

    for feature in features:
        tasks = list(FEATURE_TASKS.get(feature.id, ()))
        if not tasks:
            tasks = [f"实现 {feature.label} 的核心逻辑", "补齐资源与本地化", "写测试或场景卡片"]
        phases.append(
            Phase(
                f"feature-{feature.id}",
                f"功能：{feature.label}",
                feature.notes or f"实现 {feature.label} 的完整行为。",
                tasks,
                ["mod_reference", "dev_docs", "write_file", "edit_file", "lint_project"],
                [
                    f"src/main/java/{package.replace('.', '/')}/{item}"
                    for item in FEATURE_PACKAGES.get(feature.id, ())
                ],
            )
        )

    has_client = any(
        item.id in {"entity", "gui", "rendering", "skin", "social", "vehicle", "config"}
        for item in features
    )
    if has_client:
        phases.append(
            Phase(
                "client",
                "客户端表现与端侧隔离",
                "渲染、输入、界面只能放客户端；服务端不能引用客户端类。",
                [
                    "按加载器建立 client 入口或 @OnlyIn / DistExecutor 隔离",
                    "把 Screen、Renderer、Model、Particle 放到 client 包",
                    "验证专用服务器启动不加载客户端类",
                ],
                ["write_file", "lint_project", "mcdev_prepare", "mcdev_run_dump"],
                [f"src/main/java/{package.replace('.', '/')}/client"],
            )
        )

    phases.append(
        Phase(
            "assets",
            "资源、数据与本地化",
            "模型、纹理、blockstate、配方、战利品、语言文件全部落盘并校验。",
            [
                "pixel_art / model_json / blockstate_json 生成视觉资源",
                "recipe_json / loot_table_json / lang_json 生成数据",
                "validate_resources 与 asset_lint 逐条修复",
            ],
            [
                "pixel_art", "model_json", "blockstate_json", "recipe_json",
                "loot_table_json", "lang_json", "validate_resources", "asset_lint",
            ],
            ["src/main/resources/assets", "src/main/resources/data"],
        )
    )
    phases.append(
        Phase(
            "verify",
            "编译、运行与验收",
            "只用真实结果收尾：JAR、启动日志、GameTest 或探针断言。",
            [
                "build_project 编译并解析错误",
                "mcdev_prepare 启动开发端，mcdev_run_dump 看日志",
                "GameTest / 场景卡片断言关键行为",
                "matrix_plan 生成多版本兼容矩阵",
            ],
            [
                "build_project", "mcdev_prepare", "mcdev_run_dump",
                "mcdev_gametest", "mcdev_scenario", "matrix_plan",
            ],
            ["build/libs", ".mcmod/build-report.json"],
        )
    )
    phases.append(
        Phase(
            "release",
            "打包与发布",
            "生成 JAR、元数据和更新清单，明确未验证项。",
            [
                "studio_package 或手工打包 JAR",
                "写清加载器、MC 版本、依赖与许可证",
                "列出仍需人工确认的游戏内行为",
            ],
            ["studio_package", "build_project"],
            ["build/libs/*.jar"],
        )
    )

    return {
        "idea": text,
        "loader": loader_name,
        "mc": mc_version,
        "scope": scope,
        "modid": modid,
        "package": package,
        "features": [{"id": item.id, "label": item.label, "notes": item.notes} for item in features],
        "packages": packages,
        "phases": [
            {
                "id": item.id,
                "title": item.title,
                "goal": item.goal,
                "tasks": item.tasks,
                "tools": item.tools,
                "outputs": item.outputs,
            }
            for item in phases
        ],
        "target": target,
        "gate": {
            "classes": target["classes"],
            "packages": target["packages"],
            "phases": target["phases"],
            "tests": target["tests"],
            "note": (
                "这是大型应用能力门槛，不是安装包体积。"
                "低于门槛时不要宣称“大型模组已完成”。"
            ),
        },
        "online": {
            "dev_docs": "dev_docs lookup（按需下载 sources.jar）",
            "reference": "mod_reference auto（按功能搜索并反编译成熟模组）",
            "architecture": "arch_ref lookup（Create/GregTech/Mekanism/Botania 结构范式）",
            "resources": "resource_ensure（JDK / Gradle / MDK 在线拉取）",
            "facts": "version_facts + project_fact_check（版本事实与工程冲突）",
            "multiversion": "multiversion_plan（Stonecutter/Architectury/MultiLoader）",
        },
    }


def render(plan: dict[str, Any]) -> str:
    lines = [
        f"# 大型模组计划：{plan.get('idea') or '（未命名）'}",
        "",
        f"- 加载器：{plan.get('loader')}",
        f"- Minecraft：{plan.get('mc') or '未指定'}",
        f"- 规模：{plan.get('scope')}",
        f"- Mod ID：{plan.get('modid')}",
        f"- 包名：{plan.get('package')}",
    ]
    features = plan.get("features") or []
    if features:
        lines.append("- 识别功能：" + "、".join(item["label"] for item in features))
    lines.append("")
    lines.append("## 能力门槛")
    gate = plan.get("gate") or {}
    lines.append(
        f"- 目标：{gate.get('classes')} 个类 / {gate.get('packages')} 个功能包 / "
        f"{gate.get('phases')} 个阶段 / {gate.get('tests')} 个测试或场景"
    )
    lines.append(f"- {gate.get('note')}")
    lines.append("")
    lines.append("## 阶段")
    for index, phase in enumerate(plan.get("phases") or [], start=1):
        lines.append(f"\n### {index}. {phase.get('title')}")
        lines.append(f"{phase.get('goal')}")
        for task in phase.get("tasks") or []:
            lines.append(f"- [ ] {task}")
        tools = phase.get("tools") or []
        if tools:
            lines.append("工具：" + "、".join(tools))
        outputs = phase.get("outputs") or []
        if outputs:
            lines.append("产物/落点：" + "、".join(outputs))
    packages = plan.get("packages") or []
    if packages:
        lines.append("\n## 建议包结构")
        base = str(plan.get("package") or "").replace(".", "/")
        lines.extend(f"- src/main/java/{base}/{item}" for item in packages)
    online = plan.get("online") or {}
    if online:
        lines.append("\n## 在线资源策略")
        for key, value in online.items():
            lines.append(f"- {key}：{value}")
    return "\n".join(lines)
