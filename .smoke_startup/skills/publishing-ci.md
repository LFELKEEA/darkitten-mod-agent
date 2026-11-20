---
id: publishing-ci
name: 发布与 CI
description: GitHub Actions 构建矩阵、JAR 命名、Modrinth/CurseForge 发布、版本号与校验和。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 发布与 CI

## 构建矩阵
- 维度：MC 版本 × 加载器 × Java。
- 每个组合独立 `build`，产物按 `<modid>-<modversion>+<mc>-<loader>.jar` 命名。
- 矩阵用 `fail-fast: false`，避免一个组合失败掩盖其他结果。

## GitHub Actions 要点
- `actions/setup-java` 用 `temurin`，版本按矩阵。
- 缓存 `~/.gradle/caches` 与 `~/.gradle/wrapper`。
- 上传 `build/libs/*.jar`，排除 `-sources`、`-dev`、`-javadoc`。
- 发布前跑 GameTest 或至少 `runGameTestServer`。

## 发布元数据
- Modrinth/CurseForge 需要：版本号、支持的 MC 版本、加载器、依赖、changelog。
- 版本号与 `gradle.properties` 单一来源，不要在多个文件手改。
- 产物附 SHA-256，发布页写清 Java 要求。

## 交付检查
- [ ] 所有矩阵组合构建通过
- [ ] JAR 能在干净客户端加载
- [ ] 元数据版本区间正确
- [ ] changelog 与代码一致
- [ ] 校验和与下载链接可用
