---
id: large-mod-playbook
name: 大模组工程方法学
description: 大模组工程方法学：目录组织、注册中心、渐进构建与里程碑计划，面向 100+ 文件、跨维度/世界生成/实体/进度的大型模组。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 大模组工程方法学

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
