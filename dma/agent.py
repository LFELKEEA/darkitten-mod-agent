from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from . import ai, builder, connectors, deps, project as project_mod, toolchain, wrapper
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

TOOL_FENCE_RE = re.compile(r"```(?:tool|dma-tool)[ \t]*\n(.*?)\n```", re.S)
TOOL_TAG_RE = re.compile(r"<tool>\s*(\{.*?\})\s*</tool>", re.S)
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


class AgentError(Exception):
    pass


@dataclass
class PendingFile:
    path: str
    content: str
    reason: str = ""

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


def parse_tool_calls(text: str) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for match in TOOL_FENCE_RE.finditer(text or ""):
        raw = match.group(1).strip()
        calls.extend(_parse_call_payload(raw))
    for match in TOOL_TAG_RE.finditer(text or ""):
        calls.extend(_parse_call_payload(match.group(1).strip()))
    return calls


def _parse_call_payload(raw: str) -> list[ToolCall]:
    try:
        data = json.loads(raw)
    except ValueError:
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
    cleaned = TOOL_FENCE_RE.sub("", text or "")
    cleaned = TOOL_TAG_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


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
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        messages.extend(self.history[-self.config.context_turns * 2:])
        messages.append({"role": "user", "content": self.user_text})
        final_text = ""
        run = AgentRun()
        limit = max(0, int(self.config.max_steps or 0))
        step = 0

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
                final_text = visible or reply.strip()
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
                self.status.emit(f"自动写入失败 {relative}：{exc}")
            else:
                self.applied.append(relative)
                run.applied = list(self.applied)
                self.status.emit(f"已自动写入 {relative}")
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
        try:
            if name == "list_files":
                root = self.project.root
                return _list_files(
                    root,
                    str(args.get("path") or "."),
                    int(args.get("depth") or 2),
                )
            if name == "read_file":
                text = project_mod.read_text(
                    self.project.root,
                    str(args.get("path") or ""),
                    max_chars=int(args.get("max_chars") or 14000),
                )
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
                state = "已自动写入" if self.config.auto_apply else "已加入待写入列表"
                return f"{state}：{item.path}（{len(item.content)} 字符）"
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
                return _build_report(result)
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
                "read_skill、remember、recall、search_knowledge、read_knowledge、"
                "decompile_jar、build_project、fix_build_setup、fetch_url。"
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
        target = project_mod.safe_path(project.root, item.path)
        relative = target.relative_to(project.root.resolve()).as_posix()
        project_mod.write_text(project.root, relative, item.content)
        written.append(relative)
    return written
