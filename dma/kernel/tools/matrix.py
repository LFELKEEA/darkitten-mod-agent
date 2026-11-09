"""Multi-version matrix planning, CI template and git versioning.

Covers P1-05 (MC × loader × Java × OS matrix), P3-34 (CI template) and
P3-35 (version derived from Git).  Running the actual matrix needs the
matching JDKs and Gradle caches, so this module plans it and emits a ready
GitHub Actions workflow instead of pretending to run every cell locally.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .templates import TemplateLibrary

DEFAULT_MC = ("1.20.1", "1.21.1")
DEFAULT_LOADERS = ("Forge", "NeoForge", "Fabric")
DEFAULT_OS = ("ubuntu-latest", "windows-latest", "macos-latest")


@dataclass
class MatrixCell:
    mc: str
    loader: str
    java: str
    os: str
    supported: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "mc": self.mc,
            "loader": self.loader,
            "java": self.java,
            "os": self.os,
            "supported": self.supported,
            "note": self.note,
        }


@dataclass
class MatrixPlan:
    cells: list[MatrixCell] = field(default_factory=list)

    def supported(self) -> list[MatrixCell]:
        return [item for item in self.cells if item.supported]

    def to_dict(self) -> dict:
        return {
            "cells": [item.to_dict() for item in self.cells],
            "supported": len(self.supported()),
            "total": len(self.cells),
        }


def plan(
    *,
    mc_versions: Iterable[str] | None = None,
    loaders: Iterable[str] | None = None,
    java_versions: Iterable[str] | None = None,
    os_list: Iterable[str] | None = None,
    library: TemplateLibrary | None = None,
) -> MatrixPlan:
    library = library or TemplateLibrary()
    available = {(item.loader.lower(), item.mc) for item in library.list()}
    mcs = list(mc_versions or DEFAULT_MC)
    names = list(loaders or DEFAULT_LOADERS)
    systems = list(os_list or DEFAULT_OS)
    rows: list[MatrixCell] = []
    for mc in mcs:
        for loader in names:
            supported = (loader.lower(), mc) in available
            javas = (
                list(java_versions)
                if java_versions
                else [java_for(mc, loader)]
            )
            for java in javas:
                for system in systems:
                    rows.append(
                        MatrixCell(
                            mc=mc,
                            loader=loader,
                            java=java,
                            os=system,
                            supported=supported,
                            note="" if supported else f"缺少 {loader} {mc} 模板",
                        )
                    )
    return MatrixPlan(rows)


def java_for(mc: str, loader: str = "") -> str:
    numbers = [int(item) for item in re.findall(r"\d+", mc or "")[:2]]
    if numbers and tuple(numbers) >= (1, 21):
        return "21"
    if numbers and tuple(numbers) >= (1, 17):
        return "17"
    return "8"


def workflow(
    matrix: MatrixPlan,
    *,
    name: str = "dma-matrix",
    task: str = "build",
) -> str:
    cells = matrix.supported() or matrix.cells[:1]
    include = []
    for item in cells:
        include.append(
            "          - mc: '{mc}'\n"
            "            loader: '{loader}'\n"
            "            java: '{java}'\n"
            "            os: '{os}'".format(**item.to_dict())
        )
    lines = [
        f"name: {name}",
        "",
        "on:",
        "  push:",
        "    branches: [ main, master ]",
        "  pull_request:",
        "",
        "permissions:",
        "  contents: read",
        "",
        "jobs:",
        "  matrix:",
        "    strategy:",
        "      fail-fast: false",
        "      matrix:",
        "        include:",
    ]
    lines.extend(include)
    lines.extend([
        "    runs-on: ${{ matrix.os }}",
        "    steps:",
        "      - uses: actions/checkout@v4",
        "      - uses: actions/setup-java@v4",
        "        with:",
        "          distribution: temurin",
        "          java-version: ${{ matrix.java }}",
        "      - uses: gradle/actions/setup-gradle@v4",
        "      - name: Validate wrapper",
        "        shell: bash",
        "        run: |",
        "          if [ \"$RUNNER_OS\" = \"Windows\" ]; then",
        "            ./gradlew.bat --version",
        "          else",
        "            chmod +x gradlew && ./gradlew --version",
        "          fi",
        "      - name: Gradle " + task,
        "        shell: bash",
        "        run: |",
        "          if [ \"$RUNNER_OS\" = \"Windows\" ]; then",
        f"            ./gradlew.bat {task} --console=plain",
        "          else",
        f"            ./gradlew {task} --console=plain",
        "          fi",
        "      - name: Checksums",
        "        shell: bash",
        "        run: |",
        "          find build/libs -name '*.jar' -maxdepth 1 -print0 2>/dev/null | xargs -0 -r sha256sum || true",
        "",
    ])
    return "\n".join(lines)


def write_workflow(
    root: Path,
    matrix: MatrixPlan | None = None,
    *,
    relative: str = ".github/workflows/dma-matrix.yml",
    task: str = "build",
) -> Path:
    matrix = matrix or plan()
    target = Path(root) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(workflow(matrix, task=task), encoding="utf-8")
    return target


def report(matrix: MatrixPlan, results: Iterable[dict] | None = None) -> str:
    passed: dict[tuple[str, str, str, str], bool] = {}
    for item in results or []:
        key = (
            str(item.get("mc") or ""),
            str(item.get("loader") or ""),
            str(item.get("java") or ""),
            str(item.get("os") or ""),
        )
        passed[key] = bool(item.get("ok"))
    lines = [
        "# 兼容矩阵报告",
        "",
        "| MC | Loader | Java | OS | 模板 | 结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in matrix.cells:
        key = (item.mc, item.loader, item.java, item.os)
        result = "未运行"
        if key in passed:
            result = "通过" if passed[key] else "失败"
        lines.append(
            f"| {item.mc} | {item.loader} | {item.java} | {item.os} | "
            f"{'有' if item.supported else '缺失'} | {result} |"
        )
    lines.append("")
    lines.append(f"- 组合：{len(matrix.cells)}；有模板：{len(matrix.supported())}")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
def git_version(root: Path) -> dict:
    root = Path(root)

    def run(*args: str) -> str:
        try:
            output = subprocess.run(
                ["git", *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return output.stdout.strip() if output.returncode == 0 else ""

    describe = run("describe", "--tags", "--always", "--dirty")
    tag = run("describe", "--tags", "--abbrev=0") if describe else ""
    commit = run("rev-parse", "--short", "HEAD")
    branch = run("rev-parse", "--abbrev-ref", "HEAD")
    dirty = describe.endswith("-dirty")
    version = describe.lstrip("v")
    if not version:
        text = ""
        try:
            text = (root / "gradle.properties").read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
        match = re.search(r"^\s*mod_version\s*=\s*(\S+)", text, re.MULTILINE)
        version = match.group(1) if match else ""
    return {
        "version": version,
        "tag": tag,
        "commit": commit,
        "branch": branch,
        "dirty": dirty,
        "describe": describe,
    }


def version_properties(root: Path, *, relative: str = "version.properties") -> Path:
    info = git_version(root)
    target = Path(root) / relative
    lines = [f"{key}={value}" for key, value in info.items()]
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def summary(matrix: MatrixPlan) -> str:
    payload = matrix.to_dict()
    return json.dumps(payload, ensure_ascii=False, indent=2)
