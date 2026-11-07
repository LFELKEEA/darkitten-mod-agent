"""Gradle 包装：构建、GameTest、安装探针。"""

from __future__ import annotations

import os
import subprocess
import threading
import time
import zipfile
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class GradleError(RuntimeError):
    pass


_JAVA_CACHE: tuple[str, str] | None = None


def _preferred_java() -> tuple[str, str] | None:
    """1.20.1 优先 Java 17；没有就退到 21，避免拿 Java 25 跑 Gradle 8.8。"""
    global _JAVA_CACHE
    if _JAVA_CACHE is not None:
        return _JAVA_CACHE or None
    from .. import envcheck

    found: list[tuple[int, str, str]] = []
    for home in envcheck.java_homes():
        version = envcheck.java_version_at(home) or envcheck.probe_java_home(home)
        if not version:
            continue
        found.append((envcheck.java_major(version), str(home), version))
    chosen: tuple[str, str] | None = None
    for wanted in (17, 21):
        for major, home, version in found:
            if major == wanted:
                chosen = (home, version)
                break
        if chosen:
            break
    if chosen is None and found:
        major, home, version = min(
            found, key=lambda row: (envcheck.java_rank(row[0]), -row[0])
        )
        chosen = (home, version)
    _JAVA_CACHE = chosen or ("", "")
    return chosen


def build_env(*, windows_trust: bool = True) -> dict[str, str]:
    """Gradle 子进程环境：固定 JDK、放宽超时、必要时用 Windows 根证书。"""
    from .. import gradle as core_gradle

    java = _preferred_java()
    return core_gradle.build_env(
        windows_trust,
        java_home=java[0] if java else None,
    )


def _command(project_dir: Path, task: str) -> list[str]:
    gradlew = project_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not gradlew.is_file():
        raise GradleError(
            f"{project_dir} 里没有 {gradlew.name}，无法执行 Gradle。"
            "先补齐 Wrapper，或让助手按当前加载器生成工程骨架。"
        )
    argv = [str(gradlew), task, "--console=plain"]
    if os.name == "nt":
        argv = ["cmd.exe", "/c"] + argv
    return argv


def run_gradle(
    project_dir: str | Path,
    task: str,
    *,
    timeout: int = 900,
    cancel: threading.Event | None = None,
    log=None,
) -> tuple[int, str]:
    root = Path(str(project_dir)).expanduser()
    if not root.is_dir():
        raise GradleError(f"工程目录不存在：{root}")
    argv = _command(root, task)
    if log:
        try:
            log(f"[gradle] {' '.join(argv)}")
        except Exception:  # noqa: BLE001 - 日志回调不能影响构建
            pass
    try:
        process = subprocess.Popen(
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=CREATE_NO_WINDOW,
            env=build_env(),
        )
    except OSError as exc:
        raise GradleError(f"无法启动 gradlew：{exc}") from exc
    started = time.time()
    collected: list[str] = []
    total = 0
    cap = 200_000
    try:
        assert process.stdout is not None
        for line in process.stdout:
            collected.append(line)
            total += len(line)
            if total > cap:
                joined = "".join(collected)
                collected = [joined[-cap:]]
                total = cap
            if log and line.strip():
                try:
                    log(line.strip()[-200:])
                except Exception:  # noqa: BLE001 - 日志回调不能影响构建
                    pass
            if cancel is not None and cancel.is_set():
                _kill(process)
                raise GradleError("已取消。")
            if time.time() - started > timeout:
                _kill(process)
                raise GradleError(f"{task} 超过 {timeout} 秒，已终止。")
        code = process.wait()
    finally:
        try:
            process.stdout.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    output = "".join(collected)
    if os.name == "nt":
        # Windows 控制台偶尔输出 UTF-8 被当成 GBK，保留原样即可，这里只做换行归一。
        output = output.replace("\r\n", "\n")
    return code, output


def _kill(process: subprocess.Popen) -> None:
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
                timeout=20,
            )
        else:
            process.kill()
    except Exception:  # noqa: BLE001
        pass


def build(project_dir: str | Path, **kwargs) -> tuple[int, str]:
    return run_gradle(project_dir, "build", **kwargs)


def gametest_task(loader: str = "") -> str:
    """按加载器选择 GameTest 任务：Fabric 用 runGametest，其余用 runGameTestServer。"""
    if (loader or "").strip().lower() == "fabric":
        return "runGametest"
    return "runGameTestServer"


def gametest(project_dir: str | Path, loader: str = "", **kwargs) -> tuple[int, str]:
    return run_gradle(project_dir, gametest_task(loader), **kwargs)


def gametest_summary(code: int, output: str) -> str:
    import re

    passed = re.search(r"All (\d+) required tests passed", output)
    if passed:
        return f"All {passed.group(1)} required tests passed"
    failed = re.search(r"(\d+) required tests failed", output)
    if failed:
        return f"{failed.group(1)} required tests failed"
    return "no summary"


def _read_manifest(jar: Path) -> str:
    try:
        with zipfile.ZipFile(jar) as archive:
            return archive.read("META-INF/MANIFEST.MF").decode(
                "utf-8", errors="replace"
            )
    except Exception as exc:  # noqa: BLE001 - 清单读不到只警告
        return f"__error__:{exc}"


def install_probe(probe_jar: str | Path, run_dirs: list[str]) -> dict:
    jar = Path(str(probe_jar)).expanduser()
    if not jar.is_file():
        raise GradleError(f"探针 JAR 不存在：{jar}")
    if not jar.name.lower().endswith("-dev.jar"):
        raise GradleError(
            "探针必须是 *-dev.jar（开发映射）。普通 build/reobf 产物在开发环境会 NoSuchMethodError。"
        )
    manifest = _read_manifest(jar)
    warnings: list[str] = []
    has_mixin = False
    if manifest.startswith("__error__:"):
        warnings.append(f"清单未校验：{manifest[9:]}")
    else:
        has_mixin = "MixinConfigs: mcdev_probe.mixins.json" in manifest
        if not has_mixin:
            raise GradleError(
                '不是开发映射探针：MANIFEST.MF 缺少 "MixinConfigs: mcdev_probe.mixins.json"'
            )
    targets: list[dict] = []
    for raw in run_dirs:
        text = str(raw or "").strip()
        if not text:
            continue
        run_dir = Path(text).expanduser()
        mods = run_dir / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        dest = mods / jar.name
        dest.write_bytes(jar.read_bytes())
        targets.append({"runDir": str(run_dir), "dest": str(dest)})
    if not targets:
        raise GradleError("runDirs 为空，没有安装目标。")
    return {
        "probeJar": str(jar),
        "size": jar.stat().st_size,
        "manifestChecked": not manifest.startswith("__error__:"),
        "hasMixinConfigs": has_mixin,
        "targets": targets,
        "warnings": warnings,
    }
