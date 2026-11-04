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
    "修复加速器 / 代理环境下 Gradle 下载失败：遇到 PKIX path building failed 时自动改用 Windows 证书库重试，不再卡在「Downloading gradle-x.y-bin.zip」。",
    "编译运行页新增「使用 Windows 证书库」开关，手动勾选后所有 Gradle 任务都用系统证书库，设置会记住。",
    "证书错误会在日志里说明原因（Watt Toolkit / SteamTools 加速、公司代理、杀毒软件 HTTPS 扫描），并给出仍然失败时的处理办法。",
    "编译运行页新增「让助手补全配置」与「生成 Wrapper」：按版本和加载器生成构建脚本，或写入内置 Gradle 8.10.2 Wrapper。",
    "编译运行页：一键执行 gradlew build / runClient / runServer / runDatagen，日志实时显示，可停止、复制日志、打开 build/libs。",
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
