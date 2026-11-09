"""Structured crash log parsing and clustering (P1-03 / TOOL-17)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

FRAME_RE = re.compile(r"^\s*at\s+([\w.$]+)\.([\w$<>]+)\(([^:()]+)(?::(\d+))?\)")
CAUSE_RE = re.compile(r"^\s*Caused by:\s+([\w.$]+)(?::\s*(.*))?$")
EXCEPTION_RE = re.compile(r"^(?:java\.|net\.|com\.|org\.|io\.|jdk\.)?[\w.$]+(?:Exception|Error|Throwable)(?::\s*(.*))?$")

PATTERNS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("mixin", ("Mixin apply failed", "InvalidInjectionException", "MixinTransformerError",
               "InjectionError", "Critical injection failure"), "P0-07",
     "检查 @Inject 目标方法签名、@At 与 refmap；用 ASM 级校验器复跑。"),
    ("classpath", ("NoClassDefFoundError", "ClassNotFoundException", "LinkageError"), "P1-20",
     "确认依赖已声明并打包；检查客户端类是否进入 common 路径。"),
    ("registry", ("Unknown registry key", "Missing registry", "There is no registry",
                  "Registry is already frozen", "already registered"), "P1-15",
     "检查注册 ID 重复、注册时机和冻结顺序。"),
    ("dependency", ("Missing or unsupported mandatory dependencies", "Mod Resolution",
                    "Incompatible mod set", "requires any version"), "P1-21",
     "核对 mods.toml 版本范围与玩家实际安装版本。"),
    ("memory", ("OutOfMemoryError", "Java heap space", "GC overhead"), "P1-04",
     "限制缓存/批量大小，检查区块引用泄漏；用压力测试复现。"),
    ("thread", ("from multiple threads", "Accessing LegacyRandomSource",
                "async", "off-thread", "wrong thread"), "P0-09",
     "世界写入必须回到主线程；异步只做纯计算或快照。"),
    ("network", ("Payload may not be larger", "Packet too large", "Bad packet id",
                 "Network Protocol Error"), "P2-33",
     "检查包大小、压缩阈值、分片与版本协商。"),
    ("data", ("JsonParseException", "Couldn't parse element", "Failed to parse",
              "ParseException", "com.google.gson"), "P1-11",
     "定位数据包/资源 JSON，按 schema 修复；先跑 datapack_lint。"),
    ("client", ("Attempted to load class", "client side only",
                "net/minecraft/client", "OnlyIn"), "P0-08",
     "客户端专属类只能在客户端分发/加载；common 代码不要直接引用。"),
    ("assets", ("Unable to load model", "Missing model", "FileNotFoundException",
                "Unable to load value"), "P1-12",
     "检查模型 parent、贴图路径与 blockstate 引用。"),
    ("config", ("Config", "Toml", "ConfigSpec"), "P1-25",
     "检查配置类型/范围/默认值，并确认旧配置可迁移。"),
    ("recursion", ("StackOverflowError",), "P1-04",
     "检查递归/事件递归；加深度上限或迭代改写。"),
)


@dataclass
class Frame:
    class_name: str
    method: str
    file: str = ""
    line: int = 0

    def to_dict(self) -> dict:
        return {"class": self.class_name, "method": self.method, "file": self.file, "line": self.line}

    def __str__(self) -> str:
        location = f"{self.file}:{self.line}" if self.file else ""
        return f"{self.class_name}.{self.method}({location})"


@dataclass
class CrashCause:
    exception: str
    message: str = ""
    frames: list[Frame] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "exception": self.exception,
            "message": self.message,
            "frames": [frame.to_dict() for frame in self.frames],
        }


@dataclass
class CrashReport:
    source: str = ""
    description: str = ""
    exception: str = ""
    message: str = ""
    frames: list[Frame] = field(default_factory=list)
    causes: list[CrashCause] = field(default_factory=list)
    mods: list[str] = field(default_factory=list)
    categories: list[dict] = field(default_factory=list)

    @property
    def signature(self) -> str:
        first = self.frames[0] if self.frames else None
        location = f"{first.class_name}.{first.method}" if first else ""
        raw = f"{self.exception}|{self.message[:80]}|{location}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "signature": self.signature,
            "description": self.description,
            "exception": self.exception,
            "message": self.message,
            "frames": [frame.to_dict() for frame in self.frames],
            "causes": [cause.to_dict() for cause in self.causes],
            "mods": list(self.mods),
            "categories": list(self.categories),
        }

    def markdown(self) -> str:
        lines = [
            f"## 崩溃分析：{Path(self.source).name or '内联日志'}",
            "",
            f"- 签名：`{self.signature}`",
            f"- 异常：`{self.exception}`" + (f"：{self.message}" if self.message else ""),
            f"- 崩溃描述：{self.description or '（未提供）'}",
        ]
        if self.mods:
            lines.append(f"- 涉及 Mod：{', '.join(self.mods)}")
        if self.categories:
            lines.extend(["", "### 分类", ""])
            for item in self.categories:
                lines.append(f"- **{item['category']}**（{item['rule_id']}）：{item['suggestion']}")
        if self.frames:
            lines.extend(["", "### 关键栈帧", "", "```"])
            for frame in self.frames[:12]:
                lines.append(str(frame))
            lines.extend(["```"])
        if self.causes:
            lines.extend(["", "### Caused by", ""])
            for cause in self.causes:
                lines.append(f"- `{cause.exception}`" + (f"：{cause.message}" if cause.message else ""))
        return "\n".join(lines) + "\n"


def parse(text: str, source: str = "") -> CrashReport:
    report = CrashReport(source=source)
    lines = (text or "").splitlines()
    current_cause: CrashCause | None = None
    in_stack = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Description:"):
            report.description = stripped.split(":", 1)[1].strip()
            continue
        cause = CAUSE_RE.match(line)
        if cause:
            current_cause = CrashCause(exception=cause.group(1), message=(cause.group(2) or "").strip())
            report.causes.append(current_cause)
            in_stack = True
            continue
        if not report.exception and EXCEPTION_RE.match(stripped) and not stripped.startswith("at "):
            report.exception = stripped.split(":", 1)[0].strip()
            if ":" in stripped:
                report.message = stripped.split(":", 1)[1].strip()
            in_stack = True
            continue
        frame = FRAME_RE.match(line)
        if frame:
            parsed = Frame(
                class_name=frame.group(1),
                method=frame.group(2),
                file=frame.group(3),
                line=int(frame.group(4) or 0),
            )
            if current_cause is not None:
                current_cause.frames.append(parsed)
            if in_stack:
                report.frames.append(parsed)
            continue
        if in_stack and not stripped:
            current_cause = None
    report.mods = _mods(report)
    report.categories = classify(report)
    if not report.exception:
        report.exception = "UnknownError"
    return report


def parse_file(path: Path) -> CrashReport:
    target = Path(path)
    return parse(target.read_text(encoding="utf-8", errors="replace"), str(target))


def parse_dir(path: Path, limit: int = 20) -> list[CrashReport]:
    root = Path(path)
    rows: list[CrashReport] = []
    candidates: list[Path] = []
    for folder in ("crash-reports", "run/crash-reports", "logs"):
        base = root / folder
        if base.is_dir():
            candidates.extend(sorted(base.glob("*.txt"), key=lambda item: item.stat().st_mtime, reverse=True))
    for item in candidates[:limit]:
        try:
            rows.append(parse_file(item))
        except OSError:
            continue
    return rows


def classify(report: CrashReport) -> list[dict]:
    blob = " ".join([
        report.description, report.exception, report.message,
        *[cause.exception + " " + cause.message for cause in report.causes],
        *[str(frame) for frame in report.frames[:20]],
    ])
    rows: list[dict] = []
    for category, tokens, rule_id, suggestion in PATTERNS:
        if any(token.lower() in blob.lower() for token in tokens):
            rows.append({
                "category": category,
                "rule_id": rule_id,
                "suggestion": suggestion,
                "evidence": next((token for token in tokens if token.lower() in blob.lower()), ""),
            })
    return rows


def cluster(reports: Iterable[CrashReport]) -> list[dict]:
    buckets: dict[str, dict] = {}
    for report in reports:
        key = report.signature
        bucket = buckets.setdefault(key, {
            "signature": key,
            "exception": report.exception,
            "message": report.message,
            "count": 0,
            "sources": [],
            "categories": report.categories,
        })
        bucket["count"] += 1
        if report.source and report.source not in bucket["sources"]:
            bucket["sources"].append(report.source)
    return sorted(buckets.values(), key=lambda item: item["count"], reverse=True)


def _mods(report: CrashReport) -> list[str]:
    rows: list[str] = []
    for frame in report.frames:
        name = frame.class_name
        if name.startswith(("java.", "javax.", "jdk.", "sun.", "net.minecraft.", "com.mojang.",
                            "net.neoforged.", "net.minecraftforge.", "net.fabricmc.", "org.slf4j.")):
            continue
        top = name.split(".")[0]
        if top in {"com", "org", "net", "io"}:
            top = ".".join(name.split(".")[:2])
        if top and top not in rows and top not in {"com", "org", "net", "io"}:
            rows.append(top)
    return rows[:12]


def summarize(reports: Iterable[CrashReport]) -> str:
    rows = list(reports)
    lines = ["# 崩溃聚类报告", ""]
    if not rows:
        return "\n".join(lines + ["没有可分析的崩溃日志。"]) + "\n"
    for item in cluster(rows):
        lines.append(
            f"- `{item['signature']}` ×{item['count']}：{item['exception']} "
            f"{item['message'][:100]}"
        )
        for category in item["categories"]:
            lines.append(f"  - {category['category']}（{category['rule_id']}）")
    return "\n".join(lines) + "\n"
