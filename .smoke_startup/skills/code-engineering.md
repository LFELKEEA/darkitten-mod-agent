---
id: code-engineering
name: 代码工程与重构
description: 架构分层、命名、可读性、性能、并发、测试、重构与代码审查；写代码前先读，改完必须编译。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 代码工程与重构

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
