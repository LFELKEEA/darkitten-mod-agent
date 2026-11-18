"""多版本 / 多加载器工程策略。

“同一份代码发布多个 MC 版本或同时支持 Fabric + NeoForge”不是改几个版本号，
而是先选结构：Stonecutter 单库预处理、Architectury 公共层、MultiLoader 模板、
Modstitch 目标矩阵。选错结构，后面每加一个版本都要复制一遍工程。

本模块给出策略推荐、目录骨架、官方插件坐标与验证步骤。生成的是方案文本，
真正落盘前必须按对应官方模板核对当前插件版本；这里不假装能替代构建验证。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Strategy:
    id: str
    name: str
    best_for: str
    not_for: str
    docs: tuple[str, ...]
    plugin_ids: tuple[str, ...]
    steps: tuple[str, ...]
    pitfalls: tuple[str, ...]
    files: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "best_for": self.best_for,
            "not_for": self.not_for,
            "docs": list(self.docs),
            "plugin_ids": list(self.plugin_ids),
            "steps": list(self.steps),
            "pitfalls": list(self.pitfalls),
            "files": dict(self.files),
        }


def _stonecutter_files(targets: list[tuple[str, str]], active: str) -> dict[str, str]:
    """生成 Stonecutter 骨架。targets = [(target_name, mc_version)]。"""
    entries = "\n".join(
        f'        vers("{name}", "{mc}")' for name, mc in targets
    )
    deps = []
    for name, mc in targets:
        deps.append(
            f"# versions/dependencies/{name}.properties\n"
            f"# 该目标独立的依赖 pin；与 settings 里的目标名一致。\n"
            f"minecraft_version={mc}\n"
            f"mod_version=1.0.0\n"
            f"# loader 相关版本按该目标加载器补充\n"
        )
    settings = f"""// settings.gradle（Groovy 示例；Kotlin DSL 把 vers 换成 vers(...) 即可）
pluginManagement {{
    repositories {{
        mavenCentral()
        gradlePluginPortal()
        maven {{ url = "https://maven.kikugie.dev/releases" }}
        maven {{ url = "https://maven.fabricmc.net/" }}
        maven {{ url = "https://maven.architectury.dev" }}
        maven {{ url = "https://maven.minecraftforge.net" }}
        maven {{ url = "https://maven.neoforged.net/releases/" }}
    }}
}}
plugins {{
    id "dev.kikugie.stonecutter" version "0.9.4"
}}
stonecutter {{
    centralScript = "build.gradle"
    shared {{
{entries}
    }}
    create(rootProject)
}}
rootProject.name = "YourMod"
"""
    stonecutter_gradle = f"""// stonecutter.gradle
plugins {{
    id "dev.kikugie.stonecutter"
}}
// 当前激活目标；Stonecutter 会据此切换源码集
stonecutter active "{active}"
"""
    return {
        "settings.gradle": settings,
        "stonecutter.gradle": stonecutter_gradle,
        "versions/dependencies/README.txt": "\n".join(deps),
        "src/main/java/.../VersionGate.java": """// 预处理指令示例：按目标版本/加载器裁剪代码
//? if >=1.21 {
import net.minecraft.core.registries.Registries;
//?} else {
/* import net.minecraft.core.Registry; */
//?}

public final class VersionGate {
    //? if fabric {
    public static final String PLATFORM = "fabric";
    //?} else if neoforge {
    /* public static final String PLATFORM = "neoforge"; */
    //?} else {
    /* public static final String PLATFORM = "other"; */
    //?}
}
""",
    }


def _architectury_files(targets: list[tuple[str, str]], active: str) -> dict[str, str]:
    return {
        "settings.gradle": """// Architectury 多加载器骨架
pluginManagement {
    repositories {
        maven { url = "https://maven.architectury.dev" }
        maven { url = "https://maven.fabricmc.net/" }
        maven { url = "https://maven.neoforged.net/releases/" }
        gradlePluginPortal()
    }
}
include "common"
include "fabric"
include "neoforge"
rootProject.name = "YourMod"
""",
        "gradle.properties": f"""# 版本必须来自同一套官方模板生成器，不要手工拼
mod_version=1.0.0
minecraft_version={active}
enabled_platforms=fabric,neoforge
# architectury_version / fabric_loader_version / fabric_api_version /
# neoforge_version / loom_version 从生成器或已知可用工程复制
""",
        "common/build.gradle": """plugins {
    id "dev.architectury.loom"
}
// common 只依赖原版与 Architectury API，禁止直接 import Fabric/NeoForge 类
""",
        "fabric/build.gradle": """plugins {
    id "com.github.johnrengelman.shadow"
}
// Fabric 入口 + 平台实现；assets 尽量放 common
""",
        "neoforge/build.gradle": """plugins {
    id "com.github.johnrengelman.shadow"
}
// NeoForge 入口 + 平台实现；元数据放 neoforge/src/main/resources/META-INF
""",
        "common/src/main/java/.../PlatformHelper.java": """package com.example.mymod.platform;

import dev.architectury.injectables.annotations.ExpectPlatform;

public final class PlatformHelper {
    @ExpectPlatform
    public static boolean isModLoaded(String modId) {
        throw new AssertionError("ExpectPlatform implementation not found");
    }
}
""",
        "fabric/src/main/java/.../platform/fabric/PlatformHelperImpl.java": """package com.example.mymod.platform.fabric;

import net.fabricmc.loader.api.FabricLoader;

public final class PlatformHelperImpl {
    public static boolean isModLoaded(String modId) {
        return FabricLoader.getInstance().isModLoaded(modId);
    }
}
""",
        "neoforge/src/main/java/.../platform/neoforge/PlatformHelperImpl.java": """package com.example.mymod.platform.neoforge;

import net.neoforged.fml.ModList;

public final class PlatformHelperImpl {
    public static boolean isModLoaded(String modId) {
        return ModList.get().isLoaded(modId);
    }
}
""",
    }


def _multiloader_files(targets: list[tuple[str, str]], active: str) -> dict[str, str]:
    return {
        "settings.gradle": """// MultiLoader-Template 风格：一个 Gradle 构建，common + 平台源码集
pluginManagement {
    repositories {
        maven { url = "https://maven.fabricmc.net/" }
        maven { url = "https://maven.neoforged.net/releases/" }
        gradlePluginPortal()
    }
}
include "common"
include "fabric"
include "neoforge"
rootProject.name = "YourMod"
""",
        "gradle.properties": f"""mod_version=1.0.0
minecraft_version={active}
enabled_platforms=fabric,neoforge
# 各平台插件版本按官方模板固定
""",
        "common/build.gradle": """// 只放原版 API + 共享逻辑；不引入任何加载器类
""",
        "fabric/build.gradle": """// fabric-loom + fabric.mod.json
""",
        "neoforge/build.gradle": """// ModDevGradle（1.20.2+）/ NeoGradle（1.20.1）
""",
        "README.md": """# 多加载器布局

- common：共享代码，只依赖原版。
- fabric / neoforge：入口、平台实现、元数据、加载器专属资源。
- 用 Gradle 依赖把 common 打进各平台产物；不要把平台类放进 common。
""",
    }


def _modstitch_files(targets: list[tuple[str, str]], active: str) -> dict[str, str]:
    entries = "\n".join(
        f'        vers("{name}", "{mc}")' for name, mc in targets
    )
    props = "\n\n".join(
        f"# versions/{name}/gradle.properties\n"
        f"modstitch.platform=loom        # fabric 用 loom；neoforge 用 moddevgradle；forge 用 moddevgradle-legacy\n"
        f"deps.minecraft={mc}\n"
        f"# 对应平台再补 deps.neoforge / deps.forge / deps.neoform"
        for name, mc in targets
    )
    return {
        "settings.gradle.kts": f"""pluginManagement {{
    repositories {{
        mavenCentral()
        gradlePluginPortal()
        maven("https://maven.kikugie.dev/releases")
        maven("https://maven.fabricmc.net/")
        maven("https://maven.neoforged.net/releases/")
    }}
}}
plugins {{
    id("dev.kikugie.stonecutter") version "0.9.4"
}}
stonecutter {{
    kotlinController = true
    centralScript = "build.gradle.kts"
    create(rootProject) {{
{entries}
    }}
}}
rootProject.name = "YourMod"
""",
        "targets.properties": props,
        "README.md": """# Modstitch 目标矩阵

- modstitch 用一份 Kotlin DSL 描述多个目标（Fabric/NeoForge/Forge/Vanilla）。
- 每个目标在 versions/<target>/gradle.properties 里声明 modstitch.platform 与 deps.*。
- 目标类型与必填属性以 Modstitch 官方文档为准；插件版本随模板更新。
""",
    }


STRATEGIES: dict[str, Strategy] = {
    "stonecutter": Strategy(
        id="stonecutter",
        name="Stonecutter：单代码库跨多个 MC 版本",
        best_for="同一个加载器、需要同时维护多个 MC 版本（如 1.20.1 + 1.21.1）。",
        not_for="同时维护多个加载器；跨加载器仍需 Architectury/MultiLoader。",
        docs=("https://stonecutter.kikugie.dev/", "https://github.com/kikugie/stonecutter"),
        plugin_ids=("dev.kikugie.stonecutter 0.9.x",),
        steps=(
            "在 settings.gradle 声明目标（目标名建议 <mc>-<loader>）。",
            "为每个目标建 versions/dependencies/<target>.properties。",
            "用 //? if <条件> { ... //?} 包裹版本差异代码，先跑 active 目标构建。",
            "逐个切换目标构建 + GameTest；不要只验证最新版本。",
        ),
        pitfalls=(
            "预处理指令不能嵌套表达式；条件写法以官方文档为准。",
            "目标名与依赖文件名必须完全一致。",
            "Stonecutter 只裁剪代码，不解决 API 语义差异；差异大时抽适配层。",
        ),
    ),
    "architectury": Strategy(
        id="architectury",
        name="Architectury：common + 平台子项目",
        best_for="同一 MC 版本同时支持 Fabric 与 NeoForge，且希望共享大部分代码。",
        not_for="只需要一个加载器；或需要跨大量 MC 版本（叠加 Stonecutter）。",
        docs=("https://docs.architectury.dev/", "https://github.com/architectury/architectury-templates"),
        plugin_ids=("architectury-plugin", "dev.architectury.loom"),
        steps=(
            "用官方模板生成器创建 Multiplatform 工程，保留生成的 Gradle 布局与版本 pin。",
            "common 只依赖原版 + Architectury API。",
            "平台差异用 @ExpectPlatform，实现类按 <包>.fabric/.neoforge 命名。",
            "两个平台分别 build + runClient/runServer 验证。",
        ),
        pitfalls=(
            "common 里 import 平台类会在另一平台编译失败。",
            "DeferredRegister 必须用 Architectury 的版本，不能混用 NeoForge 的。",
            "资源尽量放 common，避免两平台各维护一份。",
        ),
    ),
    "multiloader": Strategy(
        id="multiloader",
        name="MultiLoader-Template：最小抽象的共享源码",
        best_for="Fabric + NeoForge 同版本，想自己控制源码集与依赖，不想引入 Architectury 抽象。",
        not_for="需要大量跨平台统一 API；团队更熟悉 Architectury 时优先 Architectury。",
        docs=("https://github.com/Jaredlll08/MultiLoader-Template",),
        plugin_ids=("fabric-loom", "net.neoforged.moddev / net.neoforged.gradle.userdev"),
        steps=(
            "按模板建 common/fabric/neoforge 三个子项目。",
            "平台入口各自初始化，共享逻辑放 common。",
            "分别构建两个平台产物并启动验证。",
        ),
        pitfalls=(
            "common 不能出现任何加载器类。",
            "两边资源目录结构不同（fabric.mod.json vs neoforge.mods.toml）。",
            "依赖版本要按各平台分别声明。",
        ),
    ),
    "modstitch": Strategy(
        id="modstitch",
        name="Modstitch：Kotlin DSL 目标矩阵",
        best_for="需要 Fabric/NeoForge/Forge/Vanilla 多目标，且团队接受 Kotlin DSL。",
        not_for="只用 Groovy 构建；或只需要单加载器单版本。",
        docs=("https://isxander.github.io/modstitch-docs/",),
        plugin_ids=("dev.kikugie.stonecutter", "modstitch"),
        steps=(
            "按官方 Getting Started 建 settings.gradle.kts 目标。",
            "每个目标声明 modstitch.platform 与 deps.minecraft。",
            "按目标补 deps.neoforge / deps.forge / deps.neoform。",
            "逐目标构建；Vanilla 目标不能用 Mixin。",
        ),
        pitfalls=(
            "Vanilla 目标不能含 Mixin 配置。",
            "目标属性缺一个就构建失败，按文档表核对。",
            "插件版本与 Stonecutter 版本要配套。",
        ),
    ),
    "stonecraft": Strategy(
        id="stonecraft",
        name="Stonecraft：Stonecutter + 多加载器编排",
        best_for="多 MC 版本 × 多加载器的组合矩阵。",
        not_for="单版本单加载器，纯属增加复杂度。",
        docs=("https://stonecraft.meza.gg/", "https://github.com/meza/Stonecraft-template"),
        plugin_ids=("gg.meza.stonecraft 1.9+", "dev.kikugie.stonecutter 0.8+"),
        steps=(
            "从 Stonecraft 模板开始，不要手工拼。",
            "settings.gradle.kts 里用 mc(version, loaders...) 生成目标。",
            "versions/dependencies/<target>.properties 管理各目标依赖。",
            "逐目标构建 + 运行验证。",
        ),
        pitfalls=(
            "Stonecraft 必须在 Architectury 插件之前应用。",
            "目标名、依赖文件名、CI 矩阵必须一致。",
            "版本矩阵越大，验证成本越高，先锁定主目标。",
        ),
    ),
}


def choose(loaders: list[str], mc_versions: list[str]) -> str:
    names = [item.strip().lower() for item in loaders if item.strip()]
    versions = [item.strip() for item in mc_versions if item.strip()]
    multi_loader = len(set(names)) > 1
    multi_version = len(set(versions)) > 1
    if multi_loader and multi_version:
        return "stonecraft"
    if multi_loader:
        return "architectury"
    if multi_version:
        return "stonecutter"
    return "single"


def plan(loaders: list[str], mc_versions: list[str], modid: str = "yourmod") -> str:
    """生成多版本/多加载器方案（Markdown）。"""
    names = [item.strip() for item in loaders if item.strip()]
    versions = [item.strip() for item in mc_versions if item.strip()]
    if not names:
        names = ["fabric"]
    if not versions:
        versions = ["1.21.1"]
    strategy_id = choose(names, versions)
    if strategy_id == "single":
        return (
            f"单加载器单版本（{names[0]} / {versions[0]}）不需要多版本工程。\n"
            "直接按当前工程的骨架构建；只有新增第二个版本或加载器时再引入 Stonecutter/Architectury。"
        )
    strategy = STRATEGIES[strategy_id]
    targets = [(f"{mc}-{loader}", mc) for mc in versions for loader in names]
    active = targets[-1][0]
    files = getattr(strategy, "files", {}) or {}
    if strategy_id == "stonecutter":
        files = _stonecutter_files(targets, active)
    elif strategy_id == "architectury":
        files = _architectury_files(targets, active)
    elif strategy_id == "multiloader":
        files = _multiloader_files(targets, active)
    elif strategy_id == "modstitch":
        files = _modstitch_files(targets, active)
    elif strategy_id == "stonecraft":
        files = _stonecutter_files(targets, active)
        files["settings.gradle"] = files["settings.gradle"].replace(
            'id "dev.kikugie.stonecutter" version "0.9.4"',
            'id "gg.meza.stonecraft" version "1.9.+"\n    id "dev.kikugie.stonecutter" version "0.8.+"',
        )

    lines = [
        f"# 多版本方案：{strategy.name}",
        "",
        f"- 加载器：{', '.join(names)}",
        f"- MC 版本：{', '.join(versions)}",
        f"- 目标：{', '.join(name for name, _ in targets)}",
        f"- 适用：{strategy.best_for}",
        f"- 不适用：{strategy.not_for}",
        "",
        "## 落地步骤",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(strategy.steps, 1))
    lines.append("")
    lines.append("## 官方插件坐标")
    lines.extend(f"- {item}" for item in strategy.plugin_ids)
    lines.append("")
    lines.append("## 常见坑")
    lines.extend(f"- {item}" for item in strategy.pitfalls)
    lines.append("")
    lines.append("## 参考文档")
    lines.extend(f"- {item}" for item in strategy.docs)
    if files:
        lines.append("")
        lines.append("## 骨架文件（示例，需按官方模板核对版本）")
        for path, content in files.items():
            lines.append("")
            lines.append(f"### {path}")
            lines.append("```")
            lines.append(content.rstrip())
            lines.append("```")
    lines.append("")
    lines.append("方案只是起点：必须逐个目标跑 build，并在至少一个目标上启动开发端验证。"
                 "不要把“生成了目录”当成“多版本可用”。")
    return "\n".join(lines)


def catalog() -> str:
    lines = ["多版本 / 多加载器策略："]
    for item in STRATEGIES.values():
        lines.append(f"- {item.id}｜{item.name}")
        lines.append(f"  适用：{item.best_for}")
    lines.append("用 multiversion_plan loaders=[...] mc_versions=[...] 生成方案。")
    return "\n".join(lines)