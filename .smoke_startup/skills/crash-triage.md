---
id: crash-triage
name: 崩溃与编译报错排查
description: 解析 gradle 编译错误、crash report、Mixin 注入失败，定位文件行号并给出完整修复文件。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 崩溃与编译报错排查

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
