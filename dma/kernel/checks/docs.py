"""License, provenance, metadata and disclosure checks (P3-07..12)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

NETWORK_TOKENS = ("HttpClient", "HttpRequest", "URL(", "openConnection", "new Socket", "WebSocket", "URI.create(")


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_license(ctx))
    rows.extend(_provenance(ctx))
    rows.extend(_metadata(ctx))
    rows.extend(_network(ctx))
    rows.extend(_changelog(ctx))
    rows.extend(_javadoc(ctx))
    return rows


def _root_files(ctx: ProjectContext) -> set[str]:
    try:
        return {path.name.lower() for path in ctx.root.iterdir() if path.is_file()}
    except OSError:
        return set()


def _license(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    files = _root_files(ctx)
    candidates = [name for name in ("license", "license.txt", "license.md", "copying", "copying.txt") if name in files]
    if not candidates:
        rows.append(finding(
            "P3-07", "仓库没有 LICENSE 文件",
            path="LICENSE", severity="warning",
            suggestion="明确许可证（MIT/Apache-2.0/LGPL 等），否则默认保留所有权利。",
        ))
    else:
        text = ctx.read_rel(candidates[0])
        if "all rights reserved" in text.lower():
            rows.append(finding(
                "P3-07", "LICENSE 声明保留所有权利，与公开分发矛盾",
                path=candidates[0], severity="warning",
                suggestion="选择允许分发的许可证，并在 README/发布页说明。",
            ))
        build = ""
        for name in ("build.gradle", "build.gradle.kts", "gradle.properties"):
            if ctx.exists(name):
                build += ctx.read_rel(name)
        declared = re.search(r"(?:license|mod_license)\s*[=:]\s*['\"]?([^'\"\n]+)", build)
        if declared:
            name = declared.group(1).strip()
            if name and name.lower() not in text.lower() and name.lower() not in candidates[0]:
                rows.append(finding(
                    "P3-07", f"构建声明的许可证 {name} 与 LICENSE 文件不一致",
                    path=candidates[0], severity="info",
                    suggestion="统一许可证名称与正文，NOTICE 中列出第三方素材。",
                ))
    if "notice" not in files and "third_party_notice.md" not in files:
        rows.append(finding(
            "P3-07", "没有 NOTICE，第三方/衍生素材归属不清晰",
            path="NOTICE", severity="info",
            suggestion="补 NOTICE，列出第三方库、原版衍生资源与作者。",
        ))
    return rows


def _provenance(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    assets = [
        path for path in ctx.glob("src/main/resources/assets/**/*")
        if path.is_file() and path.suffix.lower() in {".png", ".ogg", ".json", ".bbmodel"}
    ]
    files = _root_files(ctx)
    provenance = [name for name in ("assets.md", "credits.md", "notice", "sources.md") if name in files]
    if assets and not provenance:
        rows.append(finding(
            "P3-08", f"存在 {len(assets)} 个资源文件但没有资产溯源说明",
            path="ASSETS.md", severity="info",
            suggestion="ASSETS.md 注明：原版衍生 / AI 生成 / 外部素材及其许可证。",
        ))
    if any(path.suffix == ".bbmodel" for path in assets):
        rows.append(finding(
            "P3-08", "仓库包含 .bbmodel 源文件，发布前确认是否要一并分发",
            path="assets", severity="info",
        ))
    return rows


def _metadata(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    files = _root_files(ctx)
    readme = "readme.md" if "readme.md" in files else ("readme.txt" if "readme.txt" in files else "")
    if not readme:
        rows.append(finding(
            "P3-09", "没有 README，玩家无法了解安装/用法",
            path="README.md", severity="info",
            suggestion="补简介、依赖、安装步骤、配置说明与已知冲突。",
        ))
    else:
        text = ctx.read_rel(readme)
        if len(text.strip()) < 120:
            rows.append(finding(
                "P3-09", "README 内容过短，缺少安装/功能说明",
                path=readme, severity="info",
            ))
        if "install" not in text.lower() and "安装" not in text:
            rows.append(finding(
                "P3-09", "README 没有安装说明",
                path=readme, severity="info",
            ))
    icon = [
        name for name in ("icon.png", "logo.png")
        if (ctx.resource_root() / name).is_file()
    ]
    if not icon:
        rows.append(finding(
            "P3-09", "缺少 Mod 图标（resources/icon.png 或 logo.png）",
            path="src/main/resources/icon.png", severity="info",
        ))
    mods = ctx.resource_root() / "META-INF"
    for name in ("neoforge.mods.toml", "mods.toml"):
        path = mods / name
        if path.is_file():
            text = ctx.read(path)
            desc = re.search(r"^\s*description\s*=\s*\"([^\"]*)\"", text, re.MULTILINE)
            if desc and len(desc.group(1).strip()) < 10:
                rows.append(finding(
                    "P3-09", "mods.toml 的 description 过短",
                    path=ctx.rel(path), severity="info",
                ))
            if "logoFile" not in text and icon:
                rows.append(finding(
                    "P3-09", "存在图标但 mods.toml 没有 logoFile",
                    path=ctx.rel(path), severity="info",
                ))
    return rows


def _network(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    config_text = ""
    for java in ctx.java_files:
        if any(token in java.text for token in ("ConfigSpec", "ModConfigSpec", "Configuration", "ModConfig")):
            config_text += "\n" + java.text
    readme = ""
    for name in ("README.md", "README.txt"):
        if ctx.exists(name):
            readme += ctx.read_rel(name)
    for java in ctx.java_files:
        text = java.text
        hits = [token for token in NETWORK_TOKENS if token in text]
        if not hits:
            continue
        rel = ctx.rel(java.path)
        if not any(token in config_text for token in ("enable", "allowed", "telemetry", "network", "联网")):
            rows.append(finding(
                "P3-12", f"{Path(rel).name} 有联网功能但没有配置开关",
                path=rel, line=1, severity="warning",
                suggestion="提供 enable/disable 配置项，默认值遵循最小意外原则。",
            ))
        if not any(token in readme.lower() for token in ("network", "online", "联网", "telemetry", "隐私")):
            rows.append(finding(
                "P3-12", "README 没有告知联网功能与数据流向",
                path="README.md" if ctx.exists("README.md") else rel, severity="info",
                suggestion="说明联网目的、发送内容、关闭方式，并遵守平台隐私要求。",
            ))
    return rows


def _changelog(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    files = _root_files(ctx)
    if not any(name in files for name in ("changelog.md", "changelog.txt", "changes.md")):
        rows.append(finding(
            "P3-11", "没有 CHANGELOG，版本变更不可追溯",
            path="CHANGELOG.md", severity="info",
            suggestion="按版本记录新增/修复/破坏性变更，发布页复用该内容。",
        ))
    return rows


def _has_javadoc(text: str, line: int) -> bool:
    lines = text.splitlines()
    index = max(0, min(len(lines) - 1, line - 1))
    # Walk back over annotations/blank lines to find a closing doc comment.
    for cursor in range(index - 1, max(-1, index - 8), -1):
        value = lines[cursor].strip()
        if value.endswith("*/"):
            return True
        if value and not value.startswith("@") and not value.startswith("//"):
            if value.startswith("/**"):
                return True
            break
    return False


def _javadoc(ctx: ProjectContext) -> Iterable[Finding]:
    """P3-10: public API should carry Javadoc; examples should stay in sync."""
    rows: list[Finding] = []
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        for java_class in java.all_classes():
            if "public" not in java_class.modifiers:
                continue
            if not _has_javadoc(java.text, java_class.line):
                rows.append(finding(
                    "P3-10", f"公共类 {java_class.name} 没有 Javadoc",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="为公开 API 补类级 Javadoc，说明用途、线程约束与版本差异。",
                ))
            for method in java_class.methods:
                if "public" not in method.modifiers or method.is_constructor:
                    continue
                if method.annotation("Override"):
                    continue
                if not _has_javadoc(java.text, method.line):
                    rows.append(finding(
                        "P3-10", f"公共方法 {java_class.name}#{method.name} 没有 Javadoc",
                        path=rel, line=method.line, severity="info",
                        suggestion="补参数/返回值/异常说明；示例代码要与实际签名一致。",
                    ))
    return rows
