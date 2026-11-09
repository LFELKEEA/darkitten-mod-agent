"""Trusted template library: template first, incremental overlay (P1-07)."""

from __future__ import annotations

import filecmp
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DEFAULT_BASE = Path(__file__).resolve().parent.parent.parent / "resources"
TEXT_SUFFIXES = {".java", ".gradle", ".properties", ".toml", ".json", ".mcmeta", ".md", ".txt", ".xml", ".yml", ".yaml"}


@dataclass
class TemplateInfo:
    name: str
    loader: str
    mc: str
    path: str

    def to_dict(self) -> dict:
        return {"name": self.name, "loader": self.loader, "mc": self.mc, "path": self.path}


class TemplateLibrary:
    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else DEFAULT_BASE
        self.templates = self._scan()

    def _scan(self) -> list[TemplateInfo]:
        rows: list[TemplateInfo] = []
        if not self.base.is_dir():
            return rows
        for path in sorted(self.base.iterdir()):
            if not path.is_dir() or not (path / "build.gradle").is_file():
                continue
            match = re.match(r"(.+)-(\d+\.\d+(?:\.\d+)?)", path.name)
            if not match:
                continue
            loader = {"neoforge": "NeoForge", "forge": "Forge", "fabric": "Fabric"}.get(
                match.group(1).lower(), match.group(1).title(),
            )
            rows.append(TemplateInfo(path.name, loader, match.group(2), str(path)))
        return rows

    def list(self) -> list[TemplateInfo]:
        return list(self.templates)

    def resolve(self, loader: str, mc: str) -> TemplateInfo | None:
        loader = (loader or "").lower().replace("neo forge", "neoforge")
        exact = [
            item for item in self.templates
            if item.loader.lower() == loader and item.mc == mc
        ]
        if exact:
            return exact[0]
        major = ".".join((mc or "").split(".")[:2])
        partial = [
            item for item in self.templates
            if item.loader.lower() == loader and ".".join(item.mc.split(".")[:2]) == major
        ]
        if partial:
            return partial[0]
        candidates = [item for item in self.templates if item.loader.lower() == loader]
        return candidates[0] if candidates else None

    # ------------------------------------------------------------------
    def scaffold(
        self,
        dest: Path,
        *,
        loader: str = "NeoForge",
        mc: str = "1.20.1",
        mod_id: str,
        package: str,
        mod_name: str | None = None,
        version: str = "0.1.0",
        overwrite: bool = False,
        extra_values: dict[str, str] | None = None,
    ) -> dict:
        template = self.resolve(loader, mc)
        if template is None:
            raise FileNotFoundError(f"没有 {loader} {mc} 的模板")
        dest = Path(dest)
        values = {
            "mod_id": mod_id,
            "package": package,
            "class_name": _class_name(mod_id),
            "mod_name": mod_name or mod_id,
            "mod_version": version,
            "mc_version": template.mc,
        }
        values.update(extra_values or {})
        written: list[str] = []
        skipped: list[str] = []
        source = Path(template.path)
        for path in sorted(source.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            relative = relative.replace("__package__", package.replace(".", "/"))
            relative = relative.replace("ModMain.java", f"{values['class_name']}.java")
            target = dest / relative
            if target.exists() and not overwrite:
                skipped.append(relative)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.lower() in TEXT_SUFFIXES:
                text = path.read_text(encoding="utf-8", errors="replace")
                for key, value in values.items():
                    text = text.replace("{{" + key + "}}", str(value))
                target.write_text(text, encoding="utf-8")
            else:
                shutil.copy2(path, target)
            written.append(relative)
        return {
            "template": template.to_dict(),
            "values": values,
            "written": written,
            "skipped": skipped,
        }

    def overlay(self, dest: Path, files: dict[str, str | bytes], *, overwrite: bool = True) -> list[str]:
        dest = Path(dest)
        written: list[str] = []
        for relative, content in files.items():
            target = dest / relative
            if target.exists() and not overwrite:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            written.append(relative)
        return written

    def compare(
        self,
        dest: Path,
        *,
        loader: str,
        mc: str,
        mod_id: str | None = None,
        package: str | None = None,
    ) -> dict:
        """List files that differ from the template after substitution."""
        template = self.resolve(loader, mc)
        if template is None:
            return {"template": None, "different": [], "missing": []}
        dest = Path(dest)
        mod_id = mod_id or _detect_mod_id(dest)
        package = package or _detect_package(dest)
        source = Path(template.path)
        different: list[str] = []
        missing: list[str] = []
        for path in source.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            if package:
                relative = relative.replace("__package__", package.replace(".", "/"))
            if mod_id:
                relative = relative.replace("ModMain.java", f"{_class_name(mod_id)}.java")
            target = dest / relative
            if not target.exists():
                missing.append(relative)
            elif path.suffix.lower() not in TEXT_SUFFIXES and not filecmp.cmp(path, target, shallow=False):
                different.append(relative)
        return {"template": template.to_dict(), "different": different, "missing": missing}

    def describe(self) -> dict:
        return {
            "base": str(self.base),
            "templates": [item.to_dict() for item in self.templates],
        }

    def files(self, loader: str, mc: str) -> list[str]:
        template = self.resolve(loader, mc)
        if template is None:
            return []
        source = Path(template.path)
        return [path.relative_to(source).as_posix() for path in sorted(source.rglob("*")) if path.is_file()]


def _class_name(mod_id: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", mod_id)
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "ModMain"


def _detect_mod_id(dest: Path) -> str:
    props = Path(dest) / "gradle.properties"
    if props.is_file():
        match = re.search(r"^\s*mod_id\s*=\s*([\w.\-]+)", props.read_text(encoding="utf-8", errors="replace"), re.MULTILINE)
        if match:
            return match.group(1)
    return ""


def _detect_package(dest: Path) -> str:
    root = Path(dest) / "src" / "main" / "java"
    if not root.is_dir():
        return ""
    for path in root.rglob("*.java"):
        text = path.read_text(encoding="utf-8", errors="replace")
        match = re.search(r"^\s*package\s+([\w.]+)\s*;", text, re.MULTILINE)
        if match:
            return match.group(1)
    return ""


def scaffold(
    dest: Path,
    *,
    loader: str = "NeoForge",
    mc: str = "1.20.1",
    mod_id: str,
    package: str,
    **kwargs,
) -> dict:
    return TemplateLibrary().scaffold(
        dest, loader=loader, mc=mc, mod_id=mod_id, package=package, **kwargs,
    )
