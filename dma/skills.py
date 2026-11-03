from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import paths

SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,63}$")
MAX_SKILL_CHARS = 60_000


class SkillError(Exception):
    pass


@dataclass
class Skill:
    id: str
    name: str
    description: str
    body: str
    enabled: bool = True
    builtin: bool = False
    updated_at: str = ""

    def markdown(self) -> str:
        return (
            "---\n"
            f"id: {self.id}\n"
            f"name: {self.name}\n"
            f"description: {self.description}\n"
            f"enabled: {'true' if self.enabled else 'false'}\n"
            f"builtin: {'true' if self.builtin else 'false'}\n"
            f"updated_at: {self.updated_at}\n"
            "---\n\n"
            f"{self.body.strip()}\n"
        )


BUILTIN_SKILLS: list[dict] = [
    {
        "id": "fabric-scaffold",
        "name": "Fabric 工程搭建",
        "description": "从零创建 Fabric 模组：fabric-loom、gradle 配置、入口类、注册、资源与开发环境启动。",
        "body": """# Fabric 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否需要 Datagen、是否允许 Mixin。缺任何一项先问，不要猜。

## 版本要点
- 1.20.1 及更早：Java 17，Yarn 映射，`fabric-loom` 0.10.x/1.0.x 皆可，注意 `FabricLoader` 与 `FabricAPI` 版本。
- 1.20.5–1.21.1：Java 21，组件与数据包格式变化较大，注册仍走 `Registry`，但物品组件用 `DataComponentType`。
- 1.21.2+：物品/方块注册与模型 JSON 有调整，先核对当前 Yarn 映射中的方法签名。
- 永远使用选定 MC 版本对应的 Yarn 映射，不写 `class_1234` 这类混淆名。

## 最小目录
```
gradle.properties
settings.gradle
build.gradle
src/main/java/<package>/<ModClass>.java
src/main/resources/fabric.mod.json
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## build.gradle 关键点
- 插件：`fabric-loom` 版本必须与 Gradle 版本匹配。
- `minecraft`、`yarn_mappings`、`loader_version`、`fabric_version` 从 `gradle.properties` 读取。
- `JavaVersion.VERSION_17` 或 `VERSION_21` 必须与 MC 版本一致。
- `processResources` 要把 `fabric.mod.json` 里的 `${version}` 等占位符展开。
- 需要 Datagen 时加 `fabric-api` 的 datagen 依赖和 `fabricApi { configureDataGeneration() }`。

## 入口类
- 实现 `ModInitializer`，在 `onInitialize()` 里注册物品、方块、事件、网络包。
- 客户端入口实现 `ClientModInitializer`，只放渲染、按键、GUI、粒子。
- `fabric.mod.json` 的 `entrypoints` 同时声明 `main` 与 `client`。

## 交付
- 完整文件路径 + 完整代码，不要省略 import。
- 说明 `./gradlew build` 与 `./gradlew runClient`。
- 给出进游戏后的验证步骤。
""",
    },
    {
        "id": "neoforge-scaffold",
        "name": "NeoForge 工程搭建",
        "description": "从零创建 NeoForge 模组：NeoGradle、mods.toml、DeferredRegister、事件总线与客户端/服务端分离。",
        "body": """# NeoForge 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否允许 Mixin。NeoForge 只支持较新的 MC 版本，先确认目标版本有对应 NeoForge。

## 版本要点
- 1.20.1：NeoForge 早期版本，部分 API 仍沿用 Forge 写法。
- 1.20.4+：`mods.toml` 迁移为 `META-INF/neoforge.mods.toml`。
- 1.21+：注册、事件、网络包 API 有变化，使用 Mojmap 官方映射。
- 不写 SRG 名（`m_12345_`）或混淆名。

## 最小目录
```
build.gradle
gradle.properties
settings.gradle
src/main/java/<package>/<ModClass>.java
src/main/resources/META-INF/neoforge.mods.toml
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## 注册
- 使用 `DeferredRegister.Items` / `DeferredRegister.Blocks` / `DeferredRegister<EntityType<?>>`。
- 在模组构造函数里把 register 挂到 `modEventBus`。
- 方块实体、菜单、网络包各自用对应的 DeferredRegister 或 `RegisterPayloadHandlersEvent`。
- 客户端专用内容放 `@Mod(value = MODID, dist = Dist.CLIENT)` 或 `FMLClientSetupEvent`/`RegisterClientExtensionsEvent`。

## 事件
- 模组生命周期事件走 `modEventBus`。
- 游戏逻辑事件走 `NeoForge.EVENT_BUS`。
- 不要把所有监听器都塞进主类；按功能拆 `event/` 包。

## 交付
- 完整 build.gradle 与 mods.toml，不要漏 `modLoader="javafml"`、`loaderVersion`、`license`。
- 说明 `./gradlew build`、`./gradlew runClient`、`./gradlew runServer`。
- 网络包、方块实体、库存同步必须标【⚠️人工复核】。
""",
    },
    {
        "id": "incremental-edit",
        "name": "增量修改现有工程",
        "description": "在已有工程上改功能：先读文件、只改必要部分、保留原有结构与命名。",
        "body": """# 增量修改现有工程

## 规则
1. 先列出工程树，确认入口类、注册类、资源目录和构建脚本。
2. 修改前必须读取目标文件；不要凭记忆重写整个文件。
3. 只输出发生变化的完整文件，不要顺手重构无关代码。
4. 新文件才给完整内容；已有文件给“修改后的完整文件”或明确的最小 diff。
5. 保持原有包名、命名习惯、代码风格和注释语言。
6. 如果改动涉及注册表、事件总线、依赖或资源，连带更新对应文件。

## 检查
- 新增 import 是否齐全。
- 注册是否挂在正确的总线/时机。
- 客户端代码是否误入服务端。
- 资源路径是否与 Mod ID、注册名一致。
- `gradle.properties` / `build.gradle` 是否需要新增依赖。
""",
    },
    {
        "id": "crash-triage",
        "name": "崩溃与编译报错排查",
        "description": "解析 gradle 编译错误、crash report、Mixin 注入失败，定位文件行号并给出完整修复文件。",
        "body": """# 崩溃与编译报错排查

## 先分类
1. **编译错误**：javac 报错，通常是类型、方法签名、import、映射版本不匹配。
2. **注册时序错误**：`Registry is already frozen`、`NullPointerException` during init。
3. **客户端/服务端不一致**：`NoClassDefFoundError`、`ClassNotFoundException`、专用服务端崩溃。
4. **Mixin 注入失败**：`InjectionError`、`InvalidInjectionException`、目标方法签名变化。
5. **版本 API 变更**：方法被移除或改名，旧教程写法不再适用。

## 输出
- 先给结论：错误类型 + 根因。
- 给定位：文件、行号、相关调用链。
- 给修复后的完整文件或完整方法，不只说“这里错了”。
- 如果无法从日志确定，列出还需要用户提供的文件/日志，不要编造。

## Mixin
- 任何 Mixin 修复都要标【⚠️人工复核】。
- 检查目标类、方法名、描述符、注入点、`remap` 设置是否与当前映射一致。
- 提醒用户可能与其他 Mixin 模组冲突。
""",
    },
    {
        "id": "porting",
        "name": "版本与加载器移植",
        "description": "把模组移植到其他 MC 版本或 Fabric/NeoForge：映射、API、资源格式与构建脚本迁移清单。",
        "body": """# 版本与加载器移植

## 流程
1. 记录源版本/目标版本、源加载器/目标加载器。
2. 对照官方迁移说明和当前映射，列出破坏性变更。
3. 先迁构建脚本与元数据，再迁注册与事件，最后迁资源和数据包格式。
4. 逐文件给出改动，不要一次性全量重写。

## 常见迁移点
- Yarn ↔ Mojmap：类名、方法名、字段名全部变化。
- 注册 API：`Registry.register`、`DeferredRegister`、`RegisterEvent` 的时机与签名。
- 物品组件：1.20.5+ 用 `DataComponentType` 替代大量 NBT。
- 网络包：Fabric `PayloadTypeRegistry` / NeoForge `RegisterPayloadHandlersEvent`。
- 资源包格式：`pack_format`、模型 JSON、方块状态 JSON。
- Mixin：目标方法描述符随版本变化，必须重新核对。

## 风险
- 移植后必须实际编译；编译通过不代表运行正常。
- 涉及世界生成、方块实体、网络同步的改动标【⚠️人工复核】。
""",
    },
    {
        "id": "datagen-assets",
        "name": "Datagen 与资源文件",
        "description": "生成方块状态、模型、配方、战利品表、标签、语言文件、音效注册与纹理规格。",
        "body": """# Datagen 与资源文件

## 原则
- 目标版本支持 Datagen 时优先用 Datagen，不手写几十个重复 JSON。
- 仍要给出生成器类、`DataGeneratorEntrypoint`（Fabric）或 `GatherDataEvent`（NeoForge）的完整代码。
- 不支持 Datagen 的版本，逐个给出 JSON 的完整路径与内容。

## 必备资源
- `assets/<modid>/lang/en_us.json`
- `assets/<modid>/lang/zh_cn.json`
- `assets/<modid>/models/item/<name>.json`
- `assets/<modid>/models/block/<name>.json`
- `assets/<modid>/blockstates/<name>.json`
- `data/<modid>/recipes/<name>.json`
- `data/<modid>/loot_tables/blocks/<name>.json`（版本不同路径可能变化）
- `assets/<modid>/sounds.json`（有音效时）

## 纹理
- 物品/方块：16x16 或 32x32 PNG，必须带透明通道。
- 给出像素级描述：主色、描边、高光、阴影、图案位置。
- 纹理路径必须与模型引用一致。
- 复杂模型先给 Blockbench 设计描述，再导出 MC JSON。

## 音效
- `sounds.json` 注册名、字幕键、音频文件路径。
- 建议 OGG Vorbis，44.1kHz，单声道/立体声说明。
""",
    },
    {
        "id": "mixin-safety",
        "name": "Mixin 安全规范",
        "description": "只在必要时使用 Mixin，并给出目标、注入点、兼容性与人工复核清单。",
        "body": """# Mixin 安全规范

## 使用前
- 先确认能否用官方事件/API 实现；能用事件就不要 Mixin。
- 必须说明为什么非 Mixin 不可。

## 写法
- `@Mixin(TargetClass.class)`，目标类必须使用当前映射名。
- 注入点优先 `@Inject(at = @At("HEAD"/"TAIL"), method = "...")`，避免脆弱的 `@Redirect`/`@ModifyConstant`。
- 方法签名、描述符、`remap` 必须与目标版本一致。
- 客户端 Mixin 放 `mixins.<modid>.client.json`，服务端不要加载客户端类。

## 必须标注
【⚠️人工复核】Mixin 修改了 MC 内部实现，可能随版本失效、与其他模组冲突或导致崩溃。交付时提醒用户备份存档并在开发环境先测。

## 排查
- `InjectionError`：目标方法不存在或描述符不匹配。
- `ClassNotFoundException`：Mixin 配置文件名/包路径错误。
- `NoSuchMethodError`：映射或版本不一致。
""",
    },
    {
        "id": "client-server-split",
        "name": "客户端与服务端分离",
        "description": "渲染、GUI、粒子、输入只放客户端；网络同步与方块实体逻辑正确分侧。",
        "body": """# 客户端与服务端分离

## 必须放客户端
- 渲染器、模型、粒子、屏幕、HUD、按键绑定、客户端事件、客户端 Mixin。
- Fabric：`ClientModInitializer`、`ClientTickEvents`、`KeyBindingHelper`。
- NeoForge：`Dist.CLIENT`、`FMLClientSetupEvent`、`RegisterClientExtensionsEvent`。

## 必须放通用/服务端
- 物品/方块/实体注册、配方、战利品表、世界生成、命令、服务端逻辑。
- 不要在通用代码里直接 import 客户端类。

## 网络
- 自定义网络包必须标【⚠️人工复核】。
- 说明发送侧、接收侧、线程切换（客户端主线程/服务端主线程）。
- 大对象不要每 tick 同步；只同步必要字段。

## 检查
- 专用服务端能否启动（`runServer`）。
- 客户端代码是否被服务端类加载。
- 方块实体/库存/能量同步是否只在需要时触发。
""",
    },
    {
        "id": "delivery-checklist",
        "name": "交付前自检",
        "description": "生成或修改模组后，检查完整性、编译命令、测试步骤与风险标注。",
        "body": """# 交付前自检

按顺序检查：

1. **三项基础信息**：MC 版本、加载器、Mod ID 是否明确且一致。
2. **文件完整性**：构建脚本、元数据、入口类、注册、资源、语言文件是否齐全。
3. **映射**：没有混淆名，没有旧版已删除的方法。
4. **分侧**：客户端代码没有进入服务端。
5. **资源**：每个注册名都有对应模型/纹理/语言键。
6. **依赖**：Fabric API / NeoForge 版本写在构建脚本里。
7. **风险标注**：Mixin、网络包、方块实体、世界生成、大量原版修改必须标【⚠️人工复核】。
8. **验证步骤**：给出进游戏后具体做什么来验证功能。
9. **命令**：`./gradlew build`、`./gradlew runClient`（必要时 `runServer`）。

最后明确一句：编译成功不等于游戏内运行正常。
""",
    },
    {
        "id": "modrinth-lookup",
        "name": "资料查询与依赖核对",
        "description": "通过连接器查询 Modrinth / Fabric Meta / GitHub，核对模组版本、依赖与加载器兼容性。",
        "body": """# 资料查询与依赖核对

## 可用连接器
- Modrinth：`https://api.modrinth.com/v2`
- Fabric Meta：`https://meta.fabricmc.net/v2`
- GitHub：`https://api.github.com`

## 查询顺序
1. 先查目标 MC 版本下加载器是否存在。
2. 再查依赖模组的版本范围，不要写“最新版”这种模糊值。
3. 查不到就明确说查不到，不要编造版本号。

## 输出
- 给出查询到的版本号、下载页或项目 slug。
- 依赖写进 `build.gradle` / `gradle.properties` 时，注明来源。
- 如果 API 限流或网络失败，告诉用户稍后重试，不要假装成功。
""",
    },
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _parse(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip()
    return meta, parts[2].lstrip("\n")


class SkillStore:
    def __init__(self, folder: Path | None = None) -> None:
        self.folder = Path(folder or paths.skills_dir())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ensure_builtin()

    def _path(self, skill_id: str) -> Path:
        if not SKILL_ID_RE.fullmatch(skill_id or ""):
            raise SkillError("技能 ID 只能使用小写字母、数字、点、下划线和短横线。")
        return self.folder / f"{skill_id}.md"

    def ensure_builtin(self) -> None:
        for item in BUILTIN_SKILLS:
            path = self.folder / f"{item['id']}.md"
            if path.is_file():
                continue
            skill = Skill(
                id=item["id"],
                name=item["name"],
                description=item["description"],
                body=item["body"],
                enabled=True,
                builtin=True,
                updated_at=_now(),
            )
            path.write_text(skill.markdown(), encoding="utf-8")

    def list(self) -> list[Skill]:
        skills: list[Skill] = []
        for path in sorted(self.folder.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8-sig")
            except OSError:
                continue
            meta, body = _parse(text)
            skill_id = meta.get("id") or path.stem
            skills.append(
                Skill(
                    id=skill_id,
                    name=meta.get("name") or skill_id,
                    description=meta.get("description") or "",
                    body=body,
                    enabled=meta.get("enabled", "true").lower() != "false",
                    builtin=meta.get("builtin", "false").lower() == "true",
                    updated_at=meta.get("updated_at") or "",
                )
            )
        return skills

    def get(self, skill_id: str) -> Skill | None:
        path = self._path(skill_id)
        if not path.is_file():
            return None
        meta, body = _parse(path.read_text(encoding="utf-8-sig"))
        return Skill(
            id=meta.get("id") or skill_id,
            name=meta.get("name") or skill_id,
            description=meta.get("description") or "",
            body=body,
            enabled=meta.get("enabled", "true").lower() != "false",
            builtin=meta.get("builtin", "false").lower() == "true",
            updated_at=meta.get("updated_at") or "",
        )

    def save(self, skill: Skill) -> Skill:
        if len(skill.body) > MAX_SKILL_CHARS:
            raise SkillError("技能正文超过 60,000 字符。")
        skill.updated_at = _now()
        self._path(skill.id).write_text(skill.markdown(), encoding="utf-8")
        return skill

    def delete(self, skill_id: str) -> None:
        skill = self.get(skill_id)
        if skill is None:
            return
        if skill.builtin:
            raise SkillError("内置技能不能删除，可以停用或恢复默认内容。")
        self._path(skill_id).unlink(missing_ok=True)

    def reset(self, skill_id: str) -> Skill | None:
        item = next((row for row in BUILTIN_SKILLS if row["id"] == skill_id), None)
        if not item:
            return None
        skill = Skill(
            id=item["id"],
            name=item["name"],
            description=item["description"],
            body=item["body"],
            enabled=True,
            builtin=True,
            updated_at=_now(),
        )
        return self.save(skill)

    def set_enabled(self, skill_id: str, enabled: bool) -> None:
        skill = self.get(skill_id)
        if skill is None:
            raise SkillError("技能不存在。")
        skill.enabled = bool(enabled)
        self.save(skill)

    def catalog_text(self, include_disabled: bool = False) -> str:
        rows = [
            f"- {item.id}｜{item.name}：{item.description}"
            for item in self.list()
            if include_disabled or item.enabled
        ]
        return "\n".join(rows) if rows else "（暂无技能）"

    def active_bodies(self, limit: int = 6) -> str:
        items = [item for item in self.list() if item.enabled][:limit]
        if not items:
            return ""
        chunks = ["以下是当前启用的技能正文，按需参考："]
        for item in items:
            chunks.append(f"\n## 技能：{item.name}（{item.id}）\n{item.body.strip()}")
        return "\n".join(chunks)
