"""Registry audit: freeze, timing, duplicate ids/uuids (P1-13/14/15)."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Iterable

from ..findings import Finding
from ..java import mask_comments
from .common import ProjectContext, finding

REGISTER_RE = re.compile(
    r"(?:([A-Za-z_$][\w$.]*)\s*)?\.register\s*\(\s*(?:\(\s*\)\s*->\s*)?[\"']([^\"']+)[\"']"
)
CREATE_RE = re.compile(r"DeferredRegister\.create\s*\(\s*([\w.]+)\s*,\s*([^)]+)\)")
RESOURCE_RE = re.compile(
    r"(?:new\s+(?:ResourceLocation|Identifier)\s*\(|ResourceLocation\.(?:fromNamespaceAndPath|tryBuild|parse)\s*\(|Identifier\.of\s*\()\s*[\"']([^\"']+)[\"']\s*(?:,\s*[\"']([^\"']+)[\"'])?"
)
UUID_RE = re.compile(
    r"UUID\.fromString\s*\(\s*[\"']([0-9a-fA-F-]{36})[\"']\s*\)|new\s+UUID\s*\(\s*(-?\d+L?)\s*,\s*(-?\d+L?)\s*\)"
)


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    register_names: dict[str, list[tuple[str, int]]] = defaultdict(list)
    deferred_fields: dict[str, str] = {}
    resource_ids: dict[str, list[tuple[str, int]]] = defaultdict(list)
    uuids: dict[str, list[tuple[str, int]]] = defaultdict(list)
    register_calls = 0
    freeze_seen: list[tuple[str, int]] = []
    registered_sinks: set[str] = set()
    seen_files: set[str] = set()

    for java, java_class in ctx.classes():
        rel = ctx.rel(java.path)
        # 一个文件里有嵌套类时 ctx.classes() 会多次产出同一文件；
        # 字面量只按文件扫一遍，否则同一处 register 会被记成重复注册。
        file_level = rel not in seen_files
        seen_files.add(rel)
        # fields that hold DeferredRegister
        for field in java_class.fields:
            if "DeferredRegister" in field.type:
                deferred_fields[field.name] = rel
        if file_level:
            for line_no, line in enumerate(java.text.splitlines(), 1):
                if "freeze()" in line and "Registry" in line:
                    freeze_seen.append((rel, line_no))
            for match in CREATE_RE.finditer(java.masked):
                register_calls += 1
            # .register(Registry, modBus) sink registration
            for match in re.finditer(r"(\w+)\.register\s*\(\s*(?:modBus|ModLoadingContext|eventBus|bus)", java.masked):
                registered_sinks.add(match.group(1))
        # 字符串字面量必须从原文里取；java.masked 会把引号内容清成空格，
        # 结果就是一堆 `ID 重复` 但 ID 显示为空白，模型根本无法修。
        if file_level:
            literal = mask_comments(java.text)
            # registration ids grouped by variable/registry type
            for match in REGISTER_RE.finditer(literal):
                receiver = match.group(1) or ""
                name = match.group(2)
                line = literal.count("\n", 0, match.start()) + 1
                # 同名 ID 在不同注册表里完全合法（方块实体与菜单都叫 foo 很常见），
                # 只有同一个 DeferredRegister 重复注册同一个 ID 才是复制模板的错。
                register_names[f"{receiver}:{name}"].append((rel, line))
            for match in RESOURCE_RE.finditer(literal):
                namespace = match.group(1)
                path = match.group(2) or ""
                line = literal.count("\n", 0, match.start()) + 1
                if path:
                    key = f"{namespace}:{path}"
                else:
                    key = namespace
                resource_ids[key].append((rel, line))
            for match in UUID_RE.finditer(literal):
                value = match.group(1) or f"{match.group(2)}:{match.group(3)}"
                line = literal.count("\n", 0, match.start()) + 1
                uuids[value].append((rel, line))

        # early DeferredRegister#get in a static field initializer
        for field in java_class.fields:
            if "static" in field.modifiers and ".get()" in field.initializer:
                if any(name in field.initializer for name in deferred_fields):
                    rows.append(finding(
                        "P1-13", f"静态字段 {field.name} 在注册完成前调用 DeferredRegister.get()",
                        path=rel, line=field.line,
                        suggestion="延迟到 FMLCommonSetupEvent / 首次使用时取值；注册表冻结后 get() 才能安全解引用。",
                    ))
        # .get() on a deferred holder inside the same static initializer block
        for block_match in re.finditer(r"static\s*\{([^}]*)\}", java.masked):
            body = block_match.group(1)
            if ".get()" in body and any(name in body for name in deferred_fields):
                line = java.masked.count("\n", 0, block_match.start()) + 1
                rows.append(finding(
                    "P1-13", "静态初始化块中解引用 DeferredRegister 持有者",
                    path=rel, line=line,
                    suggestion="注册项在构造阶段只保存 Supplier，不要在冻结前调用 get()。",
                ))

    # duplicate simple registration names
    for key, places in register_names.items():
        if len(places) > 1:
            receiver, _, name = key.partition(":")
            files = sorted({path for path, _ in places})
            rows.append(finding(
                "P1-15",
                f"注册 ID `{name}` 在 {receiver or '同一注册表'} 中重复出现 {len(places)} 次"
                f"（{', '.join(files[:4])}）",
                path=places[0][0], line=places[0][1],
                suggestion="复制模板后忘记改 ID；为每种注册类型维护全局 ID 表。",
            ))

    # 重复的完整资源 ID：这里只审计注册调用，普通的 ResourceLocation 引用
    # （如 minecraft:empty、同一个贴图被多个模型引用）重复是正常用法，不报。
    for key, places in resource_ids.items():
        if len(places) < 2:
            continue
        namespace, _, path = key.partition(":")
        if namespace == "minecraft":
            rows.append(finding(
                "P3-14", f"注册/引用占用了 minecraft 命名空间：{key}",
                path=places[0][0], line=places[0][1],
                suggestion="改用本 Mod 的命名空间，避免与其他 Mod/原版冲突。",
            ))

    for value, places in uuids.items():
        if len(places) > 1:
            rows.append(finding(
                "P1-15", f"UUID `{value}` 硬编码重复 {len(places)} 次",
                path=places[0][0], line=places[0][1],
                suggestion="用 UUID.nameUUIDFromBytes(modid + 语义 key) 生成稳定且唯一的修饰器 UUID。",
            ))

    if freeze_seen and register_calls:
        rows.append(finding(
            "P1-13", "项目里出现 Registry.freeze()，请确认没有冻结后的注册调用",
            path=freeze_seen[0][0], line=freeze_seen[0][1], severity="info",
            suggestion="注册只发生在 mod 构造阶段；freeze 之后只能查询。",
        ))

    unregistered = [
        name for name in deferred_fields
        if name not in registered_sinks
    ]
    if unregistered:
        rows.append(finding(
            "P1-13", f"DeferredRegister 字段可能没有注册到事件总线：{', '.join(unregistered[:5])}",
            severity="warning",
            suggestion="在构造函数/初始化事件中调用 deferred.register(modBus)。",
        ))

    # Dynamic registry usage
    dynamic_markers = ("DataPackRegistryEvent", "RegistryDataLoader", "DynamicRegistryManager", "DatapackRegistry")
    uses_dynamic = any(marker in java.text for java in ctx.java_files for marker in dynamic_markers)
    if uses_dynamic:
        if not any("reload" in java.text.lower() and "DataPack" in java.text for java in ctx.java_files):
            rows.append(finding(
                "P1-14", "使用动态注册但没有看到数据包重载时的清理/幂等逻辑",
                severity="warning",
                suggestion="动态注册必须支持重载：先清理旧 ID，再重新注册；ID 生命周期与静态注册分开管理。",
            ))
    return rows
