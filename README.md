# 恶魔小猫工坊（Darkitten Mod Agent）

只写 Minecraft Java 模组的桌面 Agent。界面用 PySide6，内置对话工作台、技能、连接器、知识库与反编译能力。模组开发闭环来自 [yan-mc/minecraft-mod-dev](https://github.com/yan-mc/minecraft-mod-dev)：真实开发端启动、回环 HTTP 探针、场景卡片断言和 GameTest。

当前版本：`3.0.5`

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

发布时把 ZIP 上传到 GitHub Release（标签 `mod-agent-v<版本>`），把清单放到站点根目录；清单里的 `download_url` 指向 Release 资源。

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
