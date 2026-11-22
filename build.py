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
    "完成闸门：本轮写过需要编译的文件后，必须有一次成功的 build 才能收尾；编译未通过时会明确写「未完成：编译未通过」并附结构化错误，不再只靠一句“完成了”结束任务。关闭自动编译时会明确提示尚未验证。",
    "studio_generate 生成后默认直接执行 Gradle build（可传 build=false 关闭），结果里直接带编译错误，并同步记录内核构建状态。",
    "方块贴图：未知样式改为报错并列出可用样式；重写深板岩、黏土、木板、水晶、黑曜石、蘑菇柄等样式，去掉周期性条纹与接近纯黑的问题；validate 增加亮度跨度警告。",
    "物品贴图：修正立体倒角对亮面/暗面的判断，细长物品不再被描边吃掉；纯方块物品改用三面立方体图标；按物品名推断类别，避免把 stone 这类名字误当成方块样式。",
    "实体模型与贴图对齐：人形、四足和自定义 boxes 共用同一张 UV 表，新增四足 UV 布局；图集按原版 64×64 绘制后再缩放到目标尺寸，超出图集的方块不再采样到空白区域。",
    "UV 与导出：导出 Java 模型时按 texture_size 把像素 UV 转成 0–16；实体模型导出到 geo/*.geo.json（GeckoLib），不再错误写成方块模型。",
    "人工自由建模：模型编辑器支持旋转 X/Y/Z、轴心、外扩；3D 预览按旋转后的八个角点渲染、背面剔除和取景；支持镜像 X、阵列复制、新建空白模型；三种格式都能读写旋转/轴心/外扩。",
    "模型库新增「新建空白模型」，可以从零搭模型；空白模式保存前不改动原文件，空列表会阻止保存。",
    "回归测试新增旋转/轴心/外扩往返、镜像与阵列、完成闸门、关闭自动编译提示等用例；全量 631 项测试通过。",
    "继续保持在线优先：JDK、Gradle、版本目录、MDK、sources 与参考模组全部按需从官方源或国内镜像拉取，发行包不内置这些内容。",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


RESOURCE_SKIP_DIRS = (".gradle", "build", "out", "bin", ".git", "__pycache__", "run")


def stage_resources(target: Path) -> Path:
    """复制 dma/resources 时剔除 Gradle 生成目录。

    探针源码目录一旦在本地编译过，就会留下 build/ 与 .gradle/，
    其中的锁文件可能正被系统占用，直接打进包里既会让 PyInstaller
    复制失败，也会白白增大安装包。
    """
    source = ROOT / "dma" / "resources"
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    shutil.copytree(
        source,
        target,
        ignore=shutil.ignore_patterns(*RESOURCE_SKIP_DIRS),
    )
    return target


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
    staged = Path(holder.name) / "resources"
    stage_resources(staged)
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
            f"{staged}{os.pathsep}dma/resources",
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
