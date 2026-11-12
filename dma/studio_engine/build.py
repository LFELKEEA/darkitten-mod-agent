"""Gradle build driver with structured diagnostics and a bounded fix loop.

The compiler is the only authority. This module never claims a build succeeded
without a zero exit code, and it turns raw Gradle noise into actionable records
(file, line, symbol, likely cause).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from ..gradle import toolchain_args, windows_command
from .util import (
    McmodError, TOOLS_DIR, download, eprint, find_java_homes, log, pick_java, run,
    write_json, write_text,
)
from .versions import VersionProfile

GRADLE_MIRRORS = [
    "https://mirrors.cloud.tencent.com/gradle/gradle-{v}-bin.zip",
    "https://mirrors.aliyun.com/gradle/gradle-{v}-bin.zip",
]


def forge_flags(profile: VersionProfile) -> list[str]:
    """Disable ForgeGradle's startup connectivity probe.

    ForgeGradle 6 opens a blocking HEAD request to maven.minecraftforge.net and
    libraries.minecraft.net without a read timeout. On a filtered network the
    connection establishes but never answers, which stalls the build for many
    minutes before any task runs.
    """
    if profile.loader == "forge":
        return ["-Dnet.minecraftforge.gradle.check.certs=false"]
    return []


def gradle_command(project: Path) -> list[str] | None:
    # 不在这里拼 cmd /c：参数是后面才追加的，cmd /c 一旦遇到多个带引号的
    # 参数就会把工程路径截断到第一个空格。调用方用 windows_command 统一处理。
    wrapper_bat = project / "gradlew.bat"
    wrapper = project / "gradlew"
    if os.name == "nt" and wrapper_bat.exists():
        return [str(wrapper_bat)]
    if wrapper.exists():
        return [str(wrapper)]
    return None


def _cached_gradle(version: str) -> Path | None:
    """Reuse a Gradle distribution already present in the wrapper cache."""
    candidates: list[Path] = []
    roots = [Path.home() / ".gradle" / "wrapper" / "dists"]
    for root in roots:
        if not root.exists():
            continue
        for dist in root.glob(f"gradle-{version}-bin"):
            for bat in dist.glob(f"*/gradle-{version}/bin/gradle.bat"):
                candidates.append(bat)
            for sh in dist.glob(f"*/gradle-{version}/bin/gradle"):
                candidates.append(sh)
        if candidates:
            break
    return candidates[0] if candidates else None


def _gradle_exec(path: Path) -> list[str]:
    return [str(path)]


def ensure_gradle(profile: VersionProfile) -> list[str]:
    """Return a Gradle command, downloading a distribution if necessary."""
    version = profile.gradle_version or "8.14.3"
    home = TOOLS_DIR / f"gradle-{version}"
    bat = home / "bin" / "gradle.bat"
    sh = home / "bin" / "gradle"
    if bat.exists() or sh.exists():
        return _gradle_exec(bat if bat.exists() else sh)

    cached = _cached_gradle(version)
    if cached:
        log(f"复用已缓存的 Gradle {version}：{cached}")
        return _gradle_exec(cached)

    # A newer version in the wrapper cache is usually fine (and faster than
    # downloading). Only accept it when it is >= the requested version.
    # ForgeGradle 6 does not work on Gradle 9, so cap the major for Forge.
    cap_major = 8 if profile.loader == "forge" else 99
    options: list[tuple[tuple, Path]] = []
    for dist in (Path.home() / ".gradle" / "wrapper" / "dists").glob("gradle-*-bin"):
        name = dist.name[len("gradle-"):-len("-bin")]
        key = _key(name)
        if key < _key(version) or key[0][1] > cap_major:
            continue
        for bat in dist.glob(f"*/gradle-{name}/bin/gradle.bat"):
            options.append((key, bat))
            break
        else:
            for sh in dist.glob(f"*/gradle-{name}/bin/gradle"):
                options.append((key, sh))
                break
    if options:
        options.sort(key=lambda item: item[0])
        key, path = options[0]
        log(f"复用较新的 Gradle {'.'.join(str(p[1]) for p in key)}：{path}")
        return _gradle_exec(path)

    url = f"https://services.gradle.org/distributions/gradle-{version}-bin.zip"
    zip_path = TOOLS_DIR / f"gradle-{version}-bin.zip"
    mirrors = [m.format(v=version) for m in GRADLE_MIRRORS]
    log(f"下载 Gradle {version} …")
    download(url, zip_path, timeout=300, mirrors=mirrors)
    import zipfile
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(TOOLS_DIR)
    if bat.exists():
        return _gradle_exec(bat)
    if sh.exists():
        return _gradle_exec(sh)
    raise McmodError(f"Gradle 解压后找不到可执行文件：{home}")


def _key(v: str) -> tuple:
    out = []
    for part in re.split(r"[.\-+]", v):
        m = re.match(r"(\d+)", part)
        out.append((0, int(m.group(1))) if m else (1, part))
    return tuple(out)


def _java_env(profile: VersionProfile) -> dict[str, str]:
    homes = find_java_homes()
    chosen = pick_java(homes, profile.java_major)
    if not chosen:
        available = ", ".join(f"{j.major}({j.path})" for j in homes) or "未发现"
        raise McmodError(
            f"需要 JDK {profile.java_major}，但本机没有可用版本。已发现：{available}。"
            "请安装对应 JDK 或设置 JAVA_HOME。"
        )
    env = {"JAVA_HOME": str(chosen.path)}
    if os.name == "nt":
        env["PATH"] = str(chosen.path / "bin") + os.pathsep + os.environ.get("PATH", "")
    return env


# Artifact groups whose hosts are frequently unreachable (maven.neoforged.net,
# maven.minecraftforge.net) but whose files are usually already in the Gradle
# cache from an earlier resolution.
_LOCAL_REPO_GROUPS = (
    "net.neoforged",
    "net.neoforged.fancymodloader",
    "net.neoforged.accesstransformers",
    "cpw.mods",
    "net.minecraftforge",
)


def _declared_module_files(version_dir: Path) -> set[str]:
    """Artifact file names declared by a cached Gradle .module file."""
    names: set[str] = set()
    for module in version_dir.glob("*/*.module"):
        try:
            data = json.loads(module.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for variant in data.get("variants", []):
            for item in variant.get("files", []):
                name = item.get("name")
                if not name or name.endswith(("-sources.jar", "-javadoc.jar")):
                    continue
                names.add(name)
    return names


def _cached_file_exists(version_dir: Path, name: str) -> bool:
    return any(path.is_file() for path in version_dir.glob(f"*/{name}"))


def _has_cached_pom(version_dir: Path) -> bool:
    return any(version_dir.glob("*/*.pom"))


def _pruned_module(source: Path, version_dir: Path) -> dict | None:
    """Drop variant files that were never cached from a Gradle .module file.

    Gradle pins artifacts to the repository that supplied the metadata, so a
    variant pointing at an unservable file would fail instead of falling
    through to the mirror. Capability-only variants have no files and must be
    preserved: ModDevGradle selects neoform-runtime through its
    ``neoform-runtime-external-tools`` capability. Returns None when no
    variant survives (the POM alone is then the right metadata).
    """
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    variants: list[dict] = []
    for variant in data.get("variants", []):
        items = variant.get("files")
        if items:
            kept = [item for item in items
                    if item.get("name") and _cached_file_exists(version_dir, item["name"])]
            if not kept and not variant.get("capabilities"):
                continue
            variant = {**variant, "files": kept}
        variants.append(variant)
    if not variants:
        return None
    data["variants"] = variants
    return data


def _seed_local_maven() -> Path | None:
    """Mirror cached Forge/NeoForge artifacts into a file:// Maven repository.

    Gradle keys its metadata cache by repository URL, so rewriting an
    unreachable repository to a mirror discards artifacts that were already
    downloaded from the original host. A local repository keeps those files
    usable (and lets a missing POM be regenerated from Gradle module metadata).
    Returns None when nothing was found.
    """
    if os.environ.get("MCMOD_LOCAL_MAVEN", "").lower() in {"0", "off", "false", "no"}:
        return None
    explicit = os.environ.get("MCMOD_LOCAL_MAVEN", "")
    if explicit and explicit.lower() not in {"1", "auto", "true", "yes"}:
        path = Path(explicit)
        return path if path.exists() else None

    cache = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1"
    if not cache.exists():
        return None
    target = TOOLS_DIR / "local-maven"
    copied = 0
    for group in _LOCAL_REPO_GROUPS:
        group_dir = cache / group
        if not group_dir.is_dir():
            continue
        for module_dir in group_dir.iterdir():
            if not module_dir.is_dir():
                continue
            for version_dir in module_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                # Gradle resolves artifacts from the repository that supplied
                # the module metadata, so never publish a .module whose
                # variants reference files we do not have. Tool artifacts such
                # as mergetool only ship classifier jars ("-api", "-fatjar"),
                # and the plain jar listed in the variant metadata simply does
                # not exist upstream. Variants that lose all their files are
                # pruned; capability-only variants (ModDevGradle asks for
                # neoform-runtime's "external-tools" capability) are kept.
                declared = _declared_module_files(version_dir)
                incomplete = any(
                    not _cached_file_exists(version_dir, name) for name in declared
                )
                if incomplete:
                    jars = [p for p in version_dir.glob("*/*.jar")]
                    if not jars or not _has_cached_pom(version_dir):
                        continue
                dest = target.joinpath(*group.split("."), module_dir.name, version_dir.name)
                poms: set[str] = set()
                modules: dict[str, Path] = {}
                for hash_dir in version_dir.iterdir():
                    if not hash_dir.is_dir():
                        continue
                    for item in hash_dir.iterdir():
                        if not item.is_file():
                            continue
                        if item.suffix == ".pom":
                            poms.add(item.name)
                        elif item.suffix == ".module":
                            modules[item.name] = item
                            continue
                        out = dest / item.name
                        if out.exists():
                            continue
                        dest.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, out)
                        copied += 1
                for name, source in modules.items():
                    pruned = _pruned_module(source, version_dir)
                    if pruned is None:
                        continue
                    text = json.dumps(pruned, indent=2, ensure_ascii=False) + "\n"
                    out = dest / name
                    try:
                        if out.read_text(encoding="utf-8") == text:
                            continue
                    except OSError:
                        pass
                    dest.mkdir(parents=True, exist_ok=True)
                    write_text(out, text)
                    copied += 1
                # A cached .module without its POM cannot be served by Maven.
                for name, source in modules.items():
                    pom_name = name[: -len(".module")] + ".pom"
                    if pom_name in poms or (dest / pom_name).exists():
                        continue
                    try:
                        data = json.loads(source.read_text(encoding="utf-8"))
                        component = data["component"]
                    except (OSError, ValueError, KeyError):
                        continue
                    dest.mkdir(parents=True, exist_ok=True)
                    write_text(dest / pom_name, (
                        '<?xml version="1.0" encoding="UTF-8"?>\n'
                        '<project xmlns="http://maven.apache.org/POM/4.0.0">\n'
                        "  <modelVersion>4.0.0</modelVersion>\n"
                        "  <!-- do_not_remove: published-with-gradle-metadata -->\n"
                        f"  <groupId>{component['group']}</groupId>\n"
                        f"  <artifactId>{component['module']}</artifactId>\n"
                        f"  <version>{component['version']}</version>\n"
                        "  <packaging>pom</packaging>\n"
                        "</project>\n"
                    ))
                    copied += 1
    if copied:
        log(f"已从 Gradle 缓存生成本地 Maven 仓库：{target}（新增 {copied} 个文件）")
    return target if any(target.rglob("*.pom")) else None


def local_maven_args() -> list[str]:
    repo = _seed_local_maven()
    return [f"-Pmcmod_local_repo={repo}"] if repo else []


def mirror_args() -> list[str]:
    """官方仓库直连不通时，把依赖解析转到本地镜像代理。"""
    try:
        from .. import mirrors

        script = mirrors.prepare_launch_env(log=log)
    except Exception as exc:  # noqa: BLE001 - 镜像不可用时保持官方源
        log(f"镜像代理不可用，继续直连官方仓库：{exc}")
        return []
    if script is None:
        return []
    log("官方仓库直连不通，已启用本地镜像代理")
    return ["--init-script", str(script)]


def _run_gradle(
    project: Path,
    profile: VersionProfile,
    args: list[str],
    timeout: int,
    extra_env: dict[str, str] | None = None,
) -> tuple[Any, Path]:
    cmd = gradle_command(project) or ensure_gradle(profile)
    cmd = [*cmd, *args, *toolchain_args([str(j.path) for j in find_java_homes()]),
           *local_maven_args(), *mirror_args(), *forge_flags(profile),
           "--console=plain", "--stacktrace",
           "-Dorg.gradle.internal.http.connectionTimeout=60000",
           "-Dorg.gradle.internal.http.socketTimeout=180000"]
    env = _java_env(profile)
    if extra_env:
        env.update(extra_env)
    log(f"运行：{' '.join(cmd)}")
    started = time.time()
    stream = os.environ.get("MCMOD_STREAM")
    if stream is None:
        stream = "1" if sys.stdout.isatty() else "0"
    result = run(windows_command(cmd), cwd=project, timeout=timeout, env=env,
                 stream=stream not in {"0", "false", "no"})
    logdir = project / ".mcmod" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"gradle-{int(started)}.log"
    write_text(logfile, result.combined + f"\n\n[exit={result.returncode} duration={result.duration:.1f}s timed_out={result.timed_out}]\n")
    return result, logfile


def build(
    project: Path,
    profile: VersionProfile,
    *,
    timeout: int = 1800,
    fix: bool = False,
    max_iterations: int = 4,
    task: str = "build",
) -> dict[str, Any]:
    project = project.resolve()
    history: list[dict[str, Any]] = []
    result = None
    logfile = None
    for iteration in range(1, max_iterations + 1 if fix else 2):
        try:
            result, logfile = _run_gradle(project, profile, [task], timeout)
        except (McmodError, OSError) as exc:
            # 没有 Wrapper、JDK 缺失、Gradle 下载失败等属于环境问题，不是编译
            # 错误；这里转成结构化结果，避免异常直接冲进 UI 线程。
            message = str(exc)
            report = {
                "ok": False,
                "task": task,
                "iterations": len(history) + 1,
                "log": "",
                "errors": [{
                    "file": "", "line": 0, "message": message,
                    "hint": "检查工程是否有 gradlew，以及本机 JDK / 网络是否可用。",
                }],
                "history": history,
                "timed_out": False,
                "tail": message,
                "hints": [message],
            }
            write_json(project / ".mcmod" / "build-report.json", report)
            return report
        errors = parse_errors(result.combined)
        history.append({
            "iteration": iteration,
            "exit": result.returncode,
            "timed_out": result.timed_out,
            "duration": round(result.duration, 1),
            "errors": errors,
            "log": str(logfile),
        })
        if result.returncode == 0:
            report = {
                "ok": True,
                "task": task,
                "iterations": iteration,
                "log": str(logfile),
                "history": history,
                "errors": [],
            }
            write_json(project / ".mcmod" / "build-report.json", report)
            return report
        if not fix:
            break
        applied = apply_safe_fixes(project, profile, errors)
        if not applied:
            break
        log(f"已应用 {len(applied)} 个安全修复，重新构建 …")
        history[-1]["fixes"] = applied

    errors = parse_errors(result.combined) if result else []
    report = {
        "ok": False,
        "task": task,
        "iterations": len(history),
        "log": str(logfile) if logfile else "",
        "errors": errors,
        "history": history,
        "timed_out": bool(result and result.timed_out),
        "tail": result.tail(60) if result else "",
        "hints": [e.get("hint", "") for e in errors if e.get("hint")],
    }
    write_json(project / ".mcmod" / "build-report.json", report)
    return report


ERROR_PATTERNS = [
    re.compile(r"^(?P<file>.+?\.java):(?P<line>\d+): error: (?P<message>.+)$", re.MULTILINE),
    re.compile(r"^(?P<file>.+?\.java):(?P<line>\d+): error: (?P<message>.+)$", re.MULTILINE),
]


def parse_errors(output: str) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for pattern in ERROR_PATTERNS:
        for match in pattern.finditer(output):
            item = {
                "file": match.group("file").strip(),
                "line": int(match.group("line")),
                "message": match.group("message").strip(),
            }
            key = (item["file"], str(item["line"]), item["message"])
            if key not in seen:
                seen.add(key)
                item["hint"] = classify(item["message"], output)
                errors.append(item)
    # Symbol details often appear on the following lines.
    symbol_blocks = re.findall(
        r"error: cannot find symbol\n\s+symbol:\s+([^\n]+)\n\s+location:\s+([^\n]+)", output)
    for symbol, location in symbol_blocks:
        errors.append({
            "file": "", "line": 0,
            "message": f"cannot find symbol: {symbol.strip()} @ {location.strip()}",
            "hint": "该 API 不在当前版本中。运行 mcmod recon 并查 API 卡，不要凭记忆改。",
        })
    if not errors:
        for line in output.splitlines():
            low = line.lower()
            if any(k in low for k in [
                "could not resolve", "unsupported class file major version",
                "no matching toolchains", "plugin [id:", "failed to apply plugin",
                "could not find method", "incompatible", "requires java",
            ]):
                errors.append({"file": "", "line": 0, "message": line.strip(),
                               "hint": classify(line, output)})
    # Deduplicate by message.
    unique: list[dict[str, Any]] = []
    seen_msg: set[str] = set()
    for e in errors:
        if e["message"] in seen_msg:
            continue
        seen_msg.add(e["message"])
        unique.append(e)
    return unique[:80]


def classify(message: str, output: str) -> str:
    m = message.lower()
    if "cannot find symbol" in m:
        return "符号不存在：版本 API 不匹配或 import 错误。用 recon 的 API 卡核对类名/方法名。"
    if "incompatible types" in m:
        return "类型不匹配：常见于 Item.Properties / BlockBehaviour.Properties / 泛型注册器。核对目标版本签名。"
    if "package net.minecraft" in m and "does not exist" in m:
        return "Minecraft 包不存在：映射或依赖配置错误，检查 build.gradle 与 mappings。"
    if "unsupported class file major version" in m or "requires java" in m:
        return "JDK 版本不匹配：按 profile 的 java_major 设置 JAVA_HOME。"
    if "could not resolve" in m:
        return "依赖解析失败：检查仓库 URL、版本号与网络/镜像。"
    if "no matching toolchains" in m:
        return "Gradle toolchain 找不到指定 JDK：安装对应 JDK 或在 gradle.properties 指定 org.gradle.java.home。"
    if "failed to apply plugin" in m or "plugin [id:" in m:
        return "Gradle 插件版本与当前 Gradle/JDK 不兼容：优先使用对应版本的官方 MDK。"
    if "duplicate" in m and "class" in m:
        return "重复类：MDK 示例类未清理，或包名冲突。"
    if "mixin" in m:
        return "Mixin 目标不匹配：核对目标类/方法在 sources jar 中是否存在。"
    return "查看完整日志与 API 卡；不要盲目重试。"


def apply_safe_fixes(project: Path, profile: VersionProfile, errors: list[dict[str, Any]]) -> list[str]:
    """Apply only mechanical, high-confidence fixes. Never guess semantics."""
    applied: list[str] = []
    files = {e.get("file") for e in errors if e.get("file")}
    for file in files:
        path = Path(file)
        if not path.is_absolute():
            path = project / path
        if not path.exists() or path.suffix != ".java":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        original = text
        if profile.loader == "fabric" and _is_1_21_plus(profile.mc):
            # new Identifier(ns, path) was replaced by Identifier.of(ns, path).
            text = re.sub(r"new Identifier\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
                          r"Identifier.of(\1, \2)", text)
        if profile.loader in {"neoforge"} and _is_1_21_plus(profile.mc):
            # ResourceLocation constructors were replaced by static factories.
            text = re.sub(r"new ResourceLocation\(\s*([^,]+?)\s*,\s*([^)]+?)\s*\)",
                          r"ResourceLocation.fromNamespaceAndPath(\1, \2)", text)
            text = re.sub(r"new ResourceLocation\(\s*([^)]+?)\s*\)",
                          r"ResourceLocation.parse(\1)", text)
        if text != original:
            path.write_text(text, encoding="utf-8", newline="\n")
            applied.append(f"重写资源位置构造：{path.name}")
    # Metadata filename mismatch.
    if profile.loader == "neoforge" and _is_1_21_plus(profile.mc):
        old = project / "src" / "main" / "resources" / "META-INF" / "mods.toml"
        new = project / "src" / "main" / "resources" / "META-INF" / "neoforge.mods.toml"
        if old.exists() and not new.exists():
            old.replace(new)
            applied.append("重命名 mods.toml -> neoforge.mods.toml")
    return applied


def _is_1_21_plus(mc: str) -> bool:
    if mc.startswith("26."):
        return True
    try:
        parts = [int(x) for x in re.findall(r"\d+", mc)[:2]]
        return tuple(parts) >= (1, 21)
    except ValueError:
        return True


def summarize(report: dict[str, Any]) -> str:
    if report.get("ok"):
        return f"构建成功（第 {report['iterations']} 次，日志 {report['log']}）"
    lines = [f"构建失败（{report.get('iterations', 0)} 次尝试）", f"日志：{report.get('log', '')}"]
    if report.get("timed_out"):
        lines.append("构建超时：检查网络/Gradle 下载，或提高 --timeout。")
    for err in report.get("errors", [])[:15]:
        loc = f"{err.get('file', '')}:{err.get('line', '')}" if err.get("file") else "gradle"
        lines.append(f"- {loc} {err['message']}")
        if err.get("hint"):
            lines.append(f"  → {err['hint']}")
    return "\n".join(lines)
