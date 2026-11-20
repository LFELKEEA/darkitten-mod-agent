---
id: modrinth-lookup
name: 资料查询与依赖核对
description: 通过连接器查询 Modrinth / Fabric Meta / GitHub，核对模组版本、依赖与加载器兼容性。
enabled: true
builtin: true
updated_at: 2026-11-21T00:37:59+08:00
---

# 资料查询与依赖核对

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
