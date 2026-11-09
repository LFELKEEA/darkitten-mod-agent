"""Security and localization checks (P3-37..44)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

TRANSLATABLE_RE = re.compile(
    r"(?:Component\s*\.\s*translatable|new\s+TranslatableComponent|TranslatableContents)\s*\(\s*\"([^\"]+)\""
)
LITERAL_RE = re.compile(r"(?:Component\s*\.\s*literal|new\s+TextComponent)\s*\(\s*\"([^\"]{1,80})\"\s*\)")
PERFORM_RE = re.compile(r"perform(?:Prefixed)?Command\s*\(([^;]*)\)")
FILE_API_RE = re.compile(r"(?:new\s+File\s*\(|Path(?:s)?\s*\.\s*(?:get|of)\s*\(|new\s+FileInputStream\s*\(|Files\s*\.\s*(?:read|write|copy|move|delete|newInputStream|newOutputStream|lines)\s*\()")


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_command_injection(ctx))
    rows.extend(_file_io(ctx))
    rows.extend(_capability(ctx))
    rows.extend(_scripts(ctx))
    return rows


def run_i18n(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    lang_files = _lang_files(ctx)
    if not lang_files:
        if ctx.java_files:
            rows.append(finding(
                "P3-37", "没有找到 assets/*/lang/en_us.json，界面文本无法本地化",
                path="src/main/resources", severity="info",
                suggestion="新增 en_us.json 与 zh_cn.json，所有玩家可见文本走 translatable。",
            ))
        return rows
    base = None
    for path in lang_files:
        if path.name == "en_us.json":
            base = path
            break
    if base is None:
        rows.append(finding(
            "P3-37", "缺少 en_us.json（基准语言）",
            path=ctx.rel(lang_files[0]), severity="warning",
            suggestion="en_us.json 作为基准语言，其它语言按它补齐键。",
        ))
        base = lang_files[0]
    base_data = _read_lang(ctx, base)
    used = _used_keys(ctx)
    rel_base = ctx.rel(base)
    for key in sorted(used):
        if key.startswith(("item.", "block.", "entity.", "gui.", "message.", "config.")) and key not in base_data:
            rows.append(finding(
                "P3-37", f"翻译键缺失：{key}",
                path=rel_base, severity="warning",
                suggestion="在 lang 文件中补充该键，或改用已有键。",
            ))
    for key, value in base_data.items():
        if not str(value).strip():
            rows.append(finding(
                "P3-37", f"翻译值为空：{key}",
                path=rel_base, severity="warning",
                suggestion="空值会显示键名，补充文本。",
            ))
        elif str(value).strip() == key:
            rows.append(finding(
                "P3-37", f"翻译值与键名相同：{key}",
                path=rel_base, severity="info",
                suggestion="补充真实文本，避免界面直接显示键名。",
            ))
    for path in lang_files:
        if path == base:
            continue
        data = _read_lang(ctx, path)
        missing = [key for key in base_data if key not in data]
        if missing:
            rows.append(finding(
                "P3-37", f"{path.name} 缺少 {len(missing)} 个键（如 {missing[0]}）",
                path=ctx.rel(path), severity="info",
                suggestion="按 en_us.json 补齐，缺失键会回退到英文。",
            ))
        for key, value in data.items():
            if key in base_data:
                expected = len(re.findall(r"%[sd]", str(base_data[key])))
                actual = len(re.findall(r"%[sd]", str(value)))
                if expected != actual:
                    rows.append(finding(
                        "P3-38", f"{path.name} 的 {key} 占位符数量不一致（{expected} vs {actual}）",
                        path=ctx.rel(path), severity="warning",
                        suggestion="保持占位符数量与顺序，必要时使用位置参数。",
                    ))
    _literal_buttons(ctx, rows)
    return rows


# ----------------------------------------------------------------------
def _command_injection(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java in ctx.java_files:
        text = java.text
        rel = ctx.rel(java.path)
        for match in PERFORM_RE.finditer(text):
            argument = match.group(1)
            if re.search(r"\+\s*\w+|\w+\s*\+", argument) or '"' in argument and "format" in argument:
                rows.append(finding(
                    "P3-41", "把拼接/外部输入直接当命令执行，存在命令注入",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                    suggestion="不要拼接命令字符串；使用参数化命令源或白名单校验。",
                ))
            if any(token in argument for token in ("payload", "message", "input", "packet", "buffer")):
                rows.append(finding(
                    "P3-41", "网络/玩家输入进入命令执行路径，必须服务端校验并限制权限",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                    suggestion="改用强类型命令参数，并检查执行者权限等级。",
                ))
        if "Function" in text and "load(" in text and "CommandFunction" in text:
            rows.append(finding(
                "P3-41", "动态加载 mcfunction 前必须校验来源与命名空间",
                path=rel, line=1, severity="info",
                suggestion="只允许 mod 自带/数据包注册的函数，拒绝任意路径。",
            ))
    return rows


def _file_io(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        for java_class in java.all_classes():
            for method in java_class.methods:
                body = method.body
                if not body:
                    continue
                if not FILE_API_RE.search(body):
                    continue
                sanitized = any(
                    token in body
                    for token in ("normalize()", "toRealPath", "startsWith(", "resolve(", "getCanonicalPath", "validatePath")
                )
                dynamic = re.search(r"(?:new\s+File|Path(?:s)?\s*\.\s*(?:get|of)|FileInputStream)\s*\(\s*(?!\")([A-Za-z_$][\w$]*)", body)
                if dynamic and not sanitized:
                    rows.append(finding(
                        "P3-43", f"{java_class.name}.{method.name} 用变量拼路径且没有规范化/前缀校验",
                        path=rel, line=method.line, severity="warning",
                        suggestion="resolve 后 normalize，校验 startsWith 沙盒根目录，拒绝 .. 与绝对路径。",
                    ))
                if "Files.copy" in body or "Files.move" in body:
                    if "REPLACE_EXISTING" not in body and "ATOMIC_MOVE" in body:
                        rows.append(finding(
                            "P3-43", "原子移动没有处理目标已存在的情况",
                            path=rel, line=method.line, severity="info",
                            suggestion="明确覆盖策略并处理 AtomicMoveNotSupportedException。",
                        ))
    return rows


def _capability(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java, java_class in ctx.classes():
        text = java.text
        rel = ctx.rel(java.path)
        if any(token in text for token in ("registerCapability", "AttachmentRegistry", "CapabilityManager")):
            if "getCapability" in text and not any(
                token in text for token in ("isClientSide", "level.isClientSide", "side.isServer", "LogicalSide")
            ):
                rows.append(finding(
                    "P3-42", f"{java_class.name} 暴露能力但未区分客户端/服务端",
                    path=rel, line=java_class.line, severity="info",
                    suggestion="能力读写只对服务端权威开放，客户端通过同步包获取只读视图。",
                ))
            if "return null" in text and "getCapability" in text:
                rows.append(finding(
                    "P3-42", "能力查询返回 null，调用方未判空会崩溃",
                    path=rel, line=1, severity="info",
                    suggestion="返回 LazyOptional/Optional 或明确文档化 null 语义。",
                ))
    return rows


def _scripts(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    engines = ("ScriptEngineManager", "GroovyShell", "Nashorn", "JavaScriptEngine", "LuaScript")
    for java in ctx.java_files:
        text = java.text
        if not any(token in text for token in engines):
            continue
        guarded = any(
            token in text
            for token in ("setBindings", "Sandbox", "SecurityManager", "timeout", "maxStatements", "allowlist", "setClassFilter")
        )
        if not guarded:
            rows.append(finding(
                "P3-44", "内嵌脚本引擎没有沙盒配额",
                path=ctx.rel(java.path), line=1, severity="warning",
                suggestion="限制执行时间/内存/可访问类，或改为声明式数据驱动。",
            ))
    return rows


# ----------------------------------------------------------------------
def _lang_files(ctx: ProjectContext) -> list[Path]:
    rows = [path for path in ctx.glob("src/main/resources/assets/**/lang/*.json") if path.is_file()]
    return sorted(rows)


def _read_lang(ctx: ProjectContext, path: Path) -> dict[str, str]:
    try:
        data = json.loads(ctx.read(path))
    except (ValueError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def _used_keys(ctx: ProjectContext) -> set[str]:
    keys: set[str] = set()
    for java in ctx.java_files:
        for match in TRANSLATABLE_RE.finditer(java.text):
            key = match.group(1)
            if key and "%" not in key:
                keys.add(key)
    for path in ctx.json_files("src/main/resources/data/**/*.json"):
        text = ctx.read(path)
        for match in re.finditer(r"\"translate\"\s*:\s*\"([^\"]+)\"", text):
            keys.add(match.group(1))
    for path in ctx.json_files("src/main/resources/assets/**/*.json"):
        if "/lang/" in path.as_posix():
            continue
        text = ctx.read(path)
        for match in re.finditer(r"\"translate\"\s*:\s*\"([^\"]+)\"", text):
            keys.add(match.group(1))
    return keys


def _literal_buttons(ctx: ProjectContext, rows: list[Finding]) -> None:
    for java in ctx.java_files:
        text = java.text
        if "Screen" not in text and "Screen" not in java.path:
            continue
        for match in LITERAL_RE.finditer(text):
            value = match.group(1)
            if not value or value in {"", " ", "×", "x"}:
                continue
            rows.append(finding(
                "P3-39", f"界面硬编码文本：{value}",
                path=ctx.rel(java.path), line=text[:match.start()].count("\n") + 1,
                severity="info",
                suggestion="改用 translatable，并为按钮添加 narration/快捷键提示。",
            ))
