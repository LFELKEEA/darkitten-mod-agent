"""Packaging metadata and jar layout checks (P3-01..06)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_loader_metadata(ctx))
    rows.extend(_fabric(ctx))
    rows.extend(_pack_meta(ctx))
    rows.extend(_duplicates(ctx))
    rows.extend(_jar_layout(ctx))
    return rows


def _modid(ctx: ProjectContext) -> str:
    for name in ("gradle.properties", "build.gradle", "build.gradle.kts"):
        if ctx.exists(name):
            match = re.search(r"(?:mod_id|archivesBaseName)\s*[=:]\s*['\"]?([\w-]+)", ctx.read_rel(name))
            if match:
                return match.group(1)
    return ""


def _resolve(text: str, ctx: ProjectContext) -> str:
    """Expand ${key} placeholders from gradle.properties before validation."""
    if "${" not in text or not ctx.exists("gradle.properties"):
        return text
    values: dict[str, str] = {}
    for line in ctx.read_rel("gradle.properties").splitlines():
        match = re.match(r"\s*([\w.\-]+)\s*=\s*(.*?)\s*$", line)
        if match and not line.strip().startswith("#"):
            values[match.group(1)] = match.group(2)
    return re.sub(r"\$\{([\w.\-]+)\}", lambda m: values.get(m.group(1), m.group(0)), text)


def _metadata(ctx: ProjectContext) -> Path | None:
    base = ctx.resource_root() / "META-INF"
    for name in ("neoforge.mods.toml", "mods.toml"):
        if (base / name).is_file():
            return base / name
    return None


def _loader_metadata(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    path = _metadata(ctx)
    if path is None:
        if ctx.exists("src/main/resources/fabric.mod.json"):
            return rows
        if ctx.exists("build.gradle") or ctx.exists("build.gradle.kts"):
            rows.append(finding(
                "P3-01", "缺少 META-INF/mods.toml / neoforge.mods.toml",
                path="src/main/resources/META-INF", severity="error",
                suggestion="补加载器元数据，否则 Mod 不会被识别。",
            ))
        return rows
    text = _resolve(ctx.read(path), ctx)
    rel = ctx.rel(path)
    if path.name == "mods.toml" and ctx.loader == "NeoForge":
        modern = bool(re.match(r"1\.(2[1-9]|[3-9][0-9])", ctx.mc or ""))
        if modern:
            rows.append(finding(
                "P3-01", "NeoForge 1.21+ 应使用 neoforge.mods.toml",
                path=rel, severity="warning",
                suggestion="重命名为 neoforge.mods.toml，旧文件名在部分版本不再读取。",
            ))
    for key in ("modLoader", "loaderVersion", "license"):
        if not re.search(rf"^\s*{key}\s*=", text, re.MULTILINE):
            rows.append(finding(
                "P3-01", f"mods.toml 缺少顶层字段 {key}",
                path=rel, severity="warning",
            ))
    blocks = _mod_blocks(text)
    if not blocks:
        rows.append(finding(
            "P3-01", "mods.toml 没有 [[mods]] 段",
            path=rel, severity="error",
        ))
        return rows
    expected = _modid(ctx)
    for index, block in enumerate(blocks):
        for key in ("modId", "version", "displayName", "description"):
            if not re.search(rf"^\s*{key}\s*=", block, re.MULTILINE):
                rows.append(finding(
                    "P3-01", f"[[mods]] 第 {index + 1} 段缺少 {key}",
                    path=rel, severity="error" if key == "modId" else "warning",
                ))
        match = re.search(r"^\s*modId\s*=\s*\"([^\"]+)\"", block, re.MULTILINE)
        actual = match.group(1) if match else ""
        if actual and expected and "${" not in actual and actual != expected:
            rows.append(finding(
                "P3-01", f"modId={match.group(1)} 与 gradle mod_id={expected} 不一致",
                path=rel, severity="warning",
                suggestion="统一 modid，否则资源/配置路径会错位。",
            ))
        logo = re.search(r"^\s*logoFile\s*=\s*\"([^\"]+)\"", block, re.MULTILINE)
        if logo:
            target = ctx.resource_root() / logo.group(1)
            if not target.is_file():
                rows.append(finding(
                    "P3-09", f"logoFile 指向的文件不存在：{logo.group(1)}",
                    path=rel, severity="warning",
                ))
    if "[[dependencies" not in text:
        rows.append(finding(
            "P3-01", "mods.toml 没有声明依赖（建议至少声明 minecraft/neoforge 版本范围）",
            path=rel, severity="info",
        ))
    return rows


def _mod_blocks(text: str) -> list[str]:
    parts = re.split(r"^\[\[mods\]\]\s*$", text, flags=re.MULTILINE)
    return [part for part in parts[1:] if part.strip()]


def _fabric(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    path = ctx.resource_root() / "fabric.mod.json"
    if not path.is_file():
        if ctx.loader == "Fabric":
            rows.append(finding(
                "P3-01", "Fabric 项目缺少 fabric.mod.json",
                path="src/main/resources/fabric.mod.json", severity="error",
            ))
        return rows
    try:
        data = json.loads(ctx.read(path))
    except ValueError as exc:
        rows.append(finding(
            "P3-01", f"fabric.mod.json 解析失败：{exc}",
            path=ctx.rel(path), severity="error",
        ))
        return rows
    if not isinstance(data, dict):
        return rows
    rel = ctx.rel(path)
    for key in ("schemaVersion", "id", "version"):
        if key not in data:
            rows.append(finding(
                "P3-01", f"fabric.mod.json 缺少 {key}",
                path=rel, severity="error" if key == "id" else "warning",
            ))
    entrypoints = data.get("entrypoints")
    if not isinstance(entrypoints, dict) or "main" not in entrypoints:
        rows.append(finding(
            "P3-01", "fabric.mod.json 缺少 entrypoints.main",
            path=rel, severity="warning",
            suggestion="补 main 入口，否则 Mod 不初始化。",
        ))
    depends = data.get("depends") or {}
    for key in ("minecraft", "fabricloader"):
        if key not in depends:
            rows.append(finding(
                "P3-01", f"fabric.mod.json depends 缺少 {key}",
                path=rel, severity="warning",
            ))
    expected = _modid(ctx)
    fabric_id = str(data.get("id") or "")
    if expected and "${" not in fabric_id and fabric_id != expected:
        rows.append(finding(
            "P3-01", f"fabric.mod.json id={fabric_id} 与 gradle mod_id={expected} 不一致",
            path=rel, severity="warning",
        ))
    return rows


def _pack_meta(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    path = ctx.resource_root() / "pack.mcmeta"
    if not path.is_file():
        return rows
    data = ctx.load_json(path)
    if not isinstance(data, dict):
        return rows
    pack = data.get("pack")
    if not isinstance(pack, dict):
        rows.append(finding(
            "P3-01", "pack.mcmeta 缺少 pack 段",
            path=ctx.rel(path), severity="error",
        ))
        return rows
    if "pack_format" not in pack:
        rows.append(finding(
            "P3-01", "pack.mcmeta 缺少 pack_format",
            path=ctx.rel(path), severity="warning",
        ))
    description = pack.get("description")
    if isinstance(description, str) and description.strip().lower() in {"", "description"}:
        rows.append(finding(
            "P3-09", "pack.mcmeta 的 description 占位未填写",
            path=ctx.rel(path), severity="info",
        ))
    return rows


def _duplicates(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    main = ctx.resource_root()
    generated = ctx.root / "src" / "generated" / "resources"
    if not main.is_dir() or not generated.is_dir():
        return rows
    main_files = {path.relative_to(main).as_posix() for path in main.rglob("*") if path.is_file()}
    generated_files = {path.relative_to(generated).as_posix() for path in generated.rglob("*") if path.is_file()}
    for name in sorted(main_files & generated_files):
        rows.append(finding(
            "P3-03", f"资源重复：{name} 同时存在于 src/main 与 src/generated",
            path=name, severity="warning",
            suggestion="只保留一个来源，或确认 Gradle 去重策略。",
        ))
    return rows


def _jar_layout(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    build = ""
    for name in ("build.gradle", "build.gradle.kts"):
        if ctx.exists(name):
            build += ctx.read_rel(name)
    if not build:
        return rows
    if "module-info.java" in {path.name for path in ctx.root.rglob("module-info.java")}:
        if "jarJar" in build:
            rows.append(finding(
                "P3-03", "项目包含 module-info.java 同时使用 jarJar，内嵌库可能无法解析",
                path="module-info.java", severity="info",
                suggestion="JPMS 与加载器类路径混用时，避免对内嵌库声明 requires。",
            ))
    if "signing" not in build:
        rows.append(finding(
            "P3-04", "构建脚本没有签名配置，发布包无法校验来源",
            path="build.gradle", severity="info",
            suggestion="CI 中使用签名密钥，或至少发布 SHA-256 校验值。",
        ))
    if re.search(r"natives\s*[:\-]", build) or "lwjgl-natives" in build:
        rows.append(finding(
            "P3-05", "包含原生库依赖，需要按平台拆分/校验",
            path="build.gradle", severity="info",
            suggestion="不要把多平台原生库打成一个 jar；按 OS/arch 选择并在缺失时降级。",
        ))
    if "entryCompression" in build and "STORED" in build:
        rows.append(finding(
            "P3-06", "jar 使用 STORED 压缩，包体积会明显增大",
            path="build.gradle", severity="info",
            suggestion="确认是否为了签名/热重载；否则保持默认 DEFLATED。",
        ))
    if "jarJar" in build:
        for match in re.finditer(r"jarJar\s*\(\s*['\"]([^'\"]+)['\"]\s*\)", build):
            if ":" not in match.group(1) or "@" in match.group(1):
                continue
            rows.append(finding(
                "P3-02", f"jarJar 依赖 {match.group(1)} 没有明确版本范围",
                path="build.gradle", severity="info",
                suggestion="锁定版本并确认与加载器内置版本不冲突。",
            ))
    return rows
