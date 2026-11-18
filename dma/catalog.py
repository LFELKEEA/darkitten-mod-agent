"""Minecraft 开发生态目录。

把做模组会用到的版本与资料集中到一个可持久化的 JSON 目录里：

- Mojang 版本清单、每个版本的 Java 要求、客户端/服务端下载地址；
- Fabric / Quilt 的游戏版本、加载器、安装器、Yarn / Quilt Mappings；
- Forge 的 Maven 版本与 promotions；
- NeoForge 的 Maven 版本；
- 各加载器的 MDK / 模板包地址；
- 各加载器的 Javadoc 构件地址；
- Mojang 与各加载器的 changelog；
- Gradle 当前版本与 Adoptium 可用 JDK。

网络不可用时保留上一次成功同步的数据，不会把目录清空。所有请求都有
硬超时，后台线程可以直接取消。
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import netclient, paths

CATALOG_VERSION = 1
USER_AGENT = "DarkittenModAgent/3.2 (+https://lfelkeea.github.io/darkitten-launcher/)"
DEFAULT_TTL = 6 * 3600
MAX_BYTES = 32 * 1024 * 1024

MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
MOJANG_PATCH_NOTES = "https://launchercontent.mojang.com/v2/javaPatchNotes.json"
FABRIC_GAME = "https://meta.fabricmc.net/v2/versions/game"
FABRIC_LOADER = "https://meta.fabricmc.net/v2/versions/loader"
FABRIC_INSTALLER = "https://meta.fabricmc.net/v2/versions/installer"
FABRIC_YARN = "https://meta.fabricmc.net/v2/versions/yarn/{mc}"
FABRIC_API_META = "https://maven.fabricmc.net/net/fabricmc/fabric-api/fabric-api/maven-metadata.xml"
# meta.fabricmc.net 在国内经常连不上，BMCLAPI 有完整镜像。
FABRIC_GAME_MIRROR = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/game"
FABRIC_LOADER_MIRROR = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/loader"
FABRIC_INSTALLER_MIRROR = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/installer"
FABRIC_YARN_MIRROR = "https://bmclapi2.bangbang93.com/fabric-meta/v2/versions/yarn/{mc}"
QUILT_GAME = "https://meta.quiltmc.org/v3/versions/game"
QUILT_LOADER = "https://meta.quiltmc.org/v3/versions/loader"
QUILT_INSTALLER = "https://meta.quiltmc.org/v3/versions/installer"
QUILT_MAPPINGS = "https://meta.quiltmc.org/v3/versions/quilt-mappings/{mc}"
FORGE_META = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
FORGE_META_MIRROR = "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/maven-metadata.xml"
FORGE_PROMOS = "https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json"
NEOFORGE_META = "https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml"
NEOFORGE_META_MIRROR = "https://mirror4.nyist.edu.cn/bmclapi/net/neoforged/neoforge/maven-metadata.xml"
NEOFORGE_META_BMCL = "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/maven-metadata.xml"
GRADLE_CURRENT = "https://services.gradle.org/versions/current"
ADOPTIUM_RELEASES = "https://api.adoptium.net/v3/info/available_releases"

LOADERS = ("neoforge", "forge", "fabric", "quilt")
LOADER_LABEL = {
    "neoforge": "NeoForge",
    "forge": "Forge",
    "fabric": "Fabric",
    "quilt": "Quilt",
}

CHANGELOG_REPOS = {
    "fabric": ("FabricMC/fabric-loader", "Fabric Loader"),
    "quilt": ("QuiltMC/quilt-loader", "Quilt Loader"),
    "forge": ("MinecraftForge/MinecraftForge", "Forge"),
    "neoforge": ("neoforged/NeoForge", "NeoForge"),
}
# 只有 Fabric 在 GitHub 上维护 Releases；Forge / NeoForge / Quilt 的版本记录
# 主要在 Maven 元数据里，所以没有 Release 时按这里的顺序回退。
CHANGELOG_MAVEN = {
    "fabric": ("https://maven.fabricmc.net/net/fabricmc/fabric-loader/maven-metadata.xml",),
    "quilt": (
        "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-loader/maven-metadata.xml",
    ),
    "forge": (FORGE_META_MIRROR, FORGE_META),
    "neoforge": (NEOFORGE_META_BMCL, NEOFORGE_META_MIRROR, NEOFORGE_META),
}
CHANGELOG_SOURCE_LABEL = {
    "releases": "GitHub Releases",
    "maven": "Maven 版本库",
    "tags": "GitHub Tags",
}

Progress = Callable[[str], None]


class CatalogError(RuntimeError):
    pass


def _now() -> float:
    return time.time()


def _stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _log(progress: Progress | None, message: str) -> None:
    if progress is None:
        return
    try:
        progress(message)
    except Exception:  # noqa: BLE001 - 日志回调不能影响同步
        pass


def _xml_versions(text: str) -> list[str]:
    try:
        root = ET.fromstring(text)
        return [str(el.text).strip() for el in root.iter("version") if el.text]
    except ET.ParseError:
        return re.findall(r"<version>([^<]+)</version>", text or "")


def _version_key(value: str) -> tuple:
    parts = re.split(r"[.\-+_]", str(value or ""))
    out: list[tuple] = []
    for part in parts:
        match = re.match(r"^(\d+)(.*)$", part)
        if match:
            out.append((0, int(match.group(1)), match.group(2)))
        elif part:
            out.append((1, 0, part))
    return tuple(out)


def _latest(items: list[str]) -> str:
    return max(items, key=_version_key) if items else ""


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _release_entries(data: Any) -> list[dict[str, Any]]:
    releases: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        releases.append(
            {
                "tag": str(item.get("tag_name") or ""),
                "name": str(item.get("name") or ""),
                "published": str(item.get("published_at") or ""),
                "url": str(item.get("html_url") or ""),
                "prerelease": bool(item.get("prerelease")),
                "body": str(item.get("body") or "")[:60_000],
                "source": "releases",
            }
        )
    return releases


def _version_entries(
    label: str, repo: str, versions: list[str], source: str
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for version in versions:
        text = str(version or "").strip()
        if not text:
            continue
        note = (
            f"{label} {text}\n\n"
            "该加载器没有在 GitHub 上维护 Release 说明，"
            "这里显示的是版本库中的版本记录。"
        )
        entries.append(
            {
                "tag": text,
                "name": f"{label} {text}",
                "published": "",
                "url": f"https://github.com/{repo}/tags",
                "prerelease": any(
                    key in text.lower() for key in ("alpha", "beta", "snapshot", "-pre")
                ),
                "body": note,
                "source": source,
            }
        )
    return entries


def _tag_entries(data: Any, repo: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        entries.append(
            {
                "tag": name,
                "name": name,
                "published": "",
                "url": f"https://github.com/{repo}/tree/{name}",
                "prerelease": any(
                    key in name.lower() for key in ("alpha", "beta", "snapshot", "-pre")
                ),
                "body": "该仓库没有 Release 说明，这里显示版本标签。",
                "source": "tags",
            }
        )
    return entries


def _request(url: str, *, headers: dict[str, str] | None = None) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if headers:
        merged.update(headers)
    return urllib.request.Request(url, headers=merged)


def fetch_text(
    url: str,
    *,
    timeout: float = 25.0,
    limit: int = MAX_BYTES,
    headers: dict[str, str] | None = None,
) -> str:
    raw, _status, _headers = netclient.fetch_info(
        _request(url, headers=headers), timeout=timeout
    )
    return raw[:limit].decode("utf-8", "replace")


def fetch_json(
    url: str,
    *,
    timeout: float = 25.0,
    headers: dict[str, str] | None = None,
) -> Any:
    text = fetch_text(url, timeout=timeout, headers=headers)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CatalogError(f"接口返回的不是 JSON：{text[:160]}") from exc


def cache_dir() -> Path:
    path = paths.data_dir() / "catalog"
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifacts_dir() -> Path:
    path = paths.data_dir() / "catalog" / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_path() -> Path:
    return cache_dir() / "catalog.json"


@dataclass
class SyncEntry:
    name: str
    ok: bool = False
    message: str = ""
    count: int = 0
    updated: str = ""


@dataclass
class SyncReport:
    started: str
    finished: str = ""
    entries: list[SyncEntry] = field(default_factory=list)
    cancelled: bool = False

    @property
    def ok(self) -> int:
        return sum(1 for item in self.entries if item.ok)

    def text(self) -> str:
        lines = [f"目录同步：{self.ok}/{len(self.entries)} 项成功"]
        for item in self.entries:
            flag = "OK" if item.ok else "跳过"
            detail = f" · {item.count} 条" if item.count else ""
            extra = f" · {item.message}" if item.message else ""
            lines.append(f"[{flag}] {item.name}{detail}{extra}")
        return "\n".join(lines)


class Catalog:
    """线程安全的本地目录。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else snapshot_path()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._load()

    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> None:
        with self._lock:
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            if not isinstance(raw, dict):
                raw = {}
            raw.setdefault("catalog_version", CATALOG_VERSION)
            raw.setdefault("updated", "")
            raw.setdefault("sections", {})
            raw.setdefault("cache", {})
            self._data = raw

    def _save(self) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._path.with_name(self._path.name + ".tmp")
            temp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def section(self, name: str) -> dict[str, Any]:
        with self._lock:
            value = self._data.setdefault("sections", {}).get(name)
            return value if isinstance(value, dict) else {}

    def _put(self, name: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data.setdefault("sections", {})[name] = value
            self._data["updated"] = _stamp()
            self._save()

    def _cache_get(self, key: str, ttl: float) -> dict[str, Any] | None:
        with self._lock:
            item = self._data.setdefault("cache", {}).get(key)
        if not isinstance(item, dict):
            return None
        try:
            if _now() - float(item.get("fetched") or 0) > ttl:
                return None
        except (TypeError, ValueError):
            return None
        return item

    def _cache_put(self, key: str, data: Any, etag: str = "") -> None:
        with self._lock:
            self._data.setdefault("cache", {})[key] = {
                "fetched": _now(),
                "etag": etag,
                "data": data,
            }
            self._save()

    def _cached(
        self,
        key: str,
        url: str,
        loader: Callable[[str], Any],
        *,
        ttl: float = DEFAULT_TTL,
        force: bool = False,
        timeout: float = 25.0,
    ) -> Any:
        if not force:
            cached = self._cache_get(key, ttl)
            if cached is not None:
                return cached.get("data")
        etag = ""
        cached = self._cache_get(key, ttl * 24)
        if cached:
            etag = str(cached.get("etag") or "")
        headers = {"If-None-Match": etag} if etag else None
        try:
            text = fetch_text(url, timeout=timeout, headers=headers)
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached is not None:
                with self._lock:
                    self._data["cache"][key]["fetched"] = _now()
                    self._save()
                return cached.get("data")
            raise
        data = loader(text)
        self._cache_put(key, data, etag)
        return data

    def _cached_any(
        self,
        key: str,
        urls: tuple[str, ...] | list[str],
        loader: Callable[[str], Any],
        *,
        ttl: float = DEFAULT_TTL,
        force: bool = False,
        timeout: float = 25.0,
    ) -> Any:
        """按顺序尝试多个镜像，返回第一个成功的结果。"""
        last: Exception | None = None
        for url in urls:
            try:
                return self._cached(
                    f"{key}-{url}", url, loader, ttl=ttl, force=force, timeout=timeout
                )
            except Exception as exc:  # noqa: BLE001 - 逐个镜像降级
                last = exc
        if last is not None:
            raise last
        raise CatalogError(f"{key} 没有可用的下载地址。")

    # ------------------------------------------------------------------
    # Mojang
    # ------------------------------------------------------------------
    def sync_mojang(self, progress: Progress | None = None, force: bool = False) -> SyncEntry:
        entry = SyncEntry("Mojang 版本清单")
        try:
            data = self._cached(
                "mojang-manifest",
                MOJANG_MANIFEST,
                json.loads,
                ttl=3 * 3600,
                force=force,
            )
            versions = []
            for item in data.get("versions") or []:
                if not isinstance(item, dict) or not item.get("id"):
                    continue
                versions.append(
                    {
                        "id": str(item.get("id")),
                        "type": str(item.get("type") or "release"),
                        "url": str(item.get("url") or ""),
                        "time": str(item.get("releaseTime") or ""),
                        "sha1": str(item.get("sha1") or ""),
                        "compliance": item.get("complianceLevel"),
                    }
                )
            section = {
                "latest": data.get("latest") or {},
                "versions": versions,
                "releases": [v["id"] for v in versions if v["type"] == "release"],
                "snapshots": [v["id"] for v in versions if v["type"] == "snapshot"],
                "updated": _stamp(),
            }
            self._put("mojang", section)
            entry.ok = bool(versions)
            entry.count = len(versions)
            _log(progress, f"Mojang：{len(versions)} 个版本")
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响其它源
            entry.message = str(exc)[:240]
            _log(progress, f"Mojang 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    def sync_mojang_patch_notes(
        self, progress: Progress | None = None, force: bool = False
    ) -> SyncEntry:
        entry = SyncEntry("Mojang 更新日志")
        try:
            data = self._cached(
                "mojang-patch-notes",
                MOJANG_PATCH_NOTES,
                json.loads,
                ttl=6 * 3600,
                force=force,
            )
            notes = []
            entries = data.get("entries") if isinstance(data, dict) else None
            for item in entries or []:
                if not isinstance(item, dict):
                    continue
                notes.append(
                    {
                        "version": str(item.get("version") or item.get("id") or ""),
                        "title": str(item.get("title") or ""),
                        "date": str(item.get("date") or ""),
                        "body": str(item.get("body") or "")[:200_000],
                        "url": str(item.get("url") or ""),
                        "image": str(item.get("image") or ""),
                    }
                )
            self._put(
                "mojang_changelog",
                {"entries": notes[:80], "updated": _stamp()},
            )
            entry.ok = bool(notes)
            entry.count = len(notes[:80])
            _log(progress, f"Mojang changelog：{entry.count} 条")
        except Exception as exc:  # noqa: BLE001
            entry.message = str(exc)[:240]
            _log(progress, f"Mojang changelog 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    # ------------------------------------------------------------------
    # Fabric / Quilt
    # ------------------------------------------------------------------
    def sync_fabric(self, progress: Progress | None = None, force: bool = False) -> SyncEntry:
        entry = SyncEntry("Fabric")
        try:
            game = self._cached_any(
                "fabric-game", (FABRIC_GAME, FABRIC_GAME_MIRROR), json.loads, force=force
            )
            loader = self._cached_any(
                "fabric-loader", (FABRIC_LOADER, FABRIC_LOADER_MIRROR), json.loads, force=force
            )
            installer = self._cached_any(
                "fabric-installer",
                (FABRIC_INSTALLER, FABRIC_INSTALLER_MIRROR),
                json.loads,
                force=force,
            )
            try:
                api_xml = self._cached(
                    "fabric-api",
                    FABRIC_API_META,
                    lambda text: text,
                    force=force,
                )
            except Exception:  # noqa: BLE001 - Fabric API 只是推荐项，失败不影响版本清单
                api_xml = ""
            games = [
                str(item.get("version"))
                for item in game
                if isinstance(item, dict) and item.get("version")
            ]
            loaders = [
                {
                    "version": str(item.get("version") or ""),
                    "stable": bool(item.get("stable")),
                }
                for item in loader
                if isinstance(item, dict) and item.get("version")
            ]
            installers = [
                str(item.get("version"))
                for item in installer
                if isinstance(item, dict) and item.get("version")
            ]
            section = {
                "game": games,
                "loader": loaders,
                "loader_latest": _latest([x["version"] for x in loaders if x["stable"]])
                or _latest_release([x["version"] for x in loaders]),
                "installer": installers,
                "installer_latest": _latest(installers),
                "fabric_api": _unique(_xml_versions(api_xml)),
                "yarn": {},
                "updated": _stamp(),
            }
            self._put("fabric", section)
            entry.ok = bool(games and loaders)
            entry.count = len(games) + len(loaders)
            _log(progress, f"Fabric：{len(games)} 游戏版本 / {len(loaders)} 加载器")
        except Exception as exc:  # noqa: BLE001
            entry.message = str(exc)[:240]
            _log(progress, f"Fabric 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    def sync_quilt(self, progress: Progress | None = None, force: bool = False) -> SyncEntry:
        entry = SyncEntry("Quilt")
        try:
            game = self._cached("quilt-game", QUILT_GAME, json.loads, force=force)
            loader = self._cached("quilt-loader", QUILT_LOADER, json.loads, force=force)
            installer = self._cached(
                "quilt-installer", QUILT_INSTALLER, json.loads, force=force
            )
            games = [
                str(item.get("version"))
                for item in game
                if isinstance(item, dict) and item.get("version")
            ]
            loaders = [
                {
                    "version": str(item.get("version") or ""),
                    "stable": bool(item.get("stable")),
                }
                for item in loader
                if isinstance(item, dict) and item.get("version")
            ]
            installers = [
                str(item.get("version"))
                for item in installer
                if isinstance(item, dict) and item.get("version")
            ]
            section = {
                "game": games,
                "loader": loaders,
                "loader_latest": _latest([x["version"] for x in loaders if x["stable"]])
                or _latest_release([x["version"] for x in loaders]),
                "installer": installers,
                "installer_latest": _latest(installers),
                "mappings": {},
                "updated": _stamp(),
            }
            self._put("quilt", section)
            entry.ok = bool(games and loaders)
            entry.count = len(games) + len(loaders)
            _log(progress, f"Quilt：{len(games)} 游戏版本 / {len(loaders)} 加载器")
        except Exception as exc:  # noqa: BLE001
            entry.message = str(exc)[:240]
            _log(progress, f"Quilt 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    def sync_yarn(self, mc: str, force: bool = False) -> str:
        mc = str(mc or "").strip()
        if not mc:
            return ""
        key = f"yarn-{mc}"
        data = self._cached_any(
            key,
            (
                FABRIC_YARN.format(mc=urllib.parse.quote(mc)),
                FABRIC_YARN_MIRROR.format(mc=urllib.parse.quote(mc)),
            ),
            json.loads,
            ttl=24 * 3600,
            force=force,
        )
        if isinstance(data, list) and data and isinstance(data[0], dict):
            version = str(data[0].get("version") or "")
            if version:
                with self._lock:
                    section = self._data.setdefault("sections", {}).setdefault("fabric", {})
                    if isinstance(section, dict):
                        section.setdefault("yarn", {})[mc] = version
                        self._save()
            return version
        return ""

    def sync_quilt_mappings(self, mc: str, force: bool = False) -> str:
        mc = str(mc or "").strip()
        if not mc:
            return ""
        key = f"quilt-mappings-{mc}"
        data = self._cached(
            key,
            QUILT_MAPPINGS.format(mc=urllib.parse.quote(mc)),
            json.loads,
            ttl=24 * 3600,
            force=force,
        )
        if isinstance(data, list) and data and isinstance(data[0], dict):
            version = str(data[0].get("version") or "")
            if version:
                with self._lock:
                    section = self._data.setdefault("sections", {}).setdefault("quilt", {})
                    if isinstance(section, dict):
                        section.setdefault("mappings", {})[mc] = version
                        self._save()
            return version
        return ""

    # ------------------------------------------------------------------
    # Forge / NeoForge
    # ------------------------------------------------------------------
    def sync_forge(self, progress: Progress | None = None, force: bool = False) -> SyncEntry:
        entry = SyncEntry("Forge")
        versions: list[str] = []
        promos: dict[str, str] = {}
        error = ""
        try:
            xml = self._cached_any(
                "forge-metadata",
                (FORGE_META, FORGE_META_MIRROR),
                lambda text: text,
                force=force,
            )
            versions = _xml_versions(xml)
        except Exception as exc:  # noqa: BLE001 - 官方 Maven 常被墙，先用 promotions 兜底
            error = str(exc)
        try:
            data = self._cached(
                "forge-promotions", FORGE_PROMOS, json.loads, force=force
            )
            if isinstance(data, dict) and isinstance(data.get("promos"), dict):
                promos = {
                    str(key): str(value)
                    for key, value in data["promos"].items()
                    if str(value).strip()
                }
        except Exception as exc:  # noqa: BLE001
            error = error or str(exc)
        if not versions and promos:
            versions = sorted(
                {
                    f"{key.rsplit('-', 1)[0]}-{value}"
                    for key, value in promos.items()
                    if key.endswith(("-recommended", "-latest"))
                }
            )
        if versions:
            section = {
                "versions": versions,
                "promotions": promos,
                "latest": _latest(versions),
                "updated": _stamp(),
            }
            self._put("forge", section)
            entry.ok = True
            entry.count = len(versions)
            _log(progress, f"Forge：{len(versions)} 个版本")
        else:
            entry.message = (error or "没有拿到 Forge 版本清单")[:240]
            _log(progress, f"Forge 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    def sync_neoforge(
        self, progress: Progress | None = None, force: bool = False
    ) -> SyncEntry:
        entry = SyncEntry("NeoForge")
        last_error = ""
        for url in (NEOFORGE_META_MIRROR, NEOFORGE_META_BMCL, NEOFORGE_META):
            try:
                xml = self._cached(
                    f"neoforge-metadata-{url}",
                    url,
                    lambda text: text,
                    force=force,
                )
                versions = _xml_versions(xml)
                if not versions:
                    continue
                self._put(
                    "neoforge",
                    {
                        "versions": versions,
                        "latest": _latest(versions),
                        "source": url,
                        "updated": _stamp(),
                    },
                )
                entry.ok = True
                entry.count = len(versions)
                _log(progress, f"NeoForge：{len(versions)} 个版本")
                break
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
        if not entry.ok:
            entry.message = last_error[:240]
            _log(progress, f"NeoForge 同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    # ------------------------------------------------------------------
    # 工具链
    # ------------------------------------------------------------------
    def sync_toolchain(
        self, progress: Progress | None = None, force: bool = False
    ) -> SyncEntry:
        entry = SyncEntry("Gradle / JDK")
        try:
            gradle = self._cached(
                "gradle-current", GRADLE_CURRENT, json.loads, ttl=24 * 3600, force=force
            )
            adoptium = self._cached(
                "adoptium-releases",
                ADOPTIUM_RELEASES,
                json.loads,
                ttl=24 * 3600,
                force=force,
            )
            section = {
                "gradle": {
                    "current": str(gradle.get("version") or ""),
                    "download_url": str(gradle.get("downloadUrl") or ""),
                    "checksum_url": str(gradle.get("checksumUrl") or ""),
                    "wrapper_checksum_url": str(
                        gradle.get("wrapperChecksumUrl") or ""
                    ),
                },
                "adoptium": {
                    "available_releases": adoptium.get("available_releases") or [],
                    "available_lts_releases": adoptium.get(
                        "available_lts_releases"
                    )
                    or [],
                    "most_recent_lts": adoptium.get("most_recent_lts") or 0,
                },
                "updated": _stamp(),
            }
            self._put("toolchain", section)
            entry.ok = bool(section["gradle"]["current"])
            entry.count = 1
            _log(
                progress,
                f"工具链：Gradle {section['gradle']['current'] or '未知'}"
                f" / JDK LTS {section['adoptium']['most_recent_lts'] or '未知'}",
            )
        except Exception as exc:  # noqa: BLE001
            entry.message = str(exc)[:240]
            _log(progress, f"工具链同步失败：{entry.message}")
        entry.updated = _stamp()
        return entry

    # ------------------------------------------------------------------
    # Changelog
    # ------------------------------------------------------------------
    def sync_changelogs(
        self, progress: Progress | None = None, force: bool = False
    ) -> SyncEntry:
        entry = SyncEntry("加载器更新日志")
        total = 0
        errors: list[str] = []
        for loader, (repo, label) in CHANGELOG_REPOS.items():
            releases: list[dict[str, Any]] = []
            source = ""
            problem = ""
            # 1. GitHub Releases：只有 Fabric 长期维护发布说明。
            try:
                url = f"https://api.github.com/repos/{repo}/releases?per_page=12"
                data = self._cached(
                    f"changelog-{loader}",
                    url,
                    json.loads,
                    ttl=6 * 3600,
                    force=force,
                )
                releases = _release_entries(data)
                if releases:
                    source = "releases"
            except Exception as exc:  # noqa: BLE001 - 继续尝试下一个来源
                problem = str(exc)[:120]
            # 2. Maven 元数据：Forge / NeoForge / Quilt 的主要版本记录来源。
            if not releases:
                meta_urls = CHANGELOG_MAVEN.get(loader) or ()
                if meta_urls:
                    try:
                        text = self._cached_any(
                            f"changelog-meta-{loader}",
                            meta_urls,
                            lambda value: value,
                            ttl=6 * 3600,
                            force=force,
                        )
                        versions = sorted(
                            _unique(_xml_versions(str(text))), key=_version_key
                        )
                        if versions:
                            releases = _version_entries(
                                label, repo, versions[-12:][::-1], "maven"
                            )
                            source = "maven"
                    except Exception as exc:  # noqa: BLE001 - 继续尝试下一个来源
                        problem = (problem + "；" if problem else "") + str(exc)[:120]
            # 3. GitHub Tags：最后的兜底，至少有版本号和时间顺序。
            if not releases:
                try:
                    url = f"https://api.github.com/repos/{repo}/tags?per_page=12"
                    data = self._cached(
                        f"changelog-tags-{loader}",
                        url,
                        json.loads,
                        ttl=6 * 3600,
                        force=force,
                    )
                    releases = _tag_entries(data, repo)
                    if releases:
                        source = "tags"
                except Exception as exc:  # noqa: BLE001
                    problem = (problem + "；" if problem else "") + str(exc)[:120]
            if not releases:
                errors.append(f"{label}: {problem or '没有可用的版本记录'}")
                continue
            with self._lock:
                section = self._data.setdefault("sections", {}).setdefault(
                    "changelogs", {}
                )
                if not isinstance(section, dict):
                    section = {}
                    self._data["sections"]["changelogs"] = section
                section[loader] = {
                    "label": label,
                    "repo": repo,
                    "source": source,
                    "releases": releases,
                    "updated": _stamp(),
                }
                self._data["updated"] = _stamp()
                self._save()
            total += len(releases)
            _log(
                progress,
                f"{label} 更新日志：{len(releases)} 条"
                f"（{CHANGELOG_SOURCE_LABEL.get(source, source)}）",
            )
        entry.ok = total > 0
        entry.count = total
        if errors:
            entry.message = "；".join(errors)[:240]
        _log(progress, f"更新日志：{total} 条")
        entry.updated = _stamp()
        return entry

    # ------------------------------------------------------------------
    # 完整同步
    # ------------------------------------------------------------------
    def refresh(
        self,
        progress: Progress | None = None,
        *,
        force: bool = False,
        cancel: threading.Event | None = None,
    ) -> SyncReport:
        report = SyncReport(started=_stamp())
        steps = [
            self.sync_mojang,
            self.sync_mojang_patch_notes,
            self.sync_fabric,
            self.sync_quilt,
            self.sync_forge,
            self.sync_neoforge,
            self.sync_toolchain,
            self.sync_changelogs,
        ]
        for step in steps:
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                break
            report.entries.append(step(progress, force=force))
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                break
        report.finished = _stamp()
        _log(progress, report.text())
        return report

    # ------------------------------------------------------------------
    # 查询与派生地址
    # ------------------------------------------------------------------
    def minecraft_versions(self, kind: str = "release") -> list[dict[str, Any]]:
        section = self.section("mojang")
        items = section.get("versions") or []
        if kind in ("release", "snapshot"):
            return [item for item in items if item.get("type") == kind]
        return list(items)

    def minecraft_ids(self, kind: str = "release") -> list[str]:
        return [str(item.get("id")) for item in self.minecraft_versions(kind) if item.get("id")]

    def version_info(self, version_id: str, force: bool = False) -> dict[str, Any]:
        version_id = str(version_id or "").strip()
        if not version_id:
            return {}
        for item in self.minecraft_versions("all"):
            if item.get("id") == version_id:
                url = str(item.get("url") or "")
                if not url:
                    return item
                try:
                    data = self._cached(
                        f"mc-version-{version_id}",
                        url,
                        json.loads,
                        ttl=30 * 24 * 3600,
                        force=force,
                        timeout=20.0,
                    )
                    return {
                        **item,
                        "java": (data.get("javaVersion") or {}).get("majorVersion"),
                        "downloads": data.get("downloads") or {},
                        "asset_index": data.get("assetIndex") or {},
                        "libraries": data.get("libraries") or [],
                        "main_class": data.get("mainClass") or "",
                        "type": data.get("type") or item.get("type"),
                    }
                except Exception:  # noqa: BLE001
                    return item
        return {}

    def fabric_yarn(self, mc: str, force: bool = False) -> str:
        cached = self.section("fabric").get("yarn", {}).get(mc)
        if cached and not force:
            return str(cached)
        try:
            return self.sync_yarn(mc, force=force)
        except Exception:  # noqa: BLE001
            return ""

    def quilt_mappings(self, mc: str, force: bool = False) -> str:
        cached = self.section("quilt").get("mappings", {}).get(mc)
        if cached and not force:
            return str(cached)
        try:
            return self.sync_quilt_mappings(mc, force=force)
        except Exception:  # noqa: BLE001
            return ""

    def loader_versions(self, loader: str) -> list[str]:
        loader = str(loader or "").lower()
        if loader == "forge":
            return list(self.section("forge").get("versions") or [])
        if loader == "neoforge":
            return list(self.section("neoforge").get("versions") or [])
        if loader == "fabric":
            return [
                str(item.get("version"))
                for item in self.section("fabric").get("loader") or []
                if isinstance(item, dict)
            ]
        if loader == "quilt":
            return [
                str(item.get("version"))
                for item in self.section("quilt").get("loader") or []
                if isinstance(item, dict)
            ]
        return []

    def loader_for_mc(self, loader: str, mc: str) -> str:
        loader = str(loader or "").lower()
        mc = str(mc or "").strip()
        if loader == "forge":
            exact = [
                version
                for version in self.loader_versions("forge")
                if version.startswith(mc + "-")
            ]
            if exact:
                return max(exact, key=_version_key).split("-", 1)[1]
            promos = self.section("forge").get("promotions") or {}
            for key in (f"{mc}-recommended", f"{mc}-latest"):
                value = str(promos.get(key) or "")
                if value:
                    return value
        if loader == "neoforge":
            prefix = _neoforge_prefix(mc)
            exact = [
                version
                for version in self.loader_versions("neoforge")
                if version.startswith(prefix + ".")
            ]
            if exact:
                return max(exact, key=_version_key)
        if loader == "fabric":
            section = self.section("fabric")
            stable = [
                item.get("version")
                for item in section.get("loader") or []
                if isinstance(item, dict) and item.get("stable")
            ]
            return (
                _latest(stable)
                or str(section.get("loader_latest") or "")
                or _latest_release(self.loader_versions("fabric"))
            )
        if loader == "quilt":
            section = self.section("quilt")
            stable = [
                item.get("version")
                for item in section.get("loader") or []
                if isinstance(item, dict) and item.get("stable")
            ]
            return (
                _latest(stable)
                or str(section.get("loader_latest") or "")
                or _latest_release(self.loader_versions("quilt"))
            )
        return ""

    def mdk(self, loader: str, mc: str, version: str = "") -> dict[str, Any]:
        loader = str(loader or "").lower()
        mc = str(mc or "").strip()
        version = str(version or "").strip() or self.loader_for_mc(loader, mc)
        if loader == "forge" and mc and version:
            full = f"{mc}-{version}"
            return {
                "loader": "forge",
                "mc": mc,
                "version": version,
                "name": f"forge-{full}-mdk.zip",
                "url": (
                    "https://maven.minecraftforge.net/net/minecraftforge/forge/"
                    f"{full}/forge-{full}-mdk.zip"
                ),
                "mirror": (
                    "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/"
                    f"{full}/forge-{full}-mdk.zip"
                ),
            }
        if loader == "neoforge" and version:
            # NeoForge 不在 Maven 发布 mdk.zip，官方 MDK 由 NeoForgeMDKs 组织按版本维护。
            base = "https://github.com/NeoForgeMDKs"
            mdg = f"{base}/MDK-{mc}-ModDevGradle/archive/refs/heads/main.zip"
            neo = f"{base}/MDK-{mc}-NeoGradle/archive/refs/heads/main.zip"
            prefix = _neoforge_prefix(mc)
            mdg_prefix = f"{base}/MDK-{prefix}-ModDevGradle/archive/refs/heads/main.zip"
            legacy = (
                f"{base}/MDK-Forge-{mc}-ModDevGradle/archive/refs/heads/main.zip"
                if mc == "1.20.1"
                else ""
            )
            urls = [item for item in (legacy, mdg, neo, mdg_prefix) if item]
            return {
                "loader": "neoforge",
                "mc": mc,
                "version": version,
                "name": f"neoforge-{mc}-mdk.zip",
                "url": urls[0],
                "mirrors": urls[1:],
                "docs_url": "https://docs.neoforged.net/",
            }
        if loader == "fabric":
            branch = mc if mc and mc.startswith("1.") else "main"
            return {
                "loader": "fabric",
                "mc": mc,
                "version": version or str(self.section("fabric").get("loader_latest") or ""),
                "name": f"fabric-example-mod-{branch}.zip",
                "url": (
                    "https://github.com/FabricMC/fabric-example-mod/archive/refs/"
                    f"heads/{urllib.parse.quote(branch)}.zip"
                ),
                "template": True,
            }
        if loader == "quilt":
            branch = _quilt_branch(mc)
            return {
                "loader": "quilt",
                "mc": mc,
                "version": version or str(self.section("quilt").get("loader_latest") or ""),
                "name": f"quilt-template-mod-{branch}.zip",
                "url": (
                    "https://github.com/QuiltMC/quilt-template-mod/archive/refs/"
                    f"heads/{urllib.parse.quote(branch)}.zip"
                ),
                "mirrors": [
                    "https://github.com/QuiltMC/quilt-template-mod/archive/refs/"
                    "heads/1.20.6.zip"
                ],
                "template": True,
            }
        return {}

    def javadoc(self, loader: str, mc: str = "", version: str = "") -> dict[str, Any]:
        loader = str(loader or "").lower()
        version = str(version or "").strip() or self.loader_for_mc(loader, mc)
        if loader == "forge" and mc and version:
            # Forge 只发布 sources.jar，没有 javadoc.jar；用源码包代替，配合官方文档站学习。
            full = f"{mc}-{version}"
            return {
                "loader": "forge",
                "mc": mc,
                "version": version,
                "kind": "sources",
                "name": f"forge-{full}-sources.jar",
                "url": (
                    "https://maven.minecraftforge.net/net/minecraftforge/forge/"
                    f"{full}/forge-{full}-sources.jar"
                ),
                "mirror": (
                    "https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/"
                    f"{full}/forge-{full}-sources.jar"
                ),
                "docs_url": "https://docs.minecraftforge.net/",
            }
        if loader == "neoforge" and version:
            # NeoForge 同样只发布 sources.jar，没有 javadoc.jar。
            return {
                "loader": "neoforge",
                "version": version,
                "kind": "sources",
                "name": f"neoforge-{version}-sources.jar",
                "url": (
                    "https://maven.neoforged.net/releases/net/neoforged/neoforge/"
                    f"{version}/neoforge-{version}-sources.jar"
                ),
                "mirror": (
                    "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge/"
                    f"{version}/neoforge-{version}-sources.jar"
                ),
                "docs_url": "https://docs.neoforged.net/",
            }
        if loader == "fabric" and version:
            return {
                "loader": "fabric",
                "version": version,
                "kind": "javadoc",
                "name": f"fabric-loader-{version}-javadoc.jar",
                "url": (
                    "https://maven.fabricmc.net/net/fabricmc/fabric-loader/"
                    f"{version}/fabric-loader-{version}-javadoc.jar"
                ),
                "docs_url": "https://docs.fabricmc.net/",
            }
        if loader == "quilt" and version:
            return {
                "loader": "quilt",
                "version": version,
                "kind": "javadoc",
                "name": f"quilt-loader-{version}-javadoc.jar",
                "url": (
                    "https://maven.quiltmc.org/repository/release/org/quiltmc/"
                    f"quilt-loader/{version}/quilt-loader-{version}-javadoc.jar"
                ),
                "docs_url": "https://wiki.quiltmc.org/",
            }
        return {}

    def fabric_api_version(self, mc: str) -> str:
        versions = [
            version
            for version in self.section("fabric").get("fabric_api") or []
            if str(version).endswith("+" + str(mc))
        ]
        return _latest(versions)

    def recommendations(self, mc: str, loader: str = "") -> dict[str, Any]:
        loader = str(loader or "").lower()
        if not loader:
            return {
                "mc": mc,
                "loaders": {
                    name: {
                        "loader_version": self.loader_for_mc(name, mc),
                        "mdk": self.mdk(name, mc),
                        "javadoc": self.javadoc(name, mc),
                    }
                    for name in LOADERS
                },
            }
        version = self.loader_for_mc(loader, mc)
        return {
            "mc": mc,
            "loader": loader,
            "loader_version": version,
            "fabric_api": self.fabric_api_version(mc) if loader == "fabric" else "",
            "yarn": self.fabric_yarn(mc) if loader == "fabric" else "",
            "quilt_mappings": self.quilt_mappings(mc) if loader == "quilt" else "",
            "mdk": self.mdk(loader, mc, version),
            "javadoc": self.javadoc(loader, mc, version),
        }

    def changelog(self, loader: str) -> list[dict[str, Any]]:
        section = self.section("changelogs").get(str(loader or "").lower())
        if not isinstance(section, dict):
            return []
        return list(section.get("releases") or [])

    def summary(self) -> dict[str, Any]:
        mojang = self.section("mojang")
        fabric = self.section("fabric")
        quilt = self.section("quilt")
        forge = self.section("forge")
        neoforge = self.section("neoforge")
        toolchain = self.section("toolchain")
        return {
            "updated": self._data.get("updated") or "",
            "minecraft_versions": len(mojang.get("versions") or []),
            "minecraft_latest": (mojang.get("latest") or {}).get("release", ""),
            "fabric_game": len(fabric.get("game") or []),
            "fabric_loader": len(fabric.get("loader") or []),
            "quilt_game": len(quilt.get("game") or []),
            "quilt_loader": len(quilt.get("loader") or []),
            "forge_versions": len(forge.get("versions") or []),
            "neoforge_versions": len(neoforge.get("versions") or []),
            "gradle": (toolchain.get("gradle") or {}).get("current", ""),
            "jdk_lts": (toolchain.get("adoptium") or {}).get("most_recent_lts", 0),
            "changelog_loaders": sorted(
                key for key, value in (self.section("changelogs") or {}).items()
                if isinstance(value, dict)
            ),
        }

    def download(
        self,
        item: dict[str, Any],
        *,
        dest_dir: Path | None = None,
        progress: Callable[[int, str], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> Path:
        """下载目录里的构件（MDK / Javadoc / 模板）。"""
        url = str(item.get("url") or "").strip()
        name = Path(str(item.get("name") or "")).name
        if not url:
            raise CatalogError("这个条目没有下载地址。")
        if not name:
            name = Path(urllib.parse.urlsplit(url).path).name or "artifact.zip"
        target_dir = Path(dest_dir) if dest_dir is not None else artifacts_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / name
        if target.is_file() and target.stat().st_size > 0:
            if progress is not None:
                progress(100, f"{name} 已存在")
            return target

        urls = [url]
        for extra in item.get("mirrors") or []:
            text = str(extra or "").strip()
            if text and text not in urls:
                urls.append(text)
        mirror = str(item.get("mirror") or "").strip()
        if mirror and mirror not in urls:
            urls.append(mirror)

        last: Exception | None = None
        for candidate in urls:
            request = _request(candidate)
            try:
                raw, status, headers = netclient.fetch_info(
                    request, timeout=120.0, cancel=cancel
                )
                if status and status >= 400:
                    raise CatalogError(f"下载失败：HTTP {status}")
                temp = target.with_name(target.name + ".part")
                temp.write_bytes(raw)
                temp.replace(target)
                if progress is not None:
                    progress(100, f"{name} · {len(raw) / 1024 / 1024:.1f} MB")
                return target
            except Exception as exc:  # noqa: BLE001 - 官方源失败时换镜像
                last = exc
        raise CatalogError(f"下载失败：{last}")


def _release_versions(versions: list[str]) -> list[str]:
    """过滤 alpha / beta / pre / rc / snapshot，挑出可用于开发的发布版。"""
    pattern = re.compile(r"(?:^|[-.])(alpha|beta|pre|rc|snapshot|dev)", re.IGNORECASE)
    return [str(item) for item in versions if item and not pattern.search(str(item))]


def _latest_release(versions: list[str]) -> str:
    values = [str(item) for item in versions if item]
    return _latest(_release_versions(values)) or _latest(values)


def _neoforge_prefix(mc: str) -> str:
    """1.21.1 -> 21.1；1.20.4 -> 20.4；26.3 -> 26.3。"""
    text = str(mc or "").strip()
    if text.startswith("1."):
        rest = text[2:]
        parts = rest.split(".")
        if len(parts) >= 2:
            return f"{parts[0]}.{parts[1]}"
        return rest
    return text


def _quilt_branch(mc: str) -> str:
    """Quilt 模板仓库的分支名。

    仓库按 1.18.2 / 1.19.2 / 1.20.6 / 1.21 建分支，1.21.1 这类补丁版本
    需要退回 1.21，找不到时再退到默认分支 1.20.6。
    """
    text = str(mc or "").strip()
    if not text:
        return "1.20.6"
    known = {
        "1.18.2",
        "1.19.2",
        "1.19.4",
        "1.20.1",
        "1.20.2",
        "1.20.4",
        "1.20.6",
        "1.21",
    }
    if text in known:
        return text
    if text.startswith("1.21"):
        # 1.21.1 / 1.21.2 都没有单独分支，统一用 1.21。
        return "1.21"
    return "1.20.6"


class CatalogSyncService:
    """后台定时同步，界面关闭时 stop() 即可。"""

    def __init__(
        self,
        catalog: Catalog | None = None,
        *,
        interval: float = 6 * 3600,
        progress: Callable[[str], None] | None = None,
        finished: Callable[[SyncReport], None] | None = None,
    ) -> None:
        self.catalog = catalog or Catalog()
        self.interval = max(600.0, float(interval))
        self.progress = progress
        self.finished = finished
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last: SyncReport | None = None

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="catalog-sync", daemon=True
        )
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            report = self.catalog.refresh(self.progress)
            self._last = report
            if self.finished is not None:
                try:
                    self.finished(report)
                except Exception:  # noqa: BLE001
                    pass
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()