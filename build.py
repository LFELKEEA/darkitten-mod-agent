from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dma.version import APP_EXE, APP_NAME, APP_VERSION  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIST = DIST / "DarkittenModAgent"
ZIP_NAME = f"DarkittenModAgent-{APP_VERSION}.zip"
NOTES = [
    "镜像代理不再逐个源排队等超时：所有可用源同时发起请求，谁先返回有效数据就用谁，慢源和死源不再拖住整个下载。源的速度按实测结果动态排序，连续失败的源会短暂降权，恢复后再放回来。",
    "JDK、Gradle 发行包和更新包这类大文件支持多连接分段下载：文件不小于 8 MB 时自动开 6 条 Range 连接并行拉取，断线后按已下载的 .part 续传，合并后校验大小与 SHA-256。",
    "自动更新走 gh-proxy 加速下载 GitHub Release 资源，实测从 65–92 KB/s 提升到 1 MB/s 以上，仍然逐字节校验 SHA-256，校验不过直接删除重下。",
    "Gradle 发行包增加腾讯云镜像兜底，JDK 增加华为云 OpenJDK 兜底；镜像不可用时自动退回 Gradle 官方和 Adoptium 官方地址。",
    "Mojang 版本清单、版本 JSON 和 libraries.minecraft.net 依赖增加 BMCLAPI 兜底，piston-meta 连不上时构建不再卡在元数据阶段。",
    "同一个文件从 A 源下到一半失败时保留 .part，换 B 源继续下，不再从头再来；只有确认 404 才会记成缺失。",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_pyinstaller() -> None:
    icon = ROOT / "assets" / "dma.ico"
    if not icon.is_file():
        subprocess.run(
            [sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True
        )
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--onedir",
        "--name",
        "DarkittenModAgent",
        "--icon",
        str(icon),
        "--add-data",
        f"{ROOT / 'assets'}{os.pathsep}assets",
        "--collect-submodules",
        "pypdf",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "PIL",
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        str(ROOT / "main.py"),
    ]
    print("running:", " ".join(command))
    subprocess.run(command, check=True, cwd=str(ROOT))


def make_zip() -> Path:
    exe = APP_DIST / APP_EXE
    if not exe.is_file():
        raise SystemExit(f"missing {exe}")
    target = DIST / ZIP_NAME
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(APP_DIST.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(APP_DIST).as_posix())
    return target


def write_manifest(archive: Path) -> Path:
    size = archive.stat().st_size
    tag = f"mod-agent-v{APP_VERSION}"
    manifest = {
        "name": APP_NAME,
        "version": APP_VERSION,
        "released": date.today().isoformat(),
        "download_url": (
            f"https://github.com/LFELKEEA/darkitten-mod-agent/releases/download/"
            f"{tag}/{archive.name}"
        ),
        "page_url": "https://lfelkeea.github.io/darkitten-launcher/",
        "sha256": sha256(archive),
        "size": size,
        "notes": NOTES,
    }
    target = DIST / "mod_agent_release.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def main() -> None:
    for folder in (BUILD, DIST):
        if folder.exists():
            shutil.rmtree(folder)
    run_pyinstaller()
    archive = make_zip()
    manifest = write_manifest(archive)
    print()
    print("built:", APP_DIST)
    print("zip:", archive)
    print("size:", archive.stat().st_size)
    print("sha256:", sha256(archive))
    print("manifest:", manifest)


if __name__ == "__main__":
    main()
