"""Project + dependency symbol index (checklist P0-05 / TOOL-04).

The index combines:

* Java sources in the project (classes, methods, fields, annotations);
* ``.class`` files from jars / class directories via a minimal class-file
  reader (no ASM, no external dependencies).

Queries: class, method, field, overrides, subclasses/implementations, callers
and event listeners.  The result is cached under ``.dma/index/symbols.json``.
"""

from __future__ import annotations

import json
import re
import struct
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

from .java import JavaFile, iter_java_files, parse_java


# ----------------------------------------------------------------------
# Class-file reader
# ----------------------------------------------------------------------

CONSTANT_SIZES = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4, 12: 4,
                  15: 3, 16: 2, 17: 4, 18: 4, 19: 2, 20: 2}


@dataclass
class RawClass:
    name: str
    super_name: str = ""
    interfaces: list[str] = field(default_factory=list)
    methods: list[tuple[str, str, int]] = field(default_factory=list)
    fields: list[tuple[str, str, int]] = field(default_factory=list)
    source: str = ""


def _read_u1(data: bytes, offset: int) -> tuple[int, int]:
    return data[offset], offset + 1


def _read_u2(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">H", data, offset)[0], offset + 2


def _read_u4(data: bytes, offset: int) -> tuple[int, int]:
    return struct.unpack_from(">I", data, offset)[0], offset + 4


def parse_class_bytes(data: bytes, source: str = "") -> RawClass | None:
    """Parse the parts of a JVM class file needed by the index."""
    if len(data) < 10 or data[:4] != b"\xca\xfe\xba\xbe":
        return None
    offset = 8
    count, offset = _read_u2(data, offset)
    pool: dict[int, tuple[int, Any]] = {}
    index = 1
    try:
        while index < count:
            tag, offset = _read_u1(data, offset)
            if tag == 1:
                length, offset = _read_u2(data, offset)
                value = data[offset:offset + length].decode("utf-8", "replace")
                offset += length
                pool[index] = (tag, value)
            elif tag in CONSTANT_SIZES:
                size = CONSTANT_SIZES[tag]
                if tag == 7:
                    value, offset = _read_u2(data, offset)
                    pool[index] = (tag, value)
                elif tag in {9, 10, 11, 12, 17, 18}:
                    first, offset = _read_u2(data, offset)
                    second, offset = _read_u2(data, offset)
                    pool[index] = (tag, (first, second))
                elif tag == 8:
                    value, offset = _read_u2(data, offset)
                    pool[index] = (tag, value)
                elif tag == 15:
                    kind, offset = _read_u1(data, offset)
                    ref, offset = _read_u2(data, offset)
                    pool[index] = (tag, (kind, ref))
                elif tag == 16:
                    value, offset = _read_u2(data, offset)
                    pool[index] = (tag, value)
                else:
                    offset += size
                    pool[index] = (tag, None)
                if tag in {5, 6}:
                    index += 1
            else:
                return None
            index += 1
    except (struct.error, IndexError):
        return None

    def utf(index_value: int) -> str:
        entry = pool.get(index_value)
        return str(entry[1]) if entry and entry[0] == 1 else ""

    def class_name(index_value: int) -> str:
        entry = pool.get(index_value)
        if not entry or entry[0] != 7:
            return ""
        return utf(int(entry[1])).replace("/", ".")

    try:
        _, offset = _read_u2(data, offset)
        this_class, offset = _read_u2(data, offset)
        super_class, offset = _read_u2(data, offset)
        interface_count, offset = _read_u2(data, offset)
        interfaces: list[str] = []
        for _ in range(interface_count):
            value, offset = _read_u2(data, offset)
            interfaces.append(class_name(value))
    except (struct.error, IndexError):
        return None

    result = RawClass(
        name=class_name(this_class),
        super_name=class_name(super_class),
        interfaces=interfaces,
        source=source,
    )

    def read_members(offset: int, is_method: bool) -> tuple[list[tuple[str, str, int]], int]:
        rows: list[tuple[str, str, int]] = []
        try:
            total, offset = _read_u2(data, offset)
            for _ in range(total):
                flags, offset = _read_u2(data, offset)
                name_index, offset = _read_u2(data, offset)
                descriptor_index, offset = _read_u2(data, offset)
                attr_count, offset = _read_u2(data, offset)
                for _ in range(attr_count):
                    _, offset = _read_u2(data, offset)
                    length, offset = _read_u4(data, offset)
                    offset += length
                rows.append((utf(name_index), utf(descriptor_index), flags))
        except (struct.error, IndexError):
            pass
        return rows, offset

    result.fields, offset = read_members(offset, False)
    result.methods, offset = read_members(offset, True)
    return result


def iter_class_files(root: Path) -> Iterator[Path]:
    for path in Path(root).rglob("*.class"):
        yield path


def read_class_path(path: Path) -> list[RawClass]:
    target = Path(path)
    rows: list[RawClass] = []
    if target.is_dir():
        for item in iter_class_files(target):
            try:
                parsed = parse_class_bytes(item.read_bytes(), str(item))
            except OSError:
                continue
            if parsed:
                rows.append(parsed)
        return rows
    if target.suffix.lower() == ".jar":
        try:
            with zipfile.ZipFile(target) as archive:
                for name in archive.namelist():
                    if not name.endswith(".class") or name.startswith("META-INF/"):
                        continue
                    try:
                        parsed = parse_class_bytes(archive.read(name), f"{target.name}!{name}")
                    except (OSError, zipfile.BadZipFile):
                        continue
                    if parsed:
                        rows.append(parsed)
        except (OSError, zipfile.BadZipFile):
            return []
    return rows


# ----------------------------------------------------------------------
# Index model
# ----------------------------------------------------------------------

@dataclass
class IndexedMethod:
    owner: str
    name: str
    descriptor: str = ""
    return_type: str = ""
    params: list[str] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    line: int = 0
    path: str = ""
    source: str = "project"

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner, "name": self.name, "descriptor": self.descriptor,
            "return_type": self.return_type, "params": list(self.params),
            "modifiers": list(self.modifiers), "annotations": list(self.annotations),
            "line": self.line, "path": self.path, "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "IndexedMethod":
        return cls(
            owner=str(raw.get("owner") or ""), name=str(raw.get("name") or ""),
            descriptor=str(raw.get("descriptor") or ""),
            return_type=str(raw.get("return_type") or ""),
            params=[str(item) for item in raw.get("params") or []],
            modifiers=[str(item) for item in raw.get("modifiers") or []],
            annotations=[str(item) for item in raw.get("annotations") or []],
            line=int(raw.get("line") or 0), path=str(raw.get("path") or ""),
            source=str(raw.get("source") or "project"),
        )

    def label(self) -> str:
        params = ", ".join(self.params)
        return f"{self.owner}#{self.name}({params})"


@dataclass
class IndexedClass:
    name: str
    super_name: str = ""
    interfaces: list[str] = field(default_factory=list)
    kind: str = "class"
    annotations: list[str] = field(default_factory=list)
    line: int = 0
    path: str = ""
    source: str = "project"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "super_name": self.super_name,
            "interfaces": list(self.interfaces), "kind": self.kind,
            "annotations": list(self.annotations), "line": self.line,
            "path": self.path, "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "IndexedClass":
        return cls(
            name=str(raw.get("name") or ""),
            super_name=str(raw.get("super_name") or ""),
            interfaces=[str(item) for item in raw.get("interfaces") or []],
            kind=str(raw.get("kind") or "class"),
            annotations=[str(item) for item in raw.get("annotations") or []],
            line=int(raw.get("line") or 0), path=str(raw.get("path") or ""),
            source=str(raw.get("source") or "project"),
        )


@dataclass
class IndexedField:
    owner: str
    name: str
    type: str = ""
    modifiers: list[str] = field(default_factory=list)
    line: int = 0
    path: str = ""
    source: str = "project"

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner, "name": self.name, "type": self.type,
            "modifiers": list(self.modifiers), "line": self.line,
            "path": self.path, "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "IndexedField":
        return cls(
            owner=str(raw.get("owner") or ""), name=str(raw.get("name") or ""),
            type=str(raw.get("type") or ""),
            modifiers=[str(item) for item in raw.get("modifiers") or []],
            line=int(raw.get("line") or 0), path=str(raw.get("path") or ""),
            source=str(raw.get("source") or "project"),
        )


def _descriptor(type_name: str) -> str:
    value = re.sub(r"<.*>", "", str(type_name or "")).strip()
    if not value:
        return "Ljava/lang/Object;"
    dimensions = 0
    while value.endswith("[]"):
        dimensions += 1
        value = value[:-2].strip()
    base = {
        "void": "V", "boolean": "Z", "byte": "B", "char": "C",
        "short": "S", "int": "I", "long": "J", "float": "F", "double": "D",
    }.get(value)
    if base is None:
        base = "L" + value.replace(".", "/") + ";"
    return "[" * dimensions + base


def method_descriptor(java_method: Any) -> str:
    params = "".join(_descriptor(item[0]) for item in java_method.params)
    return f"({params}){_descriptor(java_method.return_type)}"


class SymbolIndex:
    def __init__(self) -> None:
        self.classes: dict[str, IndexedClass] = {}
        self.methods: list[IndexedMethod] = []
        self.fields: list[IndexedField] = []
        self.files: list[str] = []
        self.root: str = ""
        self.built_at: str = ""
        self.cache_key: str = ""
        self._call_cache: dict[str, list[dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    def add_class(self, item: IndexedClass) -> None:
        self.classes[item.name] = item
        simple = item.name.rsplit(".", 1)[-1]
        self.classes.setdefault(simple, item)

    def add_method(self, item: IndexedMethod) -> None:
        self.methods.append(item)

    def add_field(self, item: IndexedField) -> None:
        self.fields.append(item)

    # ------------------------------------------------------------------
    def build(self, root: Path, *, class_paths: Iterable[Path] = ()) -> "SymbolIndex":
        self.root = str(Path(root).resolve())
        self.files = []
        for path in iter_java_files(Path(root)):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            java = parse_java(text, str(path))
            self.files.append(str(path))
            self._add_java(java, Path(root))
        for item in class_paths:
            for raw in read_class_path(item):
                self._add_raw(raw)
        if not self.built_at:
            from datetime import datetime, timezone
            self.built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.cache_key = self._compute_cache_key(Path(root))
        return self

    def _add_java(self, java: JavaFile, root: Path) -> None:
        try:
            relative = Path(java.path).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            relative = java.path
        for item in java.all_classes():
            self.add_class(
                IndexedClass(
                    name=item.qualified or item.name,
                    super_name=item.extends,
                    interfaces=list(item.implements),
                    kind=item.kind,
                    annotations=list(item.annotations),
                    line=item.line,
                    path=relative,
                )
            )
            for method in item.methods:
                self.add_method(
                    IndexedMethod(
                        owner=item.qualified or item.name,
                        name=method.name,
                        descriptor=method_descriptor(method),
                        return_type=method.return_type,
                        params=[param_type for param_type, _ in method.params],
                        modifiers=list(method.modifiers),
                        annotations=list(method.annotations),
                        line=method.line,
                        path=relative,
                    )
                )
            for thing in item.fields:
                self.add_field(
                    IndexedField(
                        owner=item.qualified or item.name,
                        name=thing.name,
                        type=thing.type,
                        modifiers=list(thing.modifiers),
                        line=thing.line,
                        path=relative,
                    )
                )

    def _add_raw(self, raw: RawClass) -> None:
        if not raw.name:
            return
        self.add_class(
            IndexedClass(
                name=raw.name,
                super_name=raw.super_name,
                interfaces=list(raw.interfaces),
                source="dependency",
                path=raw.source,
            )
        )
        owner = raw.name
        for name, descriptor, flags in raw.methods:
            self.add_method(
                IndexedMethod(
                    owner=owner,
                    name=name,
                    descriptor=descriptor,
                    modifiers=_flags(flags),
                    source="dependency",
                    path=raw.source,
                )
            )
        for name, descriptor, flags in raw.fields:
            self.add_field(
                IndexedField(
                    owner=owner,
                    name=name,
                    type=descriptor,
                    modifiers=_flags(flags),
                    source="dependency",
                    path=raw.source,
                )
            )

    # ------------------------------------------------------------------
    def find_class(self, name: str, *, fuzzy: bool = True) -> list[IndexedClass]:
        value = str(name or "").strip()
        if not value:
            return []
        exact = self.classes.get(value)
        rows: list[IndexedClass] = []
        if exact is not None:
            rows.append(exact)
        simple = value.rsplit(".", 1)[-1]
        if simple != value and simple in self.classes:
            candidate = self.classes[simple]
            if candidate not in rows:
                rows.append(candidate)
        if fuzzy and not rows:
            lowered = value.lower()
            seen: set[str] = set()
            for key, item in self.classes.items():
                if "." in key:
                    continue
                if lowered in key.lower():
                    if item.name not in seen:
                        seen.add(item.name)
                        rows.append(item)
        return rows[:30]

    def find_method(
        self,
        name: str,
        *,
        owner: str = "",
        descriptor: str = "",
        include_dependencies: bool = True,
    ) -> list[IndexedMethod]:
        rows: list[IndexedMethod] = []
        for item in self.methods:
            if item.name != name:
                continue
            if owner and not (
                owner == item.owner
                or item.owner.endswith("." + owner)
                or owner.endswith("." + item.owner)
            ):
                continue
            if descriptor and item.descriptor and item.descriptor != descriptor:
                continue
            if not include_dependencies and item.source != "project":
                continue
            rows.append(item)
        return rows[:80]

    def find_field(self, name: str, *, owner: str = "") -> list[IndexedField]:
        return [
            item for item in self.fields
            if item.name == name and (not owner or owner in item.owner)
        ][:80]

    def lookups(self, query: str, limit: int = 30) -> list[dict[str, Any]]:
        value = str(query or "").strip()
        rows: list[dict[str, Any]] = []
        for item in self.find_class(value):
            rows.append({"kind": "class", **item.to_dict()})
        for item in self.find_method(value):
            rows.append({"kind": "method", **item.to_dict()})
        for item in self.find_field(value):
            rows.append({"kind": "field", **item.to_dict()})
        return rows[:limit]

    # ------------------------------------------------------------------
    def _parents(self, name: str) -> list[str]:
        item = self.classes.get(name) or self.classes.get(name.rsplit(".", 1)[-1])
        if item is None:
            return []
        rows = [item.super_name] if item.super_name else []
        rows.extend(item.interfaces)
        return [value for value in rows if value]

    @staticmethod
    def _same_class(left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        left_simple = left.rsplit(".", 1)[-1]
        right_simple = right.rsplit(".", 1)[-1]
        return left_simple == right_simple and (
            "." not in left or "." not in right
        )

    def subclasses_of(self, name: str, *, transitive: bool = True) -> list[IndexedClass]:
        target = str(name or "")
        rows: list[IndexedClass] = []
        queue = [target]
        seen: set[str] = {target}
        visited_parents: set[str] = set()
        while queue:
            current = queue.pop(0)
            if current in visited_parents:
                continue
            visited_parents.add(current)
            for item in self.classes.values():
                if item.name in seen:
                    continue
                parents = [item.super_name] + item.interfaces
                if any(self._same_class(parent, current) for parent in parents if parent):
                    rows.append(item)
                    seen.add(item.name)
                    queue.append(item.name)
            if not transitive:
                break
        unique: dict[str, IndexedClass] = {item.name: item for item in rows}
        return list(unique.values())[:200]

    def implementations_of(self, name: str) -> list[IndexedClass]:
        return self.subclasses_of(name)

    def overrides_of(self, owner: str, method_name: str = "") -> list[IndexedMethod]:
        """Methods in project subclasses that override ``owner#method_name``."""
        base = owner.rsplit(".", 1)[-1]
        children = {
            item.name for item in self.subclasses_of(owner)
            if item.source == "project"
        }
        rows: list[IndexedMethod] = []
        for item in self.methods:
            if item.source != "project":
                continue
            if item.owner not in children:
                continue
            if method_name and item.name != method_name:
                continue
            if not method_name and base not in item.annotations:
                pass
            rows.append(item)
        return rows[:100]

    def callers_of(self, name: str, *, limit: int = 100) -> list[dict[str, Any]]:
        if name in self._call_cache:
            return self._call_cache[name][:limit]
        rows: list[dict[str, Any]] = []
        pattern = re.compile(rf"(?<![\w$]){re.escape(name)}\s*\(")
        for path in self.files:
            try:
                text = Path(path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            masked = re.sub(r"//[^\n]*|/\*.*?\*/", lambda match: " " * len(match.group(0)), text, flags=re.S)
            for match in pattern.finditer(masked):
                line = masked.count("\n", 0, match.start()) + 1
                rows.append({
                    "path": path,
                    "line": line,
                    "snippet": text.splitlines()[line - 1].strip()[:200] if line - 1 < len(text.splitlines()) else "",
                })
                if len(rows) >= limit * 4:
                    break
        self._call_cache[name] = rows
        return rows[:limit]

    def listeners_for(self, event: str = "", *, limit: int = 100) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self.methods:
            annotations = " ".join(item.annotations)
            if not any(key in annotations for key in ("SubscribeEvent", "EventHandler", "Subscribe")):
                continue
            if event and event not in " ".join(item.params) and event not in item.descriptor:
                continue
            rows.append(item.to_dict())
            if len(rows) >= limit:
                break
        return rows

    def dependency_classes(self) -> list[IndexedClass]:
        return [item for item in self.classes.values() if item.source == "dependency"]

    def summary(self) -> dict[str, Any]:
        return {
            "classes": len({item.name for item in self.classes.values()}),
            "methods": len(self.methods),
            "fields": len(self.fields),
            "files": len(self.files),
            "project_methods": sum(1 for item in self.methods if item.source == "project"),
            "dependency_methods": sum(1 for item in self.methods if item.source == "dependency"),
            "root": self.root,
            "built_at": self.built_at,
        }

    # ------------------------------------------------------------------
    def _compute_cache_key(self, root: Path) -> str:
        parts: list[str] = []
        try:
            for path in iter_java_files(root):
                try:
                    parts.append(f"{path}:{path.stat().st_mtime_ns}")
                except OSError:
                    continue
        except OSError:
            pass
        import hashlib
        return hashlib.sha1("\n".join(sorted(parts)).encode("utf-8", "ignore")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "dma-symbols/2",
            "root": self.root,
            "built_at": self.built_at,
            "cache_key": self.cache_key,
            "files": list(self.files),
            "classes": [item.to_dict() for item in {id(v): v for v in self.classes.values()}.values()],
            "methods": [item.to_dict() for item in self.methods],
            "fields": [item.to_dict() for item in self.fields],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SymbolIndex":
        index = cls()
        index.root = str(raw.get("root") or "")
        index.built_at = str(raw.get("built_at") or "")
        index.cache_key = str(raw.get("cache_key") or "")
        index.files = [str(item) for item in raw.get("files") or []]
        for item in raw.get("classes") or []:
            if isinstance(item, dict):
                index.add_class(IndexedClass.from_dict(item))
        index.methods = [
            IndexedMethod.from_dict(item) for item in raw.get("methods") or []
            if isinstance(item, dict)
        ]
        index.fields = [
            IndexedField.from_dict(item) for item in raw.get("fields") or []
            if isinstance(item, dict)
        ]
        return index


def _flags(flags: int) -> list[str]:
    rows: list[str] = []
    if flags & 0x0001:
        rows.append("public")
    if flags & 0x0002:
        rows.append("private")
    if flags & 0x0004:
        rows.append("protected")
    if flags & 0x0008:
        rows.append("static")
    if flags & 0x0010:
        rows.append("final")
    if flags & 0x0400:
        rows.append("abstract")
    if flags & 0x1000:
        rows.append("synthetic")
    return rows


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------

CACHE_RELATIVE = Path(".dma") / "index" / "symbols.json"


def cache_path(root: Path) -> Path:
    return Path(root) / CACHE_RELATIVE


def build_index(
    root: Path,
    *,
    class_paths: Iterable[Path] = (),
    use_cache: bool = True,
    persist: bool = True,
) -> SymbolIndex:
    root = Path(root)
    if use_cache:
        cached = load_index(root)
        if cached is not None:
            probe = SymbolIndex()
            if probe._compute_cache_key(root) == cached.cache_key:
                return cached
    index = SymbolIndex().build(root, class_paths=class_paths)
    if persist:
        save_index(index, root)
    return index


def save_index(index: SymbolIndex, root: Path) -> Path:
    path = cache_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_index(root: Path) -> SymbolIndex | None:
    path = cache_path(root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != "dma-symbols/2":
        return None
    return SymbolIndex.from_dict(raw)


def query(root: Path, name: str, *, kind: str = "auto", owner: str = "") -> dict[str, Any]:
    index = build_index(root)
    if kind == "class":
        rows = [{"kind": "class", **item.to_dict()} for item in index.find_class(name)]
    elif kind == "method":
        rows = [{"kind": "method", **item.to_dict()} for item in index.find_method(name, owner=owner)]
    elif kind == "field":
        rows = [{"kind": "field", **item.to_dict()} for item in index.find_field(name, owner=owner)]
    elif kind == "callers":
        rows = index.callers_of(name)
    elif kind == "listeners":
        rows = index.listeners_for(name)
    else:
        rows = index.lookups(name)
    return {"ok": True, "query": name, "kind": kind, "count": len(rows), "results": rows}
