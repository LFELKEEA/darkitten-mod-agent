from __future__ import annotations

import json
import os
import re
import struct
import threading
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from . import ai, builder, connectors, deps, project as project_mod, toolchain, wrapper
from . import spec as spec_mod
from . import workflow
from .config import Config
from .knowledge import (
    KnowledgeStore,
    decompile_jar_sources,
    knowledge_context,
    write_decompiled_tree,
)
from .memory import MemoryStore, memory_context
from .prompt import build_system_prompt
from .skills import SkillStore

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
MAX_BUILD_RESULT = 24_000
MAX_BUILD_SUCCESS = 4_000
MAX_FENCE_FILES = 40
BUILD_TRIGGER_EXTS = {
    ".java", ".kt", ".kts", ".gradle", ".properties", ".json", ".json5",
    ".toml", ".xml", ".mcmeta", ".lang", ".yml", ".yaml", ".glsl", ".fsh",
    ".vsh", ".cfg", ".conf",
}


def _project_has_sources(root: Path) -> bool:
    """已有 build.gradle 或 Java 源码的工程按“存量工程”处理，不强制先做规格。"""
    base = Path(root)
    if any((base / name).is_file() for name in ("build.gradle", "build.gradle.kts")):
        return True
    source = base / "src"
    if source.is_dir():
        try:
            return any(source.rglob("*.java"))
        except OSError:
            return False
    return False


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
    """识别“我先读 / 让我看看 / 接下来检查”这类只写计划、没有工具调用的回复。

    只看最后一段，不再限制整条回复的长度：模型经常先列三五个问题，
    最后用一句“先读二个文件”收尾，然后整轮就停住了。
    """
    body = (text or "").strip()
    if not body:
        return False
    tail = body[-240:]
    if re.search(r"已经|已读取|已查看|已完成|已修改|已修复|总结如下|结论如下", tail):
        return False
    return bool(INTENT_MARKER_RE.search(tail))


HISTORY_CHAR_BUDGET = 120_000
MESSAGE_CHAR_BUDGET = 480_000


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
    """把本轮较早的大块工具结果折叠掉，保留最近几轮完整内容。

    单次工具结果可以有 20 万字符，多轮读写堆起来会超过模型上下文。
    这里只压缩旧的工具结果，不动系统提示和最近的对话。
    """
    total = sum(len(str(item.get("content") or "")) for item in messages)
    if total <= MESSAGE_CHAR_BUDGET:
        return
    limit = max(0, len(messages) - 1)
    for index in range(2, limit):
        if total <= MESSAGE_CHAR_BUDGET:
            break
        item = messages[index]
        content = str(item.get("content") or "")
        if item.get("role") != "user" or not content.startswith("[工具结果："):
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
    """解析模型写坏了的 JSON：字符串值里的双引号没有转义。

    edit_file 的 old / new 直接放 Java 代码时很常见，标准 json.loads 会失败。
    这里按顶层 `"key":` 切成字段，值里的引号和换行原样保留。
    """
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


def _depth_ok(path: Path, root: Path, depth: int) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return len(relative.parts) <= max(1, depth)


def _list_files(root: Path, path: str, depth: int) -> str:
    base = project_mod.safe_path(root, path or ".")
    if not base.is_dir():
        return f"不是目录：{path}"
    rows: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in project_mod.SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            item = Path(current) / name
            if not _depth_ok(item, base, depth):
                continue
            rows.append(item.relative_to(base).as_posix())
            if len(rows) >= 300:
                return "\n".join(rows) + "\n…（已截断）"
    return "\n".join(rows) if rows else "（目录为空）"


class AgentWorker(QObject):
    status = Signal(str)
    assistant = Signal(str)
    pending_changed = Signal(object)
    spec_changed = Signal(object)
    workflow_changed = Signal(object)
    artifact_changed = Signal(object)
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
        stage: str = "",
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.user_text = user_text
        self.history = [dict(item) for item in history]
        self.project = project
        self.stage = stage
        self.config = config
        self.skill_store = skill_store
        self.memory = memory
        self.knowledge = knowledge
        self.connector_store = connector_store
        self.pending: list[PendingFile] = []
        self.applied: list[str] = []
        self._plan_submitted = False
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

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
        try:
            run = self._loop()
        except Exception as exc:
            if not self.cancelled:
                self.failed.emit(str(exc))
        else:
            if not self.cancelled:
                self.done.emit(run.text)
        finally:
            self.finished.emit()

    def _loop(self) -> AgentRun:
        project = self.project
        project_key = str(project.root) if project is not None else ""
        current_spec = spec_mod.load_spec(project.root) if project is not None else None
        state = workflow.load_state(project.root, current_spec) if project is not None else None
        if self.stage:
            if state is None:
                state = workflow.WorkflowState(stage=self.stage)
            else:
                state.stage = self.stage if self.stage in workflow.STAGES else state.stage
        elif current_spec is not None or (state is not None and state.stage != "draft"):
            self.stage = state.stage if state is not None else "review"
        elif project is not None and _project_has_sources(project.root):
            # 老工程没有规格文件时不要卡在规划阶段，直接按增量修改处理。
            self.stage = "done"
            if state is not None:
                state.stage = "done"
        else:
            self.stage = "planning"
            if state is not None:
                state.stage = "planning"
        if project is not None:
            # 每次开工都从磁盘取，避免上一条消息里的规格已经被改过。
            workflow.save_state(project.root, state)
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
        system = build_system_prompt(
            project,
            self.skill_store,
            memory_context(memory_items),
            self.connector_store.catalog_text(),
            max_steps=self.config.max_steps,
            knowledge_text=knowledge_context(knowledge_hits),
            query=self.user_text,
            spec=current_spec,
            workflow=state,
            stage=self.stage,
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(_history_window(self.history, self.config.context_turns))
        messages.append({"role": "user", "content": self.user_text})
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
        plan_submitted = False

        while True:
            if limit > 0 and step >= limit:
                break
            if self.cancelled:
                raise AgentError("已取消")
            step += 1
            run.steps = step
            if limit <= 0:
                self.status.emit(f"正在请求模型（第 {step} 轮，不限制）…")
            else:
                self.status.emit(f"正在请求模型（第 {step}/{limit} 轮）…")
            reply = ai.request_chat(self.config.ai, messages)
            calls = parse_tool_calls(reply)

            visible = strip_tool_blocks(reply)
            for item in extract_file_blocks(visible or reply):
                self._stage_file(item, run)

            if not calls:
                if broken_tool_retries < 2 and _looks_like_tool_attempt(reply):
                    # 模型偶尔会漏掉 > 或闭合标签，任务会看起来“突然结束”。
                    # 不发出去当最终答复，让它按完整格式重写一次。
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
                    # 这里要求它把计划变成实际调用，最多催两次，避免死循环。
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
                if auto_builds < 2 and self._should_auto_build(run, applied_mark):
                    auto_builds += 1
                    applied_mark = len(run.applied)
                    ok, report = self._auto_build()
                    base = (visible or reply.strip()).strip()
                    if ok:
                        final_text = (base + "\n\n自动编译通过。\n" + report).strip()
                        run.text = final_text
                        return run
                    if auto_builds >= 2:
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
                run.text = final_text
                return run

            if visible:
                self.assistant.emit(visible)

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
            _compact_messages(messages)
            plan_submitted = self._plan_submitted
            if plan_submitted:
                # 规划提交后必须停下来等用户确认，不能顺势开始写代码。
                run.text = (
                    visible.strip()
                    or "规格已生成。请检查右侧说明书，确认后才会开始施工。"
                )
                return run

        final_text = final_text or "已达到工具调用轮数上限。请继续发送消息，让我接着完成剩余部分。"
        run.text = final_text
        return run

    def _stage_file(self, item: PendingFile, run: AgentRun) -> bool:
        if self.project is None:
            return False
        try:
            target = project_mod.safe_path(self.project.root, item.path)
        except project_mod.ProjectError as exc:
            self.status.emit(f"跳过不安全路径 {item.path}：{exc}")
            return False
        relative = target.relative_to(self.project.root.resolve()).as_posix()
        staged = PendingFile(path=relative, content=item.content, reason=item.reason)
        if self.config.auto_apply:
            try:
                project_mod.write_text(self.project.root, relative, item.content)
            except Exception as exc:
                self.status.emit(f"自动写入失败 {relative}：{exc}，已放入待写入列表")
            else:
                self.applied.append(relative)
                run.applied = list(self.applied)
                self.status.emit(f"已自动写入 {relative}")
                # 自动写入也要在面板里留记录，否则用户只会看到“0 个”，无法确认写过什么。
                staged.applied = True
                self._add_pending(staged)
                run.pending = list(self.pending)
                return True
        self._add_pending(staged)
        run.pending = list(self.pending)
        return True

    def _known_jar_dirs(self) -> list[Path]:
        """知识库里导入过的 JAR 所在目录，找 libs/ 依赖时优先看这些地方。"""
        if self.knowledge is None:
            return []
        try:
            documents = self.knowledge.list_documents(limit=500)
        except Exception:  # noqa: BLE001 - 读不到知识库不影响构建
            return []
        dirs: list[Path] = []
        seen: set[str] = set()
        for item in documents:
            source = str(item.get("source") or "").strip()
            if not source:
                continue
            path = Path(source)
            try:
                if not path.is_file():
                    continue
                folder = path.parent
                key = str(folder.resolve()).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            dirs.append(folder)
        return dirs

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
        try:
            result = builder.build_project(
                self.project.root,
                "build",
                extra_jar_dirs=self._known_jar_dirs(),
                log=self._build_log,
                timeout=900,
                cancel=self._cancel,
            )
        except builder.BuilderError as exc:
            return False, f"自动编译没有开始：{exc}"
        except Exception as exc:  # noqa: BLE001 - 自动编译失败不应吞掉本轮结果
            return False, f"自动编译出错：{exc}"
        report = _build_report(result)
        if result.ok:
            report += self._finalize_artifact()
        else:
            self._record_build_failure()
        return result.ok, report

    def _finalize_artifact(self) -> str:
        """编译成功后读取真实 JAR，生成成品说明书并刷新右侧面板。"""
        if self.project is None:
            return ""
        try:
            current = spec_mod.load_spec(self.project.root)
            state, inventory, _manual = workflow.finalize_build(
                self.project.root, True, current
            )
        except Exception as exc:  # noqa: BLE001 - 说明书失败不影响编译结果
            return f"\n\n成品说明书生成失败：{exc}"
        self.stage = state.stage
        self.spec_changed.emit(current)
        self.workflow_changed.emit(state)
        self.artifact_changed.emit(
            {
                "jar": inventory.jar,
                "summary": inventory.summary(),
                "inventory": inventory.to_dict(),
                "manual": "MANUAL.md",
            }
        )
        self.status.emit("已读取最终 JAR，生成成品说明书")
        if inventory.jar:
            return (
                "\n\n【成品巡检】\n"
                f"- JAR：{inventory.jar}\n"
                f"- 注册内容：{inventory.content_count()} 个"
                f"（物品 {len(inventory.items)} / 方块 {len(inventory.blocks)} / "
                f"实体 {len(inventory.entities)} / 命令 {len(inventory.commands)}）\n"
                f"- 配方：{len(inventory.recipes)} 个\n"
                f"- 纹理：{len(inventory.textures)} 张\n"
                "已生成 MANUAL.md。"
            )
        return "\n\n编译通过，但 build/libs 里没有找到可巡检的 jar。"

    def _record_build_failure(self) -> None:
        if self.project is None:
            return
        try:
            current = spec_mod.load_spec(self.project.root)
            state = workflow.load_state(self.project.root, current)
            state.last_build_ok = False
            if current is not None and current.confirmed():
                state.stage = "building"
            workflow.save_state(self.project.root, state)
            self.stage = state.stage
            self.workflow_changed.emit(state)
        except Exception:
            pass

    def _execute(self, call: ToolCall, run: AgentRun) -> str:
        name = call.name.strip().lower()
        args = call.args
        allowed_without_project = {
            "read_skill",
            "remember",
            "recall",
            "fetch_url",
            "search_knowledge",
            "read_knowledge",
        }
        if self.project is None and name not in allowed_without_project:
            return "当前没有打开工程，无法执行文件操作。请先在界面创建或打开工程。"
        if self.stage in {"draft", "planning", "review"} and name in {
            "write_file",
            "edit_file",
            "replace_in_file",
            "apply_patch",
            "pixel_art",
            "build_project",
            "fix_build_setup",
            "decompile_jar",
        }:
            return (
                "规格还没有确认，现在不能修改工程。请先把完整规格提交给用户，"
                "等用户点「确认规格并开始施工」后再写代码。"
            )
        try:
            if name == "list_files":
                root = self.project.root
                return _list_files(
                    root,
                    str(args.get("path") or "."),
                    int(args.get("depth") or 2),
                )
            if name == "read_file":
                try:
                    offset = max(0, int(args.get("offset") or 0))
                except (TypeError, ValueError):
                    offset = 0
                try:
                    window = int(args.get("max_chars") or 14000)
                except (TypeError, ValueError):
                    window = 14000
                window = max(1000, min(MAX_TOOL_RESULT, window))
                text = project_mod.read_text(
                    self.project.root,
                    str(args.get("path") or ""),
                    max_chars=min(MAX_TOOL_RESULT, offset + window),
                )
                if offset:
                    if offset >= len(text):
                        return f"offset={offset} 超过可读长度（{len(text)} 字符）。"
                    text = text[offset:]
                return text[:MAX_TOOL_RESULT]
            if name == "search_text":
                hits = project_mod.search_text(
                    self.project.root,
                    str(args.get("query") or ""),
                    max_results=int(args.get("limit") or 40),
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
                try:
                    data, width, height = _encode_pixel_png(palette, rows)
                except ValueError as exc:
                    return f"pixel_art 参数错误：{exc}"
                if self.project is None:
                    return "当前没有打开工程。"
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
            if name in {"edit_file", "replace_in_file", "apply_patch"}:
                relative = str(args.get("path") or "").strip()
                old = str(args.get("old") or args.get("find") or "")
                if args.get("new") is not None:
                    new = str(args.get("new"))
                else:
                    new = str(args.get("replacement") or "")
                if not relative:
                    return "edit_file 缺少 path。"
                if not old:
                    return "edit_file 缺少 old（要替换的原文）。"
                try:
                    current = project_mod.read_text(
                        self.project.root, relative, max_chars=2 * 1024 * 1024
                    )
                except Exception as exc:  # noqa: BLE001 - 文件不存在或太大
                    return f"读取失败：{exc}"
                if "…（已截断" in current:
                    return "文件太大，edit_file 需要完整内容，请改用 write_file 重写整份文件。"
                if old not in current:
                    return (
                        f"没有在 {relative} 里找到要替换的原文。"
                        "请先用 read_file 确认内容，old 必须和文件里的字符完全一致。"
                    )
                updated = current.replace(old, new, 1)
                item = PendingFile(path=relative, content=updated, reason="edit")
                if not self._stage_file(item, run):
                    return f"修改失败：路径不合法（{relative}）。"
                written = any(
                    entry.applied
                    for entry in self.pending
                    if entry.path == item.path.replace("\\", "/")
                )
                state = "已自动写入" if written else "已加入待写入列表"
                delta = len(updated) - len(current)
                return f"{state}：{relative}（替换 1 处，{delta:+d} 字符）"
            if name in {"submit_spec", "submit_plan"}:
                payload = args.get("spec")
                if not isinstance(payload, dict):
                    payload = {
                        key: value
                        for key, value in args.items()
                        if key not in {"tool", "name"}
                    }
                existing = spec_mod.load_spec(self.project.root)
                try:
                    created = spec_mod.parse_spec(
                        payload, project=self.project, base=existing
                    )
                except (ValueError, TypeError) as exc:
                    return f"规格格式不正确：{exc}"
                created.status = "draft"
                created.revision = (existing.revision + 1) if existing else 1
                if not created.title:
                    created.title = self.project.meta.name or self.project.meta.mod_id
                spec_mod.save_spec(self.project.root, created)
                state = workflow.load_state(self.project.root, created)
                state.stage = "review"
                state.spec_revision = created.revision
                state.tasks = []
                state.jar = ""
                state.manual = ""
                workflow.save_state(self.project.root, state)
                self.stage = "review"
                self._plan_submitted = True
                self.spec_changed.emit(created)
                self.workflow_changed.emit(state)
                passed, total = spec_mod.checklist_score(created)
                missing = spec_mod.missing_items(created)
                tail = (
                    "\n缺少：" + "、".join(missing)
                    if missing
                    else "\n10 项需求已全部覆盖。"
                )
                return (
                    f"规格已保存（第 {created.revision} 版），需求 {passed}/{total}。"
                    f"{tail}\n用户确认后才会开始施工。"
                )
            if name == "update_spec":
                payload = args.get("spec")
                if not isinstance(payload, dict):
                    payload = {
                        key: value
                        for key, value in args.items()
                        if key not in {"tool", "name"}
                    }
                existing = spec_mod.load_spec(self.project.root)
                if existing is None:
                    return "还没有规格，请先用 submit_spec 提交完整规格。"
                try:
                    updated = spec_mod.apply_patch(
                        existing, payload, project=self.project
                    )
                except (ValueError, TypeError) as exc:
                    return f"规格格式不正确：{exc}"
                changes = spec_mod.diff_spec(existing, updated)
                if existing.confirmed():
                    updated.status = "confirmed"
                    state = workflow.load_state(self.project.root, updated)
                    state.stage = "building"
                    state.spec_revision = updated.revision
                    if not state.tasks:
                        state.tasks = workflow.plan_tasks(updated)
                    self.stage = "building"
                else:
                    updated.status = "draft"
                    state = workflow.load_state(self.project.root, updated)
                    state.stage = "review"
                    state.spec_revision = updated.revision
                    self.stage = "review"
                    self._plan_submitted = True
                spec_mod.save_spec(self.project.root, updated)
                workflow.save_state(self.project.root, state)
                self.spec_changed.emit(updated)
                self.workflow_changed.emit(state)
                passed, total = spec_mod.checklist_score(updated)
                return (
                    f"规格已更新到第 {updated.revision} 版，需求 {passed}/{total}。\n"
                    + "\n".join(f"- {line}" for line in changes)
                    + (
                        "\n用户确认后开始施工。"
                        if updated.status == "draft"
                        else "\n已保留现有工程，按新规格继续施工。"
                    )
                )
            if name in {"complete_task", "finish_task"}:
                task_id = str(
                    args.get("id") or args.get("task") or args.get("task_id") or ""
                ).strip()
                if not task_id:
                    return "complete_task 缺少 id。"
                state = workflow.load_state(
                    self.project.root, spec_mod.load_spec(self.project.root)
                )
                known = {item.get("id") for item in state.tasks}
                if task_id not in known:
                    return (
                        f"任务清单里没有 {task_id}。可用："
                        + "、".join(sorted(str(item) for item in known))
                    )
                workflow.complete_task(self.project.root, state, task_id)
                self.workflow_changed.emit(state)
                done, total = state.task_counts()
                remaining = [
                    item.get("id")
                    for item in state.tasks
                    if item.get("status") != "done"
                ]
                nxt = ("；下一项：" + remaining[0]) if remaining else "；全部完成"
                return f"任务 {task_id} 已完成，进度 {done}/{total}{nxt}。"
            if name in {"start_task", "begin_task"}:
                task_id = str(args.get("id") or args.get("task") or "").strip()
                if not task_id:
                    return "start_task 缺少 id。"
                state = workflow.load_state(
                    self.project.root, spec_mod.load_spec(self.project.root)
                )
                workflow.start_task(self.project.root, state, task_id)
                self.workflow_changed.emit(state)
                return f"开始任务：{task_id}"
            if name == "inspect_artifact":
                inventory = workflow.inspect_artifacts(self.project.root)
                current = spec_mod.load_spec(self.project.root)
                manual = workflow.render_artifact_manual(current, inventory)
                try:
                    (self.project.root / "MANUAL.md").write_text(
                        manual, encoding="utf-8", newline="\n"
                    )
                except OSError as exc:
                    return f"成品说明书写入失败：{exc}"
                state = workflow.load_state(self.project.root, current)
                if inventory.jar:
                    state.jar = inventory.jar
                    state.manual = "MANUAL.md"
                    state.stage = "done"
                    self.stage = "done"
                    workflow.save_state(self.project.root, state)
                self.artifact_changed.emit(
                    {
                        "jar": inventory.jar,
                        "summary": inventory.summary(),
                        "inventory": inventory.to_dict(),
                        "manual": "MANUAL.md",
                    }
                )
                self.workflow_changed.emit(state)
                if not inventory.jar:
                    return (
                        "build/libs 里还没有 jar。先让编译通过，再读取最终产物。\n\n"
                        + manual[:4000]
                    )
                return (
                    f"成品巡检完成：{inventory.summary()}\n"
                    f"JAR：{inventory.jar}\n已写入 MANUAL.md。\n\n"
                    + manual[:12000]
                )
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
                    limit=int(args.get("limit") or 8),
                )
                return memory_context(items) or "没有找到相关记忆。"
            if name == "search_knowledge":
                if self.knowledge is None:
                    return "知识库不可用。"
                project_key = str(self.project.root) if self.project else ""
                hits = self.knowledge.search(
                    str(args.get("query") or ""),
                    project=project_key,
                    limit=int(args.get("limit") or 8),
                    budget=int(args.get("budget") or MAX_TOOL_RESULT),
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
                try:
                    limit = float(args.get("timeout") or 1800)
                except (TypeError, ValueError):
                    limit = 1800.0
                limit = min(max(limit, 60.0), 3600.0)
                try:
                    result = builder.build_project(
                        self.project.root,
                        task,
                        extra_jar_dirs=self._known_jar_dirs(),
                        log=self._build_log,
                        timeout=limit,
                        cancel=self._cancel,
                    )
                except builder.BuilderError as exc:
                    return f"编译没有开始：{exc}"
                report = _build_report(result)
                if task == "build":
                    if result.ok:
                        report += self._finalize_artifact()
                    else:
                        self._record_build_failure()
                return report
            if name == "fix_build_setup":
                root = self.project.root
                report: list[str] = []
                try:
                    written = toolchain.ensure_wrapper_files(root)
                    if written:
                        report.append("补齐 Wrapper 文件：" + "、".join(written))
                except Exception as exc:  # noqa: BLE001 - 逐项报告
                    report.append(f"补齐 Wrapper 失败：{exc}")
                try:
                    changed, before, after, reason = wrapper.ensure_compatible(root)
                    if changed:
                        report.append(
                            f"Gradle Wrapper：{before or '缺失/损坏'} → {after}（{reason}）"
                        )
                    else:
                        report.append(
                            f"Gradle Wrapper 保持 {after or before or '当前配置'}，无需调整"
                        )
                except Exception as exc:  # noqa: BLE001 - 逐项报告
                    report.append(f"调整 Gradle 版本失败：{exc}")
                try:
                    jars = deps.ensure_local_jars(root)
                    report.append(jars.summary())
                except Exception as exc:  # noqa: BLE001 - 逐项报告
                    report.append(f"整理本地 jar 失败：{exc}")
                return "\n".join(report)
            if name == "fetch_url":
                url = str(args.get("url") or "").strip()
                if not url:
                    return "fetch_url 缺少 url。"
                return connectors.fetch_url(url)[:MAX_FETCH_RESULT]
            return (
                f"未知工具：{name}。可用工具：list_files、read_file、search_text、write_file、"
                "edit_file、pixel_art、submit_spec、update_spec、start_task、complete_task、"
                "inspect_artifact、read_skill、remember、recall、search_knowledge、"
                "read_knowledge、decompile_jar、build_project、fix_build_setup、fetch_url。"
            )
        except Exception as exc:
            return f"工具执行失败：{exc}"


def _build_report(result: builder.BuildResult) -> str:
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
        parts.append("没有在 build/libs 里找到 jar；如果这是数据生成或运行任务，可以忽略。")
    output = (result.output or "").strip()
    if output:
        limit = MAX_BUILD_RESULT if not result.ok else MAX_BUILD_SUCCESS
        tail = output[-limit:]
        if len(output) > len(tail):
            tail = "（日志较长，只保留末尾部分）\n" + tail
        parts.append(f"Gradle 日志：\n{tail}")
    if result.message:
        parts.append(f"启动信息：{result.message}")
    if not result.ok:
        try:
            parts.append(
                workflow.analyze_build_failure(result.output, result.code).render()
            )
        except Exception:
            pass
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
