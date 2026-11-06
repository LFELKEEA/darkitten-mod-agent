from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

Log = Callable[[str], None]

BUILD_SCRIPTS = ("build.gradle", "build.gradle.kts")
LIB_DIR = "libs"
MAX_SCAN_ENTRIES = 20_000
MAX_SCAN_DEPTH = 2
SKIP_DIRS = {
    ".git",
    ".gradle",
    ".idea",
    ".vscode",
    "build",
    "out",
    "run",
    "runs",
    "logs",
    "node_modules",
    "__pycache__",
    "reference",
}

_QUOTED = re.compile(r"""["']([^"'\r\n]+)["']""")
_LINE_COMMENT = re.compile(r"//[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SOURCE_SUFFIXES = ("-sources", "-javadoc", "-dev", "-api", "-slim")


@dataclass
class LocalJars:
    """一次本地依赖整理的结果。"""

    required: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    copied: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.copied)

    def summary(self) -> str:
        parts: list[str] = []
        if self.copied:
            parts.append(
                "已放入 libs：" + "、".join(f"{name}（来自 {source}）" for name, source in self.copied)
            )
        if self.present:
            parts.append("已在工程里：" + "、".join(self.present))
        if self.missing:
            parts.append(
                "没找到本地 jar："
                + "、".join(self.missing)
                + "。已搜索下载目录、桌面、工程目录和知识库来源目录"
            )
        if not parts:
            parts.append("没有声明本地 jar 依赖")
        return "；".join(parts)


def _log(log: Log | None, text: str) -> None:
    if log is None:
        return
    try:
        log(text)
    except Exception:
        pass


def _strip_comments(text: str) -> str:
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def jar_references(project_root: Path) -> list[str]:
    """从 build.gradle / build.gradle.kts 里找出 libs/ 下的 jar 相对路径。"""
    root = Path(project_root)
    found: list[str] = []
    seen: set[str] = set()
    for name in BUILD_SCRIPTS:
        path = root / name
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for value in _QUOTED.findall(_strip_comments(text)):
            item = value.strip().replace("\\", "/")
            if not item.lower().endswith(".jar"):
                continue
            if "libs/" not in item.lower():
                continue
            pure = item[2:] if item.startswith("./") else item
            if (
                not pure
                or pure.startswith("/")
                or ".." in Path(pure).parts
                or ":" in pure.split("/")[0]
            ):
                continue
            if pure.lower() in seen:
                continue
            seen.add(pure.lower())
            found.append(pure)
    return found


def _squash(name: str) -> str:
    return re.sub(r"\s+", "", Path(name).name).lower()


def match_score(expected: str, candidate: str) -> int:
    """给候选文件名打分，0 表示不匹配。

    实际下载的模组文件名经常带中文前缀或版本后缀，例如
    「[凡家物语：重生] mca-neoforge-7.7.36-beta.3+1.21.1.jar」，
    所以除了完全相等，也接受「期望文件名完整出现在候选名末尾」。
    """
    want = _squash(expected)
    have = _squash(candidate)
    if not want or not have or not have.endswith(".jar"):
        return 0
    if have == want:
        return 1000
    want_stem = want[:-4]
    have_stem = have[:-4]
    if have_stem == want_stem:
        return 900
    if not have_stem.endswith(want_stem):
        return 0
    # 不要把 -sources / -javadoc 之类的旁支文件当成主 jar
    for suffix in _SOURCE_SUFFIXES:
        if have_stem.endswith(suffix) and not want_stem.endswith(suffix):
            return 0
    return 700 - len(have_stem)


def downloads_dir() -> Path | None:
    if os.name == "nt":
        try:
            import winreg

            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                value, _ = winreg.QueryValueEx(
                    handle, "{374DE290-123F-4565-9164-39C4925E467B}"
                )
            folder = Path(os.path.expandvars(str(value)))
            if folder.is_dir():
                return folder
        except Exception:
            pass
    for name in ("Downloads", "下载"):
        folder = Path.home() / name
        if folder.is_dir():
            return folder
    return None


def drive_download_dirs() -> list[Path]:
    """其它盘符下的下载文件夹，兼容把「下载」目录挪到 D 盘的用户。"""
    if os.name != "nt":
        return []
    found: list[Path] = []
    user_name = Path.home().name
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        drive = Path(f"{letter}:\\")
        try:
            if not drive.is_dir():
                continue
        except OSError:
            continue
        for base in (drive, drive / user_name):
            for name in ("下载", "Downloads"):
                folder = base / name
                try:
                    if folder.is_dir():
                        found.append(folder)
                except OSError:
                    continue
    return found


def _add_dir(target: list[Path], seen: set[str], folder: Path | None) -> None:
    if folder is None:
        return
    try:
        path = Path(folder).expanduser()
        if not path.is_dir():
            return
        key = os.path.normcase(os.path.normpath(str(path)))
    except OSError:
        return
    if key in seen:
        return
    seen.add(key)
    target.append(path)


def search_dirs(project_root: Path, extra_dirs: Iterable[Path] | None = None) -> list[Path]:
    root = Path(project_root)
    dirs: list[Path] = []
    seen: set[str] = set()
    _add_dir(dirs, seen, root)
    _add_dir(dirs, seen, root.parent)
    for item in extra_dirs or ():
        _add_dir(dirs, seen, item)
    _add_dir(dirs, seen, downloads_dir())
    for folder in drive_download_dirs():
        _add_dir(dirs, seen, folder)
    home = Path.home()
    for name in ("Desktop", "桌面", "Documents", "文档"):
        _add_dir(dirs, seen, home / name)
    return dirs


def _iter_files(folder: Path, depth: int) -> Iterable[Path]:
    base = folder
    counter = 0
    for current, dirs, files in os.walk(base):
        try:
            level = len(Path(current).parts) - len(base.parts)
        except OSError:
            level = 0
        dirs[:] = [
            item
            for item in sorted(dirs)
            if not item.startswith(".") and item.lower() not in SKIP_DIRS
        ]
        if level >= depth:
            dirs[:] = []
        for name in files:
            counter += 1
            if counter > MAX_SCAN_ENTRIES:
                return
            yield Path(current) / name


def find_jar(expected: str, dirs: Iterable[Path]) -> Path | None:
    best: tuple[int, int, Path] | None = None
    for folder in dirs:
        depth = 1 if folder == Path.home() else MAX_SCAN_DEPTH
        try:
            for item in _iter_files(folder, depth):
                if not item.name.lower().endswith(".jar"):
                    continue
                score = match_score(expected, item.name)
                if score <= 0:
                    continue
                key = (score, -len(item.name), item)
                if best is None or key[:2] > best[:2]:
                    best = key
        except OSError:
            continue
    return best[2] if best is not None else None


def ensure_local_jars(
    project_root: Path,
    *,
    log: Log | None = None,
    extra_dirs: Iterable[Path] | None = None,
) -> LocalJars:
    """把 build.gradle 里引用的 libs/*.jar 自动补进工程。

    只复制到工程自己的 libs/ 目录，不改动 build.gradle，也不下载文件。
    """
    root = Path(project_root)
    result = LocalJars()
    references = jar_references(root)
    result.required = list(references)
    if not references:
        return result
    directories = search_dirs(root, extra_dirs)
    for relative in references:
        target = root / relative
        if target.is_file():
            result.present.append(relative)
            _log(log, f"[准备] 本地依赖已就绪：{relative}")
            continue
        source = find_jar(Path(relative).name, directories)
        if source is None:
            result.missing.append(relative)
            _log(log, f"[准备] 没有找到 {Path(relative).name}，请把 jar 放进下载文件夹后重新点「一键准备」")
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        result.copied.append((relative, str(source)))
        _log(log, f"[准备] 已自动放入 {relative}（来自 {source}）")
    return result


def mentioned_jar(text: str) -> str:
    """从 Gradle 报错里找出缺失的 libs/ jar 文件名，找不到返回空字符串。"""
    lowered = (text or "").lower()
    if "libs/" not in lowered and "libs\\" not in lowered:
        return ""
    for pattern in (
        r"libs[\\/]([^\\/\s'\"]+\.jar)",
        r"([^\\/\s'\"]+\.jar)",
    ):
        match = re.search(pattern, text or "", re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def is_missing_jar_error(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    # files()/fileTree 匹配不到任何文件时 Gradle 只报这一句，不带具体路径
    if "could not find files for the given pattern" in lowered:
        return True
    if not mentioned_jar(text):
        return False
    markers = (
        "does not exist",
        "no such file",
        "cannot find",
        "failed to resolve",
        "could not resolve",
    )
    return any(marker in lowered for marker in markers)
