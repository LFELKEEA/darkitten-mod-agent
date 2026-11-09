"""Ecosystem compatibility checks (P3-13, P3-14, P3-15, P3-17, P3-18)."""

from __future__ import annotations

import json
import re
from typing import Iterable

from .common import ProjectContext, finding

# Mods that are known to fight with common rendering / optimisation stacks.
CONFLICTS: list[tuple[tuple[str, ...], str, str]] = [
    (("optifine", "embeddium"), "OptiFine 与 Embeddium 的渲染注入互相覆盖",
     "只保留一个，或改用 Sodium/Embeddium + Iris 组合"),
    (("optifine", "sodium"), "OptiFine 与 Sodium 的渲染管线不兼容",
     "二选一；在文档中标注并给出替代方案"),
    (("rubidium", "sodium"), "Rubidium 是 Sodium 的 Forge 移植，不能同时装",
     "按 loader 只选一个"),
    (("performant", "sodium"), "Performant 与 Sodium 修改同一批区块/实体路径",
     "二选一，并在冲突库中登记"),
]

_GLOBAL_PATTERNS = (
    (re.compile(r"\b(randomTickSpeed|tickRate|spawnCap|mobSpawnCap|naturalSpawn)\b"), "修改原版全局生成/计时参数"),
    (re.compile(r"\b(getServer\(\)\.setTickRate|setRandomTickSpeed)\b"), "运行时修改服务器全局 tick 参数"),
    (re.compile(r"\bSpawnPlacements\.register\b"), "修改生成规则但未提供配置开关"),
)

_INTEGRATIONS = {
    "mezz.jei": ("JEI", "client"),
    "dev.emi.emi": ("EMI", "client"),
    "me.shedaniel.rei": ("REI", "client"),
    "snownee.jade": ("Jade", "client"),
}


def run(ctx: ProjectContext) -> Iterable[Finding]:
    yield from _globals(ctx)
    yield from _namespaces(ctx)
    yield from _tags(ctx)
    yield from _integrations(ctx)
    yield from _conflicts(ctx)


# ----------------------------------------------------------------------
def _globals(ctx: ProjectContext) -> Iterable[Finding]:
    config_keys = _config_keys(ctx)
    for java, java_class in ctx.classes():
        text = ctx.read(java.path)
        rel = ctx.rel(java.path)
        if "config" in java_class.name.lower():
            continue
        for pattern, label in _GLOBAL_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue
            if config_keys and any(key.lower() in text.lower() for key in config_keys):
                continue
            yield finding(
                "P3-13",
                f"{java_class.name} {label}，但没有绑定配置开关",
                path=rel,
                line=_line(text, match.start()),
                severity="info",
                suggestion="把全局影响项提升为配置项（默认保持原版值），并在文档说明影响范围。",
            )
            break


def _namespaces(ctx: ProjectContext) -> Iterable[Finding]:
    modid = _modid(ctx)
    if not modid:
        return
    for java, java_class in ctx.classes():
        text = ctx.read(java.path)
        rel = ctx.rel(java.path)
        for match in re.finditer(r'Commands\.literal\s*\(\s*"([^"]+)"', text):
            name = match.group(1)
            if name.lower() in {"help", "reload", "list"}:
                continue
            if modid.lower() not in name.lower():
                yield finding(
                    "P3-14",
                    f"命令字面量 `{name}` 没有 {modid} 前缀，容易与其他 Mod 冲突",
                    path=rel,
                    line=_line(text, match.start()),
                    severity="warning",
                    suggestion=f'改用 "{modid}" 作为根命令，或 "{modid}:{name}" 形式。',
                )
        for match in re.finditer(
            r'(getOrCreateObjective|getOrCreateScore|setStorage|storage)\s*\(\s*"([^":]+)"',
            text,
        ):
            name = match.group(2)
            if modid.lower() not in name.lower():
                yield finding(
                    "P3-14",
                    f"记分板/storage 名称 `{name}` 未带命名空间前缀",
                    path=rel,
                    line=_line(text, match.start()),
                    severity="warning",
                    suggestion=f'统一使用 "{modid}.{name}"。',
                )


def _tags(ctx: ProjectContext) -> Iterable[Finding]:
    """Custom blocks/items should expose tag-based filters (P3-15)."""
    if not ctx.exists("src/main/resources/data"):
        return
    java_text = "\n".join(ctx.read(java.path) for java, _ in ctx.classes())
    if not re.search(r"\b(BlockTags|ItemTags|TagKey)\b", java_text):
        has_custom = bool(re.search(r"\b(DeferredBlock|DeferredItem|RegistryObject|Block\b|Item\b)", java_text))
        if has_custom:
            yield finding(
                "P3-15",
                "自定义方块/物品没有提供标签过滤入口",
                path="src/main/java",
                severity="info",
                suggestion="为掉落、生成、效果等逻辑增加 TagKey 白/黑名单，并支持数据包热重载。",
            )
        return
    # Tags exist: check for hard-coded identity checks next to tag checks.
    for java, java_class in ctx.classes():
        text = ctx.read(java.path)
        if "Tag" not in text:
            continue
        hard = re.search(r"==\s*[A-Z]\w*\.(BLOCK|ITEM|ENTITY_TYPE)\b", text)
        if hard:
            yield finding(
                "P3-15",
                f"{java_class.name} 同时存在标签与硬编码对象比较，硬编码分支会绕开数据包配置",
                path=ctx.rel(java.path),
                line=_line(text, hard.start()),
                severity="info",
                suggestion="把硬编码比较改成 TagKey 判断，或在配置中显式记录例外。",
            )


def _integrations(ctx: ProjectContext) -> Iterable[Finding]:
    for java, java_class in ctx.classes():
        text = ctx.read(java.path)
        rel = ctx.rel(java.path)
        for namespace, (label, side) in _INTEGRATIONS.items():
            if namespace not in text:
                continue
            if side == "client" and ctx.is_common_path(rel) and not _is_compat(rel):
                yield finding(
                    "P3-17",
                    f"{java_class.name} 在 common 源码里直接引用 {label} 客户端 API",
                    path=rel,
                    severity="warning",
                    suggestion="把集成移到 client 源集或 compat 包，并用 @Optional/反射隔离。",
                )
            if re.search(rf"{re.escape(namespace)}[^;\n]*\.get[A-Z]\w*\(\)", text):
                yield finding(
                    "P3-17",
                    f"{java_class.name} 在类初始化阶段直接查询 {label}，可能在加载顺序上过早",
                    path=rel,
                    severity="info",
                    suggestion="改为懒加载：首次使用时查询，并处理插件未安装的情况。",
                )


def _conflicts(ctx: ProjectContext) -> Iterable[Finding]:
    declared = _declared_mods(ctx)
    if not declared:
        return
    lowered = {item.lower() for item in declared}
    for group, problem, advice in CONFLICTS:
        present = [item for item in group if item in lowered]
        if len(present) >= 2:
            yield finding(
                "P3-18",
                f"检测到已知冲突组合：{' + '.join(present)} —— {problem}",
                path=_metadata_rel(ctx),
                severity="warning",
                suggestion=advice,
            )


# ----------------------------------------------------------------------
def _modid(ctx: ProjectContext) -> str:
    text = ctx.read_rel("gradle.properties")
    match = re.search(r"^\s*mod_id\s*=\s*([\w.\-]+)", text, re.MULTILINE)
    if match:
        return match.group(1)
    match = re.search(r'"modid"\s*:\s*"([\w.\-]+)"', ctx.read_rel("src/main/resources/fabric.mod.json"))
    return match.group(1) if match else ""


def _config_keys(ctx: ProjectContext) -> set[str]:
    keys: set[str] = set()
    for java, java_class in ctx.classes():
        if "config" not in java_class.name.lower():
            continue
        text = ctx.read(java.path)
        keys.update(re.findall(r'\.(?:define|comment|push)\s*\(\s*"([\w.\-]+)"', text))
    return keys


def _is_compat(rel: str) -> bool:
    lowered = rel.lower()
    return any(part in lowered for part in ("/compat/", "/integration/", "/client/", "/platform/"))


def _metadata_rel(ctx: ProjectContext) -> str:
    for relative in (
        "src/main/resources/META-INF/neoforge.mods.toml",
        "src/main/resources/META-INF/mods.toml",
        "src/main/resources/fabric.mod.json",
    ):
        if ctx.exists(relative):
            return relative
    return ""


def _declared_mods(ctx: ProjectContext) -> list[str]:
    relative = _metadata_rel(ctx)
    if not relative:
        return []
    text = ctx.read_rel(relative)
    if relative.endswith(".json"):
        try:
            raw = json.loads(text)
        except ValueError:
            return []
        rows = list((raw.get("depends") or {}).keys())
        rows += list((raw.get("recommends") or {}).keys())
        return [str(item) for item in rows]
    return re.findall(r'^\s*modId\s*=\s*"([^"]+)"', text, re.MULTILINE)


def _line(text: str, index: int) -> int:
    return text[: max(0, index)].count("\n") + 1
