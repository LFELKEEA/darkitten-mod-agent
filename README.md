# 恶魔小猫工坊（Darkitten Mod Agent）

只写 Minecraft Java 模组的独立桌面 Agent。基于 PySide6，自带对话工作台、技能、连接器和知识库，可从下载站、GitHub 与 Darkitten 启动器获取，并支持自动更新。

当前版本：`1.0.2`

## 功能

- **Fabric / NeoForge / Architectury**：创建工程、增量修改、崩溃排查、跨版本移植。
- **三项硬性前置**：MC 版本、加载器、Mod ID，缺一项就不会开始写代码。
- **完整文件交付**：按工程相对路径输出完整文件，不用“其余不变”省略。
- **图文教程**：侧边栏「教程」把配置模型、新建工程、描述需求、写入检查、编译运行、自动修复、出包安装做成 7 步演示，每一步带界面示意图和直达按钮，并提供常用需求模板。
- **待写入确认**：生成的文件先进入右侧列表，确认后才落盘；覆盖前自动备份。也可以在对话页直接打开「自动写入」，生成后立即落盘并留下「已自动写入」记录。
- **写入后自动编译**：打开「自动编译」后，每轮写完自动执行 `gradlew build`；失败时把 Gradle 日志直接交给助手修一轮，最多连续编译两次，仍失败就停下并把日志给你。助手改代码优先用 `edit_file` 精确替换，不再整份重写。
- **技能**：内置 10 个模组开发工作流，可停用、改写、恢复默认或新建自定义技能。
- **连接器**：Modrinth、Fabric Meta、GitHub API 开箱可用，支持自定义 HTTP 接口与 `${ENV}` 密钥引用。
- **一键准备**：自动挑 JDK（优先 21，缺了就下载 Temurin 21）、补齐 Wrapper、从中科大 / 华为云镜像装好 Gradle 发行包，不用手动配环境变量；大文件走多连接分段下载，断线可续传。
- **本地依赖自动整理**：`build.gradle` 里写 `files('libs/xxx.jar')` 后，点「编译」或「一键准备」会自动从下载目录、桌面、其它盘符的「下载」文件夹和知识库来源目录找出同名 jar（带中文前缀也认），复制进工程并创建 `libs/`；找不到时才会提示缺哪个文件。
- **助手自己编译**：对话里的助手可以直接调用 `build_project` 执行 `build` / `runClient` / `runServer` / 数据生成（NeoForge 用 `runData`，Fabric 用 `runDatagen`，按工程自动选择），自己准备 JDK、Wrapper、本地 jar 和镜像代理，自己读取日志、改代码、再次编译，直到出包；不需要用户手动执行 `gradlew` 或把日志贴回来。
- **开发运行自动修复**：编译前自动补齐 NeoForge ModDevGradle 的 `mods { sourceSet sourceSets.main }`，修掉无效的 `clientData()` 数据生成运行并补上 `--mod / --output / --existing` 参数，避免「开发客户端 Mixin 找不到类」和「unknown run: clientData」；`ProcessResources` 用 `expand` 替换中文时自动加 `filteringCharset = 'UTF-8'`。
- **Gradle 版本自适应**：识别到 ModDevGradle / NeoGradle 时自动把 Wrapper 切到 Gradle 8.10.2，其它工程默认 9.8.2；`gradle-wrapper.properties` 损坏会自动重写，编译报 `requires at least Gradle X` 时也会自动升级重试。
- **NeoForge 依赖自动兜底**：`maven.neoforged.net` 返回 502、连接被拒、被 DNS 污染或证书校验失败时，自动启动本机 Maven 代理并重试；代理按需从国内镜像取文件并缓存，缺的 `neoform-runtime` 和 `minecraft-dependencies` 会在本机合成，不需要手动改 hosts 或配镜像源。
- **删除工程**：对话页右上角的「删除工程」先把工程目录移到 Windows 回收站，再从最近列表里移除并清掉会话记录；有生成任务或 Gradle 任务在跑时会先拦住。
- **环境自检**：检查 Java、JAVA_HOME、Gradle、Git、目录写入、资料源连通性和模型通道，给出缺失项与修复建议，可复制报告。
- **编译运行**：点任务按钮时先自动准备环境，再执行 `gradlew build` / `runClient` / `runServer` / 数据生成（`runData` / `runDatagen` 按工程自动选择）；日志实时回显，可停止、复制日志、打开 `build/libs`。
- **工具轮数**：默认 32 轮，可调到 999；填 0 表示不限制，适合大型工程。
- **知识库**：导入 txt / md / docx / pdf / jar / 代码和配置文件，自动提取正文、分片；每轮对话前检索相关片段，也能让助手用 `search_knowledge` / `read_knowledge` 按需查。列表里点选文件只加载前 4 万字符预览，上千万字的大 JAR 也不会卡住界面。
- **模组 JAR 分析**：直接导入现成模组，提取 `fabric.mod.json` / `mods.toml`、语言与资源 JSON、类结构，并用内置 Vineflower 反编译出 Java 源码；开头生成「模组速览 + 类索引」，中文问题也能对上英文类名，做兼容、移植或复刻功能时可直接参考实现。
- **反编译到工程**：知识库页选中 JAR 点「反编译到工程」，把主 JAR 和 `META-INF/jars` 里的自有内嵌模块一起展开成 `.java`，写到 `reference/decompiled/<JAR 名>/`；按原包路径保存，生成 `INDEX.md`，不参与编译，方便让助手逐个文件迁移。对话里也可以直接让助手调用 `decompile_jar`。
- **桌面快捷方式**：首次启动询问是否创建。
- **自动更新**：读取 `mod_agent_release.json`，从 GitHub Release 下载 ZIP（自动走加速源）、校验 SHA-256，退出后由后台脚本替换程序文件并重启。

## 直接运行（源码）

```bash
python -m pip install -r requirements.txt
python main.py
```

Windows 上也可以双击 `main.py`（需要已安装 Python 与依赖）。

## 使用流程

1. 打开程序，在「设置」里配置模型通道；不熟悉流程可以先看「教程」页。
2. 在右上角点「新建工程」，填写 MC 版本、加载器、Mod ID、依赖和补充要求。默认勾选「创建后立即生成可编译骨架」，创建后会直接写文件、补依赖并编译。
3. 在输入框描述功能，或点「插入需求单」套用模板；Ctrl+Enter 发送。
4. 开启「自动写入」时文件直接落盘；关闭时会先进入右侧「待写入文件」，检查后点「写入全部」，或先双击预览单个文件。
5. 打开「自动编译」时，每轮写入后自动 build；失败会自动回传日志让助手修一轮。也可以到「编译运行」页手动执行编译、开发客户端、开发服务端或数据生成。
6. 需要让它接着上一轮继续做时，点输入框上方的「继续」，不用重新描述一遍。
7. 不再需要的工程，点右上角「删除工程」。确认后目录进入回收站，最近记录和对话记录一并清除。

## 知识库

侧边栏「知识库」用来放做模组时要参考的资料：开发规范、版本笔记、API 文档、以前的踩坑记录都可以导入。

- **支持格式**：`txt` / `md` / `docx` / `pdf` / `jar`，以及 `json`、`yaml`、`toml`、`properties`、`gradle`、`java`、`kt`、`py`、`html`、`xml`、`csv` 等文本文件。
- **模组 JAR**：导入 `.jar` 后会提取模组元数据、`lang` 语言文件、资源 JSON、包内 `.java` 源码和类结构，并在本机有 JDK 17+ 时调用内置的 Vineflower 1.12.0 反编译出 Java 源码。反编译结果按类拼接进同一份资料，助手可以直接检索方法体。
- **JAR 速览**：每份 JAR 资料开头先写模组 ID、入口类、重点类和中文字符串提示，再按包列出完整类索引；末尾才是反编译源码。助手打开资料先拿到一张地图，不会只看到一堆方法体。
- **中文检索**：提问里的「附身 / 皮肤 / 渲染 / 网络」等词会额外映射到 `Possession / Skin / Render / Packet` 等英文标识符；检索按整份源码文件返回，同一文件的多处命中会合并，并按类名、文件名和关键词出现次数排序，关键词没命中时还会自动把速览和类索引交给助手。
- **旧资料**：0.3.3 之前导入的 JAR 没有速览和中文提示，程序会从旧的类结构里补一份类索引；想要完整反编译源码和新版提示，在知识库页对同一个 JAR 重新导入即可覆盖旧记录。
- **JAR 限制**：Fabric 模组里常见 `class_1234` / `method_5678`，这是 intermediary 名称，不是反编译失败；需要可读名称时对照工程当前的 Yarn / Mojmap 映射，或再导入对应的 `-sources.jar`。反编译属于机器还原，变量名和部分控制流可能与作者原稿不同。
- **JAR 上限**：最多展开 5000 个类和 2000 个文本条目，反编译源码最多 3000 万字符；单份资料上限 3000 万字符，正常模组不会被切掉后半段。旧版本导入的资料被截断时，读取会提示重新导入。
- **反编译到工程**：选中 JAR 后点「反编译到工程」，会同时展开主 JAR 和它自己的 `META-INF/jars/*.jar` 内嵌模块（依赖库会跳过），默认写到 `reference/decompiled/<JAR 名>/`，按原包路径保存并生成 `INDEX.md`。这个目录不在 `src/main/java` 下，不参与编译，只作为迁移参考；目标已有 `.java` 时会先确认覆盖。助手在对话里也可以调用 `decompile_jar` 完成同样的事。
- **导入范围**：默认所有工程共用；勾选「导入到当前工程」后只有当前工程能检索到。重复导入同一个文件会更新原条目。
- **自动检索**：每轮对话前，助手会按用户这句话从知识库里挑最相关的片段放进系统提示，不需要手动复制粘贴。
- **按需深挖**：资料不够时，助手会调用 `search_knowledge` 再搜，或用 `read_knowledge` 读整份文件。检索现在直接返回完整的 `## 源码：路径` 条目，不再只给 1200 字符的代码片段；读 JAR 源码时用 `read_knowledge` 的 `file` 参数（例如 `file="Hypnosis.java"`）一次拿整个 `.java`，单文件十几万字符也不截断。类结构摘要最多列 300 个方法，摘要里的「…还有 N 个」只是摘要截断，不代表源码缺失。
- **管理**：列表可以搜索文件名、来源和正文；选中后看提取出的正文；删除只清知识库里的副本，不动原始文件。
- **限制**：扫描版 PDF 没有文字层，提取不到内容；加密 PDF、旧版 `.doc` 也不支持，先转成 `.docx` 或带文字层的 PDF。

导入的正文和分片存在 `data/knowledge.db`，原始文件仍留在你原来的位置。

## AI 通道

设置页支持三种：

- **免费公共通道**：默认，开箱可用，稳定性一般。
- **OpenAI 兼容接口**：填写 Base URL、模型、API Key。
- **本地 Ollama**：默认 `http://127.0.0.1:11434/v1`，例如 `qwen2.5-coder:7b`。

API Key 只保存在本机 `data/config.json`，不会上传到本项目服务器。建议使用最低权限的 Key。

## 环境自检

侧边栏「环境」页首次打开会自动检测：

- Java 是否在 PATH / JAVA_HOME / 常见安装目录中，版本是否为 17 或 21；同时装了多个 JDK 时会优先选 21。
- JAVA_HOME 是否指向 JDK 根目录。
- 当前工程是否有可用的 Gradle Wrapper，系统是否安装 Gradle。
- Git 是否可用（可选）。
- 工程目录、数据目录是否可写。
- Modrinth、Fabric Meta、GitHub API 是否连通。
- 当前模型通道是否缺少 API Key。

检测结果按「通过 / 注意 / 缺失 / 提示」分级，并给出下载页或修复命令。安装 JDK 或修改环境变量后点「重新检测」即可。

环境页右上角的「一键准备」会把缺的东西直接装好：

- **JDK**：扫描 JAVA_HOME、PATH、注册表和常见目录（`C:\Program Files\Java`、`D:\developer\Java`、`~/.jdks`、`~/.gradle/jdks` 等），优先选 Java 21；本机一个 17–25 的 JDK 都没有时，从中科大 Adoptium 镜像下载 Temurin 21，镜像不可用时退回华为云 OpenJDK 和 Adoptium 官方，校验 SHA-256 后解压到 `data/tools/jdk-*`，不写系统环境变量。
- **Wrapper**：只补缺失的 `gradlew` / `gradlew.bat` / `gradle-wrapper.jar` / `gradle-wrapper.properties`，不覆盖工程里已有的 Gradle 版本。
- **Gradle**：读取工程 Wrapper 里配置的版本，先看 `~/.gradle/wrapper/dists` 是否已安装；缺了就从华为云镜像、腾讯云镜像或 Gradle 官方下载，校验 SHA-256 后直接解压进 Wrapper 缓存。这样第一次编译不用再等 Wrapper 自己慢慢下 150 MB。

JDK 和 Gradle 发行包这类大文件（≥ 8 MB）默认开 6 条连接分段下载，断线后从已下载的部分继续，合并后再校验一遍大小和 SHA-256。

下载和安装进度会显示在「一键准备」下面的日志框里。执行 Gradle 和启动游戏仍在「编译运行」页。

## 编译与运行

侧边栏「编译运行」页直接对当前工程执行 Gradle：

- **编译**：`gradlew build`，产物在 `build/libs`。
- **开发客户端**：`gradlew runClient`，自动带上当前模组，游戏目录是工程下的 `run/`。
- **开发服务端**：`gradlew runServer`。
- **数据生成**：`gradlew runDatagen`。

点任何一个任务按钮，工坊会先自动跑一遍「一键准备」：挑 JDK、补 Wrapper、装 Gradle，然后再执行任务。环境刚准备过（120 秒内、同一个工程）会直接启动，不重复扫描。准备过程写在运行日志里，和 Gradle 输出连在一起。

优先使用工程自带的 `gradlew` / `gradlew.bat`，没有则回退到系统 PATH 里的 `gradle`。构建会把选中的 JDK 放进 `JAVA_HOME` 和 `PATH`，并用 `GRADLE_OPTS` 把 HTTP 超时放宽到 60 秒。程序装在带空格的目录（例如 `D:\Darkitten Launcher\...`）时也能正常启动 Wrapper，运行日志里显示的是实际执行的完整命令。依赖下载遇到 `Connection reset` / `Read timed out` / `Could not download` 时，会自动重试最多两次，已经下完的依赖会保留。

日志逐行实时显示，可以停止任务、复制日志贴回对话页，或直接打开 `build/libs`。关闭工坊会结束正在运行的 Gradle 任务。首次运行仍要下载 Minecraft 和 Fabric / NeoForge 依赖，这部分速度取决于你的网络和源站；Fabric 的 `maven.fabricmc.net` 目前没有可用的国内镜像，工坊无法替它加速。

页面底部有三个补配置的按钮：

- **一键准备**：只装环境、不跑任务；适合第一次打开工程或换机器时先确认 JDK / Gradle 都就绪。
- **让助手补全配置**：工程里没有 `build.gradle` / `settings.gradle` 时，自动切到对话页并发出构建脚本请求，按工程记录的 MC 版本、加载器和 mod id 生成这三个文件（`gradle.properties` 一并给出），加载器分别用 Fabric Loom、NeoGradle / ModDevGradle 或 Architectury 多模块写法。
- **生成 Wrapper**：把内置的 Gradle 9.8.2 Wrapper 文件（`gradlew`、`gradlew.bat`、`gradle-wrapper.jar`、`gradle-wrapper.properties`）写进工程，本机不装 Gradle 也能编译；一键准备会预下载发行包，避免首次编译卡在下载。已有 Wrapper 时会先问是否覆盖。

版本匹配：Fabric 的 `fabric-loom` 1.19.x 要求 Gradle 9.8.0 及以上，所以内置 Wrapper 用 9.8.2。工程里已有更旧的 Wrapper 时，编译会报 `requires at least Gradle X` / `Minimum supported Gradle version is X`；只要 X 不高于 9.8.2，工坊会自动把 Wrapper 重写成 9.8.2、装好对应发行包并重试一次。X 更高时才会提示你去降插件或手动升级。

`assets/wrapper/` 里的 Wrapper 文件来自 [gradle/gradle v9.8.2](https://github.com/gradle/gradle/tree/v9.8.2)，遵循 Apache-2.0，出处与许可见该目录下的 `NOTICE.txt`。

`assets/decompiler/vineflower.jar` 来自 [Vineflower 1.12.0](https://github.com/Vineflower/vineflower/releases/tag/1.12.0)，遵循 Apache-2.0；许可证与出处见同目录的 `LICENSE.txt`、`NOTICE.txt`。反编译只在本机运行，不会把 JAR 或生成的源码上传到任何服务器。

### 证书与代理

第一次用 Wrapper 时会下载 Gradle 发行包。如果日志里出现 `PKIX path building failed` / `unable to find valid certification path`，说明本机有程序在拦截 HTTPS：常见于 Watt Toolkit（SteamTools）的 GitHub 加速、公司代理，以及带 HTTPS 扫描的杀毒软件。它们会用自己的根证书重签流量，JDK 自带证书库不认，而 Windows 证书库里通常已经装了这张根证书。

工坊的处理方式：

- 检测到上述错误时，自动勾选「使用 Windows 证书库」并重试一次，通过 `JAVA_OPTS=-Djavax.net.ssl.trustStoreType=Windows-ROOT` 让 JVM 改用系统证书库。
- 这个开关也可以在编译运行页手动勾选，设置会保存到 `config.json` 的 `gradle_windows_certs`，之后所有 Gradle 任务都生效。
- 仍然失败时，先关掉加速器的 GitHub 加速，或把它的根证书导入 JDK 的 `lib/security/cacerts`。急着编译也可以手动下载对应 Gradle 发行包，解压后把 `bin` 目录临时加入 PATH，让工坊走系统 Gradle。

### NeoForge 依赖镜像

NeoForge 的 ModDevGradle 会从 `maven.neoforged.net` 拉 `neoform-runtime` 和一批构建工具。这个域名在国内经常出现 502、连接被拒或 DNS 污染，表现为编译到 `:createMinecraftArtifacts` 时失败。

工坊的处理方式：

- 启动编译前先探测 `maven.neoforged.net`；连不上时不打扰你，直接在本机 `127.0.0.1` 起一个 Maven 代理，用 Gradle init script 把 `maven.neoforged.net` 的仓库地址换到代理。
- 代理并发尝试 bmclapi、南邮、Gradle 插件门户、Stellar、明坤等镜像，谁先返回可用数据就用谁，不用再逐个等超时；文件缓存在 `data/mirrors/repo/`，下次编译直接命中。
- `net.neoforged:minecraft-dependencies` 在镜像上不存在，按 Mojang 的版本清单现场生成；`neoform-runtime` 的 `-all.jar` 用内置的 282KB 主 jar 合并依赖得到，不需要下载完整的合并包。
- `neoform-runtime` 运行时依赖 `fabric-loom-native`，这个工件在所有公开镜像上都取不到。程序内置一个只实现 `LoomNativePlatform` 三个静态方法的空壳 jar，合并进 `-all.jar`；它只在 NeoFormRuntime 排查文件锁时被调用，不加载本地库。
- 源的速度会按实测结果动态排序，连续失败的源短暂降权，恢复后自动放回；同一个文件换源时保留已下载的部分继续传。
- 临时超时返回 503 让 Gradle 重试，只有确认 404 才会记成“不存在”。
- 编译失败且日志里出现 `502 Bad Gateway`、`Connection refused`、`PKIX path building failed` 等关键字时，会自动启用代理并重试一次。

代理只监听本机回环地址，不对外提供访问；缓存的都是公开的 Maven 构件。

### 下载与更新加速

- **大文件分段**：JDK、Gradle 发行包和更新包不小于 8 MB 时，自动拆成多段用 6 条连接并行下载；断线按 `.part` 续传，合并后校验大小和 SHA-256。
- **自动更新**：GitHub Release 的 ZIP 先走 `gh-proxy.com` 加速，失败再回退直连；下载完仍然逐字节校验 SHA-256，校验失败会删掉重来。
- **Mojang 元数据**：版本清单、版本 JSON 和 `libraries.minecraft.net` 依赖都会在 piston-meta / Mojang 直连失败时改走 BMCLAPI，构建不会卡在元数据阶段。
- **JDK / Gradle 镜像**：JDK 依次尝试中科大 Adoptium、华为云 OpenJDK、Adoptium 官方；Gradle 使用华为云和腾讯云镜像，都失败才回官方地址。

## 工具轮数

设置页的「工具轮数上限」默认 32，可调到 999；填 0 表示不限制。大型工程可以调高，但轮数越多，模型调用次数、耗时和费用也越高。运行中随时可以点「停止」。

## 数据与安全

所有用户数据在程序目录下的 `data/`（可用环境变量 `DMA_DATA_DIR` 改位置）：

```
data/
  config.json        模型与界面设置
  memory.db          长期记忆（助手自动记录工程决策）
  knowledge.db       知识库（导入文件的正文与分片）
  connectors.json    连接器与请求头
  skills/            技能 Markdown
  projects/          默认工程目录（新建工程放在这里）
  projects.json      最近工程
  sessions/          每个工程的对话记录
  backups/           覆盖文件前的自动备份
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
- Windows 上走系统回收站（`SHFileOperation` + `FOF_ALLOWUNDO`），可以从回收站还原；回收站不可用时会移到 `data/trash/`，不会直接永久删除。
- 磁盘根目录、用户主目录、程序目录、数据目录以及包含这些目录的上级目录会被拒绝删除。
- 有助手生成任务或 Gradle 任务在运行时不能删除，需要先停止。

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
- `dist/DarkittenModAgent-<版本>.zip`：上传到下载站/GitHub Release 的更新包。
- `dist/mod_agent_release.json`：更新清单，包含版本、大小、SHA-256 与更新说明。

发布时把 ZIP 上传到 GitHub Release（标签 `mod-agent-v<版本>`），把清单放到站点根目录；清单里的 `download_url` 指向 Release 资源，启动器读取同一份清单即可下载和自动更新。

## 边界

- 「编译运行」页可以代跑 Gradle 和开发客户端，但不能判断游戏内表现；编译成功也不等于功能正常。
- Gradle 构建脚本会执行本机代码，只在自己信任的工程里运行。
- Mixin、自定义网络包、方块实体、世界生成等高风险改动会标注【⚠️人工复核】。
- 首次使用建议先在测试工程里验证，再合并到正式工程。
