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

from dma.version import APP_EXE, APP_NAME, APP_VERSION, SITE_URL  # noqa: E402

DIST = ROOT / "dist"
BUILD = ROOT / "build"
APP_DIST = DIST / "DarkittenModAgent"
ZIP_NAME = f"DarkittenModAgent-{APP_VERSION}.zip"
NOTES = [
    "后台客户端改用 Windows 独立桌面：CreateDesktopW 建桌面，自写 CreateProcessW 传 lpDesktop，游戏从创建第一个窗口起就不在用户桌面出现。",
    "后台启动追加 --no-daemon，避免复用用户桌面的 Gradle daemon；停止时 CloseDesktop 释放桌面句柄。整轮 16933 次窗口采样可见窗口为 0，此前 23-56ms 的闪现已消除。",
    "默认创造模式：server.properties 强制创造，假玩家与客户端探针默认创造；后台冒烟 6/6 通过（出生、行走 2.0 格、放置、挖掘、僵尸伤害 20.0 → 19.81）。",
    "新增 game_bot_playtest / game_playtest / game_window 三个工具，可在完全不开客户端的情况下验证人物操作，也可查询或隐藏客户端窗口；工具总数 115。",
    "新增模组模型库：统一扫描实体、方块、物品、群系、维度，展示 3D 模型、贴图与描述；用户的描述、自定义字段、替换模型和贴图保存在 .dma/mod_library.json，不覆盖扫描结果。",
    "新增手动模型编辑器：支持 Blockbench .bbmodel、GeckoLib/Bedrock .geo.json 与 Java 模型 JSON，可改立方体位置、尺寸、UV 并写回原文件。",
    "新增手动绘图页：像素画布 + UV 图集 + 3D 预览，铅笔、橡皮、油漆桶、取色、直线、画笔大小、撤销/重做与色板；可点模型面反查 UV 上色。",
    "实体绘图带原版 64x64 人形 UV 分区提示，方块提供等距三面预览；绘图与模型编辑都不依赖第三方绘图库。",
    "回归测试覆盖模型库扫描与持久化、模型编辑器、绘图工具、后台隐藏桌面启动与假玩家冒烟；全量 594 项测试通过。",
    "继续保持在线优先：JDK、Gradle、版本目录、MDK、sources 与参考模组全部按需从官方源或国内镜像拉取，发行包不内置这些内容。",
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
            f"{ROOT / 'dma' / 'resources'}{os.pathsep}dma/resources",
            "--add-data",
            f"{version_file}{os.pathsep}.",
            "--collect-submodules",
            "pypdf",
            "--collect-submodules",
            "dma",
            "--exclude-module",
            "numpy",
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


def write_bootstrap() -> Path:
    """在线引导清单：程序本体保持轻量，资源全部从网站与镜像按需拉取。"""
    from dma import resources

    payload = resources.bootstrap_manifest()
    payload["download_page"] = SITE_URL
    target = DIST / "mod_agent_bootstrap.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def write_manifest(archive: Path) -> Path:
    size = archive.stat().st_size
    from dma import resources

    # 下载直链指向站点本体：站点会随发布一起更新，不会出现
    # GitHub Release 尚未创建时按钮 404 的情况。
    manifest = {
        "name": APP_NAME,
        "version": APP_VERSION,
        "released": date.today().isoformat(),
        "kind": "online",
        "policy": "online-first",
        "download_url": SITE_URL + archive.name,
        "page_url": SITE_URL,
        "bootstrap_url": SITE_URL + "mod_agent_bootstrap.json",
        "sha256": sha256(archive),
        "size": size,
        "bundled": [],
        "online_resources": resources.bootstrap_manifest()["resources"],
        "notes": NOTES,
    }
    target = DIST / "mod_agent_release.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


def sync_source() -> None:
    """Push source + changelog after a successful build (non-fatal)."""
    if os.environ.get("DMA_NO_SYNC") == "1":
        print("source sync: skipped (DMA_NO_SYNC=1)")
        return
    script = ROOT / "tools" / "sync_github.py"
    if not script.is_file():
        return
    print()
    print("source sync: pushing to GitHub…")
    result = subprocess.run(
        [sys.executable, str(script), "--quiet"],
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        # 源码同步失败不影响已经构建好的发行包。
        print(f"source sync: failed (exit {result.returncode}); build is still valid")


def main() -> None:
    for folder in (BUILD, DIST):
        if folder.exists():
            shutil.rmtree(folder)
    run_pyinstaller()
    archive = make_zip()
    manifest = write_manifest(archive)
    bootstrap = write_bootstrap()
    print()
    print("built:", APP_DIST)
    print("zip:", archive)
    print("size:", archive.stat().st_size)
    print("sha256:", sha256(archive))
    print("manifest:", manifest)
    print("bootstrap:", bootstrap)
    sync_source()


if __name__ == "__main__":
    main()
