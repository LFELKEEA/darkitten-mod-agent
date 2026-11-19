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
    "修复停止功能：取消信号已贯通开发端启动、探针安装、进服握手、游戏测试步骤与存档等待；取消后自动清理本次任务新启动的开发端和 Gradle daemon。",
    "对话运行期间不再整体禁用运行面板：只锁启动按钮，停止全部与刷新状态始终可用；放弃等待时后台清理本次启动的进程。",
    "新增 design_studio 多方案设计：生物/物品图标/方块一次生成 2-8 套候选，可预览、补描述重新生成、保留模型只改图案，或上传自己的 PNG / JSON / bbmodel。",
    "选定方案后自动写入贴图、物品/方块模型与方块状态；实体输出 GeckoLib geo.json 起始模型并返回写入路径。生成全程离线，无第三方绘图依赖。工具总数 102。",
    "修正测试环境难度：game_setup / game_new_world / game_test 的默认难度由 peaceful 改为 normal，避免新建存档后根本不生成敌对生物；显式传 peaceful 仍会返回提示。",
    "新增 game_control 动作 difficulty / gamemode / time / weather / spawn；spawn 在 peaceful 下自动把难度提到 normal，并在玩家周围生成实体后回读附近实体确认。",
    "game_test 新增 difficulty / gamemode / time / weather / spawn 步骤，可直接编写“夜晚 + 生存 + 生成僵尸 + 攻击 + 截图”的敌对生物测试。",
    "AI 接管游戏：新增 game_setup / game_control / game_observe / game_test 等 6 个工具，可换中文、新建存档、控制视角/移动/按键/攻击/使用/挖掘/聊天/命令、按文字点击，并在每次操作后截图做画面体检。",
    "Forge 1.20.1 探针新增语言、存档、玩家状态/移动/按键/攻击、聊天、界面文字点击与输入等端点；存档用原版 WorldOpenFlows.createFreshLevel，支持普通/超平坦/放大/大型生物群系/单一生物群系。",
    "跨加载器兜底：客户端启动前自动把 options.txt 的 lang 设为 zh_cn；没有探针的加载器可按窗口截图看画面，但控制与断言仍只在 Forge 1.20.1 可用。工具总数 101。",
    "Fabric sources 兜底：本地版本目录为空时直接向 Fabric meta 取当前稳定加载器版本，dev_docs / api_lookup 首次即可下载 sources.jar 并建索引（已用 0.87 MB 真实源码验证：233 类 / 966 方法）。",
    "源码自动同步：build.py 打包成功后调用 tools/sync_github.py 推送源码、README 与 CHANGELOG；同步前拒绝本地配置与运行数据并扫描密钥，支持 --dry-run / --no-push / DMA_NO_SYNC=1。",
    "新增确定性任务图工具：workflow_plan / status / next / complete / fail / skip / reopen。任务状态由 .dma/workflow.json 推进，workflow_complete 先跑验收门禁，缺证据会返回阻断项。",
    "新增公共契约冻结与漂移审计（dma/kernel/contract.py + contract_freeze / contract_check）：重构前冻结 public/protected 类、方法、字段与资源，重构后报告删除、签名变更和资源缺失。",
    "新增离线 API 签名索引（dma/apiindex.py + api_lookup）：从 sources.jar 建索引并缓存，直接查类、方法参数、返回类型与字段类型，不再每次扫描整包。",
    "需求变化用 workflow_plan 合并进 .dma/spec.json 并重建 DAG，保留已完成状态；查 API 优先 api_lookup，需要实现细节再 dev_docs lookup；工具总数 95。",
    "版本列表修复：开发中心的 Minecraft / 加载器全部改为点选下拉，滚轮不再误改；版本清单优先在线获取并合并本地目录，不再截断。",
    "快速生成按所选加载器在线拉取完整可用版本（Fabric / Forge / NeoForge 各自的真实清单），切换加载器自动重载；离线时退回完整内置版本表。",
    "新增版本事实层（dma/modfacts.py）：把 Java 版本、元数据文件名、数据目录单复数、物品定义、映射与 ID API、构建插件固化成可查询数据；新增 version_facts 与 project_fact_check 两个工具，并把冲突接入 lint_project。",
    "跨版本专项修正：1.21+ 数据目录改单数、1.21.4+ 物品定义 assets/<ns>/items、NeoForge 1.20.5+ 用 neoforge.mods.toml、26.x 未混淆与非 remap、1.21.9+/26.x 使用 Identifier.fromNamespaceAndPath，均可在写代码前自动核对。",
    "新增大型模组架构参考（dma/archref.py + arch_ref 工具）：提炼 Create、GregTech Modern、Mekanism、Botania 的注册分层、材料体系、RecipeMap、多方块、能力 API、配置与游戏内文档模式，支持按玩法检索。",
    "新增多版本/多加载器方案（dma/multiversion.py + multiversion_plan 工具）：在 Stonecutter、Architectury、MultiLoader、Modstitch、Stonecraft 之间自动选型，输出目录骨架、官方插件坐标、迁移步骤与验证顺序。",
    "强化调试闭环：崩溃分类新增 Java class 版本、映射改名、Access Widener/Transformer、元数据文件名、数据生成与配方错误；编译提示可识别 17/21/25 的 class 文件版本并在日志里直接给出对应 Java。",
    "新增 6 个内置技能：version-facts、multiversion、access-widener-transformer、gametest、architecture-patterns、publishing-ci；系统提示新增强制顺序：先 version_facts/project_fact_check，再 dev_docs/mod_reference/arch_ref，最后动手。",
    "大型模组计划新增 facts 阶段，reference 阶段加入 arch_ref，spec 阶段加入 multiversion_plan；工具总数 85。",
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
