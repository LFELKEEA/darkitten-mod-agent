"""在线参考管线：识别功能意图，搜索成熟模组，按需下载、反编译并检索代码。

设计目标是“不内置、不预装”：

1. 用户的自然语言需求先被拆成功能类型（实体、GUI、维度、Mixin……）；
2. 用功能类型去 Modrinth 在线搜索同加载器、同 Minecraft 版本的成熟模组；
3. 只下载与当前功能最相关的少量 JAR，反编译到 data/library/sources；
4. 给反编译源码建立可检索索引，按功能关键词返回精简代码片段；
5. Agent 在写代码前自动拿到这些片段作为参考，而不是复制第三方源码。

反编译结果只保存在本机 data/library 下，供学习与排错使用；不重新分发，
也不允许直接写入用户工程的 src/。调用方必须保留原模组许可证提示。
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from . import modlibrary, paths

Progress = Callable[[int, str], None]
MAX_MOD_BYTES = 160 * 1024 * 1024
MAX_INDEX_FILE_BYTES = 800_000
MAX_SNIPPET_CHARS = 4_000
MAX_SNIPPET_LINES = 70
INDEX_VERSION = 1
LICENSE_NOTICE = (
    "反编译源码仅用于本地学习与排错。请遵守原模组许可证，"
    "不要把第三方源码直接复制进当前工程或重新分发。"
)


class ReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class Feature:
    id: str
    label: str
    aliases: tuple[str, ...]
    queries: tuple[str, ...]
    tokens: tuple[str, ...]
    notes: str = ""


FEATURES: tuple[Feature, ...] = (
    Feature(
        "entity",
        "生物 / 实体",
        ("实体", "生物", "怪物", "boss", "npc", "mob", "entity", "寻路", "ai", "生成蛋", "spawn egg"),
        ("custom entity", "mob ai goals", "entity renderer"),
        ("entity", "mob", "goal", "pathfinder", "attribute", "renderer", "spawn"),
        "实体注册、属性、AI Goal、生成蛋、渲染器与客户端同步。",
    ),
    Feature(
        "block",
        "方块 / 机器",
        ("方块", "block", "machine", "机器", "作物", "crop", "矿石", "ore"),
        ("custom block", "block entity", "crop block"),
        ("block", "blockentity", "blockstate", "crop", "tick", "entityblock"),
        "方块、BlockEntity、方块状态、掉落与刻逻辑。",
    ),
    Feature(
        "item",
        "物品 / 装备",
        ("物品", "item", "工具", "tool", "武器", "weapon", "盔甲", "armor", "装备", "食物", "food"),
        ("custom item", "custom tool", "custom armor"),
        ("item", "tool", "armor", "tier", "food", "durability", "components"),
        "物品、工具等级、护甲、食物与数据组件。",
    ),
    Feature(
        "gui",
        "GUI / 容器",
        ("界面", "菜单", "gui", "screen", "container", "背包", "箱子", "按钮", "widget"),
        ("custom gui screen", "container menu", "screen handler"),
        ("screen", "menu", "screenhandler", "container", "widget", "button", "slot"),
        "Screen + Menu/ScreenHandler，客户端与服务端成对注册。",
    ),
    Feature(
        "worldgen",
        "维度 / 世界生成",
        ("维度", "群系", "世界生成", "dimension", "biome", "worldgen", "结构", "structure", "矿物", "ore"),
        ("custom dimension", "biome worldgen", "structure generation"),
        ("dimension", "biome", "worldgen", "structure", "feature", "chunk", "placement"),
        "维度、群系、结构、矿物生成与数据包注册。",
    ),
    Feature(
        "rendering",
        "渲染 / 动画 / 粒子",
        ("渲染", "动画", "模型", "render", "animation", "model", "粒子", "particle", "shader", "光影"),
        ("entity renderer animation", "custom particle", "model loader"),
        ("renderer", "particle", "model", "animation", "pose", "layer", "vertex"),
        "渲染层、模型、动画、粒子与客户端资源。",
    ),
    Feature(
        "mixin",
        "Mixin 注入",
        ("mixin", "注入", "hook", "拦截", "改写原版", "overwrite"),
        ("mixin injection", "mixin living entity"),
        ("mixin", "inject", "redirect", "overwrite", "accessor", "shadow", "at"),
        "Mixin 目标、注入点、访问器与 mixins.json 注册。",
    ),
    Feature(
        "network",
        "网络同步",
        ("网络", "数据包", "packet", "同步", "sync", "payload", "channel"),
        ("network packet sync", "payload networking"),
        ("packet", "payload", "network", "channel", "sync", "codec"),
        "自定义负载、编解码、客户端/服务端双向同步。",
    ),
    Feature(
        "data",
        "数据持久化 / 能力",
        ("能力", "capability", "attachment", "数据存储", "持久化", "save data", "persistent", "存档"),
        ("capability data attachment", "persistent player data"),
        ("capability", "attachment", "persistent", "saveddata", "nbt", "codec"),
        "Capability / DataAttachment / SavedData 与 NBT 序列化。",
    ),
    Feature(
        "recipe",
        "配方 / 战利品 / 数据包",
        ("配方", "recipe", "战利品", "loot", "数据包", "datapack", "标签", "tag"),
        ("custom recipe", "loot table modifier", "datapack registry"),
        ("recipe", "loot", "tag", "datapack", "codec", "condition"),
        "配方、战利品表、标签与数据包注册。",
    ),
    Feature(
        "command",
        "命令",
        ("命令", "command", "指令"),
        ("brigadier command", "custom command"),
        ("command", "brigadier", "argument", "suggestion"),
        "Brigadier 命令树、参数类型与权限。",
    ),
    Feature(
        "config",
        "配置",
        ("配置", "config", "设置", "settings", "选项"),
        ("mod config screen", "config file"),
        ("config", "options", "screen", "json", "toml"),
        "配置模型、读写与配置界面。",
    ),
    Feature(
        "effect",
        "状态效果",
        ("药水", "效果", "buff", "effect", "状态"),
        ("custom mob effect", "status effect"),
        ("effect", "mobeffect", "status", "potion", "attribute"),
        "MobEffect 注册、属性修饰与药水。",
    ),
    Feature(
        "villager",
        "村民 / 交易",
        ("村民", "交易", "trade", "villager"),
        ("custom villager profession", "trading"),
        ("villager", "profession", "trade", "merchant", "offer"),
        "职业、交易表与村民 AI。",
    ),
    Feature(
        "quest",
        "任务 / 进度",
        ("任务", "进度", "成就", "advancement", "quest"),
        ("advancement quest", "custom advancement"),
        ("advancement", "quest", "criteria", "reward", "trigger"),
        "进度、判据、奖励与任务链。",
    ),
    Feature(
        "skin",
        "外观 / 换肤",
        ("皮肤", "外观", "skin", "换装"),
        ("player skin renderer", "custom player model"),
        ("skin", "renderer", "model", "texture", "layer", "player"),
        "玩家/实体渲染层与纹理切换，通常需要 Mixin。",
    ),
    Feature(
        "social",
        "社交 / 对话",
        ("社交", "关系", "对话", "chat", "dialog"),
        ("npc dialogue", "relationship system"),
        ("dialog", "chat", "relationship", "screen", "data"),
        "关系数据、对话界面与 AI 行为联动。",
    ),
    Feature(
        "vehicle",
        "载具 / 骑乘",
        ("载具", "vehicle", "船", "车", "mount", "骑乘"),
        ("custom vehicle entity", "rideable entity"),
        ("vehicle", "ride", "mount", "entity", "control"),
        "可骑乘实体、控制输入与碰撞。",
    ),
    Feature(
        "storage",
        "存储 / 物品栏",
        ("存储", "仓库", "箱子", "inventory", "storage"),
        ("custom inventory storage", "item handler"),
        ("inventory", "storage", "handler", "container", "slot", "stack"),
        "物品栏、容器与跨端同步。",
    ),
)


def _lower(text: Any) -> str:
    return str(text or "").lower()


def _alias_hit(haystack: str, alias: str) -> bool:
    """英文别名按单词边界匹配，避免 ai 命中 said、item 命中 items 之外的词。"""
    text = alias.lower().strip()
    if not text:
        return False
    if re.search(r"[\u4e00-\u9fff]", text):
        return text in haystack
    pattern = r"(?<![a-z0-9])" + re.escape(text).replace(r"\ ", r"[\s_\-]+") + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def detect_features(text: str) -> list[Feature]:
    """从自然语言需求里识别功能类型；按 FEATURES 顺序去重返回。"""
    haystack = _lower(text)
    if not haystack.strip():
        return []
    result: list[Feature] = []
    for feature in FEATURES:
        if any(_alias_hit(haystack, alias) for alias in feature.aliases):
            result.append(feature)
    return result


def feature_tokens(features: Iterable[Feature]) -> list[str]:
    tokens: list[str] = []
    for feature in features:
        for token in feature.tokens:
            if token not in tokens:
                tokens.append(token)
    return tokens


def _clean_query(text: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_+\-]{1,30}", str(text or ""))
    cleaned = " ".join(words[:10]).strip()
    return cleaned or "minecraft mod"


def queries_for(text: str, features: list[Feature]) -> list[str]:
    queries: list[str] = []
    for feature in features:
        for query in feature.queries:
            if query not in queries:
                queries.append(query)
    cleaned = _clean_query(text)
    if not features and cleaned not in queries:
        queries.append(cleaned)
    return queries[:8]


def _merge_hits(groups: Iterable[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for item in group:
            key = str(item.get("id") or item.get("slug") or "")
            if not key:
                continue
            existing = merged.get(key)
            if existing is None:
                merged[key] = dict(item)
            else:
                # 保留下载量信息更完整的记录，但补上缺失字段。
                for field_name, value in item.items():
                    if not existing.get(field_name) and value:
                        existing[field_name] = value
    return sorted(
        merged.values(),
        key=lambda item: int(item.get("downloads") or 0),
        reverse=True,
    )


def search_references(
    text: str,
    *,
    loader: str = "",
    mc: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """按功能意图在线搜索参考模组，返回候选列表与识别到的功能。"""
    features = detect_features(text)
    queries = queries_for(text, features)
    groups: list[list[dict[str, Any]]] = []
    notes: list[str] = []
    for query in queries:
        try:
            groups.append(
                modlibrary.search(
                    query,
                    loader=loader,
                    mc=mc,
                    limit=max(3, min(12, limit)),
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单个查询失败不影响其它查询
            notes.append(f"搜索 {query!r} 失败：{exc}")
    candidates = _merge_hits(groups)
    return {
        "features": features,
        "queries": queries,
        "candidates": candidates[: max(1, min(30, limit))],
        "notes": notes,
    }


# ----------------------------------------------------------------------
# 源码索引


def library_sources_dir() -> Path:
    path = modlibrary.sources_dir()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "-", str(value or "")).strip("-")[:80] or "mod"


PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
CLASS_RE = re.compile(r"\b(?:class|interface|enum|record)\s+([A-Za-z_$][\w$]*)")
METHOD_RE = re.compile(
    r"(?:^|\s)(?:public|protected|private)\s+"
    r"(?:static\s+|final\s+|abstract\s+|synchronized\s+|native\s+|default\s+)*"
    r"[\w.$<>\[\],?]+\s+([A-Za-z_$][\w$]*)\s*\("
)
ANNOTATION_RE = re.compile(r"@([A-Za-z_$][\w$.]*)")


def _extract_java_entry(path: Path, root: Path) -> dict[str, Any] | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size <= 0 or size > MAX_INDEX_FILE_BYTES:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    relative = path.relative_to(root).as_posix()
    package_match = PACKAGE_RE.search(text)
    return {
        "path": relative,
        "package": package_match.group(1) if package_match else "",
        "classes": list(dict.fromkeys(CLASS_RE.findall(text))),
        "methods": list(dict.fromkeys(METHOD_RE.findall(text))),
        "annotations": list(dict.fromkeys(ANNOTATION_RE.findall(text))),
        "chars": len(text),
        "size": size,
    }


def index_path(source_dir: Path) -> Path:
    return Path(source_dir) / "INDEX.json"


def ensure_index(source_dir: Path, *, force: bool = False) -> dict[str, Any]:
    """为反编译目录建立类/方法索引，供关键词检索。"""
    root = Path(source_dir)
    if not root.is_dir():
        raise ReferenceError(f"源码目录不存在：{root}")
    target = index_path(root)
    if target.is_file() and not force:
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == INDEX_VERSION:
                return data
        except (OSError, ValueError):
            pass
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.java")):
        entry = _extract_java_entry(path, root)
        if entry is not None:
            files.append(entry)
    data = {
        "version": INDEX_VERSION,
        "root": str(root),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "files": files,
        "file_count": len(files),
        "class_count": len({name for item in files for name in item.get("classes") or []}),
    }
    target.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return data


def _all_source_dirs() -> list[Path]:
    root = library_sources_dir()
    result: list[Path] = []
    for path in sorted(root.glob("*/*")):
        if path.is_dir() and any(path.rglob("*.java")):
            result.append(path)
    return result


def _tokenize_query(text: str) -> list[str]:
    raw = re.findall(r"[A-Za-z_$][A-Za-z0-9_$]{1,40}|[\u4e00-\u9fff]{2,12}", str(text or ""))
    tokens: list[str] = []
    for item in raw:
        token = item.lower()
        if token not in tokens:
            tokens.append(token)
        # camelCase / PascalCase 拆词，例如 ScreenHandler -> screen, handler
        parts = re.findall(r"[A-Z]?[a-z]{3,}|[A-Z]{3,}", item)
        for part in parts:
            lowered = part.lower()
            if lowered not in tokens:
                tokens.append(lowered)
    return tokens[:24]


def _score_entry(entry: dict[str, Any], tokens: list[str]) -> int:
    if not tokens:
        return 0
    classes = " ".join(entry.get("classes") or []).lower()
    methods = " ".join(entry.get("methods") or []).lower()
    annotations = " ".join(entry.get("annotations") or []).lower()
    path = f"{entry.get('package', '')}/{entry.get('path', '')}".lower()
    score = 0
    for token in tokens:
        if token in classes:
            score += 8
        if token in methods:
            score += 4
        if token in path:
            score += 3
        if token in annotations:
            score += 2
    return score


def _snippet_from_file(path: Path, tokens: list[str]) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    if not lines:
        return ""
    best_index = 0
    best_score = -1
    for index, line in enumerate(lines):
        lowered = line.lower()
        score = sum(2 if token in lowered else 0 for token in tokens)
        if score > best_score:
            best_score = score
            best_index = index
    start = max(0, best_index - 14)
    end = min(len(lines), best_index + 28)
    if end - start > MAX_SNIPPET_LINES:
        end = start + MAX_SNIPPET_LINES
    header = f"// {path.name}:{start + 1}-{end}"
    body = "\n".join(lines[start:end])
    return (header + "\n" + body)[:MAX_SNIPPET_CHARS]


def search_snippets(
    query: str,
    *,
    tokens: Iterable[str] | None = None,
    roots: Iterable[Path] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """在已反编译源码里按关键词检索，返回带文件路径的代码片段。"""
    token_list = list(tokens or []) + _tokenize_query(query)
    token_list = list(dict.fromkeys(token.lower() for token in token_list if token))[:32]
    source_dirs = [Path(item) for item in roots] if roots else _all_source_dirs()
    scored: list[tuple[int, dict[str, Any], Path]] = []
    for source_dir in source_dirs:
        try:
            index = ensure_index(source_dir)
        except Exception:  # noqa: BLE001 - 坏目录跳过
            continue
        for entry in index.get("files") or []:
            if not isinstance(entry, dict):
                continue
            score = _score_entry(entry, token_list)
            if score <= 0:
                continue
            path = source_dir / str(entry.get("path") or "")
            if path.is_file():
                scored.append((score, entry, path))
    scored.sort(key=lambda item: item[0], reverse=True)
    hits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for score, entry, path in scored:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        snippet = _snippet_from_file(path, token_list)
        if not snippet:
            continue
        hits.append(
            {
                "score": score,
                "path": str(path),
                "relative": entry.get("path", ""),
                "package": entry.get("package", ""),
                "classes": entry.get("classes") or [],
                "methods": (entry.get("methods") or [])[:12],
                "snippet": snippet,
            }
        )
        if len(hits) >= max(1, min(30, limit)):
            break
    return hits


# ----------------------------------------------------------------------
# 下载与反编译


def _existing_source(slug: str) -> Path | None:
    root = library_sources_dir() / _safe_name(slug)
    if not root.is_dir():
        return None
    candidates = [item for item in root.iterdir() if item.is_dir() and any(item.rglob("*.java"))]
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    return candidates[0]


def prepare_reference(
    project: str,
    *,
    loader: str = "",
    mc: str = "",
    version_id: str = "",
    force: bool = False,
    max_bytes: int = MAX_MOD_BYTES,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """下载并反编译一个 Modrinth 模组，返回源码目录与索引。"""
    project_id = str(project or "").strip()
    if not project_id:
        raise ReferenceError("缺少 Modrinth 项目 slug 或 ID。")
    info = modlibrary.project(project_id)
    slug = str(info.get("slug") or project_id)
    if not force:
        existing = _existing_source(slug)
        if existing is not None:
            index = ensure_index(existing)
            return {
                "project": info,
                "slug": slug,
                "sources": str(existing),
                "files": int(index.get("file_count") or 0),
                "cached": True,
                "license": info.get("license") or "",
                "notice": LICENSE_NOTICE,
            }
    chosen: dict[str, Any] | None = None
    if version_id:
        for item in modlibrary.versions(project_id, loader=loader, mc=mc):
            if item.get("id") == version_id:
                chosen = item
                break
    if chosen is None:
        chosen = modlibrary.pick_version(project_id, loader=loader, mc=mc)
    if chosen is None:
        raise ReferenceError(
            f"没有匹配版本（loader={loader or '任意'}，mc={mc or '任意'}）。"
        )
    file_info = chosen.get("file") or {}
    size = int(file_info.get("size") or 0)
    if size and size > max_bytes:
        raise ReferenceError(
            f"{slug} 的目标版本有 {size / 1024 / 1024:.0f} MB，"
            f"超过单模组参考上限 {max_bytes / 1024 / 1024:.0f} MB，已跳过。"
        )
    if progress is not None:
        progress(5, f"下载参考模组：{info.get('title') or slug}")
    jar = modlibrary.download(chosen, progress=progress, cancel=cancel)
    if progress is not None:
        progress(45, "反编译参考模组…")
    result = modlibrary.decompile(
        jar,
        project_slug=slug,
        version_label=str(chosen.get("version_number") or chosen.get("id") or ""),
        progress=progress,
    )
    source_dir = Path(str(result.get("sources") or ""))
    index = ensure_index(source_dir)
    if progress is not None:
        progress(100, f"参考源码就绪：{index.get('file_count', 0)} 个文件")
    return {
        "project": info,
        "slug": slug,
        "version": chosen,
        "sources": str(source_dir),
        "files": int(index.get("file_count") or 0),
        "classes": int(index.get("class_count") or 0),
        "jar": str(jar),
        "cached": False,
        "license": info.get("license") or "",
        "notice": LICENSE_NOTICE,
    }


# ----------------------------------------------------------------------
# 自动模式


def auto_reference(
    text: str,
    *,
    loader: str = "",
    mc: str = "",
    max_mods: int = 2,
    max_snippets: int = 8,
    max_bytes: int = MAX_MOD_BYTES,
    force: bool = False,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """一条龙：识别功能 -> 在线搜索 -> 反编译 -> 返回可用代码片段。"""
    search = search_references(text, loader=loader, mc=mc, limit=max(6, max_mods * 3))
    features: list[Feature] = list(search.get("features") or [])
    candidates: list[dict[str, Any]] = list(search.get("candidates") or [])
    tokens = feature_tokens(features) + _tokenize_query(text)
    prepared: list[dict[str, Any]] = []
    notes: list[str] = list(search.get("notes") or [])
    for index, hit in enumerate(candidates[: max(1, min(6, max_mods))]):
        if cancel is not None and cancel.is_set():
            notes.append("已取消。")
            break
        slug = str(hit.get("slug") or hit.get("id") or "")
        if not slug:
            continue
        try:
            if progress is not None:
                progress(0, f"准备参考 {index + 1}/{max_mods}：{hit.get('title') or slug}")
            prepared.append(
                prepare_reference(
                    slug,
                    loader=loader,
                    mc=mc,
                    force=force,
                    max_bytes=max_bytes,
                    progress=progress,
                    cancel=cancel,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单个模组失败不影响整体
            notes.append(f"{slug}：{exc}")
    roots = [Path(item["sources"]) for item in prepared if item.get("sources")]
    if not roots:
        # 即使本次没有下载成功，也允许检索之前已经反编译过的参考库。
        roots = _all_source_dirs()
    snippets = search_snippets(
        text,
        tokens=tokens,
        roots=roots,
        limit=max_snippets,
    )
    return {
        "features": features,
        "queries": search.get("queries") or [],
        "candidates": candidates,
        "prepared": prepared,
        "snippets": snippets,
        "notes": notes,
        "license_notice": LICENSE_NOTICE,
    }


def render_auto(result: dict[str, Any], *, snippet_limit: int = 6) -> str:
    """把 auto_reference 的结果渲染成可直接放进模型上下文的 Markdown。"""
    features: list[Feature] = list(result.get("features") or [])
    prepared = list(result.get("prepared") or [])
    snippets = list(result.get("snippets") or [])
    notes = list(result.get("notes") or [])
    lines: list[str] = []
    if features:
        lines.append(
            "识别到的功能类型："
            + "、".join(f"{item.label}（{item.id}）" for item in features)
        )
        for item in features:
            if item.notes:
                lines.append(f"- {item.label}：{item.notes}")
    if prepared:
        lines.append("\n已下载并反编译的参考模组：")
        for item in prepared:
            project = item.get("project") or {}
            lines.append(
                f"- {project.get('title') or item.get('slug')} · "
                f"{item.get('files', 0)} 个文件 / {item.get('classes', 0)} 个类 · "
                f"源码 `{item.get('sources')}` · 许可证 {item.get('license') or '未知'}"
            )
    elif result.get("candidates"):
        lines.append("\n没有本地参考源码；候选模组（需要时可 prepare）：")
        for item in result.get("candidates")[:6]:
            lines.append(
                f"- {item.get('title')}（slug={item.get('slug') or item.get('id')}，"
                f"{int(item.get('downloads') or 0):,} 次下载）"
            )
    if snippets:
        lines.append("\n相关代码片段（只读参考，不要逐字复制）：")
        for hit in snippets[:snippet_limit]:
            lines.append(
                f"\n### {hit.get('relative')}（score={hit.get('score')}，"
                f"类：{', '.join(hit.get('classes') or [])[:160]}）\n"
                f"```java\n{hit.get('snippet')}\n```"
            )
    if notes:
        lines.append("\n备注：")
        lines.extend(f"- {note}" for note in notes[:12])
    lines.append("\n" + str(result.get("license_notice") or LICENSE_NOTICE))
    return "\n".join(lines)


def status() -> dict[str, Any]:
    summary = modlibrary.library_summary()
    dirs = _all_source_dirs()
    indexed = 0
    files = 0
    classes = 0
    for item in dirs:
        try:
            data = ensure_index(item)
        except Exception:  # noqa: BLE001
            continue
        indexed += 1
        files += int(data.get("file_count") or 0)
        classes += int(data.get("class_count") or 0)
    return {
        **summary,
        "indexed_sources": indexed,
        "indexed_files": files,
        "indexed_classes": classes,
        "source_roots": [str(item) for item in dirs],
    }
