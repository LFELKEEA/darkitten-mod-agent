"""与 Darkitten 启动器联动。

两种模式：
- bridge：启动器正在运行，读 APP_DIR/bridge.json，通过本机 HTTP 调用版本、
  下载源、Modrinth 搜索/下载/反编译等能力；
- files：启动器没运行，直接读它的 config.json 和 versions/ 目录，只提供
  只读的版本与目录信息；网络资源回退到 Mojang / Modrinth 官方接口。

任何情况下都不会读取或返回账号令牌、API Key。
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import netclient

USER_AGENT = "DarkittenModAgent/3.2 (+https://lfelkeea.github.io/darkitten-launcher/)"
LAUNCHER_SITE = "https://lfelkeea.github.io/darkitten-launcher/"
MOJANG_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
BMCL_MANIFEST = "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json"
MAX_JSON_BYTES = 8 * 1024 * 1024
Progress = Callable[[int, str], None]


class LauncherBridgeError(RuntimeError):
    pass


@dataclass
class LaunchInstance:
    """启动器里的一个游戏实例。"""

    id: str
    type: str = "release"
    loader: str = ""
    loader_version: str = ""
    base_id: str = ""
    java_major: int = 0
    release_time: str = ""
    json_path: str = ""
    game_dir: str = ""
    mods_dir: str = ""
    is_loader_profile: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "loader": self.loader,
            "loader_version": self.loader_version,
            "base_id": self.base_id,
            "java_major": self.java_major,
            "release_time": self.release_time,
            "json_path": self.json_path,
            "game_dir": self.game_dir,
            "mods_dir": self.mods_dir,
            "is_loader_profile": self.is_loader_profile,
            "error": self.error,
        }


@dataclass
class LauncherLink:
    mode: str = "offline"
    url: str = ""
    token: str = ""
    version: str = ""
    app_dir: str = ""
    game_dir: str = ""
    bridge_file: str = ""
    error: str = ""
    version_isolation: bool = True
    loaders: dict[str, str] = field(default_factory=dict)

    @property
    def online(self) -> bool:
        return self.mode == "bridge" and bool(self.url and self.token)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "online": self.online,
            "url": self.url,
            "version": self.version,
            "app_dir": self.app_dir,
            "game_dir": self.game_dir,
            "bridge_file": self.bridge_file,
            "version_isolation": self.version_isolation,
            "error": self.error,
        }


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else None


def launcher_candidates() -> list[Path]:
    """常见的启动器安装/源码目录。"""
    roots: list[Path] = []
    for name in ("DARKITTEN_LAUNCHER_HOME", "DARKITTEN_HOME"):
        item = _env_path(name)
        if item:
            roots.append(item)
    roots.extend(
        [
            Path("D:/Darkitten Launcher"),
            Path("C:/Darkitten Launcher"),
            Path.home() / "Darkitten Launcher",
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "Darkitten Launcher",
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Darkitten Launcher",
            Path(os.environ.get("APPDATA", Path.home())) / "Darkitten Launcher",
            Path("C:/Program Files/Darkitten Launcher"),
        ]
    )
    expanded: list[Path] = []
    for root in roots:
        expanded.append(root)
        try:
            for child in root.glob("DarkittenLauncher-*"):
                if child.is_dir():
                    expanded.append(child)
        except OSError:
            continue
    seen: set[str] = set()
    result: list[Path] = []
    for item in expanded:
        try:
            key = str(item.resolve()).lower()
        except OSError:
            key = str(item).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _http_json(url: str, *, token: str = "", timeout: float = 20.0) -> dict:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["X-Darkitten-Token"] = token
    request = urllib.request.Request(url, headers=headers)
    raw, status, _headers = netclient.fetch_info(request, timeout=timeout)
    if status and status >= 400:
        raise LauncherBridgeError(f"HTTP {status}")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise LauncherBridgeError("返回的不是 JSON") from exc
    if not isinstance(data, dict):
        raise LauncherBridgeError("返回格式无法识别")
    return data


def _http_post(url: str, body: dict, *, token: str = "", timeout: float = 900.0) -> dict:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json; charset=utf-8",
        "X-Darkitten-Token": token,
    }
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    raw, status, _headers = netclient.fetch_info(request, timeout=timeout)
    if status and status >= 400:
        try:
            detail = json.loads(raw.decode("utf-8", errors="replace")).get("error", "")
        except Exception:  # noqa: BLE001
            detail = raw[:300].decode("utf-8", errors="replace")
        raise LauncherBridgeError(f"HTTP {status}：{detail}")
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise LauncherBridgeError("返回的不是 JSON") from exc
    if not isinstance(data, dict):
        raise LauncherBridgeError("返回格式无法识别")
    if data.get("ok") is False:
        raise LauncherBridgeError(str(data.get("error") or "启动器返回失败"))
    return data


def _loader_from_id(version_id: str) -> tuple[str, str, str]:
    """从版本名推断加载器、加载器版本和基础游戏版本。"""
    text = str(version_id or "")
    lower = text.lower()
    if lower.startswith("fabric-loader-"):
        rest = text[len("fabric-loader-") :]
        parts = rest.rsplit("-", 1)
        return "fabric", parts[0], parts[1] if len(parts) > 1 else ""
    if lower.startswith("quilt-loader-"):
        rest = text[len("quilt-loader-") :]
        parts = rest.rsplit("-", 1)
        return "quilt", parts[0], parts[1] if len(parts) > 1 else ""
    if lower.startswith("neoforge-"):
        return "neoforge", text[len("neoforge-") :], ""
    if "-neoforge-" in lower:
        base, rest = text.split("-neoforge-", 1)
        return "neoforge", rest, base
    if "-forge-" in lower:
        base, rest = text.split("-forge-", 1)
        return "forge", rest, base
    return "", "", text


class LauncherBridge:
    """启动器能力客户端；每次调用前会自动重新发现。"""

    def __init__(self, link: LauncherLink | None = None) -> None:
        self.link = link or discover()

    # ------------------------------------------------------------------
    def refresh(self) -> LauncherLink:
        self.link = discover()
        return self.link

    def about(self) -> dict:
        link = self.refresh()
        info = link.to_dict()
        if link.online:
            try:
                info["health"] = _http_json(f"{link.url}/health", timeout=6.0)
            except Exception as exc:  # noqa: BLE001 - 桥挂掉就回退文件模式
                info["error"] = str(exc)
                info["online"] = False
                self.link = link = LauncherLink(
                    mode="files" if link.game_dir else "offline",
                    app_dir=link.app_dir,
                    game_dir=link.game_dir,
                    bridge_file=link.bridge_file,
                    error=f"联动桥不可用：{exc}",
                )
                info.update(link.to_dict())
        return info

    def _bridge(self) -> LauncherLink:
        link = self.link
        if not link.online:
            link = self.refresh()
        if not link.online:
            raise LauncherBridgeError("启动器联动桥未运行。请先打开 Darkitten 启动器。")
        return link

    def _get(self, path: str, params: dict | None = None, timeout: float = 30.0) -> dict:
        link = self._bridge()
        query = urllib.parse.urlencode(
            {key: value for key, value in (params or {}).items() if value not in (None, "")}
        )
        url = f"{link.url}{path}" + (f"?{query}" if query else "")
        return _http_json(url, token=link.token, timeout=timeout)

    def _post(self, path: str, body: dict, timeout: float = 900.0) -> dict:
        link = self._bridge()
        return _http_post(f"{link.url}{path}", body, token=link.token, timeout=timeout)

    # ------------------------------------------------------------------
    def versions(self) -> list[LaunchInstance]:
        link = self.link
        if link.online:
            try:
                data = self._get("/versions")
                return [self._instance_from_bridge(item) for item in data.get("versions") or []]
            except Exception:  # noqa: BLE001 - 回退到目录扫描
                pass
        return self._scan_versions()

    def _instance_from_bridge(self, item: dict) -> LaunchInstance:
        version_id = str(item.get("id") or "")
        loader, loader_version, base = _loader_from_id(version_id)
        loader = str(item.get("loader") or loader)
        loader_version = str(item.get("loader_version") or loader_version)
        base = str(item.get("base_id") or item.get("parent_id") or base)
        instance_dir = self._instance_dir(version_id)
        return LaunchInstance(
            id=version_id,
            type=str(item.get("type") or "release"),
            loader=loader,
            loader_version=loader_version,
            base_id=base,
            java_major=int(item.get("java_major") or 0),
            release_time=str(item.get("release_time") or ""),
            json_path=str(item.get("json_path") or ""),
            game_dir=str(instance_dir),
            mods_dir=str(instance_dir / "mods"),
            is_loader_profile=bool(item.get("is_loader_profile")),
            error=str(item.get("error") or ""),
        )

    def _instance_dir(self, version_id: str) -> Path:
        link = self.link
        root = Path(link.game_dir) if link.game_dir else Path()
        if link.version_isolation and version_id:
            return root / "versions" / version_id
        return root

    def _scan_versions(self) -> list[LaunchInstance]:
        link = self.link
        if not link.game_dir:
            return []
        root = Path(link.game_dir) / "versions"
        result: list[LaunchInstance] = []
        try:
            folders = [item for item in root.iterdir() if item.is_dir()]
        except OSError:
            return []
        for folder in folders:
            json_path = folder / f"{folder.name}.json"
            if not json_path.is_file():
                continue
            data = _read_json(json_path)
            loader, loader_version, base = _loader_from_id(folder.name)
            inherits = str(data.get("inheritsFrom") or "")
            if inherits and not base:
                base = inherits
            is_profile = bool(inherits) and loader in ("fabric", "quilt") and folder.name.lower().startswith(
                (loader + "-loader-")
            )
            result.append(
                LaunchInstance(
                    id=folder.name,
                    type=str(data.get("type") or "release"),
                    loader=loader,
                    loader_version=loader_version,
                    base_id=base or inherits,
                    java_major=int((data.get("javaVersion") or {}).get("majorVersion") or 0),
                    release_time=str(data.get("releaseTime") or ""),
                    json_path=str(json_path),
                    game_dir=str(self._instance_dir(folder.name)),
                    mods_dir=str(self._instance_dir(folder.name) / "mods"),
                    is_loader_profile=is_profile,
                )
            )
        result.sort(key=lambda item: (item.release_time, item.id), reverse=True)
        return result

    def instances(self, loader: str = "") -> list[LaunchInstance]:
        items = [item for item in self.versions() if not item.is_loader_profile]
        if loader:
            items = [item for item in items if item.loader == loader]
        return items

    def recommended_instances(self) -> list[LaunchInstance]:
        """给工坊用的实例列表：原版和带加载器的都保留，优先带加载器。"""
        items = [item for item in self.versions() if not item.is_loader_profile]
        items.sort(key=lambda item: (bool(item.loader), item.release_time), reverse=True)
        return items

    # ------------------------------------------------------------------
    def downloads(self) -> dict:
        link = self.link
        if link.online:
            try:
                return self._get("/downloads", timeout=40.0)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "error": str(exc), **_static_downloads()}
        return {"ok": False, "error": "启动器未运行", **_static_downloads()}

    def vanilla(self, version_id: str) -> dict:
        if self.link.online:
            try:
                return self._get(f"/vanilla/{urllib.parse.quote(version_id)}", timeout=60.0)
            except Exception:  # noqa: BLE001 - 回退官方清单
                pass
        return _vanilla_from_mojang(version_id)

    def search_mods(self, query: str, *, loader: str = "", mc: str = "", limit: int = 20, offset: int = 0) -> dict:
        if self.link.online:
            try:
                return self._get(
                    "/mods/search",
                    {"query": query, "loader": loader, "mc": mc, "limit": limit, "offset": offset},
                )
            except Exception:  # noqa: BLE001
                pass
        from . import modlibrary

        hits = modlibrary.search(query, loader=loader, mc=mc, limit=limit, offset=offset)
        return {"ok": True, "hits": hits, "total_hits": len(hits)}

    def mod_versions(self, project: str, *, loader: str = "", mc: str = "") -> list[dict]:
        if self.link.online:
            try:
                data = self._get("/mods/versions", {"project": project, "loader": loader, "mc": mc})
                return list(data.get("versions") or [])
            except Exception:  # noqa: BLE001
                pass
        from . import modlibrary

        return modlibrary.versions(project, loader=loader, mc=mc)

    def download_mod(self, project: str, *, loader: str = "", mc: str = "", version: str = "", target: str = "learn", instance: str = "", progress: Progress | None = None) -> dict:
        if self.link.online:
            if progress is not None:
                progress(5, "正在通过启动器下载…")
            data = self._post(
                "/mods/download",
                {"project": project, "loader": loader, "mc": mc, "version": version, "target": target, "instance": instance},
            )
            if progress is not None:
                progress(100, f"已下载到 {data.get('path', '')}")
            return data
        from . import modlibrary

        version_info = modlibrary.pick_version(project, loader=loader, mc=mc)
        if not version_info:
            raise LauncherBridgeError("没有找到匹配的模组版本")

        def report(done: int, total: int) -> None:
            if progress is not None:
                progress(int(done * 100 / total) if total else 50, "下载中")

        path = modlibrary.download(version_info, progress=report if progress else None)
        return {"ok": True, "path": str(path), "size": path.stat().st_size}

    def decompile_mod(self, project: str, *, loader: str = "", mc: str = "", version: str = "", progress: Progress | None = None) -> dict:
        if self.link.online:
            data = self._post(
                "/mods/decompile",
                {"project": project, "loader": loader, "mc": mc, "version": version},
                timeout=1800.0,
            )
            if progress is not None:
                progress(100, "反编译完成")
            return data
        from . import modlibrary

        return modlibrary.learn(project, loader=loader, mc=mc, version_id=version, progress=progress)

    def learned(self) -> list[dict]:
        if self.link.online:
            try:
                return list(self._get("/learn").get("items") or [])
            except Exception:  # noqa: BLE001
                pass
        path = Path(self.link.app_dir or ".") / "learn" / "index.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return data if isinstance(data, list) else []

    def open_path(self, path: str | Path) -> dict:
        if self.link.online:
            try:
                return self._post("/open", {"path": str(path)}, timeout=20.0)
            except Exception:  # noqa: BLE001
                pass
        target = Path(path).expanduser()
        if not target.exists():
            raise FileNotFoundError(str(target))
        if os.name == "nt":
            os.startfile(str(target))  # noqa: S606 - 用户主动请求打开
        return {"ok": True, "path": str(target)}

    def activate(self) -> dict:
        if not self.link.online:
            raise LauncherBridgeError("启动器未运行")
        return self._post("/activate", {}, timeout=10.0)

    def launch(self, version_id: str) -> dict:
        if not self.link.online:
            raise LauncherBridgeError("启动器未运行，无法用启动器启动游戏")
        return self._post("/launch", {"version": version_id}, timeout=20.0)

    # ------------------------------------------------------------------
    def download_vanilla(self, version_id: str, kind: str, dest_dir: str | Path, *, progress: Progress | None = None) -> Path:
        """下载原版 client/server JAR；优先 BMCLAPI 镜像，再回退官方。"""
        info = self.vanilla(version_id)
        entry = (info.get("downloads") or {}).get(kind) or {}
        urls = [url for url in (entry.get("mirror"), entry.get("url")) if url]
        if not urls:
            raise LauncherBridgeError(f"{version_id} 没有 {kind} 下载地址")
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        name = f"{version_id}-{kind}.jar"
        target = dest / name
        sha1 = str(entry.get("sha1") or "")
        size = int(entry.get("size") or 0)
        if target.is_file() and target.stat().st_size > 0:
            if not size or target.stat().st_size == size:
                if progress is not None:
                    progress(100, f"{name} 已存在")
                return target
        last_error: Exception | None = None
        for url in urls:
            try:
                _download(url, target, size=size, progress=progress)
                if sha1:
                    digest = _sha1(target)
                    if digest != sha1:
                        raise LauncherBridgeError("SHA-1 校验失败")
                return target
            except Exception as exc:  # noqa: BLE001 - 试下一个源
                last_error = exc
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
        raise LauncherBridgeError(f"下载 {version_id} {kind} 失败：{last_error}")

    def mods_list(self, version_id: str = "") -> list[dict]:
        link = self.link
        if version_id:
            folder = self._instance_dir(version_id) / "mods"
        else:
            folder = Path(link.game_dir) / "mods"
        items: list[dict] = []
        try:
            files = list(folder.glob("*.jar"))
        except OSError:
            return []
        for path in files:
            try:
                stat = path.stat()
            except OSError:
                continue
            items.append({"name": path.name, "path": str(path), "size": stat.st_size})
        items.sort(key=lambda item: item["name"].lower())
        return items


def _sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path, *, size: int = 0, progress: Progress | None = None) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temp = target.with_name(target.name + ".part")
    try:
        with netclient.open_stream(request, timeout=60.0) as stream:
            total = size or int((stream.headers or {}).get("Content-Length") or 0)
            done = 0
            with temp.open("wb") as fp:
                while True:
                    chunk = stream.read(256 * 1024)
                    if not chunk:
                        break
                    fp.write(chunk)
                    done += len(chunk)
                    if progress is not None and total:
                        progress(min(99, int(done * 100 / total)), f"{target.name} · {done // 1024} KB")
        temp.replace(target)
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _static_downloads() -> dict:
    base = LAUNCHER_SITE.rstrip("/")
    return {
        "launcher": {"version": "", "site": LAUNCHER_SITE, "update_url": f"{base}/update.json"},
        "manifests": {
            "mod_agent": f"{base}/mod_agent_release.json",
            "pet": f"{base}/pet_release.json",
            "plugins": f"{base}/plugins.json",
        },
        "minecraft_manifest": {"official": MOJANG_MANIFEST, "bmclapi": BMCL_MANIFEST},
        "modrinth": "https://api.modrinth.com/v2",
    }


def _vanilla_from_mojang(version_id: str) -> dict:
    manifest: dict = {}
    errors: list[str] = []
    for url in (BMCL_MANIFEST, MOJANG_MANIFEST):
        try:
            manifest = _http_json(url, timeout=20.0)
            break
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    if not manifest:
        raise LauncherBridgeError("无法读取 Mojang 版本清单：" + "；".join(errors))
    target = ""
    for item in manifest.get("versions") or []:
        if item.get("id") == version_id:
            target = str(item.get("url") or "")
            break
    if not target:
        raise LauncherBridgeError(f"版本清单里没有 {version_id}")
    raw = _http_json(target, timeout=40.0)
    downloads = raw.get("downloads") or {}
    return {
        "ok": True,
        "id": raw.get("id", version_id),
        "type": raw.get("type", ""),
        "release_time": raw.get("releaseTime", ""),
        "local": "",
        "source": target,
        "java_major": (raw.get("javaVersion") or {}).get("majorVersion", 0),
        "asset_index": raw.get("assetIndex") or {},
        "downloads": {
            "client": downloads.get("client") or {},
            "server": downloads.get("server") or {},
        },
        "libraries": [],
        "fallback": True,
    }


def discover() -> LauncherLink:
    """优先找正在运行的联动桥，其次读启动器配置文件。"""
    env_file = _env_path("DARKITTEN_BRIDGE_FILE")
    candidates: list[Path] = []
    if env_file:
        candidates.append(env_file)
    errors: list[str] = []
    first_app: Path | None = None
    first_game = ""
    first_isolation = True
    first_loaders: dict[str, str] = {}
    for root in launcher_candidates():
        bridge_file = root / "bridge.json"
        if bridge_file.is_file():
            try:
                data = _read_json(bridge_file)
                url = str(data.get("url") or "").strip()
                token = str(data.get("token") or "").strip()
                if url and token:
                    health = _http_json(f"{url}/health", timeout=5.0)
                    if health.get("ok"):
                        return LauncherLink(
                            mode="bridge",
                            url=url.rstrip("/"),
                            token=token,
                            version=str(data.get("version") or health.get("version") or ""),
                            app_dir=str(data.get("app_dir") or root),
                            game_dir=str(data.get("game_dir") or ""),
                            bridge_file=str(bridge_file),
                            version_isolation=True,
                        )
            except Exception as exc:  # noqa: BLE001 - 桥挂了继续找文件
                errors.append(f"{bridge_file}: {exc}")
        config_file = root / "config.json"
        if config_file.is_file() and first_app is None:
            data = _read_json(config_file)
            if data.get("game_dir") or (root / "minecraft").is_dir():
                first_app = root
                first_game = str(data.get("game_dir") or (root / "minecraft"))
                first_isolation = bool(data.get("version_isolation", True))
                first_loaders = {
                    str(key): str(value)
                    for key, value in (data.get("version_loaders") or {}).items()
                    if str(key).strip() and str(value).strip()
                }
    if first_app is not None:
        return LauncherLink(
            mode="files",
            app_dir=str(first_app),
            game_dir=first_game,
            version_isolation=first_isolation,
            loaders=first_loaders,
            error="启动器未运行，只提供本地版本信息；打开启动器后可联动下载。",
        )
    return LauncherLink(mode="offline", error="；".join(errors) or "没有找到 Darkitten 启动器。")