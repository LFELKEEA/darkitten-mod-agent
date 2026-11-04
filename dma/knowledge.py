from __future__ import annotations

import io
import json
import math
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
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
MAX_DOC_CHARS = 30_000_000
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
MAX_JAR_CLASSES = 5_000
MAX_JAR_TEXT_ENTRIES = 2_000
MAX_JAR_ENTRY_BYTES = 4 * 1024 * 1024
MAX_JAR_READ_BYTES = 256 * 1024 * 1024
MAX_JAR_ENTRY_CHARS = 200_000
MAX_CLASS_CHARS = 12_000
MAX_DECOMPILE_CHARS = 30_000_000
MAX_DECOMPILE_FILE_BYTES = 2 * 1024 * 1024
DECOMPILE_TIMEOUT = 300
MAX_EXPORT_FILES = 10_000
MAX_NESTED_JARS = 4
MAX_NESTED_JAR_BYTES = 12 * 1024 * 1024
_LIBRARY_PACKAGES = (
    "net/fabricmc/", "net/minecraft/", "com/mojang/", "com/google/",
    "org/apache/", "org/slf4j/", "org/jetbrains/", "org/intellij/",
    "org/spongepowered/", "kotlin/", "kotlinx/", "io/netty/", "it/unimi/",
    "javax/", "java/", "jdk/", "sun/", "com/electronwill/",
)
Progress = Callable[[str], None]

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_ASCII_TOKEN = re.compile(r"[A-Za-z0-9_.$#@-]{2,}")
_CJK_RUN = re.compile(r"[\u4e00-\u9fff]{2,}")
_SECTION_MARKER = re.compile(r"^## (源码|类|文件)：([^\n]+)$", re.MULTILINE)
_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "you", "are", "was", "but",
    "not", "have", "from", "they", "will", "can", "all", "any", "how", "why",
    "what", "when", "where", "which", "use", "using", "code", "file", "please",
    "into", "your", "our", "its", "his", "her", "them", "than", "then", "there",
}
# 中文问题检索英文源码时的常用对照。只补关键词，不改变原文匹配。
_TERM_HINTS = {
    "附身": ("possession", "possess", "body"),
    "催眠": ("hypnosis", "hypnot", "induction"),
    "皮肤": ("skin",),
    "模型": ("model",),
    "渲染": ("render",),
    "实体": ("entity",),
    "方块": ("block",),
    "物品": ("item",),
    "界面": ("screen", "gui"),
    "命令": ("command",),
    "网络": ("packet", "network"),
    "事件": ("event",),
    "注册": ("register", "registry"),
    "配方": ("recipe",),
    "战利品": ("loot",),
    "音效": ("sound",),
    "动画": ("animation", "animate"),
    "存档": ("save", "storage"),
    "权限": ("permission",),
    "配置": ("config",),
    "攻击": ("attack", "damage"),
    "治疗": ("heal",),
    "传送": ("teleport",),
    "掉落": ("drop",),
    "交易": ("trade", "market"),
    "属性": ("attribute",),
    "粒子": ("particle",),
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
            for flags, field_name, desc in fields[:150]:
                mods = _flags(flags, _CLASS_MEMBER_FLAGS)
                lines.append(f"  - {(mods + ' ') if mods else ''}{field_name}: {desc}")
            if len(fields) > 150:
                lines.append(f"  - …还有 {len(fields) - 150} 个字段")
        if methods:
            lines.append("方法：")
            for flags, method_name, desc in methods[:300]:
                mods = _flags(flags, _CLASS_MEMBER_FLAGS)
                lines.append(f"  - {(mods + ' ') if mods else ''}{method_name}{desc}")
            if len(methods) > 300:
                lines.append(
                    f"  - …（这里只列出前 300 个方法，另有 {len(methods) - 300} 个；"
                    "完整实现看反编译源码，不要据此判断源码不完整）"
                )
        refs: list[str] = []
        for cp_index in class_names:
            value = _dot(class_name(cp_index))
            if value and value not in {name, "java.lang.Object"} and value not in refs:
                refs.append(value)
        if refs:
            lines.append("引用的类：")
            lines.append("  - " + ", ".join(refs[:150]))
            if len(refs) > 150:
                lines.append(f"  - …还有 {len(refs) - 150} 个")
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
            for item in calls[:200]:
                lines.append(f"  - {item}")
            if len(calls) > 200:
                lines.append(f"  - …还有 {len(calls) - 200} 个")
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
            for item in literals[:100]:
                lines.append(f'  - "{item}"')
            if len(literals) > 100:
                lines.append(f"  - …还有 {len(literals) - 100} 个")
        text = "\n".join(lines)
        if len(text) > MAX_CLASS_CHARS:
            text = text[:MAX_CLASS_CHARS] + "\n…（该类结构已截断）"
        return text
    except Exception:
        return ""


def _class_meta(body: str) -> dict[str, Any]:
    """从 _class_summary 的输出里取类名、方法、中文字符串，用于生成速览。"""
    lines = body.splitlines()
    head = lines[0] if lines else ""
    match = re.search(r"(?:class|interface|enum) ([\w.$]+)", head)
    if not match:
        return {}
    name = match.group(1)
    package, _, simple = name.rpartition(".")
    if " interface " in f" {head} ":
        kind = "interface"
    elif " enum " in f" {head} ":
        kind = "enum"
    else:
        kind = "class"
    section = ""
    methods: list[str] = []
    fields: list[str] = []
    chinese: list[str] = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped in {"字段：", "方法：", "引用的类：", "调用的方法/字段：", "字符串常量："}:
            section = stripped
            continue
        if not line.startswith("  - ") or stripped.startswith("- …"):
            continue
        value = line[4:].strip()
        if section == "方法：":
            methods.append(value)
        elif section == "字段：":
            fields.append(value)
        elif section == "字符串常量：":
            for run in _CJK_RUN.findall(value.strip('"')):
                if run not in chinese:
                    chinese.append(run)
    return {
        "name": name,
        "simple": simple,
        "package": package,
        "kind": kind,
        "methods": methods,
        "fields": fields,
        "chinese": chinese,
    }


def _mod_summary(metadata: dict[str, str]) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    entrypoints: list[str] = []
    fabric = metadata.get("fabric.mod.json")
    if fabric:
        try:
            data = json.loads(fabric)
        except ValueError:
            data = None
        if isinstance(data, dict):
            ident = str(data.get("id") or "")
            title = str(data.get("name") or "")
            version = str(data.get("version") or "")
            bits = []
            if ident:
                bits.append(f"id={ident}")
            if title:
                bits.append(f"名称={title}")
            if version:
                bits.append(f"版本={version}")
            if bits:
                lines.append("Fabric：" + "，".join(bits))
            entry = data.get("entrypoints")
            if isinstance(entry, dict):
                for key in ("main", "client", "server"):
                    values = entry.get(key)
                    if not isinstance(values, list):
                        continue
                    for item in values[:6]:
                        if isinstance(item, str):
                            entrypoints.append(item)
                        elif isinstance(item, dict) and item.get("value"):
                            entrypoints.append(str(item["value"]))
    toml = metadata.get("meta-inf/mods.toml") or metadata.get("meta-inf/neoforge.mods.toml")
    if toml:
        ident = re.search(r'modId\s*=\s*"([^"]+)"', toml)
        title = re.search(r'displayName\s*=\s*"([^"]+)"', toml)
        version = re.search(r'version\s*=\s*"([^"]+)"', toml)
        bits = []
        if ident:
            bits.append(f"id={ident.group(1)}")
        if title:
            bits.append(f"名称={title.group(1)}")
        if version:
            bits.append(f"版本={version.group(1)}")
        if bits:
            lines.append("NeoForge/Forge：" + "，".join(bits))
    return lines, list(dict.fromkeys(entrypoints))


def _jar_overview(
    records: list[dict[str, Any]],
    metadata: dict[str, str],
) -> str:
    mod_lines, entrypoints = _mod_summary(metadata)
    entry_text = " ".join(entrypoints).lower()
    ranked = sorted(
        records,
        key=lambda item: (
            item["simple"].lower() in entry_text,
            len(item["chinese"]),
            len(item["methods"]),
        ),
        reverse=True,
    )
    lines = ["## 模组速览"]
    lines.extend(f"- {line}" for line in mod_lines)
    if entrypoints:
        names = [item.rsplit(".", 1)[-1] for item in entrypoints]
        lines.append("- 入口类：" + "、".join(dict.fromkeys(names))[:240])
    lines.append(f"- 顶层类 {len(records)} 个，下面先列重点类，再列完整类索引。")
    for item in ranked[:10]:
        label = item["simple"]
        bits = [f"{label}（{item['kind']}，{len(item['methods'])} 个方法）"]
        if item["chinese"]:
            bits.append("含中文：" + "、".join(item["chinese"][:3]))
        lines.append("- " + "；".join(bits))
    if len(ranked) > 10:
        lines.append(f"- 其余 {len(ranked) - 10} 个类见下方类索引。")
    lines.append(
        "- 用法：这是反编译源码资料，先用类名/方法名/字段名做关键词，"
        "例如 read_knowledge id=1 query=\"BodyPossession\"；不要因为中文问题搜不到英文类名就放弃。"
    )
    text = "\n".join(lines)
    if len(text) > 1000:
        text = text[:1000].rsplit("\n", 1)[0] + "\n- …（速览已截断）"
    return text


def _jar_class_index(records: list[dict[str, Any]]) -> str:
    groups: dict[str, list[str]] = {}
    for item in records:
        groups.setdefault(item["package"], []).append(item["simple"])
    lines = [f"## 类索引（共 {len(records)} 个类，按包）"]
    for package in sorted(groups):
        names = ", ".join(sorted(set(groups[package])))
        lines.append(f"{package or '(默认包)'}: {names}")
    return "\n".join(lines)


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


def _notify(progress: Progress | None, message: str) -> None:
    if progress is None:
        return
    try:
        progress(message)
    except Exception:
        pass


def _java_for_decompile() -> tuple[Path | None, str]:
    try:
        from . import toolchain

        install = toolchain.find_java()
    except Exception as exc:  # noqa: BLE001 - 检测失败不应影响结构提取
        return None, f"检测 JDK 失败：{exc}"
    if install is None:
        return None, (
            "本机没有找到 JDK，未生成反编译源码。到「环境」页点「一键准备」"
            "装好 JDK 21，再重新导入这个 JAR。"
        )
    if install.major < 17:
        return None, (
            f"本机 JDK {install.version} 版本过低，未生成反编译源码；"
            "反编译需要 JDK 17 或更高。"
        )
    executable = install.home / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not executable.is_file():
        return None, "没有找到可用的 java 可执行文件，未生成反编译源码。"
    return executable, ""


def _decompile_jar(
    path: Path, progress: Progress | None = None, label: str = ""
) -> tuple[list[tuple[str, str]], str]:
    jar = paths.resource_dir() / "decompiler" / "vineflower.jar"
    if not jar.is_file():
        return [], "没有找到内置反编译器，未生成反编译源码。"
    java, note = _java_for_decompile()
    if java is None:
        return [], note
    _notify(progress, f"正在反编译 {label or path.name}，这一步可能需要几十秒…")
    command = [
        str(java),
        "-Xmx1024m",
        "-jar",
        str(jar),
        "-s",
        "--thread-count",
        "4",
        str(path),
    ]
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = 0x08000000
    try:
        with tempfile.TemporaryDirectory(prefix="dma-jar-") as temp:
            output = Path(temp) / "src"
            output.mkdir()
            proc = subprocess.run(
                command + [str(output)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=DECOMPILE_TIMEOUT,
                **kwargs,
            )
            files: list[tuple[str, str]] = []
            total = 0
            for item in sorted(output.rglob("*.java")):
                try:
                    raw = item.read_bytes()
                except OSError:
                    continue
                if len(raw) > MAX_DECOMPILE_FILE_BYTES:
                    continue
                body = raw.decode("utf-8-sig", "replace").replace("\x00", "")
                if not body.strip():
                    continue
                relative = item.relative_to(output).as_posix()
                files.append((relative, body))
                total += len(body)
                if total >= MAX_DECOMPILE_CHARS * 2:
                    break
    except subprocess.TimeoutExpired:
        return [], f"反编译超过 {DECOMPILE_TIMEOUT} 秒，已停止；没有生成源码。"
    except OSError as exc:
        return [], f"无法启动反编译器：{exc}"
    if not files:
        detail = ""
        if proc.returncode:
            detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            detail = f"：{detail[-1][:160]}" if detail else ""
        return [], f"反编译没有生成 Java 源码，可能是纯资源包或类版本不受支持{detail}。"
    _notify(progress, f"反编译完成，共 {len(files)} 个 Java 文件，正在整理…")
    if proc.returncode:
        return files, f"反编译器返回码 {proc.returncode}，生成的结果可能不完整。"
    return files, ""


def _is_library_class(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(prefix) for prefix in _LIBRARY_PACKAGES)


def _nested_jar_candidates(
    path: Path, progress: Progress | None, notes: list[str]
) -> list[tuple[str, bytes]]:
    """挑出 JAR 里 META-INF/jars 下属于模组自己的内嵌模块。"""
    candidates: list[tuple[str, bytes]] = []
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        return candidates
    with archive:
        infos = [
            item
            for item in archive.infolist()
            if not item.is_dir()
            and item.filename.lower().startswith("meta-inf/jars/")
            and item.filename.lower().endswith(".jar")
        ]
        for info in sorted(infos, key=lambda item: item.filename.lower()):
            if len(candidates) >= MAX_NESTED_JARS:
                notes.append(f"内嵌 jar 超过 {MAX_NESTED_JARS} 个，后面的未展开。")
                break
            if info.file_size > MAX_NESTED_JAR_BYTES:
                notes.append(f"跳过过大的内嵌 jar：{info.filename}")
                continue
            try:
                raw = archive.read(info)
                with zipfile.ZipFile(io.BytesIO(raw)) as nested:
                    names = nested.namelist()
                    classes = [name for name in names if name.endswith(".class")]
                    if not classes:
                        continue
                    if not any(not _is_library_class(name) for name in classes):
                        notes.append(f"跳过依赖库内嵌 jar：{info.filename}")
                        continue
            except (OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
                continue
            candidates.append((info.filename, raw))
    return candidates


def decompile_jar_sources(
    path: Path,
    *,
    progress: Progress | None = None,
    include_nested: bool = True,
) -> tuple[list[dict[str, Any]], list[str]]:
    """把 JAR 和它自己的内嵌模块反编译成可写入工程的 .java 文件列表。"""
    source = Path(path).expanduser()
    if not source.is_file():
        raise KnowledgeError(f"文件不存在：{source}")
    if source.suffix.lower() != ".jar":
        raise KnowledgeError("只能反编译 .jar 文件。")
    notes: list[str] = []
    files: list[dict[str, Any]] = []
    main_files, note = _decompile_jar(source, progress)
    if note:
        notes.append(note)
    for relative, body in main_files:
        files.append({"path": relative, "text": body, "origin": source.name})
    if include_nested:
        nested = _nested_jar_candidates(source, progress, notes)
        if nested:
            _notify(progress, f"正在反编译 {len(nested)} 个内嵌模块…")
            with tempfile.TemporaryDirectory(prefix="dma-export-") as temp:
                for index, (name, raw) in enumerate(nested):
                    item = Path(temp) / f"nested-{index}.jar"
                    try:
                        item.write_bytes(raw)
                    except OSError as exc:
                        notes.append(f"无法展开内嵌 jar {name}：{exc}")
                        continue
                    part, part_note = _decompile_jar(
                        item, progress, label=Path(name).name
                    )
                    if part_note:
                        notes.append(f"{Path(name).name}：{part_note}")
                    for relative, body in part:
                        files.append(
                            {"path": relative, "text": body, "origin": name}
                        )
    if not files:
        detail = "；".join(dict.fromkeys(note for note in notes if note))
        raise KnowledgeError(
            detail
            or (
                "反编译没有生成 Java 源码，可能这个 JAR 只有资源，"
                "或本机 JDK 版本不受支持。"
            )
        )
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in files:
        relative = str(item["path"]).replace("\\", "/").lstrip("/")
        if not relative or ".." in Path(relative).parts:
            notes.append(f"跳过不安全的源码路径：{relative}")
            continue
        if relative in seen:
            notes.append(f"多个模块包含同名文件，保留先出现的：{relative}")
            continue
        seen.add(relative)
        deduped.append({**item, "path": relative})
    if len(deduped) > MAX_EXPORT_FILES:
        notes.append(f"源码文件超过 {MAX_EXPORT_FILES} 个，后面的未导出。")
        deduped = deduped[:MAX_EXPORT_FILES]
    notes.append(f"共生成 {len(deduped)} 个 Java 文件。")
    return deduped, notes


def write_decompiled_tree(
    project_root: Path,
    target: str,
    files: list[dict[str, Any]],
    *,
    jar_name: str,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """把反编译结果写到工程内的参考目录，并生成 INDEX.md。"""
    from . import project as project_mod

    relative_target = str(target or "").replace("\\", "/").strip("/")
    if not relative_target:
        raise KnowledgeError("目标目录不能为空。")
    try:
        base = project_mod.safe_path(project_root, relative_target)
    except project_mod.ProjectError as exc:
        raise KnowledgeError(str(exc)) from exc
    base.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    origins: dict[str, int] = {}
    for item in files:
        relative = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        if not relative:
            skipped += 1
            continue
        try:
            path = project_mod.safe_path(
                project_root, f"{relative_target}/{relative}"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(item.get("text") or ""), encoding="utf-8")
        except (project_mod.ProjectError, OSError):
            skipped += 1
            continue
        written += 1
        origin = str(item.get("origin") or jar_name)
        origins[origin] = origins.get(origin, 0) + 1
    lines = [
        f"# 反编译参考资料：{jar_name}",
        "",
        f"- 生成时间：{_now()}",
        f"- 目标目录：{relative_target}",
        f"- Java 文件：{written} 个",
        "- 说明：这些文件只用于阅读和迁移，不参与编译；"
        "迁移时逐个改写包名、映射和目标加载器 API。",
        "- 工具：Vineflower 1.12.0（Apache-2.0），机器还原，"
        "变量名和部分结构可能与作者原稿不同。",
        "",
        "## 来源",
    ]
    for origin, count in origins.items():
        lines.append(f"- {origin}：{count} 个")
    if notes:
        lines.extend(["", "## 备注"])
        lines.extend(f"- {note}" for note in notes)
    index_path = base / "INDEX.md"
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "base": str(base),
        "target": relative_target,
        "written": written,
        "skipped": skipped,
        "index": f"{relative_target}/INDEX.md",
    }


def _jar_text(path: Path, progress: Progress | None = None) -> str:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise KnowledgeError("这个 .jar 无法打开，文件可能损坏。") from exc
    with archive:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        infos.sort(key=_jar_rank)
        header = (
            f"# 模组 JAR：{path.name}\n"
            "说明：以下内容由程序自动提取，包括模组速览、类索引、元数据、语言与资源文本、"
            "类结构（字段、方法、引用类、调用关系、字符串常量），"
            "并尝试用内置反编译器生成 Java 源码（需要本机安装 JDK 17 或更高）。"
        )
        parts = [header]
        used = len(header) + 16000  # 给速览和类索引预留空间
        metadata: dict[str, str] = {}
        records: list[dict[str, Any]] = []
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
                meta = _class_meta(body)
                if meta:
                    records.append(meta)
                title = f"## 类：{_dot(name[:-6])}"
            else:
                body = _decode_entry(raw)
                if not body.strip():
                    continue
                text_entries += 1
                limit = 8_000 if low == "meta-inf/manifest.mf" else MAX_JAR_ENTRY_CHARS
                if len(body) > limit:
                    body = body[:limit] + "\n…（该文件已截断）"
                if low in JAR_METADATA:
                    metadata[low] = body
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
        if classes == 0 and text_entries == 0:
            raise KnowledgeError("这个 .jar 里没有可提取的文本或类结构，可能只是资源包或空包。")
        if records:
            parts[1:1] = [
                _jar_overview(records, metadata),
                _jar_class_index(records),
            ]
        structural = "\n\n".join(parts)
        source_files: list[tuple[str, str]] = []
        source_note = ""
        source_origin = ""
        if classes:
            try:
                exported, export_notes = decompile_jar_sources(
                    path, progress=progress, include_nested=True
                )
            except KnowledgeError as exc:
                exported, export_notes = [], [str(exc)]
            for item in exported:
                source_files.append((str(item["path"]), str(item["text"])))
            origins = {
                str(item.get("origin") or path.name) for item in exported
            }
            extra_origins = [item for item in origins if item != path.name]
            if extra_origins:
                source_origin = f"，其中包含 {len(extra_origins)} 个内嵌模块的源码"
            notes_from_export = [
                note
                for note in export_notes
                if note and not note.startswith("共生成")
            ]
            if notes_from_export:
                source_note = "；".join(notes_from_export)
        source_text = ""
        decompile_truncated = False
        if source_files:
            source_header = (
                "# 反编译源码（Vineflower 1.12.0，Apache-2.0）\n"
                "以下源码由机器反编译生成，变量名和部分结构可能与作者原稿不同，"
                "只用于理解实现。需要精确对应时，请以官方源码或 -sources.jar 为准。"
                "Fabric 模组里如果出现 class_1234 / method_5678，这是 intermediary 名称，"
                "需要用工程当前的 Yarn / Mojmap 映射对照。"
                f"共 {len(source_files)} 个 Java 文件{source_origin}。"
            )
            source_parts = [source_header]
            source_used = len(source_header)
            for relative, body in source_files:
                block = f"## 源码：{relative}\n{body.strip()}"
                if source_used + len(block) + 2 > MAX_DECOMPILE_CHARS:
                    decompile_truncated = True
                    break
                source_parts.append(block)
                source_used += len(block) + 2
            if len(source_parts) > 1:
                source_text = "\n\n".join(source_parts)
        max_structural = MAX_DOC_CHARS - (len(source_text) + 2 if source_text else 0) - 2000
        structural_trimmed = False
        if len(structural) > max_structural:
            structural = (
                structural[: max(0, max_structural)]
                + "\n\n（结构部分已截断，为反编译源码让出空间）"
            )
            structural_trimmed = True
        notes: list[str] = []
        if structural_trimmed:
            notes.append("结构部分超过单份资料上限，已截断并为反编译源码让出空间。")
        elif truncated:
            notes.append("内容超过单份资料上限，后面的条目未展开。")
        if decompile_truncated:
            notes.append("反编译源码超过单份资料上限，后面的 Java 文件未展开。")
        if source_note:
            notes.append(source_note)
        if omitted:
            notes.append(f"另有 {omitted} 个条目因数量或体积上限未展开。")
        if nested:
            notes.append(f"已跳过 {nested} 个内嵌 jar（通常是依赖库）。")
            if classes == 0:
                notes.append(
                    "这个 JAR 的代码主要在内嵌模块里，没有顶层类；"
                    "需要分析实现时，请单独导入对应的模块 jar。"
                )
        if skipped:
            notes.append(f"已跳过 {skipped} 个二进制、签名或损坏条目。")
        if source_text:
            structural += "\n\n" + source_text
        if notes:
            structural += "\n\n---\n" + "\n".join(notes)
        return structural


def extract_text(
    path: Path, *, progress: Progress | None = None
) -> tuple[str, str]:
    """返回（正文, 类型）。支持 txt/md/docx/pdf/jar 和常见代码/配置文件。"""
    source = Path(path)
    if not source.is_file():
        raise KnowledgeError(f"文件不存在：{source}")
    suffix = source.suffix.lower()
    if suffix == ".docx":
        text = _docx_text(source)
    elif suffix == ".pdf":
        text = _pdf_text(source)
    elif suffix == ".jar":
        text = _jar_text(source, progress=progress)
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


def _append_overlap(base: str, chunk: str) -> str:
    """拼接相邻分块，去掉 split_chunks 留下的重叠部分。"""
    if not base:
        return chunk
    candidates = [chunk]
    stripped = chunk.lstrip()
    if stripped != chunk:
        candidates.append(stripped)
    for candidate in candidates:
        limit = min(len(base), len(candidate), CHUNK_OVERLAP + 12)
        for size in range(limit, 19, -1):
            if base.endswith(candidate[:size]):
                return base + candidate[size:]
    return base + "\n" + chunk


def _entry_points(
    items: list[tuple[int, str]],
) -> list[tuple[int, str, str]]:
    """列出资料里每个条目的起始位置：(分块下标, 类型, 名称)。

    分块重叠会让同一个标题出现两次，这里只保留第一次。
    """
    points: list[tuple[int, str, str]] = []
    last: tuple[int, str, str] | None = None
    for position, (_order, text) in enumerate(items):
        for match in _SECTION_MARKER.finditer(text):
            kind = match.group(1)
            name = match.group(2).strip()
            if last is not None and last[1] == kind and last[2] == name:
                continue
            last = (position, kind, name)
            points.append(last)
    return points


def _entry_at(
    points: list[tuple[int, str, str]], position: int
) -> tuple[int, str, str] | None:
    """找到某个分块所属的条目；不在任何条目里时返回 None。"""
    low = 0
    high = len(points)
    while low < high:
        middle = (low + high) // 2
        if points[middle][0] <= position:
            low = middle + 1
        else:
            high = middle
    return points[low - 1] if low else None


def _rank_entries(
    items: list[tuple[int, str]],
    points: list[tuple[int, str, str]],
    tokens: set[str],
    fallback_name: str = "",
) -> list[tuple[float, str, str, int]]:
    """按条目（源码文件 / 类结构）给整份资料排序，返回 (分数, 类型, 名称, 起始分块)。

    同一文件的多个分块会合并计数，避免长文件因为分块多而被零散片段挤掉。
    没有条目标题的普通文档返回一个整份资料的伪条目。
    """
    if not points:
        count = 0.0
        best = 0.0
        best_position = 0
        for position, (_order, text) in enumerate(items):
            lowered = text.lower()
            matches = sum(lowered.count(token) for token in tokens)
            if not matches:
                continue
            count += matches
            value = _score(fallback_name, text, tokens)
            if value > best:
                best = value
                best_position = position
        if not count:
            return []
        return [(math.log1p(count) * 3.0 + best, "", fallback_name, best_position)]
    stats: dict[tuple[str, str], list[float]] = {}
    for position, (_order, text) in enumerate(items):
        markers = list(_SECTION_MARKER.finditer(text))
        segments: list[tuple[tuple[int, str, str] | None, str]] = []
        if markers:
            prefix = text[: markers[0].start()]
            if prefix.strip():
                previous = _entry_at(points, position - 1) if position else None
                segments.append((previous, prefix))
            for index, match in enumerate(markers):
                start = match.end()
                end = (
                    markers[index + 1].start()
                    if index + 1 < len(markers)
                    else len(text)
                )
                segments.append(
                    (
                        (position, match.group(1), match.group(2).strip()),
                        text[start:end],
                    )
                )
        else:
            segments.append((_entry_at(points, position), text))
        for point, segment in segments:
            if point is None:
                continue
            key = (point[1], point[2])
            stat = stats.get(key)
            if stat is None:
                stat = [0.0, 0.0, float(point[0])]
                stats[key] = stat
            matches = sum(segment.lower().count(token) for token in tokens)
            if matches:
                stat[0] += matches
                stat[1] = max(stat[1], _score(point[2], segment, tokens))
    ranked: list[tuple[float, str, str, int]] = []
    for (kind, name), (count, best, start) in stats.items():
        simple = _simple_entry_name(name).lower()
        name_bonus = 0.0
        for token in tokens:
            clean = _simple_entry_name(token).lower()
            if clean == simple:
                name_bonus += 40.0
            elif len(clean) >= 4 and clean in simple:
                name_bonus += 8.0
        if count <= 0 and name_bonus <= 0:
            continue
        rank = math.log1p(count) * 3.0 + best + name_bonus
        ranked.append((rank, kind, name, int(start)))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked


def _next_other_marker(
    text: str, kind: str, name: str
) -> re.Match[str] | None:
    """找到与当前条目不同的下一个条目标题，跳过同名标记（分块重叠会重复）。"""
    pos = 0
    while True:
        match = _SECTION_MARKER.search(text, pos)
        if match is None:
            return None
        if match.group(1) == kind and match.group(2).strip() == name:
            pos = match.end()
            continue
        return match


def _simple_entry_name(value: str) -> str:
    """把路径或全限定类名压成最后一段，用于匹配。"""
    value = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
    if value.endswith(".java"):
        return value[:-5]
    if "." in value:
        return value.rsplit(".", 1)[-1]
    return value


def _entry_matches(name: str, needle: str) -> bool:
    left = name.strip().lower()
    right = needle.strip().lower()
    if not left or not right:
        return False
    if left == right or left.endswith("/" + right) or left.endswith("." + right):
        return True
    return _simple_entry_name(left).lower() == _simple_entry_name(right).lower()


def _tokens(query: str) -> set[str]:
    text = str(query or "").lower()
    tokens: set[str] = {match.group(0) for match in _ASCII_TOKEN.finditer(text)}
    for chinese, english in _TERM_HINTS.items():
        if chinese in text:
            tokens.update(english)
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
    def add_file(
        self,
        path: Path,
        *,
        project: str = "",
        progress: Progress | None = None,
    ) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        text, kind = extract_text(source, progress=progress)
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
        body = ""
        for chunk in chunks:
            body = _append_overlap(body, str(chunk["text"]))
        item["text"] = body
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
        limit: int = 8,
        budget: int = 200_000,
    ) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        if not tokens:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT c.ord, c.text, c.document_id, d.name, d.source, d.project, d.kind "
                "FROM chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.project='' OR d.project=? ORDER BY c.document_id, c.ord",
                (str(project or ""),),
            ).fetchall()
        grouped: dict[int, list[tuple[int, str]]] = {}
        meta: dict[int, dict[str, Any]] = {}
        for row in rows:
            document_id = int(row["document_id"])
            grouped.setdefault(document_id, []).append(
                (int(row["ord"]), str(row["text"]))
            )
            meta.setdefault(
                document_id,
                {
                    "document_id": document_id,
                    "name": str(row["name"]),
                    "source": str(row["source"]),
                    "project": str(row["project"]),
                    "kind": str(row["kind"] or ""),
                },
            )
        ranked: list[tuple[float, int, str, str, int]] = []
        points: dict[int, list[tuple[int, str, str]]] = {}
        for document_id, items in grouped.items():
            doc_points = _entry_points(items)
            points[document_id] = doc_points
            info = meta[document_id]
            for rank, kind, name, start in _rank_entries(
                items, doc_points, tokens, info["name"]
            ):
                ranked.append((rank, document_id, kind, name, start))
        ranked.sort(key=lambda item: item[0], reverse=True)
        hits: list[dict[str, Any]] = []
        seen: set[tuple[int, str, str]] = set()
        per_document: dict[int, int] = {}
        used = 0
        for rank, document_id, kind, name, start in ranked:
            info = meta[document_id]
            # JAR 反编译资料有上千个文件，按关键词查一个类时需要多给几份源码。
            allowed = 8 if info["kind"] == "jar" else 2
            if per_document.get(document_id, 0) >= allowed:
                continue
            key = (document_id, kind, name)
            if key in seen:
                continue
            items = grouped[document_id]
            if kind:
                body = self._entry_body(items, start, (kind, name)) or ""
                text = f"## {kind}：{name}\n{body}"
            else:
                text = items[start][1]
            if not text.strip():
                continue
            if used + len(text) > budget:
                if hits:
                    continue
                text = (
                    text[:budget]
                    + "\n…（文件较长，本次只返回开头；需要完整实现请用 "
                    "read_knowledge 的 file 参数读这个文件。）"
                )
            hits.append(
                {
                    **info,
                    "text": text,
                    "score": rank,
                    "entry": name if kind else "",
                    "entry_kind": kind,
                }
            )
            seen.add(key)
            per_document[document_id] = per_document.get(document_id, 0) + 1
            used += len(text)
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def overview_hits(
        self, *, project: str = "", limit: int = 2
    ) -> list[dict[str, Any]]:
        """返回 JAR 资料的速览/类索引；关键词没命中时给 Agent 一个入口。"""
        with self._lock:
            docs = self._conn.execute(
                "SELECT id, name, source, project FROM documents "
                "WHERE (kind='jar' OR name LIKE '%.jar') AND (project='' OR project=?) "
                "ORDER BY updated_at DESC, id DESC LIMIT ?",
                (str(project or ""), max(1, min(10, int(limit) * 3))),
            ).fetchall()
            hits: list[dict[str, Any]] = []
            for doc in docs:
                document_id = int(doc["id"])
                first = self._conn.execute(
                    "SELECT text FROM chunks WHERE document_id=? ORDER BY ord LIMIT 1",
                    (document_id,),
                ).fetchone()
                first_text = str(first["text"]) if first else ""
                if "## 模组速览" in first_text:
                    text = first_text
                else:
                    rows = self._conn.execute(
                        "SELECT text FROM chunks WHERE document_id=? "
                        "AND (text LIKE '%## 类：%' OR text LIKE '%## 源码：%') ORDER BY ord",
                        (document_id,),
                    ).fetchall()
                    names: list[str] = []
                    files: list[str] = []
                    for row in rows:
                        value = str(row["text"])
                        names.extend(re.findall(r"## 类：([\w.$]+)", value))
                        files.extend(re.findall(r"## 源码：([^\n]+)", value))
                    names = list(dict.fromkeys(names))[:300]
                    files = list(dict.fromkeys(files))[:60]
                    if not names and not files:
                        continue
                    groups: dict[str, list[str]] = {}
                    for name in names:
                        package, _, simple = name.rpartition(".")
                        groups.setdefault(package, []).append(simple)
                    lines = [
                        f"# {doc['name']}（旧版 JAR 资料速览）",
                        f"## 类索引（共 {len(names)} 个类）",
                    ]
                    for package in sorted(groups):
                        lines.append(
                            f"{package or '(默认包)'}: "
                            + ", ".join(sorted(set(groups[package])))
                        )
                    if files:
                        lines.append(f"## 源码文件（{len(files)} 个）")
                        lines.extend(f"- {item}" for item in files)
                    lines.append(
                        "（这份资料没有模组速览或中文提示，可能由旧版本导入；"
                        "需要完整反编译源码时，请在知识库页重新导入这个 JAR。）"
                    )
                    text = "\n".join(lines)
                    if len(text) > 9000:
                        text = text[:9000] + "\n…（速览已截断）"
                hits.append(
                    {
                        "document_id": document_id,
                        "name": str(doc["name"]),
                        "source": str(doc["source"]),
                        "project": str(doc["project"]),
                        "kind": "jar",
                        "text": text,
                        "score": 0.0,
                    }
                )
                if len(hits) >= max(1, int(limit)):
                    break
        return hits

    def document_hits(
        self,
        document_id: int,
        query: str,
        *,
        limit: int = 8,
        budget: int = 200_000,
    ) -> list[dict[str, Any]]:
        tokens = _tokens(query)
        if not tokens:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT ord, text FROM chunks WHERE document_id=? ORDER BY ord",
                (int(document_id),),
            ).fetchall()
        items = [(int(row["ord"]), str(row["text"])) for row in rows]
        points = _entry_points(items)
        hits: list[dict[str, Any]] = []
        used = 0
        for _rank, kind, name, start in _rank_entries(items, points, tokens):
            if kind:
                body = self._entry_body(items, start, (kind, name)) or ""
                text = f"## {kind}：{name}\n{body}"
            else:
                text = items[start][1] if items else ""
            if not text.strip():
                continue
            if used + len(text) > budget:
                if hits:
                    continue
                text = (
                    text[:budget]
                    + "\n…（文件较长，本次只返回开头；需要完整实现请用 "
                    "read_knowledge 的 file 参数读这个文件。）"
                )
            hits.append(
                {
                    "ord": items[start][0] if items else 0,
                    "text": text,
                    "kind": kind,
                    "name": name,
                }
            )
            used += len(text)
            if len(hits) >= max(1, int(limit)):
                break
        return hits

    def read_source(
        self,
        document_id: int,
        target: str,
        *,
        offset: int = 0,
        limit: int = 0,
    ) -> dict[str, Any] | None:
        """按文件名或类名取回某个条目的完整正文；limit=0 表示整份返回。"""
        needle = str(target or "").replace("\\", "/").strip()
        if not needle:
            return None
        with self._lock:
            rows = self._conn.execute(
                "SELECT ord, text FROM chunks WHERE document_id=? ORDER BY ord",
                (int(document_id),),
            ).fetchall()
        items = [(int(row["ord"]), str(row["text"])) for row in rows]
        matches: list[tuple[int, str, str]] = []
        for position, (_order, text) in enumerate(items):
            for match in _SECTION_MARKER.finditer(text):
                kind, name = match.group(1), match.group(2).strip()
                if _entry_matches(name, needle):
                    matches.append((position, kind, name))
        if not matches:
            return None
        chosen = next((item for item in matches if item[1] == "源码"), matches[0])
        position, kind, name = chosen
        body = self._entry_body(items, position, (kind, name))
        if body is None:
            return None
        start = max(0, int(offset))
        if int(limit) <= 0:
            end = len(body)
        else:
            end = start + max(200, int(limit))
        return {
            "kind": kind,
            "name": name,
            "text": body[start:end],
            "total": len(body),
            "offset": start,
            "next": end if end < len(body) else None,
        }

    @staticmethod
    def _entry_body(
        items: list[tuple[int, str]],
        position: int,
        marker: tuple[str, str],
    ) -> str | None:
        kind, name = marker
        joined = ""
        body_start = -1
        scan_from = 0
        for index in range(position, len(items)):
            _order, text = items[index]
            joined = _append_overlap(joined, text)
            if body_start < 0:
                found = None
                for match in _SECTION_MARKER.finditer(joined):
                    if match.group(1) == kind and match.group(2).strip() == name:
                        found = match
                if found is None:
                    continue
                body_start = found.end()
                scan_from = body_start
            other = _next_other_marker(joined[scan_from:], kind, name)
            if other is not None:
                return joined[body_start : scan_from + other.start()].strip("\n")
            scan_from = len(joined)
            if len(joined) > MAX_DOC_CHARS:
                break
        if body_start < 0:
            return None
        return joined[body_start:].strip("\n")

    def source_entries(self, document_id: int, *, limit: int = 400) -> list[str]:
        """列出资料里的反编译源码文件，供找不到目标时提示。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT text FROM chunks WHERE document_id=? AND text LIKE '%## 源码：%' "
                "ORDER BY ord",
                (int(document_id),),
            ).fetchall()
        found: list[str] = []
        for row in rows:
            for kind, name in _SECTION_MARKER.findall(str(row["text"])):
                if kind != "源码":
                    continue
                name = name.strip()
                if name and name not in found:
                    found.append(name)
                    if len(found) >= max(1, int(limit)):
                        return found
        return found

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
        "以下是用户导入知识库的资料，做模组时优先参考；引用时写清文件名。"
        "检索命中的源码条目会整份返回（带 ## 源码：路径 标题），"
        "不是片段；资料没写到的内容不要当成事实补全。"
    ]
    for hit in hits:
        scope = "当前工程" if hit.get("project") else "全部工程"
        ident = hit.get("document_id")
        suffix = f"｜编号 {ident}" if ident else ""
        entry = str(hit.get("entry") or "")
        where = f"｜{entry}" if entry else ""
        lines.append(
            f"\n[{hit.get('name')}{where}｜{scope}{suffix}]\n{hit.get('text')}"
        )
    return "\n".join(lines)
