from __future__ import annotations

import math
import re
import sqlite3
import threading
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from . import paths

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    size INTEGER NOT NULL DEFAULT 0,
    chars INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_project ON documents(project);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL,
    ord INTEGER NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id);
"""

CHUNK_SIZE = 1200
CHUNK_OVERLAP = 150
MAX_DOC_CHARS = 1_500_000
MAX_XML_BYTES = 40 * 1024 * 1024
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".json", ".json5", ".yaml", ".yml", ".toml",
    ".properties", ".gradle", ".java", ".kt", ".kts", ".py", ".js", ".ts",
    ".html", ".htm", ".xml", ".csv", ".cfg", ".conf", ".lang", ".mcmeta",
    ".mcfunction", ".accesswidener",
}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".docx", ".pdf", ".jar"}
SUPPORTED_FILTER = (
    "文档与模组 (*.md *.markdown *.txt *.docx *.pdf *.jar *.json *.yaml *.yml "
    "*.toml *.properties *.gradle *.java *.kt *.py *.html *.xml *.csv);;"
    "模组 JAR (*.jar);;所有文件 (*.*)"
)

JAR_METADATA = {
    "fabric.mod.json",
    "quilt.mod.json",
    "mcmod.info",
    "pack.mcmeta",
    "meta-inf/mods.toml",
    "meta-inf/neoforge.mods.toml",
    "meta-inf/manifest.mf",
}
MAX_JAR_CLASSES = 500
MAX_JAR_TEXT_ENTRIES = 400
MAX_JAR_ENTRY_BYTES = 4 * 1024 * 1024
MAX_JAR_READ_BYTES = 32 * 1024 * 1024
MAX_JAR_ENTRY_CHARS = 200_000
MAX_CLASS_CHARS = 6_000

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_.$#@-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "you", "are", "was", "but",
    "not", "have", "from", "they", "will", "can", "all", "any", "how", "why",
    "what", "when", "where", "which", "use", "using", "code", "file", "please",
    "into", "your", "our", "its", "his", "her", "them", "than", "then", "there",
}


class KnowledgeError(Exception):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text
    raise KnowledgeError("文件不是可识别的文本，可能是二进制格式。")


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _docx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_XML_BYTES:
                raise KnowledgeError("Word 文档内容过大，已拒绝解析。")
            raw = archive.read("word/document.xml")
    except KeyError as exc:
        raise KnowledgeError("这个 .docx 里没有正文（word/document.xml）。") from exc
    except zipfile.BadZipFile as exc:
        raise KnowledgeError("Word 文档损坏，无法读取。") from exc
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise KnowledgeError("Word 文档 XML 解析失败。") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{{{_WORD_NS}}}p"):
        pieces: list[str] = []
        for node in paragraph.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag == "t" and node.text:
                pieces.append(node.text)
            elif tag == "tab":
                pieces.append("\t")
            elif tag in {"br", "cr"}:
                pieces.append("\n")
        paragraphs.append("".join(pieces))
    return "\n".join(paragraphs)


def _pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise KnowledgeError("读取 PDF 需要 pypdf，请重新安装完整版本。") from exc
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise KnowledgeError("PDF 已加密，无法读取。") from exc
        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
    except KnowledgeError:
        raise
    except OSError as exc:
        raise KnowledgeError(
            "读取 PDF 失败。如果文件在 OneDrive 等云端，请先右键选择「始终保留在此设备」再导入。"
        ) from exc
    except Exception as exc:
        raise KnowledgeError(f"PDF 解析失败：{exc}") from exc
    text = "\n\n".join(item.strip() for item in pages if item.strip())
    if not text:
        raise KnowledgeError("这个 PDF 里没有可提取的文字，可能是扫描件。")
    return text


def _html_text(path: Path) -> str:
    parser = _HTMLText()
    parser.feed(_decode(path.read_bytes()))
    return parser.text()


_CLASS_MEMBER_FLAGS = (
    (0x0001, "public"), (0x0002, "private"), (0x0004, "protected"),
    (0x0008, "static"), (0x0010, "final"), (0x0020, "synchronized"),
    (0x0040, "bridge"), (0x0080, "varargs"), (0x0100, "native"),
    (0x0400, "abstract"), (0x1000, "synthetic"),
)
_CLASS_TYPE_FLAGS = (
    (0x0001, "public"), (0x0010, "final"), (0x0200, "interface"),
    (0x0400, "abstract"), (0x1000, "synthetic"), (0x4000, "enum"),
)
_NAMESPACED_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,64}:[a-z0-9_./-]{1,80}$")


def _flags(value: int, table) -> str:
    names = [label for bit, label in table if value & bit]
    return " ".join(names)


def _dot(name: str) -> str:
    return name.replace("/", ".")


def _class_summary(raw: bytes) -> str:
    """从 .class 常量池和成员表提取结构，不依赖反编译器。"""
    try:
        view = memoryview(raw)
        pos = 0

        def take(count: int) -> bytes:
            nonlocal pos
            if count < 0 or pos + count > len(view):
                raise ValueError("truncated")
            chunk = bytes(view[pos : pos + count])
            pos += count
            return chunk

        def u2() -> int:
            return int.from_bytes(take(2), "big")

        def u4() -> int:
            return int.from_bytes(take(4), "big")

        if take(4) != b"\xca\xfe\xba\xbe":
            return ""
        u2()
        u2()
        pool_count = u2()
        utf8: dict[int, str] = {}
        class_names: dict[int, str] = {}
        name_and_types: dict[int, tuple[int, int]] = {}
        member_refs: list[tuple[int, int]] = []
        string_refs: list[int] = []
        index = 1
        while index < pool_count:
            tag = take(1)[0]
            if tag == 1:
                utf8[index] = take(u2()).decode("utf-8", "replace")
            elif tag in (3, 4):
                take(4)
            elif tag in (5, 6):
                take(8)
                index += 1
            elif tag == 7:
                class_names[index] = u2()
            elif tag == 8:
                string_refs.append(u2())
            elif tag in (9, 10, 11):
                member_refs.append((u2(), u2()))
            elif tag == 12:
                name_and_types[index] = (u2(), u2())
            elif tag in (17, 18):
                take(4)
            elif tag == 15:
                take(3)
            elif tag in (16, 19, 20):
                take(2)
            else:
                return ""
            index += 1

        access = u2()
        this_index = u2()
        super_index = u2()
        interface_count = u2()
        interfaces = [u2() for _ in range(interface_count)]

        def class_name(cp_index: int) -> str:
            if not cp_index:
                return ""
            return utf8.get(class_names.get(cp_index, -1), "")

        def members() -> list[tuple[int, str, str]]:
            count = u2()
            items: list[tuple[int, str, str]] = []
            for _ in range(count):
                flags = u2()
                name_index = u2()
                desc_index = u2()
                attribute_count = u2()
                for _ in range(attribute_count):
                    u2()
                    take(u4())
                items.append(
                    (flags, utf8.get(name_index, ""), utf8.get(desc_index, ""))
                )
            return items

        fields = members()
        methods = members()

        name = _dot(class_name(this_index))
        if not name:
            return ""
        is_interface = bool(access & 0x0200)
        modifiers = _flags(access, _CLASS_TYPE_FLAGS)
        if is_interface:
            modifiers = (
                modifiers.replace("interface", "")
                .replace("abstract", "")
                .replace("  ", " ")
                .strip()
            )
        header = f"{('interface' if is_interface else 'class')} {name}"
        super_name = _dot(class_name(super_index))
        if super_name and super_name != "java.lang.Object" and not is_interface:
            header += f" extends {super_name}"
        parents = [_dot(class_name(item)) for item in interfaces]
        parents = [item for item in parents if item]
        if parents:
            header += " implements " + ", ".join(parents)
        if modifiers:
            header = f"{modifiers} {header}"

        lines = [header]
        if fields:
            lines.append("字段：")
            for flags, field_name, desc in fields[:40]:
                mods = _flags(flags, _CLASS_MEMBER_FLAGS)
                lines.append(f"  - {(mods + ' ') if mods else ''}{field_name}: {desc}")
            if len(fields) > 40:
                lines.append(f"  - …还有 {len(fields) - 40} 个字段")
        if methods:
            lines.append("方法：")
            for flags, method_name, desc in methods[:60]:
                mods = _flags(flags, _CLASS_MEMBER_FLAGS)
                lines.append(f"  - {(mods + ' ') if mods else ''}{method_name}{desc}")
            if len(methods) > 60:
                lines.append(f"  - …还有 {len(methods) - 60} 个方法")
        refs: list[str] = []
        for cp_index in class_names:
            value = _dot(class_name(cp_index))
            if value and value not in {name, "java.lang.Object"} and value not in refs:
                refs.append(value)
        if refs:
            lines.append("引用的类：")
            lines.append("  - " + ", ".join(refs[:40]))
            if len(refs) > 40:
                lines.append(f"  - …还有 {len(refs) - 40} 个")
        calls: list[str] = []
        for owner_index, nat_index in member_refs:
            owner = _dot(class_name(owner_index))
            nat = name_and_types.get(nat_index)
            if not owner or not nat:
                continue
            member = utf8.get(nat[0], "")
            desc = utf8.get(nat[1], "")
            if not member:
                continue
            value = f"{owner}.{member}{desc}" if desc.startswith("(") else f"{owner}.{member}:{desc}"
            if value not in calls:
                calls.append(value)
        if calls:
            lines.append("调用的方法/字段：")
            for item in calls[:60]:
                lines.append(f"  - {item}")
            if len(calls) > 60:
                lines.append(f"  - …还有 {len(calls) - 60} 个")
        literals: list[str] = []
        for cp_index in string_refs:
            value = utf8.get(cp_index, "")
            if value and value not in literals:
                literals.append(value)
        for value in utf8.values():
            if _NAMESPACED_ID.match(value) and value not in literals:
                literals.append(value)
        literals = [
            item.strip()
            for item in literals
            if 3 <= len(item.strip()) <= 160
            and all(ord(char) >= 32 for char in item)
        ]
        if literals:
            lines.append("字符串常量：")
            for item in literals[:30]:
                lines.append(f'  - "{item}"')
            if len(literals) > 30:
                lines.append(f"  - …还有 {len(literals) - 30} 个")
        text = "\n".join(lines)
        if len(text) > MAX_CLASS_CHARS:
            text = text[:MAX_CLASS_CHARS] + "\n…（该类结构已截断）"
        return text
    except Exception:
        return ""


def _jar_rank(info: zipfile.ZipInfo) -> tuple[int, str]:
    low = info.filename.lower()
    if low in JAR_METADATA:
        return (0, low)
    if low.endswith(".java"):
        return (1, low)
    if low.endswith(".mixins.json") or low.endswith(".accesswidener"):
        return (2, low)
    if low.endswith(".class"):
        return (3, low)
    return (4, low)


def _jar_entry_kind(name: str) -> str:
    low = name.lower()
    if low.startswith("meta-inf/jars/") or low.startswith("meta-inf/versions/"):
        return "skip"
    if low.endswith((".sf", ".rsa", ".dsa", ".ec")):
        return "skip"
    if low.endswith(".class"):
        return "class"
    if low in JAR_METADATA or low.startswith("meta-inf/services/"):
        return "text"
    if "refmap" in low.rsplit("/", 1)[-1] and low.endswith(".json"):
        return "skip"
    if Path(low).suffix in TEXT_SUFFIXES:
        return "text"
    return "skip"


def _decode_entry(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding).replace("\x00", "")
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace").replace("\x00", "")


def _jar_text(path: Path) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise KnowledgeError("这个 .jar 无法打开，文件可能损坏。") from exc
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        infos.sort(key=_jar_rank)
        header = (
            f"# 模组 JAR：{path.name}\n"
            "说明：以下内容由程序自动提取，包括模组元数据、语言与资源文本、"
            "类结构（字段、方法、引用的类、字符串常量）以及包内源码。"
            "这不是完整反编译源码，复杂逻辑需要结合类名和调用关系推断。"
        )
        parts = [header]
        used = len(header)
        classes = 0
        text_entries = 0
        omitted = 0
        skipped = 0
        nested = 0
        read_bytes = 0
        truncated = False
        for info in infos:
            name = info.filename
            low = name.lower()
            kind = _jar_entry_kind(name)
            if kind == "skip":
                if low.startswith("meta-inf/jars/"):
                    nested += 1
                skipped += 1
                continue
            if info.flag_bits & 0x1:
                skipped += 1
                continue
            if kind == "class" and classes >= MAX_JAR_CLASSES:
                omitted += 1
                continue
            if kind == "text" and text_entries >= MAX_JAR_TEXT_ENTRIES:
                omitted += 1
                continue
            if info.file_size > MAX_JAR_ENTRY_BYTES:
                skipped += 1
                continue
            if read_bytes + info.file_size > MAX_JAR_READ_BYTES:
                omitted += 1
                continue
            try:
                raw = archive.read(info)
            except (RuntimeError, OSError, ValueError, NotImplementedError, zipfile.BadZipFile):
                skipped += 1
                continue
            read_bytes += len(raw)
            if kind == "class":
                body = _class_summary(raw)
                if not body:
                    skipped += 1
                    continue
                classes += 1
                title = f"## 类：{_dot(name[:-6])}"
            else:
                body = _decode_entry(raw)
                if not body.strip():
                    continue
                text_entries += 1
                limit = 8_000 if low == "meta-inf/manifest.mf" else MAX_JAR_ENTRY_CHARS
                if len(body) > limit:
                    body = body[:limit] + "\n…（该文件已截断）"
                title = f"## 文件：{name}"
            block = title + "\n" + body.strip()
            if used + len(block) + 2 > MAX_DOC_CHARS:
                remain = MAX_DOC_CHARS - used - 2
                if remain > 200:
                    parts.append(block[:remain] + "\n（内容超过上限，已截断）")
                truncated = True
                break
            parts.append(block)
            used += len(block) + 2
        notes: list[str] = []
        if truncated:
            notes.append("内容超过单份资料上限，后面的条目未展开。")
        if omitted:
            notes.append(f"另有 {omitted} 个条目因数量或体积上限未展开。")
        if nested:
            notes.append(f"已跳过 {nested} 个内嵌 jar（通常是依赖库）。")
        if skipped:
            notes.append(f"已跳过 {skipped} 个二进制、签名或损坏条目。")
        if classes == 0 and text_entries == 0:
            raise KnowledgeError("这个 .jar 里没有可提取的文本或类结构，可能只是资源包或空包。")
        if notes:
            parts.append("---\n" + "\n".join(notes))
        return "\n\n".join(parts)


def extract_text(path: Path) -> tuple[str, str]:
    """返回（正文, 类型）。支持 txt/md/docx/pdf 和常见代码/配置文件。"""
    source = Path(path)
    if not source.is_file():
        raise KnowledgeError(f"文件不存在：{source}")
    suffix = source.suffix.lower()
    if suffix == ".docx":
        text = _docx_text(source)
    elif suffix == ".pdf":
        text = _pdf_text(source)
    elif suffix == ".jar":
        text = _jar_text(source)
    elif suffix in {".html", ".htm"}:
        text = _html_text(source)
    elif suffix in TEXT_SUFFIXES or suffix == "":
        text = _decode(source.read_bytes())
    else:
        raise KnowledgeError(
            f"暂不支持 {suffix or '无扩展名'} 文件，可导入 txt / md / docx / pdf / jar 或代码文本。"
        )
    text = _normalize(text)
    if not text.strip():
        raise KnowledgeError("文件里没有可读取的文字。")
    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS] + "\n\n（内容超过上限，已截断）"
    return text, suffix.lstrip(".") or "text"


def _normalize(text: str) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def split_chunks(text: str) -> list[str]:
    text = _normalize(text)
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(length, start + CHUNK_SIZE)
        if end < length:
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind("。"), window.rfind("\n"))
            if cut > CHUNK_SIZE // 2:
                end = start + cut + 1
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - CHUNK_OVERLAP, start + 1)
    return chunks


def _tokens(query: str) -> set[str]:
    text = str(query or "").lower()
    tokens: set[str] = {match.group(0) for match in _ASCII_TOKEN.finditer(text)}
    for match in _CJK_RUN.finditer(text):
        run = match.group(0)
        if len(run) <= 6:
            tokens.add(run)
        for index in range(len(run) - 1):
            tokens.add(run[index : index + 2])
        for index in range(len(run) - 2):
            tokens.add(run[index : index + 3])
    return {
        token
        for token in tokens
        if len(token) >= 2 and token not in _STOPWORDS
    }


def _score(name: str, body: str, tokens: set[str]) -> float:
    lowered = body.lower()
    score = 0.0
    for token in tokens:
        count = lowered.count(token)
        if count:
            score += min(count, 4) * (1 + min(len(token), 6) * 0.5)
    name_lower = name.lower()
    for token in tokens:
        if token in name_lower:
            score += 5
    if score <= 0:
        return 0.0
    return score / math.sqrt(max(120, len(body)))


class KnowledgeStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or paths.knowledge_path())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def add_file(self, path: Path, *, project: str = "") -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        text, kind = extract_text(source)
        try:
            size = source.stat().st_size
        except OSError:
            size = 0
        return self.add_text(
            source.name,
            text,
            project=project,
            source=str(source),
            kind=kind,
            size=size,
        )

    def add_text(
        self,
        name: str,
        text: str,
        *,
        project: str = "",
        source: str = "",
        kind: str = "text",
        size: int = 0,
    ) -> dict[str, Any]:
        title = str(name or "").strip() or "未命名资料"
        body = _normalize(text)
        if not body:
            raise KnowledgeError("资料内容为空。")
        if len(body) > MAX_DOC_CHARS:
            body = body[:MAX_DOC_CHARS] + "\n\n（内容超过上限，已截断）"
        pieces = split_chunks(body)
        stamp = _now()
        with self._lock, self._conn:
            document_id = 0
            if source:
                row = self._conn.execute(
                    "SELECT id, created_at FROM documents WHERE project=? AND source=?",
                    (str(project or ""), str(source)),
                ).fetchone()
                if row:
                    document_id = int(row["id"])
            if document_id:
                self._conn.execute(
                    "UPDATE documents SET name=?, kind=?, size=?, chars=?, updated_at=? WHERE id=?",
                    (title, kind, int(size), len(body), stamp, document_id),
                )
                self._conn.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            else:
                cursor = self._conn.execute(
                    "INSERT INTO documents(project, name, source, kind, size, chars, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(project or ""), title, str(source or ""), kind, int(size), len(body), stamp, stamp),
                )
                document_id = int(cursor.lastrowid)
            self._conn.executemany(
                "INSERT INTO chunks(document_id, ord, text) VALUES (?, ?, ?)",
                [(document_id, index, piece) for index, piece in enumerate(pieces)],
            )
        return {
            "id": document_id,
            "name": title,
            "chars": len(body),
            "chunks": len(pieces),
            "project": str(project or ""),
        }

    def delete_document(self, document_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM chunks WHERE document_id=?", (int(document_id),))
            self._conn.execute("DELETE FROM documents WHERE id=?", (int(document_id),))

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE id=?", (int(document_id),)
            ).fetchone()
            if row is None:
                return None
            chunks = self._conn.execute(
                "SELECT text FROM chunks WHERE document_id=? ORDER BY ord", (int(document_id),)
            ).fetchall()
        item = dict(row)
        item["text"] = "\n\n".join(str(chunk["text"]) for chunk in chunks)
        return item

    def find_document(self, name: str, *, project: str = "") -> dict[str, Any] | None:
        needle = str(name or "").strip()
        if not needle:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM documents WHERE name=? AND (project='' OR project=?) "
                "ORDER BY project DESC, updated_at DESC LIMIT 1",
                (needle, str(project or "")),
            ).fetchone()
            if row is None:
                row = self._conn.execute(
                    "SELECT id FROM documents WHERE name LIKE ? AND (project='' OR project=?) "
                    "ORDER BY project DESC, updated_at DESC LIMIT 1",
                    (f"%{needle}%", str(project or "")),
                ).fetchone()
        return self.get_document(int(row["id"])) if row else None

    def list_documents(
        self,
        *,
        project: str = "",
        current_only: bool = False,
        query: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if current_only:
            if project:
                clauses.append("(d.project='' OR d.project=?)")
                args.append(str(project))
            else:
                clauses.append("d.project=''")
        needle = str(query or "").strip()
        if needle:
            clauses.append(
                "(d.name LIKE ? OR d.source LIKE ? OR EXISTS("
                "SELECT 1 FROM chunks c WHERE c.document_id=d.id AND c.text LIKE ?))"
            )
            like = f"%{needle}%"
            args.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count "
            f"FROM documents d {where} ORDER BY d.updated_at DESC, d.id DESC LIMIT ?"
        )
        args.append(max(1, min(2000, int(limit))))
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        project: str = "",
        limit: int = 5,
        budget: int = 6000,
    ) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        if not tokens:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.text, c.document_id, d.name, d.source, d.project "
                "FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.project='' OR d.project=?",
                (str(project or ""),),
            ).fetchall()
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in rows:
            value = _score(str(row["name"]), str(row["text"]), tokens)
            if value > 0:
                scored.append(
                    (
                        value,
                        {
                            "document_id": int(row["document_id"]),
                            "name": str(row["name"]),
                            "source": str(row["source"]),
                            "project": str(row["project"]),
                            "text": str(row["text"]),
                            "score": value,
                        },
                    )
                )
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        per_document: dict[int, int] = {}
        used = 0
        for _score_value, hit in scored:
            document_id = hit["document_id"]
            if per_document.get(document_id, 0) >= 2:
                continue
            if used + len(hit["text"]) > budget and hits:
                continue
            hits.append(hit)
            per_document[document_id] = per_document.get(document_id, 0) + 1
            used += len(hit["text"])
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def document_hits(
        self,
        document_id: int,
        query: str,
        *,
        limit: int = 5,
        budget: int = 10000,
    ) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        if not tokens:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT ord, text FROM chunks WHERE document_id=? ORDER BY ord",
                (int(document_id),),
            ).fetchall()
        scored: list[tuple[float, int, str]] = []
        for row in rows:
            text = str(row["text"])
            value = _score("", text, tokens)
            if value > 0:
                scored.append((value, int(row["ord"]), text))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        used = 0
        for _value, order, text in scored:
            if used + len(text) > budget and hits:
                continue
            hits.append({"ord": order, "text": text})
            used += len(text)
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"] if row else 0)

    def chunk_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return int(row["n"] if row else 0)


def knowledge_context(hits: list[dict[str, Any]]) -> str:
    if not hits:
        return ""
    lines = [
        "以下是用户导入知识库的资料片段，做模组时优先参考；引用时写清文件名。"
        "资料没写到的内容不要当成事实补全。"
    ]
    for hit in hits:
        scope = "当前工程" if hit.get("project") else "全部工程"
        lines.append(f"\n[{hit.get('name')}｜{scope}]\n{hit.get('text')}")
    return "\n".join(lines)
