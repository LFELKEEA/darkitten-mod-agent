"""A small, dependency-free Java structure parser.

It is not a compiler: it masks comments/strings, matches braces and extracts
packages, imports, classes, methods, fields and annotations.  That is enough
for the 2.0 linters, the symbol index and AST-aware edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

_IDENT = r"[A-Za-z_$][\w$]*"
_CLASS_RE = re.compile(rf"(?<!\.)\b(class|interface|enum|record|@interface)\s+({_IDENT})")
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_IMPORT_RE = re.compile(r"^\s*import\s+(static\s+)?([\w.*]+)\s*;", re.MULTILINE)
_ANNOTATION_RE = re.compile(rf"@({_IDENT}(?:\.{_IDENT})*)(?:\s*\(([^)]*)\))?")
_MODIFIERS = {
    "public", "protected", "private", "static", "final", "abstract",
    "synchronized", "native", "default", "strictfp", "transient", "volatile", "sealed", "non-sealed",
}


def mask(text: str) -> str:
    """Blank out comments and string/char contents while keeping offsets."""
    result = list(text)
    index = 0
    length = len(text)
    state = "code"
    while index < length:
        char = text[index]
        nxt = text[index + 1] if index + 1 < length else ""
        if state == "code":
            if char == "/" and nxt == "/":
                state = "line"
                result[index] = result[index + 1] = " "
                index += 2
                continue
            if char == "/" and nxt == "*":
                state = "block"
                result[index] = result[index + 1] = " "
                index += 2
                continue
            if char == '"':
                if text[index:index + 3] == '"""':
                    state = "textblock"
                    index += 3
                    continue
                state = "string"
                index += 1
                continue
            if char == "'":
                state = "char"
                index += 1
                continue
            index += 1
            continue
        if state == "line":
            if char == "\n":
                state = "code"
            else:
                result[index] = " "
            index += 1
            continue
        if state == "block":
            if char == "*" and nxt == "/":
                result[index] = result[index + 1] = " "
                state = "code"
                index += 2
                continue
            if char != "\n":
                result[index] = " "
            index += 1
            continue
        if state == "textblock":
            if text[index:index + 3] == '"""':
                state = "code"
                index += 3
                continue
            if char != "\n":
                result[index] = " "
            index += 1
            continue
        if state == "string":
            if char == "\\":
                result[index] = " "
                if index + 1 < length and text[index + 1] != "\n":
                    result[index + 1] = " "
                index += 2
                continue
            if char == '"':
                state = "code"
                index += 1
                continue
            if char != "\n":
                result[index] = " "
            index += 1
            continue
        if state == "char":
            if char == "\\":
                result[index] = " "
                if index + 1 < length and text[index + 1] != "\n":
                    result[index + 1] = " "
                index += 2
                continue
            if char == "'":
                state = "code"
                index += 1
                continue
            if char != "\n":
                result[index] = " "
            index += 1
            continue
    return "".join(result)


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, max(0, index)) + 1


def _matching_brace(masked: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(masked)):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level(value: str, separator: str = ",") -> list[str]:
    rows: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(value):
        if char in "(<[":
            depth += 1
        elif char in ")>]":
            depth = max(0, depth - 1)
        elif char == separator and depth == 0:
            rows.append(value[start:index])
            start = index + 1
    rows.append(value[start:])
    return [row.strip() for row in rows if row.strip()]


def parse_annotations(text: str) -> list[dict[str, str]]:
    annotations: list[dict[str, str]] = []
    for match in _ANNOTATION_RE.finditer(text):
        name = match.group(1)
        if name in {"Override", "SuppressWarnings", "Deprecated", "FunctionalInterface", "SafeVarargs"}:
            pass
        annotations.append({"name": name, "args": (match.group(2) or "").strip()})
    return annotations


def _header_start(masked: str, position: int) -> int:
    index = position
    while index > 0 and masked[index - 1] not in ";}{":
        index -= 1
    return index


def _strip_leading_annotations(header: str) -> tuple[list[str], str]:
    annotations: list[str] = []
    value = header.lstrip()
    while value.startswith("@"):
        match = re.match(rf"@{_IDENT}(?:\.{_IDENT})*", value)
        if not match:
            break
        end = match.end()
        rest = value[end:].lstrip()
        if rest.startswith("("):
            depth = 0
            for index, char in enumerate(rest):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        end += len(rest[:index + 1])
                        break
        annotations.append(value[:end].strip())
        value = value[end:].lstrip()
    return annotations, value


@dataclass
class JavaField:
    name: str
    type: str = ""
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    initializer: str = ""
    line: int = 0

    def is_static(self) -> bool:
        return "static" in self.modifiers


@dataclass
class JavaMethod:
    name: str
    return_type: str = ""
    params: list[tuple[str, str]] = field(default_factory=list)
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    line: int = 0
    body: str = ""
    owner: str = ""
    is_constructor: bool = False

    def annotation(self, name: str) -> str:
        for item in self.annotations:
            if item.lstrip("@").split("(")[0].split(".")[-1] == name:
                return item
        return ""

    @property
    def signature(self) -> str:
        params = ",".join(param_type for param_type, _ in self.params)
        return f"{self.name}({params})"

    def calls(self) -> list[str]:
        keywords = {
            "if", "for", "while", "switch", "catch", "synchronized", "return",
            "new", "throw", "assert", "super", "this", "do", "else", "try",
        }
        rows = re.findall(rf"\b({_IDENT})\s*\(", self.body)
        return [name for name in rows if name not in keywords]


@dataclass
class JavaClass:
    name: str
    kind: str = "class"
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    extends: str = ""
    implements: list[str] = field(default_factory=list)
    line: int = 0
    body: str = ""
    methods: list[JavaMethod] = field(default_factory=list)
    fields: list[JavaField] = field(default_factory=list)
    inner: list["JavaClass"] = field(default_factory=list)
    package: str = ""
    path: str = ""

    @property
    def qualified(self) -> str:
        return f"{self.package}.{self.name}" if self.package else self.name

    def method(self, name: str) -> JavaMethod | None:
        for item in self.methods:
            if item.name == name:
                return item
        return None


@dataclass
class JavaFile:
    path: str
    text: str
    masked: str
    package: str = ""
    imports: list[str] = field(default_factory=list)
    static_imports: list[str] = field(default_factory=list)
    classes: list[JavaClass] = field(default_factory=list)
    methods: list[JavaMethod] = field(default_factory=list)
    fields: list[JavaField] = field(default_factory=list)

    def all_classes(self) -> Iterator[JavaClass]:
        def walk(items: Iterable[JavaClass]) -> Iterator[JavaClass]:
            for item in items:
                yield item
                yield from walk(item.inner)
        return walk(self.classes)

    def all_methods(self) -> Iterator[JavaMethod]:
        for item in self.all_classes():
            yield from item.methods
        yield from self.methods

    def class_named(self, name: str) -> JavaClass | None:
        for item in self.all_classes():
            if item.name == name or item.qualified == name:
                return item
        return None


def _parse_members(
    text: str,
    masked: str,
    start: int,
    end: int,
    owner: JavaClass | None,
    package: str,
    path: str,
) -> tuple[list[JavaClass], list[JavaMethod], list[JavaField]]:
    classes: list[JavaClass] = []
    methods: list[JavaMethod] = []
    fields: list[JavaField] = []
    index = start
    while index < end:
        char = masked[index]
        if char.isspace() or char == ";":
            index += 1
            continue
        header_start = index
        depth = 0
        cursor = index
        while cursor < end:
            current = masked[cursor]
            if current in "(<[":
                depth += 1
            elif current in ")>]":
                depth = max(0, depth - 1)
            elif current == ";" and depth == 0:
                break
            elif current == "{" and depth == 0:
                break
            elif current == "}" and depth == 0:
                break
            cursor += 1
        if cursor >= end:
            break
        if masked[cursor] == ";":
            declaration = text[header_start:cursor].strip()
            fields.extend(_parse_field(declaration, line_of(text, header_start)))
            index = cursor + 1
            continue
        if masked[cursor] == "}":
            index = cursor + 1
            continue
        header = text[header_start:cursor].strip()
        close = _matching_brace(masked, cursor)
        if close < 0:
            break
        body = text[cursor + 1:close]
        annotations, stripped = _strip_leading_annotations(header)
        class_match = _CLASS_RE.search(stripped)
        if class_match:
            kind, name = class_match.group(1), class_match.group(2)
            before = stripped[:class_match.start()].strip()
            after = stripped[class_match.end():].strip()
            extends = ""
            implements: list[str] = []
            ext_match = re.search(r"\bextends\s+([\w.$<>\[\], ?]+?)(?:\s+implements\s+|$)", after)
            if ext_match:
                extends = ext_match.group(1).strip()
            imp_match = re.search(r"\bimplements\s+([\w.$<>\[\], ?]+)$", after)
            if imp_match:
                implements = [item.strip() for item in _split_top_level(imp_match.group(1))]
            if not implements and kind == "interface":
                ext_match = re.search(r"\bextends\s+([\w.$<>\[\], ?]+)$", after)
                if ext_match:
                    implements = [item.strip() for item in _split_top_level(ext_match.group(1))]
            java_class = JavaClass(
                name=name,
                kind=kind.lstrip("@"),
                modifiers=[word for word in before.split() if word in _MODIFIERS],
                annotations=annotations,
                extends=extends,
                implements=implements,
                line=line_of(text, header_start),
                body=body,
                package=package,
                path=path,
            )
            inner_classes, inner_methods, inner_fields = _parse_members(
                text, masked, cursor + 1, close, java_class, package, path
            )
            java_class.inner = inner_classes
            java_class.methods = inner_methods
            java_class.fields = inner_fields
            classes.append(java_class)
            index = close + 1
            continue

        # Method / constructor with a body.
        method = _parse_method(
            text, stripped, body, annotations, line_of(text, header_start),
            owner.name if owner else "", owner,
        )
        if method is not None:
            methods.append(method)
        index = close + 1

    return classes, methods, fields


def _parse_field(declaration: str, line: int) -> list[JavaField]:
    if not declaration or "(" in declaration:
        return []
    annotations, stripped = _strip_leading_annotations(declaration)
    if not stripped or stripped == "package" or stripped.startswith("import "):
        return []
    modifier_rows = [word for word in stripped.split() if word in _MODIFIERS]
    remainder = " ".join(word for word in stripped.split() if word not in _MODIFIERS)
    initializer = ""
    if "=" in remainder:
        remainder, initializer = remainder.split("=", 1)
        initializer = initializer.strip()
    remainder = remainder.strip()
    if not remainder or " " not in remainder:
        return []
    type_name, _, rest = remainder.partition(" ")
    rows: list[JavaField] = []
    for piece in _split_top_level(rest):
        name = piece.strip().split("=")[0].strip()
        if re.fullmatch(_IDENT, name):
            rows.append(
                JavaField(
                    name=name,
                    type=type_name.strip(),
                    modifiers=list(modifier_rows),
                    annotations=list(annotations),
                    initializer=initializer,
                    line=line,
                )
            )
    return rows


def _parse_method(
    text: str,
    header: str,
    body: str,
    annotations: list[str],
    line: int,
    owner: str,
    owner_class: JavaClass | None,
) -> JavaMethod | None:
    open_paren = header.find("(")
    if open_paren < 0:
        return None
    close_paren = None
    depth = 0
    for index in range(open_paren, len(header)):
        char = header[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                close_paren = index
                break
    if close_paren is None:
        return None
    prefix = header[:open_paren].strip()
    params_text = header[open_paren + 1:close_paren]
    prefix = re.sub(r"\bthrows\b.*$", "", prefix).strip()
    words = prefix.split()
    modifiers = [word for word in words if word in _MODIFIERS]
    name_words = [word for word in words if word not in _MODIFIERS]
    if not name_words:
        return None
    name = name_words[-1].split(".")[-1]
    return_type = " ".join(name_words[:-1]).strip()
    is_constructor = bool(owner_class and name == owner_class.name)
    if not return_type and not is_constructor:
        return None
    params: list[tuple[str, str]] = []
    for raw in _split_top_level(params_text):
        cleaned = re.sub(r"@\w+(?:\([^)]*\))?\s*", "", raw).strip()
        cleaned = re.sub(r"\bfinal\b\s*", "", cleaned).strip()
        pieces = cleaned.split()
        if not pieces:
            continue
        param_name = pieces[-1]
        param_type = " ".join(pieces[:-1]) or "var"
        params.append((param_type, param_name))
    return JavaMethod(
        name=name,
        return_type=return_type,
        params=params,
        modifiers=modifiers,
        annotations=annotations,
        line=line,
        body=body,
        owner=owner,
        is_constructor=is_constructor,
    )


def parse_java(text: str, path: str = "") -> JavaFile:
    masked = mask(text)
    package_match = _PACKAGE_RE.search(masked)
    package = package_match.group(1) if package_match else ""
    imports: list[str] = []
    static_imports: list[str] = []
    for match in _IMPORT_RE.finditer(masked):
        value = match.group(2)
        if match.group(1):
            static_imports.append(value)
        else:
            imports.append(value)
    classes, methods, fields = _parse_members(
        text, masked, 0, len(masked), None, package, path
    )
    return JavaFile(
        path=path,
        text=text,
        masked=masked,
        package=package,
        imports=imports,
        static_imports=static_imports,
        classes=classes,
        methods=methods,
        fields=fields,
    )


def read_java(path: Path) -> JavaFile:
    target = Path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    return parse_java(text, str(target))


def iter_java_files(root: Path, *, skip_dirs: Iterable[str] = ()) -> Iterator[Path]:
    skipped = {str(item).replace("\\", "/").strip("/") for item in skip_dirs}
    root = Path(root)
    for path in root.rglob("*.java"):
        relative = path.relative_to(root).as_posix()
        if any(part in skipped for part in relative.split("/")[:-1]):
            continue
        if any(part in {"build", ".gradle", "out", "run", "target"} for part in relative.split("/")[:-1]):
            continue
        yield path


def annotation_arg(annotation: str, key: str) -> str:
    """Extract ``key = "value"`` from an annotation string."""
    match = re.search(rf"\b{re.escape(key)}\s*=\s*(?:\{{)?\s*\"([^\"]*)\"", annotation)
    if match:
        return match.group(1)
    match = re.search(rf"\b{re.escape(key)}\s*=\s*([\w.$]+)", annotation)
    if match:
        return match.group(1)
    return ""


def annotation_positional(annotation: str) -> str:
    match = re.search(r"\(\s*(?:\{[^}]*\})?\s*\"([^\"]*)\"", annotation)
    return match.group(1) if match else ""


def find_calls(text: str, name: str) -> list[int]:
    rows: list[int] = []
    for match in re.finditer(rf"(?<![\w$]){re.escape(name)}\s*\(", mask(text)):
        rows.append(line_of(text, match.start()))
    return rows


def enclosing_method(java: JavaFile, line: int) -> JavaMethod | None:
    best: JavaMethod | None = None
    for method in java.all_methods():
        if method.line <= line and (best is None or method.line >= best.line):
            best = method
    return best
