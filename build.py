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
    "修复 Gradle Wrapper 配置写成反斜杠 URL（https\\\\://）导致发行版地址无法解析：新建与修正 Wrapper 时统一写标准 https://，并校验已有配置的 scheme，坏配置会自动重建。",
    "修复模板生成：Fabric 模板补齐 LICENSE，LICENSE / gradlew 也参与占位符替换，{{year}} 会填成当前年份。",
    "启动开发端与 GameTest 前自动准备运行文件：缺少 eula.txt 时写入 eula=true；服务端 server.properties 固定 server-port=25566、online-mode=false，并保留用户已有配置，不再首次启动即退出或连错端口。",
    "构建异常（Gradle 未找到、进程无法启动等）改为写入 .mcmod/build-report.json 并进入修复循环，不再把异常直接抛到界面。",
    "修复场景卡片 YAML：行内注释会被正确剥离（a#b 这种不带空格的井号保留），注释不再被当命令发给游戏；非对象入参降级为 warning；| / > 多行块与 & / * 锚点明确报错，不再静默解析成错误值。",
    "长期记忆页接入侧边栏「记忆」，可直接查看、编辑、删除和导出记忆，不再只能靠对话写入。",
    "镜像缓存路径加固：绝对路径与 ../ 不能再逃出缓存目录；远端 POM 解析前限制 4MB；SHA-1 / MD5 标记为非安全用途，消除安全扫描告警。",
    "工具入参统一校验：limit / depth / timeout 等参数传了非数字时返回「参数 limit 必须是整数」这类可读提示，不再变成 invalid literal for int() 的 traceback。",
    "修复重复信号与注解遮蔽：对话框按钮不再重复触发 accept；SkillStore / MemoryStore 的 list 注解改用 builtins.list，类型检查与 get_type_hints 不再报错；运行时 assert 改为显式异常。",
    "修复若干边界崩溃：GitHub 检索结果 repo/path 为 null 时不再 AttributeError；技能关键词去掉误写的 1.21.11；知识库缺 JDK 提示改为 JDK 17 或更高。",
    "新增 GitHub Actions CI（windows-latest + Python 3.12），每次推送自动跑全量单元测试，避免干净环境才暴露的问题。",
    "修复 1.20.1 NeoForge 工程卡在 net.neoforged:neoforge:47.1.106 无法解析：构建前识别被改写成 NeoGradle 7（1.20.2+ 才支持）的构建脚本，自动恢复内置 ModDevGradle legacyforge 骨架，原文件备份到 .dma/backups，镜像不再为不存在的坐标背锅。",
    "修复 mods.toml 里的中文被写成非法 UTF-8：processResources 的 expand 现在固定 filteringCharset = 'UTF-8'，中文 Windows 下不会再用 GBK 读写资源，游戏加载时不再解析失败。",
    "更正 NeoForge 技能文档：1.20.1 使用 net.neoforged:forge:1.20.1-47.1.106 与 ModDevGradle legacyforge，NeoGradle 7 与 net.neoforged:neoforge 坐标从 1.20.2 起才适用。",
    "修复 NeoForge / Fabric 骨架入口类文件名与类名不一致：ModMain.java 里的公开类是 BootstrapMod，现在会生成 BootstrapMod.java，javac 不再报“类 X 是公共的，应在 X.java 中声明”。",
    "修复 NeoForge 1.20.1 构建报“No matching variant”：合成的 minecraft-dependencies 变体改为按该 Minecraft 版本的 javaVersion 声明（1.20.1 是 Java 17），不再写死 Java 21。",
    "修复镜像合成模块的 .sha1 未随内容更新：Gradle 校验不一致时会退回旧缓存，升级后仍然拿到过期元数据；现在按新内容返回并缓存正确的 SHA-1。",
    "修复 maven-metadata.xml 取到不完整镜像列表：neoforged / forge / cpw 的版本清单仍以 Stellar 为准，普通库在上游不可用时退回缓存副本，避免“找不到匹配 4.0.+”。",
    "修正 NeoForge 1.20.1 工程骨架：改用 ModDevGradle legacyforge 2.0.91 + net.neoforged:forge:1.20.1-47.1.106，恢复 Forge 包名、forge mods.toml 与 Forge 事件总线，不再引用 1.20.2+ 才有的 net.neoforged:neoforge。",
    "修复“TCP 能连、TLS 不通”时镜像不启用：仓库探测改为请求真实存在的 maven-metadata.xml，证书错误、TLS 重置、超时和 5xx 都按不可达处理。",
    "构建因证书或网络问题失败时会强制挂载镜像 init script 重试一次，覆盖 Gradle 使用的 JDK 信任库不认中间证书的情况。",
    "修复工程路径含空格时 Gradle 命令被截断：构建、启动开发端、GameTest、反编译统一用 cmd /d /s /c 传递完整命令行，D:\\Darkitten Launcher\\... 这类目录不再报“'D:\\Darkitten' 不是内部或外部命令”。",
    "修复 NeoGradle / ModDevGradle 工程不走镜像：插件在运行时才添加 maven.neoforged.net，构建脚本里扫不到地址；现在每次构建都探测官方仓库，连不上就自动启用本地镜像代理。",
    "修复构建卡死：Gradle 子进程改由独立看门狗监管，取消、总超时和“长时间无输出”都能立即终止；每 15 秒输出一次心跳，界面不再看起来像死机。",
    "官方 Maven 仓库连不上时自动启用本地镜像代理：通过 Gradle init script 把 maven.neoforged.net、maven.minecraftforge.net、maven.fabricmc.net 的依赖解析转到 bmclapi、华为、腾讯、阿里和 Gradle 插件门户等国内镜像，构建继续在线完成；代理也不可用时才退回 --offline 快速失败。",
    "构建前检查 Wrapper 里的 Gradle 发行版，缺失时优先从华为、腾讯镜像预装，不再卡在 services.gradle.org 的慢速下载；JDK 同样走中科大 / 华为镜像。",
    "模型请求增加总时限与取消支持：服务端接了连接却不返回内容时按超时中断；等待期间每 5 秒显示已用时间，点“停止”能立即生效。",
    "输入框旁显示实时耗时（生成中… Ns / 停止（Ns）），长时间任务可以一眼确认程序还在工作。",
    "2.0 内核接入：需求先落 ModSpec，再生成 DAG 任务图；骨架、功能、静态检查、编译、运行时验证、验收、打包按依赖顺序执行，失败会阻断下游。",
    "验收门禁不再接受“编译通过就算完成”：必须同时具备编译、测试、运行时断言和静态检查证据；缺证据的任务只能标记 blocked。",
    "mcdev_prepare 默认超时改为 900 秒，并且只有客户端真正进入世界（joined）才返回 ok=true；wait_join=false 不再谎报就绪。",
    "新增 176 条静态检查规则（P0/P1/P2/P3/TOOL），覆盖符号索引、映射转译、Mixin、客户端、线程、网络、注册表、数据包、资产、Gradle、生态与版本。",
    "edit_file 支持按 Class.method 替换方法体、按类插入成员；read_file 支持内容哈希与分段读取，未变化时直接跳过，CRLF 文件也能正确判断。",
    "构建前自动快照，失败可回滚到最近成功快照；新增循环保护、影响分析、迁移前存档备份和离线缓存。",
    "新增资产库、配置界面生成器、兼容矩阵 / GitHub Actions 工作流生成器、块模型等距预览；预览与纹理生成的 Pillow 依赖已随包提供。",
    "新增调试模板生成器：生产默认关闭的 DebugFlags、dump 子命令、崩溃上下文 CrashContext、分层性能探针与可选 JFR 埋点。",
    "保留 Forge / NeoForge / Fabric / Architectury 多加载器能力；Forge 1.20.1 仍可用真实开发端探针、场景卡片断言和 GameTest，其他加载器以编译、日志和 GameTest 为准。",
    "Gradle 缓存、发行版、NeoForm 数据和临时文件继续放在程序自己的 data 目录，不默认写 C 盘用户目录。",
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
