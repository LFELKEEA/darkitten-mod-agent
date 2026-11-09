"""Logging, error handling, deprecation and version checks (P3-20..29)."""

from __future__ import annotations

import re
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

SECRET_HINTS = ("token", "secret", "password", "passwd", "apikey", "api_key", "accesskey", "event_secret")
LOG_CALL_RE = re.compile(r"\b(LOG|LOGGER|logger|LOGGER_?[A-Z]*)\s*\.\s*(trace|debug|info|warn|error)\s*\(([^;]*)\)")
SYS_OUT_RE = re.compile(r"\bSystem\s*\.\s*(out|err)\s*\.\s*(println|printf|print)\s*\(")
CATCH_RE = re.compile(r"\bcatch\s*\(([^)]*)\)\s*\{")
DEPRECATED_CALLS = {
    "getOrCreateTag": "1.20.5+ 使用 DataComponents / custom data",
    "Component.Serializer.toJson": "1.21+ 使用 Codec/JSON 组件序列化",
    "Registry.BLOCK": "1.21+ 使用 BuiltInRegistries / RegistryAccess",
    "Registry.ITEM": "1.21+ 使用 BuiltInRegistries / RegistryAccess",
    "Registry.ENTITY_TYPE": "1.21+ 使用 BuiltInRegistries / RegistryAccess",
    "ItemStack.getTag": "1.20.5+ 使用 DataComponents",
}


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    rows.extend(_logging(ctx))
    rows.extend(_exceptions(ctx))
    rows.extend(_deprecated(ctx))
    rows.extend(_versions(ctx))
    rows.extend(_diagnostics(ctx))
    return rows


def _modid(ctx: ProjectContext) -> str:
    for name in ("gradle.properties", "build.gradle", "build.gradle.kts"):
        if not ctx.exists(name):
            continue
        text = ctx.read_rel(name)
        match = re.search(r"(?:mod_id|archivesBaseName|rootProject\.name)\s*[=:]\s*['\"]?([\w-]+)", text)
        if match:
            return match.group(1)
    return ""


def _logging(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    modid = _modid(ctx)
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        text = java.text
        for match in SYS_OUT_RE.finditer(text):
            rows.append(finding(
                "P3-20", "使用 System.out/err 输出，而不是 Mod logger",
                path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                suggestion="使用 LoggerFactory.getLogger(modid) 并带结构化上下文。",
            ))
        for match in LOG_CALL_RE.finditer(text):
            args = match.group(3)
            lowered = args.lower()
            if any(hint in lowered for hint in SECRET_HINTS):
                rows.append(finding(
                    "P3-21", "日志可能输出密钥/token 等敏感信息",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                    suggestion="脱敏后再记录，只打印长度或哈希前缀。",
                ))
            if "{}" in args and args.count("{}") != args.count(","):
                rows.append(finding(
                    "P3-20", "日志占位符数量与参数不一致，堆栈会错位",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="info",
                    suggestion="占位符与参数一一对应；异常作为最后一个参数传入。",
                ))
        for match in re.finditer(r"LoggerFactory\s*\.\s*getLogger\s*\(\s*\"([^\"]*)\"\s*\)", text):
            name = match.group(1)
            if modid and name and name != modid and not name.startswith(modid + "."):
                rows.append(finding(
                    "P3-20", f"logger 名称 {name} 与 modid {modid} 不一致",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="info",
                    suggestion="统一用 modid 作为 logger 名称，方便日志过滤。",
                ))
        if "FileHandler(" in text and not any(token in text for token in ("setLimit", "limit", "maxFileSize", "maxFiles")):
            rows.append(finding(
                "P3-21", "文件日志没有大小上限/轮转，可能写满磁盘",
                path=rel, line=1, severity="warning",
                suggestion="设置单文件上限与滚动文件数，或交给启动器/加载器日志系统。",
            ))
        if "printStackTrace" in text:
            rows.append(finding(
                "P3-23", "使用 printStackTrace 而不是 logger，且可能吞掉上下文",
                path=rel, line=1, severity="info",
                suggestion="用 logger.error(msg, throwable) 并附加方块/实体/玩家上下文。",
            ))
    return rows


def _exceptions(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        for java_class in java.all_classes():
            for method in java_class.methods:
                body = method.body
                if not body:
                    continue
                for match in CATCH_RE.finditer(body):
                    inner = _block_after(body, match.end() - 1)
                    code = _strip_comments(inner).strip()
                    if not code:
                        rows.append(finding(
                            "P3-23", f"{java_class.name}.{method.name} 的空 catch 吞掉异常",
                            path=rel, line=method.line, severity="warning",
                            suggestion="至少 debug 记录，或明确注释为什么可以忽略。",
                        ))
                    elif code in {"return;", "return null;", "return false;", "return 0;"}:
                        rows.append(finding(
                            "P3-23", f"{java_class.name}.{method.name} 捕获后直接返回默认值，隐藏了失败",
                            path=rel, line=method.line, severity="warning",
                            suggestion="记录异常并让调用方知道失败，不要静默降级。",
                        ))
    return rows


def _deprecated(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    mc = ctx.mc or ""
    modern = bool(re.match(r"1\.(2[0-9]|[3-9][0-9])", mc))
    for java in ctx.java_files:
        text = java.text
        rel = ctx.rel(java.path)
        for token, advice in DEPRECATED_CALLS.items():
            if token in text and modern:
                rows.append(finding(
                    "P3-27", f"使用已弃用 API：{token}",
                    path=rel, line=1, severity="info",
                    suggestion=advice,
                ))
        for match in re.finditer(r"new\s+ResourceLocation\s*\(\s*\"[^\"]*\"\s*,\s*\"([^\"]+)\"", text):
            value = match.group(1)
            if value != value.lower() or " " in value:
                rows.append(finding(
                    "P3-31", f"资源 ID {value} 含大写或空格，旧版本迁移后会失配",
                    path=rel, line=text[:match.start()].count("\n") + 1, severity="warning",
                    suggestion="资源路径统一小写 snake_case，并为旧 ID 提供重映射。",
                ))
    return rows


def _versions(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    if ctx.exists("gradle.properties"):
        text = ctx.read_rel("gradle.properties")
        match = re.search(r"^\s*mod_version\s*[=:]\s*(\S+)", text, re.MULTILINE)
        if match:
            value = match.group(1).strip()
            if not re.match(r"^\d+\.\d+\.\d+(?:[-+][\w.-]+)?$", value):
                rows.append(finding(
                    "P3-29", f"mod_version={value} 不是语义化版本",
                    path="gradle.properties", line=1, severity="warning",
                    suggestion="使用 MAJOR.MINOR.PATCH（可带 -beta.1）便于比较与依赖范围。",
                ))
    build = ctx.read_rel("build.gradle") if ctx.exists("build.gradle") else ""
    if re.search(r"version\s*=\s*['\"]\$\{", build) or re.search(r"version\s*=\s*['\"][^'\"]*[A-Za-z]{3,}[^'\"]*['\"]", build):
        match = re.search(r"version\s*=\s*['\"]([^'\"]+)['\"]", build)
        if match and not re.search(r"\$\{", match.group(1)):
            rows.append(finding(
                "P3-29", f"构建版本号不是语义化版本：{match.group(1)}",
                path="build.gradle", line=1, severity="info",
                suggestion="版本号使用 MAJOR.MINOR.PATCH，并在 CI 中校验。",
            ))
    for java in ctx.java_files:
        text = java.text
        for match in re.finditer(r"\.define\s*\(\s*\"([^\"]+)\"", text):
            key = match.group(1)
            if " " in key or key != key.lower():
                rows.append(finding(
                    "P3-31", f"配置键 {key} 不是小写点分格式，改名会丢失旧配置",
                    path=ctx.rel(java.path), line=text[:match.start()].count("\n") + 1,
                    severity="info",
                    suggestion="配置键统一小写点分，并提供旧键到新键的迁移读取。",
                ))
    config_keys = _config_keys(ctx)
    if config_keys:
        docs = " ".join(
            ctx.read_rel(name) for name in ("README.md", "CHANGELOG.md") if ctx.exists(name)
        )
        if docs and not any(key in docs for key in config_keys):
            rows.append(finding(
                "P3-19", "配置项没有在文档中说明，改动影响玩家无感知",
                path="README.md" if ctx.exists("README.md") else "CHANGELOG.md",
                line=1, severity="info",
                suggestion="在 README/CHANGELOG 中列出配置项、默认值与影响范围。",
            ))
    return rows


def _config_keys(ctx: ProjectContext) -> list[str]:
    keys: list[str] = []
    for java in ctx.java_files:
        for match in re.finditer(r"\.define(?:InRange|Enum|List|Int|Long|Double|Boolean)?\s*\(\s*\"([^\"]+)\"", java.text):
            keys.append(match.group(1))
    return keys


def _diagnostics(ctx: ProjectContext) -> Iterable[Finding]:
    """P3-22/24/25/26: debug surfaces must be gated, dumpable and measurable."""
    rows: list[Finding] = []
    command_text = ""
    command_path = ""
    crash_used = ""
    debug_classes: list[tuple[str, str, str]] = []
    probe_classes: list[tuple[str, str, str]] = []
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        text = java.text
        if "Commands.literal(" in text or "CommandRegistrationCallback" in text:
            command_text += "\n" + text
            command_path = command_path or rel
        if "CrashReport" in text:
            crash_used = crash_used or rel
        for java_class in java.all_classes():
            lowered = java_class.name.lower()
            if any(token in lowered for token in ("debug", "overlay", "profiler")):
                debug_classes.append((java_class.name, rel, text))
            if any(token in lowered for token in ("perf", "probe", "profiler", "timer")):
                probe_classes.append((java_class.name, rel, text))

    # P3-22: a command surface without a dump subcommand is hard to support.
    if command_text and "dump" not in command_text.lower():
        rows.append(finding(
            "P3-22", "存在命令注册，但没有 debug dump 子命令",
            path=command_path, line=1, severity="info",
            suggestion="用 debug_scaffold 生成 DebugDumpCommand，默认关闭并写入 logs/。",
        ))

    # P3-24: crash reports without mod context are hard to triage.
    if crash_used and not any(
        token in ctx.read_rel(crash_used) for token in ("CrashContext", "CrashReportCategory")
    ):
        rows.append(finding(
            "P3-24", "使用 CrashReport，但没有附加 Mod 版本/功能/配置章节",
            path=crash_used, line=1, severity="info",
            suggestion="用 CrashContext 提供无副作用的 supplier，并挂到加载器崩溃回调。",
        ))

    # P3-25: debug UI must be behind a gate that production builds cannot open.
    gate_tokens = (
        "DebugFlags.enabled", "isProduction()", "isDevelopment()",
        "FMLLoader", "FabricLoader", "System.getProperty", "Boolean.getBoolean",
        "@Environment(Dist.CLIENT)", "Dist.CLIENT",
    )
    reported: set[str] = set()
    for name, rel, text in debug_classes:
        if rel in reported:
            continue
        if not any(token in text for token in ("render", "Screen", "Overlay", "KeyMapping")):
            continue
        if any(token in text for token in gate_tokens):
            continue
        reported.add(rel)
        rows.append(finding(
            "P3-25", f"调试界面 {name} 没有生产环境开关",
            path=rel, line=1, severity="warning",
            suggestion="所有调试控件走 DebugFlags.enabled()，正式构建保持关闭。",
        ))

    # P3-26: perf probes need per-layer timing, not only a boolean "laggy".
    for name, rel, text in probe_classes:
        if any(token in text for token in ("System.nanoTime", "LongAdder", "AtomicLong", "jdk.jfr")):
            continue
        if not any(token in text for token in ("tick", "render", "sync", "Path", "pathfind")):
            continue
        rows.append(finding(
            "P3-26", f"性能类 {name} 没有分层计数器",
            path=rel, line=1, severity="info",
            suggestion="用 PerfProbe 分别统计 tick/同步/渲染/寻路，并可选开启 JFR。",
        ))
    return rows


# ----------------------------------------------------------------------
def _block_after(text: str, brace_index: int) -> str:
    depth = 0
    for index in range(brace_index, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[brace_index + 1:index]
    return text[brace_index + 1:]


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text
