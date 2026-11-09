"""规格驱动生成器的桥接层。

把内置的 studio_engine（原 mcmod-studio）接到 Darkitten Mod Agent：
界面和聊天 Agent 只需要关心“一句话构思 + 加载器 + MC 版本”，
这里负责生成规格、落地工程、审计、构建、验证和打包。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from . import paths, project as project_mod
from .studio_engine import audit as audit_mod
from .studio_engine import build as build_mod
from .studio_engine import package as package_mod
from .studio_engine import scaffold as scaffold_mod
from .studio_engine import spec as spec_mod
from .studio_engine import verify as verify_mod
from .studio_engine import versions as versions_mod
from .studio_engine.util import McmodError

Progress = Callable[[str], None]

LOADER_TO_ENGINE = {
    "forge": "forge",
    "neoforge": "neoforge",
    "neo": "neoforge",
    "fabric": "fabric",
}
ENGINE_TO_LOADER = {
    "forge": "Forge",
    "neoforge": "NeoForge",
    "fabric": "Fabric",
}
APP_LOADERS = tuple(ENGINE_TO_LOADER.values())
SCOPES = (
    ("small", "小型：12 个内容节点，单条短进度线"),
    ("medium", "中型：22 个内容节点，完整前期到中期"),
    ("large", "大型：40 个内容节点，多阶段与终局"),
    ("epic", "史诗：64 个内容节点，接近完整模组"),
)
SCOPE_KEYS = tuple(item[0] for item in SCOPES)
MC_VERSIONS = [
    "1.20.1",
    "1.21.1",
    "1.21.4",
    "1.21.8",
    "26.3",
    "1.20.4",
    "1.19.2",
    "1.18.2",
    "1.16.5",
]


class StudioError(RuntimeError):
    """生成器可读失败。"""


def normalize_loader(value: str) -> str:
    key = str(value or "").strip().lower()
    engine = LOADER_TO_ENGINE.get(key)
    if engine is None:
        raise StudioError(
            f"不支持的加载器：{value or '（空）'}。可选：Forge、NeoForge、Fabric。"
            "Architectury 需要手工工程，规格生成器不直接产出。"
        )
    return engine


def loader_label(engine_loader: str) -> str:
    return ENGINE_TO_LOADER.get(str(engine_loader).lower(), str(engine_loader))


def normalize_scope(value: str) -> str:
    scope = str(value or "medium").strip().lower()
    if scope not in SCOPE_KEYS:
        raise StudioError(f"不支持的规模：{value}。可选：{', '.join(SCOPE_KEYS)}。")
    return scope


def normalize_modid(value: str, idea: str = "") -> str:
    text = re.sub(r"[^a-z0-9_]", "_", str(value or "").strip().lower()).strip("_")
    if not text:
        text = re.sub(r"[^a-z0-9_]", "_", (idea or "")[:24].lower()).strip("_")
    if not text:
        text = "generated_mod"
    if not re.match(r"^[a-z]", text):
        text = "mod_" + text
    return project_mod.validate_mod_id(text[:64])


def plan(
    idea: str,
    loader: str,
    mc: str,
    *,
    name: str = "",
    modid: str = "",
    package: str = "",
    scope: str = "medium",
    author: str = "",
    license: str = "MIT",
) -> dict[str, Any]:
    """只生成机器可读规格，不落盘。"""
    idea = str(idea or "").strip()
    if len(idea) < 2:
        raise StudioError("请至少写一句模组构思。")
    engine_loader = normalize_loader(loader)
    mc = str(mc or "").strip()
    if not mc:
        raise StudioError("请选择 MC 版本。")
    scope = normalize_scope(scope)
    modid = normalize_modid(modid, idea) if (modid or name) else ""
    spec = spec_mod.build_spec(
        idea,
        engine_loader,
        mc,
        name=name.strip() or None,
        modid=modid or None,
        package=package.strip() or None,
        scope=scope,
        author=(author or "Darkitten Mod Agent").strip(),
        license=(license or "MIT").strip(),
    )
    spec["target"]["loader_label"] = loader_label(engine_loader)
    return spec


def spec_markdown(spec: dict[str, Any]) -> str:
    return spec_mod.spec_to_markdown(spec)


def _install_progress(progress: Progress | None) -> None:
    if progress is None:
        return

    def sink(message: str) -> None:
        text = str(message or "").rstrip()
        if text:
            progress(text)

    # 引擎里的 log 是直接导入的函数，两个入口都要替换。
    from .studio_engine import build as engine_build
    from .studio_engine import recon as engine_recon
    from .studio_engine import util as engine_util

    engine_util.log = sink  # type: ignore[assignment]
    engine_build.log = sink  # type: ignore[assignment]
    engine_recon.log = sink  # type: ignore[assignment]


def _emit(progress: Progress | None, message: str) -> None:
    if progress is not None:
        progress(message)


def resolve_profile(loader: str, mc: str, *, progress: Progress | None = None):
    engine_loader = normalize_loader(loader)
    mc = str(mc or "").strip()
    _emit(progress, f"正在解析 {loader_label(engine_loader)} {mc} 的加载器版本…")
    try:
        profile = versions_mod.resolve(engine_loader, mc)
    except McmodError as exc:
        raise StudioError(f"版本解析失败：{exc}") from exc
    if not profile.supported:
        hint = ""
        try:
            suggestion = versions_mod.suggestions(engine_loader, mc)
            nearby = "、".join(suggestion.get("mc", [])[:6])
            if nearby:
                hint = f" 邻近可用版本：{nearby}。"
        except Exception:  # noqa: BLE001 - 建议不是必需信息
            pass
        raise StudioError(
            f"{loader_label(engine_loader)} {mc} 不在已验证范围（能力等级 {profile.tier}）。"
            f"请换版本或加载器。{hint}"
        )
    _emit(
        progress,
        f"版本已解析：{loader_label(profile.loader)} {profile.loader_version} / "
        f"Java {profile.java_major} / 能力 {profile.tier}",
    )
    return profile


def _ensure_fabric_mappings(profile, spec: dict[str, Any], progress: Progress | None) -> None:
    """Fabric 的高级 Java（实体/机器/粒子/网络/流体）按 Mojang 映射生成。

    Yarn 映射下这些类名对不上，生成器会整体跳过；用户要的是完整模组，
    所以在 spec 确实包含高级内容时自动切换官方映射，而不是悄悄降级。
    """
    if profile.loader != "fabric":
        return
    if (profile.mappings or "official") in {"", "official"}:
        return
    from .studio_engine import systems as engine_systems

    try:
        needs_advanced = engine_systems.content_plan(spec).any_java
    except Exception:  # noqa: BLE001 - 规格结构异常时保持默认映射
        needs_advanced = False
    if not needs_advanced:
        return
    if str(profile.mc).startswith("26."):
        return
    _emit(
        progress,
        "Fabric 工程包含实体/机器/粒子/网络/流体，已自动切换到官方 Mojang 映射以生成完整 Java。",
    )
    profile.mappings = "official"


def _mark_project(root: Path, spec: dict[str, Any], profile) -> project_mod.Project:
    ident = spec["identity"]
    project = project_mod.load_project(root)
    project.meta.name = ident.get("name_zh") or root.name
    project.meta.mod_id = ident.get("modid") or root.name
    project.meta.mc_version = profile.mc
    project.meta.loader = loader_label(profile.loader)
    project.meta.runtime = "双端"
    project.meta.task_type = "创建全新模组"
    project.meta.notes = (
        f"规格驱动生成：{len(spec.get('content', []))} 个内容节点，主题 "
        + "、".join(spec.get("themes", []))
    )
    project.save()
    project_mod.remember_project(project)
    return project


def generate(
    *,
    idea: str,
    loader: str,
    mc: str,
    dest_root: str | Path | None = None,
    name: str = "",
    modid: str = "",
    package: str = "",
    scope: str = "medium",
    author: str = "",
    license: str = "MIT",
    force: bool = False,
    use_mdk: bool = True,
    audit_after: bool = True,
    build_after: bool = False,
    verify_after: bool = False,
    package_after: bool = False,
    build_timeout: int = 1800,
    verify_timeout: int = 600,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """一句话构思 → 规格 → 可编译工程，可选审计/构建/验证/打包。

    返回 dict：project(Path)、spec、profile、scaffold、audit、build、verify、package。
    """
    _install_progress(progress)
    spec = plan(
        idea,
        loader,
        mc,
        name=name,
        modid=modid,
        package=package,
        scope=scope,
        author=author,
        license=license,
    )
    ident = spec["identity"]
    profile = resolve_profile(loader, mc, progress=progress)
    _ensure_fabric_mappings(profile, spec, progress)

    base = Path(dest_root).expanduser().resolve() if dest_root else paths.default_project_root()
    target = base / ident["modid"]
    if target.exists() and any(target.iterdir()):
        if not force:
            raise StudioError(
                f"目标目录非空：{target}。换一个目录，或勾选“覆盖已有工程”。"
            )
        # 覆盖时先把旧工程整体挪到带时间戳的备份目录，避免不同加载器/版本
        # 的残留文件混进新工程，也避免直接删除用户内容。
        import time

        backup = target.with_name(f"{target.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}")
        counter = 1
        while backup.exists():
            backup = target.with_name(
                f"{target.name}.backup-{time.strftime('%Y%m%d-%H%M%S')}-{counter}"
            )
            counter += 1
        target.rename(backup)
        _emit(progress, f"旧工程已备份到：{backup}")
    _emit(progress, f"正在生成工程：{target}")
    try:
        scaffold_report = scaffold_mod.scaffold(
            target, spec, profile, use_mdk=use_mdk, force=force
        )
    except McmodError as exc:
        raise StudioError(f"工程生成失败：{exc}") from exc
    for warning in scaffold_report.get("warnings", []):
        _emit(progress, f"提示：{warning}")

    _emit(progress, "正在登记为 Darkitten 工程…")
    project = _mark_project(target, spec, profile)
    result: dict[str, Any] = {
        "project": target,
        "project_label": project.label(),
        "spec": spec,
        "profile": profile.to_dict(),
        "scaffold": scaffold_report,
        "audit": None,
        "build": None,
        "verify": None,
        "package": None,
    }

    if audit_after:
        _emit(progress, "正在审计资源与注册一致性…")
        report = audit_mod.audit(target, profile)
        result["audit"] = report
        _emit(progress, audit_mod.summarize(report))

    if build_after:
        _emit(progress, f"正在执行 Gradle build（最长 {build_timeout} 秒）…")
        report = build_mod.build(target, profile, timeout=build_timeout, fix=True)
        result["build"] = report
        _emit(progress, build_mod.summarize(report))
        if not report.get("ok"):
            return result

    if verify_after:
        _emit(progress, f"正在启动开发服务端做冒烟验证（最长 {verify_timeout} 秒）…")
        report = verify_mod.verify_server(target, profile, timeout=verify_timeout)
        result["verify"] = report
        _emit(progress, verify_mod.summarize(report))
        if not report.get("ok"):
            return result

    if package_after:
        _emit(progress, "正在打包并生成发布元数据…")
        report = package_mod.package(target, spec, profile)
        result["package"] = report
        _emit(progress, package_mod.summarize(report))

    return result


def audit_project(project_root: str | Path, *, progress: Progress | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    profile = _profile_from_state(root, progress)
    _emit(progress, "正在审计资源与注册一致性…")
    report = audit_mod.audit(root, profile)
    _emit(progress, audit_mod.summarize(report))
    return report


def build_project(
    project_root: str | Path,
    *,
    timeout: int = 1800,
    fix: bool = True,
    progress: Progress | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    profile = _profile_from_state(root, progress)
    _emit(progress, f"正在执行 Gradle build（最长 {timeout} 秒）…")
    report = build_mod.build(root, profile, timeout=timeout, fix=fix)
    _emit(progress, build_mod.summarize(report))
    return report


def verify_project(
    project_root: str | Path,
    *,
    timeout: int = 600,
    progress: Progress | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    profile = _profile_from_state(root, progress)
    _emit(progress, f"正在启动开发服务端做冒烟验证（最长 {timeout} 秒）…")
    report = verify_mod.verify_server(root, profile, timeout=timeout)
    _emit(progress, verify_mod.summarize(report))
    return report


def package_project(
    project_root: str | Path,
    *,
    progress: Progress | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    spec = read_spec(root)
    profile = _profile_from_state(root, progress)
    _emit(progress, "正在打包并生成发布元数据…")
    report = package_mod.package(root, spec, profile)
    _emit(progress, package_mod.summarize(report))
    return report


def read_spec(project_root: str | Path) -> dict[str, Any]:
    from .studio_engine.util import read_json

    spec = read_json(Path(project_root) / "mod-spec.json")
    if not spec:
        raise StudioError("工程里没有 mod-spec.json，无法按规格继续操作。")
    return spec


def _profile_from_state(root: Path, progress: Progress | None = None):
    from .studio_engine.util import read_json

    state = read_json(root / ".mcmod" / "state.json", {}) or {}
    data = state.get("profile")
    if not data:
        raise StudioError("工程缺少 .mcmod/state.json，请先用生成器重新生成。")
    return versions_mod.VersionProfile(**data)


def jar_paths(project_root: str | Path) -> list[Path]:
    libs = Path(project_root) / "build" / "libs"
    if not libs.is_dir():
        return []
    return sorted(
        (item for item in libs.glob("*.jar") if not item.name.endswith(("-sources.jar", "-javadoc.jar"))),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )


def primary_jar(project_root: str | Path) -> Path | None:
    jars = jar_paths(project_root)
    return jars[0] if jars else None


def summarize(result: dict[str, Any]) -> str:
    """把 generate 的返回值压成给聊天 Agent 的短报告。"""
    lines = [f"工程：{result.get('project')}"]
    profile = result.get("profile") or {}
    if profile:
        lines.append(
            f"目标：{loader_label(profile.get('loader', ''))} {profile.get('mc', '')} / "
            f"加载器 {profile.get('loader_version', '')} / Java {profile.get('java_major', '')}"
        )
    spec = result.get("spec") or {}
    if spec:
        lines.append(
            f"规格：{len(spec.get('content', []))} 个内容节点，主题 "
            + "、".join(spec.get("themes", []))
        )
    audit = result.get("audit")
    if audit:
        lines.append(
            f"审计：{'通过' if audit.get('ok') else '未通过'}，"
            f"{audit.get('score', 0)}/100，错误 {audit.get('errors', 0)}"
        )
    build = result.get("build")
    if build:
        lines.append(f"构建：{'通过' if build.get('ok') else '失败'}（{build.get('iterations', 0)} 轮）")
    verify = result.get("verify")
    if verify:
        lines.append(f"服务端验证：{'通过' if verify.get('ok') else '失败'}")
    package = result.get("package")
    if package:
        lines.append(f"打包：{package.get('jar')}")
    return "\n".join(lines)
