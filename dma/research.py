from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from . import connectors

GITHUB_API = "https://api.github.com"
GREP_API = "https://grep.app/api/search"
MODRINTH_API = "https://api.modrinth.com/v2"
MAX_TEXT = 240_000
REPO_RE = re.compile(r"^(?:https?://github\.com/)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?(?:$|[?#])")


class ResearchError(Exception):
    pass


def _github_headers() -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _json(url: str, *, headers: dict[str, str] | None = None) -> Any:
    text = connectors.fetch_url(url, headers=headers or _github_headers(), timeout=35, max_bytes=MAX_TEXT)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ResearchError(f"接口返回的不是 JSON：{text[:200]}") from exc


def _rate_limit_message(detail: str) -> str:
    if "rate limit" in detail.lower() or "403" in detail:
        return (
            "GitHub 匿名接口已限流（每小时 60 次）。可以：\n"
            "1) 在环境变量里设置 GITHUB_TOKEN 后重试；\n"
            "2) 先用 Modrinth / 已知仓库名，再用 github_file 精确读取；\n"
            "3) 等一小时后再查。"
        )
    return detail


def parse_repo(value: str) -> str:
    text = (value or "").strip()
    match = REPO_RE.match(text)
    if not match:
        raise ResearchError("仓库名要写成 owner/repo，例如 Luke100000/minecraft-comes-alive。")
    return f"{match.group(1)}/{match.group(2)}"


def repo_search(query: str, limit: int = 8) -> list[dict[str, Any]]:
    text = (query or "").strip()
    if not text:
        raise ResearchError("搜索关键词为空。")
    query_text = urllib.parse.quote(text)
    url = f"{GITHUB_API}/search/repositories?q={query_text}&sort=stars&order=desc&per_page={max(1, min(30, limit))}"
    try:
        data = _json(url)
    except connectors.ConnectorError as exc:
        raise ResearchError(_rate_limit_message(str(exc))) from exc
    items = data.get("items") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "repo": str(item.get("full_name") or ""),
                "url": str(item.get("html_url") or ""),
                "description": str(item.get("description") or ""),
                "stars": int(item.get("stargazers_count") or 0),
                "branch": str(item.get("default_branch") or ""),
                "language": str(item.get("language") or ""),
            }
        )
    return result


def repo_info(repo: str) -> dict[str, Any]:
    name = parse_repo(repo)
    try:
        data = _json(f"{GITHUB_API}/repos/{name}")
    except connectors.ConnectorError as exc:
        raise ResearchError(_rate_limit_message(str(exc))) from exc
    if not isinstance(data, dict):
        raise ResearchError("没有取到仓库信息。")
    return {
        "repo": name,
        "url": str(data.get("html_url") or f"https://github.com/{name}"),
        "description": str(data.get("description") or ""),
        "branch": str(data.get("default_branch") or "main"),
        "stars": int(data.get("stargazers_count") or 0),
        "language": str(data.get("language") or ""),
        "topics": data.get("topics") or [],
    }


def repo_tree(repo: str, ref: str = "", path: str = "", limit: int = 260) -> list[str]:
    """列出仓库文件。路径过滤用前缀，例如 common/src/main/java。"""
    name = parse_repo(repo)
    branch = (ref or "").strip()
    if not branch:
        branch = str(repo_info(name).get("branch") or "main")
    encoded = urllib.parse.quote(branch, safe="")
    url = f"{GITHUB_API}/repos/{name}/git/trees/{encoded}?recursive=1"
    try:
        data = _json(url)
    except connectors.ConnectorError as exc:
        raise ResearchError(_rate_limit_message(str(exc))) from exc
    tree = data.get("tree") if isinstance(data, dict) else None
    if not isinstance(tree, list):
        raise ResearchError("没有取到目录树。检查分支或标签名是否正确。")
    prefix = str(path or "").strip().strip("/")
    result: list[str] = []
    for item in tree:
        if not isinstance(item, dict) or item.get("type") != "blob":
            continue
        item_path = str(item.get("path") or "")
        if prefix and not item_path.startswith(prefix):
            continue
        result.append(item_path)
        if len(result) >= limit:
            break
    return result


def repo_file(repo: str, path: str, ref: str = "") -> str:
    """读取仓库里的单个文本文件（源码、JSON、Gradle 配置）。"""
    name = parse_repo(repo)
    clean = str(path or "").strip().lstrip("/")
    if not clean or ".." in clean.split("/"):
        raise ResearchError("文件路径不合法。")
    branch = (ref or "").strip()
    if not branch:
        branch = str(repo_info(name).get("branch") or "main")
    encoded_path = urllib.parse.quote(clean, safe="/")
    encoded_ref = urllib.parse.quote(branch, safe="")
    url = f"{GITHUB_API}/repos/{name}/contents/{encoded_path}?ref={encoded_ref}"
    try:
        text = connectors.fetch_url(
            url,
            headers={"Accept": "application/vnd.github.raw"},
            timeout=40,
            max_bytes=MAX_TEXT,
        )
    except connectors.ConnectorError as exc:
        message = _rate_limit_message(str(exc))
        if "404" in message:
            raise ResearchError(
                f"{name} 的 {branch} 分支里没有 {clean}。可以先用 github_tree 查路径。"
            ) from exc
        raise ResearchError(message) from exc
    return text


def grep_search(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """grep.app 代码搜索，不需要 token，但公共接口经常限流。"""
    text = (query or "").strip()
    if not text:
        raise ResearchError("搜索关键词为空。")
    url = f"{GREP_API}?q={urllib.parse.quote(text)}&regexp=false"
    try:
        data = _json(
            url,
            headers={"Accept": "application/json", "User-Agent": "DarkittenModAgent/1.3"},
        )
    except connectors.ConnectorError as exc:
        raise ResearchError(
            "grep.app 代码搜索暂时不可用（常见原因是限流）。"
            "可以改用 github_repos 找仓库，再 github_tree / github_file 精确读取。"
        ) from exc
    hits = data.get("hits", {}).get("hits") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for item in hits or []:
        if not isinstance(item, dict):
            continue
        raw_repo = item.get("repo") or {}
        raw_path = item.get("path") or {}
        repo = str(raw_repo.get("raw") or raw_repo.get("name") or "") if isinstance(raw_repo, dict) else str(raw_repo)
        path = str(raw_path.get("raw") or raw_path.get("name") or "") if isinstance(raw_path, dict) else str(raw_path)
        content = str(item.get("content", {}).get("snippet") or "")
        content = re.sub(r"<[^>]+>", "", content)
        result.append({"repo": repo, "path": path, "snippet": content.strip()})
        if len(result) >= limit:
            break
    return result


def modrinth_search(
    query: str,
    *,
    mc_version: str = "",
    loader: str = "",
    limit: int = 8,
) -> list[dict[str, Any]]:
    facets: list[list[str]] = []
    if mc_version:
        facets.append([f"versions:{mc_version}"])
    if loader:
        facets.append([f"categories:{loader.lower()}"])
    params = {
        "query": (query or "").strip(),
        "limit": str(max(1, min(20, limit))),
    }
    if facets:
        params["facets"] = json.dumps(facets)
    url = f"{MODRINTH_API}/search?{urllib.parse.urlencode(params)}"
    try:
        data = _json(url, headers={"User-Agent": "DarkittenModAgent/1.3"})
    except connectors.ConnectorError as exc:
        raise ResearchError(f"Modrinth 查询失败：{exc}") from exc
    hits = data.get("hits") if isinstance(data, dict) else None
    result: list[dict[str, Any]] = []
    for item in hits or []:
        if not isinstance(item, dict):
            continue
        result.append(
            {
                "slug": str(item.get("slug") or ""),
                "title": str(item.get("title") or ""),
                "description": str(item.get("description") or ""),
                "downloads": int(item.get("downloads") or 0),
                "url": f"https://modrinth.com/mod/{item.get('slug')}",
                "versions": item.get("versions") or [],
                "categories": item.get("categories") or [],
            }
        )
    return result


def format_repo_results(items: list[dict[str, Any]]) -> str:
    if not items:
        return "没有找到匹配的仓库。"
    lines = ["找到这些仓库："]
    for item in items:
        stars = item.get("stars") or 0
        desc = item.get("description") or "（无描述）"
        lines.append(
            f"- [{item.get('repo')}]({item.get('url')}) · ★{stars} · "
            f"{item.get('language') or '未知语言'}\n  {desc}"
        )
    return "\n".join(lines)


def format_tree(repo: str, items: list[str], limit: int = 260) -> str:
    if not items:
        return "这个路径下没有文件。"
    lines = [f"{repo} 的文件（{len(items)} 个）：", "```"]
    lines.extend(items[:limit])
    if len(items) > limit:
        lines.append(f"…还有 {len(items) - limit} 个")
    lines.append("```")
    return "\n".join(lines)


def format_grep_results(items: list[dict[str, Any]]) -> str:
    if not items:
        return "没有找到匹配代码。"
    lines = ["代码搜索结果："]
    for item in items:
        lines.append(
            f"- {item.get('repo')} · `{item.get('path')}`\n"
            f"  ```java\n{item.get('snippet')[:600]}\n  ```"
        )
    return "\n".join(lines)


def search_code(query: str, *, kind: str = "auto", limit: int = 12) -> str:
    """给 Agent 的统一检索入口，返回可直接阅读的 Markdown。"""
    text = (query or "").strip()
    if not text:
        return "搜索关键词为空。"
    kind = (kind or "auto").strip().lower()
    errors: list[str] = []
    if kind in {"auto", "grep", "code"}:
        try:
            hits = grep_search(text, limit=limit)
        except ResearchError as exc:
            errors.append(str(exc))
        else:
            if hits:
                return format_grep_results(hits)
            errors.append("grep.app 没有找到匹配代码。")
    if kind in {"auto", "repo", "repos", "github"}:
        try:
            repos = repo_search(text, limit=limit)
        except ResearchError as exc:
            errors.append(str(exc))
        else:
            if repos:
                note = ""
                if errors:
                    note = "\n\n（代码搜索不可用，已改为仓库搜索：）\n"
                return note + format_repo_results(repos)
    if kind in {"auto", "modrinth", "mod"}:
        try:
            mods = modrinth_search(text, limit=limit)
        except ResearchError as exc:
            errors.append(str(exc))
        else:
            if mods:
                lines = ["Modrinth 上的相关模组："]
                for item in mods:
                    lines.append(
                        f"- [{item['title']}]({item['url']}) · "
                        f"{item.get('downloads', 0):,} 次下载\n  {item.get('description', '')}"
                    )
                return "\n".join(lines)
    return "检索没有结果：\n- " + "\n- ".join(dict.fromkeys(errors or ["没有匹配内容。"]))


@dataclass
class ReferenceFile:
    repo: str
    path: str
    text: str
    ref: str = ""


@dataclass
class ReferenceBundle:
    repo: str
    ref: str = ""
    files: list[ReferenceFile] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def render(self, limit: int = 60_000) -> str:
        chunks = [f"# 参考资料：{self.repo}（{self.ref or '默认分支'}）"]
        for item in self.files:
            chunks.append(f"\n## {item.path}\n```\n{item.text[:limit]}\n```")
        if self.notes:
            chunks.append("\n## 备注\n" + "\n".join(f"- {note}" for note in self.notes))
        return "\n".join(chunks)


def fetch_reference_bundle(
    repo: str,
    patterns: list[str] | None = None,
    *,
    ref: str = "",
    max_files: int = 8,
    max_chars: int = 60_000,
) -> ReferenceBundle:
    """按关键词挑文件并读取，用于“先看别人怎么实现”。"""
    name = parse_repo(repo)
    branch = ref or str(repo_info(name).get("branch") or "main")
    bundle = ReferenceBundle(repo=name, ref=branch)
    try:
        tree = repo_tree(name, branch, limit=8000)
    except ResearchError as exc:
        bundle.notes.append(str(exc))
        return bundle
    wanted = [str(item).lower() for item in (patterns or []) if str(item).strip()]
    candidates: list[str] = []
    for path in tree:
        lower = path.lower()
        if not lower.endswith((".java", ".kt", ".gradle", ".properties", ".toml", ".json", ".md")):
            continue
        if wanted and not any(token in lower for token in wanted):
            continue
        candidates.append(path)
    for path in candidates[:max_files]:
        try:
            text = repo_file(name, path, branch)
        except ResearchError as exc:
            bundle.notes.append(f"{path}：{exc}")
            continue
        bundle.files.append(ReferenceFile(repo=name, path=path, text=text[:max_chars], ref=branch))
    if not bundle.files and not bundle.notes:
        bundle.notes.append("没有匹配关键词的文件，请换更通用的词，例如 mixin、Villager、Menu。")
    return bundle
