"""Mixin static validation (P0-07 / P2-59 / P2-60)."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from ..java import annotation_arg
from .common import ProjectContext, finding

VALID_AT = {
    "HEAD", "TAIL", "RETURN", "INVOKE", "INVOKE_ASSIGN", "INVOKE_MIXIN",
    "FIELD", "NEW", "CONSTANT", "JUMP", "LOAD", "STORE", "THROW",
}
INJECTORS = {
    "Inject", "Redirect", "ModifyArg", "ModifyArgs", "ModifyVariable",
    "ModifyConstant", "ModifyExpressionValue", "WrapOperation", "WrapWithCondition",
    "ModifyReturnValue", "ModifyReceiver",
}


def _mixin_configs(ctx: ProjectContext) -> tuple[dict[str, str], list[dict]]:
    members: dict[str, str] = {}
    configs: list[dict] = []
    root = ctx.root / "src" / "main" / "resources"
    if not root.is_dir():
        return members, configs
    for path in root.rglob("*mixins*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        configs.append({"path": ctx.rel(path), "data": data})
        package = str(data.get("package") or "")
        for key in ("mixins", "client", "server"):
            for entry in data.get(key) or []:
                if isinstance(entry, str):
                    members[f"{package}.{entry}".replace("../", "") if package else entry] = ctx.rel(path)
                    members[entry] = ctx.rel(path)
    return members, configs


def _targets(annotation: str) -> list[str]:
    rows: list[str] = []
    for match in re.finditer(r"([\w.$]+)\.class", annotation):
        rows.append(match.group(1))
    return rows


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    members, configs = _mixin_configs(ctx)
    overwrite_index: Counter[tuple[str, str]] = Counter()
    mixin_classes = 0

    for java, java_class in ctx.classes():
        mixin_annotation = ""
        for annotation in java_class.annotations:
            if annotation.lstrip("@").startswith("Mixin"):
                mixin_annotation = annotation
                break
        if not mixin_annotation:
            continue
        mixin_classes += 1
        rel = ctx.rel(java.path)
        targets = _targets(mixin_annotation)
        if not targets:
            rows.append(finding(
                "P0-07", "@Mixin 没有声明目标类", path=rel, line=java_class.line,
                suggestion="@Mixin(TargetClass.class)，或 value = {A.class, B.class}。",
            ))
        priority = annotation_arg(mixin_annotation, "priority")
        if priority.isdigit() and not (-10000 <= int(priority) <= 10000):
            rows.append(finding(
                "P0-07", f"Mixin priority={priority} 超出 [-10000, 10000]", path=rel, line=java_class.line,
                suggestion="默认 1000，仅在同目标冲突时调整。",
            ))

        # Config membership
        if members:
            qualified = f"{java_class.package}.{java_class.name}"
            if qualified not in members and java_class.name not in members:
                rows.append(finding(
                    "P0-07", f"Mixin 类 {java_class.name} 未登记在任何 mixins json 中",
                    path=rel, line=java_class.line,
                    suggestion="把类名加入 mixins/client/server 数组，否则不会被加载。",
                ))

        unique_names: Counter[str] = Counter()
        for field in java_class.fields:
            annotation_text = " ".join(field.annotations)
            if "@Unique" in annotation_text:
                unique_names[field.name] += 1
            if "@Shadow" in annotation_text and field.initializer:
                rows.append(finding(
                    "P0-07", f"@Shadow 字段 {field.name} 不应有初始化值",
                    path=rel, line=field.line,
                    suggestion="删除初始化，@Shadow 只声明目标字段。",
                ))
        for method in java_class.methods:
            annotation_text = " ".join(method.annotations)
            if "@Unique" in annotation_text:
                unique_names[method.name] += 1
            injector = next((name for name in INJECTORS if f"@{name}" in annotation_text), "")
            if injector:
                declaration = next(
                    (item for item in method.annotations if item.lstrip("@").startswith(injector)), ""
                )
                if not annotation_arg(declaration, "method") and injector != "ModifyVariable":
                    rows.append(finding(
                        "P0-07", f"@{injector} 没有 method 目标", path=rel, line=method.line,
                        suggestion="声明 method = \"methodName(desc)\"，避免注入到错误重载。",
                    ))
                at_values = re.findall(r"@At\s*\(([^)]*)\)", annotation_text)
                if injector in {"Inject", "ModifyArg", "ModifyArgs", "ModifyConstant", "ModifyExpressionValue", "WrapOperation"} and not at_values:
                    rows.append(finding(
                        "P0-07", f"@{injector} 缺少 @At 注入点", path=rel, line=method.line,
                        suggestion="HEAD/TAIL/RETURN 或 INVOKE + target；不要依赖隐式默认。",
                    ))
                for value in at_values:
                    at_value = annotation_arg(value, "value")
                    if not at_value:
                        match = re.search(r"@At\s*\(\s*\"([^\"]+)\"", annotation_text)
                        at_value = match.group(1) if match else ""
                    if at_value and at_value not in VALID_AT:
                        rows.append(finding(
                            "P0-07", f"未知 @At 注入点：{at_value}", path=rel, line=method.line,
                            suggestion=f"使用 {', '.join(sorted(VALID_AT))} 之一。",
                        ))
                    target = annotation_arg(value, "target")
                    if at_value in {"INVOKE", "INVOKE_ASSIGN", "FIELD", "NEW", "CONSTANT", "JUMP"} and not target:
                        rows.append(finding(
                            "P0-07", f"@At(\"{at_value}\") 缺少 target", path=rel, line=method.line,
                            suggestion="INVOKE 需要 owner + 方法名 + 描述符，例如 Lnet/minecraft/...;foo(I)V。",
                        ))
                if injector == "Inject":
                    if method.return_type not in {"void", ""}:
                        rows.append(finding(
                            "P0-07", f"@Inject 处理器必须返回 void，当前是 {method.return_type}",
                            path=rel, line=method.line,
                            suggestion="改成 void handler(..., CallbackInfo ci)。",
                        ))
                    last_type = method.params[-1][0] if method.params else ""
                    if "CallbackInfo" not in last_type:
                        rows.append(finding(
                            "P0-07", "@Inject 处理器最后一个参数必须是 CallbackInfo / CallbackInfoReturnable",
                            path=rel, line=method.line,
                            suggestion="补上 CallbackInfo ci 或 CallbackInfoReturnable<T> cir。",
                        ))
                if injector == "ModifyVariable" and not at_values:
                    if not any(key in annotation_text for key in ("index", "ordinal", "name")):
                        rows.append(finding(
                            "P0-07", "@ModifyVariable 未指定 index/ordinal/name，可能改错变量",
                            path=rel, line=method.line,
                            suggestion="显式指定 index 或 ordinal，并用 @Local 捕获。",
                        ))
            if "@Overwrite" in annotation_text:
                for target in targets or [java_class.name]:
                    overwrite_index[(target, method.name)] += 1
                rows.append(finding(
                    "P2-59", f"@Overwrite 覆盖 {method.name}，与其他 Mod 冲突概率高",
                    path=rel, line=method.line, severity="warning",
                    suggestion="优先 @Inject/@WrapOperation/@ModifyReturnValue；必须覆盖时加 @Mixin(priority=...) 和兼容说明。",
                ))
            if any(name in annotation_text for name in ("@Accessor", "@Invoker")):
                if method.body.strip():
                    rows.append(finding(
                        "P0-07", f"@Accessor/@Invoker 方法 {method.name} 必须有方法体为空",
                        path=rel, line=method.line,
                        suggestion="声明为 abstract 或 native，不要写实现。",
                    ))
            if any(name in annotation_text for name in ("@Shadow",)):
                if method.body.strip():
                    rows.append(finding(
                        "P0-07", f"@Shadow 方法 {method.name} 不应有实现",
                        path=rel, line=method.line,
                        suggestion="声明为 abstract/native，Mixin 会在运行时绑定目标方法。",
                    ))
            # lambda / varargs injection stability (P2-60)
            if injector and ("->" in method.body or "::" in method.body) and "INVOKE" in annotation_text:
                rows.append(finding(
                    "P2-60", f"处理器 {method.name} 在 lambda 内注入 INVOKE，混淆后目标可能不稳定",
                    path=rel, line=method.line, severity="info",
                    suggestion="优先注入具名方法；必须注入 lambda 时固定完整描述符并用 refmap 校验。",
                ))
            if injector and any("..." in param_type for param_type, _ in method.params):
                rows.append(finding(
                    "P2-60", "可变参数处理器在混淆后参数顺序可能变化",
                    path=rel, line=method.line, severity="info",
                    suggestion="展开为显式参数，或改用 @ModifyArgs。",
                ))
        for name, count in unique_names.items():
            if count > 1:
                rows.append(finding(
                    "P0-07", f"@Unique 成员 {name} 在同一 Mixin 中重复声明",
                    path=rel, line=java_class.line,
                    suggestion="重命名其中一个，避免合并后冲突。",
                ))

    for (target, method), count in overwrite_index.items():
        if count > 1:
            rows.append(finding(
                "P2-59", f"{target}.{method} 被多个 @Overwrite 覆盖（{count} 处）",
                severity="warning",
                suggestion="保留一个覆盖，其余改用可组合注入。",
            ))

    if mixin_classes and not members:
        rows.append(finding(
            "P0-07", f"发现 {mixin_classes} 个 @Mixin 类，但没有可用的 mixins json 登记",
            suggestion="添加并注册 mixins 配置；Forge/NeoForge 还要保证 refmap 生成。",
        ))
    return rows
