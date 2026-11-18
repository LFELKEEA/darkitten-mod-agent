"""构建全量离线发行版（默认发行版已改为在线优先，本脚本需显式 --offline）。

日常发布请用 ``python build.py``：程序本体保持轻量，JDK、Gradle、开发资料和
参考模组都在首次使用时从网站与镜像按需拉取。

普通 build.py 只打程序本体（约 140MB），用户第一次做模组仍要下载 JDK、
Gradle 发行版、Minecraft 依赖和 NeoForm 缓存。这个脚本把本机已经验证可用
的整套开发缓存一起打包，得到一个开箱即用、离线也能完成首次编译的大型发行版。

产物结构（dist/DarkittenModAgent-full/）::

    DarkittenModAgent.exe
    _internal/
    data/
      tools/jdk-21/        # 可直接用于 Gradle 的 JDK
      gradle/              # Gradle wrapper + 依赖 + NeoForm / Loom 缓存
      mirrors/             # 本地 Maven / Mojang 镜像缓存
      catalog/             # 版本清单与 MDK / Javadoc / 源码索引
    docs/                  # 离线说明
    README-FULL.txt

打包时按 core / jdk / gradle / assets 分组生成多个 ZIP，每个 ZIP 默认不超过
1900MB，方便上传到 GitHub Release（单资产上限 2GB）。所有分卷解压到同一个
目录即可，不要分别解压到不同位置。

用法::

    python build_full.py                      # 复用 dist/DarkittenModAgent，自动查找缓存
    python build_full.py --build              # 先跑 PyInstaller
    python build_full.py --source-cache "D:/.../DarkittenModAgent/data"
    python build_full.py --dry-run            # 只打印计划，不复制不压缩
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import zipfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dma.version import APP_NAME, APP_VERSION  # noqa: E402

try:  # Windows 控制台默认 GBK，日志里的中文会变成乱码。
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

DIST = ROOT / "dist"
APP_DIST = DIST / "DarkittenModAgent"
FULL_DIST = DIST / "DarkittenModAgent-full"
FULL_ZIP_PREFIX = f"DarkittenModAgent-full-{APP_VERSION}"
MIN_SIZE_DEFAULT = 2 * 1024**3
SPLIT_DEFAULT = 1900 * 1024**2

# 这些是 Gradle 的运行期垃圾，换机器后没有价值，反而会拖慢首次启动。
GRADLE_SKIP_DIRS = {
    "daemon",
    "notifications",
    "workers",
}
GRADLE_SKIP_FILES = {
    "daemon",
    ".lock",
    "gc.properties",
}
GRADLE_SKIP_SUFFIX = (
    ".lock",
    ".part",
    ".log",
    ".hprof",
)

# 已经是压缩格式的文件用 STORED，省时间也不损失体积。
_STORED_SUFFIX = (
    ".jar",
    ".zip",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ogg",
    ".mp3",
    ".class",
    ".bin",
    ".pack",
    ".dat",
    ".nbt",
    ".gz",
    ".xz",
    ".zst",
)


# ----------------------------------------------------------------------
# 基础工具


def log(message: str) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _long_path(path: Path) -> str:
    text = str(Path(path).resolve())
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _copy_file(src: Path, dest: Path) -> tuple[bool, int]:
    """复制单文件；已存在且大小一致时跳过。返回 (是否复制, 字节数)。"""
    try:
        size = src.stat().st_size
    except OSError:
        return False, 0
    if dest.exists():
        try:
            if dest.stat().st_size == size:
                return False, 0
        except OSError:
            pass
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        os.makedirs(_long_path(dest.parent), exist_ok=True)
    try:
        shutil.copy2(src, dest)
    except OSError:
        # 路径过长时走 Windows 长路径前缀。
        with open(_long_path(src), "rb") as reader, open(_long_path(dest), "wb") as writer:
            shutil.copyfileobj(reader, writer, 1024 * 1024)
        try:
            shutil.copystat(_long_path(src), _long_path(dest))
        except OSError:
            pass
    return True, size


def _skip_gradle_file(path: Path) -> bool:
    name = path.name.lower()
    if name in GRADLE_SKIP_FILES:
        return True
    return any(name.endswith(suffix) for suffix in GRADLE_SKIP_SUFFIX)


def copy_tree(
    src: Path,
    dest: Path,
    *,
    label: str = "",
    skip_dirs: set[str] | None = None,
    skip_file=None,
) -> tuple[int, int]:
    """复制目录，跳过已存在且大小一致的文件。返回 (文件数, 字节数)。"""
    if not src.is_dir():
        log(f"跳过 {label or src}：目录不存在")
        return 0, 0
    skip_dirs = skip_dirs or set()
    files = 0
    total = 0
    started = time.monotonic()
    last = started
    for item in src.rglob("*"):
        try:
            if item.is_dir():
                continue
        except OSError:
            continue
        try:
            relative = item.relative_to(src)
        except ValueError:
            continue
        if any(part in skip_dirs for part in relative.parts[:-1]):
            continue
        if skip_file is not None and skip_file(item):
            continue
        copied, size = _copy_file(item, dest / relative)
        if copied:
            files += 1
            total += size
        now = time.monotonic()
        if now - last > 5:
            last = now
            log(
                f"  {label or src.name}: {files} 个文件 / {human(total)}"
                f"（{human(total / max(now - started, 0.001))}/s）"
            )
    log(f"复制完成 {label or src.name}：{files} 个文件 / {human(total)}")
    return files, total


def _rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(_long_path(path), ignore_errors=True)


# ----------------------------------------------------------------------
# 源缓存定位


def _candidate_data_dirs(explicit: str = "") -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("DARKITTEN_MOD_AGENT_DATA", "").strip()
    if env:
        candidates.append(Path(env).expanduser())
    local = ROOT / "data"
    candidates.append(local)
    for base in (
        Path(r"D:/Darkitten Launcher/DarkittenLauncher-1.11.6-source/mod_agent/DarkittenModAgent/data"),
        Path.home() / "Darkitten Launcher" / "mod_agent" / "DarkittenModAgent" / "data",
        Path(os.environ.get("LOCALAPPDATA", "")) / "DarkittenModAgent" / "data",
    ):
        if str(base):
            candidates.append(base)
    unique: list[Path] = []
    seen: set[str] = set()
    for item in candidates:
        try:
            key = str(item.resolve()).lower()
        except OSError:
            key = str(item).lower()
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def pick_data_dir(explicit: str = "") -> tuple[Path | None, str]:
    """挑选缓存最完整的 data 目录。"""
    best: Path | None = None
    best_score = 0
    best_note = ""
    for item in _candidate_data_dirs(explicit):
        if not item.is_dir():
            continue
        score = 0
        notes: list[str] = []
        gradle = item / "gradle"
        if (gradle / "caches" / "modules-2").is_dir():
            score += 2
            notes.append("gradle/modules-2")
        if (gradle / "caches" / "neoformruntime").is_dir():
            score += 2
            notes.append("neoformruntime")
        if (gradle / "caches" / "fabric-loom").is_dir():
            score += 1
            notes.append("fabric-loom")
        if (gradle / "wrapper" / "dists").is_dir():
            score += 1
            notes.append("wrapper")
        if (item / "mirrors").is_dir():
            score += 1
            notes.append("mirrors")
        if (item / "catalog" / "catalog.json").is_file():
            score += 1
            notes.append("catalog")
        if score > best_score:
            best, best_score, best_note = item, score, "、".join(notes)
    if best is not None:
        log(f"缓存来源：{best}（{best_note}）")
    else:
        log("没有找到可复用的 data 缓存")
    return best, best_note


def find_jdk() -> Path | None:
    candidates: list[Path] = []
    for env_name in ("DARKITTEN_JDK21",):
        value = os.environ.get(env_name, "").strip()
        if value:
            candidates.append(Path(value))
    candidates.extend(
        [
            Path(r"D:/developer/Java/jdk-21"),
            Path(os.environ.get("JAVA_HOME", "")) if os.environ.get("JAVA_HOME") else None,
        ]
    )
    tools = ROOT / "data" / "tools"
    if tools.is_dir():
        candidates.extend(sorted(tools.glob("jdk-2*")))
        candidates.extend(sorted(tools.glob("jdk-21*")))
    for item in candidates:
        if item is None or not item.is_dir():
            continue
        if (item / "bin" / "java.exe").is_file() or (item / "bin" / "java").is_file():
            try:
                release = (item / "release").read_text(encoding="utf-8", errors="ignore")
                if "JAVA_VERSION=\"21" in release or "JAVA_VERSION=\"17" in release:
                    return item
            except OSError:
                pass
            return item
    return None


# ----------------------------------------------------------------------
# 组装


def prepare_base(build: bool) -> None:
    exe = APP_DIST / "DarkittenModAgent.exe"
    if build or not exe.is_file():
        log("运行 PyInstaller…")
        import build as build_mod

        build_mod.run_pyinstaller()
    if not (APP_DIST / "DarkittenModAgent.exe").is_file():
        raise SystemExit(f"基础发行版缺失：{APP_DIST}")


def distribution_stats() -> dict:
    """按最终目录统计各分组体积；--reuse 时同名文件会被跳过，不能沿用复制量。"""
    data_root = FULL_DIST / "data"
    result = {
        "jdk": dir_size(data_root / "tools" / "jdk-21"),
        "gradle": dir_size(data_root / "gradle"),
        "mirrors": dir_size(data_root / "mirrors"),
        "catalog": dir_size(data_root / "catalog"),
        "docs": dir_size(FULL_DIST / "docs"),
    }
    result["app"] = max(0, dir_size(FULL_DIST) - sum(result.values()))
    return result


def assemble(data_dir: Path | None, *, with_jdk: bool, with_gradle: bool, reuse: bool = False) -> dict:
    log(f"准备全量目录：{FULL_DIST}")
    if FULL_DIST.exists() and not reuse:
        log("清理旧的全量目录…")
        _rmtree(FULL_DIST)
    FULL_DIST.mkdir(parents=True, exist_ok=True)

    log("复制程序本体…")
    copy_tree(APP_DIST, FULL_DIST, label="程序本体")

    result = {
        "app": 0,
        "jdk": 0,
        "gradle": 0,
        "mirrors": 0,
        "catalog": 0,
        "docs": 0,
    }

    # 已有的 data 目录只取缓存，不把 config.json / sessions / projects 带进发行版。
    if data_dir is not None and with_gradle:
        gradle_src = data_dir / "gradle"
        if gradle_src.is_dir():
            log(f"复制 Gradle 缓存（{human(dir_size(gradle_src))}）…")
            copy_tree(
                gradle_src,
                FULL_DIST / "data" / "gradle",
                label="Gradle 缓存",
                skip_dirs=GRADLE_SKIP_DIRS,
                skip_file=_skip_gradle_file,
            )
        mirrors_src = data_dir / "mirrors"
        if mirrors_src.is_dir():
            log(f"复制镜像缓存（{human(dir_size(mirrors_src))}）…")
            copy_tree(
                mirrors_src,
                FULL_DIST / "data" / "mirrors",
                label="镜像缓存",
            )
        catalog_src = data_dir / "catalog"
        if catalog_src.is_dir():
            log("复制开发生态目录…")
            copy_tree(
                catalog_src,
                FULL_DIST / "data" / "catalog",
                label="生态目录",
            )

    if with_jdk:
        jdk = find_jdk()
        if jdk is None:
            log("没有找到可用 JDK，跳过内置 JDK")
        else:
            log(f"内置 JDK：{jdk}（{human(dir_size(jdk))}）")
            copy_tree(
                jdk,
                FULL_DIST / "data" / "tools" / "jdk-21",
                label="JDK 21",
            )
            marker = FULL_DIST / "data" / "tools" / "jdk-21" / ".darkitten-jdk"
            marker.write_text(
                "bundled with the full offline distribution\n", encoding="utf-8"
            )

    docs_src = ROOT / "docs"
    if docs_src.is_dir():
        copy_tree(docs_src, FULL_DIST / "docs", label="离线文档")

    # 统计必须基于最终目录，--reuse 时同名文件会被跳过，不能沿用本次复制量。
    result = distribution_stats()

    write_readme(data_dir)
    return result


def sync_catalog(dest: Path) -> None:
    """联网刷新版本清单，写入全量目录的 data/catalog。"""
    from dma import catalog as catalog_mod

    log("同步开发生态目录（Mojang / Fabric / Forge / NeoForge / Quilt）…")
    catalog = catalog_mod.Catalog(dest / "catalog.json")
    try:
        report = catalog.refresh(progress=lambda text: log(f"  {text}"))
    except Exception as exc:  # noqa: BLE001 - 构建不能因为某个源失败而中断
        log(f"目录同步失败：{exc}")
        return
    log(report.text())
    log(f"目录条目：{json.dumps(catalog.summary(), ensure_ascii=False)}")


def download_docs(dest: Path, mc: str) -> None:
    """下载指定 MC 版本四种加载器的 MDK 与文档/源码。"""
    from dma import catalog as catalog_mod

    catalog = catalog_mod.Catalog(dest / "catalog.json")
    if not mc:
        return
    for loader in catalog_mod.LOADERS:
        for kind, item in (
            ("mdk", catalog.mdk(loader, mc)),
            ("javadoc", catalog.javadoc(loader, mc)),
        ):
            if not item:
                log(f"  {loader} {kind}：没有匹配版本，跳过")
                continue
            try:
                path = catalog.download(item, dest_dir=dest / "artifacts" / kind)
                log(f"  {loader} {kind}：{path.name}（{human(path.stat().st_size)}）")
            except Exception as exc:  # noqa: BLE001
                log(f"  {loader} {kind} 下载失败：{exc}")


def write_readme(data_dir: Path | None) -> None:
    lines = [
        f"{APP_NAME} 全量离线发行版 {APP_VERSION}",
        "=" * 56,
        "",
        "这个版本已经把 JDK、Gradle 发行版、Minecraft 依赖、NeoForm / Loom 缓存",
        "和版本清单一起打包，第一次做模组不需要再下载几个 GB 的环境。",
        "",
        "使用方法",
        "--------",
        "1. 把全部分卷压缩包解压到同一个目录（不要分别解压到不同位置）。",
        "2. 双击 DarkittenModAgent.exe。",
        "3. 首次启动会自动识别 data/tools/jdk-21 和 data/gradle，无需手动配置。",
        "",
        "目录说明",
        "--------",
        "data/tools/jdk-21   JDK 21，Gradle 构建直接使用",
        "data/gradle         Gradle wrapper、依赖与 NeoForm / Loom 缓存",
        "data/mirrors        本地 Maven / Mojang 镜像缓存",
        "data/catalog/Minecraft 版本、Fabric / Forge / NeoForge / Quilt 资料",
        "docs/               离线使用说明",
        "",
        "注意事项",
        "--------",
        "- 不要删除 data/gradle，删除后需要重新下载数 GB 依赖。",
        "- 如果系统设置了 GRADLE_USER_HOME，程序会优先使用该变量。",
        "- 缓存来自公开的 Gradle / Maven / Mojang 仓库，仅用于加速本地构建。",
        "- 反编译得到的三方模组源码仅限本地学习与排错，请遵守原模组许可证。",
        "",
    ]
    if data_dir is not None:
        lines.append(f"缓存来源：{data_dir}")
    (FULL_DIST / "README-FULL.txt").write_text(
        "\n".join(lines), encoding="utf-8"
    )


# ----------------------------------------------------------------------
# 分卷压缩


class VolumeWriter:
    def __init__(self, prefix: str, max_bytes: int) -> None:
        self.prefix = prefix
        self.max_bytes = max(64 * 1024**2, int(max_bytes))
        self.parts: list[Path] = []
        self._index = 0
        self._zip: zipfile.ZipFile | None = None
        self._handle = None
        self._bytes = 0
        self.files = 0
        self.total_bytes = 0

    def _open(self) -> None:
        self._index += 1
        target = DIST / f"{self.prefix}-part{self._index:02d}.zip"
        self._handle = target.open("wb")
        self._zip = zipfile.ZipFile(
            self._handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1
        )
        self.parts.append(target)
        self._bytes = 0
        log(f"写入分卷：{target.name}")

    def _close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if self._handle is not None:
            try:
                self._bytes = self._handle.tell()
            except OSError:
                pass
            self._handle.close()
            self._handle = None
        self.total_bytes += self._bytes

    def add(self, path: Path, arcname: str) -> None:
        if self._zip is None:
            self._open()
        compression = (
            zipfile.ZIP_STORED
            if path.suffix.lower() in _STORED_SUFFIX
            else zipfile.ZIP_DEFLATED
        )
        try:
            info = zipfile.ZipInfo.from_file(path, arcname)
            info.compress_type = compression
            info.external_attr = 0o644 << 16
            with path.open("rb") as source, self._zip.open(info, "w") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        except (OSError, ValueError) as exc:
            log(f"  跳过 {arcname}：{exc}")
            return
        self.files += 1
        try:
            current = self._handle.tell()
        except (OSError, AttributeError):
            current = self._bytes
        if current >= self.max_bytes:
            self._close()
            self._open()

    def finish(self) -> list[Path]:
        if self._zip is not None:
            self._close()
        # 最后一个分卷可能是空的（刚好在阈值处切换）。
        if self.parts and not self.parts[-1].exists():
            self.parts.pop()
        return self.parts


def package(groups: dict[str, list[Path]], max_bytes: int) -> list[dict]:
    log("开始生成分卷压缩包…")
    results: list[dict] = []
    used_names: set[str] = set()
    for group, paths in groups.items():
        if not paths:
            continue
        writer = VolumeWriter(f"{FULL_ZIP_PREFIX}-{group}", max_bytes)
        for path in sorted(paths, key=lambda item: item.as_posix()):
            arcname = path.relative_to(FULL_DIST).as_posix()
            writer.add(path, arcname)
        parts = writer.finish()
        for part in parts:
            digest = sha256(part)
            results.append(
                {
                    "group": group,
                    "name": part.name,
                    "size": part.stat().st_size,
                    "sha256": digest,
                }
            )
            used_names.add(part.name)
            log(f"  {part.name}  {human(part.stat().st_size)}  {digest[:16]}…")
    if not results:
        raise SystemExit("没有生成任何压缩包")
    return results


def group_files() -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = {
        "core": [],
        "jdk": [],
        "gradle": [],
        "assets": [],
    }
    for path in FULL_DIST.rglob("*"):
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        relative = path.relative_to(FULL_DIST)
        parts = relative.parts
        if parts and parts[0] == "data":
            if len(parts) >= 2 and parts[1] == "tools":
                groups["jdk"].append(path)
                continue
            if len(parts) >= 2 and parts[1] == "gradle":
                groups["gradle"].append(path)
                continue
            groups["assets"].append(path)
            continue
        if parts and parts[0] == "docs":
            groups["assets"].append(path)
            continue
        groups["core"].append(path)
    return groups


def write_manifest(parts: list[dict], stats: dict, min_size: int) -> Path:
    total = sum(item["size"] for item in parts)
    raw = dir_size(FULL_DIST)
    manifest = {
        "name": f"{APP_NAME} 全量离线版",
        "version": APP_VERSION,
        "released": date.today().isoformat(),
        "tag": f"mod-agent-v{APP_VERSION}",
        "kind": "full",
        "uncompressed_size": raw,
        "archive_size": total,
        "min_expected_size": min_size,
        "parts": parts,
        "stats": stats,
        "notes": [
            "包含 JDK 21、Gradle 依赖与 NeoForm / Loom 缓存，首次构建可离线完成。",
            "所有分卷解压到同一个目录后运行 DarkittenModAgent.exe。",
            "每个分卷均可独立解压，但必须合并到同一目标目录。",
        ],
        "page_url": "https://lfelkeea.github.io/darkitten-launcher/",
        "github_url": "https://github.com/LFELKEEA/darkitten-mod-agent",
    }
    target = DIST / "mod_agent_full_release.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


# ----------------------------------------------------------------------
# 入口


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="构建全量离线发行版（需显式 --offline；默认发行版已改为在线优先）"
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="确认要生成内置缓存的离线版；不传该参数会直接退出",
    )
    parser.add_argument("--build", action="store_true", help="先运行 PyInstaller")
    parser.add_argument("--source-cache", default="", help="已有的 data 目录")
    parser.add_argument("--no-jdk", action="store_true", help="不内置 JDK")
    parser.add_argument("--no-gradle", action="store_true", help="不内置 Gradle 缓存")
    parser.add_argument(
        "--min-size-mb",
        type=int,
        default=MIN_SIZE_DEFAULT // 1024**2,
        help="低于该体积时报错，默认 2048",
    )
    parser.add_argument(
        "--split-mb",
        type=int,
        default=SPLIT_DEFAULT // 1024**2,
        help="单个分卷上限，默认 1900",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划")
    parser.add_argument("--allow-small", action="store_true", help="体积不足也继续")
    parser.add_argument("--keep-zips", action="store_true", help="保留旧分卷")
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="复用已有全量目录，只覆盖变化文件（增量更新用）",
    )
    parser.add_argument(
        "--sync-catalog",
        action="store_true",
        help="打包前联网刷新 Mojang / Fabric / Forge / NeoForge / Quilt 目录",
    )
    parser.add_argument(
        "--docs-mc",
        default="",
        help="下载该 Minecraft 版本四种加载器的 MDK 与文档/源码，例如 1.21.1",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not args.offline:
        log(
            "默认发行版已经改为在线优先：python build.py 生成轻量包，"
            "首次构建时从网站与镜像拉取 JDK / Gradle / 开发资料。"
        )
        log("如确需内置数 GB 缓存的离线版，请显式加 --offline。")
        raise SystemExit(2)
    if not args.keep_zips:
        for old in DIST.glob(f"{FULL_ZIP_PREFIX}-*-part*.zip"):
            old.unlink(missing_ok=True)

    data_dir, note = pick_data_dir(args.source_cache)
    jdk = find_jdk() if not args.no_jdk else None
    planned = {
        "base": APP_DIST,
        "data": data_dir,
        "catalog": (data_dir / "catalog") if data_dir else None,
        "gradle": (data_dir / "gradle") if data_dir else None,
        "jdk": jdk,
        "min_size": args.min_size_mb,
        "split": args.split_mb,
    }
    log("构建计划：")
    for key in ("base", "data", "catalog", "gradle", "jdk"):
        value = planned[key]
        if value is None:
            log(f"  {key}: 无")
        elif isinstance(value, Path):
            log(f"  {key}: {value}（{human(dir_size(value))}）")
        else:
            log(f"  {key}: {value}")
    if args.dry_run:
        if data_dir is not None:
            log(f"预计 Gradle 缓存：{human(dir_size(data_dir / 'gradle'))}")
            log(f"预计镜像缓存：{human(dir_size(data_dir / 'mirrors'))}")
        return

    prepare_base(args.build)
    stats = assemble(
        data_dir,
        with_jdk=not args.no_jdk,
        with_gradle=not args.no_gradle,
        reuse=bool(args.reuse),
    )
    catalog_dir = FULL_DIST / "data" / "catalog"
    if args.sync_catalog:
        sync_catalog(catalog_dir)
    if args.docs_mc:
        download_docs(catalog_dir, args.docs_mc.strip())
    stats = distribution_stats()
    raw = dir_size(FULL_DIST)
    log(f"全量目录体积：{human(raw)}")

    min_size = int(args.min_size_mb) * 1024**2
    if raw < min_size and not args.allow_small:
        raise SystemExit(
            f"体积 {human(raw)} 低于目标 {human(min_size)}。"
            "请用 --source-cache 指定完整的 data 目录，"
            "或加 --allow-small 继续打包。"
        )

    groups = group_files()
    for name, items in groups.items():
        log(f"  {name}: {len(items)} 个文件 / {human(sum(i.stat().st_size for i in items))}")
    parts = package(groups, int(args.split_mb) * 1024**2)
    manifest = write_manifest(parts, stats, min_size)
    total = sum(item["size"] for item in parts)
    log(f"完成：{len(parts)} 个分卷 / 压缩后 {human(total)} / 解压后 {human(raw)}")
    log(f"清单：{manifest}")


if __name__ == "__main__":
    main()