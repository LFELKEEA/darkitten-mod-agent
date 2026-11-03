# 暗猫模组工坊（Darkitten Mod Agent）

只写 Minecraft Java 模组的独立桌面 Agent。基于 PySide6，自带对话工作台、技能、连接器和记忆库，可从下载站、GitHub 与 Darkitten 启动器获取，并支持自动更新。

当前版本：`0.1.0`

## 功能

- **Fabric / NeoForge / Architectury**：创建工程、增量修改、崩溃排查、跨版本移植。
- **三项硬性前置**：MC 版本、加载器、Mod ID，缺一项就不会开始写代码。
- **完整文件交付**：按工程相对路径输出完整文件，不用“其余不变”省略。
- **待写入确认**：生成的文件先进入右侧列表，确认后才落盘；覆盖前自动备份。
- **技能**：内置 10 个模组开发工作流，可停用、改写、恢复默认或新建自定义技能。
- **连接器**：Modrinth、Fabric Meta、GitHub API 开箱可用，支持自定义 HTTP 接口与 `${ENV}` 密钥引用。
- **记忆库**：SQLite 本地存储，按工程召回，支持标签、搜索、导出 JSON。
- **桌面快捷方式**：首次启动询问是否创建。
- **自动更新**：读取 `mod_agent_release.json`，下载 ZIP、校验 SHA-256，退出后由后台脚本替换程序文件并重启。

## 直接运行（源码）

```bash
python -m pip install -r requirements.txt
python main.py
```

Windows 上也可以双击 `main.py`（需要已安装 Python 与依赖）。

## 使用流程

1. 打开程序，在右上角点“新建工程”，填写 MC 版本、加载器、Mod ID、依赖和补充要求。
2. 在输入框描述功能，或点“插入需求单”套用模板；Ctrl+Enter 发送。
3. 助手会先输出目录树和完整文件，文件进入右侧“待写入文件”。
4. 检查后点“写入全部”，或先双击预览单个文件。
5. 到工程目录执行 `./gradlew build` / `./gradlew runClient`，把报错贴回来继续修。

## AI 通道

设置页支持三种：

- **免费公共通道**：默认，开箱可用，稳定性一般。
- **OpenAI 兼容接口**：填写 Base URL、模型、API Key。
- **本地 Ollama**：默认 `http://127.0.0.1:11434/v1`，例如 `qwen2.5-coder:7b`。

API Key 只保存在本机 `data/config.json`，不会上传到本项目服务器。建议使用最低权限的 Key。

## 数据与安全

所有用户数据在程序目录下的 `data/`（可用环境变量 `DMA_DATA_DIR` 改位置）：

```
data/
  config.json        模型与界面设置
  memory.db          记忆库
  connectors.json    连接器与请求头
  skills/            技能 Markdown
  projects.json      最近工程
  sessions/          每个工程的对话记录
  backups/           覆盖文件前的自动备份
  updates/           更新包与更新日志
```

写入约束：

- 只允许写工程目录内的相对路径，拒绝绝对路径、盘符和 `..`。
- 单文件超过 2,000,000 字符拒绝写入。
- 覆盖已有文件前复制到 `data/backups/<工程>/<时间戳>/`。
- 自动写入默认关闭，需要在设置里主动开启。

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
- `dist/DarkittenModAgent-0.1.0.zip`：上传到下载站/GitHub Release 的更新包。
- `dist/mod_agent_release.json`：更新清单，包含版本、大小、SHA-256 与更新说明。

发布时把 ZIP 上传到 GitHub Release（标签 `mod-agent-v<版本>`），把清单放到站点根目录；清单里的 `download_url` 指向 Release 资源，启动器读取同一份清单即可下载和自动更新。

## 边界

- 助手不能替你真正运行 Gradle、启动游戏或验证运行结果；编译成功也不等于游戏内正常。
- Mixin、自定义网络包、方块实体、世界生成等高风险改动会标注【⚠️人工复核】。
- 首次使用建议先在测试工程里验证，再合并到正式工程。
