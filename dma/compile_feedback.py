"""编译失败的结构化反馈引擎。

agent 的通用编译闭环（``build_project`` / 写文件后自动编译）之前把 Gradle 的
原始日志（可能上百 KB，混着下载进度、daemon 警告、Task 列表）整段丢给模型，
模型要自己在里面找 ``error: ...`` 和 ``文件:行``，定位慢、易错，大工程几十个
类互相引用时几乎必然陷进「写错→编译失败→猜错→又写错」的死循环。

本模块复用 :mod:`dma.studio_engine.build` 的错误解析与 :mod:`dma.kernel.symbols`
的符号表，把原始日志转成一份精确到「文件:行 + 错误类型 + 修复提示 + 候选符号」
的可执行清单，让模型直接照着修，而不是读日志猜。这是「编译驱动实现引擎」的
核心一环：编译器的报错是唯一权威，我们把它的结构原样、无损地交给模型。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .studio_engine.build import classify, parse_errors

# 每个错误附带候选符号的数量上限，避免清单过长。
_MAX_CANDIDATES = 8
# 结构化清单最多展示的错误条数。
MAX_ERRORS = 30

# javac 的「找不到符号」在标准输出里把符号名放在后续的 symbol: 行。
_SYMBOL_RE = re.compile(
    r"symbol\s*:\s*(?:class|method|variable|interface|enum|constructor)\s+([\w.]+)"
)
# 紧凑格式（某些 Gradle 版本 / 单行输出）与中文本地化兜底。
_SYMBOL_INLINE_RE = re.compile(
    r"(?:cannot find symbol|找不到符号)[:：]?\s*(?:class\s+|类\s+)?([\w.]+)"
)


def _extract_symbol_name(message: str, output: str) -> str | None:
    """从错误消息/原始输出里提取「找不到符号」的符号名（仅取简单名）。"""
    for pattern in (_SYMBOL_RE, _SYMBOL_INLINE_RE):
        m = pattern.search(output)
        if m:
            return m.group(1).rsplit(".", 1)[-1]
    # 中文「找不到符号」但符号在别处时，从消息里尽力取一个类名样子的词。
    if "找不到符号" in message or "cannot find symbol" in message:
        m = re.search(r"([A-Z][A-Za-z0-9_]{2,})", message)
        if m:
            return m.group(1)
    return None


def _symbol_candidates(root: Path, symbol_name: str | None) -> list[str]:
    """用符号表对符号名做模糊匹配，给出工程里真实存在的相近类名。"""
    if not symbol_name or root is None:
        return []
    try:
        from .kernel.symbols import build_index

        index = build_index(root)
        rows = index.find_class(symbol_name)
        return [r.name for r in rows if r.name != symbol_name][:_MAX_CANDIDATES]
    except Exception:  # noqa: BLE001 - 符号索引失败不应影响错误反馈本身
        return []


def analyze_failures(output: str, root: Path | None = None, max_errors: int = MAX_ERRORS) -> str:
    """把编译失败的原始输出转成结构化修复清单（Markdown）。

    ``output`` 是 Gradle/javac 的原始输出；``root`` 是工程根目录（用于符号表
    候选匹配，可为 None 则跳过候选）。返回可直接塞给模型的文本。
    """
    text = output or ""
    errors = parse_errors(text)
    if not errors:
        # 不是 javac 的逐文件错误（多为 Gradle 配置/依赖/插件问题），
        # 回退到原始日志的尾部，但去掉纯噪音行。
        return _fallback(text)

    lines: list[str] = []
    lines.append(f"编译失败，共解析出 {len(errors)} 个错误（下面按文件:行 精确定位）：")
    shown = 0
    for e in errors:
        if shown >= max_errors:
            lines.append(f"… 其余 {len(errors) - shown} 个错误略去，修完这些再编译会继续报。")
            break
        shown += 1
        loc = f"{e.get('file')}:{e.get('line')}" if e.get("file") else "（全局 / 构建脚本）"
        message = e.get("message") or ""
        hint = e.get("hint") or classify(message, text)
        lines.append(f"{shown}. `{loc}` — {message}")
        if hint:
            lines.append(f"   → {hint}")
        # 找不到符号时，给出工程里真实存在的相近类名，直接纠正大小写/拼写错。
        symbol = _extract_symbol_name(message, text)
        candidates = _symbol_candidates(root, symbol) if root is not None else []
        if candidates:
            lines.append(
                f"   → 工程里实际存在这些类名：{', '.join(candidates)}。"
                " 若上面的符号是拼写/大小写错误，直接用其中一个。"
            )

    # 附加一段「最常见根因」汇总，帮模型一次看全局，而不是逐个文件盲改。
    summary = _summary(errors)
    if summary:
        lines.append("")
        lines.append(summary)

    return "\n".join(lines)


def success_symbols(root: Path, limit: int = 160) -> str:
    """编译成功后，列出工程当前已声明的类与注册 id，作为「契约快照」。

    这是「契约先行」的落地：模型写第 30 个类时，看到的就是前 29 个类真实产出的
    类名与注册 id，而不是凭记忆猜，从而避免「类名/包名/注册 id 前后对不上」这类
    在大工程里最常见、最难查的编译错误。

    注意：不依赖 :mod:`dma.kernel.symbols` 的索引——它会把 ``.dma/snapshots`` 里
    的历史快照也扫进来，且同名类多份文件互相覆盖，``path`` 不可靠。这里直接扫
    真实源码目录提取包名/类名，结果确定。
    """
    if root is None:
        return ""
    from collections import defaultdict

    by_pkg: dict[str, set[str]] = defaultdict(set)
    for path in _project_sources(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        pkg = ""
        m = _PACKAGE_RE.search(text)
        if m:
            pkg = m.group(1)
        for cls in _CLASS_RE.findall(text):
            by_pkg[pkg or "(默认包)"].add(cls)

    # 展平成有序文本，控制总量。
    entries: list[str] = []
    for pkg in sorted(by_pkg):
        names = sorted(by_pkg[pkg])
        entries.append(f"- {pkg}: {', '.join(names)}")
    if not entries:
        return ""
    lines = ["工程当前已声明的类（写新代码时按此引用，避免类名/包名前后不一致）："]
    lines.extend(entries)

    regs = _registered_ids(root)
    if regs:
        lines.append("")
        lines.append("已注册的 id（物品/方块/实体等，新代码引用时保持一致）：")
        lines.append("- " + ", ".join(regs))

    return "\n".join(lines)


_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
# 匹配顶层 class / interface / enum / record 声明（含修饰符，不含 import/注释里的）。
_CLASS_RE = re.compile(
    r"\b(?:public|protected|private|abstract|final|sealed|static|non-sealed)\s+"
    r"(?:class|interface|enum|record)\s+([A-Za-z_]\w*)"
)


def _is_project_source(path: str) -> bool:
    """判断一条 path 是否属于工程自身的源码（而非快照/build/反编译参考）。"""
    p = path.replace("\\", "/")
    if ".dma/" in p or "reference/" in p or "decompiled/" in p or "/build/" in p:
        return False
    return "/src/" in p or p.startswith("src/")


def _project_sources(root: Path):
    """真实工程源码文件（排除 .dma 快照 / build / reference / decompiled）。"""
    try:
        from .kernel.symbols import iter_java_files
    except Exception:  # noqa: BLE001
        return []
    return (p for p in iter_java_files(root) if _is_project_source(str(p)))


_ID_RE = re.compile(
    r"(?:Identifier\.of|new\s+Identifier|ResourceLocation\.of)\s*\(\s*"
    r"[\"']([^\"']+)[\"']\s*,\s*[\"']([^\"']+)[\"']"
)


def _registered_ids(root: Path, limit: int = 120) -> list[str]:
    """扫描工程源码里的 ``Identifier.of("ns", "path")`` 提取已注册 id（排除原版）。"""
    ids: set[str] = set()
    for path in _project_sources(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ns, name in _ID_RE.findall(text):
            # 原版 minecraft: 前缀是引用而非 mod 自身注册，排除。
            if ns in {"minecraft", "c", "br"}:
                continue
            ids.add(f"{ns}:{name}")
    return sorted(ids)[:limit]


def _summary(errors: list[dict[str, Any]]) -> str:
    """按错误类别聚合，给模型一个「先修哪个」的优先级提示。"""
    kinds: dict[str, int] = {}
    for e in errors:
        msg = (e.get("message") or "").lower()
        if "cannot find symbol" in msg or "找不到符号" in msg:
            key = "找不到符号（类名/方法名拼写或 import 缺失）"
        elif "package" in msg and "does not exist" in msg:
            key = "包不存在（依赖或映射没配好）"
        elif "incompatible types" in msg or "类型不匹配" in msg or "不兼容的类型" in msg:
            key = "类型不匹配（API 签名不对，多是版本差异）"
        elif "already defined" in msg or "duplicate" in msg or "重复" in msg:
            key = "重复定义（类名/方法重复，或示例类没删）"
        else:
            key = "其他"
        kinds[key] = kinds.get(key, 0) + 1
    if not kinds:
        return ""
    ordered = sorted(kinds.items(), key=lambda kv: -kv[1])
    parts = [f"- {name}：{count} 处" for name, count in ordered]
    return "错误分布（先修数量最多的一类，同类错误通常同源）：\n" + "\n".join(parts)


def _fallback(output: str) -> str:
    """无逐文件 javac 错误时的兜底：给日志尾部，但压掉最啰嗦的行。"""
    tail = output.strip()
    if not tail:
        return "编译失败（退出码非 0），但没有拿到错误日志。"
    # 只保留末尾 24K，并去掉纯空白行。
    tail = tail[-24_000:]
    kept = [ln for ln in tail.splitlines() if ln.strip()]
    if not kept:
        return "编译失败，日志为空。"
    return (
        "编译失败（非 javac 逐文件错误，可能是 Gradle 配置/依赖/插件问题）。"
        "相关日志尾部：\n" + "\n".join(kept[-80:])
    )
