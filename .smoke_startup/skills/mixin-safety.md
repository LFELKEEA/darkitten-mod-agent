---
id: mixin-safety
name: Mixin 安全规范
description: 只在必要时使用 Mixin，并给出目标、注入点、兼容性与人工复核清单。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# Mixin 安全规范

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
