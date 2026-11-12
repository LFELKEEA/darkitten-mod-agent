"""API reconnaissance: turn the real sources jar into a version-specific API card.

This is the anti-hallucination core. Before generating or fixing code, the agent
extracts actual class/method signatures from the exact Minecraft + loader
version, then checks every import against that card.
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Any

from ..gradle import windows_command
from .build import _java_env, ensure_gradle, forge_flags, gradle_command, local_maven_args
from .util import McmodError, iter_files, log, read_json, run, write_json, write_text
from .versions import VersionProfile

PACKAGE_PREFIXES = (
    "net/minecraft/",
    "net/neoforged/",
    "net/minecraftforge/",
    "net/fabricmc/",
    "com/mojang/",
)

CLASS_RE = re.compile(
    r"^\s*(?P<mods>(?:(?:public|protected|private|abstract|final|static|sealed|non-sealed)\s+)*)"
    r"(?P<kind>class|interface|enum|record)\s+(?P<name>\w+)", re.MULTILINE)
METHOD_RE = re.compile(
    r"^\s*(?P<mods>(?:(?:public|protected|static|final|abstract|default|synchronized|native)\s+)+)"
    r"(?:<[^>]+>\s*)?(?P<ret>[\w<>\[\]., ?]+?)\s+(?P<name>\w+)\s*\((?P<args>[^)]*)\)",
    re.MULTILINE)
FIELD_RE = re.compile(
    r"^\s*(?P<mods>(?:(?:public|protected|static|final|volatile|transient)\s+)+)"
    r"(?P<type>[\w<>\[\]., ?]+?)\s+(?P<name>\w+)\s*(?:=|;)", re.MULTILINE)
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
ANNOTATION_RE = re.compile(r"^\s*@(\w+)", re.MULTILINE)


def _sources_candidates(project: Path) -> list[Path]:
    candidates: list[Path] = []
    patterns = [
        project.glob("build/**/*-sources.jar"),
        project.glob("build/**/*sources*.jar"),
        project.glob("**/sources.jar"),
    ]
    for pattern in patterns:
        candidates.extend(pattern)
    for base in [Path.home() / ".gradle" / "caches"]:
        if base.exists():
            candidates.extend(base.rglob("*sources.jar"))
    # Keep unique, newest first.
    seen = set()
    unique = []
    for p in candidates:
        key = str(p.resolve())
        if key not in seen and p.stat().st_size > 0:
            seen.add(key)
            unique.append(p)
    return sorted(unique, key=lambda p: p.stat().st_mtime, reverse=True)


def run_gen_sources(project: Path, profile: VersionProfile, timeout: int = 1200) -> dict[str, Any]:
    cmd = gradle_command(project) or ensure_gradle(profile)
    full = [*cmd, "genSources", *local_maven_args(), *forge_flags(profile), "--console=plain"]
    env = _java_env(profile)
    log("提取 sources（首次会下载并反编译，可能需要几分钟）…")
    result = run(windows_command(full), cwd=project, timeout=timeout, env=env)
    return {"exit": result.returncode, "timed_out": result.timed_out, "tail": result.tail(40)}


def build_card(project: Path, profile: VersionProfile, *, jar: Path | None = None, gen_sources: bool = True) -> dict[str, Any]:
    if gen_sources:
        gen = run_gen_sources(project, profile)
    else:
        gen = {"skipped": True}
    if jar is None:
        candidates = _sources_candidates(project)
        if not candidates:
            raise McmodError(
                "没有找到 sources jar。先运行 `mcmod recon`（会自动执行 genSources），"
                "或用 --jar 指定 sources jar。"
            )
        jar = candidates[0]
    classes: dict[str, Any] = {}
    with zipfile.ZipFile(jar) as zf:
        names = [n for n in zf.namelist() if n.endswith(".java") and n.startswith(PACKAGE_PREFIXES)]
        for name in names:
            try:
                raw = zf.read(name)
            except KeyError:
                continue
            text = raw.decode("utf-8", errors="replace")
            if len(text) > 600_000:
                continue
            pkg_match = PACKAGE_RE.search(text)
            cls_match = CLASS_RE.search(text)
            if not pkg_match or not cls_match:
                continue
            fqcn = f"{pkg_match.group(1)}.{cls_match.group('name')}"
            members: list[str] = []
            for m in METHOD_RE.finditer(text):
                sig = re.sub(r"\s+", " ", m.group(0)).strip().rstrip("{").strip()
                if len(sig) < 400:
                    members.append(sig)
            for f in FIELD_RE.finditer(text):
                sig = re.sub(r"\s+", " ", f.group(0)).strip().rstrip(";").strip()
                if len(sig) < 300:
                    members.append(sig)
            annotations = sorted(set(ANNOTATION_RE.findall(text[:4000])))
            classes[fqcn] = {
                "kind": cls_match.group("kind"),
                "file": name,
                "annotations": annotations[:12],
                "members": members[:120],
            }
            if len(classes) >= 25000:
                break
    card = {
        "schema": "mcmod-api-card/1",
        "loader": profile.loader,
        "mc": profile.mc,
        "loader_version": profile.loader_version,
        "java_major": profile.java_major,
        "source_jar": str(jar),
        "generated_by": "mcmod recon",
        "class_count": len(classes),
        "classes": classes,
    }
    out = project / ".mcmod" / "api-card"
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "api-card.json", card)
    _write_card_markdown(out / "api-card.md", card)
    write_json(project / ".mcmod" / "recon-report.json", {
        "ok": len(classes) > 0,
        "source_jar": str(jar),
        "classes": len(classes),
        "gen_sources": gen,
        "card": str(out / "api-card.json"),
    })
    return card


def _write_card_markdown(path: Path, card: dict[str, Any]) -> None:
    classes = card["classes"]
    lines = [
        f"# API Card — {card['loader']} {card['mc']}",
        "",
        f"- Loader: `{card['loader_version']}`",
        f"- Java: `{card['java_major']}`",
        f"- Classes: `{card['class_count']}`",
        f"- Source jar: `{card['source_jar']}`",
        "",
        "只允许使用本卡中存在的类。方法签名以 sources jar 为准；本卡是索引，不是替代品。",
        "",
        "## 关键类速查",
        "",
    ]
    keys = [
        "net.minecraft.world.item.Item",
        "net.minecraft.world.item.ItemStack",
        "net.minecraft.world.level.block.Block",
        "net.minecraft.world.level.block.state.BlockBehaviour",
        "net.minecraft.core.Registry",
        "net.minecraft.core.registries.Registries",
        "net.minecraft.resources.ResourceLocation",
        "net.minecraft.network.chat.Component",
        "net.minecraft.world.entity.Entity",
        "net.minecraft.world.level.block.entity.BlockEntity",
        "net.minecraft.world.inventory.AbstractContainerMenu",
        "net.neoforged.bus.api.IEventBus",
        "net.neoforged.neoforge.registries.DeferredRegister",
        "net.neoforged.fml.common.Mod",
        "net.minecraftforge.eventbus.api.IEventBus",
        "net.minecraftforge.registries.DeferredRegister",
        "net.fabricmc.api.ModInitializer",
        "net.fabricmc.fabric.api.itemgroup.v1.FabricItemGroup",
    ]
    for key in keys:
        if key in classes:
            info = classes[key]
            lines.append(f"### `{key}`")
            lines.append("")
            lines.append(f"- kind: `{info['kind']}`")
            for member in info["members"][:25]:
                lines.append(f"- `{member}`")
            lines.append("")
    lines += ["", "## 所有类（分页省略）", ""]
    for name in sorted(classes)[:500]:
        lines.append(f"- `{name}`")
    if len(classes) > 500:
        lines.append(f"- … 其余 {len(classes) - 500} 个类见 api-card.json")
    write_text(path, "\n".join(lines) + "\n")


def load_card(project: Path) -> dict[str, Any]:
    path = project / ".mcmod" / "api-card" / "api-card.json"
    card = read_json(path)
    if not card:
        raise McmodError("没有 API 卡。先运行 `mcmod recon`。")
    return card


def check_imports(project: Path, card: dict[str, Any] | None = None) -> dict[str, Any]:
    project = project.resolve()
    card = card or load_card(project)
    classes = card["classes"]
    known_fqcn = set(classes)
    # Also accept nested classes by prefix.
    issues = []
    imports: set[str] = set()
    for path in iter_files(project / "src" / "main" / "java", (".java",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in re.finditer(r"^\s*import\s+(?:static\s+)?([\w.]+)\s*;", text, re.MULTILINE):
            fq = match.group(1)
            if fq.startswith(("java.", "javax.", "com.google.", "org.slf4j.", "org.jetbrains.", "it.unimi.")):
                continue
            if not fq.startswith(("net.minecraft.", "net.neoforged.", "net.minecraftforge.", "net.fabricmc.", "com.mojang.")):
                continue
            imports.add(fq)
            # Nested classes: strip trailing segments until a known class matches.
            parts = fq.split(".")
            found = False
            for i in range(len(parts), 2, -1):
                if ".".join(parts[:i]) in known_fqcn:
                    found = True
                    break
            if not found:
                issues.append({
                    "file": str(path.relative_to(project)),
                    "import": fq,
                    "message": f"API 卡中没有 `{fq}`，该 import 在当前版本不存在或包名错误。",
                })
    report = {
        "ok": not issues,
        "checked": len(imports),
        "issues": issues,
        "card": {"loader": card["loader"], "mc": card["mc"], "classes": card["class_count"]},
    }
    write_json(project / ".mcmod" / "import-report.json", report)
    return report


def api_search(project: Path, query: str, limit: int = 40) -> list[str]:
    card = load_card(project)
    q = query.lower()
    results = []
    for name, info in card["classes"].items():
        if q in name.lower():
            results.append(f"{name} ({info['kind']})")
            if len(results) >= limit:
                break
    if not results:
        for name, info in card["classes"].items():
            for member in info["members"]:
                if q in member.lower():
                    results.append(f"{name} :: {member}")
                    if len(results) >= limit:
                        break
            if len(results) >= limit:
                break
    return results


def summarize(card: dict[str, Any]) -> str:
    return (
        f"API 卡已生成：{card['loader']} {card['mc']}，"
        f"{card['class_count']} 个类，来源 {card['source_jar']}"
    )
