from __future__ import annotations

import builtins
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
        "description": "从零创建 Fabric 模组：fabric-loom、Yarn 映射、入口类、注册、资源与开发端运行。",
        "body": """# Fabric 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否需要 Datagen、是否允许 Mixin。缺任何一项先问，不要猜。

## 版本要点
- 1.20.1：Java 17，Yarn 映射，fabric-loom 1.7.x 可用。
- 1.20.5–1.21.1：Java 21，物品组件改用 `DataComponentType`。
- 1.21.2+：注册与模型 JSON 有调整，先核对当前 Yarn 映射的方法签名。
- 永远使用目标 MC 版本对应的 Yarn 映射，不写 `class_1234` 这类混淆名。

## 最小目录
```
gradle.properties
settings.gradle
build.gradle
src/main/java/<package>/<ModId>Mod.java
src/main/resources/fabric.mod.json
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## build.gradle 关键点
- 插件 `fabric-loom`，版本与 Gradle / MC 版本匹配。
- `minecraft`、`yarn_mappings`、`loader_version`、`fabric_version` 从 `gradle.properties` 读取。
- Java 版本必须与 MC 版本一致（1.20.1 用 17，1.20.5+ 用 21）。
- `processResources` 展开 `fabric.mod.json` 里的 `${version}`。
- Datagen 用 fabric-api 的 datagen 依赖和 `fabricApi { configureDataGeneration() }`。

## 代码结构
- 入口实现 `ModInitializer`，在 `onInitialize()` 注册物品、方块、事件、网络包。
- 客户端入口实现 `ClientModInitializer`，只放渲染、按键、GUI、粒子。
- `fabric.mod.json` 的 `entrypoints` 同时声明 `main` 与 `client`。

## 验证
- `./gradlew build`，再 `./gradlew runClient` / `runServer`。
- Fabric 没有本工坊的 Forge 探针：用开发端日志、GameTest（`./gradlew runGametest`）和单元测试验证。
- 不说“应该能跑”；以编译结果和真实日志为准。
""",
    },
    {
        "id": "forge-scaffold",
        "name": "Forge 工程搭建",
        "description": "Forge 模组：ForgeGradle、mods.toml、DeferredRegister、事件总线，以及 1.20.1 探针验证。",
        "body": """# Forge 工程搭建

## 1.20.1 基线
- Minecraft 1.20.1，Forge 47.4.28（`47.x`），Java 17。
- ForgeGradle `[6.0,6.2)`，Gradle 8.8，Mojang 官方映射（official）。
- 新建工程用 `scaffold_project`（loader=Forge），不要手工从零写构建脚本。

## 关键文件
```
build.gradle / settings.gradle / gradle.properties
gradlew, gradlew.bat, gradle/wrapper/*
src/main/java/<package>/ModMain.java
src/main/resources/META-INF/mods.toml
src/main/resources/pack.mcmeta
```

## build.gradle 要点
- `minecraft { mappings channel: 'official', version: '1.20.1' }`。
- `java.toolchain.languageVersion = JavaLanguageVersion.of(17)`。
- 依赖：`minecraft "net.minecraftforge:forge:1.20.1-47.4.28"`。
- `processResources` 用 `expand` 展开 `mods.toml` / `pack.mcmeta` 里的 `${...}`。
- runs 保留 `client`、`server`、`gameTestServer`、`data`，工作目录分别是 `run-client`、`run-server`、`run-gametest`、`run-data`。

## 代码结构
- 入口 `@Mod(MODID)` 构造器里创建 `DeferredRegister` 并挂到 `modEventBus`。
- 注册用 `DeferredRegister.Items` / `DeferredRegister.Blocks` / `DeferredRegister<EntityType<?>>`。
- 生命周期用 `FMLCommonSetupEvent`、`FMLClientSetupEvent`；游戏事件用 `MinecraftForge.EVENT_BUS`。
- 客户端专用类放 `client/` 包，并加 `@Mod.EventBusSubscriber(value = Dist.CLIENT, bus = Mod.EventBusSubscriber.Bus.MOD)` 或 `DistExecutor` 兜底。

## 资源（1.20.1 用复数目录）
- `assets/<mod_id>/models/item/<id>.json`
- `assets/<mod_id>/textures/item/<id>.png`
- `assets/<mod_id>/lang/en_us.json`、`zh_cn.json`
- `data/<mod_id>/recipes/`、`loot_tables/`、`tags/`（1.21 才改成单数）

## 验证
- `mcdev_build` 编译，`mcdev_prepare` 启动开发端，再用探针状态、命令、UI 和场景断言验证。
- Forge 1.20.1 是唯一支持游戏内探针的基线；其他 Forge 版本用日志和 GameTest。
""",
    },
    {
        "id": "neoforge-scaffold",
        "name": "NeoForge 工程搭建",
        "description": "从零创建 NeoForge 模组：NeoGradle / ModDevGradle、mods.toml、DeferredRegister 与事件总线。",
        "body": """# NeoForge 工程搭建

## 先确认
MC 版本、Mod ID、显示名、依赖、是否允许 Mixin。先确认目标 MC 版本有对应 NeoForge。

## 版本要点
- 1.20.1：用 ModDevGradle 的 `net.neoforged.moddev.legacyforge`（2.0.91），
  构件坐标是 `net.neoforged:forge:1.20.1-47.1.106`，**不是** `net.neoforged:neoforge`；
  代码仍用 `net.minecraftforge.*` 包，`mods.toml` 的依赖 modId 也写 `forge`。
  NeoGradle 7（`net.neoforged.gradle.userdev`）从 1.20.2 起才适用，1.20.1 用了会解析失败。
- 1.20.2+：ModDevGradle `net.neoforged.moddev` + `net.neoforged:neoforge:<版本>`。
- 1.20.5+：元数据改为 `META-INF/neoforge.mods.toml`。
- 构建文件一律用 `scaffold_project` 生成，不要凭记忆手写插件版本和坐标。
- 不写 SRG 名（`m_12345_`）或混淆名。

## 最小目录
```
build.gradle
gradle.properties
settings.gradle
src/main/java/<package>/<ModId>Mod.java
src/main/resources/META-INF/mods.toml
src/main/resources/assets/<modid>/lang/en_us.json
src/main/resources/assets/<modid>/lang/zh_cn.json
```

## 注册与事件
- 用 `DeferredRegister` / `Registries` 注册，挂到构造器注入的 `IEventBus`。
- 游戏逻辑事件走 `NeoForge.EVENT_BUS`；模组生命周期走 mod event bus。
- 方块实体、菜单、网络包用对应的 DeferredRegister 或 `RegisterPayloadHandlersEvent`。
- 客户端内容放 `@Mod(value = MODID, dist = Dist.CLIENT)` 或客户端事件，不要混进服务端。

## 验证
- `./gradlew build`、`./gradlew runClient`、`./gradlew runServer`。
- GameTest 任务用 `runGameTestServer`；本工坊没有 NeoForge 游戏内探针，验证以日志和 GameTest 为准。
- 网络包、方块实体、库存同步必须标【⚠️人工复核】。
""",
    },
    {
        "id": "architectury-scaffold",
        "name": "Architectury 多加载器工程",
        "description": "用 Architectury 组织 common + Fabric + NeoForge 多加载器工程，处理平台差异与构建脚本。",
        "body": """# Architectury 多加载器工程

## 结构
```
settings.gradle
build.gradle
gradle.properties
common/        # 通用代码
fabric/        # Fabric 入口与平台实现
neoforge/      # NeoForge 入口与平台实现
```

## 构建
- 使用 architectury-loom / architectury plugin，版本与目标 MC 对齐。
- `common` 只依赖 Architectury API 和原版，不直接依赖 Fabric API 或 NeoForge API。
- `fabric` / `neoforge` 各自声明对应加载器依赖和入口。
- 平台差异用 `@ExpectPlatform` 或接口 + 平台实现桥接。

## 代码边界
- common 放物品/方块注册抽象、事件抽象、网络包抽象。
- 平台子项目放加载器入口、客户端初始化、平台专属 API 调用。
- 不要在 common 里 import `net.fabricmc.*` 或 `net.neoforged.*`。

## 验证
- 分别构建 `:fabric:build` 与 `:neoforge:build`，再启动各自的开发端。
- 没有内置 Architectury 骨架时，按官方模板生成，并先核对插件与 MC 版本。
- 两个平台都要能编译；只通过一个平台不算完成。
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
        "name": "从现有 JAR 参考实现",
        "description": "把源 JAR 反编译成可读源码，按功能拆解后，用当前工程加载器的官方写法重写，不直接搬运混淆代码。",
        "body": """# 从现有 JAR 参考实现

## 目标
目标加载器、MC 版本、映射以当前工程为准：Fabric 用 Yarn，Forge / NeoForge 用 Mojang 官方映射。参考 JAR 只用来确认行为、注册项和资源结构，最终代码必须能按当前工程编译。

## 流程
1. `decompile_jar` 反编译源 JAR，读索引和目标类；不要凭记忆猜实现。
2. 列出源 JAR 的注册项（物品/方块/实体/命令）、事件钩子、网络包、资源目录。
3. 在现有工程里按“注册 → 交互 → 网络 → 客户端 → 数据/资源”顺序重写，每批都编译。
4. 用 `build_project` 编译；Forge 1.20.1 再 `mcdev_prepare` 启动真实开发端并用探针验证，其他加载器用运行日志和 GameTest 验证。
5. 涉及贴图、模型、语言文件时，逐项核对 `assets/<mod_id>/` 下的路径与注册名。

## 映射要点（按目标加载器选择）
- 标识符：Fabric Yarn `Identifier`；Forge / NeoForge `ResourceLocation`（1.20.1 用 `new ResourceLocation(namespace, path)`）。
- 注册：Fabric `Registry.register` + `Registries.*`；Forge / NeoForge `DeferredRegister` 挂到对应 event bus；Architectury 把公共注册放 common，平台差异用 `@ExpectPlatform`。
- 事件：Fabric 回调 / `ServerLifecycleEvents`；Forge `MinecraftForge.EVENT_BUS` + mod event bus；NeoForge `NeoForge.EVENT_BUS` + `IEventBus`。
- 文本：`Text` / `Component` 统一成目标版本的组件类型；1.20.1 是 `Component`。
- 物品属性：Fabric `Item.Settings` → 目标加载器的 `Item.Properties`（1.20.1）。
- 客户端：`net.minecraft.client` 相关代码只能放客户端入口或平台客户端子工程，专用服务端不能加载。

## 风险
- 反编译代码只作参考，不要整段复制；许可证不兼容的代码不能用。
- 编译通过不等于运行正常；能启动开发端就启动，不能就明确写“仅编译通过”。
- 世界生成、方块实体、网络同步、Mixin 改动标【⚠️人工复核】。
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
        "id": "asset-3d-modeling",
        "name": "3D 模型与资产建模",
        "description": "方块/物品/实体 3D 模型、Blockbench 流程、UV 与纹理、模型 JSON、GeckoLib 动画和游戏内预览。",
        "body": """# 3D 模型与资产建模

## 适用
方块、物品、手持/穿戴、实体、方块实体和装饰件的模型与纹理；Blockbench 建模流程；GeckoLib 动画；3D 概念图与三视图。

## 先确认
模型类型（方块 / 物品 / 实体 / 方块实体 / 盔甲）、目标 MC 版本、加载器、尺寸基准、纹理分辨率、是否需要动画、是否使用 GeckoLib、是否已有贴图。缺关键项先问，不要猜。

## 流程
1. **参考**：正视、侧视、顶视、三视角，标出尺寸、比例和材质。
2. **白模**：只做体块，先定轮廓和比例，不急着画细节。
3. **低模**：Minecraft 用元素盒（elements）拼，不用高模；面数越少越稳。
4. **UV**：每个面用 0–16 的像素坐标，纹理分区不重叠、不越界。
5. **纹理**：16 / 32 / 64 像素，透明背景，像素对齐；需要像素图时用 `pixel_art` 工具。
6. **导出**：Java 模型 JSON；GeckoLib 用 `.geo.json` + `animation.json`。
7. **入游戏**：blockstates / models/item 引用、渲染器注册、语言键。
8. **验证**：物品栏、地面、第三人称、方块六面、透明与剔除。

## Java 模型 JSON
- `textures`：`{"0":"modid:block/name","particle":"modid:block/name"}`。
- `elements`：`from` / `to` 为 [x,y,z]，范围 0–16；可带 `rotation {origin, axis, angle, rescale}`。
- `faces`：`uv` 为 [u1,v1,u2,v2]，`texture` 用 `#0`，按需写 `cullface`、`rotation`、`tintindex`。
- `display`：`gui`、`ground`、`fixed`、`thirdperson_righthand` 等变换；物品模型常用 `item/generated` + `layer0/1`。
- 方块状态 JSON 用 `variants` 或 `multipart`，属性名必须和 BlockState 完全一致。
- 方块模型默认 16×16×16；超出部分要确认游戏是否允许，不要靠缩放硬塞。

## 实体模型
- 原版：`EntityModel<T>` + `ModelPart`，按状态调 `setRotation` / `visible`；渲染器通过 `EntityRenderers`（Fabric）或 `RegisterRenderers`（NeoForge）注册。
- GeckoLib：`GeoModel` / `GeoEntityRenderer` / `GeoAnimatable`，动画控制器只放客户端。
- 模型 pivot、碰撞箱、眼睛高度、骑乘位置要一起核对，不要只对齐视觉。

## 质量检查
- 无 z-fighting（面不共面）、无反面剔除错误、纹理不拉伸、UV 不越界。
- 方块 `cullface` 正确，避免相邻方块露缝；透明纹理按版本选 cutout / translucent。
- 物品 `display` 变换不要让模型出框；手持角度在游戏里实际看一遍。
- 动画骨骼命名稳定，不要用中文或空格。

## 3D 概念图提示词（给图像工具用）
> 四格视图：正面 / 侧面 / 顶面 / 三视角；纯色背景；无阴影；标注主色与材质；等距视角；方块化、像素化；16:9。

工坊不能直接生成 `.obj` / `.fbx`；Minecraft 用 JSON 模型。需要外部 3D 渲染图时，把上面的提示词交给图像工具，不要假装已经生成模型文件。

## 交付
模型 JSON 路径 + 完整内容；纹理规格或 `pixel_art` 参数；blockstates / 模型引用；渲染器与注册代码；游戏内验证步骤。
""",
    },
    {
        "id": "visual-design",
        "name": "绘图、纹理与界面视觉",
        "description": "像素纹理、物品/方块图标、模组图标、GUI、HUD、SVG 图表与宣传图；可直接生成 PNG 像素图。",
        "body": """# 绘图、纹理与界面视觉

## 能做什么
像素纹理、物品/方块图标、模组图标、GUI、HUD、SVG 架构图/流程图、宣传图提示词。二进制纹理用 `pixel_art` 直接生成 PNG；矢量图用 `write_file` 写 SVG 或 draw.io XML。

## pixel_art 用法
```tool
{"tool":"pixel_art","path":"src/main/resources/assets/modid/textures/item/gem.png","palette":{".":"transparent","o":"#1b1b1f","a":"#7fd6ff","h":"#d8f6ff"},"rows":["................","................","......oooo......",".....oaaaao.....","....oaaaaaao....","...oaahhaaaao...","...oaahhaaaao...","...oaaaaaaaao...","...oaaaaaaaao...","....oaaaaaao....",".....oaaaao.....","......oooo......","................","................","................","................"]}
```
- `rows` 每行长度必须一致；调色板建议 8–20 色；输出 RGBA PNG。
- 16×16 用于物品/方块；32×32 用于精细图标；GUI 建议 256×256。
- 动画纹理竖向排列帧，旁边写 `.png.mcmeta`：`{"animation":{"frametime":2}}`。

## 像素画规则
- 先用 3–5 个主色定调，再加 1–2 个高光/阴影；不要用纯黑描边，用深色同色相。
- 光源左上；高光 1px、阴影 1px；外轮廓闭合。
- 透明区域用 `.`；不要抗锯齿，保持硬边。
- 物品轮廓要在地面和手中看得清：主体至少占 12×12。
- 方块纹理要能四方连续：边缘颜色一致，避免明显接缝。

## 模型与纹理对应
- 每个注册名都要有模型和纹理；路径 `assets/<modid>/textures/...`。
- 物品模型 `layer0` 指向物品纹理；方块模型 `texture` 键与 blockstates 引用一致。
- GUI：九宫格背景、按钮三态（normal / hover / disabled）、槽位 18×18、文字对比度足够。

## 图表
- 架构图/流程图/类图优先 SVG：`viewBox="0 0 1200 800"`，系统无衬线字体，颜色 5 种以内。
- draw.io XML 用 `mxGraphModel`，节点 id 唯一，连线带箭头。
- 图里不要塞大段代码；节点用类名/模块名，边标调用或依赖关系。

## 封面与宣传图提示词
主体 + 风格 + 构图 + 光照 + 色调 + 分辨率；Minecraft 风格要写明方块化、像素化、等距视角。不要生成受版权保护的角色或直接复制他人美术。

## 检查
像素对齐、透明通道、路径与引用一致、GUI 状态齐全、文字可读、没有模糊缩放。
""",
    },
    {
        "id": "code-engineering",
        "name": "代码工程与重构",
        "description": "架构分层、命名、可读性、性能、并发、测试、重构与代码审查；写代码前先读，改完必须编译。",
        "body": """# 代码工程与重构

## 原则
先读后改；最小变更；不为未来造抽象；三行重复优于过早抽象；命名表达意图；错误不要吞。

## 分层
注册/内容（registry、content）→ 逻辑（logic、blockentity、entity）→ 数据（data、recipe、loot）→ 网络（network）→ 客户端（client、render、screen）→ 配置（config）。
依赖方向：client → common；logic 不 import client；network 只传 DTO。

## Java 规范
- 类小、方法短；一个方法只做一件事。
- 字段尽量 `final`；可变状态收敛到一处；避免 public 字段。
- 用 `record` 表达不可变数据；枚举表达状态；`Optional` 只用于返回值。
- 空值边界在入口校验，内部不要反复判空。
- 日志带上下文（modid、坐标、注册名），不要只打印 `e.getMessage()`。

## 性能
- 每 tick 路径避免 new 对象、stream、正则和字符串拼接。
- 缓存注册表查询；方块实体只在必要时 `markDirty` / `setChanged`。
- 网络包做限频与增量同步，不要每 tick 全量发。
- 先测量再优化，不做猜测性优化。

## 并发
- 服务端逻辑只在 server thread；客户端渲染只在 client thread；网络回调切回对应线程。
- 共享集合用并发容器或快照；不要在遍历时修改。

## 重构流程
1. 读目标文件和调用方；2. 列行为不变清单；3. 小步改；4. 编译；5. 跑测试或进游戏验证；6. 再清理。
- 不顺手改无关格式；不重命名公共 API；不删除兼容层，除非用户要求。

## 审查清单
注册时机、客户端/服务端分侧、空值、异常、资源关闭、线程、性能热点、NBT/组件键、版本 API、Mixin 风险、测试覆盖。

## 交付
改动文件清单 + 关键 diff 或完整文件 + 编译结果 + 验证步骤 + 风险标注。编译成功不等于运行正常。
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
    {
        "id": "fabric-to-neoforge-port",
        "name": "Fabric 源实现 → Forge 1.20.1",
        "description": "读 Fabric 源码或反编译 JAR，按 1.20.1 Forge 的 Mojang 映射重写：注册、事件、网络、资源目录逐项对照。",
        "body": """# Fabric 源实现 → Forge 1.20.1

## 先做的事
1. 用 `decompile_jar` 或直接读源工程，确认注册项、事件钩子、网络包和客户端渲染类。
2. 在现有 Forge 1.20.1 工程里重写；不要生成 Fabric 工程，也不要整段复制反编译代码。
3. 按“注册 → 交互 → 网络 → 客户端 → 数据/资源”分批改，每批 `mcdev_build` 编译。

## 映射表（到 Forge 1.20.1 / Mojang 官方映射）
- 入口：`ModInitializer` → `@Mod` 构造器；`ClientModInitializer` → `Dist.CLIENT` 专用类 + `FMLClientSetupEvent`。
- 注册：`Registry.register(Registries.ITEM, ...)` → `DeferredRegister.create(ForgeRegistries.ITEMS, MODID)`。
- 标识符：`Identifier` → `new ResourceLocation(namespace, path)`。
- 世界/玩家：`World` → `Level`，`PlayerEntity` → `Player`，`ServerPlayerEntity` → `ServerPlayer`。
- 物品：`Item.Settings` → `new Item.Properties()`；`use(World, PlayerEntity, Hand)` → `use(Level, Player, InteractionHand)`。
- 交互：`useOnBlock` → `useOn(UseOnContext)`。
- 文本：`Text` → `Component`。
- 网络：Fabric `PayloadTypeRegistry` / `ServerPlayNetworking` → Forge 1.20.1 的 `SimpleChannel` + `NetworkRegistry.newSimpleChannel`。
- 事件：Fabric `ServerTickEvents` → `MinecraftForge.EVENT_BUS.addListener` 或 `@Mod.EventBusSubscriber`。
- 配置目录：`FabricLoader.getInstance().getConfigDir()` → `FMLPaths.CONFIGDIR.get()`。

## 资源（1.20.1）
- 物品模型：`assets/<ns>/models/item/<id>.json`，`layer0` 指向真实 PNG。
- 纹理：`assets/<ns>/textures/item/<id>.png`。
- 数据包目录用复数：`data/<ns>/recipes/`、`loot_tables/`、`tags/`（1.21 才改单数）。
- 配方格式是 1.20.1 的 `{"item": "...", "count": n}`，不是 1.20.5+ 的 `{"id": ...}`。
- 中文 lang 如果是 GBK，先转 UTF-8。

## 验收
- 源 JAR 的每个注册项在 Java 里都有对应注册，且资源路径一致。
- 客户端类不在公共包，专用服务端不引用 `net.minecraft.client`。
- 编译通过后用 `mcdev_prepare` 启动开发端，探针确认物品能拿到、界面能打开、交互生效。
""",
    },
    {
        "id": "mca-reborn-compat",
        "name": "MCA Reborn 兼容",
        "description": "让作用于玩家 / 村民的道具在 MCA 村民上同样生效，并让玩家接管村民身份后继承姓名、关系与外观，可以代替村民与他人结婚生育。",
        "body": """# MCA Reborn 兼容（Forge 1.20.1 参考）

> 下面是 MCA 的通用机制。实际类名和方法签名以当前工程依赖的 MCA 1.20.1 源码为准，写之前先用 `decompile_jar` 或 `github_file` 核对；1.21.x 的 `player.level()` 这类写法在 1.20.1 要换成 `player.level`。

## 判定目标
- 村民实体实现 `VillagerLike<?>`；判断必须写 `entity instanceof VillagerLike<?> && !(entity instanceof Player)`。
- 不要用 `VillagerLike.toVillager(entity)` 做“是不是村民”的判断：MCA 对任何 `ServerPlayer` 都会临时返回一个村民视图，会把玩家误判成村民。

## 道具对村民生效
- 只重写 `use` / `useOn` 不够，必须重写：
  `public InteractionResult interactLivingEntity(ItemStack stack, Player player, LivingEntity target, InteractionHand hand)`
- 服务端执行真实逻辑，客户端返回 `InteractionResult.sidedSuccess(player.level().isClientSide)`。
- MCA 村民的 `mobInteract` 会先消费右键（打开对话/交易），所以物品自己的 `interactLivingEntity` 不会被调用。必须在 `PlayerInteractEvent.EntityInteract` 里抢先处理：只接管本模组道具，调用 `item.interactLivingEntity`，结果 `consumesAction()` 时 `setCancellationResult` + `setCanceled(true)`。`event/ServerEvents` 里已有这份兜底，不要删。
- 对玩家和对村民共用同一套身份同步函数，避免两套逻辑行为不一致。
- 在现有工程里建议拆成：
  - `compat/McaCompat.java`：`possess(serverPlayer, target)`、`wear(serverPlayer, skinStack)`、`release(serverPlayer)`、`captureVillager(target)`、`tick(player)`。
  - 对应道具的 `item/*Item`：在 `interactLivingEntity` 里调用同一套同步函数。
  - `data/SkinData.java`、`client/ClientSkinCache.java`、`client/ClientEvents.java`、`event/ServerEvents.java`、`mixin/client/AbstractClientPlayerMixin.java`。
  - 快照存在玩家 `getPersistentData()`，服务器重启后仍能还原；每 20 tick 检查承载身份的道具是否还在装备栏，不在就自动还原。

## 身份同步
1. `PlayerSaveData data = PlayerSaveData.get(serverPlayer);`
2. 备份旧数据：`CompoundTag previous = data.getEntityData();`（退出时恢复）
3. 把村民数据写进去：
   ```
   CompoundTag tag = new CompoundTag();
   villager.addAdditionalSaveData(tag);
   data.setEntityData(tag);
   data.setEntityDataSet(true);
   player.refreshDimensions();
   ```
4. 广播给同维度所有玩家，否则别人看到的皮肤还是旧的：
   `Network.sendToPlayer(new PlayerDataMessage(player.getUUID(), data.getEntityData()), viewer)`
   （`net.conczin.mca.network.s2c.PlayerDataMessage` / `net.conczin.mca.network.Network`）
5. 皮肤/衣服/头发都在村民 tracked data 里；`addAdditionalSaveData` 已经覆盖，不要只改一个字符串。

## 姓名 / 关系 / 家庭
- 玩家节点：`FamilyTreeNode to = PlayerSaveData.get(player).getFamilyEntry();`
- 村民节点：`FamilyTreeNode from = EntityRelationship.of(villager).orElseThrow().getFamilyEntry();`
- 复制：`to.setName(from.getName())`、`to.setGender(from.gender())`、`to.setProfession(from.getProfession())`、`to.setRelationshipState(from.getRelationshipState())`。
- 配偶：`to.updatePartner(partnerEntity, from.getRelationshipState())`；如果对方还在，`partnerNode.updatePartner(player, state)`。对方 UUID 等于被占用的村民时，只改姓名/性别/职业，不要让他指向自己。
- 子女：`to.children().clear(); to.children().addAll(from.children());`，并在每个子女节点里把 father/mother 从村民 UUID 改成玩家 UUID。
- 父母：把父母节点 `children()` 里的村民 UUID 换成玩家 UUID。
- 结婚/生育：不要自己造关系表。只要玩家节点保留原村民的配偶、子女、关系状态，MCA 的村民 AI 与 FamilyTree 就会继续把玩家当作原村民。
- 顺序很重要：先恢复村民本体和家庭树，再写玩家节点，最后广播；否则配偶指针会被后写的空数据覆盖。

## 退出 / 恢复
- 保存进入前的 `PlayerSaveData` 快照和原村民 FamilyTree 引用。
- 退出时恢复 `setEntityData(previous)`、`setEntityDataSet(previousSet)`、`refreshDimensions()`，并把 FamilyTree 引用反向改回原村民 UUID。
- 恢复后再次广播 `PlayerDataMessage`。

## 风险
- MCA 是 GPL-3.0，只做兼容调用，不要复制它的源码进你的模组。
- 不要直接 `new VillagerEntityMCA`：会绕过注册和维度初始化。需要村民实体时从世界或 `VillagerLike` 视图里取。
- 关系改动只放服务端；客户端只接收 `PlayerDataMessage`。
""",
    },
    {
        "id": "reference-research",
        "name": "真实代码检索",
        "description": "写代码前先查真实实现：GitHub 仓库、grep.app、Modrinth，处理限流和网络失败，避免凭记忆猜 API。",
        "body": """# 真实代码检索

## 顺序
1. 目标加载器官方仓库（Forge 1.20.1 / MinecraftForge）。
2. 目标模组本体仓库（例如 MCA：Luke100000/minecraft-comes-alive）。
3. 同类模组的同类功能实现。
4. grep.app 全局搜方法名/事件名。
5. Modrinth 查版本、依赖、下载页。

## 工具
- `search_code`：自动在 GitHub / grep.app / Modrinth 之间回退。
- `github_repo`：确认默认分支、最近更新时间。
- `github_tree`：定位文件路径，不要猜路径。
- `github_file`：读全文；大文件一次读不完时按行截取。
- `fetch_url`：读取 raw 文件、API JSON、Maven metadata。

## 失败处理
- GitHub 403：匿名 API 每小时 60 次，换 `github_file` 走 jsDelivr CDN，或等一段时间。
- grep.app 429：改用 GitHub 仓库搜索。
- raw.githubusercontent / jsDelivr 连接超时：直接改用 `github_file`（走 api.github.com）。`fetch_url` 已经把 jsDelivr 和 raw 链接自动改写到 GitHub API，不要反复重试同一个地址。
- Maven 仓库超时：换用列表里的镜像域名；连续两次超时就停下来告诉用户网络有问题。
- 网络完全不可用：明确告诉用户查不到，基于本地知识库/反编译源码实现，不要编造类名。

## 使用原则
- 查到源码后只提取 API 形状（类名、方法签名、事件名、字段），不要大段复制他人代码。
- 以目标 MC 版本的 tag/branch 为准；1.20.1 的代码不能拿 1.21.x 的凑。
- 引用来源写进答复，方便用户核对。
""",
    },
]


# 用户这句话命中哪些词时，优先把对应技能正文放进系统提示。
# 技能目录始终完整，这里只决定“先加载哪几份正文”。
SKILL_HINTS: dict[str, tuple[str, ...]] = {
    "fabric-scaffold": ("fabric", "fabric-loom", "yarn"),
    "forge-scaffold": ("forge", "forgegradle", "minecraftforge"),
    "neoforge-scaffold": ("neoforge", "neo forge"),
    "architectury-scaffold": ("architectury", "多加载器", "双加载器"),
    "incremental-edit": ("修改", "新增功能", "加功能", "改功能", "现有工程"),
    "crash-triage": ("崩溃", "报错", "编译失败", "异常", "crash"),
    "porting": ("移植", "升级版本", "降级", "换加载器"),
    "datagen-assets": ("datagen", "资源", "配方", "战利品", "语言文件", "音效"),
    "asset-3d-modeling": (
        "3d", "模型", "建模", "blockbench", "方块模型", "物品模型", "实体模型",
        "骨骼", "uv", "白模", "三视图", "动画", "geckolib", "geo",
    ),
    "visual-design": (
        "绘图", "画图", "纹理", "贴图", "图标", "界面", "gui", "ui", "像素",
        "png", "svg", "流程图", "架构图", "海报", "宣传图", "配色", "美术",
        "logo", "封面",
    ),
    "code-engineering": (
        "代码", "编程", "重构", "架构", "审查", "review", "性能", "优化",
        "测试", "bug", "并发", "线程", "设计模式", "可读性", "维护",
    ),
    "mixin-safety": ("mixin", "注入"),
    "client-server-split": ("客户端", "服务端", "渲染", "专用服务端"),
    "delivery-checklist": ("交付", "检查", "验收"),
    "modrinth-lookup": ("依赖", "modrinth", "版本查询", "下载地址"),
    "fabric-to-neoforge-port": (
        "移植", "迁移", "fabric转", "转neoforge", "换加载器", "1.21.1",
        "neoforge",
    ),
    "mca-reborn-compat": (
        "mca", "凡家", "村民", "身份同步", "外观同步", "接管身份", "结婚",
        "生小孩", "生育", "关系", "comes alive",
    ),
    "reference-research": (
        "参考", "搜索", "源码", "github", "grep", "别人代码", "查一下",
        "api", "文档",
    ),
}


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

    def list(self) -> builtins.list[Skill]:
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

    def select_for(
        self, query: str, base: builtins.list[str] | None = None, limit: int = 6
    ) -> builtins.list[str]:
        """按用户这句话挑选要加载正文的技能，基础技能优先，关键词命中的靠前。"""
        text = (query or "").lower()
        enabled = {item.id: item for item in self.list() if item.enabled}
        selected: list[str] = []
        for skill_id in base or []:
            if skill_id in enabled and skill_id not in selected:
                selected.append(skill_id)
        scored: list[tuple[int, str]] = []
        for skill_id, skill in enabled.items():
            if skill_id in selected:
                continue
            score = 0
            for keyword in SKILL_HINTS.get(skill_id, ()):
                if keyword.lower() in text:
                    score += 3
            blob = f"{skill.name} {skill.description}".lower()
            for token in set(re.findall(r"[a-z0-9_+-]{3,}", text)):
                if token in blob:
                    score += 1
            if score:
                scored.append((score, skill_id))
        scored.sort(key=lambda row: (-row[0], row[1]))
        for _, skill_id in scored:
            if len(selected) >= limit:
                break
            selected.append(skill_id)
        return selected[:limit]

    def active_bodies(self, limit: int = 6) -> str:
        items = [item for item in self.list() if item.enabled][:limit]
        if not items:
            return ""
        chunks = ["以下是当前启用的技能正文，按需参考："]
        for item in items:
            chunks.append(f"\n## 技能：{item.name}（{item.id}）\n{item.body.strip()}")
        return "\n".join(chunks)
