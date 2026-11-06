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
    "重做整条做模流程：先规划、再确认、后施工。用户用一句话描述需求后，助手会一次性拆出完整规格（玩法、注册内容、获取方式、数值、美术、验收标准、风险），右侧「规格」页显示 10 项需求清单和说明书，必须点「确认规格并开始施工」才会写代码，避免一上来就生成一堆不合要求的文件。",
    "新增施工状态机与任务清单：确认规格后自动拆成骨架、注册、玩法、纹理、配方、语言、编译、验收等任务，助手每完成一项就调用 complete_task，右侧进度实时前进；编译失败会按真实日志归类为 Java 版本、依赖下载、Mixin、找不到符号、类型不匹配、缺少程序包、依赖变体、Gradle 任务或内存不足，并给出对应处理方式。",
    "新增真实产物巡检：编译通过后直接读取 build/libs 里的 JAR，解析 fabric.mod.json / neoforge.mods.toml、物品与方块模型、blockstates、配方、掉落表、纹理、音效和语言文件，生成「成品说明书」，列出真实注册内容、配方数量、模型纹理数量、AI 推荐玩法和安装步骤，不再由模型凭记忆编造。",
    "新增增量修改模式：工程已有规格和成品后，继续提要求会保留源码、规格、记忆和贴图，只改必要文件；需求变化时用 update_spec 提交差异并保留已确认状态，改完重新编译并刷新成品说明书。支持「版本改为 1.21.1 Fabric；保留已有贴图，只修改合成配方」这类说法。",
    "新建工程增加「运行端」选项（双端 / 客户端 / 服务端），规格与说明书会一并记录；手动点「编译」成功时也会自动生成成品说明书。继续包含 1.1.0 的 3D 建模、绘图、代码工程技能与 pixel_art。",
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
