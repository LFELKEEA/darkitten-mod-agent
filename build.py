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
    "导入 .jar 时自动用内置 Vineflower 1.12.0 反编译出 Java 源码，助手可以直接检索方法体、调用关系和字段读写。",
    "同时保留元数据、语言文件、资源 JSON 和类结构摘要；即使反编译部分失败，也能靠结构定位类和 API。",
    "反编译需要本机 JDK 17 或更高；没有 JDK 时会先提示，仍然可以只导入结构，到「环境」页装好 JDK 后重新导入即可生成源码。",
    "反编译在本机执行，不上传 JAR 或源码；单次最长 180 秒，超时或个别类失败不会中断整次导入。",
    "Fabric 模组里的 class_1234 / method_5678 是 intermediary 名称，不是反编译失败；需要可读名称时对照工程当前的 Yarn / Mojmap 映射。",
    "单个 JAR 最多 500 个类、400 个文本条目，反编译源码最多 70 万字符；结构和源码共享 150 万字符上限，超出会截断并注明。",
    "内嵌在 META-INF/jars 下的依赖库、签名文件、图片音频等二进制内容会跳过；损坏的 .class 也不会影响其余内容。",
    "导入对话框保留「模组 JAR (*.jar)」筛选，原有文档和代码文件继续支持。",
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
