"""Gradle script validation and repair hints (P0-12 / P1-40 / P3-33..36)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding


def _read(ctx: ProjectContext, *names: str) -> tuple[str, str]:
    for name in names:
        path = ctx.root / name
        if path.is_file():
            return name, ctx.read(path)
    return "", ""


def _mc_at_least(mc: str, major: int, minor: int) -> bool:
    match = re.match(r"(\d+)\.(\d+)", str(mc or ""))
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (major, minor)


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    build_name, build = _read(ctx, "build.gradle", "build.gradle.kts")
    settings_name, settings = _read(ctx, "settings.gradle", "settings.gradle.kts")
    _, properties = _read(ctx, "gradle.properties")
    toml_path = ctx.root / "gradle" / "libs.versions.toml"
    toml = ctx.read(toml_path) if toml_path.is_file() else ""
    combined = "\n".join([build, settings, properties])
    lowered = combined.lower()

    if not build_name:
        rows.append(finding(
            "P0-12", "缺少 build.gradle / build.gradle.kts",
            suggestion="使用受控模板生成构建脚本，不要手写空工程。",
        ))
        return rows
    if not settings_name:
        rows.append(finding(
            "P0-12", "缺少 settings.gradle，Gradle 无法确定工程名与仓库",
            suggestion="补 settings.gradle，声明 pluginManagement 与 rootProject.name。",
        ))

    loader = ctx.loader or ("NeoForge" if "neoforge" in lowered else "Fabric" if "fabric" in lowered else "Forge" if "forge" in lowered else "")
    if not loader:
        rows.append(finding(
            "P0-12", "无法从构建脚本判断 loader（NeoForge/Forge/Fabric）",
            suggestion="显式声明 loader 插件与版本。",
        ))
    elif loader == "NeoForge":
        if "net.neoforged.moddev" not in lowered and "net.neoforged.gradle" not in lowered:
            rows.append(finding(
                "P0-12", "NeoForge 工程缺少 ModDevGradle/NeoGradle 插件",
                path=build_name,
                suggestion="添加 id 'net.neoforged.moddev'（1.20.2+）、id 'net.neoforged.moddev.legacyforge'（1.20.1）或 id 'net.neoforged.gradle.userdev'。",
            ))
        if "neo_version" not in lowered and "neoforge" not in lowered:
            rows.append(finding(
                "P0-12", "NeoForge 依赖未声明",
                path=build_name,
                suggestion="声明 neo_version 并引入 net.neoforged:neoforge（1.20.2+）或 net.neoforged:forge:1.20.1-<版本>（1.20.1）。",
            ))
    elif loader == "Forge":
        if "net.minecraftforge.gradle" not in lowered:
            rows.append(finding(
                "P0-12", "Forge 工程缺少 ForgeGradle 插件",
                path=build_name,
                suggestion="添加 id 'net.minecraftforge.gradle' version '[6.0,6.2)'。",
            ))
        if not re.search(r"\bmappings\s*[({]", build):
            rows.append(finding(
                "P0-12", "ForgeGradle 缺少 mappings 配置",
                path=build_name,
                suggestion="在 minecraft { } 中声明 mappings channel: 'official', version: minecraft_version。",
            ))
    elif loader == "Fabric":
        if "fabric-loom" not in lowered:
            rows.append(finding(
                "P0-12", "Fabric 工程缺少 fabric-loom 插件",
                path=build_name,
                suggestion="添加 id 'fabric-loom' version '1.7-SNAPSHOT'。",
            ))
        if not re.search(r"\bmappings\s+['\"]", build):
            rows.append(finding(
                "P0-12", "Fabric 工程缺少 mappings 依赖",
                path=build_name,
                suggestion="mappings \"net.fabricmc:yarn:${yarn_mappings}:v2\"。",
            ))

    # Java toolchain
    version_match = (
        re.search(r"JavaLanguageVersion\.of\((\d+)\)", build)
        or re.search(r"sourceCompatibility\s*=\s*['\"]?(\d+)", build)
        or re.search(r"JavaVersion\.VERSION_(\d+)", build)
        or re.search(r"jvmToolchain\((\d+)\)", build)
    )
    java_version = int(version_match.group(1)) if version_match else 0
    if _mc_at_least(ctx.mc, 1, 21):
        required = 21
    elif _mc_at_least(ctx.mc, 1, 18):
        required = 17
    elif _mc_at_least(ctx.mc, 1, 17):
        required = 16
    else:
        required = 8
    if not java_version:
        rows.append(finding(
            "P0-12", "未声明 Java toolchain，构建会使用本机 JDK，结果不可复现",
            path=build_name,
            suggestion=f"java.toolchain.languageVersion = JavaLanguageVersion.of({required})。",
        ))
    elif java_version < required:
        rows.append(finding(
            "P0-12", f"Java 版本 {java_version} 低于目标 MC 要求的 {required}",
            path=build_name,
            suggestion=f"改为 JavaLanguageVersion.of({required})，并同步 IDEA/Gradle JDK。",
        ))

    # Mixin annotation processor / config
    uses_mixin = any(
        "@Mixin" in java.text for java in ctx.java_files
    )
    mixin_configs = list((ctx.root / "src" / "main" / "resources").rglob("*mixins*.json")) if (ctx.root / "src" / "main" / "resources").is_dir() else []
    if uses_mixin:
        if not mixin_configs:
            rows.append(finding(
                "P0-07", "使用了 @Mixin 但没有 mixins json 配置",
                suggestion="在 src/main/resources 下添加 <modid>.mixins.json 并在 mods.toml/fabric.mod.json 注册。",
            ))
        if loader == "Forge" and "annotationprocessor" not in lowered.replace(" ", ""):
            rows.append(finding(
                "P0-07", "Forge Mixin 缺少 annotationProcessor 依赖，refmap 不会生成",
                path=build_name,
                suggestion="annotationProcessor 'org.spongepowered:mixin:0.8.5:processor'。",
            ))
        if mixin_configs:
            for config in mixin_configs:
                rel = ctx.rel(config)
                data = ctx.load_json(config)
                if not isinstance(data, dict):
                    rows.append(finding("P0-07", "Mixin 配置不是合法 JSON", path=rel))
                    continue
                package = str(data.get("package") or "")
                if not package:
                    rows.append(finding(
                        "P0-07", "Mixin 配置缺少 package 字段", path=rel,
                        suggestion="package 必须指向 mixin 类所在包。",
                    ))
                elif not (ctx.root / "src" / "main" / "java" / Path(package.replace(".", "/"))).is_dir():
                    rows.append(finding(
                        "P0-07", f"Mixin package 指向的目录不存在：{package}", path=rel,
                        suggestion="把 mixin 类放进对应包，或修正 package。",
                    ))
                if not data.get("refmap") and loader in {"Forge", "NeoForge"} and not _mc_at_least(ctx.mc, 1, 20):
                    rows.append(finding(
                        "P0-07", "Mixin 配置缺少 refmap（旧版 Forge/NeoForge 必需）", path=rel,
                        suggestion="添加 \"refmap\": \"<modid>.refmap.json\"。",
                    ))

    # Data generation run
    if loader in {"Forge", "NeoForge"} and "rundata" not in lowered.replace(" ", "") and "data" not in lowered:
        rows.append(finding(
            "P1-11", "构建脚本没有 data/dataGen 运行配置，数据包生成会缺失",
            path=build_name, severity="warning",
            suggestion="添加 runs { data { ... } } 或 runData 任务。",
        ))

    # Version catalog (P1-40)
    hardcoded = re.findall(
        r"""['"]([\w.\-]+):([\w.\-]+):([0-9][\w.\-]*)['"]""", build
    )
    uses_catalog = "libs." in build or "versionCatalogs" in build
    if hardcoded and not toml and not uses_catalog:
        rows.append(finding(
            "P1-40", f"依赖版本散落在构建脚本中（{len(hardcoded)} 处），没有 version catalog",
            path=build_name, severity="warning",
            suggestion="迁移到 gradle/libs.versions.toml，用 libs.<alias> 引用。",
        ))
    if uses_catalog and not toml:
        rows.append(finding(
            "P1-40", "构建脚本引用了 libs.*，但缺少 gradle/libs.versions.toml",
            path=build_name,
        ))
    if toml:
        versions = re.findall(r"^([\w.\-]+)\s*=\s*['\"]([^'\"]+)['\"]", toml, re.MULTILINE)
        seen: dict[str, str] = {}
        for key, value in versions:
            seen.setdefault(value, key)
    else:
        seen = {}
    by_artifact: dict[str, set[str]] = {}
    for group, artifact, version in hardcoded:
        by_artifact.setdefault(f"{group}:{artifact}", set()).add(version)
    for artifact, versions in by_artifact.items():
        if len(versions) > 1:
            rows.append(finding(
                "P1-40", f"依赖 {artifact} 出现多个版本：{', '.join(sorted(versions))}",
                path=build_name,
                suggestion="统一版本，避免同一库多版本进入 classpath。",
            ))

    # Wrapper / Gradle version
    wrapper = ctx.root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not wrapper.is_file():
        rows.append(finding(
            "P0-12", "缺少 gradle wrapper，构建依赖本机 Gradle 版本",
            suggestion="提交 gradle/wrapper 与 gradlew，保证可复现构建。",
            severity="warning",
        ))
    else:
        match = re.search(r"gradle-(\d+)\.(\d+)", ctx.read(wrapper))
        if match and (int(match.group(1)), int(match.group(2))) < (8, 1):
            rows.append(finding(
                "P0-12", f"Gradle {match.group(1)}.{match.group(2)} 过旧，现代 loader 插件需要 8.1+",
                path=ctx.rel(wrapper), severity="warning",
                suggestion="升级到 Gradle 8.10.2 或与 loader 模板一致的版本。",
            ))

    # Formatting / CI / versioning (P3)
    if "spotless" not in lowered and "google-java-format" not in lowered:
        rows.append(finding(
            "P3-33", "未配置代码格式化（spotless / google-java-format）",
            path=build_name, severity="info",
            suggestion="添加 spotless 插件，提交前统一格式。",
        ))
    workflows = ctx.root / ".github" / "workflows"
    if not workflows.is_dir() or not list(workflows.glob("*.y*ml")):
        rows.append(finding(
            "P3-34", "没有 CI 流水线，构建/测试/静态检查不会自动跑",
            severity="info",
            suggestion="添加 .github/workflows/build.yml，至少执行 gradle build 与测试。",
        ))
    if re.search(r"^version\s*=\s*['\"]", build, re.MULTILINE) and "git" not in lowered:
        rows.append(finding(
            "P3-35", "版本号硬编码，未与 Git tag/commit 绑定",
            path=build_name, severity="info",
            suggestion="用 git describe 或环境变量生成版本号。",
        ))

    return rows
