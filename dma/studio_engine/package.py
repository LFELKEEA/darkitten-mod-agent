"""Package a built jar and generate publishing metadata."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Any

from .util import McmodError, now_iso, read_json, sha256_file, write_json, write_text
from .versions import VersionProfile


def _pick_jar(project: Path) -> Path | None:
    libs = project / "build" / "libs"
    if not libs.exists():
        return None
    jars = [p for p in libs.glob("*.jar")]
    if not jars:
        return None
    def score(p: Path) -> int:
        name = p.name.lower()
        penalty = 0
        for bad in ("-sources", "-javadoc", "-dev", "-shadow", "-all", "-plain"):
            if bad in name:
                penalty += 10
        return penalty
    return sorted(jars, key=lambda p: (score(p), -p.stat().st_size))[0]


def _validate_jar(jar: Path, spec: dict[str, Any]) -> dict[str, Any]:
    modid = spec["identity"]["modid"]
    issues: list[str] = []
    with zipfile.ZipFile(jar) as zf:
        names = zf.namelist()
    meta = [n for n in names if n in {
        "META-INF/mods.toml", "META-INF/neoforge.mods.toml", "fabric.mod.json",
    }]
    if not meta:
        issues.append("jar 内缺少 mods.toml / neoforge.mods.toml / fabric.mod.json")
    if not any(n.startswith(f"assets/{modid}/") for n in names):
        issues.append(f"jar 内缺少 assets/{modid}/ 资源")
    if any(n.endswith(".java") for n in names):
        issues.append("jar 内包含 .java 源文件（应为 sources jar）")
    if any(n.startswith("run/") for n in names):
        issues.append("jar 内包含 run/ 目录")
    return {"ok": not issues, "issues": issues, "entries": len(names), "metadata": meta}


def package(project: Path, spec: dict[str, Any], profile: VersionProfile) -> dict[str, Any]:
    project = project.resolve()
    jar = _pick_jar(project)
    if not jar:
        raise McmodError("找不到构建产物 build/libs/*.jar，请先运行 mcmod build")
    ident = spec["identity"]
    dist = project / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    target = dist / f"{ident['modid']}-{ident['version']}-{profile.loader}-{profile.mc}.jar"
    shutil.copy2(jar, target)
    validation = _validate_jar(target, spec)
    checksum = sha256_file(target)

    body = f"""# {ident['name_zh']} ({ident['name_en']})

{ident['description_zh']}

## 目标版本

- Minecraft `{profile.mc}`
- {profile.loader} `{profile.loader_version}`
- Java `{profile.java_major}`

## 内容

""" + "\n".join(f"- **{n['name_zh']}**（`{n['id']}`）：{n['role']}" for n in spec["content"][:30]) + f"""

## 安装

1. 安装 Minecraft `{profile.mc}` 与 {profile.loader} `{profile.loader_version}`。
2. 将 `{target.name}` 放入 `mods/`。
3. 需要 Java `{profile.java_major}`。

## 构建信息

- 构建时间：{now_iso()}
- SHA256：`{checksum}`
"""
    write_text(dist / "README.md", body)
    write_text(dist / "CHANGELOG.md", f"""# Changelog

## {ident['version']}

- 初始版本，目标 `{profile.loader} {profile.mc}`。
- 内容节点 {len(spec['content'])} 个。
""")
    write_json(dist / "modrinth.json", {
        "name": ident["name_en"],
        "version_number": ident["version"],
        "version_type": "beta",
        "loaders": [profile.loader],
        "game_versions": [profile.mc],
        "featured": False,
        "dependencies": [],
        "file": target.name,
        "body": body,
    })
    write_text(dist / "curseforge.md", body)
    write_text(dist / "SHA256SUMS", f"{checksum}  {target.name}\n")

    report = {
        "ok": validation["ok"],
        "jar": str(target),
        "source_jar": str(jar),
        "sha256": checksum,
        "size": target.stat().st_size,
        "validation": validation,
        "files": [str(p) for p in sorted(dist.iterdir()) if p.is_file()],
    }
    write_json(project / ".mcmod" / "package-report.json", report)
    return report


def summarize(report: dict[str, Any]) -> str:
    lines = [
        f"打包{'通过' if report['ok'] else '有警告'}：{report['jar']}",
        f"大小：{report['size'] / 1024:.1f} KiB，SHA256：{report['sha256'][:16]}…",
    ]
    for issue in report["validation"]["issues"]:
        lines.append(f"- {issue}")
    return "\n".join(lines)
