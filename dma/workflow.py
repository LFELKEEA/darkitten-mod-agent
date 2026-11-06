from __future__ import annotations

import json
import re
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .spec import ModSpec, checklist_score, load_spec, render_manual

WORKFLOW_FILE = ".dma/workflow.json"
MANUAL_FILE = "MANUAL.md"

STAGES = ("draft", "planning", "review", "building", "verifying", "done")
STAGE_LABELS = {
    "draft": "需求采集",
    "planning": "AI 规划",
    "review": "确认规格",
    "building": "施工中",
    "verifying": "编译验证",
    "done": "已完成",
}
STAGE_HINTS = {
    "draft": "用一句话描述你想做的模组，AI 会先拆完整规格。",
    "planning": "AI 正在一次性拆解玩法、数值、美术和资源方案。",
    "review": "检查右侧规格说明书，确认后才会创建工程并开始施工。",
    "building": "正在按任务清单写代码、画纹理、补配方并编译。",
    "verifying": "编译已通过，正在读取真实 JAR 生成成品说明书。",
    "done": "成品已生成。继续提要求会走增量修改，保留已有源码。",
}

TASK_BLUEPRINT = (
    ("scaffold", "搭建工程骨架"),
    ("register", "注册内容"),
    ("behavior", "实现玩法逻辑"),
    ("assets", "生成纹理与模型"),
    ("recipes", "补齐配方与掉落"),
    ("lang", "语言文件与提示"),
    ("build", "编译并修复"),
    ("verify", "按验收标准检查"),
)


@dataclass
class WorkTask:
    id: str
    label: str
    status: str = "pending"


@dataclass
class WorkflowState:
    stage: str = "draft"
    spec_revision: int = 0
    tasks: list[dict[str, str]] = field(default_factory=list)
    current: str = ""
    last_build_ok: bool | None = None
    jar: str = ""
    manual: str = ""
    confirmed_at: str = ""
    updated_at: str = ""

    def normalized(self) -> "WorkflowState":
        if self.stage not in STAGES:
            self.stage = "draft"
        self.tasks = [
            {
                "id": str(item.get("id") or ""),
                "label": str(item.get("label") or ""),
                "status": str(item.get("status") or "pending"),
            }
            for item in self.tasks
            if isinstance(item, dict) and item.get("id")
        ]
        return self

    def task_counts(self) -> tuple[int, int]:
        done = sum(1 for item in self.tasks if item.get("status") == "done")
        return done, len(self.tasks)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def plan_tasks(spec: ModSpec) -> list[dict[str, str]]:
    """按规格生成施工清单。内容越复杂，步骤越具体。"""
    tasks: list[dict[str, str]] = []
    for task_id, label in TASK_BLUEPRINT:
        if task_id == "assets" and not spec.assets:
            continue
        if task_id == "recipes" and not spec.obtain:
            continue
        tasks.append({"id": task_id, "label": label, "status": "pending"})
    for index, feature in enumerate(spec.features[:6], 1):
        tasks.append(
            {
                "id": f"feature_{index}",
                "label": f"实现：{feature[:40]}",
                "status": "pending",
            }
        )
    return tasks


def state_path(root: Path) -> Path:
    return Path(root) / WORKFLOW_FILE


def load_state(root: Path, spec: ModSpec | None = None) -> WorkflowState:
    target = state_path(root)
    if target.is_file():
        try:
            data = json.loads(target.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            allowed = set(WorkflowState.__dataclass_fields__)
            try:
                return WorkflowState(
                    **{key: value for key, value in data.items() if key in allowed}
                ).normalized()
            except TypeError:
                pass
    current = spec if spec is not None else load_spec(root)
    state = WorkflowState()
    if current is not None:
        state.spec_revision = current.revision
        if current.confirmed():
            state.stage = "building"
            state.tasks = plan_tasks(current)
        elif current.empty():
            state.stage = "draft"
        else:
            state.stage = "review"
    return state.normalized()


def save_state(root: Path, state: WorkflowState) -> Path:
    state.updated_at = _now()
    target = state_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(asdict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return target


def set_stage(root: Path, state: WorkflowState, stage: str) -> WorkflowState:
    if stage not in STAGES:
        raise ValueError(f"未知阶段：{stage}")
    state.stage = stage
    save_state(root, state)
    return state


def confirm(root: Path, spec: ModSpec) -> tuple[ModSpec, WorkflowState]:
    spec.status = "confirmed"
    spec.revision += 1
    spec.updated_at = _now()
    from .spec import save_spec

    save_spec(root, spec)
    state = load_state(root, spec)
    state.stage = "building"
    state.spec_revision = spec.revision
    state.tasks = plan_tasks(spec)
    state.confirmed_at = _now()
    state.jar = ""
    state.manual = ""
    save_state(root, state)
    return spec, state


def back_to_planning(root: Path, spec: ModSpec) -> tuple[ModSpec, WorkflowState]:
    from .spec import save_spec

    spec.status = "draft"
    spec.updated_at = _now()
    save_spec(root, spec)
    state = load_state(root, spec)
    state.stage = "planning"
    state.spec_revision = spec.revision
    state.tasks = []
    save_state(root, state)
    return spec, state


def start_task(root: Path, state: WorkflowState, task_id: str) -> WorkflowState:
    for item in state.tasks:
        if item.get("id") == task_id:
            item["status"] = "doing"
            state.current = task_id
            break
    save_state(root, state)
    return state


def complete_task(root: Path, state: WorkflowState, task_id: str) -> WorkflowState:
    for item in state.tasks:
        if item.get("id") == task_id:
            item["status"] = "done"
            break
    if state.current == task_id:
        state.current = ""
    save_state(root, state)
    return state


def _is_primary_jar(name: str) -> bool:
    lower = name.lower()
    return not any(
        token in lower
        for token in ("-sources", "-javadoc", "-dev", "-api", "-test")
    )


def find_jar(root: Path) -> Path | None:
    folder = Path(root) / "build" / "libs"
    if not folder.is_dir():
        return None
    jars = [item for item in folder.glob("*.jar") if item.is_file()]
    if not jars:
        return None
    primary = [item for item in jars if _is_primary_jar(item.name)]
    pool = primary or jars
    return max(pool, key=lambda item: item.stat().st_mtime)


@dataclass
class ArtifactInventory:
    jar: str = ""
    mod_name: str = ""
    mod_id: str = ""
    mc_version: str = ""
    loader: str = ""
    items: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    recipes: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    textures: list[str] = field(default_factory=list)
    sounds: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    lang: dict[str, str] = field(default_factory=dict)

    def content_count(self) -> int:
        return len(self.items) + len(self.blocks) + len(self.entities) + len(self.commands)

    def summary(self) -> str:
        return (
            f"{self.mod_name or self.mod_id or '模组'} · "
            f"{self.content_count()} 个注册内容 · {len(self.recipes)} 个配方 · "
            f"{len(self.textures)} 张纹理"
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["content_count"] = self.content_count()
        return data


def _read_json(zf: zipfile.ZipFile, name: str) -> Any:
    try:
        return json.loads(zf.read(name).decode("utf-8-sig", "replace"))
    except (KeyError, ValueError, OSError):
        return None


def _scan_commands(root: Path) -> list[str]:
    folder = Path(root) / "src"
    if not folder.is_dir():
        return []
    found: list[str] = []
    for path in folder.rglob("*.java"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in re.findall(r"\.literal\(\s*\"([A-Za-z0-9_:-]{2,40})\"\s*\)", text):
            if match not in found:
                found.append(match)
        if len(found) >= 20:
            break
    return found


def inspect_artifacts(root: Path) -> ArtifactInventory:
    """读取最终 JAR，而不是相信模型的口头描述。"""
    root = Path(root)
    inventory = ArtifactInventory()
    jar = find_jar(root)
    if jar is None:
        inventory.commands = _scan_commands(root)
        return inventory
    inventory.jar = jar.relative_to(root).as_posix()
    namespaces: dict[str, int] = {}
    try:
        with zipfile.ZipFile(jar) as zf:
            names = zf.namelist()
            for name in names:
                parts = name.split("/")
                if len(parts) > 2 and parts[0] in {"assets", "data"}:
                    namespaces[parts[1]] = namespaces.get(parts[1], 0) + 1

            lang_candidates = [
                name
                for name in names
                if re.search(r"assets/[^/]+/lang/(zh_cn|en_us)\.json$", name)
            ]
            for name in lang_candidates:
                data = _read_json(zf, name)
                if isinstance(data, dict):
                    for key, value in data.items():
                        if isinstance(value, str):
                            inventory.lang[str(key)] = value

            fabric = _read_json(zf, "fabric.mod.json")
            if isinstance(fabric, dict):
                inventory.mod_id = str(fabric.get("id") or "")
                inventory.mod_name = str(fabric.get("name") or "")
                depends = fabric.get("depends") if isinstance(fabric.get("depends"), dict) else {}
                inventory.mc_version = str(depends.get("minecraft") or "")
            toml_name = next(
                (name for name in names if name.endswith("neoforge.mods.toml")),
                "",
            )
            if toml_name:
                try:
                    text = zf.read(toml_name).decode("utf-8-sig", "replace")
                except OSError:
                    text = ""
                id_match = re.search(r'modId\s*=\s*"([^"]+)"', text)
                name_match = re.search(r'displayName\s*=\s*"([^"]+)"', text)
                if id_match:
                    inventory.mod_id = inventory.mod_id or id_match.group(1)
                if name_match:
                    inventory.mod_name = inventory.mod_name or name_match.group(1)

            for name in names:
                if name.endswith("/"):
                    continue
                lower = name.lower()
                parts = name.split("/")
                if len(parts) < 4 or parts[0] not in {"assets", "data"}:
                    if lower.endswith("sounds.json") and len(parts) >= 3:
                        data = _read_json(zf, name)
                        if isinstance(data, dict):
                            inventory.sounds.extend(list(data.keys())[:40])
                    continue
                ns = parts[1]
                if "/models/item/" in name and lower.endswith(".json"):
                    inventory.items.append(Path(name).stem)
                elif "/models/block/" in name and lower.endswith(".json"):
                    inventory.blocks.append(Path(name).stem)
                elif "/blockstates/" in name and lower.endswith(".json"):
                    inventory.blocks.append(Path(name).stem)
                elif re.search(r"/recipes?/[^/]+\.json$", name):
                    inventory.recipes.append(Path(name).stem)
                elif "/loot_table/entities/" in name or "/loot_tables/entities/" in name:
                    inventory.entities.append(Path(name).stem)
                elif "/textures/entity/" in name and lower.endswith(".png"):
                    inventory.entities.append(Path(name).stem)
                elif "/models/" in name and lower.endswith(".json"):
                    inventory.models.append(Path(name).stem)
                elif "/textures/" in name and lower.endswith(".png"):
                    inventory.textures.append(Path(name).stem)
    except (zipfile.BadZipFile, OSError):
        pass

    def unique(items: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for item in items:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    inventory.items = unique(inventory.items)
    inventory.blocks = unique(inventory.blocks)
    inventory.entities = unique(inventory.entities)
    inventory.recipes = unique(inventory.recipes)
    inventory.models = unique(inventory.models)
    inventory.textures = unique(inventory.textures)
    inventory.sounds = unique(inventory.sounds)
    inventory.commands = unique(_scan_commands(root))
    if not inventory.mod_id:
        inventory.mod_id = max(namespaces, key=namespaces.get) if namespaces else ""
    if not inventory.mc_version:
        inventory.mc_version = ""
    if not inventory.mod_name:
        spec = load_spec(root)
        if spec is not None:
            inventory.mod_name = spec.title
            inventory.mc_version = inventory.mc_version or spec.mc_version
            inventory.loader = spec.loader
    return inventory


def _display(inventory: ArtifactInventory, kind: str, ident: str) -> str:
    ns = inventory.mod_id or "mod"
    for key in (f"{kind}.{ns}.{ident}", f"{kind}.{ident}"):
        if inventory.lang.get(key):
            return f"{inventory.lang[key]}（{ident}）"
    return ident


def render_artifact_manual(
    spec: ModSpec | None, inventory: ArtifactInventory
) -> str:
    name = inventory.mod_name or (spec.title if spec else "") or "未命名模组"
    lines = [
        f"# {name} · 成品说明书",
        "",
        "> 内容由最终 JAR 的真实文件结构生成，不是模型复述。",
        "",
        "## 产物",
        "",
        f"- JAR：`{inventory.jar or '尚未生成'}`",
        f"- Mod ID：`{inventory.mod_id or '未知'}`",
    ]
    if inventory.mc_version:
        lines.append(f"- 游戏版本：{inventory.mc_version}")
    if inventory.loader:
        lines.append(f"- 加载器：{inventory.loader}")
    lines.extend(
        [
            "",
            "## 已注册内容",
            "",
            f"- 物品：{len(inventory.items)} 个",
            f"- 方块：{len(inventory.blocks)} 个",
            f"- 实体：{len(inventory.entities)} 个",
            f"- 命令：{len(inventory.commands)} 个",
            "",
        ]
    )

    def listing(items: list[str], kind: str, limit: int = 40) -> str:
        if not items:
            return "- （没有识别到）"
        rows = [f"- {_display(inventory, kind, item)}" for item in items[:limit]]
        if len(items) > limit:
            rows.append(f"- …还有 {len(items) - limit} 个")
        return "\n".join(rows)

    if inventory.items:
        lines.extend(["### 物品", "", listing(inventory.items, "item"), ""])
    if inventory.blocks:
        lines.extend(["### 方块", "", listing(inventory.blocks, "block"), ""])
    if inventory.entities:
        lines.extend(["### 实体", "", listing(inventory.entities, "entity"), ""])
    if inventory.commands:
        lines.extend(
            [
                "### 命令",
                "",
                "\n".join(f"- `/{item}`" for item in inventory.commands[:20]),
                "",
            ]
        )

    lines.extend(["## 合成配方", ""])
    if inventory.recipes:
        for item in inventory.recipes[:40]:
            lines.append(f"- {item}")
        if len(inventory.recipes) > 40:
            lines.append(f"- …还有 {len(inventory.recipes) - 40} 个")
    else:
        lines.append("- 最终 JAR 中没有识别到工作台配方。")
    lines.append("")

    lines.extend(
        [
            "## 模型与纹理",
            "",
            f"- 模型：{len(inventory.models) + len(inventory.items) + len(inventory.blocks)} 个",
            f"- 纹理：{len(inventory.textures)} 张",
            f"- 音效：{len(inventory.sounds)} 个",
            "",
        ]
    )

    lines.extend(["## 使用功能", ""])
    if spec is not None and spec.features:
        lines.extend(f"- {item}" for item in spec.features)
    elif spec is not None and spec.content:
        lines.extend(
            f"- {item.get('type') or '内容'}：{item.get('name') or item.get('id')}"
            for item in spec.content
        )
    else:
        lines.append("- 进入游戏后检查注册内容是否正常加载。")
    lines.append("")

    lines.extend(["## AI 推荐怎么玩", ""])
    if spec is not None and spec.acceptance:
        lines.extend(f"- {item}" for item in spec.acceptance)
    else:
        lines.extend(
            [
                "- 先在创造模式里确认物品/方块能在背包或物品栏中找到。",
                "- 再按配方合成一次，确认材料与产物正确。",
                "- 最后在测试存档里试玩，不要先放进主存档。",
            ]
        )
    lines.append("")

    passed, total = checklist_score(spec) if spec is not None else (0, 10)
    lines.extend(
        [
            "## 规格完成度",
            "",
            f"- 需求清单：{passed}/{total}",
            f"- 与规格差异："
            + (
                "注册内容与配方已按规格生成。"
                if inventory.content_count() or inventory.recipes
                else "最终 JAR 里还没有识别到注册内容，检查注册代码是否执行。"
            ),
            "",
            "## 安装",
            "",
            "1. 把上面的 JAR 放进 `.minecraft/mods`。",
            "2. 确认安装对应加载器与 Minecraft 版本。",
            "3. 启动一次游戏，检查日志里没有 `Missing or unsupported` 或 Mixin 报错。",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass
class FailureReport:
    category: str
    title: str
    detail: str = ""
    location: str = ""
    actions: list[str] = field(default_factory=list)
    auto_fixable: bool = False

    def render(self) -> str:
        lines = [f"【失败分析】{self.title}（{self.category}）"]
        if self.location:
            lines.append(f"位置：{self.location}")
        if self.detail:
            lines.append(f"原因：{self.detail}")
        if self.actions:
            lines.append("处理：" + "；".join(self.actions))
        lines.append("可自动修复：" + ("是" if self.auto_fixable else "否，需要人工确认"))
        return "\n".join(lines)


def analyze_build_failure(output: str, code: int = 1) -> FailureReport:
    text = output or ""
    if code == 0:
        return FailureReport("成功", "编译通过")

    location = ""
    match = re.search(r"([A-Za-z0-9_./\\-]+\.java):(\d+)", text)
    if match:
        location = f"{match.group(1)}:{match.group(2)}"

    errors = re.findall(r"(?m)^.*\berror:.*$", text)
    if not errors:
        errors = re.findall(r"(?m)^.*错误[:：].*$", text)
    detail = errors[0].strip()[:500] if errors else ""

    if re.search(r"Unsupported class file major version|invalid target release|release version .* not supported", text, re.I):
        return FailureReport(
            "Java 版本",
            "Java / 字节码版本不匹配",
            detail or "当前 JDK 版本与工程目标版本不一致。",
            location,
            ["在「环境」页切换到工程要求的 JDK", "清理 build 目录后重新编译"],
            True,
        )
    if re.search(r"Could not resolve|Could not GET|PKIX|Connection refused|UnknownHost|timed out", text, re.I):
        return FailureReport(
            "依赖下载",
            "依赖仓库连接失败",
            detail or "Gradle 无法从远程仓库拉取依赖。",
            location,
            ["程序会自动切换本地 Maven 代理并重试", "仍失败时检查网络或前置模组版本"],
            True,
        )
    if re.search(r"mixin", text, re.I) and re.search(r"failed|error|crash", text, re.I):
        return FailureReport(
            "Mixin",
            "Mixin 注入或配置失败",
            detail or "Mixin 目标类、注入点或配置与当前版本不匹配。",
            location,
            ["核对 mixins.json 的包名与类名", "优先改用官方事件，减少注入点"],
            False,
        )
    if re.search(r"cannot find symbol|找不到符号", text, re.I):
        return FailureReport(
            "编译",
            "找不到类、方法或字段",
            detail or "代码引用了当前版本不存在的 API，或漏了 import。",
            location,
            ["按目标映射核对 API 名称", "检查依赖是否已声明", "补全 import 或改用当前版本写法"],
            True,
        )
    if re.search(r"incompatible types|不兼容的类型", text, re.I):
        return FailureReport(
            "编译",
            "类型不匹配",
            detail or "返回值或参数类型与目标方法不一致。",
            location,
            ["按报错行核对泛型和返回类型", "不要用强制转换掩盖问题"],
            True,
        )
    if re.search(r"package .* does not exist|程序包.*不存在", text, re.I):
        return FailureReport(
            "依赖",
            "缺少程序包或依赖",
            detail or "build.gradle 没有声明对应依赖，或依赖版本不对。",
            location,
            ["在 build.gradle 声明依赖", "确认前置模组版本与 MC 版本匹配"],
            True,
        )
    if re.search(r"No matching variant|Could not find .*\.jar|variant .* matching", text, re.I):
        return FailureReport(
            "构建配置",
            "Gradle 依赖变体不匹配",
            detail or "依赖坐标或版本与当前加载器/游戏版本不兼容。",
            location,
            ["核对版本号与加载器", "必要时改用本地 libs/*.jar"],
            True,
        )
    if re.search(r"Execution failed for task '([^']+)'", text):
        task = re.search(r"Execution failed for task '([^']+)'", text)
        return FailureReport(
            "Gradle 任务",
            f"任务失败：{task.group(1) if task else '未知任务'}",
            detail or "任务本身失败，需要看上方第一条 error。",
            location,
            ["定位第一条 error", "修复后重新执行 build"],
            False,
        )
    if re.search(r"OutOfMemoryError|GC overhead", text, re.I):
        return FailureReport(
            "环境",
            "Gradle 内存不足",
            detail or "编译进程堆内存不够。",
            "",
            ["关闭占用内存的程序", "在 gradle.properties 提高 org.gradle.jvmargs"],
            True,
        )
    return FailureReport(
        "未知",
        "编译失败",
        detail or "日志里没有匹配到已知错误类型。",
        location,
        ["查看 Gradle 日志末尾的 What went wrong", "把第一条 error 发给我继续定位"],
        False,
    )


def finalize_build(
    root: Path, ok: bool, spec: ModSpec | None = None
) -> tuple[WorkflowState, ArtifactInventory, str]:
    """编译结束后读取真实产物，写 MANUAL.md，并把阶段推进到成品。"""
    root = Path(root)
    current = spec if spec is not None else load_spec(root)
    state = load_state(root, current)
    state.last_build_ok = bool(ok)
    if ok:
        inventory = inspect_artifacts(root)
        manual = render_artifact_manual(current, inventory)
        try:
            (root / MANUAL_FILE).write_text(manual, encoding="utf-8", newline="\n")
            (root / ".dma").mkdir(parents=True, exist_ok=True)
            (root / ".dma" / "manual.md").write_text(
                manual, encoding="utf-8", newline="\n"
            )
        except OSError:
            pass
        state.jar = inventory.jar
        state.manual = MANUAL_FILE
        # 只有真的产出 JAR 才算完成；否则停在施工阶段，避免空手报“已完成”。
        state.stage = "done" if inventory.jar else "building"
        if inventory.jar:
            for item in state.tasks:
                if item.get("id") in {"build", "verify"}:
                    item["status"] = "done"
    elif current is not None and current.confirmed():
        state.stage = "building"
    save_state(root, state)
    if ok:
        return state, inventory, manual
    return state, ArtifactInventory(), ""


def stage_block(state: WorkflowState | None, spec: ModSpec | None) -> str:
    if state is None:
        state = WorkflowState()
    passed, total = checklist_score(spec) if spec is not None else (0, 10)
    done, tasks = state.task_counts()
    return (
        "【当前工作流】\n"
        f"- 阶段：{STAGE_LABELS.get(state.stage, state.stage)}\n"
        f"- 需求清单：{passed}/{total}\n"
        f"- 施工任务：{done}/{tasks}\n"
        f"- 阶段说明：{STAGE_HINTS.get(state.stage, '')}"
    )
