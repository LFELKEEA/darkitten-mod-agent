"""在线资源准备：JDK、Gradle、MDK、源码、版本目录全部按需拉取。

发行版本体只包含程序代码和空目录，不再捆绑几 GB 的 JDK / Gradle / 依赖缓存。
第一次使用或换机器时，由这里按“官方源 -> 国内镜像”的顺序在线获取：

- JDK 21：Adoptium / 中科大 / 华为镜像；
- Gradle：wrapper 声明的版本，官方源 / 华为 / 腾讯镜像；
- MDK、Javadoc、sources.jar：加载器 Maven 与 BMCLAPI 镜像；
- 版本清单：Mojang、Fabric、Quilt、Forge、NeoForge；
- 参考模组：Modrinth API 与 CDN。

每个资源都有明确的本地落点，下载失败会保留已有缓存并给出可执行的下一步。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import catalog as catalog_mod
from . import devdocs, toolchain
from .version import (
    APP_VERSION,
    SITE_URL,
    UPDATE_MANIFEST_URL,
)

Log = Callable[[str], None]

MIRRORS: dict[str, list[str]] = {
    "mojang": [
        "https://piston-meta.mojang.com",
        "https://bmclapi2.bangbang93.com",
    ],
    "fabric": [
        "https://meta.fabricmc.net",
        "https://bmclapi2.bangbang93.com/fabric-meta",
    ],
    "quilt": [
        "https://meta.quiltmc.org",
        "https://bmclapi2.bangbang93.com/quilt-meta",
    ],
    "forge": [
        "https://maven.minecraftforge.net",
        "https://bmclapi2.bangbang93.com/maven",
    ],
    "neoforge": [
        "https://maven.neoforged.net/releases",
        "https://bmclapi2.bangbang93.com/maven",
    ],
    "gradle": [
        "https://services.gradle.org",
        "https://repo.huaweicloud.com/gradle",
        "https://mirrors.cloud.tencent.com/gradle",
    ],
    "jdk": [
        "https://api.adoptium.net",
        "https://mirrors.ustc.edu.cn/adoptium",
        "https://mirrors.huaweicloud.com/openjdk",
    ],
    "mods": [
        "https://api.modrinth.com/v2",
        "https://cdn.modrinth.com",
    ],
}

RESOURCE_KINDS = ("all", "jdk", "gradle", "catalog", "mdk", "sources", "docs", "status")


def _emit(progress: Callable[[int, str], None] | None, percent: int, text: str) -> None:
    if progress is not None:
        progress(percent, text)


def _log(progress: Callable[[int, str], None] | None) -> Log:
    return lambda text: _emit(progress, 50, text)


def _catalog() -> catalog_mod.Catalog:
    return catalog_mod.Catalog()


def site_manifest(timeout: float = 10.0) -> dict[str, Any]:
    """优先读取网站发布的引导清单；不可达时退回本地清单。"""
    url = SITE_URL + "mod_agent_bootstrap.json"
    try:
        data = catalog_mod.fetch_json(url, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - 网站不可达不阻塞本地使用
        fallback = bootstrap_manifest()
        fallback["source"] = "local"
        fallback["error"] = str(exc)
        return fallback
    if not isinstance(data, dict) or not data.get("resources"):
        fallback = bootstrap_manifest()
        fallback["source"] = "local"
        fallback["error"] = "网站清单格式无法识别。"
        return fallback
    data["source"] = "site"
    return data


def _gradle_distribution(project_root: Path | None):
    if project_root is None:
        return None
    url = toolchain.distribution_url(project_root) or toolchain.default_distribution_url()
    return toolchain.distribution_for(url)


def status(project_root: Path | None = None) -> dict[str, Any]:
    """查看当前机器已经具备哪些在线资源（不下载任何东西）。"""
    java = toolchain.find_java()
    dist = _gradle_distribution(project_root)
    catalog = _catalog()
    docs = devdocs.status()
    try:
        summary = catalog.summary()
    except Exception:  # noqa: BLE001 - 目录损坏不应阻塞状态页
        summary = {}
    return {
        "policy": "online-first",
        "site": SITE_URL,
        "update_manifest": UPDATE_MANIFEST_URL,
        "java": (
            {
                "home": str(java.home),
                "version": java.version,
                "major": java.major,
                "source": java.source,
            }
            if java is not None
            else None
        ),
        "gradle": (
            {
                "url": dist.url,
                "version": dist.version,
                "home": str(dist.home),
                "installed": dist.installed(),
            }
            if dist is not None
            else None
        ),
        "catalog": summary,
        "docs": {
            "count": docs.get("count", 0),
            "bytes": docs.get("bytes", 0),
            "root": docs.get("root", ""),
        },
        "mirrors": MIRRORS,
    }


def ensure(
    kind: str,
    *,
    project_root: Path | None = None,
    loader: str = "",
    mc: str = "",
    version: str = "",
    progress: Callable[[int, str], None] | None = None,
    cancel=None,
) -> dict[str, Any]:
    """按需拉取资源；返回每一项的真实落点或失败原因。"""
    key = str(kind or "status").strip().lower()
    if key not in RESOURCE_KINDS:
        raise ValueError(f"未知资源类型：{kind}")
    if key == "status":
        return status(project_root)
    site = site_manifest()
    results: list[dict[str, Any]] = []
    log = _log(progress)
    steps = (
        [key]
        if key != "all"
        else ["jdk", "gradle", "catalog", "mdk", "sources"]
    )
    for step in steps:
        try:
            if step == "jdk":
                _emit(progress, 5, "检查 JDK 21…")
                install = toolchain.ensure_java(log=log, allow_download=True)
                results.append(
                    {
                        "kind": "jdk",
                        "ok": install is not None and install.major >= 17,
                        "home": str(install.home) if install else "",
                        "version": install.version if install else "",
                        "source": install.source if install else "",
                    }
                )
            elif step == "gradle":
                if project_root is None:
                    results.append(
                        {
                            "kind": "gradle",
                            "ok": False,
                            "error": "需要先打开工程，Gradle 版本由工程的 wrapper 决定。",
                        }
                    )
                    continue
                dist = toolchain.ensure_distribution(
                    Path(project_root), log=log, allow_download=True
                )
                results.append(
                    {
                        "kind": "gradle",
                        "ok": bool(dist and dist.installed()),
                        "home": str(dist.home) if dist else "",
                        "version": dist.version if dist else "",
                    }
                )
            elif step == "catalog":
                _emit(progress, 15, "同步 Mojang / Fabric / Forge / NeoForge / Quilt 目录…")
                report = _catalog().refresh(log)
                results.append(
                    {
                        "kind": "catalog",
                        "ok": bool(report.text()),
                        "message": report.text()[:4000],
                    }
                )
            elif step in {"mdk", "docs", "sources"}:
                if not loader:
                    results.append(
                        {"kind": step, "ok": False, "error": "需要 loader（fabric/forge/neoforge/quilt）。"}
                    )
                    continue
                if step == "mdk":
                    item = _catalog().mdk(loader, mc, version)
                    if not item:
                        results.append(
                            {"kind": "mdk", "ok": False, "error": "没有匹配的 MDK 版本。"}
                        )
                        continue
                    path = _catalog().download(
                        item,
                        dest_dir=catalog_mod.artifacts_dir() / "mdk",
                        progress=progress,
                        cancel=cancel,
                    )
                    results.append(
                        {
                            "kind": "mdk",
                            "ok": True,
                            "path": str(path),
                            "url": item.get("url", ""),
                            "mirrors": item.get("mirrors") or [],
                        }
                    )
                else:
                    path = devdocs.ensure_sources(
                        loader,
                        mc,
                        version,
                        progress=progress,
                        cancel=cancel,
                    )
                    results.append(
                        {
                            "kind": step,
                            "ok": path is not None,
                            "path": str(path) if path else "",
                        }
                    )
            else:
                results.append({"kind": step, "ok": False, "error": "未实现。"})
        except Exception as exc:  # noqa: BLE001 - 单项失败保留其它结果
            results.append({"kind": step, "ok": False, "error": str(exc)})
    return {
        "policy": "online-first",
        "bootstrap": {
            "source": site.get("source", "local"),
            "version": site.get("version", ""),
            "site": site.get("site", SITE_URL),
        },
        "results": results,
        "mirrors": site.get("mirrors") or MIRRORS,
    }


def render(result: dict[str, Any]) -> str:
    if "results" not in result:
        return _render_status(result)
    bootstrap = result.get("bootstrap") or {}
    source = (
        "网站清单"
        if bootstrap.get("source") == "site"
        else "本地清单（网站不可达）"
    )
    lines = [f"在线资源准备结果（{source}）："]
    for item in result.get("results") or []:
        state = "成功" if item.get("ok") else "失败"
        bits = [f"- {item.get('kind')}：{state}"]
        for field in ("home", "path", "version", "source"):
            if item.get(field):
                bits.append(f"{field}={item[field]}")
        if item.get("error"):
            bits.append(f"原因={item['error']}")
        lines.append(" · ".join(bits))
    return "\n".join(lines)


def _render_status(data: dict[str, Any]) -> str:
    java = data.get("java") or {}
    gradle = data.get("gradle") or {}
    catalog = data.get("catalog") or {}
    docs = data.get("docs") or {}
    lines = [
        "在线资源状态（不会自动下载）：",
        f"- JDK：{java.get('version') or '未找到'} {java.get('home') or ''}",
        f"- Gradle：{gradle.get('version') or '未准备'} "
        f"{'已安装' if gradle.get('installed') else '未安装'} {gradle.get('home') or ''}",
        f"- 版本目录：Mojang {catalog.get('minecraft_versions', 0)} · "
        f"Fabric {catalog.get('fabric_loader', 0)} · "
        f"Forge {catalog.get('forge_versions', 0)} · "
        f"NeoForge {catalog.get('neoforge_versions', 0)}",
        f"- 源码构件：{docs.get('count', 0)} 个 / {docs.get('bytes', 0) / 1024 / 1024:.1f} MB",
        "- 镜像顺序：官方源 -> 华为 / 腾讯 / 中科大 / BMCLAPI",
    ]
    return "\n".join(lines)


def bootstrap_manifest() -> dict[str, Any]:
    """给轻量发行版和网站用的在线引导清单。"""
    return {
        "name": "Darkitten Mod Agent 在线版",
        "version": APP_VERSION,
        "policy": "online-first",
        "site": SITE_URL,
        "update_manifest": UPDATE_MANIFEST_URL,
        "mirrors": MIRRORS,
        "resources": [
            {
                "kind": "jdk",
                "major": 21,
                "required_for": "Gradle 构建",
                "sources": MIRRORS["jdk"],
            },
            {
                "kind": "gradle",
                "required_for": "工程 wrapper 声明的 Gradle 版本",
                "sources": MIRRORS["gradle"],
            },
            {
                "kind": "catalog",
                "required_for": "版本选择、MDK / 源码索引",
                "sources": ["https://piston-meta.mojang.com", *MIRRORS["fabric"], *MIRRORS["forge"]],
            },
            {
                "kind": "sources",
                "required_for": "API 查询与参考代码",
                "sources": [*MIRRORS["forge"], *MIRRORS["neoforge"]],
            },
            {
                "kind": "mods",
                "required_for": "自动参考反编译",
                "sources": MIRRORS["mods"],
            },
        ],
        "notes": [
            "程序本体不包含 JDK、Gradle、依赖缓存或第三方模组源码。",
            "首次构建会自动从官方源或镜像下载所需环境。",
            "所有下载都落在程序 data 目录，可随时删除后重新拉取。",
        ],
    }
