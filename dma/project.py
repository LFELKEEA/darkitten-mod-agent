from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import paths

META_DIR = ".dma"
META_FILE = "project.json"
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
    META_DIR,
}
TEXT_SUFFIXES = {
    ".java", ".kt", ".kts", ".gradle", ".properties", ".json", ".json5",
    ".toml", ".yml", ".yaml", ".md", ".txt", ".xml", ".mcmeta", ".lang",
    ".cfg", ".conf", ".sh", ".bat", ".cmd", ".ps1", ".py", ".js", ".ts",
    ".css", ".html", ".fsh", ".vsh", ".glsl", ".csv",
}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_WRITE_CHARS = 2_000_000

# 模型偶尔会把 Markdown 代码围栏一起写进源文件，导致 javac 报「非法字符: '`'」。
# 只处理整份内容被围栏包住的情况，正文中间出现的反引号保持原样。
_FENCE_LINE = re.compile(r"^\s*```[A-Za-z0-9_+.#-]*\s*$")
FENCE_STRIP_SUFFIXES = {
    ".java", ".kt", ".kts", ".gradle", ".properties", ".json", ".json5",
    ".toml", ".yml", ".yaml", ".xml", ".mcmeta", ".lang", ".cfg", ".conf",
    ".py", ".js", ".ts", ".css", ".html", ".sh", ".bat", ".cmd", ".ps1",
}


def strip_wrapping_fences(relative: str, content: str) -> str:
    """去掉包住整份代码文件的 ``` 围栏，避免写进源文件后编译失败。"""
    if Path(relative).suffix.lower() not in FENCE_STRIP_SUFFIXES or "```" not in content:
        return content
    lines = content.splitlines()
    if not lines:
        return content
    changed = False
    if _FENCE_LINE.match(lines[0]):
        lines = lines[1:]
        changed = True
    while lines and _FENCE_LINE.match(lines[-1]):
        lines.pop()
        changed = True
    if not changed:
        return content
    return "\n".join(lines).rstrip("\n") + "\n"


class ProjectError(Exception):
    pass


@dataclass
class ProjectMeta:
    name: str = ""
    mod_id: str = ""
    mc_version: str = ""
    loader: str = "Forge"
    runtime: str = "双端"
    dependencies: str = ""
    task_type: str = "创建全新模组"
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""

    def complete(self) -> bool:
        return bool(self.mod_id.strip() and self.mc_version.strip() and self.loader.strip())

    def missing(self) -> list[str]:
        items: list[str] = []
        if not self.mc_version.strip():
            items.append("MC 版本")
        if not self.loader.strip():
            items.append("加载器")
        if not self.mod_id.strip():
            items.append("Mod ID")
        return items


@dataclass
class Project:
    root: Path
    meta: ProjectMeta = field(default_factory=ProjectMeta)

    def save(self) -> None:
        self.meta.updated_at = _now()
        folder = self.root / META_DIR
        folder.mkdir(parents=True, exist_ok=True)
        (folder / META_FILE).write_text(
            json.dumps(asdict(self.meta), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def label(self) -> str:
        name = self.meta.name.strip() or self.meta.mod_id.strip() or self.root.name
        version = self.meta.mc_version.strip()
        return f"{name} · {self.meta.loader} {version}".strip(" ·")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def validate_mod_id(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        raise ProjectError("Mod ID 不能为空。")
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", text):
        raise ProjectError("Mod ID 只能使用小写字母、数字和下划线，必须以字母开头，长度 2-64。")
    return text


def safe_path(root: Path, relative: str) -> Path:
    text = str(relative or "").replace("\\", "/").strip()
    if not text:
        raise ProjectError("文件路径不能为空。")
    pure = Path(text)
    if pure.is_absolute() or re.match(r"^[A-Za-z]:", text):
        raise ProjectError("只允许写入工程目录内的相对路径。")
    if ".." in pure.parts:
        raise ProjectError("文件路径不能包含 ..。")
    root_resolved = Path(root).resolve()
    target = (root_resolved / pure).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise ProjectError("文件路径超出了工程目录。")
    return target


def create_project(
    root: Path,
    *,
    name: str,
    mod_id: str,
    mc_version: str,
    loader: str,
    runtime: str = "双端",
    dependencies: str = "",
    task_type: str = "创建全新模组",
    notes: str = "",
    package: str = "",
) -> Project:
    mod_id = validate_mod_id(mod_id)
    mc_version = (mc_version or "").strip()
    loader = (loader or "").strip()
    if not mc_version:
        raise ProjectError("请填写 MC 版本。")
    if loader not in {"Forge", "Fabric", "NeoForge", "Architectury"}:
        raise ProjectError("加载器只能是 Forge、Fabric、NeoForge 或 Architectury。")
    base = Path(root).expanduser().resolve()
    target = base / mod_id
    target.mkdir(parents=True, exist_ok=True)
    meta = ProjectMeta(
        name=(name or mod_id).strip(),
        mod_id=mod_id,
        mc_version=mc_version,
        loader=loader,
        runtime=(runtime or "双端").strip(),
        dependencies=dependencies.strip(),
        task_type=task_type,
        notes=notes.strip(),
        created_at=_now(),
        updated_at=_now(),
    )
    project = Project(root=target, meta=meta)
    project.save()
    scaffolded = False
    scaffold_error = ""
    try:
        from .mcdev import templates as mcdev_templates

        if mcdev_templates.supports_scaffold(loader, mc_version):
            mcdev_templates.create_scaffold(
                loader,
                target,
                mod_id=mod_id,
                mod_name=meta.name,
                package=package,
                mc_version=mc_version,
            )
            scaffolded = True
    except Exception as exc:  # noqa: BLE001 - 骨架失败也要让工程能打开
        scaffold_error = str(exc)
    readme = target / "README.md"
    if not readme.exists():
        note = (
            "已生成可编译骨架。\n"
            if scaffolded
            else (
                "内置骨架目前覆盖 Minecraft 1.20.1 的 Forge / Fabric / NeoForge。"
                "当前版本或加载器没有内置骨架，助手会按目标版本核对官方模板后生成构建脚本。\n"
            )
        )
        if scaffold_error:
            note = f"骨架生成遇到问题：{scaffold_error}\n助手会改为手工生成构建脚本。\n"
        readme.write_text(
            f"# {meta.name}\n\n"
            f"- Minecraft: {mc_version}\n"
            f"- Loader: {loader}\n"
            f"- Mod ID: `{mod_id}`\n\n"
            f"{note}\n"
            "由 Darkitten Mod Agent 创建。\n",
            encoding="utf-8",
        )
    remember_project(project)
    return project


def load_project(root: Path) -> Project:
    base = Path(root).expanduser().resolve()
    meta_path = base / META_DIR / META_FILE
    meta = ProjectMeta()
    if meta_path.is_file():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = {}
        if isinstance(data, dict):
            allowed = set(ProjectMeta.__dataclass_fields__)
            meta = ProjectMeta(**{k: v for k, v in data.items() if k in allowed})
    project = Project(root=base, meta=meta)
    if not meta.mod_id:
        _infer_meta(project)
    return project


def _infer_meta(project: Project) -> None:
    root = project.root
    meta = project.meta
    meta.name = meta.name or root.name
    if not meta.mod_id:
        meta.mod_id = re.sub(r"[^a-z0-9_]", "_", root.name.lower()).strip("_") or "mod"
    fabric = root / "src/main/resources/fabric.mod.json"
    if fabric.is_file():
        meta.loader = "Fabric"
        try:
            data = json.loads(fabric.read_text(encoding="utf-8-sig"))
            meta.mod_id = str(data.get("id") or meta.mod_id)
            meta.name = str(data.get("name") or meta.name)
            depends = data.get("depends") if isinstance(data.get("depends"), dict) else {}
            meta.mc_version = str(depends.get("minecraft") or meta.mc_version)
        except (OSError, ValueError):
            pass
    modern_toml = root / "src/main/resources/META-INF/neoforge.mods.toml"
    legacy_toml = root / "src/main/resources/META-INF/mods.toml"
    for path in (modern_toml, legacy_toml):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8-sig")
                id_match = re.search(r'modId\s*=\s*"([^"]+)"', text)
                name_match = re.search(r'displayName\s*=\s*"([^"]+)"', text)
                version_match = re.search(r'versionRange\s*=\s*"\[?([0-9][^",\]]*)', text)
                # 模板里的 ${mod_id} 是 Gradle 占位符，构建前读出来不是真实值。
                if id_match and "${" not in id_match.group(1):
                    meta.mod_id = id_match.group(1)
                if name_match and "${" not in name_match.group(1):
                    meta.name = name_match.group(1)
                if version_match and not meta.mc_version:
                    meta.mc_version = version_match.group(1)
                # mods.toml 同时被 Forge 和 NeoForge 使用，靠依赖里的命名空间区分。
                if path == modern_toml or "net.neoforged" in text:
                    meta.loader = "NeoForge"
                else:
                    meta.loader = "Forge"
            except OSError:
                pass
            break
    gradle = root / "gradle.properties"
    if gradle.is_file() and not meta.mc_version:
        try:
            text = gradle.read_text(encoding="utf-8-sig")
            match = re.search(r"minecraft_version\s*=\s*([^\s#]+)", text)
            if match:
                meta.mc_version = match.group(1)
        except OSError:
            pass


def file_tree(root: Path, max_entries: int = 240) -> list[str]:
    base = Path(root)
    if not base.is_dir():
        return []
    result: list[str] = []
    for current, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            path = Path(current) / name
            try:
                relative = path.relative_to(base).as_posix()
            except ValueError:
                continue
            result.append(relative)
            if len(result) >= max_entries:
                return result
    return result


def is_text_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        return True
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ogg", ".wav", ".mp3", ".jar", ".zip", ".class", ".ttf", ".otf"}:
        return False
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return False
        chunk = path.read_bytes()[:1024]
        return b"\x00" not in chunk
    except OSError:
        return False


def read_text(root: Path, relative: str, max_chars: int = 16000) -> str:
    path = safe_path(root, relative)
    if not path.is_file():
        raise ProjectError(f"文件不存在：{relative}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ProjectError(f"文件太大，拒绝直接读取：{relative}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n…（已截断，原文件 {len(text)} 字符）"
    return text


def search_text(root: Path, query: str, max_results: int = 40) -> list[str]:
    needle = str(query or "").strip().lower()
    if len(needle) < 2:
        return []
    base = Path(root)
    hits: list[str] = []
    for relative in file_tree(base, max_entries=5000):
        path = base / relative
        if not is_text_file(path):
            continue
        try:
            if path.stat().st_size > 512 * 1024:
                continue
            for number, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if needle in line.lower():
                    hits.append(f"{relative}:{number}: {line.strip()[:220]}")
                    break
        except OSError:
            continue
        if len(hits) >= max_results:
            break
    return hits


def _backup_path(root: Path, relative: Path) -> Path:
    slug = re.sub(r"[^A-Za-z0-9_.-]", "_", root.name)[:40] or "project"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return paths.backups_dir() / slug / stamp / relative


def repair_fences(root: Path) -> list[str]:
    """编译前扫描工程，修掉整份被代码围栏包住的源文件。返回改动过的相对路径。"""
    base = Path(root).resolve()
    fixed: list[str] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if any(part in SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix.lower() not in FENCE_STRIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        cleaned = strip_wrapping_fences(relative.name, text)
        if cleaned == text:
            continue
        try:
            write_text(base, relative.as_posix(), cleaned)
        except (OSError, ProjectError):
            continue
        fixed.append(relative.as_posix())
    return fixed


def repair_build_scripts(root: Path) -> list[str]:
    """修掉能自动补的构建脚本缺陷，返回改动说明。

    NeoForge 的 ModDevGradle 必须用 mods { ... } 声明主源码集，否则编译能过，
    但开发运行拿不到 modFolders，Mixin 会在启动时报「指定的 mixin 未找到」。
    这类问题不该让用户自己猜，编译前直接补齐。
    """
    base = Path(root).resolve()
    changed: list[str] = []
    for name in ("build.gradle", "build.gradle.kts"):
        path = base / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        original = text
        if name == "build.gradle":
            text = _ensure_neoforge_mods(base, text)
            text = _ensure_data_run(base, text)
            text = _ensure_filtering_charset(text)
        if text != original:
            try:
                write_text(base, name, text)
            except (OSError, ProjectError):
                continue
            changed.append(name)
    return changed


def _gradle_mod_id(root: Path) -> str:
    try:
        text = (root / "gradle.properties").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    match = re.search(r"(?m)^\s*mod_id\s*=\s*([A-Za-z0-9_.-]+)", text)
    if match:
        return match.group(1).strip()
    return re.sub(r"[^a-z0-9_]", "_", root.name.lower()) or "mod"


def _ensure_neoforge_mods(root: Path, text: str) -> str:
    if "net.neoforged.moddev" not in text and not re.search(
        r"(?m)^\s*neoForge\s*\{", text
    ):
        return text
    if re.search(r"(?m)^\s*mods\s*\{", text):
        return text
    neo = re.search(r"(?m)^([ \t]*)neoForge\s*\{", text)
    if neo is None:
        return text
    runs = re.search(r"(?m)^([ \t]*)runs\s*\{", text[neo.end():])
    if runs is None:
        return text
    indent = runs.group(1)
    inner = indent + "    "
    mod_id = _gradle_mod_id(root)
    block = (
        f"{indent}mods {{\n"
        f'{inner}"{mod_id}" {{\n'
        f"{inner}    sourceSet sourceSets.main\n"
        f"{inner}}}\n"
        f"{indent}}}\n\n"
    )
    insert_at = neo.end() + runs.start()
    return text[:insert_at] + block + text[insert_at:]


def _ensure_data_run(root: Path, text: str) -> str:
    """补齐 NeoForge 数据生成运行。

    ModDevGradle 的运行类型只有 client / server / data / gameTestServer / junit，
    写成 clientData() 会让 :prepareDataRun 直接失败（Trying to prepare unknown
    run: clientData）。数据生成还必须带 --mod / --output / --existing，否则
    即使任务跑起来也不会把文件写到 src/generated/resources。
    """
    match = re.search(r"(?ms)^([ \t]*)data\s*\{.*?^\1\}", text)
    if match is None:
        return text
    indent = match.group(1)
    inner = indent + "    "
    block = match.group(0)
    updated = re.sub(r"\b(?:clientData|serverData)\s*\(\s*\)", "data()", block)
    if not re.search(r"\bdata\s*\(\s*\)", updated) and not re.search(
        r"\btype\s*=", updated
    ):
        updated = updated.replace(f"{indent}}}", f"{inner}data()\n{indent}}}", 1)
    if "programArguments" not in updated:
        mod_id = _gradle_mod_id(root)
        args = (
            f"{inner}programArguments.addAll '--mod', '{mod_id}', '--all', "
            f"'--output', file('src/generated/resources/').getAbsolutePath(), "
            f"'--existing', file('src/main/resources/').getAbsolutePath()\n"
        )
        updated = updated.replace(f"{indent}}}", args + f"{indent}}}", 1)
    if updated == block:
        return text
    return text[: match.start()] + updated + text[match.end():]


def _ensure_filtering_charset(text: str) -> str:
    if "ProcessResources" not in text or "expand" not in text:
        return text
    if "filteringCharset" in text:
        return text
    match = re.search(
        r"(?m)^([ \t]*)tasks\.withType\(ProcessResources\)[^\n]*\{", text
    )
    if match is None:
        return text
    indent = match.group(1) + "    "
    return text[: match.end()] + f"\n{indent}filteringCharset = 'UTF-8'" + text[match.end():]


def write_text(root: Path, relative: str, content: str) -> Path:
    content = strip_wrapping_fences(relative, content)
    if len(content) > MAX_WRITE_CHARS:
        raise ProjectError("单个文件超过 2,000,000 字符，已拒绝写入。")
    target = safe_path(root, relative)
    if target.exists():
        if target.is_dir():
            raise ProjectError(f"目标是一个目录：{relative}")
        backup = _backup_path(Path(root), Path(relative))
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(target, backup)
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    return target


def write_bytes(root: Path, relative: str, data: bytes) -> Path:
    """写入纹理、图标这类二进制资源，覆盖前同样备份。"""
    if len(data) > MAX_WRITE_CHARS:
        raise ProjectError("单个文件超过 2,000,000 字节，已拒绝写入。")
    target = safe_path(root, relative)
    if target.exists():
        if target.is_dir():
            raise ProjectError(f"目标是一个目录：{relative}")
        backup = _backup_path(Path(root), Path(relative))
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(target, backup)
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def remember_project(project: Project) -> None:
    path = paths.projects_path()
    items: list[dict[str, Any]] = []
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                items = [item for item in data if isinstance(item, dict)]
        except (OSError, ValueError):
            items = []
    entry = {
        "root": str(project.root),
        "label": project.label(),
        "updated_at": _now(),
    }
    items = [item for item in items if item.get("root") != entry["root"]]
    items.insert(0, entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(items[:30], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def recent_projects() -> list[dict[str, Any]]:
    path = paths.projects_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def forget_project(root: Path) -> None:
    """把工程从「最近」列表里移除。"""
    path = paths.projects_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return
    if not isinstance(data, list):
        return
    target = _norm_path(root)
    items = [
        item
        for item in data
        if not (isinstance(item, dict) and _norm_path(item.get("root")) == target)
    ]
    path.write_text(
        json.dumps(items[:30], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _norm_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return os.path.normcase(os.path.normpath(str(Path(text).expanduser().resolve())))
    except OSError:
        return os.path.normcase(os.path.normpath(text))


def _check_deletable(root: Path) -> Path:
    try:
        target = Path(root).expanduser().resolve()
    except OSError as exc:
        raise ProjectError(f"无法解析工程目录：{exc}") from exc
    if not target.is_dir():
        raise ProjectError("工程目录不存在，可能已经被移动或删除。")
    if target == Path(target.anchor):
        raise ProjectError("不能删除磁盘根目录。")
    protected = (
        Path.home().resolve(),
        Path.cwd().resolve(),
        paths.app_dir().resolve(),
        paths.data_dir().resolve(),
    )
    for item in protected:
        if target == item or item.is_relative_to(target):
            raise ProjectError("这个目录包含程序或用户文件，已拒绝删除，请手动处理。")
    return target


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", ctypes.c_uint),
        ("pFrom", ctypes.c_wchar_p),
        ("pTo", ctypes.c_wchar_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", ctypes.c_int),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_wchar_p),
    ]


def _recycle_windows(target: Path) -> None:
    if sys.platform != "win32":
        raise ProjectError("当前系统不支持 Windows 回收站。")
    flags = 0x0004 | 0x0010 | 0x0040 | 0x0400  # 静默 + 不确认 + 可撤销 + 不报错弹窗
    source = ctypes.create_unicode_buffer(str(target) + "\0\0")
    operation = _SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=3,  # FO_DELETE
        pFrom=ctypes.cast(source, ctypes.c_wchar_p),
        pTo=None,
        fFlags=flags,
        fAnyOperationsAborted=0,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    except OSError as exc:
        raise ProjectError(f"调用回收站失败：{exc}") from exc
    if result != 0 or operation.fAnyOperationsAborted:
        raise ProjectError(
            "移到回收站失败。请关闭正在占用该工程目录的程序（IDE、资源管理器、编译任务）后再试。"
        )


def move_to_trash(path: Path, *, recycle: bool = True) -> Path | None:
    """把目录移到系统回收站；非 Windows 或指定 recycle=False 时移到 data/trash。"""
    try:
        target = Path(path).expanduser().resolve()
    except OSError as exc:
        raise ProjectError(f"无法解析目录：{exc}") from exc
    if not target.exists():
        raise ProjectError("目录不存在。")
    if recycle and sys.platform == "win32":
        _recycle_windows(target)
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    trash = paths.data_dir() / "trash"
    destination = trash / f"{target.name}-{stamp}"
    counter = 1
    while destination.exists():
        destination = trash / f"{target.name}-{stamp}-{counter}"
        counter += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(target), str(destination))
    except OSError as exc:
        raise ProjectError(f"移动工程到回收目录失败：{exc}") from exc
    return destination


def delete_project(root: Path, *, recycle: bool = True) -> Path | None:
    """删除工程目录并清理最近记录与会话记录。

    默认走系统回收站，可以从回收站还原；返回 None 表示已进回收站，
    返回路径表示移到了 data/trash 下的备用目录。
    """
    target = _check_deletable(root)
    destination = move_to_trash(target, recycle=recycle)
    forget_project(target)
    from . import sessions

    sessions.clear_history(target)
    return destination


def project_context(project: Project, max_files: int = 180) -> str:
    tree = file_tree(project.root, max_entries=max_files)
    meta = project.meta
    lines = [
        f"工程目录：{project.root}",
        f"模组名称：{meta.name or '未填写'}",
        f"Mod ID：{meta.mod_id or '未填写'}",
        f"MC 版本：{meta.mc_version or '未填写'}",
        f"加载器：{meta.loader or '未填写'}",
        f"运行端：{meta.runtime or '双端'}",
        f"依赖：{meta.dependencies or '无'}",
        f"任务类型：{meta.task_type or '创建全新模组'}",
        "",
        "工程文件（已跳过 build/.gradle/run 等目录）：",
    ]
    if tree:
        lines.extend(f"- {item}" for item in tree)
    else:
        lines.append("- （空工程）")
    return "\n".join(lines)
