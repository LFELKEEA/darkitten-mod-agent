---
id: reference-research
name: 真实代码检索
description: 写代码前先查真实实现：GitHub 仓库、grep.app、Modrinth，处理限流和网络失败，避免凭记忆猜 API。
enabled: true
builtin: true
updated_at: 2026-11-21T00:38:50+08:00
---

# 真实代码检索

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
