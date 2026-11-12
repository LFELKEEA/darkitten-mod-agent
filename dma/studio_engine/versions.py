"""Dynamic loader / Minecraft version resolution.

Nothing here is hard-coded to a single Minecraft era. We query the official
metadata endpoints, cache the result, and fall back to a small offline seed so
the tool still works with a flaky network.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from typing import Any

from .util import McmodError, http_get_json, http_get_text, read_json, write_json, cache_path

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
FABRIC_LOADER = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_GAME = "https://meta.fabricmc.net/v2/versions/game"
FABRIC_API_META = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml"
FABRIC_LOOM_META = "https://maven.fabricmc.net/net/fabricmc/fabric-loom/maven-metadata.xml"
FORGE_META = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_META = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
NEOFORGE_META_MIRROR = "https://mirror4.nyist.edu.cn/bmclapi/net/neoforged/neoforge/maven-metadata.xml"

LOADERS = ("neoforge", "forge", "fabric")
LOADER_LABEL = {"neoforge": "NeoForge", "forge": "Forge", "fabric": "Fabric"}

# Offline seed: used only when every metadata endpoint fails.
SEED_RELEASES = [
    "26.3", "26.2", "26.1.2", "26.1.1", "26.1", "1.21.11", "1.21.10",
    "1.21.9", "1.21.8", "1.21.7", "1.21.6", "1.21.5", "1.21.4", "1.21.3",
    "1.21.1", "1.21", "1.20.6", "1.20.4", "1.20.2", "1.20.1", "1.19.4",
    "1.18.2", "1.16.5", "1.12.2", "1.8.9", "1.7.10",
]

# Offline fallbacks. These are only consulted when the official metadata is
# unreachable; live metadata always wins.
FORGE_SEED = {
    "1.21.8": "1.21.8-58.0.3", "1.21.7": "1.21.7-57.0.3",
    "1.21.6": "1.21.6-56.0.9", "1.21.5": "1.21.5-55.0.24",
    "1.21.4": "1.21.4-54.1.6", "1.21.3": "1.21.3-53.1.2",
    "1.21.1": "1.21.1-52.1.2", "1.21": "1.21-51.0.33",
    "1.20.6": "1.20.6-50.2.1", "1.20.4": "1.20.4-49.2.0",
    "1.20.2": "1.20.2-48.1.0", "1.20.1": "1.20.1-47.4.5",
    "1.19.2": "1.19.2-43.5.1", "1.18.2": "1.18.2-40.3.11",
    "1.16.5": "1.16.5-36.2.39", "1.12.2": "1.12.2-14.23.5.2860",
    "1.8.9": "1.8.9-11.15.1.2318", "1.7.10": "1.7.10-10.13.4.1614",
}

NEOFORGE_SEED = {
    "26.3": "26.3.0.101", "26.2": "26.2.0.93", "26.1.2": "26.1.2.126",
    "1.21.11": "21.11.48", "1.21.10": "21.10.64", "1.21.8": "21.8.57",
    "1.21.5": "21.5.101", "1.21.4": "21.4.161", "1.21.3": "21.3.100",
    "1.21.1": "21.1.265", "1.21": "21.0.167", "1.20.6": "20.6.144",
    "1.20.4": "20.4.253", "1.20.2": "20.2.93",
}

FABRIC_API_SEED = {
    "26.3": "0.165.2+26.3", "1.21.8": "0.116.0+1.21.8",
    "1.21.4": "0.119.4+1.21.4", "1.21.1": "0.116.6+1.21.1",
    "1.20.1": "0.92.2+1.20.1", "1.19.2": "0.77.0+1.19.2",
    "1.18.2": "0.77.0+1.18.2", "1.16.5": "0.42.0+1.16",
}


def _version_key(v: str) -> tuple:
    parts = re.split(r"[.\-+]", v)
    out = []
    for p in parts:
        m = re.match(r"^(\d+)(.*)$", p)
        if m:
            out.append((0, int(m.group(1)), m.group(2)))
        else:
            out.append((1, 0, p))
    return tuple(out)


def _xml_versions(xml_text: str) -> list[str]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.findall(r"<version>([^<]+)</version>", xml_text)
    return [el.text.strip() for el in root.iter("version") if el.text]


@dataclass
class McVersion:
    id: str
    type: str = "release"
    release_time: str = ""
    url: str = ""


@dataclass
class VersionProfile:
    loader: str
    mc: str
    loader_version: str = ""
    java_major: int = 21
    gradle_version: str = "8.14.3"
    mappings: str = ""
    fabric_api: str = ""
    loom: str = ""
    template: str = ""
    tier: str = "scaffold"          # full | scaffold | legacy | design-only | unsupported
    supported: bool = False
    mdk_url: str = ""
    mdk_mirror: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def minecraft_releases(refresh: bool = False) -> list[McVersion]:
    try:
        data = http_get_json(MOJANG_MANIFEST, ttl=6 * 3600, refresh=refresh,
                             cache_name="minecraft-manifest.json")
        versions = [
            McVersion(id=v["id"], type=v.get("type", "release"),
                      release_time=v.get("releaseTime", ""), url=v.get("url", ""))
            for v in data.get("versions", [])
        ]
        if versions:
            write_json(cache_path("minecraft_manifest.json"), [v.__dict__ for v in versions])
            # Keep manifest order (newest first).
            return versions
    except McmodError:
        pass
    cached = read_json(cache_path("minecraft_manifest.json"), None)
    if cached:
        return [McVersion(**v) for v in cached]
    return [McVersion(id=v) for v in SEED_RELEASES]


def minecraft_release_ids(refresh: bool = False) -> list[str]:
    return [v.id for v in minecraft_releases(refresh=refresh) if v.type == "release"]


def java_major_for_mc(mc: str, refresh: bool = False) -> int:
    """Read javaVersion.majorVersion from the official version JSON."""
    versions = {v.id: v for v in minecraft_releases(refresh=refresh)}
    meta = versions.get(mc)
    if meta and meta.url and not os.environ.get("MCMOD_FAST"):
        try:
            data = http_get_json(meta.url, ttl=30 * 24 * 3600, timeout=8,
                                 cache_name=f"mc-version-{mc}.json", refresh=refresh)
            major = int(data.get("javaVersion", {}).get("majorVersion", 0))
            if major:
                return major
        except (McmodError, ValueError, TypeError):
            pass
    # Fallback table. Minecraft 26.x uses Java 25; 1.20.5+ uses Java 21.
    if mc.startswith("26."):
        return 25
    if _version_key(mc) >= _version_key("1.20.5"):
        return 21
    if _version_key(mc) >= _version_key("1.18"):
        return 17
    if mc.startswith("1.17"):
        return 16
    if _version_key(mc) >= _version_key("1.12"):
        return 8
    return 8


def _fabric_games(refresh: bool = False) -> set[str]:
    try:
        data = http_get_json(FABRIC_GAME, ttl=6 * 3600, refresh=refresh, timeout=30,
                             retries=2, cache_name="fabric-games.json")
        return {v["version"] for v in data}
    except McmodError:
        return set()


def _fabric_loader(refresh: bool = False) -> str:
    try:
        data = http_get_json(FABRIC_LOADER, ttl=6 * 3600, refresh=refresh, timeout=30,
                             retries=2, cache_name="fabric-loader.json")
        stable = [v["version"] for v in data if v.get("stable")]
        if stable:
            return stable[0]
        if data:
            return data[0]["version"]
    except McmodError:
        pass
    return "0.16.10"


def _fabric_api_for(mc: str, refresh: bool = False) -> str:
    try:
        xml = http_get_text(FABRIC_API_META, ttl=24 * 3600, refresh=refresh, timeout=30,
                            retries=2, cache_name="fabric-api-metadata.xml")
        versions = _xml_versions(xml)
        exact = [v for v in versions if v.endswith("+" + mc)]
        if exact:
            return max(exact, key=_version_key)
        # 1.21 -> 1.21.1 compatibility is not guaranteed; return newest and warn.
        return max(versions, key=_version_key) if versions else FABRIC_API_SEED.get(mc, "")
    except McmodError:
        return FABRIC_API_SEED.get(mc, "")


def _fabric_loom(refresh: bool = False, mc: str = "") -> str:
    latest = ""
    try:
        xml = http_get_text(FABRIC_LOOM_META, ttl=24 * 3600, refresh=refresh, timeout=30,
                            retries=2, cache_name="fabric-loom-metadata.xml")
        m = re.search(r"<release>([^<]+)</release>", xml)
        if m and not re.search(r"(?i)alpha|beta", m.group(1)):
            latest = m.group(1).strip()
        else:
            versions = [v for v in _xml_versions(xml) if v.endswith("-SNAPSHOT") or "-beta" in v]
            if versions:
                latest = max(versions, key=_version_key)
    except McmodError:
        pass
    if not latest:
        latest = "1.19.3"
    # Loom 1.19+ requires Gradle 9.8, while pre-26.x Fabric profiles still
    # build with Gradle 8.14. Pin the 8.14-compatible Loom line for them so
    # plugin resolution does not fail before the build even starts.
    if mc and not mc.startswith("26.") and _version_key(mc) < _version_key("26.1"):
        if _version_key(latest) >= _version_key("1.19"):
            return "1.11-SNAPSHOT"
    return latest


def _yarn_for(mc: str, refresh: bool = False) -> str:
    try:
        data = http_get_json(f"https://meta.fabricmc.net/v2/versions/yarn/{mc}",
                             ttl=24 * 3600, cache_name=f"yarn-{mc}.json", refresh=refresh,
                             timeout=30, retries=2)
        if data:
            return data[0]["version"]
    except (McmodError, KeyError, IndexError):
        pass
    return ""


def _forge_versions(refresh: bool = False) -> list[str]:
    try:
        xml = http_get_text(FORGE_META, ttl=6 * 3600, refresh=refresh,
                            cache_name="forge-metadata.xml")
        versions = _xml_versions(xml)
        return versions or list(FORGE_SEED.values())
    except McmodError:
        return list(FORGE_SEED.values())


def _neoforge_versions(refresh: bool = False) -> list[str]:
    # The official maven is unreachable on some university/corporate networks.
    # Try the BMCLAPI mirror first, then the official endpoint.
    for url in (NEOFORGE_META_MIRROR, NEOFORGE_META):
        try:
            xml = http_get_text(url, ttl=6 * 3600, refresh=refresh,
                                cache_name="neoforge-metadata.xml")
            versions = _xml_versions(xml)
            if versions:
                return versions
        except McmodError:
            continue
    return list(NEOFORGE_SEED.values())


def neoforge_mc_prefix(mc: str) -> str:
    if mc.startswith("1."):
        parts = mc.split(".")[1:]
        if len(parts) == 1:
            parts.append("0")
        return ".".join(parts)
    return mc


def _latest_for_mc(versions: list[str], mc: str, loader: str) -> str:
    if loader == "forge":
        matches = [v for v in versions if v.startswith(mc + "-")]
        if not matches:
            return ""
        return max(matches, key=lambda v: _version_key(v.split("-", 1)[1]))
    if loader == "neoforge":
        prefix = neoforge_mc_prefix(mc)
        matches = [v for v in versions if re.match(rf"^{re.escape(prefix)}\.", v)]
        if not matches:
            # Alpha builds may carry +snapshot suffix; ignore them.
            matches = [v for v in versions
                       if re.match(rf"^{re.escape(prefix)}\.", v) and "alpha" not in v.lower()]
        if not matches:
            return ""
        stable = [v for v in matches if "alpha" not in v.lower() and "beta" not in v.lower()]
        pool = stable or matches
        return max(pool, key=_version_key)
    return ""


def _template_for(loader: str, mc: str) -> str:
    key = _version_key(mc)
    if loader == "fabric":
        if key >= _version_key("1.14"):
            return "fabric-modern"
        return "fabric-legacy"
    if loader == "neoforge":
        return "neoforge-modern"
    if loader == "forge":
        if key >= _version_key("1.20.2"):
            return "forge-modern"
        if key >= _version_key("1.17"):
            return "forge-117"
        if key >= _version_key("1.12"):
            return "forge-legacy"
        return "forge-ancient"
    return "generic"


def _tier_for(loader: str, mc: str, loader_version: str) -> str:
    if not loader_version:
        return "unsupported"
    key = _version_key(mc)
    if loader == "fabric" and key < _version_key("1.14"):
        return "design-only"
    if loader == "forge" and key < _version_key("1.7.10"):
        return "design-only"
    # Versions with a maintained modern toolchain and bundled template.
    # 1.21.8 / 26.x 的生成器产物尚未按 1.21.2+ 的 Java 改写(高级 Java 全部
    # 跳过)，标注 full 会误导用户，这里降为 scaffold(基础骨架 + 保守生成)。
    modern_full = {
        ("neoforge", "1.20.4"), ("neoforge", "1.20.6"), ("neoforge", "1.21"),
        ("neoforge", "1.21.1"), ("neoforge", "1.21.4"),
        ("forge", "1.20.1"), ("forge", "1.19.2"), ("forge", "1.18.2"),
        ("forge", "1.12.2"),
        ("fabric", "1.20.1"), ("fabric", "1.21.1"), ("fabric", "1.21.4"),
    }
    if (loader, mc) in modern_full:
        return "full"
    if key >= _version_key("1.7.10"):
        return "scaffold"
    return "design-only"


def resolve(
    loader: str,
    mc: str | None = None,
    *,
    loader_version: str | None = None,
    refresh: bool = False,
) -> VersionProfile:
    loader = loader.lower()
    if loader not in LOADERS:
        raise McmodError(f"Unknown loader: {loader}")

    releases = minecraft_release_ids(refresh=refresh)
    if not mc:
        mc = _default_mc_for(loader, releases, refresh=refresh)

    notes: list[str] = []
    supported_games: set[str] = set()
    if loader == "fabric":
        supported_games = _fabric_games(refresh=refresh)
        if supported_games and mc not in supported_games:
            notes.append(f"Fabric 元数据中没有 {mc} 的正式支持记录。")
        lv = loader_version or _fabric_loader(refresh=refresh)
    elif loader == "forge":
        versions = _forge_versions(refresh=refresh)
        lv = loader_version or _latest_for_mc(versions, mc, "forge")
        if not lv:
            notes.append(f"Forge 没有发布 {mc} 的构建。")
    else:
        versions = _neoforge_versions(refresh=refresh)
        lv = loader_version or _latest_for_mc(versions, mc, "neoforge")
        if not lv:
            notes.append(f"NeoForge 没有发布 {mc} 的构建（NeoForge 从 1.20.2 开始）。")

    java_major = java_major_for_mc(mc, refresh=refresh)
    tier = _tier_for(loader, mc, lv)
    if tier == "unsupported" and mc in supported_games:
        # Fabric metadata may lag but Loom can still build some versions.
        tier = "scaffold"
        notes.append("加载器元数据未列出该版本，尝试按 Loom 模板生成。")

    profile = VersionProfile(
        loader=loader,
        mc=mc,
        loader_version=lv,
        java_major=java_major,
        # ForgeGradle 6 breaks on Gradle 8.9+ with "project services has been
        # closed" while running MCP steps; 8.8 is the last known-good line.
        # Fabric Loom 1.19+ on Java 25 requires Gradle 9.8+.
        gradle_version=("8.8" if loader == "forge"
                        else ("8.14.3" if java_major <= 21
                              else ("9.8" if loader == "fabric" else "9.1.0"))),
        mappings="",
        template=_template_for(loader, mc),
        tier=tier,
        supported=tier in {"full", "scaffold", "legacy"},
        notes=notes,
    )

    if loader == "fabric":
        yarn = _yarn_for(mc, refresh=refresh)
        profile.mappings = yarn or "official"
        if not yarn and (mc.startswith("26.") or _version_key(mc) >= _version_key("26.1")):
            notes.append("Minecraft 26.1+ 的 jar 不再混淆，工程不声明 mappings。")
        elif not yarn:
            notes.append("Yarn 映射不可用，改用官方 Mojang 映射。")
        profile.fabric_api = _fabric_api_for(mc, refresh=refresh)
        if not profile.fabric_api:
            notes.append("Fabric API 版本未解析，需在构建时补齐。")
        profile.loom = _fabric_loom(refresh=refresh, mc=mc)
    elif loader == "forge" and lv:
        # Forge artifact versions normally include the MC prefix already
        # (1.20.1-47.4.5); older manifests may only carry the build number.
        full = lv if lv.startswith(f"{mc}-") else f"{mc}-{lv}"
        profile.mdk_url = (
            f"https://maven.minecraftforge.net/net/minecraftforge/forge/{full}/"
            f"forge-{full}-mdk.zip"
        )
        profile.mdk_mirror = (
            f"https://mirror.minguncle.cn/maven.minecraftforge.net/net/minecraftforge/forge/{full}/"
            f"forge-{full}-mdk.zip"
        )
    # NeoForge no longer ships a runnable MDK zip for modern versions; the
    # built-in ModDevGradle template is the supported path.
    return profile


def _default_mc_for(loader: str, releases: list[str], *, refresh: bool = False) -> str:
    if loader == "fabric":
        games = _fabric_games(refresh=refresh)
        for mc in releases:
            if not games or mc in games:
                return mc
        return releases[0]
    if loader == "forge":
        versions = _forge_versions(refresh=refresh)
        for mc in releases:
            if _latest_for_mc(versions, mc, "forge"):
                return mc
        return "1.20.1"
    versions = _neoforge_versions(refresh=refresh)
    for mc in releases:
        if _latest_for_mc(versions, mc, "neoforge"):
            return mc
    return "1.21.1"


def matrix(loaders: list[str] | None = None, limit: int = 30, refresh: bool = False) -> list[dict[str, Any]]:
    loaders = loaders or list(LOADERS)
    releases = minecraft_release_ids(refresh=refresh)[:limit]
    out = []
    for mc in releases:
        row: dict[str, Any] = {"mc": mc, "java": java_major_for_mc(mc, refresh=refresh)}
        for loader in loaders:
            try:
                p = resolve(loader, mc, refresh=refresh)
                row[loader] = p.loader_version
                row[f"{loader}_tier"] = p.tier
            except McmodError:
                row[loader] = ""
        out.append(row)
    return out


def suggestions(loader: str, mc: str) -> dict[str, Any]:
    """Nearest supported versions for an unsupported combo."""
    releases = minecraft_release_ids()
    idx = releases.index(mc) if mc in releases else -1
    out: dict[str, Any] = {"mc": [], "loaders": []}
    if idx >= 0:
        out["mc"] = releases[max(0, idx - 4): idx + 5]
    for alt in LOADERS:
        try:
            p = resolve(alt, mc)
            if p.supported:
                out["loaders"].append({"loader": alt, "version": p.loader_version})
        except McmodError:
            pass
    return out


def profile_to_markdown(p: VersionProfile) -> str:
    lines = [
        f"# {LOADER_LABEL[p.loader]} {p.mc}",
        "",
        f"- 加载器版本：`{p.loader_version or '未解析'}`",
        f"- Java：`{p.java_major}`",
        f"- Gradle：`{p.gradle_version}`",
        f"- 模板：`{p.template}`",
        f"- 能力等级：`{p.tier}`",
    ]
    if p.loader == "fabric":
        lines += [f"- Yarn：`{p.mappings}`", f"- Fabric API：`{p.fabric_api or '未解析'}`",
                  f"- Loom：`{p.loom or '未解析'}`"]
    if p.mdk_url:
        lines.append(f"- MDK：`{p.mdk_url}`")
    if p.notes:
        lines += ["", "## 注意", ""] + [f"- {n}" for n in p.notes]
    return "\n".join(lines) + "\n"
