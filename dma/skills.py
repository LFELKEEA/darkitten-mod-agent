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

## Blockbench → JSON 导出规范
- 方块导出勾选「Texture Scale 16」；不勾会把 UV 写成 0–1 浮点，游戏读错。
- 元素盒的 `from`/`to` 保持整数或 .5/.25 小数，避免 15.9 这种导致缝隙。
- 旋转用 pivot 定中心：在 Blockbench 里把 pivot 移到旋转轴再设 rotation，导出才是 `origin` + `axis` + `angle`。
- 导出后检查：`textures` 键引用 `#0`；`particle` 键存在（掉落物粒子）；`cullface` 只在需要的面写；`display` 各姿势不空。
- GeckoLib 实体导出用「Export → Blockbench Project / Geo」专用格式，不要用方块导出器导实体。

## 旋转与 UV 坑（重点）
- **angle 符号**：绕 Y 轴 `-45` 和 `45` 方向相反；Blockbench 预览与游戏渲染可能镜像，导出后以游戏实际为准，不要照搬预览。
- **rescale**：`rescale: true` 让旋转后贴图随盒缩放；`false` 时贴图保持原像素尺寸，旋转的盒会被裁边。斜向元素（45° 旋转的杆/棱）务必开 `rescale`。
- **UV 顺序**：每个面 `uv: [u1, v1, u2, v2]` 是左下角→右上角；写反（`[u2,v2,u1,v1]`）贴图镜像。
- **UV 越界**：UV 坐标超过纹理尺寸（如 16×16 纹理写 `[0,0,20,20]`）会拉伸或报错；导出前在纹理面板确认每个面都在画布内。
- **共面 z-fighting**：两个盒贴在同一平面会闪烁；错开 0.01 或合并成一个盒。
- **cullface 漏写/多写**：透明方块漏 `cullface` 会看到内面；实心方块多写会让相邻面被剔除露缝。
- **方块纹理连续**：需要四方连续的方块（草、矿石、地板），上下左右纹理边缘颜色一致，否则方块并排有明显接缝。

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

## 16×16 纹理绘制技巧
- **先画形状后上色**：先用 1–2 个色块定主体轮廓（占 10×10 以上），再分层上色、描边、高光。
- **外轮廓**：用比主体深一档的同色相描 1px 边，不要用纯黑 `#000`（会脏、不自然）。
- **光源左上**：高光放左上/上侧 1px，阴影放右下/下侧 1px；高光用比基色亮 2–3 档的颜色。
- **像素对齐**：斜线用 1:1 / 2:1 / 2:2 的阶梯，不要用抗锯齿半透明过渡；保持硬边。
- **对称**：左右对称的物品先画一半再镜像，保证轮廓工整。
- **主体够大**：物品主体至少 12×12，否则在地面/手中看不清。

## 调色板
- 先定 3–5 个主色（基色 + 亮部 + 暗部 + 描边 + 点缀色），再各加 1 个高光/阴影档；不要超过 20 色。
- 明暗用**同色相、不同明度**（HSL 调 L），不要只调 RGB 三通道；饱和度随明度一起微调更自然。
- 金属/宝石：亮部更亮、暗部更深、加 1 个纯白高光点；木头/布料：明暗对比低，柔和过渡。
- 冷色做阴影、暖色做高光更通透（如蓝色物体阴影偏紫、高光偏青白）。
- 透明区域用 `.`，不要在透明区放半透明色（除非版本支持 translucent）。

## 体积感
- **两明一暗**：主体最少「基色 + 1 亮 + 1 暗」三层，才有立体感；只有基色就是平片。
- **渐变阶梯**：从亮到暗 3–4 阶，每阶 1px，做斜面/球面体积。
- **轮廓转折**：在边缘内侧加一圈暗色，中心加亮色，做「圆润」感（如宝石、药水）。
- **投影**：地面物品可在地面纹理上画 1px 暗影，增加落地感。
- 参考：先看原版同类物品（如钻石、金锭）的明暗分布，再套用自己的配色。

## 动画帧
- 动画纹理竖向排列帧：`png.mcmeta` 写 `{"animation":{"frametime":2}}`；`frametime` 是每帧 tick（20 tick=1 秒）。
- 每帧尺寸一致，帧之间只改「动的部分」（火焰、闪光、呼吸），静态部分复用。
- 火焰/水：3–8 帧循环；旋转动画用 `{"animation":{"interpolate":true}}`（平滑插值）或逐帧画旋转。
- 帧顺序从上到下；`frames` 数组可指定播放顺序和每帧时长（`{"frames":[{"index":0,"time":4},1,2]}`）。
- 画帧时先画第一帧，复制后微调像素，不要每帧重画。

## 像素画规则（总则）
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
        "id": "large-mod-playbook",
        "name": "大模组工程方法学",
        "description": "大模组工程方法学：目录组织、注册中心、渐进构建与里程碑计划，面向 100+ 文件、跨维度/世界生成/实体/进度的大型模组。",
        "body": """# 大模组工程方法学

## 适用
暮色森林级别：100+ 文件，跨维度、世界生成、实体、进度、配方联动、多方块结构、Boss 战、科技树。不是“加几个物品”，而是系统工程。

## 里程碑（先骨架后血肉）
1. **骨架跑通**：工程能编译、能启动、入口/注册中心/包结构成型，哪怕只有 1 个占位物品。此阶段只验证“能跑”。
2. **注册管线**：所有 DeferredRegister / 注册中心就位，物品/方块/实体/方块实体/附魔/药水/标签页骨架齐全，空实现也能编译。
3. **内容填充**：按“一域一域”推进——先物品→再方块→再实体→再世界生成→再进度→再配方联动。每域独立编译、独立进游戏验证。
4. **配方联动**：最后把内容串起来——合成树、掉落、进度触发、JEI、战利品、成就引导。没有内容前不要提前写联动。

## 目录组织（分域，不按类型平铺）
```
<package>/
  core/        # ModMain、常量、Config、注册中心聚合
  registry/    # 每个注册表的 DeferredRegister 与注册入口
  item/        # 物品类 + 子域（armor、tool、food、scroll）
  block/       # 方块类、方块实体、行为
  entity/      # 实体类、AI、渲染器、spawn egg
  worldgen/    # feature、placement、dimension、biome、structure
  recipe/      # 自定义配方类型、序列化器
  loot/        # 战利品函数/条件扩展
  advancement/ # 进度触发器与条件
  network/     # 网络包
  client/      # 渲染、GUI、HUD、粒子（永远只在客户端加载）
  compat/      # 与其他模组/API 的兼容层
```
- 包按“域”分，不按“Item/Block/Entity 全挤一个包”。一个域一个包，域内再分子包。
- 注册名统一命名 `modid:domain_name`，资源路径与注册名一一对应。

## 注册中心模式
- 每个域一个 `ModItems` / `ModBlocks` / `ModEntities` 等注册类，用 `DeferredRegister`（Forge/NeoForge）或 `Registry.register`（Fabric）集中注册，构造器只调用这些注册类。
- 常量化所有注册名/资源位置（`ResourceLocation`），禁止到处手写字符串字面量。
- 颜色/特性/配方/掉落/进度全部绑定到注册名，不靠硬编码下标索引。

## 跨域依赖
- 明确依赖方向：item/block → core/registry → 无；entity → item(掉落)/block；recipe → item/block；advancement → 一切；client → 一切但只读。
- 不要让 item 直接 import entity 或 client；需要联动时通过注册名/事件，不通过类引用。

## 渐进验证
- 每完成一个域就 `./gradlew build`（或 `mcdev_build`），能启动就启动看一遍。
- 用一个“内容清单”（registry id → 文件路径 → 状态）跟踪 100+ 文件的完成度，不要靠记忆。
- 世界生成、实体 AI、网络、进度触发、跨维度传送这些高风险域，逐项标【⚠️人工复核】并给游戏内验证步骤。

## 红线
- 不一次性生成 50 个文件再编译；每 5–10 个文件编译一次。
- 先跑通骨架再堆内容；没有注册中心就写实体/世界生成必乱。
- 客户端类绝不进公共包（见 client-server-split）。
""",
    },
    {
        "id": "mechanism-playbook",
        "name": "复杂机制实现映射",
        "description": "玩家变身/夺舍/换皮肤/对话/物品框/社交等抽象机制的「真实现 vs 示意实现」判定与所需技术手段(mixin/相机/状态机/Screen)。",
        "body": """# 复杂机制实现映射

## 核心判定：什么才算「做完了」
一个游戏机制「做完」的定义 = 玩家在游戏里能观察到对应的、可逆的动态行为变化。
不是「代码里有个方法叫这个名」，更不是「spawn 个实体 / 改个名字 / 发条消息 / 存个 List」。

## 机制 → 真实现 对照表（写码前先对照，避免示意实现）
| 需求说法 | 假实现（禁止） | 真实现（必须） |
|---|---|---|
| 玩家变成 X（史莱姆/动物/形态） | spawn 个 X 在玩家旁边，玩家还是玩家 | 玩家本体的控制/渲染/碰撞转移或替换，且能切回来；状态可逆 |
| 夺舍 / 进入他人身体 | 改 customName + discard 目标实体 | 控制权 + 外观 + 数据切换到目标，原载体冻结暂存，可退出还原 |
| 换皮肤 / 换外观 | 改 customName，或存皮肤 id 但渲染恒用固定纹理 | 渲染层按数据切换纹理；皮肤数据真实可加载 |
| 对话 / 聊天框 | 右键发一条硬编码消息 | 打开对话 GUI（Screen），可选回应，状态可推进 |
| 物品框 / 背包交换 | setStack 覆盖背包 | ScreenHandler + Screen 打开容器，物品真正可放取且同步 |
| 社交关系 / 好感 | 存 List<关系> 但无人读取 | AI 读取关系改变行为（跟随/敌对/交易/对话），关系随交互变化 |
| 能力 / 状态 / 冷却 | 存个 boolean 字段 | tick 生效，有持续/冷却/恢复，真正影响游戏逻辑 |

## 可逆状态机（变身/夺舍/形态切换必须先画）
1. 列出全部状态（如：正常玩家 / 人格史莱姆 / 空壳 / 夺舍中）。
2. 每个状态转换的触发条件（喝药 / 按 R / 右键空壳）。
3. 每个状态下数据的归属：谁持有「人格」「外观」「物品栏」「社交关系」。
4. 每个转换必须同时处理：渲染、控制、碰撞、NBT 数据、网络同步、退出还原。
5. 退出必须能还原到进入前的状态（数据不丢、位置正确、外观正确）。

## 技术手段速查
- 控制权/相机切换：让玩家进入 spectator 并把相机绑定到目标实体（client.setCameraEntity），或让玩家骑乘目标实体并隐藏玩家本体；真正的输入转发要 mixin PlayerEntity/LocalPlayer。绝不能只 spawn 一个实体。
- 换皮肤：mixin 渲染器（如 PlayerEntityRenderer.getTexture）按数据返回纹理；皮肤来源（如 littleskin）要下载并缓存成可用纹理，否则用本地多套纹理兜底。不能改 customName 冒充换皮肤。
- 对话/聊天框：ScreenHandler + Screen；纯对话框可用 Screen 直接显示，带选择则用按钮。
- 物品框：ScreenHandler（容器）+ Screen，槽位 addSlot 与客户端渲染顺序一致。
- 社交 AI：关系数据存实体 NBT，AI Goal 的 canUse()/tick() 读它决定行为。
- 网络：状态切换用 payload 同步（客户端按键 → 服务端执行 → 同步结果）。

## 红线
1. 禁止用「spawn 实体 / 改 customName / 发消息 / 存 List / 存 boolean」当作「完成机制」。
2. 每个机制交付时，必须写明「玩家在游戏里怎么一步步验证它真的生效」。
3. 机制未经验证前，只能写「已实现，尚未验证」，禁止写「完成/能用/已生效」。
4. 涉及玩家本体（变身/夺舍/皮肤/相机）必须用 mixin 或官方 API 落地，并在 mixins.json 里注册对应 mixin 类；空 mixins.json 等于没实现。
""",
    },
    {
        "id": "mod-registry",
        "name": "注册中心模式",
        "description": "注册中心模式：DeferredRegister / RegisterEvent 的 Forge/Fabric 双写法，物品/方块/实体/附魔/药水/创造标签页全注册表清单。",
        "body": """# 注册中心模式

## 目标
所有内容（物品/方块/实体/方块实体/附魔/药水/创造标签页/菜单/声音/粒子/配方类型/战利品/维度/特征）通过统一注册中心注册，注册名与资源路径一一对应，禁止散落各处手写注册。

## Forge / NeoForge：DeferredRegister
```java
public static final DeferredRegister<Item> ITEMS = DeferredRegister.create(ForgeRegistries.ITEMS, MODID);
public static final RegistryObject<Item> EXAMPLE = ITEMS.register("example", () -> new Item(new Item.Properties()));
// @Mod 构造器里：ITEMS.register(modEventBus);
```
- 每个注册表一个 `DeferredRegister`，字段用 `public static final`，`RegistryObject<T>` 暴露引用。
- 按注册表类型取：`ForgeRegistries.BLOCKS / BLOCK_ENTITY_TYPES / ENTITY_TYPES / MOB_EFFECTS / POTIONS / CREATIVE_MODE_TABS / MENU_TYPES / SOUND_EVENTS / PARTICLE_TYPES / RECIPE_TYPES / LOOT_CONDITION_TYPES / LOOT_FUNCTION_TYPES / FEATURES / ...`。
- 在 `@Mod` 构造器里把每个 DeferredRegister 挂到 `modEventBus`；不要在主类里 new 内容对象。
- 需要额外处理（方块物品绑定、附魔合成、实体属性）用 `bus.addListener(...)` 或 `DeferredRegister` 的 `register` 回调，不要塞进构造器。

## Fabric：Registry.register
```java
public static final Item EXAMPLE = Registry.register(Registries.ITEM,
    new Identifier(MODID, "example"), new Item(new Item.Settings()));
```
- 在 `ModInitializer.onInitialize()` 里注册；用 `Registries.ITEM / BLOCK / ENTITY_TYPE / ENCHANTMENT / STATUS_EFFECT / ...`。
- 1.21+ 部分注册改用 `Registry.register(RegistryKey, ...)`，先核对当前 Yarn 映射。

## 全注册表清单
| 内容 | Forge 注册表 | Fabric 注册表 |
| --- | --- | --- |
| 物品 | `ForgeRegistries.ITEMS` | `Registries.ITEM` |
| 方块 | `ForgeRegistries.BLOCKS` | `Registries.BLOCK` |
| 方块实体 | `ForgeRegistries.BLOCK_ENTITY_TYPES` | `Registries.BLOCK_ENTITY_TYPE` |
| 实体 | `ForgeRegistries.ENTITY_TYPES` | `Registries.ENTITY_TYPE` |
| 附魔 | `ForgeRegistries.ENCHANTMENTS` | `Registries.ENCHANTMENT` |
| 药水效果 | `ForgeRegistries.MOB_EFFECTS` | `Registries.STATUS_EFFECT` |
| 药水 | `ForgeRegistries.POTIONS` | `Registries.POTION` |
| 创造标签页 | `ForgeRegistries.CREATIVE_MODE_TABS` | `Registries.ITEM_GROUP` |
| 菜单类型 | `ForgeRegistries.MENU_TYPES` | `Registries.SCREEN_HANDLER` |
| 声音事件 | `ForgeRegistries.SOUND_EVENTS` | `Registries.SOUND_EVENT` |
| 粒子类型 | `ForgeRegistries.PARTICLE_TYPES` | `Registries.PARTICLE_TYPE` |
| 配方类型 | `ForgeRegistries.RECIPE_TYPES` | `Registries.RECIPE_TYPE` |
| 战利品条件/函数 | `ForgeRegistries.LOOT_CONDITION_TYPES` / `LOOT_FUNCTION_TYPES` | `Registries.LOOT_CONDITION_TYPE` / `LOOT_FUNCTION_TYPE` |
| 维度/特征/生物群系 | 数据包 JSON + `Registries.*` | 数据包 JSON + `Registries.*` |

## 注册顺序与时机
- 内容对象创建要早、副作用（方块掉落、实体属性、附魔合成）要晚、游戏内事件最晚。
- 方块物品：方块注册后，物品注册时 `new BlockItem(block, props)` 引用方块对象。
- 附魔、药水等带参数的注册，先注册“类型”，再在 `FMLCommonSetupEvent` / `onInitialize` 后补参数。
- 不要用“注册表 id 数组 + for 循环下标”的脆弱方式映射内容；用常量引用。

## 资源绑定
- 每个注册名都有 `models/`、`textures/`、`lang/`、`blockstates/`（方块）、`spawn_eggs`/渲染器（实体）、`sounds.json`（声音）对应项，路径用同一 `modid:name`。
- 注册名只用小写 + 下划线，不用中文、空格、大写。

## 检查
- DeferredRegister 是否都挂到正确总线；`RegistryObject` 是否在注册前被取值（会 NPE/崩溃）。
- Fabric 是否都跑在 `onInitialize` 时机；专用服务端是否加载了客户端注册（如创造标签页图标渲染）。
""",
    },
    {
        "id": "mod-model-texture",
        "name": "物品方块实体模型规范",
        "description": "物品/方块/实体 JSON 模型规范：parents、display、elements、rotations、blockstates、cuboid UV，16/32 像素纹理与 Blockbench 导出。",
        "body": """# 物品 / 方块 / 实体模型与纹理规范

## 模型类型速查
- **物品模型**：`assets/<modid>/models/item/<id>.json`，扁平图标用 `parent: "item/generated"` + `layer0`；手持 3D 用 `parent` + `display` + `elements`。
- **方块模型**：`assets/<modid>/models/block/<id>.json`（16×16×16 元素盒）+ `assets/<modid>/blockstates/<id>.json`（variants / multipart）。
- **实体模型**：Java `EntityModel<T>` + `ModelPart`（原版）或 GeckoLib `.geo.json`。

## 物品模型 JSON 要点
```json
{
  "parent": "item/generated",
  "textures": { "layer0": "modid:item/example" }
}
```
- `parent` 可继承原版（`item/generated`、`item/handheld`、`block/cube_all` 等）或自己的父模型，省重复。
- `display` 键：`gui`、`ground`、`fixed`、`thirdperson_righthand`、`thirdperson_lefthand`、`firstperson_righthand`、`firstperson_lefthand`；`rotation`/`translation`/`scale` 控制手持姿势。
- 手持模型不要超出 16×16×16 太多，否则第三人称/第一人称穿模。

## 方块模型 JSON 要点
```json
{
  "parent": "block/cube_all",
  "textures": { "all": "modid:block/example" }
}
```
- `elements`：`from`/`to` 为 `[x,y,z]`（0–16），`faces` 每面 `uv: [u1,v1,u2,v2]` + `texture: "#0"`，按需 `cullface`、`rotation`、`tintindex`。
- `rotation`：`{origin: [x,y,z], axis: "x"|"y"|"z", angle: ±22.5|±45, rescale: bool}`；`rescale` 让旋转后贴图随盒缩放而不是被裁。
- 透明/半透明方块：`parent` 选 `cutout` 或 `translucent`（按版本），否则透明区域显示黑块。
- 光照/粒子：`textures` 里加 `"particle"` 键指定粒子贴图，避免掉落物粒子全黑。

## blockstates：variants 与 multipart
- **variants**：一个属性组合一个模型，如 `{"variants": {"facing=north": {"model": "modid:block/x"}, "facing=south": {"model": "modid:block/x", "y": 180}}}`；属性名必须与 BlockState 属性完全一致。
- **multipart**：按条件叠加多个部分（栅栏、红石、墙），`when` 条件 + `apply` 模型；适合有多个独立可开关部分的方块。
- 旋转用 `y`（绕 Y 轴 0/90/180/270）和 `x`；`uvlock: true` 让顶底面纹理随旋转锁定，避免朝向纹理乱转。

## 实体模型
- 原版：`ModelPart` 树 + `addBox(...)` 建盒，`setPos` 定枢轴，动画改 `setRotation`/`visible`；渲染器经 `RegisterRenderers`（Forge）/`EntityRenderers`（Fabric）注册，`ModelLayer` 定义层。
- GeckoLib：`.geo.json`（骨骼）、`animation.json`（动画）、`GeoModel`/`GeoEntityRenderer`；动画控制器只放客户端。

## 纹理规范
- **16×16**：物品、方块、简单实体，一像素一格；**32×32**：精细图标、GUI 元素、较大实体。
- 必须带透明通道（RGBA PNG）；物品主体占满 12×12 以上，地面/手中看得清。
- 方块纹理要四方连续，边缘颜色一致避免接缝；上下左右纹理编号对齐模型 `faces` 的 UV。
- 实体纹理按 UV 布局展开，区域不重叠、不越界、不拉伸；用 Blockbench 画布看 UV 对应关系。

## Blockbench 工作流
1. 新建 Java Block/Item 或 Modded Entity；方块单位设为 16，画布 UV 用 0–16 像素坐标（不是 0–1）。
2. 建元素盒拼轮廓（白模），再用 `pivot` 定旋转中心。
3. 分配 UV：每个面指定纹理区域，确保贴图分区对应。
4. 画贴图，或导出 UV 模板交给 `pixel_art` 手绘。
5. 导出：方块/物品选「Minecraft Java Block/Item」；实体选 GeckoLib 或原版格式。
6. **导出后必须人工核对 JSON**：Blockbench 偶发字段（`rotation` 的 angle、uv 顺序、`rescale`）与游戏实际读取不一致。

## 手写 JSON 要点（不用 Blockbench 时）
- `elements` 从 `from`（左下后角）到 `to`（右上前角），坐标 0–16 整数或 .5/.25 小数。
- 每个面 UV 顺序是 `[u1, v1, u2, v2]`（左下角到右上角），`texture` 引用 `#` 编号键。
- `cullface` 值：`down/up/north/south/west/east`，只有面向该方向才渲染，避免内面穿帮。
- 不要混用 1.21 的 `item_model` 语法和 1.20.1 的 `layer0`；以目标 MC 版本为准。

## 质量检查
- 无 z-fighting（共面）、无 UV 越界、纹理不拉伸、透明剔除正确。
- 物品 `display` 实际进游戏看手持角度；方块六面、掉落物、第三人称都验证一遍。
- 完整建模流程见 asset-3d-modeling。
""",
    },
    {
        "id": "mod-worldgen",
        "name": "世界生成",
        "description": "世界生成：configured/placed feature、biome modifier、维度 JSON、datapack 结构与 noise settings，Forge BiomeModifier 与 Fabric biome API。",
        "body": """# 世界生成

## 层次（从数据到游戏）
`biome → placed feature → configured feature → feature（生成逻辑）`，再加 `noise settings`（维度地形）、`dimension`（维度 JSON）、`structure`（数据包结构）。逐层是“什么生成在哪儿、多大密度、什么形状”。

## Feature 三层（1.20.1 数据驱动）
- **ConfiguredFeature**：`data/<ns>/worldgen/configured_feature/<name>.json`，指定 `type`（如 `minecraft:ore`、`minecraft:tree`、自定义 feature 类）+ `config`（矿的 `targets`、树的结构）。
- **PlacedFeature**：`data/<ns>/worldgen/placed_feature/<name>.json`，`feature` 引用 configured，`placement` 数组指定 `count`/`rarity`/`height` 过滤器等。
- 自定义 `Feature` 类：`Feature<FC>` + `Codec`，在 `DeferredRegister`（`ForgeRegistries.FEATURES`）注册，JSON 里 `type: "modid:name"` 引用。

## 1.20.1 Forge：BiomeModifier
- 用 `BiomeModifier` 把 placed feature 加进生物群系，而不是直接改 biome JSON。
- 常用 `ForgeBiomeModifiers.AddFeaturesBiomeModifier`，JSON 在 `data/<ns>/forge/biome_modifier/<name>.json`：
```json
{
  "type": "forge:add_features",
  "biomes": "#minecraft:is_overworld",
  "features": "modid:ore_example",
  "step": "underground_ores"
}
```
- `step` 决定生成阶段（`raw_generation`/`underground_ores`/`vegetal_decoration`/`top_layer_modification` 等），错阶段会不生成。
- `biomes` 用 biome 标签（`#`）或单个 biome id；想加进所有生物群系用 `#is_overworld` 这类原版标签。

## Fabric：Biome Modification API
- 用 `fabric-biome-api-v1`，`BiomeModifications.addFeature(BiomeSelectors.foundInOverworld(), GenerationStep.Feature.UNDERGROUND_ORES, registryKey)`。
- `BiomeSelectors`：`foundInOverworld()`/`foundInTheNether()`/`tag(...)`/`includeByKey(...)`。
- `GenerationStep.Feature.*` 对应 Forge 的 `step`。
- 需在 `fabric.mod.json` 声明 `fabric-biome-api-v1` 依赖，并在 entrypoint 里执行。

## 维度 JSON
- `data/<ns>/dimension/<name>.json`：`type`（`minecraft:overworld` 或自定义 `DimensionType`）、`generator`（`type: minecraft:noise` + `settings` 引用 noise settings + `biome_source`）。
- 自定义维度先要有 `DimensionType`（JSON）与 `noise_settings`，再在 JSON 里引用；跨维度传送用 `ServerPlayer.changeDimension` / `teleportTo`，标【⚠️人工复核】。

## Noise settings（地形）
- `data/<ns>/worldgen/noise_settings/<name>.json`：`noise_router`（各层噪声）、`sea_level`、`default_block`/`default_fluid`、`surface_rule` 等。
- 改地形是高风险操作，逐字段核对原版样例，不凭记忆写 `noise_router`；改错直接崩地形或白屏。

## Structure（数据包结构）
- 结构有 `.nbt`（用结构方块导出）+ `structure`/`structure_set` JSON（`data/<ns>/worldgen/structure/` 与 `structure_set/`）+ 模板池（`template_pool`，用于 jigsaw）。
- 结构方块或 `/place` 导出 `.nbt`，放 `data/<ns>/structures/<name>.nbt`。
- `structure` JSON 的 `biomes`、`step`、`spawn_overrides`、`type`（`jigsaw` 需要 `start_pool`）都要配全；jigsaw 结构最容易漏 `template_pool`。

## 验证
- 用 `/locate structure <ns>:<name>`、`/locate biome` 验证存在；`/place feature <ns>:<name>` 直接测试 feature。
- 新世界才生成，旧区块不刷新；测 worldgen 要开新世界或删区块。
- 生成密度、矿石埋深、生物群系过滤都要在游戏里实际看，不能只看 JSON 没报错。
- 维度、地形、跨维度传送、生物群系修改一律标【⚠️人工复核】。
""",
    },
    {
        "id": "mod-entity",
        "name": "实体开发",
        "description": "实体开发：EntityType+Entity 类、属性、渲染器/模型层、AI goal、生成蛋与战利品，Forge/Fabric 差异。",
        "body": """# 实体开发

## 组成
`EntityType` + `Entity` 子类 + 注册 + 属性（Attribute）+ 渲染器（Renderer/ModelLayer）+ AI（Goal）+ 生成蛋 + 战利品表。缺一样实体都“看不见/不会动/不掉东西”。

## 注册与 Entity 类
- Forge：`DeferredRegister.create(ForgeRegistries.ENTITY_TYPES, MODID)`，`EntityType.Builder.of(MyEntity::new, MobCategory.CREATURE).sized(w, h).build("modid:example")`。
- Fabric：`Registry.register(Registries.ENTITY_TYPE, id, FabricEntityTypeBuilder.create(SpawnGroup.CREATURE, MyEntity::new).dimensions(EntityDimensions.fixed(w, h)).build())`。
- `Entity` 子类构造器传 `EntityType` 和 `Level`；生物用 `Mob`/`PathfinderMob`，飞行用 `FlyingMob`，远程用 `Mob` + 投射物。
- `registerAttributes()`（Forge 生物）/ `createMobAttributes()`（Fabric）设置基础属性。

## 属性
- 生命 `MAX_HEALTH`、移动 `MOVEMENT_SPEED`、攻击 `ATTACK_DAMAGE`、护甲 `ARMOR`、跟随距离 `FOLLOW_RANGE`、击退抗性 `KNOCKBACK_RESISTANCE`。
- 用 `AttributeSupplier.Builder`；Forge 在 `EntityAttributeCreationEvent` 注册，Fabric 在 `DefaultAttributeRegistry.register`。

## 渲染器与模型层
- Forge：`EntityRenderers.register(type, MyRenderer::new)` 在 `FMLClientSetupEvent`；`ModelLayer` 用 `LayerDefinition.create(...)` 注册。
- Fabric：`EntityRendererRegistry.register(type, MyRenderer::new)` 在 `ClientModInitializer`；模型层 `EntityModelLayerRegistry.registerModelLayer(...)`。
- `Renderer` 指定纹理（`getTextureLocation`）、模型、阴影大小；缩放用 `scale`。
- 模型、纹理、渲染器、模型层四者注册名/路径必须一致，否则白模或透明。

## AI Goal
- 在 `registerGoals()` 里 `goalSelector.addGoal(priority, new Goal())`，优先级数字越小越先执行（0/1/2…）。
- 常用目标：`FloatGoal`、`MeleeAttackGoal`、`WaterAvoidingRandomStrollGoal`、`LookAtPlayerGoal`、`RandomLookAroundGoal`、`FollowOwnerGoal`、`PanicGoal`、`TemptGoal`。
- 自定义 AI 继承 `Goal`，实现 `canUse()`、`canContinueToUse()`、`tick()`；目标选择器（`targetSelector`）用 `NearestAttackableTargetGoal` 等。
- AI 只跑服务端；客户端只消费渲染，不要用 AI 驱动客户端表现。

## 生成蛋与自然生成
- 生成蛋：Forge `new SpawnEggItem(type, c1, c2, props)`，Fabric `new SpawnEggItem(type, c1, c2, settings)`，颜色给 16 进制。
- 自然生成：用生物群系修改（Forge `AddSpawnsBiomeModifier` / Fabric `BiomeModifications.addSpawn`），配置 `type`、`weight`、`minCount`/`maxCount`。
- 只加实体不加 spawn 配置，生物不会自然出现。

## 战利品
- `data/<ns>/loot_tables/entities/<entity_id>.json`；实体掉落由 `getLootTable` 返回表 id 或重写 `dropCustomDeathLoot` 自定义掉落。
- 避免重复掉落：原版掉落 + 自定义掉落都触发会掉两份。

## Forge / Fabric 差异速查
| 事项 | Forge 1.20.1 | Fabric |
| --- | --- | --- |
| 实体类型 | `EntityType.Builder` | `FabricEntityTypeBuilder` |
| 属性注册 | `EntityAttributeCreationEvent` | `DefaultAttributeRegistry` |
| 渲染器 | `EntityRenderers.register` | `EntityRendererRegistry.register` |
| 生成蛋 | `new SpawnEggItem(type, c1, c2, props)` | `new SpawnEggItem(type, c1, c2, settings)` |
| 世界/玩家 | `Level` / `Player` / `ServerPlayer` | `World` / `PlayerEntity` / `ServerPlayerEntity` |

## 检查
- 客户端类（渲染器/模型）是否只进 client 入口；专用服务端能否启动。
- 碰撞箱、眼睛高度、骑乘位置、命名（`/summon` 用注册名）是否一致。
- 实体 AI、远程攻击投射物、生成蛋颜色、自然生成、跨版本映射标【⚠️人工复核】。
""",
    },
    {
        "id": "mod-recipe-loot",
        "name": "配方与战利品表",
        "description": "配方与战利品表：有序/无序/熔炼/锻造台/切石机配方 JSON 与战利品池/条件/函数全规范，自定义配方类型。",
        "body": """# 配方与战利品表

## 配方 JSON（1.20.1 格式）
目录 `data/<ns>/recipes/<name>.json`。字段 `type`、`result`（1.20.1 用 `{"item": "...", "count": n}`，1.20.5+ 改 `{"id": ...}`）、`ingredients`/`key`。

### 有序（shaped）
```json
{
  "type": "minecraft:crafting_shaped",
  "pattern": ["AAA", "B B", "AAA"],
  "key": { "A": {"item": "minecraft:iron_ingot"}, "B": {"item": "minecraft:stick"} },
  "result": {"item": "modid:example", "count": 1}
}
```
- `pattern` 每行等长（空位用空格），最多 3×3；`key` 里字符→材料映射。
- 支持 tag 材料：`"A": {"tag": "minecraft:planks"}`，比写死 item 更稳。

### 无序（shapeless）
```json
{
  "type": "minecraft:crafting_shapeless",
  "ingredients": [{"item": "minecraft:diamond"}, {"tag": "minecraft:planks"}],
  "result": {"item": "modid:example", "count": 2}
}
```
- 无序不关心位置，`ingredients` 平铺数组。

### 熔炼 / 烟熏 / 高炉
```json
{
  "type": "minecraft:smelting",
  "ingredient": {"item": "modid:ore"},
  "result": "modid:ingot",
  "experience": 0.7,
  "cookingtime": 200
}
```
- `type` 用 `smelting` / `blasting` / `smoking` / `campfire_cooking`；`result` 是单 item 字符串（1.20.1），`cookingtime` 单位 tick（200=10秒）。

### 锻造台（smithing）
```json
{
  "type": "minecraft:smithing_transform",
  "template": {"item": "minecraft:netherite_upgrade_smithing_template"},
  "base": {"item": "minecraft:diamond_sword"},
  "addition": {"item": "minecraft:netherite_ingot"},
  "result": {"item": "minecraft:netherite_sword"}
}
```
- 1.20+ 分 `smithing_transform`（有模板）和 `smithing_trim`（盔甲纹饰）；1.19 及以前是 `smithing`（无 template 字段）。

### 切石机（stonecutting）
```json
{
  "type": "minecraft:stonecutting",
  "ingredient": {"item": "minecraft:stone"},
  "result": "modid:slab",
  "count": 2
}
```

## 自定义配方类型
1. 实现 `Recipe<Container>`（`matches`/`assemble`/`getSerializer`/`getType`）。
2. `RecipeSerializer`：实现 `fromJson` / `toNetwork` / `fromNetwork`，用 `Codec` 或手动读 JSON。
3. 注册：Forge `DeferredRegister.create(ForgeRegistries.RECIPE_SERIALIZERS/RECIPE_TYPES)`；Fabric `Registry.register(Registries.RECIPE_SERIALIZER/RECIPE_TYPE)`。
4. 配方 JSON 的 `type` 写自定义序列化器 id，如 `"type": "modid:my_recipe"`。
5. 复杂配方类型标【⚠️人工复核】，并给 JEI 兼容提示。

## 战利品表 JSON
目录 `data/<ns>/loot_tables/blocks/<name>.json`（方块）、`loot_tables/entities/<id>.json`（实体）、`loot_tables/chests/<name>.json`（战利品箱）。结构：
```json
{
  "type": "minecraft:block",
  "pools": [
    {
      "rolls": 1,
      "bonus_rolls": 0,
      "entries": [
        {"type": "minecraft:item", "name": "modid:example", "functions": [], "conditions": []}
      ],
      "conditions": []
    }
  ]
}
```
- **pools**：多个池独立掷骰；`rolls` 掷的次数，`bonus_rolls` 每级幸运加的次数。
- **entries**：`item` / `tag` / `loot_table`（引用别的表）/ `empty` / `alternatives` / `group`；`weight` 权重、`quality` 受幸运影响。
- **functions**：`set_count`、`set_damage`、`enchant_randomly`、`set_nbt`、`looting_enchant`、`furnace_smelt`、`set_name`、`explosion_decay`。
- **conditions**：`survives_explosion`、`killed_by_player`、`random_chance`、`match_tool`、`table_bonus`、`entity_properties`。
- 方块默认掉落由 `Block.getLootTable()` 决定；改方块掉落优先换战利品表路径，不要重写 `getDrops`。

## 常见坑
- 1.20.1 配方用 `{"item":"...","count":n}`，1.20.5+ 用 `{"id":"..."}`；混用直接解析失败。
- 战利品条件不满足时整条 entry 不落，不是“落一半”。
- `explosion_decay` 让方块被炸掉时按概率掉落，矿石类要加。
- 配方解锁：`data/<ns>/advancements/recipes/...` 的 `criteria` 决定玩家何时学会配方；漏掉则知识书里不显示。
""",
    },
    {
        "id": "mod-progression",
        "name": "进度配置标签页与 JEI",
        "description": "进度系统、配置项、创造模式标签页与 JEI 兼容思路。",
        "body": """# 进度 / 配置 / 创造标签页 / JEI

## 进度（Advancement）
目录 `data/<ns>/advancements/<category>/<name>.json`。结构：
```json
{
  "display": {
    "icon": {"item": "modid:example"},
    "title": {"translate": "advancements.modid.example.title"},
    "description": {"translate": "advancements.modid.example.desc"},
    "frame": "task",
    "background": "minecraft:textures/gui/advancements/backgrounds/stone.png",
    "show_toast": true, "announce_to_chat": true, "hidden": false
  },
  "parent": "modid:category/root",
  "criteria": {
    "got_item": {"trigger": "minecraft:inventory_changed", "conditions": {"items": [{"items": ["modid:example"]}]}}
  }
}
```
- 根进度（无 `parent`）要设 `background`；`frame` 用 `task`/`goal`/`challenge`。
- `criteria` 的 `trigger` 用原版触发器（`inventory_changed`、`entity_killed_player`、`tick`、`recipe_unlocked`、`location` 等）+ `conditions`；多条件用 `requirements` 数组控制 AND/OR。
- 语言键写进 `lang/en_us.json`、`zh_cn.json`。
- 进度树：根 → 子级用 `parent` 链起来；`hidden` 进度不预先显示，达成才出现。

## 配置项（Config）
- Forge 1.20.1：用 `ForgeConfigSpec`（`COMMON`/`CLIENT`/`SERVER` 三个 spec），构造器里 `Builder` 定义条目，`ModLoadingContext.get().registerConfig(...)` 挂载；`FMLPaths.CONFIGDIR` 定位配置目录。
- NeoForge：`ModConfigSpec` 替代 `ForgeConfigSpec`；`ModContainer.registerConfig`。
- Fabric：用 `fabric-loader` 的 `config` 或 Cloth Config / `AutoConfig`；`FabricLoader.getInstance().getConfigDir()` 定位。
- 配置值用 getter 包装，不要在运行中改 `ConfigSpec` 定义；布尔/整数/枚举/列表都支持。
- 服务器配置与客户端配置分开；同步型配置走网络包标【⚠️人工复核】。

## 创造模式标签页
- Forge：`DeferredRegister.create(ForgeRegistries.CREATIVE_MODE_TABS, MODID)`，注册 `CreativeModeTab`，`displayItems` 用 `output.accept(item)` 填充。
- 1.20.1 用 `CreativeModeTab.builder().title(...).icon(...).displayItems((params, output) -> {...}).build()`。
- Fabric：`Registries.ITEM_GROUP` + `ItemGroup.Builder` + `ItemGroupEvents.modifyEntriesEvent(...)`。
- 图标要注册名对应的物品纹理；自定义排序用 `output.accept` 顺序，不要靠名字排序。
- 客户端图标渲染别进服务端注册路径。

## JEI 兼容思路
- 显示自定义配方：给配方类型写 JEI 插件类（`@JeiPlugin` 或 `IModPlugin`），`registerRecipeCatalysts`/`registerRecipes` 把配方类型和合成台类型绑起来。
- 配方类别 `RecipeCategory` + `RecipeType`，渲染用 `IRecipeSlotDrawable`；复杂类别（多方块、自定义 GUI）标【⚠️人工复核】。
- `getRecipeType` 必须与注册的 `RecipeType` 一致，否则 JEI 不显示。
- 没有 JEI 依赖时先留扩展点，不把 JEI 类打进生产构建（用 `Optional` 或独立模块）。

## 检查
- 进度 criteria 的 trigger 名和条件字段拼写正确，语言键齐全；根进度有 background。
- 配置默认值合理、有注释；服务器/客户端配置不混淆。
- 标签页物品能正常显示图标；JEI 插件类只在 JEI 存在时加载。
""",
    },
    {
        "id": "mod-gui-network",
        "name": "GUI 网络与方块实体同步",
        "description": "GUI（Screen/Menu/AbstractContainerMenu）、网络包（SimpleChannel/自定义 payload）与方块实体同步。",
        "body": """# GUI / 网络 / 方块实体同步

## GUI 组成（服务端容器 + 客户端屏幕）
- 服务端：`MenuType` + `AbstractContainerMenu`（原版）子类；客户端：`Screen<Menu>` 子类。
- Forge 1.20.1：`DeferredRegister.create(ForgeRegistries.MENU_TYPES, MODID)`，`IContainerFactory` 构造菜单；`MenuScreens.register(menuType, MyScreen::new)` 在客户端。
- Fabric：`Registries.SCREEN_HANDLER` + `ScreenHandler`（`ScreenHandlerType`），`HandledScreens.register` 在客户端。

## AbstractContainerMenu（Forge）/ ScreenHandler（Fabric）
- 构造器保存容器引用、槽位；`quickMoveStack`（快速移动）逻辑：先容器内槽位，再玩家背包，返回 `ItemStack.EMPTY` 或剩余物。
- `stillValid(Player)` 判断玩家距离容器是否太远，远则关闭。
- `addSlot` 逐个加槽位；槽位索引要与客户端 `Screen` 的槽位渲染顺序一致，否则物品错位。
- 方块实体菜单：方块实体实现 `MenuProvider`（`createMenu` 返回菜单），方块交互用 `NetworkHooks.openScreen`（Forge）/ `ServerPlayerEntity.openHandledScreen`（Fabric）。

## Screen 客户端
- `Screen` 子类绑定纹理（`getBackgroundTexture`），`renderBg`/`render` 画背景和文字；槽位用 `Slot` 渲染。
- GUI 纹理 256×256 九宫格；槽位 18×18，按钮三态（normal/hover/disabled）；文字对比度足够（见 visual-design）。
- 客户端只读菜单里的数据（`containerData`/`getSlot`），不要直接在 `Screen` 里改服务端状态。

## 网络包
### Forge 1.20.1：SimpleChannel
```java
SimpleChannel CHANNEL = NetworkRegistry.newSimpleChannel(
    new ResourceLocation(MODID, "main"),
    () -> PROTOCOL_VERSION, PROTOCOL_VERSION::equals, PROTOCOL_VERSION::equals);
CHANNEL.registerMessage(0, MyPacket.class, MyPacket::encode, MyPacket::decode, MyPacket::handle);
```
- 每个包一个唯一 index；`encode`/`decode` 写读 `FriendlyByteBuf`；`handle` 里 `context.get().enqueueWork(...)` 切回主线程再改游戏状态，`setPacketHandled(true)` 收尾。
- 发送：`CHANNEL.send(PacketDistributor.SERVER.noArg(), packet)`（客户端→服务端）、`send(PacketDistributor.PLAYER.with(() -> serverPlayer), packet)`（服务端→某玩家）。
- 版本常量（`PROTOCOL_VERSION`）升级时同步改，否则联机报协议不匹配。

### NeoForge / Fabric：自定义 Payload
- NeoForge：`CustomPacketPayload` + `FriendlyByteBuf` codec，`RegisterPayloadHandlersEvent` 注册 play 双向处理器，`PayloadRegistrar.playToClient/playToServer`。
- Fabric：`PayloadTypeRegistry.playC2S/playS2C` 注册，`CustomPayload` 实现 `getId`，`ServerPlayNetworking`/`ClientPlayNetworking` 收发；`FabricPacket` + `PacketType` 是更简封装。
- 网络包一律标【⚠️人工复核】：说明发送侧、接收侧、线程切换、字段含义。

## 方块实体同步
- 方块实体继承 `BlockEntity`，构造器传 `BlockEntityType` 和 `pos`/`state`；注册 `BlockEntityType.Builder.of(MyBE::new, block).build()`（Forge）/ `BlockEntityType.Builder.create(...)`（Fabric）。
- 读写 NBT：1.20.1 `load`/`saveAdditional`（`CompoundTag`），1.20.5+ `loadAdditional`/`saveAdditional`（`Tag`）；同步客户端用 `getUpdateTag`/`handleUpdateTag` 或 `ClientboundBlockEntityDataPacket`。
- `markDirty`/`setChanged` 通知保存；`getUpdatePacket` 返回同步包让客户端看到变化。
- 只在状态变化时同步，不要每 tick 全量发（性能红线，见 code-engineering）。
- 库存型方块实体实现 `Container`（原版）或自定义，配合菜单同步槽位。

## 检查
- 菜单/屏幕/网络包注册时机正确；客户端类不进服务端。
- `quickMoveStack` 边界不越界；槽位索引一致；`stillValid` 生效。
- 网络包版本号一致、线程切换到位；方块实体 NBT 键稳定不漂移。
- GUI、网络、方块实体同步全部标【⚠️人工复核】。
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
    "mechanism-playbook": (
        "变身", "夺舍", "换皮肤", "皮肤", "人格", "形态", "控制权", "相机",
        "聊天框", "对话", "物品框", "背包", "社交", "关系", "好感", "状态机",
        "变成", "进入身体", "附身", "littleskin", "skin",
    ),
    "large-mod-playbook": (
        "大模组", "暮色森林", "工程方法", "目录组织", "里程碑", "注册中心",
        "渐进构建", "模组架构", "mod架构", "大型模组",
    ),
    "mod-registry": (
        "注册", "registry", "deferredregister", "registerevent", "注册表",
        "registries", "注册中心", "创造标签页", "附魔注册", "药水注册",
    ),
    "mod-model-texture": (
        "模型json", "blockstates", "multipart", "variants", "cuboid", "纹理规范",
        "16x16", "32x32", "display", "elements", "uv映射", "模型规范",
    ),
    "mod-worldgen": (
        "世界生成", "worldgen", "biome", "生物群系", "feature", "placement",
        "维度", "dimension", "noise", "地形", "结构", "structure", "矿脉", "ore",
        "biomemodifier",
    ),
    "mod-entity": (
        "实体", "entity", "entitytype", "渲染器", "renderer", "modellayer",
        "ai", "goal", "spawn egg", "生成蛋", "怪物", "生物",
        "attribute", "属性",
    ),
    "mod-recipe-loot": (
        "配方", "recipe", "战利品", "loot", "合成", "熔炼", "锻造台", "切石机",
        "smithing", "stonecutting", "无序", "有序", "loottable",
    ),
    "mod-progression": (
        "进度", "advancement", "成就", "配置", "config", "创造标签页",
        "jei", "标签页", "creative",
    ),
    "mod-gui-network": (
        "gui", "screen", "menu", "容器", "container", "网络包", "网络",
        "simplechannel", "payload", "方块实体同步", "同步", "packet", "network",
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
