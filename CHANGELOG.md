# Changelog

## 3.0.5 — 2026-11-13

阻止复杂机制模组用“实体 + 改名 + 发消息”冒充实现，任务拆分、Agent 硬规则与技能三处同时收紧。

- 任务拆分层根治“占位冒充实现”：需求里出现“变身 / 夺舍 / 换肤 / 社交聊天 / 物品框”这类复杂机制时，为每个机制生成独立任务，验收强制要求产出 `mixins.json` / mixin 目录 / 渲染器 / `Screen` + `ScreenHandler` 等真实现必需文件，并附「可玩性人工确认」提示，不再只按“物品 / 方块 / 实体”静态拆任务。
- 新增「复杂机制实现门槛」硬规则与「复杂机制实现映射」技能（mechanism-playbook）：按状态机（状态 / 触发 / 数据归属 / 渲染 / 控制 / 退出还原）用 mixin、相机、`Screen` + `ScreenHandler` 真正落地，空的 `mixins.json` 视为未实现；技能内置机制→真实现对照表、可逆状态机模板、技术手段速查与红线，做复杂机制模组时自动加载。

## 3.0.3 — 2026-11-12

修掉环境层面的三类“下载即失败”：系统代理拦截仓库域名、Gradle Wrapper 被写成坏文件、Fabric 1.21.1 缺少内置骨架。

- 修复“系统代理把 Gradle 全挡死”：构建前自动检测 Windows 系统代理（如 Clash）对 `services.gradle.org` / `maven.fabricmc.net` 返回 400/REJECT 的情况，检测到就向工程 `gradle.properties` 注入 `systemProp.java.net.useSystemProxies=false` 强制直连，并停掉旧 daemon 让下一轮构建生效，代理规则挡掉仓库域名时不再无限重试。
- 新增 Fabric 1.21.1 内置骨架（对称 NeoForge 1.21.1）：不再因为缺少内置模板而被迫手写构建脚本，Loom 1.9 + Java 21 + yarn 1.21.1 生成即用。
- 修复“反复卡死在下载 Gradle”：构建前自动检查并修复 gradle-wrapper 三件套——`gradle-wrapper.jar` 被写成占位文本（`ClassNotFoundException: GradleWrapperMain`）或 `distributionUrl` 带反斜杠时，直接重写为正确的 wrapper（保留原 Gradle 版本），不再无限重试。

## 3.0.1 — 2026-11-12

3.0 是围绕大模组与长时间运行的规模版本：放宽 Agent 的步数与上下文预算，补齐资源生成工具和模组技能，并修掉停止、证书与长任务误判这几类会中断生成的问题。

- 修复“做模组依赖下载全崩”：启动/编译开发端时不再无条件注入 Windows-ROOT 证书信任（JDK 17 读 Windows 证书存储会报 `KeyStoreException error 5`，SSL 初始化即崩）。现在默认不注入，界面设置里“Gradle 使用 Windows 证书”开关真正生效。
- 修复“无法停止”：停止构建或停止开发端时，除了杀 `gradlew` 进程树，现在还会执行 `gradlew --stop` 停掉后台常驻的 Gradle daemon，不再残留多个 `java.exe` 继续烧 CPU / 下载。
- Agent 规模升级：默认步数 32→128（上限 4000）、上下文轮数 32→128（上限 600）、模型输出 8192→16384 tokens、历史预算 12 万→60 万字符、消息预算 48 万→200 万字符。老配置自动升级且保留用户自定义值。
- 自动编译次数从 2 次放宽到 5 次，构建失败回传日志从 24K 扩大到 96K，编译页能看到完整错误而不是被截断的尾部。
- 循环熔断放宽：连续 5 次同类失败才切换策略、单会话累计 60 次才熔断；编译成功会回补失败预算并自动解除熔断。
- 45 分钟无进展保护只统计有效进展：`read_file` / `list_files` / `search_text` 等只读工具与 `mcdev_prepare` / GameTest 等长任务不再触发误停。
- 新增 7 个模型工具：`texture_template`（16×16 剑/镐/斧/锭/宝石模板）、`model_json`、`blockstate_json`、`lang_json`、`recipe_json`（8 种配方）、`loot_table_json`、`validate_resources`；`pixel_art` 支持 1-32 倍放大与描边。
- 新增 8 个大型模组技能：大模组路线图、注册中心、模型与纹理、世界生成、实体与 AI、配方与战利品、进度系统、GUI 与网络。
- 三个 1.20.1 骨架（Forge / Fabric / NeoForge）自带可编译示例：注册中心 `ModRegistries` + 示例方块/物品的全套资源（模型、blockstates、纹理、双语 lang、配方、战利品表、挖掘标签）+ MIT LICENSE，生成即跑通。
- 生成器 epic 规模补全：史诗项目现在真实生成 64+ 内容节点，主题不足时自动补足（社交/黄昏/科技/魔法/维度/农耕），跨界桥连接各主题。
- 版本矩阵修正：Fabric 26.x 改用 Gradle 9.8（Loom 1.19 要求）；1.21.8 与 26.x 降级为基础骨架标注，不再承诺生成器尚未适配的高级 Java；界面版本列表与版本种子表对齐。
- GameTest 工程没有测试用例时直接跳过并标记通过，不再把“没有跑过测试”误报为失败；验证日志排除 jarjar/metadata 等良性提示行；反编译 `genSources` 与构建一致走镜像与 Forge 参数。
- 资源生成修正：`pack.mcmeta` 无条件生成（Fabric 也需要）；流体方块模型使用 `_still` 纹理渲染；`needs_iron_tool` 标签只给矿物与机器，农作物/装饰不再被要求铁镐。
- 维度钥匙修正：只有传送门激活器会生成 `RealmKeyItem`，遗迹钥匙不再被误做成跨维度传送；没有维度时不再生成空引用。
- 审计计数修正：warn 级别的问题正确计入统计，不再被静默丢弃。
- `list_files` 支持 `offset` / `limit` 分页，大型工程目录不再只看得到前 300 个文件。

## 2.0.9 — 2026-11-11

修复“开发端无法运行”：NeoForge Maven 直连返回 502，`runServer` / `runClient` 卡在 `createMinecraftArtifacts`。

- 启动开发端时也会像编译一样启用本地 Maven 镜像代理并传入 `--init-script`。之前只有 `build_project` 走了镜像，界面上的“启动开发端”仍在直连 `https://maven.neoforged.net/...`，插件动态添加的仓库地址不会被构建脚本里的 repositories 覆盖。
- 启动阶段如果 Gradle 报依赖下载/证书错误，会自动强制切换镜像并换端口重试一次，不再把网络问题直接报成开发端启动失败。
- 镜像模式下同步放宽 Gradle 的 HTTP 超时（`slow=True`），上游慢速镜像吐大文件时不会在 30 秒 socket 超时处断掉。
- 启动开发端的默认等待时间从 180 秒提高到 600 秒。NeoForge 首次运行要生成 Minecraft artifacts，3 分钟不够；界面按钮和 Agent 走同一套默认值。
- 工程第一次启动、`run/` 与 `run-server/` 都不存在时，运行目录按 ModDevGradle / Loom / Forge MDK 的默认值使用 `run/`。之前会写成 `run-server/`，游戏去 `run/` 找不到 `eula.txt` 直接退出。
- 新增回归测试：断言启动命令包含 `--init-script` 和镜像脚本路径。

## 2.0.8 — 2026-11-11

修复“一直在运行 runServer”：服务端其实已经启动，工坊却在等一个不存在的探针，超时后把正常进程杀掉，然后再启动下一轮。

- `build_project` 不再接受 `runServer` / `runClient`。这两个是常驻 Gradle 任务，套在编译超时里只会空等到超时并杀掉开发端；启动开发端统一走 `mcdev_prepare`。
- 没有游戏内探针的加载器（NeoForge / Fabric 等）不再等待 39001/39002 探针端口：改为识别 Gradle 输出里的 `Done (…)`、`For help, type`、`Backend library` 等启动标记；到截止时间进程仍存活就按“已启动”处理，不再误杀。
- `server.properties` 定位修正：工程同时存在 `run/` 和 `run-server/` 时优先使用 `run/`，避免把 eula/端口写进游戏根本不读的目录。ModDevGradle 默认就是 `run/`。
- 启动服务端前检查实际 `server-port` 是否已被占用；已被占用（例如上次退出后残留的服务端）时直接返回 `alreadyRunning`，不再重复启动导致端口冲突。
- 同一会话内重复调用 `mcdev_prepare` 时复用已跟踪的进程，不再叠加多份 `runServer`。
- 新增 run 目录、端口读取、启动标记、重复启动拦截等回归测试。

## 2.0.7 — 2026-11-11

修复构建卡在 Gradle 自动下载 JDK：界面显示“生成中”，实际是 Gradle 在等 Adoptium 的响应。

- 构建、GameTest、启动开发端和 Studio 构建统一把本机检测到的所有 JDK 通过 `-Porg.gradle.java.installations.paths` 告诉 Gradle。像 `D:\developer\Java\jdk-21` 这种自定义目录现在能被正确识别，不再因为只看到 JAVA_HOME 里的 JDK 17 而去下载 JDK 21。
- 统一关闭 `org.gradle.java.installations.auto-download`：缺 JDK 时立刻返回可读错误（说明工程要求哪个版本、本机有哪些版本），不再卡在 TLS 握手或 5 分钟一次的 socket 读取超时上。
- 构建前预检工程声明的 `JavaLanguageVersion.of(N)` / `jvmToolchain(N)`，版本不匹配时直接提示安装对应 JDK，而不是启动一个注定失败的构建。
- 新增对应的 toolchain 解析与失败快速返回测试。

## 2.0.6 — 2026-11-11

修复“一轮生成跑几十分钟还不结束”的根本原因，并补上停止、超时与线程安全相关的一批问题。

- 新增网络访问层 `dma/netclient.py`：所有 HTTP 请求改为带硬性截止时间的后台线程 + 主线程等待，`urlopen(timeout=30)` 在打包运行时里卡在 TLS 握手（实测 `SSLSocket.do_handshake` 卡 50 分钟以上）的情况不再拖死整个 Agent。超时或取消后会立刻返回，不再无限阻塞。
- 无响应的主机记入 10 分钟黑名单，后续请求直接快速失败，避免对同一个黑洞地址反复重试。
- `cdn.jsdelivr.net` / `raw.githubusercontent.com` 的链接自动改写为 `api.github.com` 的 contents 接口（`Accept: application/vnd.github.raw`），这两个域名在部分网络环境下会完全不通。
- 工具层统一接入 `fetch_url`、`toolchain` 探测/下载、Studio 下载等网络出口，全部支持取消。
- 停止按钮真正生效：取消事件贯穿到网络请求与工具执行；点一次开始停止，再点一次可放弃等待并切回可输入状态。卡死的旧 Worker 信号不会再污染新任务。
- 新增“20 分钟无进展”看门狗：如果长时间没有写入文件、也没有完成构建，自动停止并提示用户，不再空转。
- UI 线程安全：编译/运行页的进程退出回调改为通过信号回主线程刷新，不再从后台线程直接改控件。
- 新增 `tests/test_netclient.py`（11 项）以及 Agent 取消、UI 自愈相关回归测试。

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
