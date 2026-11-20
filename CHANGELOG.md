# Changelog

## 3.6.0 — 2026-11-20

模型库开始按工程归档，并接上社区模型/皮肤；后台客户端也能在应用里直接看画面。

- **按工程查看**：模型库顶部新增工程下拉框，列表来自最近打开的工程；切换只影响模型库，不会立刻抢走对话页的工程。点「让 AI 按描述修改」时，如果对话页还停在别的工程，会先自动切换，Agent 忙时则提示等这一轮结束。
- **在线模型与皮肤**：新增 `dma/artresources.py` 与资源浏览器窗口，可搜索 Modrinth 资源包、LittleSkin 皮肤/披风、YSM 社区模型；支持预览、下载到 `reference/assets/<来源>`，皮肤还能一键套到当前实体/方块/物品的贴图上。
- **浏览器兜底**：Blockbench 画廊、MCModels、Planet Minecraft 没有稳定公开接口，直接打开官方搜索页，不抓取、不绕过站点限制。
- **Agent 可直接参考**：新增 `online_assets` 工具（sources / search / download）。AI 在做模组时可以主动找造型、贴图与资源包结构，下载落到工程参考目录，并提示遵守原作者许可。
- **实时画面**：后台客户端仍运行在隐藏桌面，用户桌面不出现游戏窗口；新增应用内画面窗口，通过探针 `/frame`（glReadPixels）拉帧，可随时打开/关闭，并可勾选「操控画面」把鼠标键盘换算成游戏内动作。
- **工具总数**：116。
- **探针指纹**：源码指纹只看源码与构建脚本，跳过 `build/`、`.gradle/` 等生成目录；编译期间被占用、临时不可读的文件不再让缓存判定抛异常。
- **回归测试**：新增在线资源来源/归一化/下载白名单与 Agent 工具调用用例；全量 614 项测试通过。

## 3.5.0 — 2026-11-20

后台跑游戏这件事做到了真正“零窗口”，同时把模组外观做成能看、能改、能画的工作台。

- **隐藏桌面**：后台客户端不再只靠隐藏窗口，而是用 `CreateDesktopW` 建独立桌面，并由自写的 `CreateProcessW` 传入 `lpDesktop` 启动；窗口从创建第一刻起就不在用户桌面。`subprocess.STARTUPINFO` 不暴露 `lpDesktop`，之前的 `setattr` 实际无效，这次已修正。
- **不碰 Gradle daemon**：后台客户端追加 `--no-daemon`，避免复用用户桌面上的常驻 daemon，游戏进程树完整继承隐藏桌面；停止时 `CloseDesktop` 释放桌面句柄。
- **实测零闪现**：服务端 + 客户端整轮启动共 16933 次窗口采样（10ms 间隔），用户桌面可见窗口 0 个；此前仍有 23–56ms 的窗口闪现。游戏窗口、GLFW 辅助窗口和 OpenGL 窗口全部位于隐藏桌面。
- **默认创造模式**：`server.properties` 强制创造，假玩家与客户端探针默认创造；后台冒烟 6/6 通过：创造出生、行走 2.0 格、放置石头、挖掉、僵尸伤害 20.0 → 19.81。
- **后台操作人物**：新增 `game_bot_playtest`（纯服务端 AI 假玩家一键盘冒烟）、`game_playtest`（带断言的完整玩法测试）与 `game_window`（查询/隐藏客户端窗口）；工具总数 115。
- **模组模型库**：新增 `dma/modcatalog.py` 与模型库页面，统一扫描实体、方块、物品、群系、维度，展示 3D 模型、贴图、描述与标准字段；用户的描述、自定义字段、替换模型/贴图保存在 `.dma/mod_library.json`，不覆盖扫描结果，也不会擅自改模组代码。
- **手动模型编辑**：新增 `dma/ui/model_editor.py`，支持 Blockbench `.bbmodel`、GeckoLib/Bedrock `.geo.json` 与 Java 模型 JSON；可选中立方体修改位置、尺寸、UV 并写回原文件，带 `parent` 的模型也能从零加立方体。
- **手动绘图**：新增 `dma/ui/paint_page.py`，参考 Blockbench Paint 的像素画布 + UV 图集 + 3D 预览；铅笔、橡皮、油漆桶、取色、直线、画笔大小、撤销/重做与色板齐全，可点 3D 模型面反查 UV 上色，实体带原版 64×64 人形 UV 分区提示，方块提供等距三面预览。
- **回归测试**：新增模型库扫描与持久化、模型编辑器、绘图工具、后台启动与假玩家冒烟用例；全量 594 项测试通过。

## 3.4.6 — 2026-11-19

修掉“点了停止没反应”，并给生物、物品图标和方块加上“多方案选择”工作流。

- **方案选择**：新增 `design_studio` 工具和选择窗口。创建生物实体、物品图标或方块外观时，一次生成 3 个候选（可调 2-8 个），带预览图、贴图、模型与调色板。
- **交互**：用户可以在窗口里点选方案、补一段更详细的描述后重新生成、保留当前模型只换图案，或直接上传自己的 PNG / JSON / `.bbmodel`。
- **落地**：选定后自动把贴图、物品/方块模型、方块状态写入 `src/main/resources/assets/<ns>/...`；实体输出 GeckoLib `.geo.json` 起始模型，并在结果里返回写入路径供后续接线。
- **按需生成**：生成与重绘完全离线，使用标准库手写 PNG，不依赖 Pillow 或外部绘图服务。
- **取消信号贯通**：`AgentWorker` 启动时把本轮取消事件绑定到共享的 `mcdev` 服务，结束时恢复；正在等待开发端就绪、安装探针、执行场景步骤的调用会立即退出，不再等到 15 分钟超时。
- **启动阶段可停止**：`LaunchManager.prepare` 在等待服务端/客户端、握手进服期间检查取消；取消后自动停止本次新启动的开发端，不会留下后台游戏进程和 Gradle daemon。
- **游戏等待可停止**：`game_setup` / `game_new_world` 等待进入世界、`game_language` 等待语言生效、`game_test` 执行步骤都会检查取消，最长 120 秒的存档等待可以随时中断。
- **界面**：对话运行期间不再整体禁用“运行”面板，只锁启动按钮；“停止全部”和“刷新状态”始终可用；放弃等待时会在后台清理本次任务启动的开发端。
- **回归测试**：新增取消信号绑定/恢复、只清理本次启动的进程、取消后测试步骤立即结束等用例；另覆盖三类设计方案的生成、只改图案、上传与资源落地。

## 3.4.5 — 2026-11-19

修正 3.4.4 的测试环境缺陷：默认 `peaceful` 难度不会生成敌对生物，导致无法测试怪物、夜间行为与伤害。

- **默认难度**：`create_world`、`game_setup`、`game_new_world`、`game_test` 的 `world` 步骤默认难度从 `peaceful` 改为 `normal`；显式传 `peaceful` 时结果里附带提示。
- **世界动作** `game_control` 新增：
  - `difficulty`：`/difficulty` 后回读 `state.difficulty`，未生效直接报错（通常是没有开作弊）。
  - `gamemode`：切换到 survival / creative / adventure / spectator，回读 `gameMode` 确认；测怪物伤害需要 survival。
  - `time`：支持 `day` / `noon` / `night` / `midnight` / `sunrise` 或 0-23999 刻，回读 `dayTime` 确认。
  - `weather`：clear / rain / thunder，可选持续秒数；探针不暴露天气，返回 `verified=false` 并说明。
  - `spawn`：按实体 ID 在玩家周围生成 1-32 个实体，可调距离与散布；生成后扫描附近实体并按类型核对。处于 `peaceful` 时会先把难度提升到 `normal`，避免怪物立即消失。
- **测试步骤**：`game_test` 新增 `difficulty` / `gamemode` / `time` / `weather` / `spawn`，可直接编排敌对生物测试。
- **提示词与工具描述**：补充“测敌对生物不要用 peaceful”的规则和完整示例，`game_control` 增加 `mob` / `count` / `distance` / `spread` / `time` / `weather` / `duration` 参数。
- **回归测试**：扩展本地假探针，覆盖难度切换、时间/模式切换、peaceful 自动抬难度生成、测试步骤编排。

## 3.4.4 — 2026-11-19

让 Agent 直接进开发客户端操作，并用截图复盘，不再停在“编译通过”。

- **游戏控制层** `dma/mcdev/game.py`：
  - `game_setup`：切换语言（默认 `zh_cn`）并新建存档，等待真正进入世界；已在存档内时不重建。
  - `game_new_world` / `game_language`：单独建存档或换语言，支持 normal / flat / amplified / large_biomes / single_biome 与种子、模式、难度、作弊。
  - `game_control`：视角、看向坐标/实体、传送/相对移动/按键行走、按/放/长按按键、攻击、使用物品/方块、破坏与挖掘、聊天、执行命令、按文字点击、输入文本、等待界面、关闭界面、背包、截图、读取状态。
  - `game_observe`：一次拿到状态、玩家与附近实体、UI 控件、日志尾部，并截图做确定性画面体检。
  - `game_test`：按步骤执行 setup / language / world / control / screenshot / observe / wait / assert，断言失败即停并返回失败步骤与上下文。
- **Forge 1.20.1 探针扩展**：新增 `/language`、`/world/create`、`/player/state`、`/player/look`、`/player/move`、`/player/key`、`/player/attack`、`/chat/send`、`/ui/click_text`、`/ui/set_text`、`/ui/wait`；`/status` 与 `/ui/snapshot` 增加语言、坐标、世界与可编辑控件信息。存档使用原版 `WorldOpenFlows.createFreshLevel`，不走脆弱的菜单自动化。
- **截图复盘**：截图后调用 `vision.review(style="screenshot")`，检查尺寸、颜色数、边缘密度与对比度，能识别黑屏、空场景和不可读 UI。
- **跨加载器兜底**：客户端启动前自动把 `options.txt` 的 `lang` 固定为 `zh_cn`（保留其他设置）；没有探针的加载器可按窗口 PID 截图并做同一套体检，但玩家控制与断言仍只在 Forge 1.20.1 可用。
- **工具协议**：新增 6 个 `game_*` 工具，提示词补充交互式验证流程；工具总数 95 → 101。
- **回归测试**：新增本地假探针覆盖语言归一化与生效等待、建存档等待、组合观察、断言通过/失败停止、探针缺失报错，以及 `options.txt` 语言预置的替换与幂等。

## 3.4.3 — 2026-11-18

修掉新装环境下 Fabric 源码索引第一次不可用的问题，并让源码同步自动化。

- **Fabric sources 解析兜底**：本地版本目录还没同步时，`dev_docs` / `api_lookup` 会直接向 Fabric meta 查询当前稳定加载器版本，不再因为目录为空返回“没有 sources.jar”。已用真实 `fabric-loader-0.20.1-sources.jar` 验证：下载 0.87 MB，索引 233 个类 / 966 个方法。
- **源码自动同步**：`build.py` 打包成功后自动调用 `tools/sync_github.py`，把源码、README、CHANGELOG 推到 `LFELKEEA/darkitten-mod-agent`；设置 `DMA_NO_SYNC=1` 可跳过。同步前会拒绝本地配置与运行数据，并扫描常见密钥格式。
- **同步脚本**：支持 `--dry-run` / `--no-push` / 自定义提交说明；遇到校园网 DNS 投毒或 Schannel 吊销检查失败时自动使用可达 IP 与进程级 TLS 兼容参数，不修改全局 Git 配置。

## 3.4.2 — 2026-11-18

参考开源 MC 模组 Agent 的确定性调度、契约冻结与离线签名索引，把已有内核真正接到 Agent 循环里。

- **任务图工具**：新增 `workflow_plan` / `workflow_status` / `workflow_next` / `workflow_complete` / `workflow_fail` / `workflow_skip` / `workflow_reopen`。模型只负责决策，任务状态由 `.dma/workflow.json` 推进；`workflow_complete` 会先跑验收门禁，缺少 build / lint / file / runtime 证据时返回阻断项，`force` 仅作为显式人工覆盖。
- **需求合并**：`workflow_plan` 把新需求合并进 `.dma/spec.json`，补充缺失功能并重建 DAG，同时保留已完成任务状态，避免重复规划把进度清零。
- **公共契约冻结**（`dma/kernel/contract.py`）：`contract_freeze` 把 public/protected 类、方法、字段与资源清单写入 `.dma/contract.json`；`contract_check` 报告被删除的类/方法/字段、方法签名变更、父类/接口变化与资源缺失。新增允许，删除和改签名视为破坏。
- **离线 API 签名索引**（`dma/apiindex.py`）：从缓存或按需下载的 `sources.jar` 建立紧凑索引，`api_lookup` 直接返回类、方法参数、返回类型、字段类型与 static 信息；索引按 jar 时间戳缓存，后续查询不再扫描整包。
- **提示词与工具协议**：新增强制工作流——先 `workflow_status` / `workflow_next`，完成节点必须过 `workflow_complete`；重构前 `contract_freeze`、重构后 `contract_check`；查 API 优先 `api_lookup`，需要实现细节再 `dev_docs lookup`。工具总数 95。
- **回归测试**：新增契约审计、API 索引、工作流门禁与需求重规划测试。

## 3.4.1 — 2026-11-18

修掉开发中心版本选择仍会滚动改值、快速生成版本列表不全的问题。

- **开发中心改为纯点选**：版本中心的 Minecraft / 加载器、开发资料的加载器、模组学习与智能参考的加载器 / Minecraft 全部换成点选下拉，滚轮经过不再改值。
- **开发中心版本不再截断**：优先读取在线正式版本清单，再合并本地目录，去掉原来的 120 条 / 40 条上限；即使启动器检测失败也会继续加载版本。
- **快速生成拉取真实版本清单**：按所选加载器在线获取该加载器实际发布过构建的全部 Minecraft 版本（例如 Fabric、Forge、NeoForge 各自完整列表），切换加载器自动重载并尽量保留当前选择；离线时退回完整内置版本表，不再只有 9 个。
- **新工程兜底表同步扩全**：内置版本表补齐 26.3、26.2、26.1.x、1.21.11–1.21.3 等版本。

## 3.4.0 — 2026-11-18

深度强化 Agent 的版本事实、架构参考与多版本工程能力。

- **版本事实层**（`dma/modfacts.py`）：新增 `version_facts` 与 `project_fact_check`。覆盖 Java 版本、元数据文件名、数据目录单复数、1.21.4+ 物品定义、映射与 ID API、构建插件；`project_fact_check` 会扫描工程并输出冲突文件与修复动作，`lint_project` 也接入同一套事实检查。
- **跨版本修正**：1.21+ 用单数数据目录；1.21.4+ 需要 `assets/<ns>/items`；NeoForge 1.20.5+ 用 `neoforge.mods.toml`；26.x 未混淆、非 remap，并使用 `Identifier.fromNamespaceAndPath`；1.21.2–1.21.8 使用 `ResourceLocation.fromNamespaceAndPath`。
- **大型模组架构参考**（`dma/archref.py` + `arch_ref`）：提炼 Create、GregTech Modern、Mekanism、Botania 的注册分层、材料/RecipeMap、多方块、能力 API、配置、游戏内文档与渲染分层模式，支持按玩法检索；只学结构，不复制源码。
- **多版本 / 多加载器**（`dma/multiversion.py` + `multiversion_plan`）：在 Stonecutter、Architectury、MultiLoader、Modstitch、Stonecraft 之间自动选型，输出目标矩阵、目录骨架、官方插件坐标、迁移步骤与逐目标验证要求。
- **调试闭环**：崩溃分类新增 Java class 版本（61/65/69 → 17/21/25）、映射改名、Access Widener/Transformer、元数据文件名、数据生成与配方错误；编译提示同步补充 26.x 与 Identifier 改名说明。
- **技能与计划**：新增 version-facts、multiversion、access-widener-transformer、gametest、architecture-patterns、publishing-ci 六个内置技能；`large_mod_plan` 新增 facts 阶段，reference 阶段加入 `arch_ref`，spec 阶段加入 `multiversion_plan`；工具总数 85。
- **提示词**：新增强制顺序——先 `version_facts` / `project_fact_check`，再 `dev_docs` / `mod_reference` / `arch_ref`，最后动手；不确定的版本差异必须在线核实，禁止把猜测写成事实。

## 3.3.3 — 2026-11-18

- **版本与 API 改为纯点选列表**：新建工程的 MC 版本、加载器版本、Fabric API 不再是可以输入文字的编辑框，改成和「加载器」一样的下拉列表：点一下展开，点选一项生效，滚轮经过不改值。列表仍来自在线目录并合并启动器实例。
- **快速生成页同步调整**：一键生成里的加载器、MC 版本、规模也改成同样的点选下拉，滚轮不再误改；MC 版本只列出骨架真实支持的版本。

## 3.3.2 — 2026-11-18

修掉新建工程里滚轮误改版本的问题，并把下载直链改为站点托管。

- **版本与 API 只按选择切换**：MC 版本、加载器、加载器版本、Fabric API、运行端、任务类型都改成滚轮不改值的下拉框。鼠标滚轮经过时不会再把 26.3 滚成别的版本，只有点开列表或键盘选择才切换；可编辑输入保持不变。
- **下载直链指向站点**：`mod_agent_release.json` 的 `download_url` 现在指向站点上的 ZIP，不再依赖 GitHub Release 是否已创建，避免网站按钮 404。

## 3.3.1 — 2026-11-18

修掉“更新日志点了没反应、Forge / NeoForge / Quilt 永远是空的”，并把新建工程从固定 10 个版本扩展到完整版本目录。

- **更新日志真正联网同步**：开发资料的「刷新更新日志」以前只重读本地缓存，现在会实际拉取；同步完成后自动刷新列表并显示来源。
- **加载器版本记录补齐**：Fabric 走 GitHub Releases；Forge / NeoForge / Quilt 没有 Release 时自动回退到 Maven 元数据（优先 BMCLAPI / 高校镜像），再失败才用 GitHub Tags。Forge / NeoForge / Quilt 现在都能看到最近 12 个版本。
- **新建工程读取完整版本目录**：MC 版本不再写死 10 个，改为读取在线 Mojang 清单（正式版 + 快照），并合并启动器里已安装的实例；列表可编辑，任何版本都能直接输入。
- **加载器与依赖版本可选**：新增「加载器版本」下拉（Fabric / Quilt 全量、Forge / NeoForge 按当前 MC 过滤），Fabric 工程新增「Fabric API」下拉；两者都会写入工程 `.dma/project.json` 并进入系统提示，避免实现时猜版本。
- **Quilt 工程**：新建工程允许选择 Quilt；没有内置骨架时由 Agent 按官方模板生成构建脚本。
- **对话框联网刷新**：新建工程里可直接「联网刷新」Mojang 与四家加载器清单，不阻塞界面。
- **自动参考覆盖更全**：对话任务开始前的自动参考从 1 个模组提高到 2 个，代码片段上限提高到 8 条；仍然只读、不写入用户工程。

## 3.3.0 — 2026-11-17

工坊改为在线优先：程序本体保持轻量，开发环境和资料从网站与镜像按需拉取；Agent 在做功能前会自动联网找同类模组、反编译并阅读实现。

- **在线资源层**（`dma/resources.py`）：新增 `resource_ensure`，按 jdk / gradle / catalog / mdk / sources / docs 在线准备环境。官方源优先，失败自动切换华为、腾讯、中科大与 BMCLAPI 镜像；所有下载落在程序 `data` 目录。发布清单附带 `bootstrap.json`，明确每个资源的来源。
- **在线开发资料**（`dma/devdocs.py`）：按加载器直接获取 `sources.jar`——Forge / NeoForge 走版本目录，Fabric / Quilt 走官方 Maven——在本地解包检索类、方法与注解；本地没有源码时回退 GitHub 代码搜索，不再依赖预置文档。
- **智能参考管线**（`dma/reference.py`）：从需求识别实体、方块、GUI、世界生成、渲染、Mixin、网络、数据、配方、命令、配置、药水效果、村民、任务、皮肤、社交、载具、存储等 19 类机制，自动搜索 Modrinth、下载合适版本、反编译并建立索引，再把相关代码片段作为只读参考注入任务。
- **自动参考默认开启**：`auto_reference` 配置与对话页「自动参考」开关；反编译源码只读、不写入用户工程，始终附带许可证提示。
- **大型模组规划器**（`dma/planner.py`）：`large_mod_plan` 按 small / medium / large / epic 生成阶段、任务与包结构；large 目标为 40 个类 / 10 个包 / 8 个阶段 / 16 项测试。这里的“2G 标准”指大型应用的工程能力，不是安装包体积。
- **Agent 工具**：新增 `mod_reference`（auto / search / prepare / snippets / status）、`dev_docs`（lookup / sources / official / status）、`resource_ensure`、`large_mod_plan`，工具总数 81。系统提示要求先 `resource_ensure` / `dev_docs` / `large_mod_plan`，再写代码。
- **开发中心 UI**：新增「智能参考」页与「在线环境」面板；可查看 JDK / Gradle / 源码构件状态、一键在线准备、自动搜索反编译并展示片段、打开参考源码目录。
- **发布方式**：`python build.py` 只打轻量在线版，并输出 `mod_agent_release.json`（`kind: online`、`bootstrap_url`、`online_resources`）与 `mod_agent_bootstrap.json`；`build_full.py` 改为必须显式 `--offline` 才构建内置缓存的离线版。

## 3.2.0 — 2026-11-17

工坊不再是一个孤立的代码助手：它和启动器连成一套开发环境，自己维护版本清单与开发资料，并能把成熟模组拉下来反编译学习。

- 新增「开发中心」页（`dma/ui/center_page.py`），四个分区：版本中心、开发资料、测试中心、模组学习，所有操作都在工坊内完成，不用来回切窗口。
- 启动器联动桥（启动器 `darkitten/bridge.py`）：只监听 `127.0.0.1`，端口随机、每次启动生成随机 token，令牌写在 `bridge.json`。工坊可读取启动器已安装版本、Mojang 版本下载地址（含 BMCLAPI 镜像）、Modrinth 搜索结果与版本，并可请求启动器启动指定实例。
- 工坊侧 `dma/launcher_bridge.py`：优先走联动桥；启动器没开时回退读取 `config.json` 与 `versions/`，仍能列出实例，不会因为启动器关闭就整页空白。
- 新增开发生态目录（`dma/catalog.py`）：持续同步 Mojang 版本清单与 Java 要求、Fabric / Quilt 的游戏与加载器版本、Yarn / Quilt Mappings、Forge / NeoForge Maven 版本、Gradle 当前版本、Adoptium JDK、四家加载器的 MDK 与 Javadoc 地址，以及更新日志。后台每 6 小时自动同步，断网时保留上一次成功数据。
- 新增模组学习（`dma/modlibrary.py`）：按加载器和 MC 版本搜索 Modrinth，下载 JAR（SHA-1 校验、上限 512MB），用 Vineflower 反编译成源码和 `INDEX.md`，只写入本机 `data/library`，不碰用户工程。
- 新增「全量离线发行版」构建脚本（`build_full.py`）：把程序本体、JDK 21、Gradle wrapper、依赖缓存、NeoForm / Loom 缓存、镜像缓存和生态目录一起打包，解压后首次构建不需要再下载几个 GB 环境；按 core / jdk / gradle / assets 分组生成不超过 1900MB 的分卷，并输出 SHA-256 清单。
- 启动器侧新增模组学习入口：模组页「模组学习」可搜索 Modrinth、下载到学习库、反编译成源码并打开目录；反编译支持 `META-INF/jarjar` 与 `META-INF/jars` 内的嵌套 JAR。
- Agent 新增 11 个工具：`catalog_sync`、`catalog_query`、`catalog_download`、`mod_search`、`mod_download`、`mod_decompile`、`launcher_versions`、`launcher_downloads`、`launcher_vanilla`、`launcher_launch`、`launcher_status`，对话里可以直接查版本、下 MDK / Javadoc、拉模组反编译、启动测试实例。
- 修复联动桥端口在 descriptor 中显示为 0 的问题；新增启动器桥 10 项端到端测试与工坊 17 项目录 / 模组库 / 联动测试。
- Forge / NeoForge 没有 `javadoc.jar` 时自动回退到 `sources.jar` 并附官方文档站；NeoForge MDK 改从 NeoForgeMDKs 仓库按版本获取；Quilt 所有 loader 被标记为非 stable 时回退到最新发布版，避免资料下载 404。
- 全量发行版改用最终目录统计分组体积，`--reuse` 增量构建也能在清单里看到 JDK / Gradle / 镜像 / 目录的真实占用。

## 3.1.0 — 2026-11-13

把编译错误变成结构化的修复清单，并在每次编译后固化工程契约，解决大工程“写错→编译失败→猜错→又写错”。

- 新增编译驱动实现引擎（`dma/compile_feedback.py`）：编译失败时不再把上百 KB 的 Gradle 日志整段丢给模型猜，而是解析成精确到「文件:行 + 错误类型 + 中文修复提示 + 工程里真实存在的候选类名 + 错误分布」的修复清单，几十个类互相引用时也能按清单定位，不再陷入盲猜死循环。
- 契约先行：每次编译成功后附一份「工程当前已声明的类（按包分组）+ 已注册 id」快照，模型写下一个类时按此引用，避免类名 / 包名 / 注册 id 前后对不上——这是大工程最常见的编译错误来源。

## 3.0.7 — 2026-11-13

修复只读工具把“45 分钟无进展”保护架空导致的空转。

- 新增只读连续计数：连续 8 次 `list_files` / `read_file` / `search_text` 等只读调用、期间没有任何写入时，主动打断并注入提示，要求模型立刻用 `write_file` 写第一个文件。此前只读工具会不断刷新进展时间戳，45 分钟保护永远不会触发；`max_steps=0` 无限步数时会陷入只读死循环。任何写入（含自动写入）都会把计数清零。
- 测试：看门狗用例屏蔽 wrapper 自愈里的 Gradle 发行版预装，避免单元测试触发真实的数百 MB 下载。

## 3.0.6 — 2026-11-13

验收从“看文件在不在”升级为“读文件内容”，占位实现会在验收阶段被拦下。

- 新增「机制语义自检」检查器（`dma/kernel/checks/mechanism.py`）：不再只看 `mixins.json` 是否存在，而是解析其内容——`mixins` / `client` / `server` 全空视为占位实现并判 error；换肤机制缺少渲染器类，或渲染器恒返回单一固定纹理（不按皮肤 id 切换）同样拦截；聊天框 / 物品框机制没有 `extends Screen` / `ScreenHandler` 的类也报 error。从根源堵住“spawn 个实体 + 空 `mixins.json` 冒充做完”。

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
