# Changelog

## 2.0.5 — 2026-11-09

修复模板、运行文件、场景卡片与工具健壮性问题，并补上 CI。

- Gradle Wrapper 配置统一写标准 `https://`，坏的反斜杠 URL 会被识别并重建。
- Fabric 模板补齐 `LICENSE`，`LICENSE` / `gradlew` 也做占位符替换，`{{year}}` 自动填当前年份。
- 启动开发端 / GameTest 前自动写 `eula.txt`；服务端 `server.properties` 固定 `server-port=25566`、`online-mode=false`，保留其他已有配置。
- 构建异常写入 `.mcmod/build-report.json`，不再把异常直接抛到界面。
- 场景卡片 YAML：行内注释正确剥离，`a#b` 保留；非对象入参降级为 warning；`|` / `>` / `&` / `*` 明确报错。
- 长期记忆页接入侧边栏「记忆」。
- 镜像缓存路径拒绝目录逃逸，远端 POM 限制 4MB，SHA-1 / MD5 标记非安全用途。
- 工具参数 `limit` / `depth` / `timeout` 等增加统一校验与可读错误提示。
- 修复重复信号、`list` 注解遮蔽、运行时 `assert`、GitHub 检索 `repo=null`、技能关键词 `1.21.11`、知识库 JDK 文案等问题。
- 新增 GitHub Actions CI（windows-latest + Python 3.12）。

## 2.0.4 — 2026-11-09

修复 1.20.1 NeoForge 工程被错误改写、中文资源编码与镜像元数据问题。

- 构建前识别被改写成 NeoGradle 7（1.20.2+ 才支持）的 1.20.1 构建脚本，自动恢复内置 ModDevGradle legacyforge 骨架，原文件备份到 `.dma/backups`，不再卡在 `net.neoforged:neoforge:47.1.106` 无法解析。
- `processResources` 的 `expand` 固定 `filteringCharset = 'UTF-8'`，中文 Windows 下不再用 GBK 读写资源，`mods.toml` 里的中文不会写成非法 UTF-8。
- 更正 NeoForge 技能文档：1.20.1 使用 `net.neoforged:forge:1.20.1-47.1.106` 与 ModDevGradle legacyforge，NeoGradle 7 与 `net.neoforged:neoforge` 坐标从 1.20.2 起才适用。
- 修复 NeoForge / Fabric 骨架入口类文件名与类名不一致：`ModMain.java` 里的公开类是 `BootstrapMod`，现在生成 `BootstrapMod.java`。
- 合成 `minecraft-dependencies` 变体按该 Minecraft 版本的 `javaVersion` 声明（1.20.1 是 Java 17），不再写死 Java 21 导致 “No matching variant”。
- 镜像合成模块的 `.sha1` 随内容更新，Gradle 校验不一致时不再退回旧缓存。
- `maven-metadata.xml` 的 neoforged / forge / cpw 版本清单仍以 Stellar 为准，普通库在上游不可用时退回缓存副本，避免 “找不到匹配 4.0.+”。

## 2.0.3 — 2026-11-09

修正 NeoForge 1.20.1 工程骨架，并让镜像在“TCP 通、TLS 不通”的网络环境下真正生效。

- 修正 NeoForge 1.20.1 骨架：1.20.1 的 NeoForge 是 Forge 47 的分支，构件坐标是 `net.neoforged:forge:1.20.1-47.1.106`，包名仍是 `net.minecraftforge.*`，`mods.toml` 仍用 `modId="forge"` + `mandatory=true`。原骨架混用了 1.20.2+ 的 `net.neoforged:neoforge` 与 `neoforge.mods.toml`，依赖根本不存在，必然构建失败。
- 构建工具切换到 ModDevGradle `net.neoforged.moddev.legacyforge` 2.0.91，版本通过 `legacyForge { enable { neoForgeVersion = ... } }` 声明；`build.gradle`、`gradle.properties`、`mods.toml`、`ModMain.java` 一并改回 1.20.1 的 Forge API。
- 修复镜像不启用：原来的可达性探测只做 TCP 握手，证书链校验失败、TLS 被重置、网关假连接都会被判成“可达”。现在改为真正请求一次仓库里必定存在的 `maven-metadata.xml`，证书 / 超时 / 5xx 一律视为不可达并切换到本地镜像。
- 构建因网络或证书问题失败、但主动探测认为仓库可达时，自动强制挂载镜像 init script 重试一次，覆盖“探测进程能联网、Gradle 的 JDK 信任库不认中间证书”的情况。
- 网络错误提示补齐 `PKIX path building failed`、`certificate_unknown` 等证书类标记。
- 在线构建的“无输出判定卡死”从 5 分钟放宽到 15 分钟：首次构建跑 NeoFormRuntime 合并与反编译时，单个任务静默十几分钟属于正常现象，不再被误杀。
- 回归测试更新：NeoForge 1.20.1 骨架断言改为 legacyforge + `neoForgeVersion` + Forge API；全量 316 项测试通过。

## 2.0.2 — 2026-11-09

修复工程路径含空格时构建被截断、NeoGradle 工程不走镜像这两个阻断问题。

- Windows 下所有 Gradle 入口（`build_project`、`mcdev_build`、`mcdev_prepare`、GameTest、`genSources`、`verify`）统一改用 `cmd.exe /d /s /c "<完整命令行>"`：`D:\Darkitten Launcher\...` 这类含空格目录不再被截断成 `'D:\Darkitten' 不是内部或外部命令`，附加的 `--init-script` 也不会再把第一个参数当成命令边界。
- 修复 NeoGradle / ModDevGradle 工程拿不到镜像：这些插件在运行时才把 `maven.neoforged.net` 加进仓库，构建脚本里扫不到 URL。现在每次构建都直接探测官方仓库，连不上就自动启用本地镜像代理，不再因为 `preflight` 结果为空而放弃镜像。
- 启用镜像代理时同步放宽 Gradle 的 HTTP 超时（连接 120 秒、读取 300 秒）：上游镜像回首个字节可能超过默认的 30 / 60 秒，不再出现 `Read timed out`。
- 新增回归测试：含空格路径的命令行还原、无仓库 URL 的 NeoGradle 项目自动挂载 init script、镜像模式下的慢速超时配置。

## 2.0.1 — 2026-11-08

修复“运行卡住不动”。

- Gradle 子进程改由独立看门狗监管：取消、总超时、长时间无输出都会立即终止进程树，不再依赖“读到下一行输出”才检查。
- 构建前探测 `build.gradle` / `settings.gradle` 里的 Maven 仓库；不可达时自动启动本地镜像代理，用 Gradle init script 把 `maven.neoforged.net`、`maven.minecraftforge.net`、`maven.fabricmc.net` 的依赖解析转到 bmclapi、华为、腾讯、阿里、Gradle 插件门户等国内源，构建继续在线完成。
- 只有镜像代理也起不来时才退回 `--offline` 快速失败，并在报错里指出具体域名与代理 / VPN / 换加载器的处理方式。
- 构建前检查 Wrapper 里的 Gradle 发行版，缺失时优先从华为 / 腾讯镜像预装；工作室引擎同样在依赖解析前挂载镜像 init script。
- Gradle 每 15 秒输出一次心跳（已用时间、最近输出时间）；离线模式 60 秒、在线模式 5 分钟无输出判定卡死，可用 `DMA_GRADLE_STALL_TIMEOUT` 调整。
- `build_project` 默认超时从 1800 秒降到 900 秒，上限 1800 秒。
- AI 请求增加整体时限（连接 + 读取响应），并支持取消：等待期间点“停止”立即生效，最多 0.25 秒响应一次。
- 等待模型时每 5 秒刷新“正在等待模型响应…已用 N 秒”；发送按钮和停止按钮显示实时耗时。

## 2.0.0 — 2026-11-08

2.0 是验证闭环重构版本，按 176 项改进清单逐条落地。

### 内核

- 新增 `ModSpec` / `Workflow` / `Gate` / `SnapshotStore` / `LoopGuard`，并接入主循环。
- 任务图按“骨架 → 功能 → 静态检查 → 编译 → 运行 → 验收 → 打包”执行；依赖失败会阻断下游。
- 验收门禁区分编译、测试、运行时、静态检查和人工确认；缺少证据时任务标记为 `blocked`，不得标记完成。
- `mcdev_prepare` 默认超时改为 900 秒；只有客户端真正 `joined`（或已在运行）才返回 `ok=true`，`wait_join=false` 不再谎报就绪。
- 构建前自动快照，失败可回滚到最近成功快照；支持 diff、保留策略与 bisect。

### 静态分析与精度

- 新增 176 条规则目录与能力矩阵，覆盖 P0 / P1 / P2 / P3 / TOOL。
- 新增符号索引、映射转译、Mixin / 客户端 / 线程 / 网络 / 注册表 / 数据包 / 资产 / Gradle / 生态 / 版本检查器。
- `edit_file` 支持按 `Class.method` 替换方法体、按类插入成员；`read_file` 支持内容哈希与分段读取，未变化直接跳过。
- 统一文本内容哈希与原始字节哈希，修复 CRLF 文件“未变化”判断失效的问题。

### 工具与资产生成

- 新增资产库（预置方块模板、克隆、导入、提升）、配置界面生成器、兼容矩阵 / GitHub Actions 工作流生成器。
- 新增调试模板生成器：生产默认关闭的 `DebugFlags`、`dump` 子命令、崩溃上下文 `CrashContext`、tick / 同步 / 渲染 / 寻路分层探针与可选 JFR 埋点。
- 新增块模型等距预览 PNG、迁移前存档备份、离线缓存与影响分析工具。
- 原生 function calling 优先，通道不支持时自动回退到文本工具协议。

### 质量

- 新增 2.0 回归测试 31 项；`mod_agent` 全量 294 项测试通过；桌面桌宠 459 项测试通过（跳过 1 项）。

## 1.5.0

- 快速生成、规格驱动引擎、多加载器骨架、开发端探针、场景卡片断言与自动更新。
