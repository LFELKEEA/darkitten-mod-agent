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
    "修复「卡住空转」：agent 反复 list_files / read_file 却从不写文件时，连续 8 次只读会主动打断并要求立即写代码——之前只读工具不断刷新「进展时间戳」，导致 45 分钟无进展保护被架空、max_steps=0 无限步数时陷入只读死循环。",
    "任务拆分层根治「占位冒充实现」：需求里出现「变身/夺舍/换肤/社交聊天/物品框」这类复杂机制时，会为每个机制生成独立任务，验收强制要求产出 mixins.json / mixin 目录 / 渲染器 / Screen+ScreenHandler 等真实现必需文件，并附「可玩性人工确认」提示——不再只按「物品/方块/实体」静态拆任务。",
    "Agent 能力升级：新增「复杂机制实现门槛」硬规则 + 「复杂机制实现映射」技能——做「玩家变身/夺舍/换皮肤/聊天框/物品框/社交关系」这类模组时，不再用「spawn 实体 + 改 customName + 发消息 + 存 List」冒充实现，而是按状态机（状态/触发/数据归属/渲染/控制/退出还原）用 mixin/相机/Screen+ScreenHandler 真正落地，空 mixins.json 视为未实现。",
    "新增内置技能「复杂机制实现映射」(mechanism-playbook)：机制→真实现对照表（变身/夺舍/换肤/对话/物品框/社交）+ 可逆状态机模板 + 技术手段速查 + 红线，做复杂机制模组时自动加载。",
    "修复“系统代理把 Gradle 全挡死”：构建前自动检测 Windows 系统代理（如 Clash）对 services.gradle.org / maven.fabricmc.net 返回 400/REJECT 的情况，检测到就向工程 gradle.properties 注入 systemProp.java.net.useSystemProxies=false 强制直连，并停掉旧 daemon 让下轮构建生效——代理规则把仓库域名挡掉时不再无限重试。",

    "新增 Fabric 1.21.1 内置骨架（对称 NeoForge 1.21.1）：Fabric 1.21.1 工程不再因为没有内置模板而被迫手写构建脚本，loom 1.9 + Java 21 + yarn 1.21.1 生成即用。",
    "修复“做模组反复卡死在下载 Gradle”：构建前自动检查并修复 gradle-wrapper 三件套——gradle-wrapper.jar 被写成占位文本（ClassNotFoundException: GradleWrapperMain）或 distributionUrl 带反斜杠时，直接重写为正确的 wrapper（保留原 Gradle 版本），模型手写的坏 wrapper 不再导致无限重试。",
    "修复“做模组依赖下载全崩”：启动/编译开发端时不再无条件注入 Windows-ROOT 证书信任（JDK 17 读 Windows 证书存储会报 KeyStoreException error 5，SSL 初始化即崩）。现在默认不注入，界面设置里“Gradle 使用 Windows 证书”开关真正生效。",
    "修复“无法停止”：停止构建或停止开发端时，除了杀 gradlew 进程树，现在还会执行 gradlew --stop 停掉后台常驻的 Gradle daemon，不再残留多个 java.exe 继续烧 CPU / 下载。",
    "Agent 规模升级：默认步数 32→128（上限 4000）、上下文轮数 32→128（上限 600）、模型输出 8192→16384 tokens、历史预算 12 万→60 万字符、消息预算 48 万→200 万字符，老配置自动升级且保留用户自定义值，大型模组不再因“达到工具调用轮数上限”卡壳。",
    "自动编译次数从 2 次放宽到 5 次，构建失败回传日志从 24K 扩大到 96K，编译页能看到完整错误而不是被截断的尾部。",
    "循环熔断放宽：连续 5 次同类失败才切换策略、单会话累计 60 次才熔断，编译成功会回补失败预算并自动解除熔断，长模组不再被误杀。",
    "45 分钟无进展保护只统计有效进展：read_file / list_files / search_text 等只读工具与 mcdev_prepare / GameTest 等长任务不再触发误停。",
    "新增 7 个模型工具：texture_template（16×16 剑/镐/斧/锭/宝石模板）、model_json、blockstate_json、lang_json、recipe_json（8 种配方）、loot_table_json、validate_resources，资源全部按 JSON 规范生成并校验引用，不再手写容易出错的资源文件；pixel_art 支持 1-32 倍放大与描边。",
    "新增 8 个大型模组技能：大模组路线图、注册中心、模型与纹理、世界生成、实体与 AI、配方与战利品、进度系统、GUI 与网络，并强化 3D 模型与视觉设计技能。",
    "三个 1.20.1 骨架（Forge/Fabric/NeoForge）自带可编译示例：注册中心 ModRegistries + 示例方块/物品的全套资源（模型、blockstates、纹理、双语 lang、配方、战利品表、挖掘标签）+ MIT LICENSE，生成即跑通。",
    "生成器 epic 规模补全：史诗项目现在真实生成 64+ 内容节点，主题不足时自动补足（社交/黄昏/科技/魔法/维度/农耕），跨界桥连接各主题，接近完整大模组的节奏。",
    "版本矩阵修正：Fabric 26.x 改用 Gradle 9.8（Loom 1.19 要求）；1.21.8 与 26.x 降级为基础骨架标注，不再承诺生成器尚未适配的高级 Java；界面版本列表与版本种子表对齐。",
    "GameTest 工程没有测试用例时直接跳过并标记通过，不再把“没有跑过测试”误报为失败；验证日志排除 jarjar/metadata 等良性提示行；反编译 genSources 与构建一致走镜像与 Forge 参数。",
    "资源生成修正：pack.mcmeta 无条件生成（Fabric 也需要）；流体方块模型使用 _still 纹理渲染；needs_iron_tool 标签只给矿物与机器，农作物/装饰不再被要求铁镐。",
    "维度钥匙修正：只有传送门激活器会生成 RealmKeyItem，遗迹钥匙不再被误做成跨维度传送；没有维度时不再生成空引用。",
    "审计计数修正：warn 级别的问题正确计入统计，不再被静默丢弃。",
    "list_files 支持 offset/limit 分页，大型工程目录不再只看得到前 300 个文件。",
    "修复“开发端无法运行”：启动 runServer / runClient 时也会启用本地 Maven 镜像代理并传入 --init-script。之前只有 build_project 走镜像，界面上的“启动开发端”仍在直连 maven.neoforged.net，返回 502 后卡在 createMinecraftArtifacts。",
    "启动阶段遇到依赖下载/证书错误时自动强制切换镜像并换端口重试一次；镜像模式下同步放宽 Gradle 的 HTTP 超时，慢速上游不会在 30 秒处断掉。",
    "启动开发端的默认等待时间从 180 秒提高到 600 秒，NeoForge 首次生成 Minecraft artifacts 不会再被 3 分钟超时误杀。",
    "工程第一次启动、run/ 与 run-server/ 都不存在时，运行目录改用 run/（ModDevGradle / Loom / Forge MDK 默认），不再把 eula.txt 写进游戏不读的目录导致首次启动直接退出。",
    "修复“一直在运行 runServer”：build_project 不再接受 runServer / runClient，这两个常驻任务要改用 mcdev_prepare 启动，避免空等到编译超时后杀掉开发端。",
    "没有游戏内探针的加载器不再等待 39001/39002 探针端口：改从 Gradle 输出识别 Done (…) / For help, type / Backend library 等启动标记；超时但进程仍存活时按已启动处理，不再误杀正常服务端。",
    "服务端运行目录修正：同时存在 run/ 和 run-server/ 时优先用 run/（ModDevGradle 默认目录），eula.txt 和 server-port 不再写到游戏不读的地方。",
    "启动服务端前检查实际 server-port 是否被占用；残留服务端占着端口时直接返回 alreadyRunning，同一会话重复调用也复用已跟踪进程，不再叠加多份 runServer。",
    "修复构建卡在 Gradle 自动下载 JDK：现在会把本机所有 JDK 通过 org.gradle.java.installations.paths 告诉 Gradle，D:\\developer\\Java\\jdk-21 这类自定义目录也能被识别，不再只看到 JAVA_HOME 里的 JDK 17。",
    "关闭 Gradle 的 JDK 自动下载：缺 JDK 时立刻给出“工程要求 JDK X、本机有 Y”的可读错误，不再卡在 Adoptium 的 TLS 握手或 5 分钟一次的读取超时上。",
    "构建前预检工程声明的 JavaLanguageVersion.of(N) / jvmToolchain(N)，版本不匹配直接提示安装对应 JDK，不再启动注定失败的构建。",
    "修复生成任务跑几十分钟不结束：所有 HTTP 请求改为带硬截止时间的工作线程，TLS 握手卡住时最多等 30 秒就会返回，不再无限停在 do_handshake。",
    "新增网络黑洞记忆：某个域名超时后 10 分钟内直接快速失败，不再对同一个地址反复重试；jsDelivr 与 raw.githubusercontent 链接自动改走 api.github.com。",
    "JDK、Gradle、更新包和 Studio 下载也接入同一套超时与取消机制，准备环境或下载资源时不会再永久卡在“准备中”。",
    "停止按钮真正生效：取消会传进网络请求和工具执行；点一次开始停止，再点一次可放弃等待并恢复输入。",
    "新增 20 分钟无进展保护：长时间没有写入文件或完成编译时自动停止并提示，不再空转。",
    "长任务上下文折叠补上模型输出：反复贴完整文件的回显会被折叠，避免上下文无限增长导致请求变慢或超限。",
    "修复编译/运行页从后台线程直接刷新控件的问题，进程退出回调改由 Qt 信号回到主线程。",
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
