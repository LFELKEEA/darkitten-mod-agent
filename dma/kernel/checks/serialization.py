"""Codec, NBT and versioned-component checks (P1-16..18 / P2-53)."""

from __future__ import annotations

import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

# NBT names that only describe transient render/client state.
TEMP_HINTS = (
    "anim", "cached", "cache", "temp", "tmp", "preview", "render", "client",
    "selected", "hover", "open", "highlight", "synced", "dirty", "lasttick",
)
# NBT getters that are safe without a contains() guard (defaults are useful).
SAFE_GETTERS = {"getBoolean", "getInt", "getLong", "getFloat", "getDouble", "getString"}
GETTER_RE = re.compile(r"\bget(Boolean|Byte|Short|Int|Long|Float|Double|String|Compound|List|IntArray|LongArray|ByteArray)\(\s*\"([^\"]+)\"")
CODEGET_RE = re.compile(r"\bforGetter\s*\(\s*([A-Za-z_$][\w$]*)\s*::\s*([A-Za-z_$][\w$]*)")
FIELD_NAME_RE = re.compile(r"\boptionalFieldOf\s*\(\s*\"([^\"]+)\"")
SAVE_METHODS = {"saveAdditional", "save", "writeNbt", "write", "serialize", "toNbt", "saveWithoutMetadata"}
LOAD_METHODS = {"load", "read", "readNbt", "deserialize", "fromNbt", "loadAdditional", "readAdditionalSaveData"}


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_codecs(ctx))
    rows.extend(_nbt(ctx))
    rows.extend(_components(ctx))
    return rows


def _codecs(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        codec_class = any(
            token in text
            for token in ("RecordCodecBuilder", "MapCodec", "Codec<", "Codec.", "MapCodec<")
        )
        if not codec_class:
            continue
        fields = [item.name for item in java_class.fields if not item.is_static()]
        getters = [match.group(2) for match in CODEGET_RE.finditer(text)]
        names = FIELD_NAME_RE.findall(text)
        if getters:
            duplicates = {name for name in getters if getters.count(name) > 1}
            for name in sorted(duplicates):
                rows.append(finding(
                    "P1-16", f"{java_class.name} 的 Codec 重复绑定字段 {name}",
                    path=rel, line=java_class.line,
                    suggestion="每个字段只绑定一次；重复 forGetter 会导致序列化丢字段。",
                ))
            missing = [name for name in fields if name not in getters and not name.startswith("CODEC")]
            if missing and len(missing) <= 3:
                rows.append(finding(
                    "P1-16", f"{java_class.name} 的 Codec 未覆盖字段：{', '.join(missing)}",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="确认这些字段是否需要持久化；需要时补 fieldOf/optionalFieldOf。",
                ))
        if len(names) != len(set(names)):
            rows.append(finding(
                "P1-16", f"{java_class.name} 的 Codec 使用了重复的 NBT 键名",
                path=rel, line=java_class.line,
                suggestion="键名重复会互相覆盖，改为唯一键名。",
            ))
        if "optionalFieldOf" not in text and "defaultFieldOf" not in text and fields:
            rows.append(finding(
                "P1-18", f"{java_class.name} 的 Codec 没有可选字段，旧存档可能读不出来",
                path=rel, line=java_class.line, severity="info",
                suggestion="为新字段提供 optionalFieldOf/defaultFieldOf，保证向前兼容。",
            ))
        if "Codec" in text and ("HashMap" in text or "Map<" in text):
            if "Codec.unboundedMap" not in text and "unboundedMap" not in text:
                rows.append(finding(
                    "P1-16", f"{java_class.name} 使用 Map 但 Codec 不是 unboundedMap，动态键会丢失",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="动态 key 使用 Codec.unboundedMap，或改为 list<entry>。",
                ))
        if re.search(r"return\s+Codec\.unit\s*\(", text):
            rows.append(finding(
                "P1-16", f"{java_class.name} 使用 Codec.unit，数据不会被保存",
                path=rel, line=java_class.line, severity="warning",
                suggestion="Codec.unit 只适合无状态类型；含字段的类型必须提供真实编解码。",
            ))
    return rows


def _nbt(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        uses_nbt = any(token in text for token in ("CompoundTag", "NbtIo", "NbtUtils", "getPersistentData"))
        if not uses_nbt:
            continue
        for method in java_class.methods:
            body = method.body
            if not body:
                continue
            if method.name in SAVE_METHODS:
                for match in re.finditer(r"\bput(?:Boolean|Byte|Short|Int|Long|Float|Double|String)\(\s*\"([^\"]+)\"", body):
                    key = match.group(1)
                    if any(hint in key.lower() for hint in TEMP_HINTS):
                        rows.append(finding(
                            "P2-53", f"{java_class.name}.{method.name} 把临时状态 {key} 写进持久 NBT",
                            path=rel, line=method.line, severity="warning",
                            suggestion="临时/渲染状态不入 NBT；需要时在客户端重建。",
                        ))
                if "setChanged()" in body and "put" not in body:
                    rows.append(finding(
                        "P1-17", f"{java_class.name}.{method.name} 触发保存但没有写入 NBT",
                        path=rel, line=method.line, severity="info",
                        suggestion="确认 save 路径确实序列化了所有持久字段。",
                    ))
            if method.name in LOAD_METHODS:
                for match in GETTER_RE.finditer(body):
                    getter, key = match.group(1), match.group(2)
                    if getter not in SAFE_GETTERS and "contains(" not in body:
                        rows.append(finding(
                            "P1-17", f"{java_class.name}.{method.name} 直接读取 {key}，未校验 contains",
                            path=rel, line=method.line, severity="warning",
                            suggestion="读取复合/列表前先 contains，或用 getCompound 的默认值重载。",
                        ))
                if "CompoundTag" in body and not any(token in body for token in ("version", "DataVersion", "dataVersion")):
                    rows.append(finding(
                        "P1-18", f"{java_class.name}.{method.name} 读取存档但没有版本标记",
                        path=rel, line=method.line, severity="info",
                        suggestion="写入 version 字段并在读取时迁移旧结构。",
                    ))
        if "getPersistentData()" in text:
            for match in re.finditer(r"getPersistentData\(\)\.put\w*\(\s*\"([^\"]+)\"", text):
                rows.append(finding(
                    "P1-17", f"{java_class.name} 直接修改 getPersistentData() 的键 {match.group(1)}",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="持久数据也要走统一 schema，并避免与玩家/NBT 合并语义冲突。",
                ))
        if "CompoundTag" in text and "copy()" in text and "merge(" in text:
            rows.append(finding(
                "P1-17", f"{java_class.name} 合并 CompoundTag，注意同名键覆盖顺序",
                path=rel, line=java_class.line, severity="info",
                suggestion="明确 merge 的方向与键冲突策略，并补单元测试。",
            ))
    return rows


def _components(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    mc = ctx.mc
    modern = bool(re.match(r"1\.(2[0-9]|[3-9][0-9])", mc or ""))
    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        text = java.text
        if "DataComponentType" in text or "DataComponents" in text:
            if "Codec" not in text and "codec(" not in text:
                rows.append(finding(
                    "P1-18", f"{java_class.name} 注册数据组件但没有提供 Codec，跨版本/同步会失败",
                    path=rel, line=java_class.line,
                    suggestion="每个 DataComponentType 都要有 Codec，并考虑持久化与网络同步。",
                ))
            if "persistent(" not in text and "build()" in text and "DataComponentType" in text:
                rows.append(finding(
                    "P1-18", f"{java_class.name} 的组件没有显式 persistent/cacheEncoding",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="明确组件是否持久化，避免每个版本默认值变化。",
                ))
        if modern and ("ItemStack" in text or java_class.extends.endswith("Item")):
            if "getOrCreateTag" in text or "getTag()" in text:
                rows.append(finding(
                    "P1-18", f"{java_class.name} 在 {mc} 使用旧 NBT 物品 API",
                    path=rel, line=java_class.line, severity="warning",
                    suggestion="1.20.5+ 使用 DataComponents / custom data，避免 NBT 路径失效。",
                ))
    if mc and re.match(r"1\.(2[1-9]|[3-9][0-9])", mc):
        for java in ctx.java_files:
            if "new ResourceLocation(" in java.text:
                rows.append(finding(
                    "P1-18", "1.21+ 使用已弃用的 new ResourceLocation(...)",
                    path=ctx.rel(java.path), severity="warning",
                    suggestion="改用 ResourceLocation.fromNamespaceAndPath / parse。",
                ))
    return rows
