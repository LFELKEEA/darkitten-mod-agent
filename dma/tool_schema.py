"""JSON schemas for native function calling (TOOL-01).

The text protocol (``​```tool``) stays available as a fallback for endpoints
that do not implement OpenAI-style tools.  These definitions are the default.
"""

from __future__ import annotations

from typing import Any


def _fn(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties or {},
                "required": list(required or []),
                "additionalProperties": False,
            },
        },
    }


_PATH = {"type": "string", "description": "工程内相对路径，禁止绝对路径和 .."}
_TEXT = {"type": "string"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _fn("list_files", "列出工程目录", {"path": _PATH, "depth": {"type": "integer"}}),
    _fn("read_file", "读取文件；支持 offset/max_chars 分片和 since_hash 跳过未变化内容",
        {"path": _PATH, "offset": {"type": "integer"}, "max_chars": {"type": "integer"},
         "since_hash": {"type": "string", "description": "上次读取返回的哈希；未变化时只返回提示"}},
        ["path"]),
    _fn("search_text", "在工程内搜索文本", {"query": _TEXT, "limit": {"type": "integer"}}, ["query"]),
    _fn("edit_file", "修改文件：优先用 symbol/method 做 AST 定位，其次用 old 精确替换",
        {"path": _PATH, "old": _TEXT, "new": _TEXT,
         "symbol": {"type": "string", "description": "类名、方法名或 Class.method"},
         "member": {"type": "string", "description": "要插入的新成员源码"},
         "replace": {"type": "string", "description": "symbol 模式下替换整个方法体/符号"},
         "mode": {"type": "string", "enum": ["body", "replace", "insert", "auto"]}},
        ["path"]),
    _fn("write_file", "新建或整体重写文件", {"path": _PATH, "content": _TEXT}, ["path", "content"]),
    _fn("pixel_art", "生成像素纹理 PNG",
        {"path": _PATH, "palette": {"type": "object"}, "rows": {"type": "array", "items": {"type": "string"}}},
        ["path", "palette", "rows"]),
    _fn("scaffold_project", "用受控模板生成工程骨架",
        {"loader": {"type": "string", "enum": ["Forge", "Fabric", "NeoForge"]},
         "mod_id": _TEXT, "name": _TEXT, "package": _TEXT, "dest": _TEXT,
         "mc_version": _TEXT, "version": _TEXT, "overwrite": {"type": "boolean"}}),
    _fn("build_project", "执行有限的 Gradle 任务；runServer/runClient 请用 mcdev_prepare",
        {"task": {"type": "string"}, "timeout": {"type": "number"}}),
    _fn("lint_project", "运行 176 项静态检查",
        {"level": {"type": "string"}, "checker": {"type": "string"},
         "fail_on": {"type": "string", "enum": ["error", "warning", "info"]}}),
    _fn("symbol_query", "查询类/方法/字段/调用链/事件监听者",
        {"query": _TEXT, "kind": {"type": "string", "enum": ["auto", "class", "method", "field", "callers", "listeners", "overrides"]},
         "limit": {"type": "integer"}}, ["query"]),
    _fn("mapping_translate", "在 Yarn / Mojmap / SRG 之间翻译类名或方法名",
        {"name": _TEXT, "from": {"type": "string"}, "to": {"type": "string"}, "kind": {"type": "string"}},
        ["name"]),
    _fn("impact_analysis", "分析改动影响面（注册项、资源、测试、规则）",
        {"paths": {"type": "array", "items": {"type": "string"}}, "git": {"type": "boolean"}}),
    _fn("log_parse", "解析崩溃日志：剥离框架栈、分类、聚类",
        {"text": _TEXT, "path": _PATH, "limit": {"type": "integer"}}),
    _fn("snapshot", "创建工程快照", {"label": _TEXT, "note": _TEXT}),
    _fn("snapshot_list", "列出快照和当前基线"),
    _fn("rollback", "回滚到指定快照或最近可编译基线",
        {"snapshot": _TEXT, "dry_run": {"type": "boolean"}}),
    _fn("asset_lint", "检查模型、贴图、音效、语言文件的一致性",
        {"path": _PATH, "kind": {"type": "string"}}),
    _fn("texture_gen", "结构化生成像素贴图并校验规格",
        {"path": _PATH, "kind": _TEXT, "palette": {"type": "string"}, "size": {"type": "integer"}}),
    _fn("vision_review", "对贴图/模型预览/游戏截图做结构化美术评审",
        {"path": _PATH, "kind": {"type": "string", "enum": ["item", "block", "screenshot"]}}),
    _fn("blockbench", "生成或导出 Blockbench .bbmodel",
        {"action": {"type": "string", "enum": ["create", "export", "validate", "load", "preview"]},
         "path": _PATH, "shape": {"type": "string"}, "size": {"type": "array", "items": {"type": "number"}}},
        ["action", "path"]),
    _fn("asset_library", "可信资产库：validated/experimental 模板，支持克隆、缩放、换贴图",
        {"action": {"type": "string", "enum": ["summary", "bootstrap", "list", "clone", "import", "promote"]},
         "name": _TEXT, "path": _PATH, "texture": _TEXT, "scale": {"type": "number"},
         "rotation": {"type": "array", "items": {"type": "number"}},
         "offset": {"type": "array", "items": {"type": "number"}},
         "validated": {"type": "boolean"}},
        ["action"]),
    _fn("config_gen", "由配置 schema 生成配置界面与翻译 key",
        {"schema_path": _PATH, "package": _TEXT, "modid": _TEXT, "class_name": _TEXT}),
    _fn("debug_scaffold", "生成调试模板：生产开关、dump 子命令、崩溃上下文、分层性能探针",
        {"package": _TEXT, "modid": _TEXT, "version": _TEXT,
         "kinds": {"type": "array", "items": {"type": "string", "enum": ["flags", "dump", "crash", "probe"]}},
         "command": _TEXT}),
    _fn("matrix_plan", "多版本矩阵、CI 工作流与 Git 版本",
        {"action": {"type": "string", "enum": ["plan", "workflow", "report", "version"]},
         "mc": {"type": "array", "items": {"type": "string"}},
         "loader": {"type": "array", "items": {"type": "string"}},
         "java": {"type": "array", "items": {"type": "string"}},
         "os": {"type": "array", "items": {"type": "string"}},
         "path": _PATH}),
    _fn("testgen", "生成 JUnit / GameTest / 场景卡片",
        {"kind": {"type": "string", "enum": ["unit", "gametest", "scenario"]},
         "path": _PATH, "name": _TEXT, "target": _TEXT}),
    _fn("migration_plan", "生成或应用版本迁移表",
        {"from_mc": _TEXT, "to_mc": _TEXT, "apply": {"type": "boolean"}}),
    _fn("screenshot_collect", "收集沙盒客户端截图并做美术评审", {"limit": {"type": "integer"}}),
    _fn("offline_cache", "管理离线缓存（模板、映射、符号、资产）",
        {"action": {"type": "string", "enum": ["summary", "verify", "prune", "export"]}, "path": _PATH}),
    _fn("studio_plan", "生成 ModSpec 与 GDD",
        {"idea": _TEXT, "loader": _TEXT, "mc": _TEXT, "scope": _TEXT,
         "name": _TEXT, "modid": _TEXT, "package": _TEXT}, ["idea"]),
    _fn("studio_generate", "按规格生成可编译工程",
        {"idea": _TEXT, "loader": _TEXT, "mc": _TEXT, "scope": _TEXT, "dest": _TEXT,
         "build": {"type": "boolean"}, "verify": {"type": "boolean"},
         "package": {"type": "boolean"}, "overwrite": {"type": "boolean"}}, ["idea"]),
    _fn("studio_audit", "审计资源与注册一致性"),
    _fn("studio_build", "规格工程编译并自动修复",
        {"timeout": {"type": "number"}, "fix": {"type": "boolean"}}),
    _fn("studio_verify", "启动开发端做冒烟验证", {"timeout": {"type": "number"}}),
    _fn("studio_package", "打包 JAR 并生成发布元数据"),
    _fn("read_skill", "读取技能全文", {"id": _TEXT}, ["id"]),
    _fn("remember", "写入长期记忆", {"content": _TEXT, "kind": _TEXT, "tags": _TEXT}, ["content"]),
    _fn("recall", "检索长期记忆", {"query": _TEXT, "limit": {"type": "integer"}}, ["query"]),
    _fn("search_knowledge", "检索知识库", {"query": _TEXT, "limit": {"type": "integer"}, "budget": {"type": "integer"}}, ["query"]),
    _fn("read_knowledge", "读取知识库文档或源码",
        {"id": {"type": "integer"}, "name": _TEXT, "file": _TEXT, "query": _TEXT,
         "offset": {"type": "integer"}}, []),
    _fn("decompile_jar", "反编译知识库 JAR 到 reference/decompiled",
        {"knowledge_id": {"type": "integer"}, "name": _TEXT, "target": _TEXT}),
    _fn("search_code", "搜索真实代码参考",
        {"query": _TEXT, "kind": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    _fn("github_repo", "查看 GitHub 仓库信息", {"repo": _TEXT}, ["repo"]),
    _fn("github_tree", "查看 GitHub 仓库目录", {"repo": _TEXT, "path": _TEXT, "ref": _TEXT}, ["repo"]),
    _fn("github_file", "读取 GitHub 单文件", {"repo": _TEXT, "path": _TEXT, "ref": _TEXT}, ["repo", "path"]),
    _fn("fetch_url", "抓取网页正文", {"url": _TEXT}, ["url"]),
    _fn("mcdev_prepare", "启动开发服务端/客户端",
        {"kind": {"type": "string", "enum": ["server", "client", "both"]},
         "timeout_ms": {"type": "integer"}, "wait_join": {"type": "boolean"},
         "join_port": {"type": "integer"}}),
    _fn("mcdev_stop", "停止开发端", {"side": {"type": "string", "enum": ["server", "client", "all"]}}),
    _fn("mcdev_launch_status", "查看宿主管理的进程与端口"),
    _fn("mcdev_status", "读取探针状态", {"side": {"type": "string"}, "port": {"type": "integer"}}),
    _fn("mcdev_command", "执行游戏命令", {"side": {"type": "string"}, "cmd": _TEXT, "port": {"type": "integer"}}, ["cmd"]),
    _fn("mcdev_ui", "读取客户端 UI 快照", {"side": {"type": "string"}, "port": {"type": "integer"}}),
    _fn("mcdev_click", "点击槽位或坐标",
        {"side": {"type": "string"}, "slot": {"type": "integer"}, "x": {"type": "integer"},
         "y": {"type": "integer"}, "button": {"type": "integer"}, "shift": {"type": "boolean"}}),
    _fn("mcdev_screenshot", "截取客户端画面", {"side": {"type": "string"}}),
    _fn("mcdev_run_dump", "导出最近运行日志", {"side": {"type": "string"}, "n": {"type": "integer"}}),
    _fn("mcdev_use", "在坐标处使用当前物品",
        {"side": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
         "z": {"type": "number"}, "face": {"type": "string"}}),
    _fn("mcdev_install_probe", "安装开发探针", {"probe_jar": _TEXT}),
    _fn("mcdev_scenario", "执行 YAML 场景卡片或步骤",
        {"card": _TEXT, "steps": {"type": "array", "items": {"type": "object"}}, "dry_run": {"type": "boolean"}}),
    _fn("mcdev_build", "用 mcdev 包装执行 Gradle build"),
    _fn("mcdev_gametest", "运行 GameTest 并返回汇总"),
]


def schemas(*, include: set[str] | None = None) -> list[dict[str, Any]]:
    if not include:
        return list(TOOL_DEFINITIONS)
    return [item for item in TOOL_DEFINITIONS if item["function"]["name"] in include]


def names() -> list[str]:
    return [item["function"]["name"] for item in TOOL_DEFINITIONS]
