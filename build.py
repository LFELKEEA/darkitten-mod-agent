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
    "手动绘图与模型编辑器的 3D 预览改成 Blockbench 式自由视角：左键空白处拖动旋转、中键或右键拖动平移、滚轮以光标为中心缩放，并提供 3/4、正面、侧面、顶面预设与重置视角。",
    "预览按相机角度做背面剔除和深度排序，转到任意角度都能正确看到前后遮挡；相机按包围盒最长边取景，旋转时不忽大忽小。",
    "模型编辑器按部件控制显隐：立方体列表带勾选框，支持「只看选中 / 全部显示」，单独检查结构时自动放大到该部件。",
    "手动绘图页新增「部件」下拉框，可单独显示某个立方体，方便绘制被帽子、外套等外层挡住的皮肤区域。",
    "数据目录兜底：程序目录、%LOCALAPPDATA%、用户目录或临时目录任意一处可写就能启动，不再因为 data/gradle 创建失败直接抛出 PermissionError；全部不可写时给出中文提示。",
    "残留锁立即接管：先用 Qt 判断持锁进程是否存活，进程已退出就直接清理 app.lock；真有实例在运行才等待激活，上限 8 秒。",
    "安装目录、数据目录本身和 _internal 不再被当成工程，即使含有 build.gradle 或 .dma/project.json；data/projects 下的正常工程不受影响。",
    "第二次启动直接叫醒已运行的窗口：改用本地激活服务，不再弹「已经在运行」后退出；确认持锁进程已退出后会自动清理上次崩溃留下的 app.lock。",
    "首次启动先出窗口：工程扫描和模型库同步推迟到首帧绘制之后，源码只扫 src/main/java 等标准目录，不再翻 Gradle 缓存导致长时间白屏。",
    "工程校验更严格：最近工程、打开入口和模型库下拉框只接受带 .dma/project.json、build.gradle 等标记的真正工程；相对路径统一落在 data/projects。",
    "删除工程后模型库立即清空列表、计数与详情，启动时自动剔除已删除或失效的最近工程记录，不再残留上一个工程的内容。",
    "模型库按工程分类：顶部的工程下拉框只在已打开过的工程之间切换，模型、贴图、自定义描述随工程各自的 .dma/mod_library.json 保存，互不串味。",
    "新增在线资源库：LittleSkin 皮肤/披风、Future YSM 社区模型、Modrinth 资源包可以直接搜索、预览并下载到工程 reference/assets 下；Blockbench 社区画廊、MCModels、Planet Minecraft 提供浏览器入口。",
    "在线资源接入 Agent：新增 online_assets 工具，可列来源、按关键词搜索、按 ID 下载；AI 制作模组时能参考社区造型、贴图与资源包结构，工具总数 116。",
    "新增开发端实时画面窗口：可随时打开、暂停、保存截图；勾选操控后把鼠标与键盘转发到隐藏桌面上的客户端，游戏本身仍然完全无窗口。",
    "画面通过本地探针的 /frame 端点读取，不落盘；后台客户端继续跑在独立隐藏桌面，不抢占用户鼠标与焦点。",
    "默认创造模式：server.properties 强制创造，假玩家与客户端探针默认创造；后台冒烟全部通过（出生、行走、放置、挖掘、僵尸伤害）。",
    "手动模型编辑器支持 Blockbench .bbmodel、GeckoLib/Bedrock .geo.json 与 Java 模型 JSON；手动绘图页支持像素画布、UV 图集与 3D 预览。",
    "回归测试覆盖单实例启动、数据目录回退、工程标记校验、最近记录清理、源码扫描范围、模型库清空、在线资源搜索与下载、3D 相机与部件显隐；全量 622 项测试通过。",
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
