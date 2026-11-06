from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

SPEC_DIR = ".dma"
SPEC_FILE = "spec.json"
CHECKLIST_TOTAL = 10


@dataclass
class CheckItem:
    key: str
    label: str
    ok: bool
    detail: str = ""


@dataclass
class ModSpec:
    """一句需求被拆成的完整规格。

    这一层是 AutoMods 式流程的核心：先确认做什么，再写代码。
    所有字段都尽量用自然语言保存，方便直接渲染成给用户看的说明书。
    """

    title: str = ""
    summary: str = ""
    mod_id: str = ""
    mc_version: str = ""
    loader: str = ""
    runtime: str = ""
    content: list[dict[str, str]] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    obtain: list[str] = field(default_factory=list)
    balance: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    status: str = "draft"
    revision: int = 0
    source_text: str = ""
    updated_at: str = ""

    def confirmed(self) -> bool:
        return self.status == "confirmed"

    def empty(self) -> bool:
        return not any(
            (
                self.title.strip(),
                self.summary.strip(),
                self.content,
                self.features,
            )
        )


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        parts = re.split(r"\r?\n|[；;]", text)
        return [part.strip().lstrip("-*0123456789.、 ").strip() for part in parts if part.strip()]
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            if isinstance(item, dict):
                label = _text(
                    item.get("name")
                    or item.get("title")
                    or item.get("id")
                    or item.get("desc")
                )
                if label:
                    result.append(label)
            else:
                text = _text(item)
                if text:
                    result.append(text)
        return result
    if isinstance(value, dict):
        return [f"{key}：{_text(item)}" for key, item in value.items() if _text(item)]
    return [_text(value)] if _text(value) else []


def _content_list(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    result: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            kind = _text(
                item.get("type")
                or item.get("kind")
                or item.get("category")
                or item.get("注册类型")
            ) or "内容"
            ident = _text(
                item.get("id")
                or item.get("key")
                or item.get("registry_name")
                or item.get("注册名")
            )
            name = _text(
                item.get("name")
                or item.get("title")
                or item.get("display")
                or item.get("名称")
            )
            desc = _text(
                item.get("desc")
                or item.get("description")
                or item.get("note")
                or item.get("说明")
            )
            if not name and ident:
                name = ident
            if name or ident:
                result.append(
                    {"type": kind, "id": ident, "name": name, "desc": desc}
                )
        else:
            text = _text(item)
            if text:
                result.append({"type": "内容", "id": "", "name": text, "desc": ""})
    return result


def _normalize_runtime(value: Any) -> str:
    text = _text(value).lower()
    if not text:
        return ""
    if text in {"client", "客户端", "client only", "仅客户端"}:
        return "客户端"
    if text in {"server", "服务端", "server only", "仅服务端"}:
        return "服务端"
    if text in {"both", "双端", "客户端+服务端", "client+server", "通用"}:
        return "双端"
    return _text(value)


def _normalize_loader(value: Any) -> str:
    text = _text(value).lower()
    if "neo" in text:
        return "NeoForge"
    if "fabric" in text:
        return "Fabric"
    if "arch" in text:
        return "Architectury"
    return _text(value)


ALIASES: dict[str, tuple[str, ...]] = {
    "title": ("title", "name", "mod_name", "模组名称", "名称"),
    "summary": ("summary", "goal", "description", "idea", "one_line", "核心玩法", "简介"),
    "mod_id": ("mod_id", "modid", "id"),
    "mc_version": ("mc_version", "minecraft_version", "version", "target_version", "游戏版本"),
    "loader": ("loader", "mod_loader", "加载器"),
    "runtime": ("runtime", "side", "environment", "运行端"),
    "content": ("content", "contents", "registrations", "items", "注册内容"),
    "features": ("features", "gameplay", "mechanics", "玩法", "功能"),
    "obtain": ("obtain", "acquisition", "how_to_get", "获取方式"),
    "balance": ("balance", "numbers", "values", "数值", "数值与平衡"),
    "assets": ("assets", "art", "textures", "models", "美术", "美术资源"),
    "acceptance": ("acceptance", "acceptance_criteria", "test_plan", "verify", "验收标准"),
    "risks": ("risks", "risk", "风险", "风险与依赖"),
    "dependencies": ("dependencies", "depends", "依赖"),
    "status": ("status", "状态"),
    "revision": ("revision", "version_no"),
    "source_text": ("source_text", "raw", "original", "manual", "原话"),
}


def _pick(payload: dict[str, Any], key: str) -> tuple[bool, Any]:
    for alias in ALIASES[key]:
        if alias in payload:
            return True, payload[alias]
    return False, None


def parse_spec(
    payload: dict[str, Any],
    project: Any | None = None,
    base: ModSpec | None = None,
) -> ModSpec:
    """把模型提交的 JSON 规格转成 ModSpec。

    payload 可以带一层 `spec`；字段名中英文都接受。缺的字段用工程信息补齐，
    这样用户确认时不至于看到大量“未填写”。
    """
    if not isinstance(payload, dict):
        raise ValueError("规格必须是 JSON 对象。")
    nested = payload.get("spec")
    if isinstance(nested, dict):
        payload = {**nested, **{k: v for k, v in payload.items() if k != "spec"}}

    spec = ModSpec(
        title=base.title if base else "",
        summary=base.summary if base else "",
        mod_id=base.mod_id if base else "",
        mc_version=base.mc_version if base else "",
        loader=base.loader if base else "",
        runtime=base.runtime if base else "",
        content=list(base.content) if base else [],
        features=list(base.features) if base else [],
        obtain=list(base.obtain) if base else [],
        balance=list(base.balance) if base else [],
        assets=list(base.assets) if base else [],
        acceptance=list(base.acceptance) if base else [],
        risks=list(base.risks) if base else [],
        dependencies=list(base.dependencies) if base else [],
        status=base.status if base else "draft",
        revision=base.revision if base else 0,
        source_text=base.source_text if base else "",
    )

    for key in (
        "title",
        "summary",
        "mod_id",
        "mc_version",
        "runtime",
        "source_text",
    ):
        present, value = _pick(payload, key)
        if present and _text(value):
            spec.__setattr__(key, _text(value))
    present, value = _pick(payload, "loader")
    if present and _text(value):
        spec.loader = _normalize_loader(value)
    spec.runtime = _normalize_runtime(spec.runtime)

    for key in (
        "features",
        "obtain",
        "balance",
        "assets",
        "acceptance",
        "risks",
        "dependencies",
    ):
        present, value = _pick(payload, key)
        if present:
            spec.__setattr__(key, _string_list(value))
    present, value = _pick(payload, "content")
    if present:
        spec.content = _content_list(value)

    present, value = _pick(payload, "status")
    if present and _text(value) in {"draft", "confirmed"}:
        spec.status = _text(value)
    present, value = _pick(payload, "revision")
    if present:
        try:
            spec.revision = max(0, int(value))
        except (TypeError, ValueError):
            pass

    if project is not None:
        meta = getattr(project, "meta", None)
        if meta is not None:
            spec.title = spec.title or _text(meta.name) or _text(meta.mod_id)
            spec.mod_id = spec.mod_id or _text(meta.mod_id)
            spec.mc_version = spec.mc_version or _text(meta.mc_version)
            spec.loader = _normalize_loader(spec.loader or _text(meta.loader))
            spec.runtime = spec.runtime or _normalize_runtime(
                getattr(meta, "runtime", "")
            )
    spec.runtime = spec.runtime or "双端"
    spec.updated_at = _now()
    return spec


def apply_patch(spec: ModSpec, payload: dict[str, Any], project: Any | None = None) -> ModSpec:
    """增量修改：只覆盖 payload 里出现的字段，并让版本号 +1。"""
    updated = parse_spec(payload, project=project, base=spec)
    updated.revision = spec.revision + 1
    updated.status = spec.status
    updated.updated_at = _now()
    return updated


def checklist(spec: ModSpec) -> list[CheckItem]:
    """10 项需求清单，对应界面上的「需求 n/10」。"""

    def has(items: list[str]) -> bool:
        return any(str(item).strip() for item in items)

    return [
        CheckItem(
            "goal",
            "核心玩法目标",
            bool(spec.summary.strip()),
            "用一句话说清玩家能玩到什么",
        ),
        CheckItem("version", "游戏版本", bool(spec.mc_version.strip()), "目标 Minecraft 版本"),
        CheckItem("loader", "加载器", bool(spec.loader.strip()), "Fabric / NeoForge / Architectury"),
        CheckItem("runtime", "运行端", bool(spec.runtime.strip()), "客户端 / 服务端 / 双端"),
        CheckItem("content", "注册内容", bool(spec.content), "物品、方块、实体、命令至少一项"),
        CheckItem("obtain", "获取方式", has(spec.obtain), "合成、掉落、生成或指令"),
        CheckItem("balance", "数值与平衡", has(spec.balance), "伤害、冷却、耐久、概率"),
        CheckItem("assets", "美术资源", has(spec.assets), "纹理、模型、音效、界面"),
        CheckItem("acceptance", "验收标准", has(spec.acceptance), "进游戏后怎么验证功能"),
        CheckItem("risks", "风险与依赖", has(spec.risks), "Mixin、前置模组、多人兼容；没有也写「无」"),
    ]


def checklist_score(spec: ModSpec) -> tuple[int, int]:
    items = checklist(spec)
    return sum(1 for item in items if item.ok), len(items)


def missing_items(spec: ModSpec) -> list[str]:
    return [item.label for item in checklist(spec) if not item.ok]


def _bullets(items: list[str], empty: str = "（未填写）") -> str:
    if not items:
        return f"- {empty}"
    return "\n".join(f"- {item}" for item in items)


def render_manual(spec: ModSpec) -> str:
    passed, total = checklist_score(spec)
    rows = ["| 类型 | 注册名 | 名称 | 说明 |", "| --- | --- | --- | --- |"]
    if spec.content:
        for item in spec.content:
            rows.append(
                "| {type} | {id} | {name} | {desc} |".format(
                    type=item.get("type") or "内容",
                    id=item.get("id") or "—",
                    name=item.get("name") or "—",
                    desc=(item.get("desc") or "—").replace("|", "\\|"),
                )
            )
    else:
        rows.append("| 内容 | — | — | 尚未规划 |")
    checks = "\n".join(
        f"- [{'x' if item.ok else ' '}] {item.label}"
        for item in checklist(spec)
    )
    return (
        f"# {spec.title or '未命名模组'} · 规格说明书\n\n"
        f"> {spec.summary or '（还没有一句话玩法说明）'}\n\n"
        f"## 构建目标\n\n"
        f"- 游戏版本：{spec.mc_version or '未填写'}\n"
        f"- 加载器：{spec.loader or '未填写'}\n"
        f"- 运行端：{spec.runtime or '双端'}\n"
        f"- Mod ID：`{spec.mod_id or '未填写'}`\n"
        f"- 需求完成度：**{passed}/{total}**\n\n"
        f"## 注册内容\n\n" + "\n".join(rows) + "\n\n"
        f"## 玩法功能\n\n{_bullets(spec.features)}\n\n"
        f"## 获取方式\n\n{_bullets(spec.obtain)}\n\n"
        f"## 数值与平衡\n\n{_bullets(spec.balance)}\n\n"
        f"## 美术与音效\n\n{_bullets(spec.assets)}\n\n"
        f"## 验收标准\n\n{_bullets(spec.acceptance)}\n\n"
        f"## 风险与依赖\n\n{_bullets(spec.risks)}\n"
        + (
            f"\n## 前置与依赖\n\n{_bullets(spec.dependencies)}\n"
            if spec.dependencies
            else ""
        )
        + f"\n## 需求清单\n\n{checks}\n"
    )


def spec_context(spec: ModSpec | None, limit: int = 4200) -> str:
    if spec is None:
        return ""
    text = render_manual(spec)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…（规格较长，已截断，完整内容见 .dma/spec.json）"


def _list_key(items: list[str]) -> tuple[str, ...]:
    return tuple(str(item).strip() for item in items)


def diff_spec(old: ModSpec, new: ModSpec) -> list[str]:
    lines: list[str] = []
    for key, label in (
        ("title", "标题"),
        ("summary", "一句话玩法"),
        ("mc_version", "游戏版本"),
        ("loader", "加载器"),
        ("runtime", "运行端"),
        ("mod_id", "Mod ID"),
    ):
        before = str(getattr(old, key) or "")
        after = str(getattr(new, key) or "")
        if before != after:
            lines.append(f"{label}：{before or '未填写'} → {after or '未填写'}")

    for key, label in (
        ("content", "注册内容"),
        ("features", "玩法功能"),
        ("obtain", "获取方式"),
        ("balance", "数值"),
        ("assets", "美术资源"),
        ("acceptance", "验收标准"),
        ("risks", "风险"),
        ("dependencies", "依赖"),
    ):
        if key == "content":
            before_items = [
                item.get("name") or item.get("id") or ""
                for item in old.content
            ]
            after_items = [
                item.get("name") or item.get("id") or ""
                for item in new.content
            ]
        else:
            before_items = list(getattr(old, key))
            after_items = list(getattr(new, key))
        before_set = {item for item in before_items if item}
        after_set = {item for item in after_items if item}
        added = [item for item in after_items if item and item not in before_set]
        removed = [item for item in before_items if item and item not in after_set]
        if added:
            lines.append(f"新增{label}：" + "、".join(added[:8]))
        if removed:
            lines.append(f"移除{label}：" + "、".join(removed[:8]))
        if not added and not removed and _list_key(before_items) != _list_key(after_items):
            lines.append(f"{label}有调整")
    return lines or ["规格未发生字段级变化"]


def spec_path(root: Path) -> Path:
    return Path(root) / SPEC_DIR / SPEC_FILE


def save_spec(root: Path, spec: ModSpec) -> Path:
    target = spec_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(spec)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def load_spec(root: Path) -> ModSpec | None:
    target = spec_path(root)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    allowed = set(ModSpec.__dataclass_fields__)
    try:
        return ModSpec(**{key: value for key, value in data.items() if key in allowed})
    except TypeError:
        return None


def confirm_spec(root: Path, spec: ModSpec) -> ModSpec:
    spec.status = "confirmed"
    spec.revision += 1
    spec.updated_at = _now()
    save_spec(root, spec)
    return spec


def reopen_spec(root: Path, spec: ModSpec) -> ModSpec:
    spec.status = "draft"
    spec.revision += 1
    spec.updated_at = _now()
    save_spec(root, spec)
    return spec
