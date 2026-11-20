from __future__ import annotations

import json
import os
import re
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from . import (
    ai,
    arttools,
    compile_feedback,
    connectors,
    devdocs,
    mcdev,
    netclient,
    paths,
    planner,
    project as project_mod,
    reference,
    research,
    resources,
    studio_bridge,
)
from .config import Config
from .kernel import mappings as kernel_mappings
from .kernel import symbols as kernel_symbols
from .kernel.edit import (
    FileCache,
    diff_text,
    insert_member_text,
    replace_body_text,
    replace_symbol,
)
from .kernel.tools import blockbench as tool_blockbench
from .kernel.tools import cache as tool_cache
from .kernel.tools import configgen as tool_configgen
from .kernel.tools import crash as tool_crash
from .kernel.tools import diagnostics as tool_diagnostics
from .kernel.tools import impact as tool_impact
from .kernel.tools import library as tool_library
from .kernel.tools import matrix as tool_matrix
from .kernel.tools import migration as tool_migration
from .kernel.tools import screenshot as tool_screenshot
from .kernel.tools import templates as tool_templates
from .kernel.tools import testgen as tool_testgen
from .kernel.tools import texture as tool_texture
from .kernel.tools import vision as tool_vision
from .kernel_bridge import KernelBridge
from .knowledge import (
    KnowledgeStore,
    decompile_jar_sources,
    knowledge_context,
    write_decompiled_tree,
)
from .memory import MemoryStore, memory_context
from .prompt import build_system_prompt
from .skills import SkillStore
from .tool_schema import TOOL_DEFINITIONS

TOOL_FENCE_RE = re.compile(r"```(?:tool|dma-tool)[ \t]*\r?\n(.*?)\r?\n?```", re.S)
TOOL_TAG_START_RE = re.compile(r"<tool\b", re.I)
TOOL_CLOSE_RE = re.compile(r"\s*</tool\s*>", re.I)
# 部分兼容通道会把原生工具调用包成 <|DSML|calls> / <|DSML|invoke> 这类标记，
# 大小写、空格和字母顺序（DSML / DSLM）都不稳定，这里放宽匹配。
DSML_TAG_RE = re.compile(
    r"<\s*(?:/\s*)?\|\s*/?\s*D\s*S\s*(?:M\s*L|L\s*M)\s*\|\s*[^>]*>", re.I
)
DSML_INVOKE_RE = re.compile(
    r"<\s*\|\s*D\s*S\s*(?:M\s*L|L\s*M)\s*\|\s*invoke\b[^>]*>", re.I
)
# 闭合标签有两种写法：</|DSML|parameter> 和 <|/DSML|invoke>。
DSML_CLOSE_RE = re.compile(
    r"<\s*(?:/\s*\|\s*|\|\s*/\s*)D\s*S\s*(?:M\s*L|L\s*M)\s*\|[^>]*>", re.I
)
FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)\n```", re.S)
PATH_EXT = (
    "java|kt|kts|gradle|properties|json|json5|toml|yml|yaml|md|txt|xml|"
    "mcmeta|lang|cfg|conf|sh|bat|cmd|ps1|py|js|ts|css|html|glsl|fsh|vsh"
)
PRE_PATH_RE = re.compile(
    rf"(?m)^[^\n]{{0,120}}?([A-Za-z0-9_.\-/\\]+\.(?:{PATH_EXT}))[^\n]{{0,40}}$"
)
INFO_PATH_RE = re.compile(
    rf"^(?:[A-Za-z0-9_+.-]+\s+)?(?:path=|file=)?([A-Za-z0-9_.\-/\\]+\.(?:{PATH_EXT}))\s*$"
)
MAX_TOOL_RESULT = 200_000
MAX_FETCH_RESULT = 60_000
MAX_STEP_RESULT = 300_000
MAX_BUILD_RESULT = 96_000
MAX_BUILD_SUCCESS = 16_000
MAX_FENCE_FILES = 40
# runServer / runClient 是常驻任务，Gradle 不会自己结束；放进 build_project
# 只会一直等到超时，然后把正常的开发端杀掉。
LONG_RUNNING_GRADLE_TASKS = {
    "runserver", "runclient", "runserverdata", "runclientdata",
}


def _build_task_error(task: str) -> str:
    if str(task or "").strip().lower() not in LONG_RUNNING_GRADLE_TASKS:
        return ""
    return (
        "build_project 不能用来启动开发端：runServer / runClient "
        "是常驻任务，Gradle 不会自己结束，套在编译超时里只会空等到"
        "超时并杀掉正常运行的进程。\n"
        "请改用 mcdev_prepare（kind=server/client/both）启动；"
        "需要先编译就执行 build_project task=build。"
    )
# 连续多久没有任何“有效进展”（写入文件或完成编译）就主动停下。
# 网络请求有 netclient 硬超时、Gradle 有 watchdog，这个兜底针对模型空转。
PROGRESS_TIMEOUT = 45 * 60
# 只读工具：纯阅读阶段不应被 PROGRESS_TIMEOUT 误杀，执行成功后刷新进展时间戳。
READ_ONLY_TOOLS = {
    "read_file", "list_files", "search_text", "search_knowledge",
    "read_knowledge", "read_skill", "search_code", "fetch_url",
    "catalog_query", "launcher_versions", "launcher_downloads",
    "launcher_status", "mod_search", "dev_docs", "large_mod_plan",
    "version_facts", "project_fact_check", "arch_ref", "multiversion_plan",
    "workflow_status", "workflow_next", "contract_check", "api_lookup",
}
# 连续只读（无任何写入）多少次后，强制打断探索循环、要求模型立即写代码。
# 只读工具刷新 _progress_at 会让 PROGRESS_TIMEOUT 永远不触发，导致「只读死循环」卡死；
# 这里用 streak 主动打断，而不是等 45 分钟兜底。
READONLY_STREAK_LIMIT = 8
# 这些校验工具的重复调用不会改变结果，重复 3 次后必须打断，
# 否则模型会一步一调地把整轮额度耗在“再确认一次”上。
VERIFY_TOOLS = {"lint_project", "validate_resources", "asset_lint", "mcdev_prepare"}
AUTO_BUILD_MAX = 5
BUILD_TRIGGER_EXTS = {
    ".java", ".kt", ".kts", ".gradle", ".properties", ".json", ".json5",
    ".toml", ".xml", ".mcmeta", ".lang", ".yml", ".yaml", ".glsl", ".fsh",
    ".vsh", ".cfg", ".conf",
}
KERNEL_TOOLS = {
    "lint_project", "symbol_query", "mapping_translate", "impact_analysis",
    "log_parse", "snapshot", "snapshot_list", "rollback", "asset_lint",
    "texture_gen", "entity_texture", "vision_review", "blockbench", "testgen", "migration_plan",
    "screenshot_collect", "offline_cache", "asset_library", "config_gen",
    "matrix_plan", "debug_scaffold", "project_fact_check",
    "workflow_plan", "workflow_status", "workflow_next", "workflow_complete",
    "workflow_fail", "workflow_skip", "workflow_reopen",
    "contract_freeze", "contract_check",
}
# 版本中心 / 启动器联动 / 模组学习：不依赖当前工程。
CENTER_TOOLS = {
    "catalog_sync", "catalog_query", "catalog_download",
    "mod_search", "mod_download", "mod_decompile", "mod_reference",
    "dev_docs", "api_lookup", "resource_ensure", "large_mod_plan",
    "version_facts", "arch_ref", "multiversion_plan",
    "launcher_versions", "launcher_downloads", "launcher_vanilla",
    "launcher_launch", "launcher_status",
}


def _split_symbol(symbol: str) -> tuple[str, str]:
    text = (symbol or "").strip()
    if not text:
        return "", ""
    if "#" in text:
        left, right = text.split("#", 1)
        return left.strip(), right.strip().split("(")[0].strip()
    if "." in text:
        left, right = text.rsplit(".", 1)
        return left.strip(), right.strip().split("(")[0].strip()
    return "", text.split("(")[0].strip()


def _needs_build(paths: list[str]) -> bool:
    """只改文档和图片时不必跑 Gradle。"""
    for path in paths:
        suffix = Path(str(path)).suffix.lower()
        if not suffix or suffix in BUILD_TRIGGER_EXTS:
            return True
    return False


def _parse_pixel_color(value: Any) -> tuple[int, int, int, int]:
    text = str(value or "").strip().lower()
    if text in {"", "transparent", "none"}:
        return (0, 0, 0, 0)
    if text.startswith("#"):
        text = text[1:]
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) == 6:
        text += "ff"
    if len(text) != 8 or not re.fullmatch(r"[0-9a-f]{8}", text):
        raise ValueError(f"颜色格式不对：{value}（用 #RRGGBB 或 #RRGGBBAA）")
    return tuple(int(text[index:index + 2], 16) for index in range(0, 8, 2))  # type: ignore[return-value]


def _encode_pixel_png(
    palette: dict[str, Any], rows: list[Any]
) -> tuple[bytes, int, int]:
    if not rows or not isinstance(rows, list):
        raise ValueError("rows 不能为空。")
    height = len(rows)
    width = len(str(rows[0]))
    if width < 1 or height < 1 or width > 512 or height > 512:
        raise ValueError("纹理尺寸必须在 1x1 到 512x512 之间。")
    colors = {str(key): _parse_pixel_color(value) for key, value in palette.items()}
    pixels: list[bytes] = []
    for index, row in enumerate(rows):
        line = str(row)
        if len(line) != width:
            raise ValueError(f"第 {index + 1} 行长度是 {len(line)}，应为 {width}。")
        chunk = bytearray()
        for char in line:
            if char not in colors:
                raise ValueError(f"第 {index + 1} 行出现调色板里没有的字符：{char}")
            chunk.extend(colors[char])
        pixels.append(bytes(chunk))
    raw = b"".join(b"\x00" + line for line in pixels)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    return png, width, height


class AgentError(Exception):
    pass


@dataclass
class PendingFile:
    path: str
    content: str
    reason: str = ""
    applied: bool = False

    def preview(self, limit: int = 160) -> str:
        text = self.content.strip().replace("\n", " ")
        return text[:limit] + ("…" if len(text) > limit else "")


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    raw: str = ""
    call_id: str = ""


@dataclass
class AgentRun:
    text: str = ""
    pending: list[PendingFile] = field(default_factory=list)
    applied: list[str] = field(default_factory=list)
    steps: int = 0


def _balanced_json(text: str, start: int) -> str | None:
    """从 start 的 { 开始截取一个配平的 JSON 对象，字符串里的括号不计入。"""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _tag_payload_start(text: str, position: int) -> int | None:
    limit = min(len(text), position + 16)
    cursor = position
    while cursor < limit and text[cursor] in " \t\r\n:=>":
        cursor += 1
    if cursor < len(text) and text[cursor] == "{":
        return cursor
    brace = text.find("{", cursor, limit)
    return brace if brace != -1 else None


def _tool_blocks(text: str) -> list[tuple[str, int, int]]:
    """收集所有工具调用：```tool 代码块，以及 <tool ...> 标签。

    有些兼容接口会把标签写成 `<tool {json}`（缺 `>` 或闭合标签），
    这里按 JSON 配平截取，避免整轮任务因为格式差一个字符就提前结束。
    """
    source = text or ""
    blocks: list[tuple[str, int, int]] = []
    fence_spans: list[tuple[int, int]] = []
    for match in TOOL_FENCE_RE.finditer(source):
        fence_spans.append((match.start(), match.end()))
        blocks.append((match.group(1).strip(), match.start(), match.end()))
    for match in TOOL_TAG_START_RE.finditer(source):
        if any(start <= match.start() < end for start, end in fence_spans):
            continue
        start = _tag_payload_start(source, match.end())
        if start is None:
            continue
        payload = _balanced_json(source, start)
        if payload is None:
            continue
        end = start + len(payload)
        closing = TOOL_CLOSE_RE.match(source, end)
        if closing:
            end = closing.end()
        blocks.append((payload, match.start(), end))
    for match in DSML_INVOKE_RE.finditer(source):
        if any(start <= match.start() < end for _, start, end in blocks):
            continue
        brace = source.find("{", match.end(), min(len(source), match.end() + 4000))
        if brace == -1:
            continue
        # 里面已经有 <tool> 标签时交给上面的分支处理，避免重复执行。
        if "<tool" in source[match.end():brace].lower():
            continue
        close = DSML_CLOSE_RE.search(source, brace)
        payload = None
        if close is not None:
            # 有闭合标签时直接按标签截取。edit_file 的 old / new 里经常带
            # 未转义的双引号（Java 代码），按括号配平容易被引号带偏。
            chunk = source[brace:close.start()].strip()
            cut = chunk.rfind("}")
            if cut != -1:
                chunk = chunk[: cut + 1]
            if chunk.startswith("{") and chunk.endswith("}"):
                payload = chunk
        if payload is None:
            payload = _balanced_json(source, brace)
        if payload is None or '"tool"' not in payload:
            continue
        end = close.end() if close is not None else brace + len(payload)
        blocks.append((payload, match.start(), end))
    return blocks


def parse_tool_calls(text: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    seen: set[str] = set()
    for payload, _, _ in _tool_blocks(text):
        key = payload.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        calls.extend(_parse_call_payload(payload))
    return calls


def _looks_like_tool_attempt(text: str) -> bool:
    return bool(
        re.search(
            r"<tool\b|```\s*(?:tool|dma-tool)\b|DSML|DSLM",
            text or "",
            re.I,
        )
    )


INTENT_MARKER_RE = re.compile(
    r"(?:我先|让我|我继续|继续|接下来|现在|先|首先|下一步|我这就|我来|我去|我准备)"
    r"[^。！？!?\n]{0,12}?"
    r"(?:读取|读|查看|看一下|看|检查|查|搜索|搜|找|打开|确认|分析|核对|定位|梳理|了解)"
)


def _looks_like_action_intent(text: str) -> bool:
    """识别“我先读 / 让我看看 / 接下来检查”这类只写计划、没有工具调用的回复。"""
    body = (text or "").strip()
    if not body:
        return False
    tail = body[-240:]
    if re.search(r"已经|已读取|已查看|已完成|已修改|已修复|总结如下|结论如下", tail):
        return False
    return bool(INTENT_MARKER_RE.search(tail))


HISTORY_CHAR_BUDGET = 600_000
MESSAGE_CHAR_BUDGET = 2_000_000


def _history_window(history: list[dict[str, str]], turns: int) -> list[dict[str, str]]:
    """按轮数和字符预算截取历史，长对话不会把上下文塞爆。"""
    items = list(history[-max(1, turns) * 2:])
    cleaned: list[dict[str, str]] = []
    for item in items:
        content = str(item.get("content") or "")
        if item.get("role") == "assistant":
            # 旧版本可能把 DSML / <tool> 原始标记存进了会话记录，
            # 升级后继续对话时先清掉，避免模型模仿这些标记。
            content = strip_tool_blocks(content)
            if not content:
                continue
        cleaned.append({"role": item.get("role", "user"), "content": content})
    kept: list[dict[str, str]] = []
    total = 0
    for item in reversed(cleaned):
        length = len(str(item.get("content") or ""))
        if kept and total + length > HISTORY_CHAR_BUDGET:
            break
        kept.append(item)
        total += length
    kept.reverse()
    return kept


def _compact_messages(messages: list[dict[str, Any]]) -> None:
    """把本轮较早的大块工具结果折叠掉，保留最近几轮完整内容。"""
    total = sum(len(str(item.get("content") or "")) for item in messages)
    if total <= MESSAGE_CHAR_BUDGET:
        return
    limit = max(0, len(messages) - 1)
    for index in range(2, limit):
        if total <= MESSAGE_CHAR_BUDGET:
            break
        item = messages[index]
        content = str(item.get("content") or "")
        role = item.get("role")
        if role == "assistant":
            # 模型每轮都会重新贴一遍完整文件，长任务里这些回显比工具结果更大。
            if len(content) <= 8000:
                continue
            shortened = content[:1200] + "\n…（较早的模型输出已折叠）"
            item["content"] = shortened
            total -= len(content) - len(shortened)
            continue
        if role == "user":
            if not content.startswith("[工具结果："):
                continue
        elif role != "tool":
            continue
        if len(content) <= 1800:
            continue
        shortened = content[:1500] + "\n…（较早的工具结果已折叠）"
        item["content"] = shortened
        total -= len(content) - len(shortened)


LOOSE_KEY_RE = re.compile(r'(?:^|,)\s*"([A-Za-z_][A-Za-z0-9_-]*)"\s*:\s*')


def _unescape_json_text(text: str) -> str:
    mapping = {
        "n": "\n",
        "t": "\t",
        "r": "\r",
        "b": "\b",
        "f": "\f",
        '"': '"',
        "\\": "\\",
        "/": "/",
    }
    result: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in mapping:
            result.append(mapping[text[index + 1]])
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _loose_json_value(text: str) -> Any:
    value = text.strip()
    if not value:
        return ""
    if value[0] in "{[":
        try:
            return json.loads(value, strict=False)
        except ValueError:
            pass
    if value.startswith('"'):
        body = value[1:-1] if value.endswith('"') and len(value) >= 2 else value[1:]
        return _unescape_json_text(body)
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    return value


def _valid_loose_value(value: str) -> bool:
    if not value:
        return False
    if value.startswith('"'):
        return len(value) >= 2 and value.endswith('"')
    if value[0] in "{[":
        try:
            json.loads(value, strict=False)
            return True
        except ValueError:
            return False
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return True
    return value in {"true", "false", "null"}


def _loose_json_object(raw: str) -> dict[str, Any] | None:
    """解析模型写坏了的 JSON：字符串值里的双引号没有转义。"""
    text = (raw or "").strip()
    if not (text.startswith("{") and text.endswith("}")):
        return None
    inner = text[1:-1]
    matches = list(LOOSE_KEY_RE.finditer(inner))
    if not matches:
        return None
    result: dict[str, Any] = {}
    for index, match in enumerate(matches):
        key = match.group(1)
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(inner)
        value = inner[start:end].strip()
        if value.endswith(","):
            value = value[:-1].rstrip()
        if not _valid_loose_value(value):
            return None
        result[key] = _loose_json_value(value)
    return result


def _parse_call_payload(raw: str) -> list[ToolCall]:
    try:
        data = json.loads(raw, strict=False)
    except ValueError:
        data = _loose_json_object(raw)
        if data is None:
            return []
    items = data if isinstance(data, list) else [data]
    result: list[ToolCall] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or "").strip()
        args = item.get("args") if isinstance(item.get("args"), dict) else None
        if args is None:
            args = {
                key: value
                for key, value in item.items()
                if key not in {"tool", "name", "args"}
            }
        if name:
            result.append(ToolCall(name=name, args=args, raw=raw))
    return result


def strip_tool_blocks(text: str) -> str:
    source = text or ""
    blocks = sorted(_tool_blocks(source), key=lambda item: item[1])
    if blocks:
        pieces: list[str] = []
        cursor = 0
        for _, start, end in blocks:
            if start > cursor:
                pieces.append(source[cursor:start])
            cursor = max(cursor, end)
        pieces.append(source[cursor:])
        source = "".join(pieces)
    source = DSML_TAG_RE.sub("", source)
    return re.sub(r"\n{3,}", "\n\n", source).strip()


def _looks_like_path(value: str) -> bool:
    text = (value or "").strip().strip("`'\"*")
    if not text or len(text) > 240:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if ".." in text or text.startswith(("/", "\\")):
        return False
    if " " in text:
        return False
    if not re.search(rf"\.(?:{PATH_EXT})$", text, re.I):
        return False
    return "/" in text or "\\" in text


def extract_file_blocks(text: str) -> list[PendingFile]:
    """从 Markdown 代码块中提取带路径的文件。"""
    files: list[PendingFile] = []
    index_by_path: dict[str, int] = {}
    source = text or ""
    for match in FENCE_RE.finditer(source):
        info = match.group(1).strip()
        first_word = info.split()[0].lower() if info else ""
        if first_word in {"tool", "dma-tool"}:
            continue
        content = match.group(2)
        path = ""
        info_match = INFO_PATH_RE.match(info)
        if info_match and _looks_like_path(info_match.group(1)):
            path = info_match.group(1)
        if not path:
            before = source[max(0, match.start() - 360): match.start()]
            candidates = [
                item.group(1)
                for item in PRE_PATH_RE.finditer(before)
                if _looks_like_path(item.group(1))
            ]
            if candidates:
                path = candidates[-1]
        if not path:
            continue
        normalized = path.replace("\\", "/").strip().strip("`'\"*")
        if normalized in index_by_path:
            files[index_by_path[normalized]].content = content
            continue
        index_by_path[normalized] = len(files)
        files.append(PendingFile(path=normalized, content=content))
        if len(files) >= MAX_FENCE_FILES:
            break
    return files


def _arg_int(args: dict[str, Any], key: str, default: int) -> int:
    """把工具参数转成整数；模型传了非数字时给出可读错误，而不是 traceback。"""
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {key} 必须是整数，收到：{raw!r}") from exc


def _arg_float(args: dict[str, Any], key: str, default: float) -> float:
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"参数 {key} 必须是数字，收到：{raw!r}") from exc


def _depth_ok(path: Path, root: Path, depth: int) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) <= max(1, depth)


def _list_files(
    root: Path,
    path: str,
    depth: int,
    offset: int = 0,
    limit: int = 300,
) -> dict[str, Any]:
    base = project_mod.safe_path(root, path or ".")
    if not base.is_dir():
        return {"files": [], "truncated": False, "total": 0, "error": f"不是目录：{path}"}
    rows: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in project_mod.SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            item = Path(current) / name
            if not _depth_ok(item, base, depth):
                continue
            rows.append(item.relative_to(base).as_posix())
    total = len(rows)
    offset = max(0, offset)
    limit = max(1, min(int(limit), 1000))
    page = rows[offset:offset + limit]
    return {
        "files": page,
        "truncated": offset + limit < total,
        "total": total,
    }


class AgentWorker(QObject):
    status = Signal(str)
    assistant = Signal(str)
    pending_changed = Signal(object)
    artifact_changed = Signal(object)
    design_request = Signal(object)
    project_created = Signal(str)
    done = Signal(str)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        *,
        user_text: str,
        history: list[dict[str, str]],
        project: project_mod.Project | None,
        config: Config,
        skill_store: SkillStore,
        memory: MemoryStore,
        connector_store: connectors.ConnectorStore,
        knowledge: KnowledgeStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.user_text = user_text
        self.history = [dict(item) for item in history]
        self.project = project
        self.config = config
        self.skill_store = skill_store
        self.memory = memory
        self.knowledge = knowledge
        self.connector_store = connector_store
        self.pending: list[PendingFile] = []
        self.applied: list[str] = []
        self.mcdev = mcdev.shared()
        self.mcdev.set_log(self._probe_log)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress_at = time.monotonic()
        self._readonly_streak = 0
        self.kernel: KernelBridge | None = None
        self._file_cache = FileCache()
        self._read_offsets: dict[str, int] = {}
        self._native_disabled = False
        self._symbol_index = None
        # scaffold_project 可能在对话中途才创建工程；创建后必须把任务图
        # 重新绑到真实目录，否则 workflow_* 只改内存，不会落盘也不会拦验收。
        self._project_dirty = False
        # 桌面端创建 worker 后会置为 True，用于弹出方案选择窗口；
        # 无界面的调用（测试、脚本）默认自动采用第一个方案。
        self.ui_available = False

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    @property
    def alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _add_pending(self, item: PendingFile) -> None:
        for index, existing in enumerate(self.pending):
            if existing.path == item.path:
                self.pending[index] = item
                break
        else:
            self.pending.append(item)
        self.pending_changed.emit(list(self.pending))

    def _run(self) -> None:
        previous_cancel = netclient.bind_cancel(self._cancel)
        # 共享的 mcdev 服务默认没有取消信号；不绑定的话，聊天里的“停止”
        # 只能在工具返回后生效，启动开发端那段会一直等到超时。
        previous_mcdev_cancel = self.mcdev.set_cancel(self._cancel)
        try:
            run = self._loop()
        except Exception as exc:
            if not self.cancelled:
                self.failed.emit(str(exc))
        else:
            if not self.cancelled:
                self.done.emit(run.text)
        finally:
            self.mcdev.restore_cancel(previous_mcdev_cancel, self._cancel)
            netclient.restore_cancel(previous_cancel)
            self.finished.emit()

    # ------------------------------------------------------------------
    def _probe_log(self, text: str) -> None:
        line = (text or "").strip()
        if line:
            self.status.emit(line[-200:])

    def _chat(self, messages: list[dict[str, Any]]) -> tuple[str, list[ToolCall], dict | None]:
        """Native function calling first, text protocol only as a fallback."""
        # 模型接口没有流式进度，等待期间每 5 秒报一次心跳，避免界面看起来像死机。
        stop = threading.Event()
        started = time.time()

        def beat() -> None:
            while not stop.wait(5):
                self.status.emit(
                    f"正在等待模型响应…已用 {int(time.time() - started)} 秒"
                )

        threading.Thread(target=beat, daemon=True).start()
        try:
            return self._chat_once(messages)
        finally:
            stop.set()

    def _chat_once(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[ToolCall], dict | None]:
        if getattr(self.config, "native_tools", False) and not self._native_disabled:
            try:
                result = ai.request_chat_tools(
                    self.config.ai, messages, TOOL_DEFINITIONS, cancel=self._cancel
                )
            except ai.AIToolsUnsupported:
                self._native_disabled = True
                self.status.emit("当前通道不支持原生工具调用，回退到文本协议。")
            except ai.AIError:
                raise
            else:
                calls = [
                    ToolCall(
                        name=item["name"],
                        args=item.get("arguments") or {},
                        call_id=item.get("id") or "",
                    )
                    for item in result.get("tool_calls") or []
                ]
                return result.get("content") or "", calls, result.get("message") or {}
        reply = ai.request_chat(self.config.ai, messages, cancel=self._cancel)
        return reply, parse_tool_calls(reply), None

    def _build_cache_path(self) -> Path | None:
        root = getattr(self.project, "root", None)
        if root is None:
            return None
        return Path(root) / ".dma" / "last_build.txt"

    def _save_build_report(self, report: str) -> None:
        """把最后一次编译结果落盘，下一轮开局直接拿错误清单，不再从头探索。"""
        path = self._build_cache_path()
        if path is None or not report:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report[:120_000], encoding="utf-8")
        except OSError:
            pass

    def _load_build_report(self) -> str:
        path = self._build_cache_path()
        if path is None:
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    def _build_cache_stale(self) -> bool:
        """缓存之后如果 src 又被改过，提示模型先重新编译再动手。"""
        path = self._build_cache_path()
        root = getattr(self.project, "root", None)
        if path is None or root is None or not path.is_file():
            return False
        try:
            built_at = path.stat().st_mtime
            for item in (Path(root) / "src").rglob("*"):
                if item.suffix in {".java", ".kt"} and item.stat().st_mtime > built_at:
                    return True
        except OSError:
            return False
        return False

    def _validate_cache_path(self) -> Path | None:
        root = getattr(self.project, "root", None)
        if root is None:
            return None
        return Path(root) / ".dma" / "last_validate.txt"

    def _save_validate_report(self, report: str) -> None:
        """缓存资源校验清单，下一轮开局直接接着补缺失项，不必重新扫一遍。"""
        path = self._validate_cache_path()
        if path is None or not report:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(report[:100_000], encoding="utf-8")
        except OSError:
            pass

    def _load_validate_report(self) -> str:
        path = self._validate_cache_path()
        if path is None:
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _validate_report_has_issues(report: str) -> bool:
        if not report:
            return False
        if "未发现问题" in report:
            return False
        return "问题总数" in report or "注册项覆盖" in report

    def _resume_workspace(self) -> bool:
        """工程里已有任务图时视为续做，避免首轮又从头规划和联网找参考。"""
        root = getattr(self.project, "root", None)
        if root is None:
            return False
        try:
            return (Path(root) / ".dma" / "workflow.json").is_file()
        except OSError:
            return False

    def _reference_preflight(self) -> str:
        """实现类需求先在线找参考：搜索、反编译、抽取片段，再交给模型。

        只在识别到明确功能类型时触发，避免“继续”“修一下”这类指令也去下载。
        失败不会阻塞主流程，模型仍可自己调用 mod_reference。
        """
        if not bool(getattr(self.config, "auto_reference", True)):
            return ""
        # 续轮不再重复联网找参考：首轮已经把资料写进上下文，每轮重搜既慢又打断修复节奏。
        if getattr(self, "history", None) or self._resume_workspace():
            return ""
        project = self.project
        if project is None:
            return ""
        features = reference.detect_features(self.user_text)
        if not features:
            return ""
        meta = project.meta
        loader = str(getattr(meta, "loader", "") or "")
        mc = str(getattr(meta, "mc_version", "") or "")
        self.status.emit(
            "正在在线搜索参考模组（"
            + "、".join(item.label for item in features[:4])
            + "）…"
        )
        try:
            result = reference.auto_reference(
                self.user_text,
                loader=loader,
                mc=mc,
                max_mods=2,
                max_snippets=8,
                progress=lambda percent, message: self.status.emit(
                    f"{message}（{int(percent)}%）"
                ),
                cancel=self._cancel,
            )
        except Exception as exc:  # noqa: BLE001 - 预检失败降级为工具提示
            return (
                "【在线参考（自动）】\n"
                f"自动准备参考失败：{exc}\n"
                "可以改用 mod_reference auto 或 dev_docs lookup 重试；"
                "不要凭记忆编造 API。"
            )
        text = reference.render_auto(result, snippet_limit=4)
        return "【在线参考（自动反编译，只读）】\n" + text

    def _large_plan_preflight(self) -> str:
        """需求跨多个机制时先生成阶段计划，避免大型模组写成散装文件。"""
        # 续轮沿用首轮的计划，不再每轮重建，避免模型被重复计划淹没而忘记编译。
        if getattr(self, "history", None) or self._resume_workspace():
            return ""
        project = self.project
        if project is None:
            return ""
        features = reference.detect_features(self.user_text)
        if len(features) < 3:
            return ""
        meta = project.meta
        scope = "epic" if len(features) >= 6 else "large"
        try:
            plan = planner.build_plan(
                self.user_text,
                loader=str(getattr(meta, "loader", "") or ""),
                mc=str(getattr(meta, "mc_version", "") or ""),
                scope=scope,
                modid=str(getattr(meta, "mod_id", "") or ""),
                package="",
            )
        except Exception as exc:  # noqa: BLE001 - 规划失败不阻塞任务
            return f"【大型模组计划】生成失败：{exc}；仍可用 large_mod_plan 重试。"
        self.status.emit(
            f"需求覆盖 {len(features)} 类机制，已生成 {scope} 规模计划。"
        )
        return (
            "【大型模组计划（自动）】\n"
            "按下面的阶段推进，注册与契约先于业务逻辑；不要跳过后面的编译与验证。\n\n"
            + planner.render(plan)
        )

    def _bind_kernel(self) -> None:
        """把任务图/规格绑定到当前工程目录。

        没有工程时 KernelBridge 只在内存里建任务图，workflow_complete 会
        假装成功却不落盘；scaffold_project 之后必须重新绑定。
        """
        project = self.project
        meta = getattr(project, "meta", None)
        self.kernel = KernelBridge(
            project.root if project is not None else None,
            idea=self.user_text,
            loader=getattr(meta, "loader", "") or "NeoForge",
            mc=getattr(meta, "mc_version", "") or "1.21.1",
            modid=getattr(meta, "mod_id", "") or "",
            package="",
            side="both",
        )

    def _loop(self) -> AgentRun:
        project = self.project
        project_key = str(project.root) if project is not None else ""
        memory_items = []
        if project is not None:
            memory_items = self.memory.recall(project_key, self.user_text, limit=6)
        knowledge_hits = []
        if self.knowledge is not None:
            knowledge_hits = self.knowledge.search(
                self.user_text, project=project_key, limit=6, budget=150_000
            )
            if not knowledge_hits:
                # 中文问题常和英文类名对不上，至少把 JAR 的速览/类索引送进上下文。
                knowledge_hits = self.knowledge.overview_hits(
                    project=project_key, limit=1
                )
        self._bind_kernel()
        plan_text = self._large_plan_preflight()
        reference_text = self._reference_preflight()
        facts_text = ""
        if project is not None:
            try:
                from . import modfacts

                meta = getattr(project, "meta", None)
                facts_text = modfacts.brief(
                    str(getattr(meta, "loader", "") or ""),
                    str(getattr(meta, "mc_version", "") or ""),
                )
            except Exception:  # noqa: BLE001 - 事实层失败不应阻断对话
                facts_text = ""
        system = build_system_prompt(
            project,
            self.skill_store,
            memory_context(memory_items),
            self.connector_store.catalog_text(),
            max_steps=self.config.max_steps,
            knowledge_text=knowledge_context(knowledge_hits),
            query=self.user_text,
            kernel_text=self.kernel.prompt_block(),
            plan_text=plan_text,
            reference_text=reference_text,
            facts_text=facts_text,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(_history_window(self.history, self.config.context_turns))
        messages.append({"role": "user", "content": self.user_text})
        if self._resume_workspace():
            # 续做工程：上一轮的编译结果直接带进上下文，模型不必再读十几个文件
            # 猜哪里出错，能省下一整段探索步骤和 token。
            cached = self._load_build_report()
            if cached and "错误" in cached:
                stale = self._build_cache_stale()
                head = (
                    "这是上一轮最后一次编译的结果缓存，直接按这份清单修，"
                    "不要再读文件探索、不要重新规划。"
                )
                if stale:
                    head += (
                        "注意：缓存之后 src 又被改过，先调用 build_project 重新编译确认，"
                        "再按最新结果修。"
                    )
                messages.append(
                    {
                        "role": "user",
                        "content": head + "\n\n" + cached[:60_000],
                    }
                )
            validate_cached = self._load_validate_report()
            if self._validate_report_has_issues(validate_cached):
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "这是上一轮 validate_resources 的缺失清单（缓存，可能已补过一部分）。"
                            "直接按清单继续补齐，不要重新读文件或重新规划；"
                            "清单里的条目先看目标文件是否已存在，存在就跳过。"
                            "补齐后再跑一次 validate_resources 拿最新结果。\n\n"
                            + validate_cached[:60_000]
                        ),
                    }
                )

        def refresh_system() -> None:
            """scaffold_project 之后重建系统提示，补上真实的工程信息。"""
            current = self.project
            facts = ""
            if current is not None:
                try:
                    from . import modfacts

                    meta = getattr(current, "meta", None)
                    facts = modfacts.brief(
                        str(getattr(meta, "loader", "") or ""),
                        str(getattr(meta, "mc_version", "") or ""),
                    )
                except Exception:  # noqa: BLE001 - 事实层失败不应阻断对话
                    facts = ""
            messages[0] = {
                "role": "system",
                "content": build_system_prompt(
                    current,
                    self.skill_store,
                    memory_context(memory_items),
                    self.connector_store.catalog_text(),
                    max_steps=self.config.max_steps,
                    knowledge_text=knowledge_context(knowledge_hits),
                    query=self.user_text,
                    kernel_text=(
                        self.kernel.prompt_block() if self.kernel is not None else ""
                    ),
                    plan_text=plan_text,
                    reference_text=reference_text,
                    facts_text=facts,
                ),
            }
        final_text = ""
        run = AgentRun()
        limit = max(0, int(self.config.max_steps or 0))
        step = 0
        broken_tool_retries = 0
        intent_retries = 0
        auto_builds = 0
        applied_mark = 0
        pending_fix = False
        last_build_report = ""
        last_signature = ""
        repeat_rounds = 0
        recent_sigs: list[str] = []
        self._recent_edits: list[tuple[str, str, str]] = []
        verify_counts: dict[str, int] = {}
        verify_nudged: set[str] = set()
        last_applied = len(run.applied)
        no_change_rounds = 0

        while True:
            if limit > 0 and step >= limit:
                # 轮次步数用尽：如果这轮写过代码却还没编译，先补一次 compileJava，
                # 把最新错误清单落盘，下一轮开局就能直接接着修。
                if len(run.applied) > applied_mark:
                    self._quick_build()
                break
            if self.cancelled:
                raise AgentError("已取消")
            if time.monotonic() - self._progress_at > PROGRESS_TIMEOUT:
                raise AgentError(
                    f"本轮已有 {PROGRESS_TIMEOUT // 60} 分钟没有写入文件或完成编译，"
                    "已停止，避免一直空转。请检查网络/模型输出后再发送「继续」。"
                )
            step += 1
            run.steps = step
            if self._readonly_streak >= READONLY_STREAK_LIMIT:
                # 连续只读多次却没有任何写入，说明模型陷入了「反复读文件、不动手」的
                # 探索死循环（只读工具会刷新 _progress_at，PROGRESS_TIMEOUT 拦不住）。
                # 主动注入强指令打断循环。
                self._readonly_streak = 0
                self.status.emit("连续多次只读未写入，正在要求模型立即动手…")
                if self._resume_workspace():
                    # 续做已有工程：这里最忌让模型“重写入口类”，会把成熟代码整批覆盖。
                    guidance = (
                        "你已经连续多次只读文件（list_files / read_file）却没有做任何修改。"
                        "这是续做已有工程，不要重写入口类、注册类或任何已有文件。"
                        "停止继续读取，改为：先调用 build_project 拿到真实编译错误，"
                        "再按 文件:行号 用 edit_file 修第一组错误，修完立即重新编译。"
                        "不要输出计划，直接调用工具。"
                    )
                else:
                    guidance = (
                        "你已经连续多次只读文件（list_files / read_file）却没有写任何代码，"
                        "陷入了探索循环。停止继续读取，立即开始实现："
                        "用 write_file 直接写出第一个代码文件（入口类或注册类），"
                        "然后继续写后续文件。不要再输出「我先看看」这类描述，直接调用写文件工具。"
                    )
                messages.append({"role": "user", "content": guidance})
            if limit <= 0:
                self.status.emit(f"正在请求模型（第 {step} 轮，不限制）…")
            else:
                self.status.emit(f"正在请求模型（第 {step}/{limit} 轮）…")
            reply, calls, native_message = self._chat(messages)

            visible = strip_tool_blocks(reply)
            for item in extract_file_blocks(visible or reply):
                self._stage_file(item, run)

            if not calls:
                if broken_tool_retries < 2 and _looks_like_tool_attempt(reply):
                    # 模型偶尔会漏掉 > 或闭合标签，任务会看起来“突然结束”。
                    broken_tool_retries += 1
                    self.status.emit("工具调用格式不完整，正在重试…")
                    messages.append({"role": "assistant", "content": reply})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "上面的工具调用没有解析成功。请重新输出完整的工具调用："
                                "```tool\n{\"tool\":\"...\"}\n```，"
                                "JSON 必须完整，代码块或 <tool> 标签都要闭合。"
                            ),
                        }
                    )
                    continue
                if intent_retries < 2 and _looks_like_action_intent(visible or reply):
                    # 有些模型只回一句“我先读 xxx”，不发工具调用，循环会把它当最终答复。
                    intent_retries += 1
                    self.status.emit("模型只描述了计划，正在要求它直接执行…")
                    messages.append({"role": "assistant", "content": reply})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "你刚才只说明了下一步要做什么，没有真正调用工具，任务会停在这里。"
                                "请不要只描述计划：现在就输出工具调用"
                                "（```tool\n{\"tool\":\"...\"}\n```，一次可以调用多个），"
                                "直接读取、搜索或修改。如果任务已经完成，请直接给出最终结论，"
                                "不要再预告下一步。"
                            ),
                        }
                    )
                    continue
                if auto_builds < AUTO_BUILD_MAX and self._should_auto_build(run, applied_mark):
                    auto_builds += 1
                    applied_mark = len(run.applied)
                    ok, report = self._auto_build()
                    base = (visible or reply.strip()).strip()
                    if ok:
                        final_text = self._finalize_text(
                            (base + "\n\n自动编译通过。\n" + report).strip()
                        )
                        run.text = final_text
                        return run
                    if auto_builds >= AUTO_BUILD_MAX or (
                        self.kernel is not None and self.kernel.guard.should_stop()
                    ):
                        final_text = (
                            base + "\n\n自动编译失败，已停止自动修复。\n\n" + report
                        ).strip()
                        run.text = final_text
                        return run
                    pending_fix = True
                    last_build_report = report
                    messages.append({"role": "assistant", "content": reply})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                "宿主已经自动执行 build，编译失败。请根据下面的日志修复代码，"
                                "修好后会再自动编译一次。不要只描述计划，"
                                "直接调用 write_file 或 edit_file。\n\n"
                                + report[:MAX_BUILD_RESULT]
                            ),
                        }
                    )
                    continue
                final_text = visible or reply.strip()
                if not visible and _looks_like_tool_attempt(reply):
                    final_text = (
                        "模型连续输出了无法解析的工具调用，本轮先停在这里。"
                        "请再发送「继续」，或检查当前模型通道是否支持工具调用。"
                    )
                if pending_fix and len(run.applied) <= applied_mark:
                    final_text = (
                        final_text
                        + "\n\n自动编译失败，而且这一轮没有检测到代码改动。\n\n"
                        + last_build_report
                    ).strip()
                if intent_retries >= 2 and _looks_like_action_intent(final_text):
                    final_text += "\n\n（这次没有实际执行工具调用。再发送「继续」我会接着执行。）"
                final_text = self._finalize_text(final_text)
                run.text = final_text
                return run

            if visible:
                self.assistant.emit(visible)

            signature = "||".join(
                sorted(
                    f"{call.name}:"
                    + json.dumps(call.args, sort_keys=True, ensure_ascii=False)
                    for call in calls
                )
            )
            if signature == last_signature:
                repeat_rounds += 1
            else:
                last_signature = signature
                repeat_rounds = 0
            call_sigs = [
                call.name
                + ":"
                + json.dumps(call.args, sort_keys=True, ensure_ascii=False)
                for call in calls
                if call.name
                in {
                    "write_file",
                    "edit_file",
                    "replace_in_file",
                    "apply_patch",
                    "scaffold_project",
                    "scaffold_forge_project",
                }
            ]
            if call_sigs:
                recent_sigs.extend(call_sigs)
                del recent_sigs[:-30]
            looping_calls = any(recent_sigs.count(sig) >= 3 for sig in call_sigs)
            if native_message is not None:
                assistant_message: dict[str, Any] = {"role": "assistant", "content": reply}
                if native_message.get("tool_calls"):
                    assistant_message["tool_calls"] = native_message["tool_calls"]
                messages.append(assistant_message)
                for call in calls:
                    if self.cancelled:
                        raise AgentError("已取消")
                    self.status.emit(_tool_status(call))
                    result = self._execute(call, run)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.call_id or call.name,
                            "content": result[:MAX_TOOL_RESULT],
                        }
                    )
            else:
                messages.append({"role": "assistant", "content": reply})
                results: list[str] = []
                for call in calls:
                    if self.cancelled:
                        raise AgentError("已取消")
                    self.status.emit(_tool_status(call))
                    result = self._execute(call, run)
                    results.append(f"[工具结果：{call.name}]\n{result}")
                messages.append(
                    {
                        "role": "user",
                        "content": "\n\n".join(results)[:MAX_STEP_RESULT],
                    }
                )
            for call in calls:
                if call.name not in VERIFY_TOOLS:
                    continue
                sig = call.name + ":" + json.dumps(call.args, sort_keys=True, ensure_ascii=False)
                verify_counts[sig] = verify_counts.get(sig, 0) + 1
                if verify_counts[sig] >= 3 and sig not in verify_nudged:
                    verify_nudged.add(sig)
                    self.status.emit(f"{call.name} 已重复执行，提醒模型换下一步…")
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"{call.name} 这一轮已经执行过多次，结果不会变化。"
                                "不要再用同样的参数重复校验：按上一次的报告去修文件，"
                                "或者换下一项检查（build_project / validate_resources / "
                                "lint_project / mcdev_prepare），也可以直接给出结论。"
                            ),
                        }
                    )
            if repeat_rounds >= 2:
                # 模型有时会连续多轮输出完全相同的一组工具调用（尤其是一次写多个
                # 文件后），把“已完成”误当成“还没开始”。这里明确打断。
                repeat_rounds = 0
                self.status.emit("检测到重复的工具调用，已要求继续下一步…")
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你连续多轮输出了完全相同的工具调用。上面的文件和修改已经执行过，"
                            "结果就是你现在看到的内容，重复调用不会产生新结果。"
                            "请立刻停止重复：检查当前工程还缺什么，继续写下一个不同的文件；"
                            "如果代码已经写完，直接调用 build_project（或 gradlew build）编译并修复错误。"
                        ),
                    }
                )
            if looping_calls:
                # 同一个写操作用完全相同的参数执行了三次：再继续只会白烧上下文，
                # 直接结束本轮，让外层重新发起并先编译拿新错误。
                self.status.emit("检测到重复写入，提前结束本轮…")
                run.text = self._finalize_text(
                    "同一个写操作已经用完全相同的参数执行了三次，结果不会改变，"
                    "本轮先停在这里。工程代码已保留；下一轮请先调用 build_project "
                    "拿到最新编译错误，再按报错行修复，不要重复同一次替换。"
                )
                return run
            if self._project_dirty:
                self._project_dirty = False
                refresh_system()
            mutating = any(
                call.name
                in {
                    "write_file",
                    "edit_file",
                    "replace_in_file",
                    "apply_patch",
                    "scaffold_project",
                    "scaffold_forge_project",
                }
                for call in calls
            )
            if len(run.applied) > last_applied:
                no_change_rounds = 0
            elif mutating:
                # 只有“想改但没改成”才算空转；纯读文件交给只读连击和进度超时兜底，
                # 否则续做时读 10 个文件就会被误判成空转、提前结束本轮。
                no_change_rounds += 1
            else:
                no_change_rounds = 0
            last_applied = len(run.applied)
            if no_change_rounds >= 10:
                # 连续多轮工具调用都没有真正改动文件：模型在空转（重复失败的
                # edit、反复读同一个文件等）。结束本轮，让外层带着任务图重新
                # 发起一轮，比无限重试更有效。
                run.text = self._finalize_text(
                    "本轮连续多次工具调用没有产生新的文件改动，先结束这一轮。"
                    "工程里的代码已经保留，下一轮请直接根据编译错误或任务图继续。"
                )
                return run
            _compact_messages(messages)
            if self.kernel is not None and step % 12 == 0 and step < limit:
                # 每 12 轮同步一次任务图进度，防止长任务里模型忘记当前应做的功能。
                block = self.kernel.prompt_block()
                if block:
                    messages.append({"role": "user", "content": block})

        final_text = final_text or "已达到工具调用轮数上限。请继续发送消息，让我接着完成剩余部分。"
        run.text = self._finalize_text(final_text)
        return run

    def _finalize_text(self, final_text: str) -> str:
        if self.kernel is None or self.kernel.last_build_ok is None:
            return final_text
        try:
            gate = self.kernel.finalize(build_ok=self.kernel.last_build_ok)
        except Exception as exc:  # noqa: BLE001 - 验收输出失败不能吞掉主结果
            return final_text + f"\n\n（验收自检失败：{exc}）"
        return (final_text + "\n\n" + gate).strip() if gate else final_text

    def _stage_file(self, item: PendingFile, run: AgentRun) -> bool:
        if self.project is None:
            return False
        try:
            target = project_mod.safe_path(self.project.root, item.path)
        except project_mod.ProjectError as exc:
            self.status.emit(f"跳过不安全路径 {item.path}：{exc}")
            return False
        relative = target.relative_to(self.project.root.resolve()).as_posix()
        if self.kernel is not None:
            feature_id = self.kernel.record_edit(relative)
            if feature_id and not item.reason:
                item.reason = f"feature:{feature_id}"
        staged = PendingFile(path=relative, content=item.content, reason=item.reason)
        if self.config.auto_apply:
            try:
                project_mod.write_text(self.project.root, relative, item.content)
            except Exception as exc:
                self.status.emit(f"自动写入失败 {relative}：{exc}，已放入待写入列表")
            else:
                self.applied.append(relative)
                run.applied = list(self.applied)
                self._progress_at = time.monotonic()
                self._readonly_streak = 0
                self.status.emit(f"已自动写入 {relative}")
                # 自动写入也要在面板里留记录，否则用户只会看到“0 个”，无法确认写过什么。
                staged.applied = True
                self._add_pending(staged)
                run.pending = list(self.pending)
                return True
        self._add_pending(staged)
        run.pending = list(self.pending)
        return True

    def _build_log(self, text: str) -> None:
        line = (text or "").strip()
        if line:
            self.status.emit(line[-160:])

    def _should_auto_build(self, run: AgentRun, applied_mark: int) -> bool:
        if not self.config.auto_build or not self.config.auto_apply:
            return False
        if self.project is None or len(run.applied) <= applied_mark:
            return False
        if not _needs_build(run.applied[applied_mark:]):
            return False
        root = self.project.root
        return (root / "build.gradle").is_file() or (root / "settings.gradle").is_file()

    def _auto_build(self) -> tuple[bool, str]:
        self.status.emit("文件已写入，正在自动编译…")
        self._progress_at = time.monotonic()
        if self.kernel is not None:
            self.kernel.before_build("auto-build")
        try:
            result = _run_gradle(self.project.root, "build", 900, self._cancel, self._build_log)
        except Exception as exc:  # noqa: BLE001 - 自动编译失败不应吞掉本轮结果
            return False, f"自动编译没有开始：{exc}"
        report = _build_report(result, root=self.project.root)
        self._save_build_report(report)
        if self.kernel is not None:
            verdict = self.kernel.record_build(result.ok, result.output)
            if verdict.get("action") == "stop":
                report = (
                    report
                    + "\n\n"
                    + str(verdict.get("advice") or "已停止自动修复，建议人工介入。")
                )
        if result.ok:
            self._emit_artifact(result)
        return result.ok, report

    def _quick_build(self) -> tuple[bool, str]:
        """轮次收尾用的轻量编译：只跑 compileJava，保证下一轮有最新错误清单。"""
        if self.project is None:
            return False, ""
        self.status.emit("轮次结束前刷新编译结果…")
        self._progress_at = time.monotonic()
        try:
            result = _run_gradle(
                self.project.root, "compileJava", 600, self._cancel, self._build_log
            )
        except Exception as exc:  # noqa: BLE001 - 收尾编译失败不阻断本轮
            return False, f"收尾编译没有开始：{exc}"
        report = _build_report(result, root=self.project.root)
        self._save_build_report(report)
        return result.ok, report

    def _emit_artifact(self, result: "BuildOutcome") -> None:
        jars = [{"name": name, "size": size} for name, size in result.jars]
        libs = self.project.root / "build" / "libs" if self.project else None
        for item in jars:
            item["path"] = str(libs / item["name"]) if libs else ""
        payload = {
            "jar": jars[0]["name"] if jars else "",
            "jars": jars,
            "ok": result.ok,
            "summary": (
                f"编译通过，{len(jars)} 个产物" if result.ok else "编译失败"
            ),
        }
        self.artifact_changed.emit(payload)

    # ------------------------------------------------------------------
    def _scaffold(self, args: dict[str, Any]) -> str:
        mod_name = str(args.get("name") or args.get("mod_name") or "").strip()
        mod_id = str(args.get("mod_id") or "").strip()
        if not mod_id:
            mod_id = re.sub(r"[^a-z0-9_]", "_", (mod_name or "").lower()).strip("_")
        if not mod_id:
            return "scaffold_project 需要 mod_id 或 name。"
        try:
            mod_id = project_mod.validate_mod_id(mod_id)
        except project_mod.ProjectError as exc:
            return f"Mod ID 不合法：{exc}"
        mod_name = mod_name or mod_id
        package = str(args.get("package") or "").strip()
        loader = str(
            args.get("loader")
            or (getattr(self.project.meta, "loader", "") if self.project else "")
            or "Forge"
        ).strip()
        canonical = {
            "forge": "Forge",
            "fabric": "Fabric",
            "neoforge": "NeoForge",
            "neo": "NeoForge",
            "architectury": "Architectury",
        }
        loader = canonical.get(loader.lower(), loader)
        mc_version = str(
            args.get("mc_version")
            or (getattr(self.project.meta, "mc_version", "") if self.project else "")
            or mcdev.templates.MC_VERSION
        ).strip()
        forge_version = str(
            args.get("forge_version") or mcdev.templates.FORGE_VERSION
        ).strip()
        mod_version = str(args.get("version") or "1.0.0").strip()
        overwrite = bool(args.get("overwrite"))

        raw_dest = str(args.get("dest") or args.get("path") or "").strip()
        if raw_dest:
            target = Path(raw_dest).expanduser()
        elif self.project is not None:
            target = self.project.root
        else:
            base = Path(self.config.project_root or Path.home()).expanduser()
            target = base / mod_id
        exists = (target / "build.gradle").is_file() or (
            target / "settings.gradle"
        ).is_file() or (target / "src").is_dir()
        if exists and not overwrite:
            return (
                f"{target} 里已经有工程文件。确认要覆盖时请传 overwrite=true，"
                "或者换一个空目录。"
            )
        try:
            if loader == "Forge":
                mcdev.templates.create_forge_project(
                    target,
                    mod_id=mod_id,
                    mod_name=mod_name,
                    package=package,
                    mc_version=mc_version,
                    forge_version=forge_version,
                    mod_version=mod_version,
                )
            else:
                mcdev.templates.create_scaffold(
                    loader,
                    target,
                    mod_id=mod_id,
                    mod_name=mod_name,
                    package=package,
                    mc_version=mc_version,
                    mod_version=mod_version,
                )
            new_project = project_mod.load_project(target)
        except Exception as exc:  # noqa: BLE001 - 生成失败要直接说明
            return f"{loader} 工程创建失败：{exc}"
        new_project.meta.name = mod_name
        new_project.meta.mod_id = mod_id
        new_project.meta.mc_version = mc_version
        new_project.meta.loader = loader
        new_project.meta.runtime = "双端"
        try:
            new_project.save()
            project_mod.remember_project(new_project)
        except Exception:  # noqa: BLE001 - 元数据保存失败不影响工程
            pass
        self.project = new_project
        self._bind_kernel()
        self._project_dirty = True
        self._progress_at = time.monotonic()
        self.project_created.emit(str(target))
        package_name = package or f"com.example.{mod_id}"
        probe_hint = (
            "该版本/加载器支持游戏内探针，可以用 mcdev_prepare + mcdev_scenario 做断言验证。"
            if loader == "Forge" and mc_version == mcdev.templates.MC_VERSION
            else "该加载器/版本不使用游戏内探针：先 build_project，再用 mcdev_prepare 启动开发端，并查看运行日志 / GameTest。"
        )
        # Fabric / NeoForge 的公开类名是 <Mod>Mod，文件名必须跟着改；
        # Forge 1.20.1 的入口类本身就叫 ModMain。
        entry_file = "ModMain.java"
        if loader != "Forge":
            entry_file = (
                "".join(part.capitalize() for part in mod_id.split("_")) + "Mod.java"
            )
        return (
            f"{loader} 工程已创建。\n"
            f"- 目录：{target}\n"
            f"- Mod ID：{mod_id}\n"
            f"- 包名：{package_name}\n"
            f"- Minecraft：{mc_version} / {loader}\n"
            f"- 入口类：src/main/java/{package_name.replace('.', '/')}/{entry_file}\n\n"
            f"下一步：先改代码，再验证。{probe_hint}"
        )

    # ------------------------------------------------------------------
    def _studio_plan(self, args: dict[str, Any]) -> str:
        idea = str(args.get("idea") or args.get("prompt") or "").strip()
        loader = str(
            args.get("loader")
            or (self.project.meta.loader if self.project else "")
            or "Forge"
        ).strip()
        mc = str(
            args.get("mc")
            or args.get("mc_version")
            or (self.project.meta.mc_version if self.project else "")
            or "1.20.1"
        ).strip()
        try:
            spec = studio_bridge.plan(
                idea,
                loader,
                mc,
                name=str(args.get("name") or "").strip(),
                modid=str(args.get("modid") or args.get("mod_id") or "").strip(),
                package=str(args.get("package") or "").strip(),
                scope=str(args.get("scope") or "medium").strip(),
                author=str(args.get("author") or "").strip(),
            )
        except studio_bridge.StudioError as exc:
            return f"规格生成失败：{exc}"
        markdown = studio_bridge.spec_markdown(spec)
        prefix = ""
        if self.project is not None:
            try:
                project_mod.write_text(
                    self.project.root,
                    "mod-spec.json",
                    json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
                )
                project_mod.write_text(self.project.root, "GDD.md", markdown)
                self.project.meta.notes = (
                    f"规格驱动：{len(spec.get('content', []))} 个内容节点，主题 "
                    + "、".join(spec.get("themes", []))
                )
                self.project.save()
                prefix = (
                    f"规格已写入 {self.project.root}\\mod-spec.json 和 GDD.md。"
                    "接下来可以用 studio_build 或 build_project 编译。\n\n"
                )
            except Exception as exc:  # noqa: BLE001 - 写规格失败不应阻止返回规格内容
                prefix = f"规格已生成，但写入工程失败：{exc}\n\n"
        return (prefix + markdown)[:MAX_TOOL_RESULT]

    def _studio_generate(self, args: dict[str, Any]) -> str:
        idea = str(args.get("idea") or args.get("prompt") or "").strip()
        loader = str(
            args.get("loader")
            or (self.project.meta.loader if self.project else "")
            or "Forge"
        ).strip()
        mc = str(
            args.get("mc")
            or args.get("mc_version")
            or (self.project.meta.mc_version if self.project else "")
            or "1.20.1"
        ).strip()
        raw_dest = str(args.get("dest") or args.get("path") or "").strip()
        if raw_dest:
            base = Path(raw_dest).expanduser()
        elif self.project is not None:
            base = self.project.root.parent
        else:
            base = Path(self.config.project_root or paths.default_project_root()).expanduser()
        try:
            result = studio_bridge.generate(
                idea=idea,
                loader=loader,
                mc=mc,
                dest_root=base,
                name=str(args.get("name") or "").strip(),
                modid=str(args.get("modid") or args.get("mod_id") or "").strip(),
                package=str(args.get("package") or "").strip(),
                scope=str(args.get("scope") or "medium").strip(),
                author=str(args.get("author") or "").strip(),
                force=bool(args.get("overwrite")),
                use_mdk=bool(args.get("use_mdk", True)),
                audit_after=bool(args.get("audit", True)),
                build_after=bool(args.get("build")),
                verify_after=bool(args.get("verify")),
                package_after=bool(args.get("package")),
                progress=self._build_log,
            )
        except Exception as exc:  # noqa: BLE001 - 生成失败要把原因带回对话
            return f"规格驱动生成失败：{exc}"
        target = Path(result["project"])
        self._progress_at = time.monotonic()
        try:
            new_project = project_mod.load_project(target)
            new_project.save()
        except Exception:  # noqa: BLE001 - 登记失败不影响工程本身
            new_project = None
        if new_project is not None:
            self.project = new_project
            self.project_created.emit(str(target))
        summary = studio_bridge.summarize(result)
        return (
            f"{summary}\n\n"
            f"工程目录：{target}\n"
            "规格文件：mod-spec.json，设计文档：GDD.md。\n"
            "下一步：读 GDD.md 和关键注册类，按用户需求用 write_file / edit_file 继续细化；"
            "改动后用 studio_build 或 build_project 编译，再按加载器选择 studio_verify / mcdev_prepare 验证。"
        )[:MAX_TOOL_RESULT]

    # ------------------------------------------------------------------
    def _execute(self, call: ToolCall, run: AgentRun) -> str:
        name = call.name.strip().lower()
        args = call.args
        allowed_without_project = {
            "read_skill",
            "texture_template",
            "remember",
            "recall",
            "fetch_url",
            "search_knowledge",
            "read_knowledge",
            "search_code",
            "github_repo",
            "github_tree",
            "github_file",
            "scaffold_project",
            "scaffold_forge_project",
            "forge_scaffold",
            "studio_plan",
            "studio_generate",
            "mcdev_prepare",
            "mcdev_stop",
            "mcdev_launch_status",
            "mcdev_status",
            "mcdev_command",
            "mcdev_ui",
            "mcdev_click",
            "mcdev_screenshot",
            "mcdev_run_dump",
            "mcdev_use",
            "mcdev_scenario",
            "game_observe",
            "game_setup",
            "game_new_world",
            "game_language",
            "game_control",
            "game_test",
            *CENTER_TOOLS,
        }
        if self.project is None and name not in allowed_without_project:
            return "当前没有打开工程，无法执行文件操作。请先在界面创建或打开工程。"
        failed = False
        try:
            if name == "list_files":
                root = self.project.root
                result = _list_files(
                    root,
                    str(args.get("path") or "."),
                    _arg_int(args, "depth", 2),
                    _arg_int(args, "offset", 0),
                    _arg_int(args, "limit", 300),
                )
                if result.get("error"):
                    return result["error"]
                return json.dumps(result, ensure_ascii=False)
            if name == "read_file":
                relative = str(args.get("path") or "").strip()
                try:
                    offset = max(0, int(args.get("offset") or 0))
                except (TypeError, ValueError):
                    offset = 0
                try:
                    window = int(args.get("max_chars") or 14000)
                except (TypeError, ValueError):
                    window = 14000
                window = max(1000, min(MAX_TOOL_RESULT, window))
                target = project_mod.safe_path(self.project.root, relative)
                current_hash = self._file_cache.hash(target)
                since = str(args.get("since_hash") or "").strip()
                if since and since == current_hash:
                    return f"文件未变化：{relative}（hash={current_hash}）"
                text = project_mod.read_text(
                    self.project.root,
                    relative,
                    max_chars=MAX_TOOL_RESULT + offset,
                )
                if offset:
                    if offset >= len(text):
                        return f"offset={offset} 超过可读长度（{len(text)} 字符）。"
                    text = text[offset:]
                piece = text[:window]
                self._read_offsets[relative] = offset + len(piece)
                end = offset + len(piece)
                total = len(text) + offset
                hint = ""
                if end < total:
                    hint = (
                        f"\n\n（本次 {offset}～{end}/{total} 字符；继续读请带 offset={end}，"
                        f"文件未改时 since_hash={current_hash} 会直接返回“未变化”。）"
                    )
                return (
                    f"[read_file] path={relative} hash={current_hash} "
                    f"range={offset}-{end}/{total}\n{piece}{hint}"
                )[:MAX_TOOL_RESULT]
            if name == "search_text":
                hits = project_mod.search_text(
                    self.project.root,
                    str(args.get("query") or ""),
                    max_results=_arg_int(args, "limit", 40),
                )
                return "\n".join(hits) if hits else "没有找到匹配内容。"
            if name == "write_file":
                item = PendingFile(
                    path=str(args.get("path") or ""),
                    content=str(args.get("content") or ""),
                    reason="tool",
                )
                if not item.path:
                    return "write_file 缺少 path。"
                if not item.content.strip():
                    return "write_file 的 content 为空，已拒绝。"
                # 同名同内容重复写入是模型常见的空转：告诉它“没变化”，
                # 避免它把同一次写入当成还没完成，反复重写同一文件。
                target = None
                try:
                    target = project_mod.safe_path(self.project.root, item.path)
                except Exception:  # noqa: BLE001 - 路径非法时交给 _stage_file 报错
                    target = None
                if target is not None and not target.exists():
                    # 修不好就新建 Foo2.java / Foo3.java 是模型的另一种逃跑方式，
                    # 只会让同一个类出现多个副本、错误更多。直接拒绝并让它改原文件。
                    match = re.match(r"(.+?)\d+\.java$", target.name)
                    if match and target.with_name(match.group(1) + ".java").exists():
                        return (
                            f"不要新建副本文件 {target.name}：原文件 "
                            f"{match.group(1)}.java 已经存在。"
                            "请直接编辑或整文件重写原文件，不要用数字后缀另起一个类。"
                        )
                try:
                    if target is not None and target.is_file():
                        current = target.read_text(encoding="utf-8", errors="replace")
                        if current.replace("\r\n", "\n") == item.content.replace(
                            "\r\n", "\n"
                        ):
                            return (
                                f"内容未变化：{item.path}（{len(item.content)} 字符）。"
                                "该文件已经是这个内容，不要重复写入；"
                                "请继续下一个文件、执行编译，或给出结论。"
                            )
                except Exception:  # noqa: BLE001 - 比较失败时照常写入
                    pass
                if not self._stage_file(item, run):
                    return f"写入失败：路径不合法或没有打开工程（{item.path}）。"
                try:
                    relative = (
                        project_mod.safe_path(self.project.root, item.path)
                        .relative_to(self.project.root.resolve())
                        .as_posix()
                    )
                except Exception:
                    relative = item.path.replace("\\", "/")
                written = any(
                    entry.applied for entry in self.pending if entry.path == relative
                )
                state = "已自动写入" if written else "已加入待写入列表"
                return f"{state}：{item.path}（{len(item.content)} 字符）"
            if name == "pixel_art":
                relative = str(args.get("path") or "").strip()
                if not relative.lower().endswith(".png"):
                    return "pixel_art 只支持 .png 路径。"
                palette = args.get("palette")
                rows = args.get("rows")
                if not isinstance(palette, dict) or not isinstance(rows, list):
                    return "pixel_art 需要 palette 对象和 rows 数组。"
                scale = args.get("scale")
                outline = args.get("outline")
                scale_int = _arg_int(args, "scale", 1)
                if scale is not None and not 1 <= scale_int <= 32:
                    return "pixel_art 的 scale 必须在 1-32 之间。"
                try:
                    if scale is None and outline is None:
                        data, width, height = _encode_pixel_png(palette, rows)
                    else:
                        data, width, height = arttools.pixel_png(
                            palette,
                            [str(row) for row in rows],
                            scale=scale_int,
                            outline=str(outline) if outline else None,
                        )
                except ValueError as exc:
                    return f"pixel_art 参数错误：{exc}"
                try:
                    target = project_mod.write_bytes(self.project.root, relative, data)
                except Exception as exc:  # noqa: BLE001 - 路径或写盘失败
                    return f"写入失败：{exc}"
                try:
                    relative = target.relative_to(self.project.root.resolve()).as_posix()
                except ValueError:
                    relative = relative.replace("\\", "/")
                staged = PendingFile(
                    path=relative,
                    content=f"[PNG {width}x{height}，{len(data)} 字节]",
                    reason="pixel_art",
                    applied=True,
                )
                self._add_pending(staged)
                self.applied.append(relative)
                run.pending = list(self.pending)
                run.applied = list(self.applied)
                self.status.emit(f"已生成纹理 {relative}")
                return f"已生成纹理：{relative}（{width}x{height}，{len(data)} 字节）"
            if name == "texture_template":
                want = str(args.get("name") or "").strip()
                if args.get("list") or not want:
                    names = ", ".join(arttools.TEMPLATE_NAMES)
                    return (
                        f"可用 16x16 模板：{names}。\n"
                        "用法：texture_template name=sword 返回字符点阵（keys: rows/palette/note），"
                        "配合 pixel_art 换色后生成 PNG。"
                    )
                try:
                    template = arttools.template_rows(want)
                except ValueError as exc:
                    return f"texture_template 参数错误：{exc}"
                return json.dumps(template, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
            if name == "model_json":
                try:
                    text, warnings = arttools.model_json(
                        kind=str(args.get("kind") or "").strip().lower(),
                        parent=str(args.get("parent") or ""),
                        textures=args.get("textures"),
                        elements=args.get("elements"),
                        display=args.get("display"),
                    )
                except ValueError as exc:
                    return f"model_json 参数错误：{exc}"
                prefix = "\n".join(f"警告：{item}" for item in warnings)
                return (prefix + "\n" if prefix else "") + text[:MAX_TOOL_RESULT]
            if name == "blockstate_json":
                try:
                    text, warnings = arttools.blockstate_json(
                        variants=args.get("variants"), multipart=args.get("multipart")
                    )
                except ValueError as exc:
                    return f"blockstate_json 参数错误：{exc}"
                prefix = "\n".join(f"警告：{item}" for item in warnings)
                return (prefix + "\n" if prefix else "") + text[:MAX_TOOL_RESULT]
            if name == "lang_json":
                entries = args.get("entries")
                if not isinstance(entries, dict) or not entries:
                    return "lang_json 需要 entries 对象（翻译键到文本）。"
                try:
                    text = arttools.lang_json(entries)
                except ValueError as exc:
                    return f"lang_json 参数错误：{exc}"
                return text[:MAX_TOOL_RESULT]
            if name == "recipe_json":
                try:
                    text, warnings = arttools.recipe_json(
                        kind=str(args.get("kind") or "").strip().lower(),
                        result=str(args.get("result") or ""),
                        count=_arg_int(args, "count", 1),
                        pattern=args.get("pattern"),
                        key=args.get("key"),
                        ingredients=args.get("ingredients"),
                        ingredient=args.get("ingredient"),
                        experience=args.get("experience"),
                        cookingtime=args.get("cookingtime"),
                        template=args.get("template"),
                        base=args.get("base"),
                        addition=args.get("addition"),
                    )
                except ValueError as exc:
                    return f"recipe_json 参数错误：{exc}"
                prefix = "\n".join(f"警告：{item}" for item in warnings)
                return (prefix + "\n" if prefix else "") + text[:MAX_TOOL_RESULT]
            if name == "loot_table_json":
                try:
                    text = arttools.loot_table_json(
                        block=str(args.get("block") or ""),
                        item=str(args.get("item") or ""),
                        silk_touch_self=bool(args.get("silk_touch_self")),
                        extra=args.get("extra"),
                    )
                except ValueError as exc:
                    return f"loot_table_json 参数错误：{exc}"
                return text[:MAX_TOOL_RESULT]
            if name == "validate_resources":
                root_arg = str(args.get("root") or "").strip()
                if root_arg:
                    target = project_mod.safe_path(self.project.root, root_arg)
                else:
                    target = self.project.root / "src" / "main" / "resources"
                if not target.is_dir():
                    return f"资源目录不存在：{target}"
                report = arttools.validate_resources(target)
                self._save_validate_report(report)
                return report[:MAX_TOOL_RESULT]
            if name in {"edit_file", "replace_in_file", "apply_patch"}:
                relative = str(args.get("path") or "").strip()
                old = str(args.get("old") or args.get("find") or "")
                if args.get("new") is not None:
                    new = str(args.get("new"))
                else:
                    new = str(args.get("replacement") or "")
                symbol = str(args.get("symbol") or args.get("method") or "").strip()
                member = str(args.get("member") or "").strip()
                mode = str(args.get("mode") or "auto").strip().lower()
                if not relative:
                    return "edit_file 缺少 path。"
                if not symbol and not member and old == new:
                    return (
                        "old 和 new 完全相同，文件不会有任何变化。"
                        "当前代码已经是这个样子，请不要重复替换；"
                        "如果这里不是编译错误的原因，请检查真正的报错行，或直接继续下一步。"
                    )
                try:
                    current = project_mod.read_text(
                        self.project.root, relative, max_chars=2 * 1024 * 1024
                    )
                except Exception as exc:  # noqa: BLE001 - 文件不存在或太大
                    return f"读取失败：{exc}"
                if "…（已截断" in current:
                    return "文件太大，edit_file 需要完整内容，请改用 write_file 重写整份文件。"
                updated = ""
                detail = ""
                if symbol or member:
                    class_name, method = _split_symbol(symbol)
                    if not class_name:
                        match = re.search(r"\b(?:class|enum|record|interface)\s+(\w+)", current)
                        class_name = match.group(1) if match else ""
                    if member or mode == "insert":
                        code = member or new
                        if not code.strip():
                            return "edit_file symbol 插入模式需要 member 或 new。"
                        updated, result = insert_member_text(
                            current,
                            class_name=class_name,
                            code=code,
                            source=relative,
                        )
                        detail = f"插入成员到 {class_name or '类'}"
                    elif method:
                        if not new.strip():
                            return "edit_file 需要 new（新的方法体）。"
                        updated, result = replace_body_text(
                            current,
                            class_name=class_name,
                            method=method,
                            body=new,
                            source=relative,
                        )
                        detail = f"替换方法体 {class_name}#{method}"
                    else:
                        result = None
                    if updated is None:
                        message = result.message if result is not None else "symbol 格式应为 Class 或 Class.method"
                        return (
                            f"AST 定位失败：{relative}（{message}）。"
                            "可以改用 old/new 精确替换，或先用 read_file 确认类名/方法名。"
                        )
                else:
                    if not old:
                        return "edit_file 缺少 old（要替换的原文）。"
                    if old not in current:
                        # 模型常把中文注释/缩进抄错，导致 old 永远匹配不上，
                        # 然后同一段 old 反复重试。直接把文件里的真实片段贴回去，
                        # 让它能复制正确的原文。
                        excerpt = ""
                        lines = current.splitlines()
                        anchor = next(
                            (line.strip() for line in old.splitlines() if line.strip()),
                            "",
                        )
                        if anchor:
                            for index, line in enumerate(lines):
                                if anchor in line:
                                    start = max(0, index - 3)
                                    end = min(len(lines), index + 18)
                                    excerpt = "\n".join(lines[start:end])
                                    break
                        hint = (
                            "\n\n文件里对应位置实际是（请直接复制，注意中文注释和缩进）：\n"
                            + excerpt
                            if excerpt
                            else ""
                        )
                        return (
                            f"没有在 {relative} 里找到要替换的原文。"
                            "请先用 read_file 确认内容，old 必须和文件里的字符完全一致；"
                            "如果多次匹配不上，直接用 write_file 重写整个文件。"
                            + hint
                        )
                    key = relative.replace("\\", "/")
                    recent = getattr(self, "_recent_edits", None) or []
                    if old != new and any(
                        path == key and prev_old == new and prev_new == old
                        for path, prev_old, prev_new in recent
                    ):
                        return (
                            f"检测到 edit_file 来回震荡：{relative} 里这段内容刚刚从 A 改成 B，"
                            "现在又要从 B 改回 A，文件会被反复改回去。\n"
                            "先停止替换并调用 build_project 编译，确认哪种写法真的能通过；"
                            "也可以用 dev_docs / api_lookup 查 1.20.1 的正确符号路径。"
                            "如果两种写法都会报同一个错，说明真正的问题在别处，不要继续切换。"
                        )
                    updated = current.replace(old, new, 1)
                    detail = "替换 1 处"
                    recent.append((key, old, new))
                    del recent[:-20]
                    self._recent_edits = recent
                item = PendingFile(path=relative, content=updated, reason="edit")
                if not self._stage_file(item, run):
                    return f"修改失败：路径不合法（{relative}）。"
                self._file_cache.invalidate(project_mod.safe_path(self.project.root, relative))
                written = any(
                    entry.applied
                    for entry in self.pending
                    if entry.path == item.path.replace("\\", "/")
                )
                state = "已自动写入" if written else "已加入待写入列表"
                delta = len(updated) - len(current)
                diff = diff_text(current, updated, relative)
                hunks = sum(1 for line in diff.splitlines() if line.startswith("@@"))
                return (
                    f"{state}：{relative}（{detail}，{delta:+d} 字符"
                    + (f"，{hunks} 个差异块" if hunks else "")
                    + "）"
                )
            if name in {"scaffold_project", "scaffold_forge_project", "forge_scaffold"}:
                return self._scaffold(args)
            if name == "read_skill":
                skill = self.skill_store.get(str(args.get("id") or ""))
                if skill is None:
                    return "技能不存在。可用技能见系统提示里的技能目录。"
                return f"# {skill.name}\n\n{skill.body}"[:MAX_TOOL_RESULT]
            if name == "remember":
                content = str(args.get("content") or "").strip()
                if not content:
                    return "记忆内容为空。"
                project_key = str(self.project.root) if self.project else ""
                memory_id = self.memory.add(
                    content,
                    project=project_key,
                    kind=str(args.get("kind") or "note"),
                    tags=str(args.get("tags") or ""),
                )
                return f"已写入记忆库，编号 {memory_id}。"
            if name == "recall":
                project_key = str(self.project.root) if self.project else ""
                items = self.memory.recall(
                    project_key,
                    str(args.get("query") or ""),
                    limit=_arg_int(args, "limit", 8),
                )
                return memory_context(items) or "没有找到相关记忆。"
            if name == "search_knowledge":
                if self.knowledge is None:
                    return "知识库不可用。"
                project_key = str(self.project.root) if self.project else ""
                hits = self.knowledge.search(
                    str(args.get("query") or ""),
                    project=project_key,
                    limit=_arg_int(args, "limit", 8),
                    budget=_arg_int(args, "budget", MAX_TOOL_RESULT),
                )
                if not hits:
                    overview = self.knowledge.overview_hits(
                        project=project_key, limit=2
                    )
                    if overview:
                        return (
                            f"没有找到完全匹配「{args.get('query') or ''}」的正文。"
                            "知识库里有这些模组源码资料，先看速览和类索引，"
                            "再用 read_knowledge 按类名或方法名查具体实现：\n\n"
                            + knowledge_context(overview)
                        )[:MAX_TOOL_RESULT]
                    return "知识库里没有找到相关内容。可以换个关键词，或让用户先在「知识库」页导入资料。"
                return knowledge_context(hits)[:MAX_TOOL_RESULT]
            if name == "read_knowledge":
                if self.knowledge is None:
                    return "知识库不可用。"
                project_key = str(self.project.root) if self.project else ""
                document = None
                if args.get("id"):
                    try:
                        document = self.knowledge.get_document(int(args.get("id")))
                    except (TypeError, ValueError):
                        document = None
                if document is None and args.get("name"):
                    document = self.knowledge.find_document(
                        str(args.get("name") or ""), project=project_key
                    )
                if document is None:
                    return "没有找到对应的知识库文件。请先用 search_knowledge 查编号。"
                header = (
                    f"# {document.get('name')}（编号 {document.get('id')}，"
                    f"{document.get('chars')} 字符）\n\n"
                )
                body = str(document.get("text") or "")
                if "资料上限" in body or "内容超过上限" in body:
                    header += (
                        "注意：这份资料是旧版本导入的，正文被截断过。"
                        "如果找不到需要的类，请让用户在「知识库」页重新导入同一个 JAR。\n\n"
                    )
                target = str(
                    args.get("file") or args.get("source") or args.get("class") or ""
                ).strip()
                if target:
                    try:
                        start_at = max(0, int(args.get("offset") or 0))
                    except (TypeError, ValueError):
                        start_at = 0
                    item = self.knowledge.read_source(
                        int(document["id"]), target, offset=start_at, limit=0
                    )
                    if item is None:
                        available = self.knowledge.source_entries(
                            int(document["id"]), limit=400
                        )
                        listing = "、".join(available) if available else "（没有源码条目）"
                        return (
                            header
                            + f"没有找到「{target}」。可用的源码文件（共 {len(available)} 个）：\n"
                            f"{listing}\n"
                            "可以只写类名，例如 file=\"BodyPossession.java\" 或 file=\"BodyPossession\"。"
                        )[:MAX_TOOL_RESULT]
                    piece = str(item.get("text") or "")
                    room = max(2000, MAX_TOOL_RESULT - len(header) - 900)
                    next_offset = None
                    if len(piece) > room:
                        piece = piece[:room]
                        next_offset = int(item["offset"]) + len(piece)
                    tail = (
                        f"\n\n（{item['kind']}：{item['name']}，本次第 {item['offset']}～"
                        f"{item['offset'] + len(piece)} 字符，共 {item['total']} 字符。"
                        + (
                            "已到结尾。）"
                            if next_offset is None
                            else f"继续读请保持 file 不变，offset={next_offset}。）"
                        )
                    )
                    return (
                        header
                        + f"## {item['kind']}：{item['name']}\n\n"
                        + piece
                        + tail
                    )[:MAX_TOOL_RESULT]
                query = str(args.get("query") or "").strip()
                if query:
                    matches = self.knowledge.document_hits(
                        int(document["id"]),
                        query,
                        limit=8,
                        budget=max(10000, MAX_TOOL_RESULT - len(header) - 700),
                    )
                    if not matches:
                        return (
                            header
                            + f"没有匹配「{query}」的片段。JAR 资料的类名是英文，"
                            "可以先用 offset=0 读开头的「模组速览 / 类索引」，"
                            "再用类名或方法名作为 query，"
                            "或者直接用 file=\"类名.java\" 读整个文件。"
                        )
                    joined = "\n\n".join(
                        f"[条目 {index + 1}]\n{item['text']}"
                        for index, item in enumerate(matches)
                    )
                    return (header + joined)[:MAX_TOOL_RESULT]
                try:
                    offset = max(0, int(args.get("offset") or 0))
                except (TypeError, ValueError):
                    offset = 0
                if offset >= len(body):
                    return header + f"起始位置 {offset} 超过正文长度。"
                piece = body[offset : offset + MAX_TOOL_RESULT - len(header) - 80]
                tail = ""
                if offset + len(piece) < len(body):
                    tail = (
                        f"\n\n（从第 {offset} 字符开始，本次到第 {offset + len(piece)} 字符；"
                        f"全文 {len(body)} 字符。继续读请把 offset 设为 {offset + len(piece)}。）"
                    )
                return header + piece + tail
            if name == "search_code":
                query = str(args.get("query") or "").strip()
                if not query:
                    return "search_code 缺少 query。"
                try:
                    limit = int(args.get("limit") or 10)
                except (TypeError, ValueError):
                    limit = 10
                return research.search_code(
                    query,
                    kind=str(args.get("kind") or "auto"),
                    limit=max(3, min(limit, 20)),
                )[:MAX_TOOL_RESULT]
            if name in {"github_repo", "github_tree", "github_file"}:
                repo = str(args.get("repo") or args.get("url") or "").strip()
                if not repo:
                    return f"{name} 缺少 repo，例如 yan-mc/minecraft-mod-dev。"
                if name == "github_repo":
                    info = research.repo_info(repo)
                    return json.dumps(info, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
                if name == "github_tree":
                    rows = research.repo_tree(
                        repo,
                        ref=str(args.get("ref") or ""),
                        path=str(args.get("path") or ""),
                        limit=_arg_int(args, "limit", 260),
                    )
                    return "\n".join(rows) if rows else "仓库目录为空。"[:MAX_TOOL_RESULT]
                path = str(args.get("path") or "").strip()
                if not path:
                    return "github_file 缺少 path。"
                return research.repo_file(
                    repo, path, ref=str(args.get("ref") or "")
                )[:MAX_TOOL_RESULT]
            if name == "decompile_jar":
                if self.knowledge is None:
                    return "知识库不可用。"
                project_key = str(self.project.root) if self.project else ""
                document = None
                document_id = args.get("knowledge_id") or args.get("id")
                if document_id:
                    try:
                        document = self.knowledge.get_document(int(document_id))
                    except (TypeError, ValueError):
                        document = None
                if document is None and args.get("name"):
                    document = self.knowledge.find_document(
                        str(args.get("name") or ""), project=project_key
                    )
                if document is None:
                    return "没有找到对应的知识库 JAR。请先用 search_knowledge 查编号。"
                if str(document.get("kind") or "") != "jar":
                    return "这份知识库资料不是 JAR，无法反编译。"
                source = Path(str(document.get("source") or ""))
                if not source.is_file():
                    return f"找不到原始 JAR：{source}。请重新导入这个文件。"
                target = str(args.get("target") or "").replace("\\", "/").strip()
                if not target:
                    target = f"reference/decompiled/{source.stem}"
                if target == "src" or target.startswith("src/"):
                    return (
                        "不能把反编译结果直接写进 src/，会缺映射和依赖导致编译失败。"
                        "请用 reference/decompiled/ 作为参考目录。"
                    )
                files, notes = decompile_jar_sources(source)
                result = write_decompiled_tree(
                    self.project.root,
                    target,
                    files,
                    jar_name=source.name,
                    notes=notes,
                )
                return (
                    f"反编译完成：{result['written']} 个 Java 文件，"
                    f"写到 {result['target']}。\n"
                    f"索引：{result['index']}\n"
                    "这些文件不在编译路径里，只作为迁移参考；"
                    "需要迁移时按文件读取，再把改写后的完整文件写到 src/main/java。"
                )[:MAX_TOOL_RESULT]
            if name == "build_project":
                task = str(args.get("task") or "build").strip()
                blocked = _build_task_error(task)
                if blocked:
                    return blocked
                try:
                    limit = float(args.get("timeout") or 900)
                except (TypeError, ValueError):
                    limit = 900.0
                limit = min(max(limit, 60.0), 1800.0)
                if self.kernel is not None and task == "build":
                    self.kernel.before_build("tool-build")
                try:
                    result = _run_gradle(
                        self.project.root, task, limit, self._cancel, self._build_log
                    )
                except mcdev.gradle.GradleError as exc:
                    return f"编译没有开始：{exc}"
                self._progress_at = time.monotonic()
                report = _build_report(result, root=self.project.root)
                self._save_build_report(report)
                if self.kernel is not None and task == "build":
                    verdict = self.kernel.record_build(result.ok, result.output)
                    if verdict.get("action") == "stop":
                        report += "\n\n" + str(verdict.get("advice") or "")
                if task == "build":
                    self._emit_artifact(result)
                return report
            if name == "fetch_url":
                url = str(args.get("url") or "").strip()
                if not url:
                    return "fetch_url 缺少 url。"
                return connectors.fetch_url(
                    url, cancel=self._cancel
                )[:MAX_FETCH_RESULT]
            if name == "studio_plan":
                return self._studio_plan(args)
            if name == "studio_generate":
                return self._studio_generate(args)
            if name == "studio_audit":
                report = studio_bridge.audit_project(
                    self.project.root, progress=self._build_log
                )
                return studio_bridge.audit_mod.summarize(report)
            if name == "studio_build":
                try:
                    limit = int(float(args.get("timeout") or 1800))
                except (TypeError, ValueError):
                    limit = 1800
                report = studio_bridge.build_project(
                    self.project.root, timeout=min(max(limit, 120), 3600),
                    fix=bool(args.get("fix", True)), progress=self._build_log,
                )
                self._progress_at = time.monotonic()
                summary = studio_bridge.build_mod.summarize(report)
                errors = report.get("errors") or []
                if errors:
                    detail = "\n".join(
                        f"- {item.get('file', '')}:{item.get('line', '')} {item.get('message', '')}"
                        for item in errors[:20]
                    )
                    summary += "\n" + detail
                if self.kernel is not None:
                    self.kernel.before_build("studio-build")
                    self.kernel.record_build(bool(report.get("ok")), summary)
                return summary
            if name == "studio_verify":
                try:
                    limit = int(float(args.get("timeout") or 600))
                except (TypeError, ValueError):
                    limit = 600
                report = studio_bridge.verify_project(
                    self.project.root, timeout=min(max(limit, 120), 1800),
                    progress=self._build_log,
                )
                if self.kernel is not None and not report.get("skipped"):
                    self.kernel.record_runtime(bool(report.get("ok")))
                return studio_bridge.verify_mod.summarize(report)
            if name == "studio_package":
                report = studio_bridge.package_project(
                    self.project.root, progress=self._build_log
                )
                return studio_bridge.package_mod.summarize(report)
            if name == "design_studio":
                return self._execute_design(args)
            if name in CENTER_TOOLS:
                return self._execute_center(name, args)
            if name in KERNEL_TOOLS:
                return self._execute_kernel(name, args)
            if name.startswith("mcdev_") or name == "game_playtest":
                return self._execute_mcdev(name, args)
            if name.startswith("game_"):
                return self._execute_game(name, args)
            return (
                f"未知工具：{name}。可用工具：list_files、read_file、search_text、write_file、"
                "edit_file、pixel_art、design_studio、scaffold_project、build_project、"
                "studio_plan、studio_generate、studio_audit、studio_build、"
                "studio_verify、studio_package、"
                "read_skill、remember、recall、search_knowledge、read_knowledge、"
                "decompile_jar、fetch_url、search_code、github_repo、github_tree、github_file、"
                "mcdev_prepare、mcdev_stop、mcdev_launch_status、mcdev_status、"
                "mcdev_command、mcdev_ui、mcdev_click、mcdev_screenshot、mcdev_run_dump、"
                "mcdev_use、mcdev_install_probe、mcdev_scenario、mcdev_build、"
                "mcdev_gametest、mcdev_build_probe、mcdev_move、mcdev_look、mcdev_attack、"
                "mcdev_mine、mcdev_break、mcdev_use_item、mcdev_inventory、game_playtest、"
                "game_observe、game_setup、game_new_world、game_language、"
                "game_control、game_test、"
                "lint_project、symbol_query、mapping_translate、impact_analysis、"
                "log_parse、snapshot、snapshot_list、rollback、asset_lint、"
                "texture_gen、entity_texture、vision_review、blockbench、testgen、migration_plan、"
                "screenshot_collect、offline_cache。"
            )
        except Exception as exc:
            failed = True
            if self.cancelled:
                # 工具在取消后被网络层/子进程提前打断，不能当作普通失败继续跑。
                raise AgentError("已取消") from exc
            return f"工具执行失败：{exc}"
        finally:
            if not failed:
                if name in READ_ONLY_TOOLS:
                    # 只读也刷新进展，避免纯阅读阶段被 PROGRESS_TIMEOUT 误杀；
                    # 但累计连续只读次数，超过阈值时由 _loop 主动打断探索循环。
                    self._readonly_streak += 1
                    self._progress_at = time.monotonic()
                else:
                    self._readonly_streak = 0
                    self._progress_at = time.monotonic()

    # ------------------------------------------------------------------
    def _center_progress(self, percent: int, message: str) -> None:
        self._build_log(f"{message}（{int(percent)}%）")

    def _execute_center(self, name: str, args: dict[str, Any]) -> str:
        from . import apiindex, catalog as catalog_mod
        from . import launcher_bridge, modlibrary

        if name == "api_lookup":
            query = str(args.get("query") or "").strip()
            if not query:
                return "api_lookup 缺少 query。"
            meta = getattr(self.project, "meta", None)
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "neoforge")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            version = str(args.get("version") or "")
            kind = str(args.get("kind") or "auto").strip().lower()
            limit = max(1, min(_arg_int(args, "limit", 20), 100))
            index, jar, notes = apiindex.ensure(
                loader,
                mc,
                version,
                allow_download=bool(args.get("allow_download", True)),
                force=bool(args.get("refresh")),
                progress=self._center_progress,
            )
            if index is None:
                return "\n".join(dict.fromkeys(notes)) or "没有可用的 API 索引。"
            hits = apiindex.query(index, query, kind=kind, limit=limit)
            info = apiindex.stats(index)
            lines = [
                f"API 索引：{info.get('loader') or loader} / {Path(str(info.get('source') or jar)).name}",
                f"规模：类 {info['counts'].get('classes', 0)} / 方法 {info['counts'].get('methods', 0)} "
                f"/ 字段 {info['counts'].get('fields', 0)}",
                f"查询：{query}（{kind}，命中 {len(hits)} 条）",
                "",
                apiindex.render(hits, limit=limit),
            ]
            if notes:
                lines += ["", "注意："] + [f"- {note}" for note in dict.fromkeys(notes)]
            return "\n".join(lines)[:MAX_TOOL_RESULT]

        if name == "catalog_sync":
            catalog = catalog_mod.Catalog()
            sections = [str(item) for item in args.get("sections") or [] if str(item)]
            force = bool(args.get("force"))
            if not sections:
                report = catalog.refresh(self._center_progress, force=force)
                return (report.text() + "\n\n" + json.dumps(
                    catalog.summary(), ensure_ascii=False, indent=2
                ))[:MAX_TOOL_RESULT]
            mapping = {
                "mojang": catalog.sync_mojang,
                "fabric": catalog.sync_fabric,
                "quilt": catalog.sync_quilt,
                "forge": catalog.sync_forge,
                "neoforge": catalog.sync_neoforge,
                "toolchain": catalog.sync_toolchain,
                "changelogs": catalog.sync_changelogs,
            }
            lines = []
            for section in sections:
                step = mapping.get(section)
                if step is None:
                    lines.append(f"- {section}：未知分区")
                    continue
                entry = step(self._center_progress, force=force)
                lines.append(f"- {section}：{'成功' if entry.ok else '失败'} · {entry.message or entry.count}")
            return "\n".join(lines)[:MAX_TOOL_RESULT]
        if name == "catalog_query":
            catalog = catalog_mod.Catalog()
            action = str(args.get("action") or "summary").strip().lower()
            loader = str(args.get("loader") or "").strip().lower()
            mc = str(args.get("mc") or "").strip()
            version = str(args.get("version") or "").strip()
            limit = max(1, min(_arg_int(args, "limit", 40), 200))
            if action == "summary":
                return json.dumps(catalog.summary(), ensure_ascii=False, indent=2)
            if action == "minecraft":
                kind = str(args.get("kind") or "release")
                ids = catalog.minecraft_ids("all" if kind == "all" else kind)
                latest = (catalog.section("mojang").get("latest") or {}).get("release", "")
                return (
                    f"共 {len(ids)} 个版本，最新 {latest}：\n" + ", ".join(ids[:limit])
                )[:MAX_TOOL_RESULT]
            if action == "loaders":
                if not loader:
                    return "loaders 需要 loader=fabric/forge/neoforge/quilt。"
                items = catalog.loader_versions(loader)
                return (
                    f"{loader} 共 {len(items)} 个版本：\n" + ", ".join(items[:limit])
                )[:MAX_TOOL_RESULT]
            if action == "recommendations":
                if not mc:
                    return "recommendations 需要 mc 版本，例如 1.21.1。"
                return json.dumps(
                    catalog.recommendations(mc, loader), ensure_ascii=False, indent=2
                )[:MAX_TOOL_RESULT]
            if action in {"mdk", "javadoc"}:
                item = catalog.mdk(loader, mc, version) if action == "mdk" else catalog.javadoc(loader, mc, version)
                if not item:
                    return f"没有找到 {action}：loader={loader or '?'} mc={mc or '?'}。先 catalog_sync。"
                return json.dumps(item, ensure_ascii=False, indent=2)
            if action == "changelog":
                if not loader:
                    return "changelog 需要 loader。"
                entries = catalog.changelog(loader)[:5]
                if not entries:
                    return f"{loader} 还没有缓存的更新日志，先 catalog_sync sections=[\"changelogs\"]。"
                lines = []
                for item in entries:
                    lines.append(
                        f"## {item.get('name') or item.get('tag')} ({item.get('published') or ''})\n"
                        f"{str(item.get('body') or '')[:1800]}"
                    )
                return "\n\n".join(lines)[:MAX_TOOL_RESULT]
            if action == "version":
                if not mc:
                    return "version 需要 mc，例如 1.21.1。"
                data = catalog.version_info(mc)
                return json.dumps(data, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
            return "未知 action：" + action
        if name == "catalog_download":
            catalog = catalog_mod.Catalog()
            kind = str(args.get("kind") or "").lower()
            loader = str(args.get("loader") or "").lower()
            mc = str(args.get("mc") or "").strip()
            version = str(args.get("version") or "").strip()
            if kind == "mdk":
                item = catalog.mdk(loader, mc, version)
            elif kind == "javadoc":
                item = catalog.javadoc(loader, mc, version)
            else:
                return "kind 只能是 mdk 或 javadoc。"
            if not item:
                return "没有找到下载地址，请先 catalog_sync 或检查 loader/mc。"
            dest = catalog_mod.artifacts_dir() / ("mdk" if kind == "mdk" else "javadoc")
            path = catalog.download(item, dest_dir=dest, progress=self._center_progress)
            return f"已下载 {kind}：{path}"
        if name == "mod_search":
            query = str(args.get("query") or "").strip()
            if not query:
                return "mod_search 缺少 query。"
            items = modlibrary.search(
                query,
                loader=str(args.get("loader") or ""),
                mc=str(args.get("mc") or ""),
                limit=max(1, min(_arg_int(args, "limit", 10), 30)),
                offset=max(0, _arg_int(args, "offset", 0)),
            )
            if not items:
                return "没有找到匹配的模组。"
            lines = []
            for item in items:
                lines.append(
                    f"- {item.get('title')}（slug={item.get('slug') or item.get('id')}，"
                    f"下载 {item.get('downloads', 0):,}）\n  {item.get('description') or ''}"
                )
            return "\n".join(lines)[:MAX_TOOL_RESULT]
        if name in {"mod_download", "mod_decompile"}:
            project = str(args.get("project") or "").strip()
            if not project:
                return f"{name} 缺少 project（Modrinth slug 或项目 ID）。"
            loader = str(args.get("loader") or "")
            mc = str(args.get("mc") or "")
            version = str(args.get("version") or "")
            if name == "mod_download":
                chosen = modlibrary.pick_version(project, loader=loader, mc=mc)
                if not chosen:
                    return "没有找到匹配的版本。"
                path = modlibrary.download(chosen, progress=self._center_progress)
                return (
                    f"已下载：{path}\n"
                    "仅用于本地学习与排错，请遵守原模组许可证，不要重新分发。"
                )
            result = modlibrary.learn(
                project,
                loader=loader,
                mc=mc,
                version_id=version,
                progress=lambda percent, message: self._center_progress(percent, message),
            )
            return (
                f"反编译完成：{result.get('files')} 个文件\n"
                f"源码目录：{result.get('sources')}\n"
                f"索引：{result.get('index')}\n"
                "只读参考，不要把这些源码复制进当前工程。"
            )[:MAX_TOOL_RESULT]
        if name == "large_mod_plan":
            meta = getattr(self.project, "meta", None)
            idea = str(args.get("idea") or self.user_text or "").strip()
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "NeoForge")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            plan = planner.build_plan(
                idea,
                loader=loader,
                mc=mc,
                scope=str(args.get("scope") or "large"),
                modid=str(args.get("modid") or getattr(meta, "mod_id", "") or ""),
                package=str(args.get("package") or ""),
            )
            return planner.render(plan)[:MAX_TOOL_RESULT]
        if name == "version_facts":
            from . import modfacts

            meta = getattr(self.project, "meta", None)
            action = str(args.get("action") or "version").strip().lower()
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            if action == "matrix":
                return modfacts.dump(loader, mc)[:MAX_TOOL_RESULT]
            return modfacts.brief(loader, mc)[:MAX_TOOL_RESULT]
        if name == "arch_ref":
            from . import archref

            action = str(args.get("action") or "lookup").strip().lower()
            if action == "catalog":
                return archref.catalog()[:MAX_TOOL_RESULT]
            if action == "profile":
                return archref.explain(str(args.get("profile") or ""))[:MAX_TOOL_RESULT]
            query = str(args.get("query") or self.user_text or "").strip()
            limit = max(1, min(_arg_int(args, "limit", 2), 4))
            return archref.search(query, limit=limit)[:MAX_TOOL_RESULT]
        if name == "multiversion_plan":
            from . import multiversion

            meta = getattr(self.project, "meta", None)
            loaders = args.get("loaders")
            versions = args.get("mc_versions")
            loader_rows = [str(item) for item in loaders] if isinstance(loaders, list) else []
            version_rows = [str(item) for item in versions] if isinstance(versions, list) else []
            if not loader_rows:
                loader_rows = [str(getattr(meta, "loader", "") or "fabric")]
            if not version_rows:
                version_rows = [str(getattr(meta, "mc_version", "") or "1.21.1")]
            modid = str(args.get("modid") or getattr(meta, "mod_id", "") or "yourmod")
            return multiversion.plan(loader_rows, version_rows, modid=modid)[:MAX_TOOL_RESULT]
        if name == "dev_docs":
            meta = getattr(self.project, "meta", None)
            action = str(args.get("action") or "lookup").strip().lower()
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            version = str(args.get("version") or "")
            if action == "status":
                return json.dumps(devdocs.status(), ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
            if action == "official":
                pages = devdocs.official_pages(loader)
                return "\n".join(f"- [{title}]({url})" for title, url in pages)
            if action == "sources":
                path = devdocs.ensure_sources(
                    loader,
                    mc,
                    version,
                    progress=self._center_progress,
                    cancel=self._cancel,
                )
                if path is None:
                    return "没有找到可用 sources.jar；先 catalog_sync，或检查 loader/mc。"
                return f"源码构件已就绪：{path}"
            query = str(args.get("query") or "").strip()
            if not query:
                return "dev_docs lookup 需要 query。"
            result = devdocs.lookup(
                query,
                loader=loader,
                mc=mc,
                version=version,
                limit=max(1, min(_arg_int(args, "limit", 8), 20)),
                cancel=self._cancel,
            )
            return devdocs.render(result)
        if name == "resource_ensure":
            kind = str(args.get("kind") or "status").strip().lower()
            project_root = self.project.root if self.project is not None else None
            meta = getattr(self.project, "meta", None)
            result = resources.ensure(
                kind,
                project_root=project_root,
                loader=str(
                    args.get("loader") or getattr(meta, "loader", "") or ""
                ),
                mc=str(args.get("mc") or getattr(meta, "mc_version", "") or ""),
                version=str(args.get("version") or ""),
                progress=self._center_progress,
                cancel=self._cancel,
            )
            return resources.render(result)[:MAX_TOOL_RESULT]
        if name == "mod_reference":
            meta = getattr(self.project, "meta", None)
            action = str(args.get("action") or "auto").strip().lower()
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            version = str(args.get("version") or "")
            query = str(args.get("query") or self.user_text or "").strip()
            limit = max(1, min(_arg_int(args, "limit", 8), 20))
            if action == "status":
                return json.dumps(reference.status(), ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
            if action == "search":
                result = reference.search_references(
                    query, loader=loader, mc=mc, limit=limit
                )
                return reference.render_auto(
                    {**result, "prepared": [], "snippets": [], "license_notice": reference.LICENSE_NOTICE}
                )[:MAX_TOOL_RESULT]
            if action == "prepare":
                project_id = str(args.get("project") or "").strip()
                if not project_id:
                    return "mod_reference prepare 需要 project（Modrinth slug 或 ID）。"
                item = reference.prepare_reference(
                    project_id,
                    loader=loader,
                    mc=mc,
                    version_id=version,
                    force=bool(args.get("force")),
                    progress=self._center_progress,
                    cancel=self._cancel,
                )
                return (
                    f"参考源码：{item.get('sources')}\n"
                    f"文件：{item.get('files')} / 类：{item.get('classes') or '未知'}\n"
                    f"许可证：{item.get('license') or '未知'}\n"
                    f"{item.get('notice') or reference.LICENSE_NOTICE}"
                )[:MAX_TOOL_RESULT]
            if action == "snippets":
                hits = reference.search_snippets(query, limit=limit)
                if not hits:
                    return "没有找到匹配片段；先用 mod_reference prepare 准备参考源码。"
                return reference.render_auto(
                    {
                        "features": reference.detect_features(query),
                        "prepared": [],
                        "snippets": hits,
                        "notes": [],
                        "license_notice": reference.LICENSE_NOTICE,
                    }
                )[:MAX_TOOL_RESULT]
            result = reference.auto_reference(
                query,
                loader=loader,
                mc=mc,
                max_mods=max(1, min(_arg_int(args, "max_mods", 2), 4)),
                max_snippets=limit,
                force=bool(args.get("force")),
                progress=self._center_progress,
                cancel=self._cancel,
            )
            return reference.render_auto(result)[:MAX_TOOL_RESULT]
        if name == "launcher_status":
            bridge = launcher_bridge.LauncherBridge()
            return json.dumps(bridge.about(), ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "launcher_versions":
            bridge = launcher_bridge.LauncherBridge()
            info = bridge.about()
            rows = []
            for item in bridge.recommended_instances():
                bits = [item.id]
                if item.loader:
                    bits.append(item.loader)
                if item.loader_version:
                    bits.append(item.loader_version)
                rows.append(" / ".join(bits))
            return (
                json.dumps(info, ensure_ascii=False, indent=2)
                + "\n\n实例：\n"
                + ("\n".join(f"- {row}" for row in rows) or "（没有实例）")
            )[:MAX_TOOL_RESULT]
        if name == "launcher_downloads":
            bridge = launcher_bridge.LauncherBridge()
            data = bridge.downloads()
            latest = data.get("latest") or {}
            for key, value in list(latest.items()):
                if isinstance(value, dict) and "notes" in value:
                    value["notes"] = list(value.get("notes") or [])[:5]
            return json.dumps(data, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "launcher_vanilla":
            version = str(args.get("version") or "").strip()
            if not version:
                return "launcher_vanilla 缺少 version。"
            kind = str(args.get("kind") or "client").lower()
            bridge = launcher_bridge.LauncherBridge()
            if bool(args.get("download")):
                path = bridge.download_vanilla(
                    version, kind, paths.data_dir() / "vanilla", progress=self._center_progress
                )
                return f"已下载：{path}"
            data = bridge.vanilla(version)
            data["libraries"] = list(data.get("libraries") or [])[:20]
            return json.dumps(data, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "launcher_launch":
            version = str(args.get("version") or "").strip()
            if not version:
                return "launcher_launch 缺少 version。"
            bridge = launcher_bridge.LauncherBridge()
            result = bridge.launch(version)
            return json.dumps(result, ensure_ascii=False)
        return "未知工具：" + name

    # ------------------------------------------------------------------
    def _execute_kernel(self, name: str, args: dict[str, Any]) -> str:
        root = self.project.root
        if name.startswith("workflow_"):
            if self.kernel is None:
                return "当前没有任务图；请先打开或创建工程。"
            if name == "workflow_plan":
                idea = str(args.get("idea") or self.user_text or "").strip()
                result = self.kernel.replan(
                    idea,
                    include_runtime=bool(args.get("include_runtime", True)),
                )
                if not result.get("ok"):
                    return str(result.get("reason") or "规划失败。")
                markdown = self.kernel.workflow.markdown() if self.kernel.workflow else ""
                return (
                    f"已重建任务图；新增功能：{'、'.join(result.get('added') or []) or '无'}\n"
                    f"任务数：{result['progress']['total']}，当前：{result['progress'].get('current') or '无'}\n\n"
                    + markdown
                )[:MAX_TOOL_RESULT]
            if name == "workflow_status":
                status = self.kernel.status()
                if not status.get("ok"):
                    return str(status.get("reason") or "没有任务图。")
                markdown = self.kernel.workflow.markdown() if self.kernel.workflow else ""
                return (
                    json.dumps(status, ensure_ascii=False, indent=2)
                    + "\n\n"
                    + markdown
                )[:MAX_TOOL_RESULT]
            if name == "workflow_next":
                status = self.kernel.status()
                if not status.get("ok"):
                    return str(status.get("reason") or "没有任务图。")
                limit = max(1, min(_arg_int(args, "limit", 8), 30))
                rows = status.get("next") or []
                rows = rows[:limit]
                current = status.get("current")
                lines = [
                    f"进度：{status['progress']['percent']}%（{status['progress']['done']}/{status['progress']['total']}）",
                    f"当前任务：{current['id']} {current['title']}" if current else "当前没有可执行任务。",
                ]
                for item in rows:
                    acceptance = "、".join(item.get("acceptance") or []) or "—"
                    lines.append(
                        f"- {item['id']} {item['title']}（依赖：{'、'.join(item.get('depends_on') or []) or '无'}；"
                        f"验收：{acceptance}）"
                    )
                return "\n".join(lines)[:MAX_TOOL_RESULT]
            if name == "workflow_complete":
                task_id = str(args.get("task_id") or "").strip()
                if not task_id:
                    return "workflow_complete 缺少 task_id。"
                evidence = args.get("evidence")
                if isinstance(evidence, str):
                    evidence = [evidence]
                manual = args.get("manual_ok")
                result = self.kernel.complete_task(
                    task_id,
                    evidence=[str(item) for item in (evidence or []) if str(item)],
                    manual_ok=bool(manual) if manual is not None else None,
                    force=bool(args.get("force")),
                )
                if not result.get("ok"):
                    return (
                        f"任务 {task_id} 未完成：{result.get('reason') or '验收未通过'}\n\n"
                        + str(result.get("markdown") or "")
                    )[:MAX_TOOL_RESULT]
                progress = result.get("progress") or {}
                head = f"任务 {task_id} 已完成。"
                if progress:
                    head += f" 总进度 {progress.get('percent')}%（{progress.get('done')}/{progress.get('total')}）。"
                return (head + "\n\n" + str(result.get("markdown") or ""))[:MAX_TOOL_RESULT]
            if name in {"workflow_fail", "workflow_skip", "workflow_reopen"}:
                from .kernel.spec import save_spec
                from .kernel.workflow import save_workflow

                task_id = str(args.get("task_id") or "").strip()
                if not task_id:
                    return f"{name} 缺少 task_id。"
                workflow = self.kernel.workflow
                if workflow is None:
                    return "当前没有任务图。"
                try:
                    if name == "workflow_fail":
                        blocked = workflow.fail(task_id, str(args.get("detail") or ""))
                        message = f"任务 {task_id} 已标记失败；阻塞下游：{'、'.join(blocked) or '无'}。"
                    elif name == "workflow_skip":
                        workflow.skip(task_id, str(args.get("reason") or ""))
                        message = f"任务 {task_id} 已跳过。"
                    else:
                        workflow.reopen(task_id)
                        message = f"任务 {task_id} 及下游已重置为待办。"
                except KeyError as exc:
                    return str(exc)
                save_workflow(workflow, root)
                if self.kernel.spec is not None:
                    save_spec(self.kernel.spec, root)
                return message
            return f"未知工作流工具：{name}"
        if name in {"contract_freeze", "contract_check"}:
            from .kernel import contract as kernel_contract

            if name == "contract_freeze":
                result = kernel_contract.freeze(root, note=str(args.get("note") or ""))
                counts = result.get("counts") or {}
                return (
                    f"已冻结公共契约：{result['path']}\n"
                    f"类 {counts.get('classes', 0)} / 方法 {counts.get('methods', 0)} "
                    f"/ 字段 {counts.get('fields', 0)} / 资源 {counts.get('resources', 0)}"
                )
            result = kernel_contract.check(
                root, strict_resources=bool(args.get("strict_resources"))
            )
            return (
                json.dumps(
                    {key: result[key] for key in
                     ("ok", "frozen", "removed", "changed", "warnings") if key in result},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n\n"
                + kernel_contract.report_markdown(result)
            )[:MAX_TOOL_RESULT]
        if name == "project_fact_check":
            from . import modfacts

            meta = getattr(self.project, "meta", None)
            loader = str(args.get("loader") or getattr(meta, "loader", "") or "")
            mc = str(args.get("mc") or getattr(meta, "mc_version", "") or "")
            return modfacts.report(root, loader, mc)[:MAX_TOOL_RESULT]
        if name == "lint_project":
            raw = str(args.get("level") or "").strip()
            # level 既可能是 P0..P3，也可能是 error/warning/info。以前把
            # "error" 直接塞进 levels（P 级别集合），过滤后一条不剩，
            # lint 会错误地报告“通过”。
            severities = None
            levels = None
            if raw:
                lowered = raw.lower()
                if lowered in {"error", "warning", "info"}:
                    severities = {lowered}
                else:
                    levels = {raw.upper()}
            from .kernel.checks import run_checks

            report = run_checks(
                root,
                levels=levels,
                severities=severities,
                only={str(args["checker"])} if args.get("checker") else None,
            )
            if self.kernel is not None:
                self.kernel.last_lint = report
            return report.markdown()[:MAX_TOOL_RESULT]
        if name == "symbol_query":
            if self._symbol_index is None or args.get("refresh"):
                index = kernel_symbols.SymbolIndex()
                class_paths = sorted((root / ".dma" / "classpath").glob("*.jar"))
                index.build(root, class_paths=class_paths)
                self._symbol_index = index
            query = str(args.get("query") or "").strip()
            kind = str(args.get("kind") or "auto").strip().lower()
            limit = max(1, min(_arg_int(args, "limit", 20), 100))
            if not query:
                return "symbol_query 缺少 query。"
            if kind == "callers":
                rows = self._symbol_index.callers_of(query, limit=limit)
            elif kind == "listeners":
                rows = self._symbol_index.listeners_for(query, limit=limit)
            elif kind == "overrides":
                owner, method = _split_symbol(query)
                rows = [
                    item.to_dict() for item in self._symbol_index.overrides_of(owner, method)
                ] if owner else []
            else:
                rows = self._symbol_index.lookups(query, limit=limit)
            return json.dumps(rows, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "mapping_translate":
            source_name = str(args.get("name") or "").strip()
            if not source_name:
                return "mapping_translate 缺少 name。"
            target = str(args.get("to") or "mojmap")
            kind = str(args.get("kind") or "auto")
            info = kernel_mappings.detect_project(root)
            if kind == "method":
                translated = kernel_mappings.normalize_method(source_name, target)
            elif kind == "class":
                translated = kernel_mappings.normalize_class(source_name, target)
            else:
                translated = (
                    kernel_mappings.normalize_method(source_name, target)
                    if source_name[:1].islower()
                    else kernel_mappings.normalize_class(source_name, target)
                )
            return json.dumps(
                {
                    "project": info.to_dict(),
                    "input": source_name,
                    "target": target,
                    "translated": translated,
                    "detectedNamespace": kernel_mappings.classify_namespace(source_name),
                },
                ensure_ascii=False,
                indent=2,
            )
        if name == "impact_analysis":
            changed = args.get("paths")
            if isinstance(changed, str):
                changed = [changed]
            if not changed and args.get("git", True):
                changed = tool_impact.changed_from_git(root)
            if not changed:
                return "没有待分析的改动；请传 paths，或在 Git 工程里先修改文件。"
            report = tool_impact.analyze(root, [str(item) for item in changed])
            return report.markdown()[:MAX_TOOL_RESULT]
        if name == "log_parse":
            raw = str(args.get("text") or "")
            path = str(args.get("path") or "").strip()
            if path:
                target = project_mod.safe_path(root, path)
                raw = target.read_text(encoding="utf-8", errors="replace")
            if not raw.strip():
                return "log_parse 需要 text 或 path。"
            report = tool_crash.parse(raw, source=path)
            causes = tool_crash.classify(report)
            clusters = tool_crash.cluster([report])
            return (
                report.markdown()
                + "\n\n### 分类\n"
                + json.dumps(causes, ensure_ascii=False, indent=2)
                + "\n\n### 聚类\n"
                + json.dumps(clusters, ensure_ascii=False, indent=2)
            )[:MAX_TOOL_RESULT]
        if name == "snapshot":
            if self.kernel is None or self.kernel.snapshots is None:
                return "没有工程，无法创建快照。"
            snap = self.kernel.snapshots.create(
                str(args.get("label") or "manual"), str(args.get("note") or "")
            )
            return json.dumps(snap.to_dict(), ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "snapshot_list":
            return json.dumps(
                self.kernel.list_snapshots() if self.kernel else {"snapshots": []},
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "rollback":
            if self.kernel is None:
                return "没有工程，无法回滚。"
            result = self.kernel.rollback(
                str(args.get("snapshot") or ""), dry_run=bool(args.get("dry_run"))
            )
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "asset_lint":
            from .kernel.checks import run_checks

            report = run_checks(root, only={"assets"})
            return report.markdown()[:MAX_TOOL_RESULT]
        if name == "texture_gen":
            relative = str(args.get("path") or "").strip()
            if not relative.lower().endswith(".png"):
                return "texture_gen 只支持 .png。"
            if "/textures/entity/" in relative.replace("\\", "/").lower():
                return ("实体皮肤不能用 texture_gen（物品模板只有 16x16，贴到模型上会被拉伸）。"
                        "请改用 entity_texture，指定 layout/boxes 生成 64x64 UV 图集。")
            target = project_mod.safe_path(root, relative)
            size = _arg_int(args, "size", 16)
            style = str(args.get("style") or args.get("kind") or "").strip().lower()
            stem_name = relative.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            is_block = "/block/" in relative.replace("\\", "/")
            if not style or style in {"block", "tile", "texture"}:
                style = (tool_texture.infer_block_style(stem_name)
                         if is_block else "box")
            raw_base = args.get("base") or args.get("palette")
            if raw_base:
                base = str(raw_base)
            elif is_block:
                base = tool_texture.block_color(stem_name, style)
            else:
                base = tool_texture.material_color(stem_name)
            # Ore/crystal sparkle needs a contrasting mineral hue; deriving
            # it from the stone base made every ore grey on grey.
            default_accent = (
                tool_texture.block_accent(stem_name, style, base)
                if style in tool_texture.BLOCK_STYLES
                else tool_texture.shade(base, 1.35)
            )
            accent = str(args.get("accent") or default_accent)
            seed = _arg_int(args, "seed", 0)
            transparent = bool(args.get("transparent"))
            try:
                if style in tool_texture.BLOCK_STYLES:
                    tool_texture.generate(
                        target,
                        tool_texture.TextureSpec(
                            size=(size, size), style=style, base=base,
                            accent=accent, seed=seed, transparent=transparent,
                            max_colors=64,
                        ),
                    )
                else:
                    tool_texture.generate_item(
                        target, style, base=base, accent=accent,
                        size=size, seed=seed,
                    )
                check = tool_texture.validate(target)
            except Exception as exc:  # noqa: BLE001 - 缺 Pillow 或参数错误
                return f"贴图生成失败：{exc}"
            return json.dumps(
                {"path": relative, "style": style, "validation": check},
                ensure_ascii=False, indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "entity_texture":
            relative = str(args.get("path") or "").strip()
            if not relative.lower().endswith(".png"):
                return "entity_texture 只支持 .png。"
            target = project_mod.safe_path(root, relative)
            layout = str(args.get("layout") or "humanoid").strip().lower()
            raw_size = args.get("size")
            if isinstance(raw_size, (list, tuple)) and len(raw_size) == 2:
                size = (max(16, min(256, int(raw_size[0]))), max(16, min(256, int(raw_size[1]))))
            else:
                size = (64, 64)
            boxes = args.get("boxes")
            if boxes is not None and not isinstance(boxes, list):
                return "entity_texture 的 boxes 必须是数组。"
            try:
                stem = relative.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                entity_base = str(args.get("base") or tool_texture.material_color(
                    stem, "#8a8a9a"))
                entity_accent = str(args.get("accent") or tool_texture.entity_accent(
                    str(args.get("variant") or stem), entity_base))
                tool_texture.generate_entity(
                    target,
                    layout=layout,
                    base=entity_base,
                    accent=entity_accent,
                    seed=_arg_int(args, "seed", 0),
                    size=size,
                    variant=str(args.get("variant") or ""),
                    boxes=boxes,
                    transparent=True if args.get("transparent") is None
                    else bool(args.get("transparent")),
                )
                check = tool_texture.validate(target, expected_size=size)
            except Exception as exc:  # noqa: BLE001
                return f"实体皮肤生成失败：{exc}"
            return json.dumps(
                {"path": relative, "layout": layout, "validation": check},
                ensure_ascii=False, indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "vision_review":
            relative = str(args.get("path") or "").strip()
            target = project_mod.safe_path(root, relative)
            review = tool_vision.review(target, style=str(args.get("kind") or "item"))
            return json.dumps(review.to_dict(), ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "blockbench":
            action = str(args.get("action") or "create").lower()
            relative = str(args.get("path") or "").strip()
            target = project_mod.safe_path(root, relative)
            namespace = str(args.get("namespace") or "").strip() or (
                self.kernel.spec.modid if self.kernel is not None and self.kernel.spec else "mod"
            )
            texture_arg = str(args.get("texture") or "").strip()
            texture_path = None
            texture_name = ""
            if texture_arg:
                try:
                    candidate = project_mod.safe_path(root, texture_arg)
                except Exception:  # noqa: BLE001 - resource name, not a file
                    candidate = None
                if candidate is not None and candidate.is_file():
                    texture_path = candidate
                    texture_name = tool_blockbench.texture_data_uri(candidate)
                elif not texture_arg.lower().endswith(".png") and "/" not in texture_arg and "\\" not in texture_arg:
                    texture_name = texture_arg
            if action in {"create", "export"}:
                shape = str(args.get("shape") or "full").lower()
                cube_specs = args.get("cubes")
                raw_size = args.get("size")
                texture_size = (64, 64) if isinstance(cube_specs, list) and cube_specs else (16, 16)
                if isinstance(raw_size, list) and len(raw_size) == 2:
                    try:
                        texture_size = (
                            max(1, int(raw_size[0])),
                            max(1, int(raw_size[1])),
                        )
                    except (TypeError, ValueError):
                        pass
                if isinstance(cube_specs, list) and cube_specs:
                    model = tool_blockbench.model_from_spec(
                        target.stem,
                        cube_specs,
                        texture_name=texture_name,
                        texture_size=texture_size,
                        author=str(args.get("author") or ""),
                    )
                elif shape == "cross":
                    model = tool_blockbench.cross_model(
                        target.stem, texture="0",
                    )
                    model.texture_name = texture_name
                else:
                    model = tool_blockbench.full_block(target.stem, texture="0")
                    model.texture_name = texture_name
                tool_blockbench.save_bbmodel(model, target)
                if action == "export":
                    written = tool_blockbench.export(
                        model,
                        root,
                        namespace=namespace,
                        name=str(args.get("name") or target.stem),
                        texture_png=texture_path,
                        kind=str(args.get("kind") or "block"),
                    )
                    rel = [item.relative_to(root).as_posix() for item in written]
                    return f"已生成 {relative}，导出：" + "、".join(rel)
                return f"已生成 {relative}（{len(model.cubes)} 个立方体，贴图 {texture_size[0]}×{texture_size[1]}）"
            if action == "load":
                model = tool_blockbench.load_bbmodel(target)
                return json.dumps(tool_blockbench.summary(model), ensure_ascii=False, indent=2)
            if action == "validate":
                model = tool_blockbench.load_bbmodel(target)
                issues = tool_blockbench.validate(model)
                return json.dumps({"issues": issues}, ensure_ascii=False, indent=2)
            if action == "preview":
                model = tool_blockbench.load_bbmodel(target)
                output = target.with_name(target.stem + "_preview.png")
                if texture_path is None:
                    sibling = target.with_suffix(".png")
                    texture_path = sibling if sibling.is_file() else None
                tool_blockbench.preview(model, output, texture=texture_path)
                return f"预览图已生成：{output.relative_to(root).as_posix()}"
            return "blockbench action 只能是 create / export / load / validate / preview。"
        if name == "testgen":
            if self.kernel is None or self.kernel.spec is None:
                return "没有规格，无法生成测试。"
            feature = str(args.get("feature") or "").strip()
            written = tool_testgen.write_suite(
                root, self.kernel.spec, features=[feature] if feature else None
            )
            return "已生成测试：\n" + "\n".join(str(item.relative_to(root)) for item in written)
        if name == "migration_plan":
            modid = self.kernel.spec.modid if self.kernel and self.kernel.spec else "unknown"
            migration = tool_migration.Migration(
                modid=modid,
                from_version=str(args.get("from_mc") or ""),
                to_version=str(args.get("to_mc") or ""),
            )
            path = tool_migration.write(root, migration)
            plan = tool_migration.plan(migration)
            if args.get("apply"):
                rows = tool_migration.apply(root, migration, dry_run=True)
                plan += "\n\n预览：\n" + json.dumps(rows, ensure_ascii=False, indent=2)
            return f"{plan}\n\n迁移表：{path.relative_to(root)}"[:MAX_TOOL_RESULT]
        if name == "screenshot_collect":
            game_dir = Path(str(args.get("path") or root))
            out = root / ".dma" / "screenshots"
            result = tool_screenshot.collect(
                game_dir, out, limit=_arg_int(args, "limit", 5)
            )
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "offline_cache":
            cache = tool_cache.OfflineCache(root)
            action = str(args.get("action") or "summary")
            if action == "verify":
                result = cache.verify()
            elif action == "prune":
                result = {"removed": cache.prune()}
            elif action == "export":
                target = project_mod.safe_path(
                    root, str(args.get("path") or ".dma/cache-export")
                )
                result = {"path": str(cache.export_tree("bundle", target))}
            else:
                result = cache.summary()
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "asset_library":
            library = tool_library.AssetLibrary(root)
            action = str(args.get("action") or "summary").lower()
            if action == "bootstrap":
                created = library.bootstrap()
                return json.dumps(
                    {"created": [item.to_dict() for item in created]},
                    ensure_ascii=False, indent=2,
                )
            if action == "list":
                return json.dumps(
                    [item.to_dict() for item in library.list(
                        validated_only=bool(args.get("validated", True))
                    )],
                    ensure_ascii=False, indent=2,
                )
            if action == "clone":
                library.bootstrap()
                target = project_mod.safe_path(root, str(args.get("path") or ""))
                rotation = args.get("rotation")
                offset = args.get("offset")
                result = library.clone(
                    str(args.get("name") or ""),
                    target,
                    texture=str(args["texture"]) if args.get("texture") else None,
                    scale=float(args.get("scale") or 1.0),
                    rotation=tuple(float(item) for item in rotation) if isinstance(rotation, list) else None,
                    offset=tuple(float(item) for item in offset) if isinstance(offset, list) else None,
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            if action == "import":
                source = project_mod.safe_path(root, str(args.get("path") or ""))
                entry = library.import_asset(source, validated=bool(args.get("validated")))
                return json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
            if action == "promote":
                entry = library.promote(str(args.get("name") or ""))
                return json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
            return json.dumps(library.summary(), ensure_ascii=False, indent=2)
        if name == "config_gen":
            modid = (
                self.kernel.spec.modid
                if self.kernel is not None and self.kernel.spec is not None
                else getattr(getattr(self.project, "meta", None), "mod_id", "")
            )
            if not modid:
                return "无法确定 modid，请先打开工程。"
            package = str(args.get("package") or "").strip() or tool_templates._detect_package(root)
            relative = str(args.get("schema_path") or f"config/{modid}.schema.json")
            schema_path = project_mod.safe_path(root, relative)
            if not schema_path.is_file():
                return f"找不到配置 schema：{relative}"
            try:
                raw = json.loads(schema_path.read_text(encoding="utf-8"))
            except ValueError as exc:
                return f"配置 schema 不是合法 JSON：{exc}"
            fields = tool_configgen.parse_schema(raw)
            if not fields:
                return "配置 schema 里没有可生成的字段。"
            result = tool_configgen.write(
                root,
                package=package,
                modid=modid,
                fields=fields,
                class_name=str(args.get("class_name") or "ModConfigScreen"),
                mc=(
                    self.kernel.spec.mc
                    if self.kernel is not None and self.kernel.spec is not None
                    else getattr(getattr(self.project, "meta", None), "mc_version", "1.20.1")
                ),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name == "debug_scaffold":
            modid = (
                self.kernel.spec.modid
                if self.kernel is not None and self.kernel.spec is not None
                else getattr(getattr(self.project, "meta", None), "mod_id", "")
            )
            if not modid:
                return "无法确定 modid，请先打开工程。"
            package = str(args.get("package") or "").strip() or tool_templates._detect_package(root)
            version = str(args.get("version") or "").strip()
            if not version:
                properties = root / "gradle.properties"
                if properties.is_file():
                    match = re.search(
                        r"^\s*mod_version\s*[=:]\s*(\S+)",
                        properties.read_text(encoding="utf-8", errors="replace"),
                        re.MULTILINE,
                    )
                    if match:
                        version = match.group(1).strip()
            raw_kinds = args.get("kinds")
            kinds = [str(item) for item in raw_kinds] if isinstance(raw_kinds, list) else None
            try:
                result = tool_diagnostics.write(
                    root,
                    package=package,
                    modid=modid,
                    version=version or "0.0.0",
                    kinds=kinds,
                    command=str(args.get("command") or "dump"),
                )
            except ValueError as exc:
                return f"生成调试模板失败：{exc}"
            return json.dumps(result, ensure_ascii=False, indent=2)
        if name == "matrix_plan":
            action = str(args.get("action") or "plan").lower()

            def values(key: str) -> list[str] | None:
                raw = args.get(key)
                return [str(item) for item in raw] if isinstance(raw, list) and raw else None

            matrix = tool_matrix.plan(
                mc_versions=values("mc"),
                loaders=values("loader"),
                java_versions=values("java"),
                os_list=values("os"),
            )
            if action == "workflow":
                target = tool_matrix.write_workflow(
                    root,
                    matrix,
                    relative=str(args.get("path") or ".github/workflows/dma-matrix.yml"),
                )
                return f"已生成 CI 工作流：{target.relative_to(root).as_posix()}"
            if action == "report":
                return tool_matrix.report(matrix)[:MAX_TOOL_RESULT]
            if action == "version":
                return json.dumps(
                    tool_matrix.git_version(root), ensure_ascii=False, indent=2
                )
            return tool_matrix.summary(matrix)[:MAX_TOOL_RESULT]
        return f"未知的内核工具：{name}"

    # ------------------------------------------------------------------
    def _execute_mcdev(self, name: str, args: dict[str, Any]) -> str:
        root = self.project.root if self.project is not None else None
        needs_project = {
            "mcdev_prepare",
            "mcdev_build",
            "mcdev_gametest",
            "mcdev_install_probe",
            "mcdev_build_probe",
        }
        if root is None and name in needs_project:
            return "这个工具需要先打开工程。"
        if name == "mcdev_prepare":
            kind = str(args.get("kind") or args.get("side") or "both").strip().lower()
            if kind not in {"server", "client", "both"}:
                return "kind 只能是 server、client 或 both。"
            try:
                timeout_ms = int(args.get("timeout_ms") or 900_000)
            except (TypeError, ValueError):
                timeout_ms = 900_000
            try:
                join_port = int(args.get("join_port") or 25566)
            except (TypeError, ValueError):
                join_port = 25566
            result = self.mcdev.prepare(
                root,
                kind,
                timeout_ms=max(30_000, min(timeout_ms, 1_800_000)),
                wait_join=bool(args.get("wait_join", True)),
                join_port=join_port,
                install_probe=bool(args.get("install_probe", True)),
                background=bool(args.get("background", True)),
            )
            self._progress_at = time.monotonic()
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "mcdev_stop":
            side = str(args.get("side") or "all").strip().lower()
            if side not in {"server", "client", "all"}:
                return "side 只能是 server、client 或 all。"
            return json.dumps(self.mcdev.stop(side), ensure_ascii=False, indent=2)
        if name == "mcdev_launch_status":
            return json.dumps(self.mcdev.launch_status(), ensure_ascii=False, indent=2)
        if name == "mcdev_install_probe":
            jar = str(args.get("probe_jar") or "").strip()
            run_dirs = args.get("run_dirs")
            if jar:
                targets = (
                    [str(item) for item in run_dirs]
                    if isinstance(run_dirs, list) and run_dirs
                    else [str(item) for item in mcdev.run_dirs(root)]
                )
                info = self.mcdev.install_probe(jar, targets)
            else:
                info = self.mcdev.ensure_probe(root)
            return json.dumps(info, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "mcdev_build_probe":
            jar = mcdev.templates.build_probe(
                root, log=self._probe_log, cancel=self._cancel
            )
            return f"探针已编译：{jar}"
        if name == "mcdev_scenario":
            steps = args.get("steps")
            if not isinstance(steps, list):
                steps = None
            card = str(args.get("card") or "").strip()
            if not card and args.get("path"):
                card = str(args.get("path"))
            result = self.mcdev.scenario(
                steps,
                card=card or None,
                dry_run=bool(args.get("dry_run")),
            )
            if self.kernel is not None and not args.get("dry_run"):
                has_assert = any(
                    str(item.get("type")) == "assert" for item in result.get("steps") or []
                )
                self.kernel.record_runtime(bool(result.get("ok")) and has_assert)
            self._progress_at = time.monotonic()
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "mcdev_build":
            if self.kernel is not None:
                self.kernel.before_build("mcdev-build")
            code, output = mcdev.gradle.build(
                root, log=self._probe_log, timeout=1800, cancel=self._cancel
            )
            tail = output[-MAX_BUILD_RESULT:]
            head = "编译成功（退出码 0）。" if code == 0 else f"编译失败（退出码 {code}）。"
            if self.kernel is not None:
                self.kernel.record_build(code == 0, output)
            self._progress_at = time.monotonic()
            return f"{head}\n\n{tail}"[:MAX_TOOL_RESULT]
        if name == "mcdev_gametest":
            code, output = mcdev.gradle.gametest(
                root,
                loader=getattr(self.project.meta, "loader", ""),
                log=self._probe_log,
                timeout=1800,
                cancel=self._cancel,
            )
            summary = mcdev.gradle.gametest_summary(code, output)
            head = "GameTest 通过。" if code == 0 else f"GameTest 失败（退出码 {code}）。"
            if self.kernel is not None:
                self.kernel.record_test(code == 0, summary)
            self._progress_at = time.monotonic()
            return f"{head}\n{summary}\n\n{output[-MAX_BUILD_RESULT:]}"[:MAX_TOOL_RESULT]
        if name in {"mcdev_bot", "game_bot_playtest", "game_window"}:
            port = args.get("port")
            try:
                port = int(port) if port else None
            except (TypeError, ValueError):
                port = None
        if name == "mcdev_bot":
            from .mcdev import bot as mcbot

            action = str(args.get("action") or "state").strip().lower()
            try:
                result = mcbot.control(action, args, port=port)
            except Exception as exc:  # noqa: BLE001 - 返回可读失败原因
                return f"mcdev_bot {action} 执行失败：{exc}"
            self._progress_at = time.monotonic()
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "game_bot_playtest":
            from .mcdev import bot as mcbot

            try:
                result = mcbot.playtest(
                    name=str(args.get("name") or mcbot.DEFAULT_BOT),
                    port=port,
                    log=lambda text: self.status.emit(f"[bot] {text}"[-160:]),
                )
            except Exception as exc:  # noqa: BLE001
                return f"game_bot_playtest 执行失败：{exc}"
            self._progress_at = time.monotonic()
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "game_window":
            from .mcdev import game as mcgame

            action = str(args.get("action") or "status").strip().lower()
            try:
                if action == "status":
                    result = mcgame.window_status(port=port)
                elif action in {"show", "hide"}:
                    result = mcgame.set_window_visible(
                        action == "show",
                        focus=bool(args.get("focus")),
                        port=port,
                    )
                else:
                    return "game_window 的 action 只能是 status、show 或 hide。"
            except Exception as exc:  # noqa: BLE001
                return f"game_window {action} 执行失败：{exc}"
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]

        side = str(args.get("side") or "server").strip().lower()
        if side not in {"server", "client"}:
            return "side 只能是 server 或 client。"
        port = args.get("port")
        try:
            port = int(port) if port else None
        except (TypeError, ValueError):
            port = None
        if name == "mcdev_status":
            return json.dumps(
                mcdev.probe.probe_json(side, "/status", port=port),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_command":
            cmd = str(args.get("cmd") or args.get("command") or "").strip()
            if not cmd:
                return "mcdev_command 缺少 cmd。"
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/command", method="POST", payload={"cmd": cmd}, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_ui":
            return json.dumps(
                mcdev.probe.probe_json(side, "/ui/snapshot", port=port),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_click":
            body: dict[str, Any] = {
                "button": _arg_int(args, "button", 0),
            }
            slot = args.get("slot")
            if isinstance(slot, int) and slot >= 0:
                body["slot"] = slot
            else:
                body["x"] = _arg_int(args, "x", 0)
                body["y"] = _arg_int(args, "y", 0)
            if args.get("shift"):
                body["shift"] = True
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/ui/click", method="POST", payload=body, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_screenshot":
            return mcdev.probe.probe(
                side, "/screenshot", port=port, timeout=30.0
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_run_dump":
            try:
                count = int(args.get("n") or 200)
            except (TypeError, ValueError):
                count = 200
            return mcdev.probe.probe(
                side, f"/run/dump?n={max(1, min(count, 2000))}", port=port
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_use":
            payload = {
                "x": args.get("x"),
                "y": args.get("y"),
                "z": args.get("z"),
                "face": args.get("face") or "up",
            }
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/player/use", method="POST", payload=payload, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_goto":
            try:
                tx = float(args.get("x"))
                tz = float(args.get("z"))
            except (TypeError, ValueError):
                return "mcdev_goto 需要数字 x 和 z。"
            try:
                timeout = max(3.0, min(120.0, float(args.get("timeout") or 25)))
            except (TypeError, ValueError):
                timeout = 25.0
            from .mcdev import gameplay

            result = gameplay.goto(
                tx, tz, timeout=timeout,
                radius=float(args.get("radius") or 0.9),
                log=lambda text: self.status.emit(f"[goto] {text}"[-160:]),
            )
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        if name == "mcdev_move":
            mode = str(args.get("mode") or "walk").strip().lower()
            ms = max(0, min(10_000, _arg_int(args, "ms", 1000)))
            payload: dict[str, Any] = {"mode": mode, "ms": ms}
            for key in ("x", "y", "z", "dx", "dy", "dz"):
                if args.get(key) is not None:
                    payload[key] = args.get(key)
            for key in ("keys", "sprint", "sneak", "jump"):
                if args.get(key) is not None:
                    payload[key] = args.get(key)
            timeout = 20.0 + ms / 1000.0
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/player/move", method="POST", payload=payload, port=port,
                    timeout=timeout,
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_look":
            payload = {}
            for key in ("x", "y", "z", "yaw", "pitch", "entity", "target"):
                if args.get(key) is not None:
                    payload[key] = args.get(key)
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/player/look", method="POST", payload=payload, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_attack":
            payload = {
                "radius": args.get("radius") if args.get("radius") is not None else 4.0,
            }
            if args.get("entity") is not None:
                payload["entity"] = args.get("entity")
            if args.get("target"):
                payload["target"] = args.get("target")
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/player/attack", method="POST", payload=payload, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name in {"mcdev_mine", "mcdev_break"}:
            payload = {
                "x": _arg_int(args, "x", 0),
                "y": _arg_int(args, "y", 64),
                "z": _arg_int(args, "z", 0),
            }
            if name == "mcdev_mine":
                payload["timeoutMs"] = _arg_int(args, "timeoutMs", 6000)
                path = "/player/mine"
                timeout = 20.0
            else:
                path = "/player/break"
                timeout = 15.0
            return json.dumps(
                mcdev.probe.probe_json(
                    side, path, method="POST", payload=payload, port=port, timeout=timeout
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_use_item":
            return json.dumps(
                mcdev.probe.probe_json(
                    side, "/player/use_item", method="POST", payload={}, port=port
                ),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "mcdev_inventory":
            return json.dumps(
                mcdev.probe.probe_json(side, "/player/inventory", port=port),
                ensure_ascii=False,
                indent=2,
            )[:MAX_TOOL_RESULT]
        if name == "game_playtest":
            from .mcdev import gameplay

            try:
                result = gameplay.playtest(
                    side=side,
                    port=port,
                    seconds=max(1, min(10, _arg_int(args, "seconds", 2))),
                    log=lambda text: self.status.emit(f"[playtest] {text}"[-200:]),
                )
            except Exception as exc:  # noqa: BLE001 - 失败要返回证据而不是抛出
                return f"game_playtest 执行失败：{exc}"
            return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]
        return f"未知的 mcdev 工具：{name}"

    # ------------------------------------------------------------------
    def _execute_design(self, args: dict[str, Any]) -> str:
        from . import designs

        if self.project is None:
            return "design_studio 需要先打开工程。"
        root = self.project.root
        action = str(args.get("action") or "generate").strip().lower()
        try:
            if action in {"generate", "create"}:
                kind = str(args.get("kind") or "entity").strip().lower()
                name = str(
                    args.get("name")
                    or args.get("target")
                    or args.get("id")
                    or "design"
                ).strip()
                description = str(
                    args.get("description") or args.get("prompt") or ""
                ).strip()
                session = designs.create_session(
                    root,
                    kind=kind,
                    name=name,
                    description=description,
                    namespace=str(
                        args.get("namespace")
                        or getattr(self.project.meta, "mod_id", "")
                        or "modid"
                    ),
                    shape=str(args.get("shape") or ""),
                    count=_arg_int(args, "count", 3),
                    seed=_arg_int(args, "seed", 0),
                )
                self._progress_at = time.monotonic()
                summary = session.summary()
                if self.ui_available:
                    self.status.emit(
                        f"已生成 {len(session.variants)} 个方案，等待你选择…"
                    )
                    request = designs.DesignRequest(session, cancel=self._cancel)
                    self.design_request.emit(request)
                    choice = request.wait(timeout=1800)
                    if choice.get("action") != "adopt":
                        return json.dumps(
                            {
                                "ok": False,
                                "cancelled": choice.get("action") == "cancel",
                                "session": session.id,
                                "variants": summary["variants"],
                                "hint": (
                                    "用户没有选定方案。不要自行假装已选定；"
                                    "如需继续，请让用户再选一次或说明要改的描述。"
                                ),
                            },
                            ensure_ascii=False,
                            indent=2,
                        )[:MAX_TOOL_RESULT]
                    variant = str(choice.get("variant") or "")
                else:
                    variant = session.variants[0]["id"]
                applied = designs.apply_variant(root, session, variant)
                self.applied.extend(
                    item for item in applied["written"] if item not in self.applied
                )
                self.status.emit(f"方案 {applied['variant']} 已写入工程资源")
                return json.dumps(
                    {
                        "ok": True,
                        "selected": applied["variant"],
                        "label": applied["label"],
                        "uploaded": applied["uploaded"],
                        "applied": applied,
                        "session": session.id,
                        "variants": summary["variants"],
                        "next": (
                            "用户已经选定外观，贴图/模型/方块状态已写入工程。"
                            "请继续完成注册代码、模型引用与语言文件；"
                            "实体模型为 GeckoLib geo.json 时按项目现有依赖接线。"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )[:MAX_TOOL_RESULT]
            if action == "list":
                session_id = str(args.get("session") or "").strip()
                session = (
                    designs.load_session(root, session_id)
                    if session_id
                    else designs.latest_session(root)
                )
                if session is None:
                    return "还没有设计方案会话；先调用 design_studio generate。"
                return json.dumps(session.summary(), ensure_ascii=False, indent=2)[
                    :MAX_TOOL_RESULT
                ]
            if action == "apply":
                session_id = str(args.get("session") or "").strip()
                session = (
                    designs.load_session(root, session_id)
                    if session_id
                    else designs.latest_session(root)
                )
                if session is None:
                    return "找不到设计方案会话；先调用 design_studio generate。"
                applied = designs.apply_variant(
                    root, session, str(args.get("variant") or "A")
                )
                return json.dumps(applied, ensure_ascii=False, indent=2)[
                    :MAX_TOOL_RESULT
                ]
            if action == "retexture":
                session_id = str(args.get("session") or "").strip()
                session = (
                    designs.load_session(root, session_id)
                    if session_id
                    else designs.latest_session(root)
                )
                if session is None:
                    return "找不到设计方案会话；先调用 design_studio generate。"
                variant = session.retexture(
                    str(args.get("variant") or "A"),
                    extra=str(args.get("description") or ""),
                )
                applied = designs.apply_variant(root, session, variant["id"])
                return json.dumps(
                    {
                        "ok": True,
                        "retextured": variant["id"],
                        "preview": str((session.dir / variant["preview"]).as_posix()),
                        "applied": applied,
                    },
                    ensure_ascii=False,
                    indent=2,
                )[:MAX_TOOL_RESULT]
            return "design_studio action 只能是 generate / list / apply / retexture。"
        except Exception as exc:  # noqa: BLE001 - 设计失败要带原因回模型
            if self.cancelled:
                raise AgentError("已取消") from exc
            return f"design_studio 失败：{exc}"

    def _execute_game(self, name: str, args: dict[str, Any]) -> str:
        """AI 游戏控制：换语言、建存档、操作玩家、截图复盘、执行测试计划。"""
        from .mcdev import game as game_mod

        port = args.get("port")
        try:
            port = int(port) if port else None
        except (TypeError, ValueError):
            port = None

        def number(key: str, default: float) -> float:
            raw = args.get(key)
            if raw is None or raw == "":
                return default
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        try:
            if name == "game_observe":
                entry = self.mcdev.launches.get("client")
                window_pid = (
                    entry.pid
                    if entry is not None and getattr(entry, "running", False)
                    else None
                )
                result = game_mod.observe(
                    radius=int(number("radius", 8)),
                    screenshot=bool(args.get("screenshot", True)),
                    label=str(args.get("label") or "observe"),
                    log_lines=int(number("log_lines", 40)),
                    port=port,
                    window_pid=window_pid,
                    project_root=self.project.root if self.project else None,
                )
            elif name == "game_language":
                result = game_mod.set_language(
                    str(args.get("code") or args.get("language") or "zh_cn"),
                    reload=bool(args.get("reload", True)),
                    port=port,
                    wait=number("wait", 20),
                    cancel=self._cancel,
                )
            elif name == "game_new_world":
                result = game_mod.create_world(
                    str(args.get("name") or "AI 测试"),
                    gamemode=str(args.get("gamemode") or "creative"),
                    difficulty=str(args.get("difficulty") or "normal"),
                    world_type=str(
                        args.get("world_type") or args.get("worldType") or "normal"
                    ),
                    seed=str(args.get("seed") or ""),
                    cheats=bool(args.get("cheats", True)),
                    structures=bool(args.get("structures", True)),
                    bonus_chest=bool(args.get("bonusChest", False)),
                    port=port,
                    wait=number("wait", 120),
                    cancel=self._cancel,
                )
            elif name == "game_setup":
                game_mod.ensure_ready(port)
                code = str(args.get("code") or args.get("language") or "zh_cn")
                result: dict[str, Any] = {
                    "language": game_mod.set_language(
                        code,
                        port=port,
                        wait=number("lang_wait", 20),
                        cancel=self._cancel,
                    )
                }
                world_arg = args.get("world", True)
                if world_arg is not False:
                    world = world_arg if isinstance(world_arg, dict) else {}
                    result["world"] = game_mod.create_world(
                        str(world.get("name") or args.get("name") or "AI 测试"),
                        gamemode=str(
                            world.get("gamemode") or args.get("gamemode") or "creative"
                        ),
                        difficulty=str(
                            world.get("difficulty") or args.get("difficulty") or "normal"
                        ),
                        world_type=str(
                            world.get("worldType")
                            or world.get("world_type")
                            or args.get("world_type")
                            or "normal"
                        ),
                        seed=str(world.get("seed") or args.get("seed") or ""),
                        cheats=bool(world.get("cheats", args.get("cheats", True))),
                        structures=bool(
                            world.get("structures", args.get("structures", True))
                        ),
                        bonus_chest=bool(world.get("bonusChest", False)),
                        port=port,
                        wait=number("world_wait", number("wait", 120)),
                        cancel=self._cancel,
                    )
                result["status"] = game_mod.ensure_ready(port)
            elif name == "game_control":
                action = str(args.get("action") or "").strip()
                if not action:
                    return (
                        "game_control 缺少 action。可用：" + "、".join(
                            sorted(game_mod.CONTROL_ACTIONS)
                        )
                    )
                params = {
                    key: value
                    for key, value in args.items()
                    if key not in {"action", "port"} and value is not None
                }
                result = game_mod.control(action, params, port=port)
            elif name == "game_test":
                raw_steps = args.get("steps")
                if isinstance(raw_steps, str):
                    try:
                        raw_steps = json.loads(raw_steps)
                    except ValueError:
                        return "game_test 的 steps 不是合法 JSON。"
                if not isinstance(raw_steps, list):
                    return "game_test 需要 steps 数组。"
                result = game_mod.run_test(
                    raw_steps,
                    port=port,
                    continue_on_error=bool(args.get("continue_on_error")),
                    cancel=self._cancel,
                )
            else:
                return f"未知的游戏控制工具：{name}"
        except game_mod.GameError as exc:
            return f"游戏控制失败：{exc}"
        except (AssertionError, ValueError) as exc:
            return f"游戏控制失败：{exc}"

        self._progress_at = time.monotonic()
        if self.kernel is not None and name == "game_test":
            steps = result.get("steps") if isinstance(result, dict) else None
            has_assert = any(
                str(item.get("type")) == "assert"
                for item in steps or []
                if isinstance(item, dict)
            )
            if has_assert:
                self.kernel.record_runtime(bool(result.get("ok")))
        return json.dumps(result, ensure_ascii=False, indent=2)[:MAX_TOOL_RESULT]


@dataclass
class BuildOutcome:
    ok: bool
    code: int
    output: str = ""
    jars: list[tuple[str, int]] = field(default_factory=list)
    message: str = ""


def _build_jars(root: Path) -> list[tuple[str, int]]:
    libs = root / "build" / "libs"
    rows: list[tuple[str, int]] = []
    if not libs.is_dir():
        return rows
    try:
        files = sorted(item for item in libs.glob("*.jar") if item.is_file())
    except OSError:
        return rows
    for item in files:
        try:
            rows.append((item.name, item.stat().st_size))
        except OSError:
            rows.append((item.name, 0))
    return rows


def _run_gradle(
    root: Path,
    task: str,
    timeout: float,
    cancel: threading.Event,
    log,
) -> BuildOutcome:
    """执行 Gradle，并把 build/libs 里的真实产物带回给模型。"""
    try:
        mcdev.templates.restore_incompatible_scaffold(root, log=log)
    except Exception:  # noqa: BLE001 - 骨架修复失败不应挡住构建
        pass
    code, output = mcdev.gradle.run_gradle(
        root,
        task,
        timeout=int(timeout),
        cancel=cancel,
        log=log,
    )
    jars = _build_jars(root) if task == "build" and code == 0 else []
    return BuildOutcome(ok=code == 0, code=code, output=output, jars=jars)


def _build_report(result: BuildOutcome, root: Path | None = None) -> str:
    if result.ok:
        head = "编译成功（退出码 0）。"
    else:
        head = f"编译失败（退出码 {result.code}）。"
    parts = [head]
    if result.jars:
        listing = "\n".join(
            f"- {name}（{size} 字节）" for name, size in result.jars
        )
        parts.append("产物：\n" + listing)
    elif result.ok:
        parts.append("没有在 build/libs 里找到 jar；如果这是运行或测试任务，可以忽略。")
    output = (result.output or "").strip()
    if output:
        if not result.ok:
            # 编译失败：走结构化反馈引擎，把错误精确定位到「文件:行 + 修复提示 +
            # 候选符号」，而不是把上百 KB 日志整段丢给模型猜。
            try:
                structured = compile_feedback.analyze_failures(output, root=root)
            except Exception:  # noqa: BLE001 - 结构化失败回退到原始日志
                structured = ""
            if structured:
                parts.append(structured)
                tail = output[-8000:]
                if len(output) > len(tail):
                    tail = "（原始日志尾部，供需要时核对）\n" + tail
                parts.append(tail)
            else:
                limit = MAX_BUILD_RESULT
                tail = output[-limit:]
                if len(output) > len(tail):
                    tail = "（日志较长，只保留末尾部分）\n" + tail
                parts.append(f"Gradle 日志：\n{tail}")
        else:
            # 编译成功：附一份「契约快照」（当前已声明的类 + 注册 id），让模型
            # 写下一个类时引用一致，避免大工程里类名/注册 id 前后对不上。
            try:
                snapshot = compile_feedback.success_symbols(root)
            except Exception:  # noqa: BLE001
                snapshot = ""
            if snapshot:
                parts.append(snapshot)
            limit = MAX_BUILD_SUCCESS
            tail = output[-limit:]
            if len(output) > len(tail):
                tail = "（日志较长，只保留末尾部分）\n" + tail
            parts.append(f"Gradle 日志：\n{tail}")
    if result.message:
        parts.append(f"启动信息：{result.message}")
    return "\n\n".join(parts)[:MAX_TOOL_RESULT]


def _tool_status(call: ToolCall) -> str:
    if call.name in {"read_file", "write_file"}:
        return f"{call.name} {call.args.get('path', '')}"
    if call.name in {"search_text", "search_knowledge"}:
        return f"{call.name} {call.args.get('query', '')}"
    if call.name == "fetch_url":
        return f"fetch_url {call.args.get('url', '')}"
    if call.name == "build_project":
        task = str(call.args.get("task") or "build")
        return f"正在执行 Gradle {task}…"
    if call.name == "mcdev_prepare":
        return f"正在启动开发端（{call.args.get('kind') or 'both'}）…"
    if call.name == "mcdev_scenario":
        return "正在执行场景卡片…"
    return f"执行 {call.name}…"


def apply_pending(project: project_mod.Project, pending: list[PendingFile]) -> list[str]:
    written: list[str] = []
    for item in pending:
        if item.applied:
            continue
        target = project_mod.safe_path(project.root, item.path)
        relative = target.relative_to(project.root.resolve()).as_posix()
        project_mod.write_text(project.root, relative, item.content)
        written.append(relative)
    return written
