"""Asset and engineering tools used by the 2.0 agent loop."""

from __future__ import annotations

from . import blockbench, cache, crash, diagnostics, impact, migration, screenshot, templates, testgen, texture, vision

CAPABILITIES: dict[str, dict] = {
    "templates": {
        "module": "dma.kernel.tools.templates",
        "description": "可信模板库：定位、生成与覆盖差异",
        "rules": ["P1-07"],
    },
    "blockbench": {
        "module": "dma.kernel.tools.blockbench",
        "description": "创建/校验 .bbmodel 并导出 Java 模型",
        "rules": ["P1-06", "P1-07", "TOOL-13"],
    },
    "texture": {
        "module": "dma.kernel.tools.texture",
        "description": "结构化像素贴图生成与规格校验",
        "rules": ["P1-08", "TOOL-14"],
    },
    "vision": {
        "module": "dma.kernel.tools.vision",
        "description": "贴图/截图的美术评审与差异对比",
        "rules": ["P1-09", "TOOL-15"],
    },
    "crash": {
        "module": "dma.kernel.tools.crash",
        "description": "崩溃日志解析、分类与聚类",
        "rules": ["P1-03", "TOOL-17"],
    },
    "impact": {
        "module": "dma.kernel.tools.impact",
        "description": "改动影响图：调用方、资源、测试与规则",
        "rules": ["TOOL-19"],
    },
    "cache": {
        "module": "dma.kernel.tools.cache",
        "description": "离线缓存：哈希校验、打包与裁剪",
        "rules": ["TOOL-20"],
    },
    "screenshot": {
        "module": "dma.kernel.tools.screenshot",
        "description": "沙盒截图收集、评审与证据清单",
        "rules": ["TOOL-16"],
    },
    "testgen": {
        "module": "dma.kernel.tools.testgen",
        "description": "JUnit/GameTest/场景卡生成",
        "rules": ["P1-01", "P1-02", "P1-05"],
    },
    "migration": {
        "module": "dma.kernel.tools.migration",
        "description": "存档/配置键迁移表与重写计划",
        "rules": ["P1-19", "P3-30", "P3-31"],
    },
    "diagnostics": {
        "module": "dma.kernel.tools.diagnostics",
        "description": "调试模板：dump 子命令、崩溃上下文、生产开关、分层性能探针",
        "rules": ["P3-22", "P3-24", "P3-25", "P3-26"],
    },
}


def capabilities() -> dict[str, dict]:
    return {name: dict(meta) for name, meta in CAPABILITIES.items()}


def rules_covered() -> list[str]:
    rows: list[str] = []
    for meta in CAPABILITIES.values():
        for rule_id in meta.get("rules") or []:
            if rule_id not in rows:
                rows.append(rule_id)
    return sorted(rows)


__all__ = [
    "templates",
    "blockbench",
    "texture",
    "vision",
    "crash",
    "impact",
    "cache",
    "screenshot",
    "testgen",
    "migration",
    "diagnostics",
    "CAPABILITIES",
    "capabilities",
    "rules_covered",
]
