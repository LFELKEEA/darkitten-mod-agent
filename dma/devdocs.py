"""在线开发资料：官方文档入口、源码构件按需下载与检索。

这个模块不依赖任何内置文档包：

- 需要查 API 时，先尝试本机已经下载过的加载器 sources.jar；
- 没有就按当前加载器 / Minecraft 版本从 Maven 或镜像在线下载；
- 源码构件里搜不到，再退到 GitHub 代码搜索和仓库搜索；
- 官方文档地址始终随结果返回，方便继续查完整说明。

所有下载都落在 data/online/docs 下，可以随时删除，下次会重新拉取。
"""

from __future__ import annotations

import html
import json
import re
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

from . import catalog as catalog_mod
from . import paths, research

Progress = Callable[[int, str], None]
MAX_ENTRY_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 6_000
MAX_RESULT_CHARS = 60_000

DOCS: dict[str, dict[str, Any]] = {
    "fabric": {
        "label": "Fabric",
        "pages": [
            ("Fabric 开发文档", "https://docs.fabricmc.net/develop/"),
            ("Fabric Wiki", "https://wiki.fabricmc.net/"),
            ("Fabric API Javadoc", "https://maven.fabricmc.net/docs/"),
        ],
        "repo": "FabricMC/fabric",
    },
    "forge": {
        "label": "Forge",
        "pages": [
            ("Forge 开发文档", "https://docs.minecraftforge.net/en/latest/"),
            ("Forge Javadoc", "https://lexxie.dev/forge/"),
        ],
        "repo": "MinecraftForge/MinecraftForge",
    },
    "neoforge": {
        "label": "NeoForge",
        "pages": [
            ("NeoForge 开发文档", "https://docs.neoforged.net/docs/"),
            ("NeoForge Javadoc", "https://docs.neoforged.net/javadoc/"),
        ],
        "repo": "neoforged/NeoForge",
    },
    "quilt": {
        "label": "Quilt",
        "pages": [
            ("Quilt 开发文档", "https://wiki.quiltmc.org/en/"),
            ("Quilt Loader Javadoc", "https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-loader/"),
        ],
        "repo": "QuiltMC/quilt-loader",
    },
}


class DevDocsError(RuntimeError):
    pass


def _loader(loader: str) -> str:
    value = str(loader or "").strip().lower()
    return value if value in DOCS else "neoforge"


def docs_root() -> Path:
    path = paths.data_dir() / "online" / "docs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def official_pages(loader: str) -> list[tuple[str, str]]:
    return list(DOCS[_loader(loader)]["pages"])


def _catalog() -> catalog_mod.Catalog:
    return catalog_mod.Catalog()


def _loader_version(loader: str, mc: str) -> str:
    try:
        return str(_catalog().loader_for_mc(loader, mc) or "")
    except Exception:  # noqa: BLE001 - 目录未同步时按空处理
        return ""


def sources_item(loader: str, mc: str = "", version: str = "") -> dict[str, Any] | None:
    """返回当前加载器的 sources.jar 条目；forge/neoforge 复用目录里的 sources。"""
    key = _loader(loader)
    if key in {"forge", "neoforge"}:
        try:
            item = _catalog().javadoc(key, mc, version)
        except Exception:  # noqa: BLE001
            item = None
        if item and str(item.get("kind") or "") == "sources":
            return item
        return item
    resolved = str(version or "").strip() or _loader_version(key, mc)
    if not resolved:
        return None
    if key == "fabric":
        name = f"fabric-loader-{resolved}-sources.jar"
        return {
            "loader": "fabric",
            "mc": mc,
            "version": resolved,
            "kind": "sources",
            "name": name,
            "url": f"https://maven.fabricmc.net/net/fabricmc/fabric-loader/{resolved}/{name}",
            "mirror": f"https://bmclapi2.bangbang93.com/maven/net/fabricmc/fabric-loader/{resolved}/{name}",
            "docs_url": "https://docs.fabricmc.net/develop/",
        }
    if key == "quilt":
        name = f"quilt-loader-{resolved}-sources.jar"
        return {
            "loader": "quilt",
            "mc": mc,
            "version": resolved,
            "kind": "sources",
            "name": name,
            "url": f"https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-loader/{resolved}/{name}",
            "mirror": f"https://bmclapi2.bangbang93.com/maven/org/quiltmc/quilt-loader/{resolved}/{name}",
            "docs_url": "https://wiki.quiltmc.org/en/",
        }
    return None


def ensure_sources(
    loader: str,
    mc: str = "",
    version: str = "",
    *,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    force: bool = False,
) -> Path | None:
    """按需下载 sources.jar；已经存在时直接复用。"""
    item = sources_item(loader, mc, version)
    if not item:
        return None
    key = _loader(loader)
    target_dir = docs_root() / key
    target_dir.mkdir(parents=True, exist_ok=True)
    name = Path(str(item.get("name") or "sources.jar")).name
    target = target_dir / name
    if target.is_file() and target.stat().st_size > 0 and not force:
        if progress is not None:
            progress(100, f"源码已缓存：{name}")
        return target
    if progress is not None:
        progress(5, f"在线下载 {name}")
    try:
        return _catalog().download(
            item, dest_dir=target_dir, progress=progress, cancel=cancel
        )
    except Exception as exc:  # noqa: BLE001 - 官方源失败时 catalog 已尝试镜像
        raise DevDocsError(f"下载 {name} 失败：{exc}") from exc


def cached_sources(loader: str, mc: str = "", version: str = "") -> Path | None:
    """只查本地已缓存的 sources.jar，不触发下载。"""
    item = sources_item(loader, mc, version)
    if not item:
        return None
    name = Path(str(item.get("name") or "")).name
    if not name:
        return None
    target = docs_root() / _loader(loader) / name
    if target.is_file() and target.stat().st_size > 0:
        return target
    return None


def _strip_html(text: str) -> str:
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(re.sub(r"\s+", " ", text))


def _archive_entries(path: Path) -> Iterable[tuple[str, str]]:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.file_size > MAX_ENTRY_BYTES:
                continue
            name = info.filename
            lower = name.lower()
            if not lower.endswith((".java", ".html", ".txt", ".md", ".json", ".toml", ".properties")):
                continue
            try:
                raw = archive.read(info)
            except (OSError, KeyError):
                continue
            text = raw.decode("utf-8", errors="replace")
            if lower.endswith((".html", ".htm")):
                text = _strip_html(text)
            yield name, text


def inspect_archive(
    path: Path,
    query: str,
    *,
    limit: int = 8,
    tokens: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """在 sources.jar / javadoc.jar 里按关键词检索并返回片段。"""
    token_list = [str(item).lower() for item in (tokens or []) if str(item)]
    if query:
        token_list.extend(
            item.lower()
            for item in re.findall(r"[A-Za-z_$][A-Za-z0-9_$]{1,40}", query)
        )
    token_list = list(dict.fromkeys(token_list))[:24]
    if not token_list:
        return []
    hits: list[dict[str, Any]] = []
    visited = 0
    for name, text in _archive_entries(path):
        visited += 1
        if visited > MAX_ENTRIES:
            break
        lowered = text.lower()
        score = sum(lowered.count(token) for token in token_list if token in lowered)
        if score <= 0:
            continue
        position = 0
        best = -1
        for token in token_list:
            found = lowered.find(token)
            if found >= 0 and (best < 0 or found < best):
                best = found
        if best < 0:
            best = 0
        start = max(0, best - 1200)
        snippet = text[start:start + 2400]
        hits.append({"entry": name, "score": score, "snippet": snippet.strip()})
        if len(hits) >= max(1, min(30, limit)):
            break
    hits.sort(key=lambda item: item["score"], reverse=True)
    return hits


def lookup(
    query: str,
    *,
    loader: str = "",
    mc: str = "",
    version: str = "",
    limit: int = 8,
    allow_download: bool = True,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """在线查 API：源码构件优先，GitHub 代码搜索兜底。"""
    text = str(query or "").strip()
    key = _loader(loader)
    pages = official_pages(key)
    result: dict[str, Any] = {
        "loader": key,
        "mc": mc,
        "version": version,
        "query": text,
        "docs": pages,
        "source": "",
        "hits": [],
        "notes": [],
    }
    if not text:
        result["notes"].append("查询关键词为空，只返回官方文档入口。")
        return result
    archive: Path | None = None
    if allow_download:
        try:
            archive = ensure_sources(
                key,
                mc,
                version,
                cancel=cancel,
                force=False,
            )
        except Exception as exc:  # noqa: BLE001 - 源码下载失败不影响文档兜底
            result["notes"].append(str(exc))
    else:
        archive = cached_sources(key, mc, version)
        if archive is None:
            result["notes"].append("未允许下载，且本地没有对应的 sources.jar。")
    if archive is not None and archive.is_file():
        result["source"] = str(archive)
        try:
            result["hits"] = inspect_archive(archive, text, limit=limit)
        except Exception as exc:  # noqa: BLE001
            result["notes"].append(f"读取源码构件失败：{exc}")
    if not result["hits"]:
        try:
            code = research.search_code(
                f"{text} {DOCS[key]['label']} {mc}".strip(), limit=limit
            )
            result["code_search"] = code
        except Exception as exc:  # noqa: BLE001
            result["notes"].append(f"代码搜索失败：{exc}")
    if not result["hits"] and not result.get("code_search"):
        try:
            result["repos"] = research.repo_search(
                f"{text} minecraft {DOCS[key]['label']}", limit=5
            )
        except Exception as exc:  # noqa: BLE001
            result["notes"].append(f"仓库搜索失败：{exc}")
    return result


def render(result: dict[str, Any], *, hit_limit: int = 6) -> str:
    """把 lookup 结果渲染成 Markdown，供模型直接阅读。"""
    lines = [
        f"在线开发资料查询：`{result.get('query') or '（空）'}` · "
        f"{DOCS.get(str(result.get('loader')), {}).get('label', result.get('loader'))} "
        f"{result.get('mc') or ''}"
    ]
    pages = result.get("docs") or []
    if pages:
        lines.append("\n官方文档：")
        lines.extend(f"- [{title}]({url})" for title, url in pages)
    if result.get("source"):
        lines.append(f"\n源码构件：`{result['source']}`")
    hits = result.get("hits") or []
    if hits:
        lines.append("\n源码命中（来自在线下载的 sources.jar，仅作参考）：")
        for item in hits[:hit_limit]:
            lines.append(
                f"\n### {item.get('entry')}\n```\n{str(item.get('snippet') or '')[:2400]}\n```"
            )
    if result.get("code_search"):
        lines.append("\nGitHub / 代码搜索结果：\n" + str(result["code_search"])[:20_000])
    repos = result.get("repos") or []
    if repos:
        lines.append("\n相关仓库：")
        for item in repos:
            lines.append(
                f"- [{item.get('repo')}]({item.get('url')}) · ★{item.get('stars', 0)} · "
                f"{item.get('description') or ''}"
            )
    notes = result.get("notes") or []
    if notes:
        lines.append("\n备注：")
        lines.extend(f"- {note}" for note in notes[:10])
    return "\n".join(lines)[:MAX_RESULT_CHARS]


def status() -> dict[str, Any]:
    root = docs_root()
    items = []
    total = 0
    for path in sorted(root.glob("*/*.jar")):
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        total += size
        items.append({"name": path.name, "loader": path.parent.name, "size": size})
    return {
        "root": str(root),
        "count": len(items),
        "bytes": total,
        "items": items,
        "official": {
            key: [{"title": title, "url": url} for title, url in value["pages"]]
            for key, value in DOCS.items()
        },
    }
