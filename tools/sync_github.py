#!/usr/bin/env python3
"""Publish the workshop source tree to GitHub.

Why a script instead of a manual push:

* the workspace is not a git repository;
* ``github.com`` is unreachable on some campus networks unless we pin a
  working IP through ``http.curloptResolve``;
* Windows Schannel fails the certificate revocation check on those links, so
  the repository uses a process-local ``http.sslverify=false`` (never global);
* every push must scan staged files for API keys and runtime data first.

Usage::

    python tools/sync_github.py                 # sync current version
    python tools/sync_github.py --dry-run       # scan + show what would change
    python tools/sync_github.py --no-push       # commit locally only
    python tools/sync_github.py -m "自定义说明"
"""

from __future__ import annotations

import argparse
import re
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REMOTE = "https://github.com/LFELKEEA/darkitten-mod-agent.git"
BRANCH = "main"
GIT_IDENTITY = ("Darkitten Source Sync", "source@local")
FALLBACK_IPS = ("20.27.177.113", "140.82.112.3", "140.82.114.4", "20.200.245.247")

BLOCKED_NAMES = {"config.json", "social.json"}
BLOCKED_DIRS = {
    ".git", "__pycache__", "build", "dist", "data", "logs", "skins",
    "minecraft", ".wrangler", ".pytest_cache", ".writing",
    "reports", "qa",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".yml", ".yaml", ".toml", ".ini",
    ".cfg", ".conf", ".gradle", ".properties", ".xml", ".html", ".css",
    ".js", ".ts", ".java", ".kt", ".kts", ".sh", ".bat", ".spec", ".gitignore",
}
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|event[_-]?secret|client[_-]?secret|"
        r"secret[_-]?key)\b\s*[:=]\s*[\"'][A-Za-z0-9_\-+/=]{16,}[\"']"
    ),
)


def _git(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    command = ["git", "-c", "http.sslverify=false", *args]
    return subprocess.run(
        command,
        cwd=str(ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )


def _checked(*args: str, timeout: int = 300) -> str:
    result = _git(*args, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} 失败：{(result.stderr or result.stdout).strip()}"
        )
    return (result.stdout or "").strip()


def _resolve_args() -> list[str]:
    """Pin github.com to a reachable IP when local DNS is poisoned."""
    try:
        infos = socket.getaddrinfo("github.com", 443, socket.AF_INET)
        addresses = {info[4][0] for info in infos}
    except OSError:
        addresses = set()
    if addresses and not any(item.startswith("57.155.") for item in addresses):
        return []
    for address in FALLBACK_IPS:
        try:
            with socket.create_connection((address, 443), timeout=4):
                return ["-c", f"http.curloptResolve=github.com:443:{address}"]
        except OSError:
            continue
    return []


def _ensure_repo() -> None:
    if not (ROOT / ".git").is_dir():
        _checked("init", "-b", BRANCH)
    remote = _git("remote", "get-url", "origin")
    if remote.returncode != 0:
        _checked("remote", "add", "origin", REMOTE)
    elif REMOTE not in (remote.stdout or ""):
        _checked("remote", "set-url", "origin", REMOTE)
    current = _git("rev-parse", "--abbrev-ref", "HEAD")
    if current.returncode != 0 or not (current.stdout or "").strip():
        _checked("checkout", "-b", BRANCH)


def _sync_remote() -> None:
    """Fetch origin/main and align the index without touching the worktree."""
    resolve = _resolve_args()
    fetch = _git("fetch", *resolve, "origin", BRANCH)
    if fetch.returncode != 0:
        # The first run may find an empty repository.
        if "couldn't find remote ref" in (fetch.stderr or "").lower():
            return
        raise RuntimeError((fetch.stderr or fetch.stdout).strip())
    head = _git("rev-parse", "--verify", "HEAD")
    if head.returncode != 0:
        _checked("reset", "--mixed", "FETCH_HEAD")
        return
    # This repository is written only by the sync script; aligning to the
    # remote prevents non-fast-forward pushes after an external edit.
    _checked("reset", "--mixed", "FETCH_HEAD")


def _blocked(path: str) -> bool:
    parts = set(Path(path).parts)
    if parts & BLOCKED_DIRS:
        return True
    return Path(path).name in BLOCKED_NAMES


def _scan_secrets(paths: list[str]) -> list[str]:
    problems: list[str] = []
    for relative in paths:
        if _blocked(relative):
            problems.append(f"{relative}：禁止提交的本地配置/运行目录")
            continue
        path = ROOT / relative
        if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {".gitignore"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for pattern in SECRET_PATTERNS:
            match = pattern.search(text)
            if match:
                problems.append(f"{relative}：疑似密钥 {match.group(0)[:12]}…")
                break
    return problems


def _version() -> str:
    try:
        from dma.version import APP_VERSION

        return str(APP_VERSION)
    except Exception:  # noqa: BLE001 - 版本号缺失不阻断同步
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description="同步工坊源码到 GitHub")
    parser.add_argument("-m", "--message", default="", help="提交说明")
    parser.add_argument("--dry-run", action="store_true", help="只检查不提交")
    parser.add_argument("--no-push", action="store_true", help="只提交到本地")
    parser.add_argument("--quiet", action="store_true", help="减少输出")
    args = parser.parse_args()

    def say(message: str) -> None:
        if not args.quiet:
            print(message, flush=True)

    _ensure_repo()
    _sync_remote()
    _checked("add", "-A")
    staged = [line for line in _checked("diff", "--cached", "--name-only").splitlines() if line]
    if not staged:
        say("源码已是远端最新，没有需要同步的改动。")
        return 0

    problems = _scan_secrets(staged)
    if problems:
        print("同步已中止：检测到不应提交的内容。", file=sys.stderr)
        for item in problems:
            print(f"  - {item}", file=sys.stderr)
        print("请把这些文件加入 .gitignore 或从暂存区移除后重试。", file=sys.stderr)
        return 2

    say(f"待同步文件：{len(staged)} 个")
    if args.dry_run:
        for item in staged[:40]:
            say(f"  {item}")
        if len(staged) > 40:
            say(f"  …另有 {len(staged) - 40} 个")
        return 0

    version = _version()
    message = args.message or f"source: 同步 {version} 源码"
    commit = _git(
        "-c", f"user.name={GIT_IDENTITY[0]}",
        "-c", f"user.email={GIT_IDENTITY[1]}",
        "commit", "-m", message,
    )
    if commit.returncode != 0:
        raise RuntimeError((commit.stderr or commit.stdout).strip())
    sha = _checked("rev-parse", "--short", "HEAD")
    say(f"已提交 {sha}：{message}")
    if args.no_push:
        return 0

    resolve = _resolve_args()
    push = _git("push", *resolve, "origin", f"HEAD:{BRANCH}")
    if push.returncode != 0:
        raise RuntimeError((push.stderr or push.stdout).strip())
    say(f"已推送：https://github.com/LFELKEEA/darkitten-mod-agent/commit/{sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - 同步失败要给出可读原因
        print(f"同步失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
