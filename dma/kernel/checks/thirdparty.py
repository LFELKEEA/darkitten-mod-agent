"""Third-party libraries, optional dependencies and shading checks (P1-20..24)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from ..findings import Finding
from .common import ProjectContext, finding

IGNORED_PREFIXES = (
    "java.", "javax.", "jdk.", "sun.", "com.sun.",
    "net.minecraft", "net.neoforged", "net.minecraftforge", "net.fabricmc",
    "com.mojang.", "org.slf4j", "org.apache.logging", "org.jetbrains.",
    "com.google.gson", "com.google.common", "it.unimi.", "org.lwjgl.",
    "io.netty.", "org.objectweb.asm",
)


def run(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    own_roots = _own_roots(ctx)
    third_party = _third_party_imports(ctx, own_roots)
    build = _build_text(ctx)
    rows.extend(_undeclared(ctx, third_party, build))
    rows.extend(_optional(ctx, own_roots, build))
    rows.extend(_services(ctx))
    rows.extend(_shading(ctx, build, third_party))
    rows.extend(_compat_probe(ctx, build))
    return rows


# ----------------------------------------------------------------------
def _own_roots(ctx: ProjectContext) -> set[str]:
    roots: set[str] = set()
    for java in ctx.java_files:
        if java.package:
            parts = java.package.split(".")
            roots.add(".".join(parts[:2]))
    return roots


def _third_party_imports(ctx: ProjectContext, own_roots: set[str]) -> dict[str, list[tuple[str, int]]]:
    found: dict[str, list[tuple[str, int]]] = {}
    for java in ctx.java_files:
        rel = ctx.rel(java.path)
        for line_no, line in enumerate(java.text.splitlines(), 1):
            match = re.match(r"\s*import\s+(?:static\s+)?([\w.]+(?:\.\*)?)\s*;", line)
            if not match:
                continue
            name = match.group(1)
            if name.startswith(IGNORED_PREFIXES):
                continue
            if any(name == root or name.startswith(root + ".") for root in own_roots):
                continue
            if name.startswith(("dma.", "com.darkitten.")):
                continue
            found.setdefault(name, []).append((rel, line_no))
    return found


def _gt(group: str, build: str) -> bool:
    if not group:
        return False
    return group in build


def _build_text(ctx: ProjectContext) -> str:
    text = ""
    for name in ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"):
        if ctx.exists(name):
            text += "\n" + ctx.read_rel(name)
    for path in ctx.glob("gradle/*.toml"):
        text += "\n" + ctx.read(path)
    return text


def _undeclared(ctx: ProjectContext, imports: dict[str, list[tuple[str, int]]], build: str) -> Iterable[Finding]:
    rows: list[Finding] = []
    for name, locations in sorted(imports.items()):
        group = ".".join(name.split(".")[:3])
        if _gt(group, build):
            continue
        # A dependency may be declared by a shorter group (org.example).
        if _gt(".".join(name.split(".")[:2]), build):
            continue
        rel, line = locations[0]
        rows.append(finding(
            "P1-20", f"引用了未在构建脚本声明的第三方包：{name}",
            path=rel, line=line, severity="warning",
            suggestion=f"在 build.gradle 中显式声明 {group} 依赖并锁定版本，避免运行时 NoClassDefFoundError。",
        ))
    return rows


def _optional(ctx: ProjectContext, own_roots: set[str], build: str) -> Iterable[Finding]:
    rows: list[Finding] = []
    mods = _mod_metadata(ctx)
    optional_ids = mods.get("optional", [])
    required_ids = mods.get("required", [])
    for java in ctx.java_files:
        text = java.text
        imports = re.findall(r"\s*import\s+(?:static\s+)?([\w.]+)\s*;", text)
        foreign = [
            name for name in imports
            if not name.startswith(IGNORED_PREFIXES)
            and not any(name == root or name.startswith(root + ".") for root in own_roots)
        ]
        guards = ("isLoaded(", "ModList.get()", "FMLModContainer", "getModContainerById", "isModLoaded")
        has_guard = any(token in text for token in guards)
        if foreign and not has_guard and "implements" in text:
            rows.append(finding(
                "P1-21", f"{Path(java.path).name} 在顶层引用外部类型，缺少软依赖隔离",
                path=ctx.rel(java.path), line=1, severity="info",
                suggestion="把可选依赖的集成代码放到单独类/包，运行时用 ModList 判断存在后再加载。",
            ))
        if optional_ids and foreign and not has_guard:
            rows.append(finding(
                "P1-21", f"{Path(java.path).name} 引用可选依赖但没有 isLoaded 判断",
                path=ctx.rel(java.path), line=1, severity="warning",
                suggestion=f"可选依赖 {', '.join(sorted(optional_ids))} 未安装时不能触发类加载。",
            ))
        if "@Mod(" in text and "dependencies" in text:
            match = re.search(r"dependencies\s*=\s*\"([^\"]*)\"", text)
            if match and "mandatory" in match.group(1) and optional_ids:
                rows.append(finding(
                    "P1-21", f"{Path(java.path).name} 把可选依赖写成了 mandatory",
                    path=ctx.rel(java.path), line=1, severity="warning",
                    suggestion="可选依赖使用 type=\"optional\" 或 after/before 顺序声明。",
                ))
        if "ServiceLoader" in text and "load(" in text and not has_guard:
            rows.append(finding(
                "P1-22", f"{Path(java.path).name} 使用 ServiceLoader 但没有缺失实现的降级",
                path=ctx.rel(java.path), line=1, severity="info",
                suggestion="捕获 ServiceConfigurationError，并为空实现提供默认实现。",
            ))
    for mod_id in required_ids:
        if mod_id and mod_id not in build and mod_id not in {"minecraft", "neoforge", "forge", "fabricloader"}:
            rows.append(finding(
                "P1-20", f"mods.toml 依赖 {mod_id} 但构建脚本没有对应依赖",
                path=_metadata_path(ctx) or "META-INF/mods.toml", severity="info",
                suggestion="确认该依赖是玩家自备还是应打包；版本范围要与发布说明一致。",
            ))
    return rows


def _services(ctx: ProjectContext) -> Iterable[Finding]:
    rows: list[Finding] = []
    service_files = [
        path for path in ctx.root.rglob("META-INF/services/*")
        if path.is_file() and "build" not in path.relative_to(ctx.root).parts
    ]
    provided = {path.name for path in service_files}
    for java in ctx.java_files:
        text = java.text
        for match in re.finditer(r"ServiceLoader\s*\.\s*load\s*\(\s*([\w.$]+)\s*\.\s*class", text):
            iface = match.group(1)
            simple = iface.split(".")[-1]
            if iface not in provided and simple not in provided:
                rows.append(finding(
                    "P1-22", f"ServiceLoader.load({iface}) 缺少 META-INF/services 文件",
                    path=ctx.rel(java.path), line=java.text[:match.start()].count("\n") + 1,
                    severity="warning",
                    suggestion=f"添加 META-INF/services/{iface}，每行一个实现类全名。",
                ))
        if "ServiceLoader" in text and "ServiceConfigurationError" not in text:
            rows.append(finding(
                "P1-22", "ServiceLoader 调用没有处理 ServiceConfigurationError",
                path=ctx.rel(java.path), line=1, severity="info",
                suggestion="损坏的服务文件会抛出 Error，捕获后记录并继续。",
            ))
    for path in service_files:
        rel = ctx.rel(path)
        for line_no, line in enumerate(ctx.read(path).splitlines(), 1):
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            if "." not in value:
                rows.append(finding(
                    "P1-22", f"服务文件条目不是全限定类名：{value}",
                    path=rel, line=line_no, severity="warning",
                    suggestion="使用 com.example.Impl 形式的全限定名。",
                ))
    return rows


def _shading(ctx: ProjectContext, build: str, imports: dict[str, list[tuple[str, int]]]) -> Iterable[Finding]:
    rows: list[Finding] = []
    uses_shadow = "com.github.johnrengelman.shadow" in build or "com.gradleup.shadow" in build
    has_relocate = "relocate(" in build or "relocate " in build
    if uses_shadow and not has_relocate and imports:
        rows.append(finding(
            "P1-23", "使用 shadow 打包但没有 relocate 第三方包，可能与其它 Mod 冲突",
            path="build.gradle", line=1, severity="warning",
            suggestion="把第三方包 relocate 到自己的命名空间，或改用 jarJar 由加载器隔离。",
        ))
    if "jarJar" in build and "implementation(" in build and "jarJar(" not in build:
        rows.append(finding(
            "P1-23", "声明了 jarJar 但依赖仍用 implementation，打包后缺少内嵌库",
            path="build.gradle", line=1, severity="info",
            suggestion="需要内嵌的依赖改用它提供的 jarJar 配置。",
        ))
    if "minimize()" in build and imports:
        rows.append(finding(
            "P1-23", "shadowJar 开启 minimize，反射加载的类可能被裁掉",
            path="build.gradle", line=1, severity="info",
            suggestion="为反射/ServiceLoader 类添加保留规则，或关闭 minimize。",
        ))
    if "duplicatesStrategy" in build and "INCLUDE" in build:
        rows.append(finding(
            "P3-03", "duplicatesStrategy=INCLUDE 会静默覆盖同名资源",
            path="build.gradle", line=1, severity="warning",
            suggestion="显式排除重复条目，或使用 EXCLUDE + 明确合并策略。",
        ))
    return rows


def _compat_probe(ctx: ProjectContext, build: str) -> Iterable[Finding]:
    rows: list[Finding] = []
    mods = _mod_metadata(ctx)
    probes = ("isLoaded(", "getModContainerById", "ModList.get()", "FMLModContainer")
    probe_files = [java for java in ctx.java_files if any(token in java.text for token in probes)]
    for mod_id in mods.get("optional", []):
        if not probe_files:
            rows.append(finding(
                "P1-24", f"声明了可选依赖 {mod_id}，但没有运行时兼容探测",
                path=_metadata_path(ctx) or "META-INF/mods.toml", line=1, severity="info",
                suggestion="启动时探测该 Mod 是否加载，并按版本分支注册兼容逻辑。",
            ))
    return rows


# ----------------------------------------------------------------------
def _metadata_path(ctx: ProjectContext) -> str:
    for name in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml", "fabric.mod.json"):
        if ctx.exists("src/main/resources/" + name):
            return "src/main/resources/" + name
    return ""


def _mod_metadata(ctx: ProjectContext) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"optional": [], "required": []}
    path = _metadata_path(ctx)
    if not path:
        return result
    text = ctx.read_rel(path)
    if path.endswith(".json"):
        import json
        try:
            data = json.loads(text)
        except ValueError:
            return result
        depends = data.get("depends") or {}
        if isinstance(depends, dict):
            result["required"] = list(depends.keys())
        return result
    for block in re.split(r"\[\[dependencies\.", text):
        if not block.strip():
            continue
        mod_id = re.search(r"modId\s*=\s*\"([^\"]+)\"", block)
        kind = re.search(r"type\s*=\s*\"(required|optional|incompatible|discouraged)\"", block)
        if not mod_id:
            continue
        if kind and kind.group(1) == "optional":
            result["optional"].append(mod_id.group(1))
        elif not kind or kind.group(1) == "required":
            result["required"].append(mod_id.group(1))
    return result
