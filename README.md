# 恶魔小猫工坊（Darkitten Mod Agent）

只写 Minecraft Java 模组的桌面 Agent。界面用 PySide6，内置对话工作台、技能、连接器、知识库与反编译能力。模组开发闭环来自 [yan-mc/minecraft-mod-dev](https://github.com/yan-mc/minecraft-mod-dev)：真实开发端启动、回环 HTTP 探针、场景卡片断言和 GameTest。

当前版本：`3.4.3`

## 3.4.3 源码自动同步与 Fabric 源码兜底

- **源码同步**：打包成功后自动把源码推送到 [LFELKEEA/darkitten-mod-agent](https://github.com/LFELKEEA/darkitten-mod-agent)；`tools/sync_github.py` 支持预演、只提交不推送、密钥扫描与 DNS 投毒兜底。
- **Fabric 源码兜底**：本地目录为空时直接向 Fabric meta 取当前稳定加载器版本，`api_lookup` / `dev_docs` 第一次就能下载 sources.jar 并建立索引。

## 3.4.2 确定性任务图、契约冻结与离线 API 索引

3.4.2 参考开源 MC 模组 Agent 的做法，把“模型负责决策、宿主负责状态”落到已有内核上：

- **任务图工具**：`workflow_plan` / `workflow_status` / `workflow_next` / `workflow_complete` / `workflow_fail` / `workflow_skip` / `workflow_reopen`。任务 DAG 存在 `.dma/workflow.json`，完成节点前必须通过验收门禁；缺少构建、静态检查、文件或运行时证据时会返回阻断项。
- **需求重规划**：`workflow_plan` 把新需求合并进 `.dma/spec.json`，补齐功能并重建 DAG，同时保留已完成状态，不会把进度清零。
- **公共契约审计**：`contract_freeze` 冻结 public/protected 类、方法、字段和资源清单到 `.dma/contract.json`；`contract_check` 报告删除的符号、签名变更、父类/接口变化与资源缺失。新增允许，删除与改签名必须显式迁移。
- **离线 API 签名索引**：`api_lookup` 从 `sources.jar` 建立紧凑索引并缓存，直接查类、方法参数、返回类型、字段类型；第一次按需下载源码，之后不再扫描整包。
- **工具总数**：95。

## 3.4.0 版本事实、架构参考与多版本工程

3.4 把“跨版本写错”从提示词提醒变成可查询、可校验的工程能力：

- **版本事实层**：新增 `version_facts` 与 `project_fact_check`。写代码前先核对 Java、元数据文件名、数据目录单复数、物品定义、映射与 ID API、构建插件；现有工程可直接扫出冲突文件与修复动作，并接入 `lint_project`。
- **跨版本专项**：1.21+ 数据目录改单数、1.21.4+ 物品定义放 `assets/<ns>/items`、NeoForge 1.20.5+ 用 `neoforge.mods.toml`、26.x 未混淆且非 remap、1.21.9+/26.x 用 `Identifier.fromNamespaceAndPath`，都有明确基线。
- **大型模组架构参考**：新增 `arch_ref`，提炼 Create、GregTech Modern、Mekanism、Botania 的注册分层、材料体系、RecipeMap、多方块、能力 API、配置与游戏内文档模式；写大型功能前先检索结构，再拆阶段。
- **多版本 / 多加载器**：新增 `multiversion_plan`，在 Stonecutter、Architectury、MultiLoader、Modstitch、Stonecraft 之间自动选型，输出目录骨架、官方插件坐标、迁移步骤与逐目标验证顺序。
- **调试闭环**：崩溃分类补齐 Java class 版本、映射改名、Access Widener/Transformer、元数据文件名、数据生成与配方错误；编译日志可直接把 class 版本换算成 Java 17/21/25。
- **技能包**：新增 version-facts、multiversion、access-widener-transformer、gametest、architecture-patterns、publishing-ci 六个技能；大型模组计划新增 facts 阶段。工具总数 85。

## 3.3.3 版本选择方式

- **版本与 API 改为纯点选列表**：新建工程里的 MC 版本、加载器版本、Fabric API 与「加载器」一致，点开下拉列表选择，不再是可输入的编辑框；滚轮经过也不会改值。快速生成页的加载器、MC 版本、规模同步改为点选下拉。

## 3.3.2 交互修正

- **版本与 API 只按选择切换**：新建工程里的 MC 版本、加载器、加载器版本、Fabric API、运行端、任务类型改为滚轮不改值的下拉框，避免鼠标滚轮经过时误改版本；点开列表或键盘选择才切换。
- **下载直链改为站点托管**：发布清单的 `download_url` 指向站点上的 ZIP，不再依赖 GitHub Release 是否已创建。

## 3.3.1 版本目录与更新日志

3.3.1 修掉了开发资料里「更新日志永远加载不出来」，并让新建工程直接对接在线版本目录：

- **更新日志真正联网同步**：开发资料的刷新按钮现在会实际拉取，而不是只重读缓存；同步完自动刷新列表并标出来源。
- **加载器版本记录补齐**：Fabric 优先 GitHub Releases；Forge / NeoForge / Quilt 没有 Release 时回退 Maven 元数据（优先 BMCLAPI 与高校镜像），再失败才用 GitHub Tags。四家加载器都能看到最近 12 个版本。
- **新建工程读取完整版本目录**：MC 版本不再写死 10 个，改为读取在线 Mojang 清单（正式版 + 快照），并合并启动器里已安装的实例；下拉框可编辑，任何版本都能直接输入。
- **加载器与依赖版本可选**：新增「加载器版本」下拉（Fabric / Quilt 全量，Forge / NeoForge 按当前 MC 过滤），Fabric 工程新增「Fabric API」下拉；两者写入 `.dma/project.json` 并进入系统提示，避免实现时猜版本。
- **Quilt 工程**：新建工程允许选择 Quilt，没有内置骨架时由 Agent 按官方模板生成构建脚本。
- **自动参考覆盖更全**：任务开始前的自动参考提升到 2 个模组 / 8 条片段，仍然只读、不写入用户工程。

## 3.3 在线优先与智能参考

3.3 不再把开发环境塞进安装包，而是让工坊按需从网站和镜像拉取：

- **轻量本体**：安装包只包含程序和运行库。JDK 21、Gradle 发行版、版本目录、MDK、`sources.jar` 和参考模组都在第一次需要时下载到程序 `data` 目录，可删除后重新拉取。
- **在线资源层**：`resource_ensure` 支持 `status / all / jdk / gradle / catalog / mdk / sources / docs`。官方源优先，失败自动切华为、腾讯、中科大与 BMCLAPI 镜像；发布清单 `mod_agent_bootstrap.json` 公开每个资源的来源。
- **在线开发资料**：`dev_docs` 直接拉取各加载器的 `sources.jar` 并检索类、方法与注解；本地没有源码时回退 GitHub 代码搜索，不依赖预置文档。
- **智能参考**：实现功能前自动识别机制（实体、方块、GUI、世界生成、渲染、Mixin、网络、配方、皮肤、社交等 19 类），搜索并下载同类模组，反编译、建索引，再把相关片段作为只读参考注入本轮任务。源码不写入用户工程，始终附带许可证提示。
- **大型模组规划**：`large_mod_plan` 生成阶段、任务与包结构；large 目标为 40 个类 / 10 个包 / 8 个阶段 / 16 项测试。这里的“2G 标准”指大型模组所需的工程能力，不是安装包体积。
- **可关闭**：对话页「自动参考」开关与 `auto_reference` 配置；不做参考时仍可用 `mod_reference` / `dev_docs` 手动查询。

## 3.2 开发中心与启动器联动

3.2 把工坊、启动器和模组资料连成一条线：

- **开发中心**：侧边栏「开发中心」包含版本中心、开发资料、测试中心、模组学习四个分区。版本中心列出启动器里的实例，可直接用启动器启动；也可以在这里下载 MDK、Javadoc / 源码、原版 client.jar / server.jar。
- **启动器联动桥**：启动器运行时会在本机随机端口开一个只接受随机 token 的 HTTP 服务（仅 `127.0.0.1`）。工坊通过它读取实例、下载地址和 Modrinth 数据，并请求启动器打开或启动实例。启动器没开时自动回退到读取 `config.json` 和 `versions/`。
- **持续同步开发资料**：后台每 6 小时同步 Mojang 版本清单、Fabric / Quilt 游戏与加载器、Yarn / Quilt Mappings、Forge / NeoForge 版本、Gradle 与 Adoptium JDK、四家加载器的 MDK / Javadoc / 源码地址和更新日志；所有数据保存到 `data/catalog/catalog.json`，断网时继续使用上次结果。
- **模组学习**：在工坊或启动器里搜索 Modrinth 模组，下载后反编译成可读源码，写入本机学习库并生成索引。源码只作阅读与排错参考，不写入用户工程，也不重新分发。
- **离线版（可选）**：默认发布使用 `python build.py` 生成轻量在线版。只有在明确需要离线交付时，才用 `python build_full.py --offline` 把 JDK 21、Gradle 依赖与 NeoForm / Loom 缓存一起打包成分卷发行版。

## 2.0 内核

2.0 不再让模型“写完就说完成”，而是把一次模组开发拆成可验收的执行链：

- **规格先行**：任何需求先落 `.dma/spec.json`（功能、文件归属、验收项），再生成 `.dma/workflow.json` 任务图。
- **DAG 调度**：骨架 → 功能 → 静态检查 → 编译 → 运行时验证 → 交付验收 → 打包；依赖未完成时下游任务不可跳过，失败会阻断下游。
- **强制验收门禁**：编译、测试、静态检查、运行时证据和人工确认分别有明确来源；缺少证据时任务只能标记为 `blocked`，不能标记为 `done`。`mcdev_prepare` 只有客户端真正进入世界（`joined`）才算 `ok=True`。
- **快照与回滚**：构建前自动快照，失败后可回滚到最近一次成功快照；快照支持 diff、保留策略和 bisect。
- **循环保护**：同一错误重复出现时记录建议并升级处理策略，避免无意义重试。
- **精确编辑**：`edit_file` 支持按 `Class.method` 替换方法体、按类插入成员，也保留 `old/new` 精确替换；`read_file` 带内容哈希和分段读取，未变化时直接跳过。
- **静态分析**：176 条规则覆盖 P0/P1/P2/P3/TOOL，内置符号索引、映射转译、Mixin / 客户端 / 线程 / 网络 / Gradle 检查；能力矩阵见 [`docs/2.0-capability-matrix.md`](docs/2.0-capability-matrix.md)。
- **离线工具层**：符号库、映射、模板、资产、文档缓存可离线使用；新增资产库、配置界面生成器、调试模板（dump / 崩溃上下文 / 分层探针）、兼容矩阵 / CI 工作流、迁移备份、块模型预览等工具。

## 支持范围

- **加载器**：Fabric、Forge、NeoForge、Architectury。新建工程时选择加载器和 Minecraft 版本，助手按选择写代码，不混用不同加载器的 API。
- **可直接生成的骨架（Minecraft 1.20.1）**：
  - Forge：ForgeGradle 6.0–6.2、Forge 47.4.28、官方映射、Gradle 8.8。
  - Fabric：fabric-loom 1.7-SNAPSHOT、Yarn `1.20.1+build.10`、Fabric Loader 0.15.11、Fabric API 0.92.2+1.20.1。
  - NeoForge：ModDevGradle legacyforge 2.0.91、`net.neoforged:forge:1.20.1-47.1.106`（仍用 Forge 包名与事件总线）。
  - Architectury：不内置骨架，由助手按官方模板生成 `common` / `fabric` / `neoforge` 多子工程。
- **其他版本**：只记录工程元数据，构建脚本、映射和依赖版本由助手核对官方模板后生成，工坊不凭空猜测版本号。
- **Java 17**、Gradle Wrapper **8.8**（内置骨架统一使用）。

## 功能

- **快速生成任意模组**：「快速生成」页输入一句构思，先生成 `mod-spec.json` 与 `GDD.md`（主题、内容图、进度、数值、资源、验收标准），再直接落地注册代码、纹理、模型、语言、配方、战利品、世界生成与社交系统。支持 Forge / NeoForge / Fabric 的受支持版本；不支持的组合会明确提示。生成后可以一键审计、编译、启动开发服务端验证和打包，也可以回到对话页继续细化玩法。聊天 Agent 同样可以调用 `studio_plan` / `studio_generate` / `studio_audit` / `studio_build` / `studio_verify` / `studio_package`。
- **先改代码，再用真实环境验证**：助手写完代码直接编译。Forge 1.20.1 工程还能启动真实开发服务端 / 客户端，用探针读取状态、背包、方块、界面和截图；其他加载器以编译结果、运行日志和 GameTest 为准，不伪造游戏内结果。
- **开发端进程管理（Forge 1.20.1）**：启动双端 / 仅服务端 / 仅客户端，自动等待端口就绪；双端模式会先起服务端，再让客户端连接 `127.0.0.1:25566`。可以在运行面板看到 PID、探针端口、是否进入世界和启动日志。
- **回环 HTTP 探针（Forge 1.20.1）**：只监听本机，服务端 `39001`、客户端 `39002`，请求头 `X-Mcdev-Token: mcdev`。支持状态、指令、UI 快照、点击、物品使用、截图、运行日志导出。其他加载器会自动跳过探针安装，并在运行面板提示改用编译与日志验证。
- **场景卡片与断言（Forge 1.20.1）**：YAML 卡片可写状态、指令、放置/破坏方块、挖矿、合成、配方、战利品、物品、使用、界面点击、截图、日志导出和断言。断言支持 `eq / ne / contains / gt / gte / lt / lte / exists`，失败会带出真实数据和步骤编号。
- **GameTest**：Fabric 执行 `gradlew runGametest`，Forge / NeoForge 执行 `gradlew runGameTestServer`，解析通过 / 失败汇总，适合纯服务端逻辑。
- **多加载器工程骨架**：1.20.1 的 Forge / Fabric / NeoForge 新建即生成可编译骨架，包含入口类、元数据、语言与资源目录、Gradle Wrapper；Architectury 和其他版本由助手按官方模板生成，再由编译结果验证。
- **按加载器写代码**：系统提示和技能会注入当前工程的加载器规范（Fabric 的 `ModInitializer` / Yarn；Forge 的 `DeferredRegister` / `mods.toml`；NeoForge 的 `IEventBus` / `mods.toml`；Architectury 的 `common` + 平台子工程），并禁止把 Forge 写法套到其他加载器。
- **编译与产物**：编译执行 `gradlew build`，从 `build/libs` 读取真实 JAR；运行面板和「成品」区域都会显示产物。
- **精确改文件**：优先用 `edit_file` 做单点替换，新建或整体重写才用 `write_file`；覆盖前自动备份。
- **技能**：内置模组开发工作流，包含 Fabric / Forge / NeoForge / Architectury 工程搭建、从 JAR 参考实现、纹理 / GUI、Mixin 风险、崩溃排查等；可停用、改写、恢复默认或新建自定义技能。
- **像素纹理生成**：助手调用 `pixel_art`，用调色板 + 字符网格直接写出 RGBA PNG。
- **连接器**：Modrinth、GitHub API 开箱可用，也支持自定义 HTTP 接口与 `${ENV}` 密钥引用。
- **知识库**：导入 txt / md / docx / pdf / jar / 代码和配置文件，自动提取正文与分片；对话前自动检索相关片段，也可用工具按需深挖。
- **模组 JAR 分析**：解析 `mods.toml` / `fabric.mod.json`、语言与资源 JSON、类结构，并用内置 Vineflower 反编译 Java 源码；生成「模组速览 + 类索引」。
- **反编译到工程**：选中 JAR 点「反编译到工程」，主 JAR 与 `META-INF/jars` 内的自有模块一起展开到 `reference/decompiled/<JAR 名>/`，不参与编译，只作迁移参考。
- **环境自检**：检查 Java / Gradle / Git / 目录写入 / 资料源连通性和模型通道；优先选择 JDK 17。
- **桌面快捷方式与自动更新**：首次启动询问创建快捷方式；更新包校验 SHA-256 后替换并重启。

## 直接运行（源码）

```bash
python -m pip install -r requirements.txt
python main.py
```

Windows 上也可以双击 `main.py`（需要已安装 Python 与依赖）。

## 使用流程

1. 打开程序，在「设置」里配置模型通道；不熟悉流程可以先看「教程」页。
2. 想要完整模组时，先到「快速生成」写一句构思，选加载器、MC 版本和规模，点「生成工程」。默认会生成规格、工程和资源，并自动审计；需要产物就勾选「生成后编译」。
3. 想从空骨架开始，也可以在右上角「新建工程」填写模组名称、Mod ID、Minecraft 版本和加载器。1.20.1 的 Forge / Fabric / NeoForge 会直接生成可编译骨架。
4. 在对话页继续描述需求，例如「把生成的矿脉改成只在深海生成，并给海龙加驯服后的跟随行为」。助手会按当前加载器读工程、改代码、补资源。
5. 需要验证时，先编译；Forge 1.20.1 工程可以让助手启动开发端，或直接点对话页右侧「运行」，首次启动会编译并安装探针。其他加载器看编译结果与运行日志。
6. 用 GameTest 或场景卡片断言结果。Forge 1.20.1 的卡片能读到真实背包、界面和日志；失败时助手会改完再跑。
7. 编译产物在工程的 `build/libs`。发布前先停掉开发端，再把模组 jar 放进 `.minecraft/mods`。
8. 不再需要的工程，点右上角「删除工程」。确认后目录进入回收站，最近记录和对话记录一并清除。

## 探针与场景卡片

探针是一个 Forge 1.20.1 模组，由工坊在首次运行时编译并安装到开发端的 `run/mods`。它只监听回环地址，不对外提供访问。Fabric、NeoForge、Architectury 和其他版本不支持探针，工坊会自动跳过安装，验证以编译、运行日志和 GameTest 为准。

场景卡片示例：

```yaml
name: 星尘矿掉落
setup:
  commands:
    - time set day
actions:
  - command: setblock 0 64 0 stardust_tools:stardust_ore
  - wait: 500
assert:
  - block_state:
      x: 0
      y: 64
      z: 0
      block: stardust_tools:stardust_ore
  - loot_break:
      x: 0
      y: 64
      z: 0
      block: stardust_tools:stardust_ore
      item: stardust_tools:stardust
      count: 1
```

`setup.commands` 先执行准备指令，`actions` 按顺序执行操作，`assert` 校验结果。助手也可以直接传步骤数组给 `mcdev_scenario`，不必写文件。第一步失败就会停下，并返回失败步骤、实际值和期望值。

## 编译与运行

侧边栏「编译运行」页和对话页右侧「运行」面板都能操作：

- **编译**：`gradlew build`
- **开发客户端**：`gradlew runClient`
- **开发服务端**：`gradlew runServer`
- **GameTest**：Fabric 用 `gradlew runGametest`，Forge / NeoForge 用 `gradlew runGameTestServer`
- **启动双端（Forge 1.20.1）**：先服务端就绪，再客户端启动并自动连接

任务日志逐行实时显示，可以停止编译、清空日志、打开 `build/libs`。关闭工坊会结束由工坊启动的开发端进程。Fabric / NeoForge / Architectury 不做探针注入，运行面板会提示以编译结果和日志为准。

优先使用工程自带的 `gradlew` / `gradlew.bat`。Windows 上路径带空格时，宿主用 `cmd /d /s /c` 包住完整命令行，不会把目录名当成命令。Gradle 子进程固定注入 Java 17 的 `JAVA_HOME`，并默认使用 Windows 根证书库（`-Djavax.net.ssl.trustStoreType=Windows-ROOT`），避免本机加速器或杀毒软件的 HTTPS 拦截导致 `PKIX path building failed`。

首次编译仍要下载 Minecraft、当前加载器和构建插件，速度取决于网络和源站；工坊不会伪造构建结果。

## 知识库

侧边栏「知识库」用来放做模组时要参考的资料：开发规范、版本笔记、API 文档、以前的踩坑记录都可以导入。

- **支持格式**：`txt` / `md` / `docx` / `pdf` / `jar`，以及 `json`、`yaml`、`toml`、`properties`、`gradle`、`java`、`kt`、`py`、`html`、`xml`、`csv` 等文本文件。
- **模组 JAR**：导入后提取模组元数据、`lang` 语言文件、资源 JSON、包内 `.java` 源码和类结构；本机有 JDK 17+ 时调用内置 Vineflower 反编译。
- **JAR 速览**：开头先写模组 ID、入口类、重点类和中文字符串提示，再按包列出完整类索引，末尾才是反编译源码。
- **中文检索**：提问里的「附身 / 皮肤 / 渲染 / 网络」等词会映射到 `Possession / Skin / Render / Packet` 等英文标识符；检索按整份源码文件返回。
- **JAR 限制**：Fabric 模组里常见 `class_1234` / `method_5678`，这是 intermediary 名称，不是反编译失败；需要可读名称时对照映射表。
- **JAR 上限**：最多展开 5000 个类和 2000 个文本条目，反编译源码最多 3000 万字符。
- **反编译到工程**：选中 JAR 后点「反编译到工程」，默认写到 `reference/decompiled/<JAR 名>/`，按原包路径保存并生成 `INDEX.md`。
- **导入范围**：默认所有工程共用；勾选「导入到当前工程」后只有当前工程能检索到。
- **管理**：列表可以搜索文件名、来源和正文；删除只清知识库里的副本，不动原始文件。
- **限制**：扫描版 PDF 没有文字层，提取不到内容；加密 PDF、旧版 `.doc` 也不支持。

导入的正文和分片存在 `data/knowledge.db`，原始文件仍留在你原来的位置。

## AI 通道

设置页支持三种：

- **免费公共通道**：默认，开箱可用，稳定性一般。
- **OpenAI 兼容接口**：填写 Base URL、模型、API Key。
- **本地 Ollama**：默认 `http://127.0.0.1:11434/v1`，例如 `qwen2.5-coder:7b`。

API Key 只保存在本机 `data/config.json`，不会上传。

## 环境自检

侧边栏「环境」页首次打开会自动检测：

- Java 是否在 PATH / JAVA_HOME / 常见安装目录中，版本是否为 17 或更高；同时装了多个 JDK 时优先 17。
- 当前工程是否有可用的 Gradle Wrapper。
- Git 是否可用（可选）。
- 工程目录、数据目录是否可写。
- Modrinth、GitHub API 是否连通。
- 当前模型通道是否缺少 API Key。

检测结果按「通过 / 注意 / 缺失 / 提示」分级，并给出修复建议。

## 数据与安全

所有用户数据在程序目录下的 `data/`（可用环境变量 `DMA_DATA_DIR` 改位置）：

```
data/
  config.json        模型与界面设置
  memory.db          长期记忆
  knowledge.db       知识库
  connectors.json    连接器与请求头
  skills/            技能 Markdown
  projects/          默认工程目录
  projects.json      最近工程
  sessions/          每个工程的对话记录
  backups/           覆盖文件前的自动备份
  cache/             探针 JAR 等缓存
  trash/             回收站不可用时的备用删除目录
  updates/           更新包与更新日志
```

写入约束：

- 只允许写工程目录内的相对路径，拒绝绝对路径、盘符和 `..`。
- 单文件超过 2,000,000 字符拒绝写入。
- 覆盖已有文件前复制到 `data/backups/<工程>/<时间戳>/`。
- 自动写入默认关闭，需要在设置里主动开启。

删除工程：

- 必须二次确认，确认框里会显示完整目录；默认按钮是「取消」。
- Windows 上走系统回收站，可以从回收站还原；回收站不可用时会移到 `data/trash/`，不会直接永久删除。
- 磁盘根目录、用户主目录、程序目录、数据目录以及包含这些目录的上级目录会被拒绝删除。

探针只监听 `127.0.0.1`，不向任何服务器上传工程、日志或截图。

## 开发与测试

```bash
pip install -r requirements-build.txt
python -m compileall -q dma
python -m unittest discover -s tests -t .
```

Windows 无桌面环境时：

```bash
set QT_QPA_PLATFORM=offscreen
python -m unittest discover -s tests -t .
```

## 打包

```bash
python -m pip install -r requirements-build.txt
python build.py
```

产物：

- `dist/DarkittenModAgent/`：PyInstaller onedir 目录，包含 `DarkittenModAgent.exe` 和 `_internal/`。
- `dist/DarkittenModAgent-<版本>.zip`：上传到下载站 / GitHub Release 的更新包。
- `dist/mod_agent_release.json`：更新清单，包含版本、大小、SHA-256 与更新说明。

发布时把 ZIP、`mod_agent_release.json`、`mod_agent_bootstrap.json` 一起放进下载站仓库（GitHub Pages）根目录；清单里的 `download_url` 直接指向站点上的 ZIP，避免 Release 未创建时 404。

## 源码同步

源码仓库：[LFELKEEA/darkitten-mod-agent](https://github.com/LFELKEEA/darkitten-mod-agent)。

```bash
python tools/sync_github.py            # 扫描敏感信息 -> 提交 -> 推送
python tools/sync_github.py --dry-run  # 只看会改哪些文件
python tools/sync_github.py --no-push  # 只提交到本地
```

`build.py` 在打包成功后会调用同一脚本自动同步源码；设置环境变量 `DMA_NO_SYNC=1` 可跳过。脚本会先拒绝 `config.json`、`social.json`、`data/`、`logs/` 等本地文件，并扫描 `sk-`、`gho_`、私钥与常见密钥字段；遇到校园网 DNS 投毒或 Schannel 吊销检查失败时，自动改用可达 IP 与进程级 TLS 兼容参数，不修改全局 Git 配置。

## 边界

- 探针能读游戏内状态和截图，但只支持 Forge 1.20.1；其他加载器没有游戏内断言，只能以编译结果、运行日志和 GameTest 为准。
- 探针也不能代替人工判断美术、手感、平衡性。
- 编译成功不等于游戏内正常；没有场景断言或 GameTest 结果时，助手必须明确写「仅编译通过」。
- 内置骨架只覆盖 1.20.1 的 Forge / Fabric / NeoForge；Architectury 和其他版本由助手生成，必须用一次真实编译验证依赖和映射版本。
- Gradle 构建脚本会执行本机代码，只在自己信任的工程里运行。
- Mixin、自定义网络包、方块实体、世界生成等高风险改动会标注【⚠️人工复核】。
- 《我的世界》EULA 与 Mojang 映射许可仍然适用；工坊不打包游戏本体，也不分发模型或资源文件的原始素材。

## 第三方许可

本项目的探针、进程管理、场景卡片和游戏验证流程参考 [yan-mc/minecraft-mod-dev](https://github.com/yan-mc/minecraft-mod-dev)，遵循 MIT License，Copyright (c) 2026 yan-mc。许可证全文见 `LICENSE-minecraft-mod-dev.txt`，第三方组件与许可见 `THIRD_PARTY_NOTICES.txt`。

`assets/decompiler/vineflower.jar` 来自 Vineflower 1.12.0，遵循 Apache-2.0；`assets/wrapper/` 内的 Wrapper 文件来自 gradle/gradle，遵循 Apache-2.0，各自目录下附有出处与许可说明。反编译只在本机运行，不会把 JAR 或生成的源码上传。
