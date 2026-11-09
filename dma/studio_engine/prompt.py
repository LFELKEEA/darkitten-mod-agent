"""Stage prompt packs for the Skill.

The CLI provides deterministic context; the model provides creativity and
semantic fixes. Keeping the two separate stops the model from inventing APIs or
silently changing scope.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .util import read_json

BASE_RULES = """你是 Minecraft 模组工程师，不是聊天助手。必须遵守：
1. 版本已锁定；禁止使用未在 API 卡中出现的类/方法。写代码前先查 .mcmod/api-card/api-card.md。
2. 编译是唯一真相；任何“已完成”必须附带 mcmod build 的 exit=0。
3. 所有注册内容必须有模型、贴图、语言键、配方/获取方式；缺一项就算未完成。
4. 公共代码禁止 import net.minecraft.client.*；客户端逻辑放 client 包或用加载器提供的 Dist 隔离。
5. 不凭记忆写映射名、事件名、注册器；不确定就运行 mcmod recon / mcmod api-search。
6. 只做当前切片，不顺手重构，不新增未在 spec 中的内容。
7. 失败两次后停止盲试，输出结构化诊断与下一步。
"""


def _load(project: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = read_json(project / "mod-spec.json", {}) or {}
    state = read_json(project / ".mcmod" / "state.json", {}) or {}
    return spec, state


def _card_excerpt(project: Path, limit: int = 6000) -> str:
    path = project / ".mcmod" / "api-card" / "api-card.md"
    if not path.exists():
        return "（尚无 API 卡；先运行 mcmod recon。）"
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[:limit]


def _report_excerpt(project: Path, name: str, limit: int = 4000) -> str:
    data = read_json(project / ".mcmod" / name)
    if not data:
        return ""
    return json.dumps(data, ensure_ascii=False, indent=2)[:limit]


def stage_prompt(project: Path, stage: str) -> str:
    spec, state = _load(project)
    if not spec:
        return "缺少 mod-spec.json。先运行 `mcmod plan`。"
    ident = spec["identity"]
    profile = state.get("profile", {})
    header = f"""# 任务：{stage}

## 项目
- 名称：{ident['name_zh']} / {ident['name_en']}
- Mod ID：`{ident['modid']}`
- 包名：`{ident['package']}`
- 目标：{profile.get('loader')} {profile.get('mc')}，loader `{profile.get('loader_version')}`，Java `{profile.get('java_major')}`
- 规模：{spec.get('scope')}，内容节点 {len(spec.get('content', []))}
- 目录：{project}

{BASE_RULES}
"""
    if stage == "design":
        return header + f"""
## 设计任务
基于下面的规格，补全/校正玩法构思。输出必须是可直接写回 `mod-spec.json` 的 JSON 片段，
不要改 modid、包名、目标版本，不要删节点。重点：
- 每个节点的 `mechanics` 写成可实现的行为，不写空话。
- 检查进度树：每个阶段必须有明确入口与出口。
- 检查经济：基础材料 → 中间产物 → 终局物品，不能出现自循环配方。
- 输出 3 个最关键的风险与验证方法。

当前规格：
```json
{json.dumps(spec, ensure_ascii=False, indent=2)[:24000]}
```
"""
    if stage == "code":
        return header + f"""
## 编码任务
只实现 `IMPLEMENTATION_TODO.md` 中未完成的节点，按垂直切片推进：
1. 先读 API 卡，列出本次要用的真实签名。
2. 修改/新增源文件；注册项由 `ModRegistry.java` 生成，不手改。
3. 运行 `mcmod audit .`，修到 0 error。
4. 运行 `mcmod build . --fix`；若失败，读 `.mcmod/build-report.json`，
   只修报告中的具体错误，最多 3 轮。
5. 输出：改动的文件、每轮构建结果、仍未解决的问题。

API 卡摘要：
```markdown
{_card_excerpt(project)}
```

当前审计：
```json
{_report_excerpt(project, 'audit-report.json')}
```

当前构建：
```json
{_report_excerpt(project, 'build-report.json')}
```
"""
    if stage == "asset":
        return header + f"""
## 素材任务
按 `spec.asset_prompts` 生成贴图。要求：
- 尺寸按 target 决定：方块/物品 16x16，实体 64x64，Boss 128x128。
- 严格使用调色板：{' '.join(spec.get('palette', []))}
- 1px 深色描边、无抗锯齿、无渐变、透明背景。
- 生成后检查尺寸与文件名必须与 target 完全一致。
- 方块贴图必须能无缝平铺；物品轮廓在 16x16 内可辨认。
- 不确定的贴图先保留程序生成的占位图，不要留空。

素材提示词：
```json
{json.dumps(spec.get('asset_prompts', [])[:60], ensure_ascii=False, indent=2)}
```
"""
    if stage == "qa":
        return header + f"""
## 验收任务
按顺序执行并如实汇报：
1. `mcmod audit .` → 0 error。
2. `mcmod build . --fix` → exit 0。
3. `mcmod verify server .` → 日志出现 Done 且无严重错误。
4. `mcmod verify client .` → 窗口出现、截图成功、无缺失贴图。
5. 用视觉模型检查截图：紫黑方块、错位模型、UI 越界、物品图标不可辨认。
6. `mcmod package .` → jar 元数据与资源完整。

输出表格：门禁 / 结果 / 证据（日志路径、截图路径、错误摘要）。
任何门禁失败，不得说“基本完成”。
"""
    return header + "\n未知阶段。可用阶段：design / code / asset / qa。\n"
