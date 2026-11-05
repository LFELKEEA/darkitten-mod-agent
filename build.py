from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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
    "对话里的助手现在可以自己编译：新增 build_project 工具，直接执行 build / runClient / runServer / runDatagen，并把 Gradle 日志和产物大小回传给模型，不再要求用户手动运行 gradlew 或粘贴日志。",
    "编译前自动准备 JDK、Wrapper、libs 本地依赖和 Maven 镜像代理；缺少本地 jar 或 Gradle 版本不够时，会自动补齐并重试一次。",
    "助手根据失败日志继续改代码并再次编译，失败信息会保留末尾关键日志，方便定位到具体文件和行。",
    "状态栏会显示正在执行的 Gradle 任务，长时间构建可以随时取消。",
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
    # 把版本号打进程序目录。启动器读取的是程序自己的版本文件，
    # 这样工坊在程序内更新过，启动器也不会再显示安装时写下的旧版本。
    holder = tempfile.TemporaryDirectory(prefix="dma-version-")
    version_file = Path(holder.name) / "version.json"
    version_file.write_text(
        json.dumps({"version": APP_VERSION}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    try:
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
            "--add-data",
            f"{version_file}{os.pathsep}.",
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
    finally:
        holder.cleanup()


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
