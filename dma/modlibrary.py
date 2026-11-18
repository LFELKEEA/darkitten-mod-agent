"""模组资料库：搜索、下载、反编译、索引。

用于“向成熟模组学习”：从 Modrinth 搜索项目，读取版本与依赖，下载 JAR，
反编译成可读 Java 源码，并生成一份索引方便 Agent 检索。

反编译结果只保存在本机 data/library 下，供学习与排错使用；不重新分发，
也不把第三方源码写进用户工程，除非用户明确要求导入。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import catalog, knowledge, paths

MODRINTH_API = "https://api.modrinth.com/v2"
USER_AGENT = "DarkittenModAgent/3.2 (+https://lfelkeea.github.io/darkitten-launcher/)"
MAX_JAR_BYTES = 512 * 1024 * 1024

Progress = Callable[[int, str], None]


class ModLibraryError(RuntimeError):
    pass


def library_dir() -> Path:
    path = paths.data_dir() / "library"
    path.mkdir(parents=True, exist_ok=True)
    return path


def mods_dir() -> Path:
    path = library_dir() / "mods"
    path.mkdir(parents=True, exist_ok=True)
    return path


def sources_dir() -> Path:
    path = library_dir() / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_json(url: str, timeout: float = 30.0) -> Any:
    return catalog.fetch_json(url, timeout=timeout, headers={"User-Agent": USER_AGENT})


def _loader_key(loader: str) -> str:
    value = str(loader or "").strip().lower()
    mapping = {
        "neoforge": "neoforge",
        "forge": "forge",
        "fabric": "fabric",
        "quilt": "quilt",
    }
    return mapping.get(value, "")


def _normalize_project(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("project_id") or item.get("id") or ""),
        "slug": str(item.get("slug") or ""),
        "title": str(item.get("title") or ""),
        "description": str(item.get("description") or ""),
        "author": str(item.get("author") or ""),
        "downloads": int(item.get("downloads") or 0),
        "follows": int(item.get("followers") or 0),
        "icon_url": str(item.get("icon_url") or ""),
        "categories": list(item.get("categories") or []),
        "versions": list(item.get("versions") or []),
        "loaders": list(item.get("loaders") or []),
        "project_type": str(item.get("project_type") or "mod"),
        "license": str(item.get("license") or ""),
        "source_url": str(item.get("source_url") or ""),
        "wiki_url": str(item.get("wiki_url") or ""),
        "issues_url": str(item.get("issues_url") or ""),
        "url": f"https://modrinth.com/{item.get('project_type') or 'mod'}/"
        f"{item.get('slug') or item.get('project_id') or item.get('id') or ''}",
    }


def search(
    query: str,
    *,
    loader: str = "",
    mc: str = "",
    limit: int = 20,
    offset: int = 0,
    project_type: str = "mod",
    sort: str = "relevance",
) -> list[dict[str, Any]]:
    kind = str(project_type or "mod").strip().lower()
    if kind not in ("mod", "modpack", "resourcepack", "shader", "plugin"):
        kind = "mod"
    facets: list[list[str]] = [[f"project_type:{kind}"]]
    key = _loader_key(loader) if kind == "mod" else ""
    if key:
        facets.append([f"categories:{key}"])
    if mc:
        facets.append([f"versions:{mc}"])
    params = {
        "query": str(query or "").strip(),
        "limit": max(1, min(50, int(limit))),
        "offset": max(0, int(offset)),
        "index": sort if sort in ("relevance", "downloads", "follows", "newest", "updated") else "relevance",
        "facets": json.dumps(facets, separators=(",", ":")),
    }
    data = _get_json(f"{MODRINTH_API}/search?" + urllib.parse.urlencode(params))
    hits = data.get("hits") if isinstance(data, dict) else None
    return [_normalize_project(item) for item in hits or [] if isinstance(item, dict)]


def project(project_id: str) -> dict[str, Any]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ModLibraryError("缺少 Modrinth 项目 ID。")
    data = _get_json(f"{MODRINTH_API}/project/{urllib.parse.quote(project_id, safe='')}")
    if not isinstance(data, dict):
        raise ModLibraryError("Modrinth 返回的项目详情无法识别。")
    item = _normalize_project(data)
    item["body"] = str(data.get("body") or "")
    item["gallery"] = data.get("gallery") or []
    item["license"] = str((data.get("license") or {}).get("id") or data.get("license") or "")
    item["source_url"] = str(data.get("source_url") or "")
    return item


def versions(
    project_id: str,
    *,
    loader: str = "",
    mc: str = "",
    featured: bool = False,
) -> list[dict[str, Any]]:
    project_id = str(project_id or "").strip()
    if not project_id:
        raise ModLibraryError("缺少 Modrinth 项目 ID。")
    params: dict[str, str] = {}
    key = _loader_key(loader)
    if key:
        params["loaders"] = json.dumps([key])
    if mc:
        params["game_versions"] = json.dumps([mc])
    if featured:
        params["featured"] = "true"
    url = f"{MODRINTH_API}/project/{urllib.parse.quote(project_id, safe='')}/version"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = _get_json(url)
    result: list[dict[str, Any]] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        files = [item for item in entry.get("files") or [] if isinstance(item, dict)]
        primary = next((item for item in files if item.get("primary")), None)
        if primary is None and files:
            primary = files[0]
        if not primary or not primary.get("url"):
            continue
        result.append(
            {
                "id": str(entry.get("id") or ""),
                "name": str(entry.get("name") or ""),
                "version_number": str(entry.get("version_number") or ""),
                "version_type": str(entry.get("version_type") or ""),
                "date_published": str(entry.get("date_published") or ""),
                "downloads": int(entry.get("downloads") or 0),
                "loaders": list(entry.get("loaders") or []),
                "game_versions": list(entry.get("game_versions") or []),
                "dependencies": [
                    dep
                    for dep in entry.get("dependencies") or []
                    if isinstance(dep, dict)
                ],
                "file": {
                    "filename": str(primary.get("filename") or ""),
                    "url": str(primary.get("url") or ""),
                    "size": int(primary.get("size") or 0),
                    "sha1": str((primary.get("hashes") or {}).get("sha1") or ""),
                    "sha512": str((primary.get("hashes") or {}).get("sha512") or ""),
                    "primary": bool(primary.get("primary")),
                },
            }
        )
    result.sort(key=lambda item: item.get("date_published") or "", reverse=True)
    return result


def pick_version(
    project_id: str,
    *,
    loader: str = "",
    mc: str = "",
) -> dict[str, Any] | None:
    items = versions(project_id, loader=loader, mc=mc)
    if items:
        # 优先稳定版，其次最新发布。
        stable = [item for item in items if item.get("version_type") == "release"]
        return (stable or items)[0]
    return None


def _sha1(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    version: dict[str, Any],
    *,
    dest_dir: Path | None = None,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    file = version.get("file") if isinstance(version, dict) else None
    if not isinstance(file, dict) or not file.get("url"):
        raise ModLibraryError("这个版本没有可下载的主文件。")
    name = Path(str(file.get("filename") or "")).name
    if not name:
        name = f"{version.get('id') or 'mod'}.jar"
    target_dir = Path(dest_dir) if dest_dir is not None else mods_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    size = int(file.get("size") or 0)
    sha1 = str(file.get("sha1") or "")
    if target.is_file() and target.stat().st_size > 0:
        if not sha1 or _sha1(target) == sha1:
            if progress is not None:
                progress(100, f"{name} 已存在")
            return target
    if progress is not None:
        progress(1, f"下载 {name}")
    raw, status, _headers = catalog.netclient.fetch_info(
        catalog._request(str(file["url"])), timeout=180.0, cancel=cancel
    )
    if status and status >= 400:
        raise ModLibraryError(f"下载失败：HTTP {status}")
    if len(raw) > MAX_JAR_BYTES:
        raise ModLibraryError("文件超过 512 MB，已停止下载。")
    temp = target.with_name(target.name + ".part")
    temp.write_bytes(raw)
    if sha1 and _sha1(temp) != sha1.lower():
        temp.unlink(missing_ok=True)
        raise ModLibraryError("SHA-1 校验失败，文件可能不完整。")
    if size and temp.stat().st_size != size:
        temp.unlink(missing_ok=True)
        raise ModLibraryError(
            f"文件大小不一致：{temp.stat().st_size} / {size}"
        )
    temp.replace(target)
    if progress is not None:
        progress(100, f"{name} · {len(raw) / 1024 / 1024:.1f} MB")
    return target


def _safe_target(root: Path, relative: str) -> Path:
    clean = str(relative or "").replace("\\", "/").lstrip("/")
    if not clean or ".." in Path(clean).parts:
        raise ModLibraryError(f"不安全的源码路径：{relative}")
    target = (root / clean).resolve()
    root_resolved = root.resolve()
    if not target.is_relative_to(root_resolved):
        raise ModLibraryError(f"源码路径越界：{relative}")
    return target


def decompile(
    jar: Path,
    *,
    project_slug: str = "",
    version_label: str = "",
    progress: Progress | None = None,
) -> dict[str, Any]:
    jar = Path(jar).expanduser()
    if not jar.is_file():
        raise ModLibraryError(f"JAR 不存在：{jar}")
    label = project_slug or jar.stem
    version_label = version_label or "latest"
    target = sources_dir() / _safe_name(label) / _safe_name(version_label)
    target.mkdir(parents=True, exist_ok=True)
    files, notes = knowledge.decompile_jar_sources(
        jar,
        progress=(lambda text: progress(50, text)) if progress is not None else None,
    )
    written = 0
    for item in files:
        relative = str(item.get("path") or "")
        try:
            path = _safe_target(target, relative)
        except ModLibraryError as exc:
            notes.append(str(exc))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(str(item.get("text") or ""), encoding="utf-8")
            written += 1
        except OSError as exc:
            notes.append(f"写入失败 {relative}：{exc}")
    index = target / "INDEX.md"
    index.write_text(
        "\n".join(
            [
                f"# {label} {version_label}",
                "",
                f"- 源文件：`{jar}`",
                f"- 反编译文件：{written}",
                f"- 仅用于本地学习，请遵守原模组许可证，不要重新分发。",
                "",
                "## 说明",
                *[f"- {note}" for note in notes],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    if progress is not None:
        progress(100, f"反编译完成：{written} 个文件")
    return {
        "jar": str(jar),
        "sources": str(target),
        "index": str(index),
        "files": written,
        "notes": notes,
    }


def _safe_name(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", str(value or "")).strip("-")
    return text[:80] or "mod"


def learn(
    project_id: str,
    *,
    loader: str = "",
    mc: str = "",
    version_id: str = "",
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """一条龙：项目信息 -> 选版本 -> 下载 -> 反编译。"""
    info = project(project_id)
    if progress is not None:
        progress(5, f"项目：{info.get('title') or project_id}")
    chosen: dict[str, Any] | None = None
    if version_id:
        for item in versions(project_id):
            if item.get("id") == version_id:
                chosen = item
                break
    if chosen is None:
        chosen = pick_version(project_id, loader=loader, mc=mc)
    if chosen is None:
        raise ModLibraryError(
            f"没有找到匹配的版本（loader={loader or '任意'}，mc={mc or '任意'}）。"
        )
    if progress is not None:
        progress(20, f"版本：{chosen.get('version_number') or chosen.get('name')}")
    jar = download(chosen, progress=progress, cancel=cancel)
    result = decompile(
        jar,
        project_slug=str(info.get("slug") or project_id),
        version_label=str(chosen.get("version_number") or chosen.get("id") or ""),
        progress=progress,
    )
    result.update(
        {
            "project": info,
            "version": chosen,
            "license": info.get("license") or "",
            "notice": "反编译源码仅用于本地学习与排错，请遵守原模组许可证。",
        }
    )
    return result


def library_summary() -> dict[str, Any]:
    mods = list(mods_dir().glob("*.jar"))
    sources = [
        path
        for path in sources_dir().glob("*/*")
        if path.is_dir()
    ]
    total = sum(path.stat().st_size for path in mods if path.is_file())
    return {
        "mods": len(mods),
        "sources": len(sources),
        "jar_bytes": total,
        "mods_dir": str(mods_dir()),
        "sources_dir": str(sources_dir()),
    }