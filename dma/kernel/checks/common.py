"""Shared context and helpers for all 2.0 checkers."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

from ..findings import Finding, Report
from ..java import JavaFile, iter_java_files, parse_java
from ..mappings import MappingInfo, detect_project
from ..rules import rule as lookup_rule
from ..symbols import SymbolIndex, build_index

SKIP_PARTS = {"build", ".gradle", "out", "run", "target", ".git", "node_modules"}


def finding(
    rule_id: str,
    message: str,
    *,
    path: str = "",
    line: int = 0,
    column: int = 0,
    suggestion: str = "",
    evidence: str = "",
    checker: str = "",
    severity: str = "",
) -> Finding:
    rule = lookup_rule(rule_id)
    level = rule.level if rule else "P2"
    return Finding(
        rule_id=rule_id,
        message=message,
        level=level,
        severity=severity,
        path=path,
        line=line,
        column=column,
        suggestion=suggestion or (rule.implementation if rule else ""),
        evidence=evidence,
        checker=checker or (rule.checker if rule else ""),
    )


@dataclass
class ProjectContext:
    root: Path
    java_files: list[JavaFile] = field(default_factory=list)
    index: SymbolIndex | None = None
    mapping: MappingInfo | None = None
    loader: str = ""
    mc: str = ""
    text_cache: dict[str, str] = field(default_factory=dict)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, root: Path, *, with_index: bool = True) -> "ProjectContext":
        root = Path(root).resolve()
        java_files: list[JavaFile] = []
        for path in iter_java_files(root):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            java_files.append(parse_java(text, str(path)))
        context = cls(root=root, java_files=java_files)
        try:
            context.mapping = detect_project(root)
        except Exception:  # pragma: no cover - detection is best effort
            context.mapping = MappingInfo()
        if with_index:
            try:
                context.index = build_index(root, persist=False)
            except Exception:  # pragma: no cover - never block linting
                context.index = None
        context.loader, context.mc = detect_target(root)
        return context

    # ------------------------------------------------------------------
    def rel(self, path: Path | str) -> str:
        target = Path(path)
        try:
            return target.resolve().relative_to(self.root).as_posix()
        except (ValueError, OSError):
            return str(path).replace("\\", "/")

    def read(self, path: Path | str) -> str:
        key = str(path)
        if key in self.text_cache:
            return self.text_cache[key]
        try:
            value = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            value = ""
        self.text_cache[key] = value
        return value

    def read_rel(self, relative: str) -> str:
        return self.read(self.root / relative)

    def exists(self, relative: str) -> bool:
        return (self.root / relative).exists()

    def glob(self, pattern: str) -> list[Path]:
        return sorted(self.root.glob(pattern))

    def java_at(self, relative: str) -> JavaFile | None:
        target = str((self.root / relative).resolve())
        for item in self.java_files:
            try:
                if str(Path(item.path).resolve()) == target:
                    return item
            except OSError:
                continue
        return None

    def methods(self) -> Iterator[tuple[JavaFile, Any, Any]]:
        for java in self.java_files:
            for java_class in java.all_classes():
                for method in java_class.methods:
                    yield java, java_class, method

    def classes(self) -> Iterator[tuple[JavaFile, Any]]:
        for java in self.java_files:
            for java_class in java.all_classes():
                yield java, java_class

    def json_files(self, pattern: str = "src/main/resources/**/*.json") -> list[Path]:
        rows: list[Path] = []
        for path in self.root.glob(pattern):
            if not path.is_file():
                continue
            if any(part in SKIP_PARTS for part in path.relative_to(self.root).parts):
                continue
            rows.append(path)
        return sorted(rows)

    def load_json(self, path: Path) -> Any:
        try:
            return json.loads(self.read(path))
        except (json.JSONDecodeError, ValueError):
            return None

    def resource_root(self) -> Path:
        return self.root / "src" / "main" / "resources"

    def is_common_path(self, relative: str) -> bool:
        value = relative.replace("\\", "/")
        if "/client/" in value or value.startswith("src/client/"):
            return False
        return "src/main/java/" in value

    def is_client_class(self, java_class: Any) -> bool:
        name = getattr(java_class, "name", "") or ""
        if name.endswith(("Client", "Screen", "Renderer", "Hud", "KeyMapping", "Particle")):
            return True
        return False


def detect_target(root: Path) -> tuple[str, str]:
    text = ""
    for name in ("gradle.properties", "build.gradle", "build.gradle.kts"):
        path = Path(root) / name
        if path.is_file():
            try:
                text += "\n" + path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    loader = ""
    lowered = text.lower()
    if "net.neoforged" in lowered or "neo_version" in lowered:
        loader = "NeoForge"
    elif "net.fabricmc" in lowered or "fabric_loader_version" in lowered:
        loader = "Fabric"
    elif "net.minecraftforge" in lowered:
        loader = "Forge"
    mc = ""
    for pattern in (
        r"minecraft_version\s*=\s*([^\s#]+)",
        r"minecraft\s*[:=]\s*['\"]([^'\"]+)",
        r"mc_version\s*=\s*([^\s#]+)",
    ):
        match = re.search(pattern, text)
        if match:
            mc = match.group(1).strip()
            break
    return loader, mc


CheckFn = Callable[[ProjectContext], Iterable[Finding]]


def run_checks(
    root: Path,
    *,
    levels: set[str] | None = None,
    only: set[str] | None = None,
    context: ProjectContext | None = None,
    with_index: bool = True,
) -> Report:
    started = time.perf_counter()
    ctx = context or ProjectContext.load(root, with_index=with_index)
    report = Report(scanned_files=len(ctx.java_files))
    checkers = _checkers()
    for name, function in checkers:
        if only and name not in only and not any(name.startswith(item) for item in only):
            continue
        try:
            rows = list(function(ctx))
        except Exception as exc:  # a broken checker must not hide other findings
            report.add(
                finding(
                    "TOOL-20", f"检查器 {name} 执行失败：{type(exc).__name__}: {exc}",
                    path="", severity="info", checker=name,
                )
            )
            continue
        for item in rows:
            if levels and item.level not in levels:
                continue
            report.add(item)
    report.dedupe().sort()
    report.duration_ms = int((time.perf_counter() - started) * 1000)
    report.meta.update({
        "root": str(ctx.root),
        "loader": ctx.loader,
        "mc": ctx.mc,
        "mapping": ctx.mapping.to_dict() if ctx.mapping else {},
        "checkers": [name for name, _ in checkers],
    })
    return report


def _checkers() -> list[tuple[str, CheckFn]]:
    from . import assets, datapack, packaging, docs, gradle, mixin
    from . import client_thread, network, registry, domain, antipattern
    from . import world, render_gui, serialization, thirdparty, logging_version
    from . import security_i18n, stress, ecosystem, version
    return [
        ("gradle", gradle.run),
        ("mixin", mixin.run),
        ("client", client_thread.run),
        ("network", network.run),
        ("registry", registry.run),
        ("domain", domain.run),
        ("antipattern", antipattern.run),
        ("world", world.run),
        ("render", render_gui.run),
        ("serialization", serialization.run),
        ("thirdparty", thirdparty.run),
        ("logging", logging_version.run),
        ("security", security_i18n.run),
        ("i18n", security_i18n.run_i18n),
        ("ecosystem", ecosystem.run),
        ("version", version.run),
        ("datapack", datapack.run),
        ("assets", assets.run),
        ("packaging", packaging.run),
        ("docs", docs.run),
        ("stress", stress.run),
    ]
