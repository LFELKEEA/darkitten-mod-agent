"""Mechanism semantic validation — 需求 → 真实现对照。

第四层防线：其它 checker 只查「工程里实际写了什么」的正确性（写了 @Mixin
就该有 target、写了 Screen 就该有 quickMoveStack），本 checker 反过来查
「需求要求了 X，工程里到底做没做 X」，专门抓「占位冒充实现」——

    - transform（变身/夺舍）: 需求要求 mixin 切换控制权，但 mixins.json 空
      / 没有 @Mixin 类 → 报 P1-34（error）。
    - dialogue（聊天/对话）: 需求要求可交互 GUI，但工程没有 extends Screen
      的类 → 报 P2-20（error）。
    - inventory_ui（物品框）: 需求要求 Screen+ScreenHandler，但工程没有
      extends ScreenHandler/AbstractContainerMenu 的类 → 报 P2-21（error）。
    - skin（换肤）: 需求要求按皮肤 id 切换纹理，但渲染器恒返回单一固定
      纹理 → 报 P1-08（error）。

rule_id 复用 MECHANISM_RULES 里每个机制的 ``rules`` 锚点（见
dma/kernel/spec.py），因此 gate 的 ``lint-mechanism`` 验收（fail_on=error）
会自动拦截这些 finding。无 .dma/spec.json 或没有机制需求时静默返回，
不会误伤普通工程。
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from ..findings import Finding
from .common import ProjectContext, finding

MECHANISM_IDS = {"transform", "skin", "dialogue", "inventory_ui"}

# 机制 id -> 缺实现时使用的 rule_id（与 spec.MECHANISM_RULES 的 rules 对齐）
_MECH_RULE = {
    "transform": "P1-34",
    "skin": "P1-08",
    "dialogue": "P2-20",
    "inventory_ui": "P2-21",
}


def _mechanism_features(ctx: ProjectContext) -> list[Any]:
    """读 .dma/spec.json 里的机制 feature，容错返回空列表。"""
    try:
        from ..spec import load_spec
        spec = load_spec(ctx.root)
    except Exception:
        return []
    if spec is None:
        return []
    features = getattr(spec, "features", None) or []
    return [f for f in features if getattr(f, "id", "") in MECHANISM_IDS]


def _mixin_registered(ctx: ProjectContext) -> bool:
    """是否有非空的 mixin 登记（mixins/client/server 任一数组非空）。"""
    root = ctx.root / "src" / "main" / "resources"
    if not root.is_dir():
        return False
    for path in root.rglob("*mixins*.json"):
        data = ctx.load_json(path)
        if not isinstance(data, dict):
            continue
        for key in ("mixins", "client", "server"):
            entries = data.get(key)
            if entries:
                return True
    return False


def _renderer_skin_aware(java_class: Any) -> bool:
    """启发式：渲染器是否按皮肤 id/字段切换纹理，而非恒返回单一固定纹理。

    真实现信号（任一命中即认为 skin-aware）：
      - 出现 skinId / getSkin / switch / Map< 等选择逻辑；
      - 方法体/字段里出现 2 个以上不同的 .png 纹理资源标识。
    """
    text = (java_class.body or "") + " " + " ".join(
        getattr(m, "body", "") or "" for m in getattr(java_class, "methods", [])
    )
    if any(token in text for token in ("skinId", "getSkin", "switch", "Map<", "HashMap", "skinIndex")):
        return True
    ids = set(re.findall(r'["\']([\w:./_-]+\.png)["\']', text))
    return len(ids) > 1


def run(ctx: ProjectContext) -> Iterable[Finding]:
    features = _mechanism_features(ctx)
    if not features:
        return []

    has_mixin_class = False
    screen_seen = False
    menu_seen = False
    skin_aware_renderer = False
    any_renderer = False

    for java, java_class in ctx.classes():
        annotations = " ".join(java_class.annotations)
        if "@Mixin" in annotations:
            has_mixin_class = True
        name = java_class.name or ""
        ext = java_class.extends or ""
        if "Screen" in ext or name.endswith("Screen"):
            screen_seen = True
        if ("ScreenHandler" in ext or "AbstractContainerMenu" in ext
                or "Menu" in ext or name.endswith("Menu")):
            menu_seen = True
        if "Renderer" in name or "Render" in name:
            any_renderer = True
            if _renderer_skin_aware(java_class):
                skin_aware_renderer = True

    for feature in features:
        fid = getattr(feature, "id", "")
        title = getattr(feature, "title", "") or fid
        rule_id = _MECH_RULE.get(fid, "P2-20")

        if fid == "transform":
            if not _mixin_registered(ctx):
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，但 mixins.json 为空（mixins/client/server 均无登记）"
                    f"——这是占位实现，玩家无法真正变身/夺舍",
                    suggestion=(
                        "在 mixins.json 登记至少一个 @Mixin 类，用 mixin 切换玩家"
                        "控制权/相机/渲染，而不是在玩家旁边 spawn 一个实体。"
                    ),
                    severity="error",
                    checker="mechanism",
                )
            elif not has_mixin_class:
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，mixins.json 有登记但源码里没有 @Mixin 类",
                    suggestion=(
                        "补写实际的 mixin 类（如 @Mixin(Player.class)），空登记不算实现。"
                    ),
                    severity="error",
                    checker="mechanism",
                )
        elif fid == "dialogue":
            if not screen_seen:
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，但工程里没有 extends Screen 的对话/聊天 GUI 类",
                    suggestion=(
                        "用 Screen+ScreenHandler 实现可交互对话界面，而不是只发一条硬编码消息。"
                    ),
                    severity="error",
                    checker="mechanism",
                )
        elif fid == "inventory_ui":
            if not menu_seen:
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，但工程里没有 extends ScreenHandler/"
                    f"AbstractContainerMenu 的物品框类",
                    suggestion=(
                        "用 Screen+ScreenHandler 实现可打开交换的物品框，"
                        "而不是直接 setStack 覆盖玩家背包。"
                    ),
                    severity="error",
                    checker="mechanism",
                )
        elif fid == "skin":
            if not any_renderer:
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，但工程里没有渲染器类（Renderer）",
                    suggestion=(
                        "补写实体渲染器，并按 skinId 从 littleskin 拉取或从本地多套纹理"
                        "选择，不要恒返回 default.png。"
                    ),
                    severity="error",
                    checker="mechanism",
                )
            elif not skin_aware_renderer:
                yield finding(
                    rule_id,
                    f"需求要求「{title}」，但渲染器恒返回单一固定纹理（未按皮肤 id 切换）",
                    suggestion=(
                        "渲染器按 skinId 从 littleskin 拉取或从本地多套纹理选择，"
                        "不要恒返回 default.png。"
                    ),
                    severity="error",
                    checker="mechanism",
                )

    return
