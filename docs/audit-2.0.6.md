# 恶魔小猫工坊 2.0.6 全面检查报告

日期：2026-11-11

## 1. 为什么一次生成跑了 2740 秒还没结束

不是模型慢，也不是 Gradle 卡住，而是 **一次工具调用永远没有返回**。

用 py-spy 对当时进程（PID 93868）抓取调用栈，主线程停在：

```
SSLSocket.do_handshake (ssl.py:1372)
SSLSocket._create (server_hostname="cdn.jsdelivr.net")
HTTPSConnection.connect
...
fetch_url (connectors.py:226)
AgentWorker._execute (agent.py:1756)
AgentWorker._loop (agent.py:923)
```

- URL：`https://cdn.jsdelivr.net/gh/neoforged/NeoForge@1.21.1/.../DataMapHooks.java`
- 该域名在当前网络里被黑洞，TCP 能建立，TLS 握手永远没有响应。
- 代码里写的是 `urllib.request.urlopen(timeout=30)`，但这个超时只约束单次 socket 操作。在打包后的 Python 3.14 运行时里，TLS 握手绕过了它，实测卡了 50 分钟以上。
- 工具调用不返回 → Agent 循环停在这一步 → 界面耗时计数器继续跑，于是显示 2740s 还在“生成中”。

## 2. 核心修复

新增 `dma/netclient.py`，所有网络出口统一走这一层：

- 请求放进 daemon 工作线程，调用方按**总时限**等待，每 0.2 秒检查取消。
- 超时立即返回 `NetTimeout`，不再无限等待 TLS 握手/正文读取。
- 取消事件贯通到网络层，点“停止”能马上中断正在等待的请求。
- 某主机超时后记入 10 分钟黑名单，后续请求快速失败，避免对同一个黑洞反复重试。
- 新增 `open_stream()`，大文件下载（JDK / Gradle / 更新包 / Studio 资源）的连接和每次读取都受硬截止时间约束。

接入范围：

- `connectors.fetch_url`（工具层所有 HTTP 读取）
- `toolchain` 的下载探测、分段下载、单连接下载
- `studio_engine.util` 的下载
- `updater` 的更新清单读取
- `fetch_url` 自动把 `cdn.jsdelivr.net`、`raw.githubusercontent.com` 改写到 `api.github.com`，这两个域名在部分网络里完全不通。

## 3. 其他修掉的问题

1. **停止按钮形同虚设**：原来只置了一个标志，卡在系统调用里的线程根本收不到。现在取消传进网络层与工具执行；第一次点开始停止，再点一次可放弃等待并释放界面。
2. **旧任务信号污染新任务**：Worker 的信号改为按实例身份过滤；放弃等待后旧线程不会再把“完成/失败”写进新一轮对话。
3. **界面可能永远停在“生成中”**：定时器检测工作线程是否还活着，线程已死但信号没送达时主动收尾。
4. **后台线程直接改 Qt 控件**：编译/运行页的进程退出回调原来在后台线程里调 `_refresh_status()`，现在改为通过 Qt 信号回主线程。
5. **无进展空转**：新增 20 分钟看门狗，长时间没有写入文件或完成编译就停止并提示，不再无限转。
6. **长任务上下文无限增长**：原来的折叠只处理工具结果，模型每轮重新贴完整文件的输出从不折叠；现在超过预算时也会折叠较早的模型输出。
7. **启动失败未兜底**：Worker 构造或 `start()` 抛异常时恢复按钮状态并提示，不会留下半启动界面。

## 4. 验证结果

- 全量单元测试：`342 tests OK`（含新增 `tests/test_netclient.py` 11 项，以及取消、上下文折叠、UI 自愈回归）。
- 打包产物启动冒烟：退出码 0。
- 构建：`dist/DarkittenModAgent-2.0.6.zip`，57,046,002 字节。
- SHA-256：`eef0ec94bee61d3e3c159b4a1615db97c9bda35eca678f1e1c3c8fbd425c0162`。
- 清单：`dist/mod_agent_release.json`，版本 2.0.6。

## 5. 还需要注意

- 当前机器上仍有一个旧版实例在运行（PID 66972），它加载的是旧代码，必须退出后重新启动才会用上修复。
- 就地升级时**只替换 exe 和 `_internal`，保留 `data` 目录**；`data` 里有配置、工程、Gradle 缓存、知识库和记忆。
- 2.0.6 已发布：
  - GitHub Release：<https://github.com/LFELKEEA/darkitten-mod-agent/releases/tag/mod-agent-v2.0.6>
  - 下载站清单：<https://lfelkeea.github.io/darkitten-launcher/mod_agent_release.json>

## 6. 追加：2.0.7（第二次卡死）

2.0.6 发布后仍然出现“又卡死”。用 py-spy 抓 Python 栈没有发现业务线程异常，换 jstack 看 Gradle daemon，发现守护进程阻塞在 `SecureFileDownloader` 的 socket 读取上。

根因：

- `possession_excretion` 的 `build.gradle` 声明 `JavaLanguageVersion.of(21)`。
- 工坊只把自带 JAVA_HOME（JDK 17）交给 Gradle，Gradle 判定缺 21，于是去 Adoptium 自动下载 JDK 21。
- 该下载在部分网络里卡在 TLS/正文读取，构建十几分钟没有任何输出。

修复：

- 把所有检测到的 JDK 目录（`envcheck.java_homes()` / `find_java_homes()`）通过 `-Porg.gradle.java.installations.paths=...` 传给 Gradle，`D:\developer\Java\jdk-21` 这类常见安装位置也能被识别，不再只认 JAVA_HOME 里的 JDK 17。
- 追加 `-Porg.gradle.java.installations.auto-download=false`，从根上关闭 JDK 自动下载。
- 编译/运行前预读 `JavaLanguageVersion.of(N)` 与 `jvmToolchain(N)`，版本不匹配时立刻给出可读错误，不再让 Gradle 静默等待。
- `mcdev.gradle`、`mcdev.process`、`studio_engine.build` 三条 Gradle 执行路径统一接入。

验证：

- `gradlew build --dry-run`（NeoForge 1.21.1，JDK 21）2 秒返回 `BUILD SUCCESSFUL`。
- 全量单元测试：`346 tests OK`。
- 构建：`dist/DarkittenModAgent-2.0.7.zip`，57,047,849 字节。
- SHA-256：`cce0963d4db5985fb2299468f03ac584a7f4973f1b837a7c21b18c99f322f1b5`。
- 2.0.7 已发布：
  - GitHub Release：<https://github.com/LFELKEEA/darkitten-mod-agent/releases/tag/mod-agent-v2.0.7>
  - 下载站清单：<https://lfelkeea.github.io/darkitten-launcher/mod_agent_release.json>

## 7. 追加：2.0.8（runServer 反复启动）

2.0.7 解决了 JDK 自动下载，但用户反馈“一直在运行 runServer”。查日志发现服务端其实已经成功启动：

- `possession_excretion/run/logs/latest.log` 里 22:26:20 出现 `Done (3.586s)!`，25565 正常监听；
- 工坊却在等 39001 的游戏内探针，而这个工程是 NeoForge 1.21.1，`supports_probe()` 返回 false，探针永远不会出现；
- 等满超时后工坊杀掉 gradlew 进程树，再开始下一轮 runServer；`taskkill /T` 还漏掉了挂在 Gradle 守护进程下的游戏进程，导致 25565 上留下孤儿服务端。

根因有三层：

1. `build_project` 允许 task=runServer/runClient。它们是常驻任务，Gradle 不会自己结束，套在编译超时里必然空等到超时。
2. 没有探针的加载器仍然按探针端口判断就绪，启动成功后也会被判失败。
3. `run_dir_for()` 在 `run/` 与 `run-server/` 同时存在时优先 `run-server/`，而 ModDevGradle 实际使用 `run/`，eula 和端口写错了目录。

修复：

- `build_project` 拒绝 runServer/runClient，明确提示改用 `mcdev_prepare`。
- 无探针加载器改为识别 Gradle 输出里的 `Done (…)`、`For help, type`、`Backend library` 等启动标记；到截止时间进程仍存活就按已启动处理，不再误杀。
- `run_dir_for()` 优先 `run/`；`server-port` 从实际 properties 读取。
- 启动前检查实际端口占用，已有服务端时返回 `alreadyRunning`；同一会话重复 prepare 复用已跟踪进程。
- 停止时除了 taskkill 进程树，再按服务端监听端口找 PID 补杀，清掉 Gradle 守护进程下的游戏进程。

验证：

- 全量单元测试：`354 tests OK`。
- 新增 run 目录、端口读取、启动标记、netstat 解析、重复启动拦截测试。
- 现场用 `_pid_on_port(25565)` 能正确找到孤儿服务端 PID。

## 8. 追加：2.0.9（NeoForge Maven 502，开发端无法启动）

用户反馈 maven.neoforged.net 完全不可达、开发端起不来。现象是每次启动都在
`:createMinecraftArtifacts` 解析 `net.neoforged:neoform-runtime:1.0.24` 时失败：

- 直连 `https://maven.neoforged.net/releases/...` 返回 HTTP 502（Python urllib 与
  curl 一致，不是本机证书问题）；
- 同一构件从本地镜像 `http://127.0.0.1:55878/...` 取只要 8ms，缓存里已有 pom / jar；
- 但启动日志里 Gradle 仍然请求官方 URL。

根因：`LaunchManager.launch()` 直接拼
`gradlew runServer --console=plain`，只追加了 toolchain 参数；镜像初始化
（`mirrors.prepare_launch_env()` + `--init-script`）只接在
`mcdev/gradle.py:run_gradle()` 里。ModDevGradle 是在插件内部动态添加
`maven.neoforged.net` 的，工程 `build.gradle` 里的 repositories 拦不住它，
所以编译能过、启动开发端却必然 502。

修复：

- `LaunchManager.launch()` 在启动前调用 `_mirror_script()`；官方仓库不通时
  追加 `--init-script <data/mirrors/mirror.gradle>`，与编译路径行为一致。
- 启动失败且输出里包含依赖下载/证书错误时，强制 `force=True, fresh_port=True`
  再重试一次；主动探测被证书链误导时也不会把网络问题报成代码问题。
- 镜像模式使用 `gradle_mod.build_env(slow=True)`，与 `run_gradle` 的慢源超时一致。
- 启动默认超时从 180 秒提高到 600 秒（`LaunchManager` 与 `McdevService` 两处），
  NeoForge 首次 `createMinecraftArtifacts` 不再被 3 分钟误杀。
- 工程首次启动、`run/` 与 `run-server/` 都不存在时，`run_dir_for()` 返回 `run/`，
  与 ModDevGradle / Loom / Forge MDK 的默认行为一致。

验证：

- `launch-server-20261111-233726.log` 里出现
  `Starting Minecraft server on *:25566` 和 `Done (4.190s)!`，
  `launch()` 返回 `ready=true, mirrored=true`；停止后 25566 无残留监听。
- 启动命令包含
  `--init-script ...\data\mirrors\mirror.gradle`，日志显示
  `[镜像] 官方仓库直连不通：maven.neoforged.net` → 本地代理 `127.0.0.1:55879`。
- 全量单元测试：`356 tests OK`。

补充记录（本机环境限制，不是本次回归）：Windows 用户名含中文时，
Gradle 会把 worker classpath 写成 `@argfile`，JDK 21 读取非 ASCII 路径会失败，
表现为 `ClassNotFoundException: GradleWorkerMain`。正式安装目录
`D:\Darkitten Launcher\...` 不含非 ASCII 字符，不受影响；这也是验证时改用
ASCII 路径副本的原因。
