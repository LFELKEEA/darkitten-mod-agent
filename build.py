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
    "新增真正的 Fabric → NeoForge 移植流水线：port_from_jar 直接扫描 JAR、转换 1.21.4+ 的 items/ 模型、反编译参考源码、生成 Gradle / mods.toml / 规格 / 验收清单，并把工程自动切换过去。用 PoHypSkin（123 个源类、6 个道具）实测，生成的工程 verify 0 错误、Gradle BUILD SUCCESSFUL。",
    "MCA Reborn 兼容不再靠猜：自动识别「皮物 + 催眠手机 / 皮化之刃」或代码里的 net.conczin.mca，自动复制 NeoForge 版 MCA 依赖；生成完整的 McaCompat、SkinData、三种道具类、客户端皮肤缓存 / Mixin、服务端事件。没有在 fabric.mod.json 声明 MCA 的生成器模组也会被识别。",
    "修复道具对 MCA 村民完全没反应：MCA 村民的 mobInteract 会先消费右键（打开对话 / 交易），物品自己的 interactLivingEntity 根本不会被调用。现在通过 PlayerInteractEvent.EntityInteract 抢先处理并取消事件，催眠手机、皮化之刃对村民和对玩家使用同一套逻辑；verify_mod 会强制检查这个兜底。",
    "附身 / 穿皮真正同步身份：备份玩家原 PlayerSaveData，把村民完整身体 NBT 写进 setEntityData，并把姓名、性别、职业、配偶、父母、子女写进 FamilyTree；解除时原样恢复并放回村民本体。穿皮后可以代替该村民与其他人结婚、生育；皮物离开头盔栏或玩家退出时自动还原，避免永久锁死身份。",
    "修复道具贴图与编译按钮：1.21.1 物品模型、纹理、语言、配方在移植时逐项校验，缺模型会直接报 item_model_missing；编译页新增「选择工程」，打开磁盘上已有工程后编译 / 开发客户端 / 开发服务端 / 数据生成按钮立即可用，不再卡在未选择工程。修正提示词与技能里把 toVillager 当村民判定的错误建议。",
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
