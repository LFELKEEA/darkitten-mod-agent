"""在线模型与皮肤资源：搜索、预览、下载。

覆盖几类常见来源：

- Modrinth：资源包 / 模型包，使用官方 v2 API，能直接下载文件；
- LittleSkin：公开皮肤库与披风库，使用站内 ``/skinlib`` JSON 接口和原始纹理；
- YSM：Yes Steve Model 社区模型仓库，通过 GitHub 文件树检索 ``.ysm``；
- Blockbench 画廊、MCModels、Planet Minecraft：没有稳定公开 API，
  只提供官方浏览/搜索链接，不抓取页面。

下载只用于本地参考或导入当前工程，不会自动重新分发第三方资源；
界面和 Agent 都会提示遵守原作者许可。
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import catalog, modlibrary, netclient, paths

USER_AGENT = "DarkittenModAgent/3.6 (+https://lfelkeea.github.io/darkitten-launcher/)"
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
YSM_REPO = "Elaina69/Yes-Steve-Model-Repo"
YSM_BRANCH = "main"
YSM_TREE_TTL = 6 * 3600
LITTLESKIN_ROOT = "https://littleskin.cn"

Progress = Callable[[str], None]


class ArtResourceError(RuntimeError):
    pass


PROVIDERS: list[dict[str, Any]] = [
    {
        "id": "modrinth",
        "name": "Modrinth 资源包",
        "kind": "modrinth",
        "homepage": "https://modrinth.com/resourcepacks",
        "description": "社区资源包与模型包，可预览图标/画廊并下载 zip。",
        "license_note": "每个项目有自己的许可证，使用或再分发前请查看项目页。",
    },
    {
        "id": "littleskin",
        "name": "LittleSkin 皮肤",
        "kind": "littleskin",
        "filter": "skin",
        "homepage": "https://littleskin.cn/skinlib?filter=skin",
        "description": "公开皮肤库，能预览 3D 效果并下载原始 64x64 / 128x128 PNG。",
        "license_note": "皮肤版权属于上传者，仅用于个人或已获授权的作品。",
    },
    {
        "id": "littleskin_cape",
        "name": "LittleSkin 披风",
        "kind": "littleskin",
        "filter": "cape",
        "homepage": "https://littleskin.cn/skinlib?filter=cape",
        "description": "公开披风库，下载原始披风 PNG。",
        "license_note": "披风版权属于上传者，仅用于个人或已获授权的作品。",
    },
    {
        "id": "ysm",
        "name": "YSM 社区模型",
        "kind": "ysm",
        "homepage": f"https://github.com/{YSM_REPO}",
        "description": "Yes Steve Model 社区仓库，检索并下载 .ysm / .zip 模型文件。",
        "license_note": "仓库声明免费使用，但单个模型可能另有作者要求；发布前请确认。",
    },
    {
        "id": "blockbench",
        "name": "Blockbench 画廊",
        "kind": "link",
        "homepage": "https://www.blockbench.net/gallery",
        "description": "官方精选画廊，偏展示，没有公开搜索/下载 API，用浏览器浏览。",
        "license_note": "画廊作品通常不提供下载，参考造型时请避免直接复制。",
    },
    {
        "id": "mcmodels",
        "name": "MCModels",
        "kind": "link",
        "homepage": "https://mcmodels.net/",
        "search": "https://mcmodels.net/?s={query}",
        "description": "模型 marketplace，部分资源付费，需在浏览器中查看和购买。",
        "license_note": "付费资源按购买时的许可使用，不能重新分发。",
    },
    {
        "id": "planetminecraft",
        "name": "Planet Minecraft",
        "kind": "link",
        "homepage": "https://www.planetminecraft.com/resources/",
        "search": "https://www.planetminecraft.com/resources/?keywords={query}",
        "description": "大型社区资源站，模型/皮肤/资源包都有，用浏览器浏览和下载。",
        "license_note": "每个作品页面会标注许可，下载前请确认是否允许用于模组。",
    },
]

_PROVIDER_MAP = {item["id"]: item for item in PROVIDERS}


def _cache_dir() -> Path:
    path = paths.data_dir() / "artresources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def providers() -> list[dict[str, Any]]:
    return [dict(item) for item in PROVIDERS]


def provider(provider_id: str) -> dict[str, Any]:
    item = _PROVIDER_MAP.get(str(provider_id or "").strip().lower())
    if item is None:
        raise ArtResourceError(f"未知资源来源：{provider_id}")
    return dict(item)


def browse_url(provider_id: str, query: str = "") -> str:
    item = provider(provider_id)
    text = str(query or "").strip()
    template = str(item.get("search") or "")
    if text and template:
        return template.format(query=urllib.parse.quote(text))
    return str(item.get("homepage") or "")


def _get_json(url: str, *, timeout: float = 25.0, headers: dict[str, str] | None = None) -> Any:
    try:
        return catalog.fetch_json(
            url,
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
        )
    except Exception as exc:  # noqa: BLE001 - 统一换成可读错误
        raise ArtResourceError(f"请求失败：{exc}") from exc


def _get_bytes(url: str, *, timeout: float = 60.0, cancel=None) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"}
    )
    try:
        raw, status, _headers = netclient.fetch_info(
            request, timeout=timeout, cancel=cancel
        )
    except Exception as exc:  # noqa: BLE001
        raise ArtResourceError(f"下载失败：{exc}") from exc
    if status and status >= 400:
        raise ArtResourceError(f"下载失败：HTTP {status}")
    if len(raw) > MAX_DOWNLOAD_BYTES:
        raise ArtResourceError("文件超过 256 MB，已停止下载。")
    return raw


def _safe_name(text: str, fallback: str = "asset", suffix: str = "") -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(text or "")).strip(" ._")
    value = value[:90] or fallback
    if suffix and not value.lower().endswith(suffix.lower()):
        value += suffix
    return value


# --------------------------------------------------------------------------- #
# Modrinth
# --------------------------------------------------------------------------- #
def _modrinth_search(query: str, limit: int) -> list[dict[str, Any]]:
    params = {
        "query": str(query or "").strip(),
        "limit": str(max(1, min(50, int(limit)))),
        "index": "downloads" if not str(query or "").strip() else "relevance",
        "facets": json.dumps([["project_type:resourcepack"]], separators=(",", ":")),
    }
    data = _get_json(
        "https://api.modrinth.com/v2/search?" + urllib.parse.urlencode(params)
    )
    hits = data.get("hits") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for item in hits or []:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id") or item.get("id") or "")
        slug = str(item.get("slug") or project_id)
        categories = [str(value) for value in item.get("categories") or []]
        result.append(
            {
                "provider": "modrinth",
                "id": project_id,
                "title": str(item.get("title") or slug),
                "author": str(item.get("author") or ""),
                "description": str(item.get("description") or ""),
                "preview_url": str(item.get("icon_url") or ""),
                "gallery": [str(url) for url in item.get("gallery") or [] if url],
                "page_url": f"https://modrinth.com/resourcepack/{slug}",
                "downloadable": bool(project_id),
                "kind": "resourcepack",
                "meta": " · ".join(
                    bit
                    for bit in (
                        f"{int(item.get('downloads') or 0):,} 次下载",
                        "、".join(categories[:4]),
                    )
                    if bit
                ),
                "license": str(item.get("license") or ""),
                "note": "下载的是资源包 zip，可解压参考模型/贴图，或作为整体资源包使用。",
            }
        )
    return result


def _modrinth_download(item_id: str, dest_dir: Path, *, cancel=None) -> Path:
    version = modlibrary.pick_version(item_id)
    if version is None:
        raise ArtResourceError("这个 Modrinth 项目没有可下载的版本。")
    return modlibrary.download(version, dest_dir=dest_dir, cancel=cancel)


# --------------------------------------------------------------------------- #
# LittleSkin
# --------------------------------------------------------------------------- #
def _littleskin_search(provider_id: str, query: str, limit: int) -> list[dict[str, Any]]:
    info = provider(provider_id)
    params = {
        "filter": str(info.get("filter") or "skin"),
        "keyword": str(query or "").strip(),
        "page": "1",
    }
    data = _get_json(
        f"{LITTLESKIN_ROOT}/skinlib/list?" + urllib.parse.urlencode(params)
    )
    rows = data.get("data") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        tid = str(item.get("tid") or "").strip()
        if not tid.isdigit():
            continue
        title = str(item.get("name") or f"#{tid}")
        result.append(
            {
                "provider": provider_id,
                "id": tid,
                "title": title,
                "author": str(item.get("nickname") or ""),
                "description": "",
                "preview_url": f"{LITTLESKIN_ROOT}/preview/{tid}",
                "gallery": [],
                "page_url": f"{LITTLESKIN_ROOT}/skinlib/show/{tid}",
                "downloadable": True,
                "kind": "skin" if provider_id == "littleskin" else "cape",
                "meta": " · ".join(
                    bit
                    for bit in (
                        str(item.get("type") or "").upper(),
                        "HD" if item.get("hd") else "",
                        f"{int(item.get('likes') or 0)} 赞",
                    )
                    if bit
                ),
                "license": "",
                "note": "下载的是原始纹理 PNG，可直接作为模型贴图参考。",
            }
        )
        if len(result) >= limit:
            break
    return result


def _littleskin_download(item_id: str, dest_dir: Path, *, cancel=None) -> Path:
    tid = str(item_id or "").strip()
    if not tid.isdigit():
        raise ArtResourceError("LittleSkin 材质编号不合法。")
    info = _get_json(f"{LITTLESKIN_ROOT}/skinlib/info/{tid}")
    if not isinstance(info, dict) or not info.get("hash"):
        raise ArtResourceError("没有取到这个材质的文件信息。")
    raw = _get_bytes(f"{LITTLESKIN_ROOT}/textures/{info['hash']}", cancel=cancel)
    name = _safe_name(str(info.get("name") or f"skin_{tid}"), f"skin_{tid}", ".png")
    path = dest_dir / f"{tid}_{name}"
    path.write_bytes(raw)
    return path


# --------------------------------------------------------------------------- #
# YSM
# --------------------------------------------------------------------------- #
def _ysm_tree(*, force: bool = False) -> list[str]:
    cache = _cache_dir() / "ysm-tree.json"
    if not force and cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if time.time() - float(data.get("fetched") or 0) < YSM_TREE_TTL:
                rows = data.get("paths")
                if isinstance(rows, list):
                    return [str(item) for item in rows]
        except (OSError, ValueError):
            pass
    url = (
        f"https://api.github.com/repos/{YSM_REPO}/git/trees/{YSM_BRANCH}"
        "?recursive=1"
    )
    try:
        data = _get_json(
            url,
            timeout=40.0,
            headers={"Accept": "application/vnd.github+json"},
        )
        tree = data.get("tree") if isinstance(data, dict) else None
        rows = [
            str(item.get("path"))
            for item in tree or []
            if isinstance(item, dict)
            and str(item.get("path") or "").lower().endswith((".ysm", ".zip"))
        ]
        cache.write_text(
            json.dumps({"fetched": time.time(), "paths": rows}, ensure_ascii=False),
            encoding="utf-8",
        )
        return rows
    except ArtResourceError:
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                rows = data.get("paths")
                if isinstance(rows, list):
                    return [str(item) for item in rows]
            except (OSError, ValueError):
                pass
        raise


def _ysm_search(query: str, limit: int) -> list[dict[str, Any]]:
    needle = str(query or "").strip().lower()
    rows = _ysm_tree()
    if needle:
        rows = [row for row in rows if needle in row.lower()]
    else:
        rows = sorted(rows)
    result: list[dict[str, Any]] = []
    for path in rows[:limit]:
        name = Path(path).name
        stem = Path(path).stem
        result.append(
            {
                "provider": "ysm",
                "id": path,
                "title": stem,
                "author": path.split("/")[0] if "/" in path else "",
                "description": path,
                "preview_url": "",
                "gallery": [],
                "page_url": f"https://github.com/{YSM_REPO}/blob/{YSM_BRANCH}/{urllib.parse.quote(path)}",
                "downloadable": True,
                "kind": "ysm",
                "meta": name.rsplit(".", 1)[-1].upper(),
                "license": "",
                "note": "放入 config/ysm/models/ 使用；模型通常不能直接转成 Java 实体模型。",
            }
        )
    return result


def _ysm_download(item_id: str, dest_dir: Path, *, cancel=None) -> Path:
    path = str(item_id or "").strip().replace("\\", "/")
    allowed = set(_ysm_tree())
    if path not in allowed or ".." in Path(path).parts:
        raise ArtResourceError("这个 YSM 模型不在已知仓库列表中。")
    url = (
        f"https://raw.githubusercontent.com/{YSM_REPO}/{YSM_BRANCH}/"
        + urllib.parse.quote(path)
    )
    raw = _get_bytes(url, timeout=120.0, cancel=cancel)
    name = _safe_name(Path(path).name, "model")
    target = dest_dir / name
    target.write_bytes(raw)
    return target


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def search(provider_id: str, query: str = "", *, limit: int = 24) -> list[dict[str, Any]]:
    item = provider(provider_id)
    kind = str(item.get("kind") or "link")
    limit = max(1, min(60, int(limit)))
    if kind == "modrinth":
        return _modrinth_search(query, limit)
    if kind == "littleskin":
        return _littleskin_search(str(item["id"]), query, limit)
    if kind == "ysm":
        return _ysm_search(query, limit)
    return []


def search_all(query: str = "", *, limit: int = 8) -> list[dict[str, Any]]:
    """同时搜索可编程来源；单个来源失败不影响其他来源。"""
    result: list[dict[str, Any]] = []
    for provider_id in ("modrinth", "littleskin", "ysm"):
        try:
            result.extend(search(provider_id, query, limit=limit))
        except ArtResourceError:
            continue
    return result


def download_item(
    provider_id: str,
    item_id: str,
    dest_dir: str | Path,
    *,
    cancel=None,
) -> Path:
    item = provider(provider_id)
    target_dir = Path(dest_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    kind = str(item.get("kind") or "")
    if kind == "modrinth":
        return _modrinth_download(str(item_id), target_dir, cancel=cancel)
    if kind == "littleskin":
        return _littleskin_download(str(item_id), target_dir, cancel=cancel)
    if kind == "ysm":
        return _ysm_download(str(item_id), target_dir, cancel=cancel)
    raise ArtResourceError("这个来源只支持在浏览器中查看，不能直接下载。")


_ALLOWED_DOWNLOAD_HOSTS = {
    "cdn.modrinth.com",
    "raw.githubusercontent.com",
    "littleskin.cn",
}


def download_url(
    url: str,
    dest_dir: str | Path,
    *,
    filename: str = "",
    cancel=None,
) -> Path:
    text = str(url or "").strip()
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_DOWNLOAD_HOSTS:
        raise ArtResourceError(
            "只允许从 Modrinth CDN、GitHub raw 或 LittleSkin 下载；"
            "其他站点请在浏览器中打开。"
        )
    target_dir = Path(dest_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    name = _safe_name(filename or Path(parsed.path).name, "asset")
    raw = _get_bytes(text, timeout=120.0, cancel=cancel)
    target = target_dir / name
    target.write_bytes(raw)
    return target


def format_results(items: list[dict[str, Any]], limit: int = 20) -> str:
    if not items:
        return "没有找到匹配资源。"
    lines = [f"找到 {len(items)} 个资源："]
    for index, item in enumerate(items[:limit], 1):
        bits = [f"{index}. [{item.get('title')}]({item.get('page_url')})"]
        meta = " · ".join(
            value
            for value in (
                str(item.get("provider") or ""),
                str(item.get("author") or ""),
                str(item.get("meta") or ""),
            )
            if value
        )
        if meta:
            bits.append(f"   {meta}")
        description = str(item.get("description") or "").strip()
        if description:
            bits.append(f"   {description[:160]}")
        if item.get("preview_url"):
            bits.append(f"   预览：{item['preview_url']}")
        if item.get("downloadable"):
            bits.append(
                "   下载：online_assets action=download "
                f"provider={item.get('provider')} id={item.get('id')}"
            )
        lines.append("\n".join(bits))
    return "\n".join(lines)
