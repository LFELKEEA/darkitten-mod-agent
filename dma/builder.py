from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import deps, gradle, mirrors, project as project_mod, toolchain, wrapper

Log = Callable[[str], None]


class BuilderError(Exception):
    pass


@dataclass
class BuildResult:
    ok: bool
    code: int
    message: str = ""
    output: str = ""
    jars: list[tuple[str, int]] = field(default_factory=list)
    prepared: toolchain.Prepared | None = None


def _run(
    root: Path,
    task: str,
    prepared: toolchain.Prepared,
    script: Path | None,
    log: Log,
    timeout: float,
    cancel: threading.Event | None,
) -> tuple[int, str, list[str]]:
    """调用 GradleRunner 并等它结束，返回退出码、启动错误和完整日志。"""
    if cancel is not None and cancel.is_set():
        raise BuilderError("任务已取消")
    launch = gradle.resolve_launch(root, task, init_script=script)
    log(launch.command or " ".join(launch.argv))
    lines: list[str] = []
    done = threading.Event()
    result: dict[str, object] = {"code": -1, "message": ""}

    def on_line(text: str) -> None:
        lines.append(text)
        log(text)
        if cancel is not None and cancel.is_set():
            runner.stop()

    def on_done(code: int, message: str) -> None:
        result["code"] = code
        result["message"] = message
        done.set()

    env = gradle.build_env(False, slow=script is not None, java_home=prepared.java_home)
    runner = gradle.GradleRunner(on_line, on_done)
    runner.start(launch, env)
    if not done.wait(timeout):
        log(f"[超时] Gradle 已运行 {timeout:.0f} 秒，正在结束任务")
        runner.stop()
        done.wait(20)
        return -1, f"超过 {timeout:.0f} 秒仍未结束", lines
    return int(result["code"]), str(result["message"]), lines


def _jars(root: Path) -> list[tuple[str, int]]:
    folder = Path(root) / "build" / "libs"
    if not folder.is_dir():
        return []
    items: list[tuple[str, int]] = []
    for path in sorted(folder.glob("*.jar")):
        if path.is_file():
            items.append((path.relative_to(root).as_posix(), path.stat().st_size))
    return items


def build_project(
    project_root: Path,
    task: str = "build",
    *,
    extra_jar_dirs: list[Path] | None = None,
    log: Log | None = None,
    timeout: float = 1800.0,
    cancel: threading.Event | None = None,
) -> BuildResult:
    """准备环境并真正执行 Gradle，供对话里的助手自己验证编译。"""
    root = Path(project_root)
    if not root.is_dir():
        raise BuilderError(f"工程目录不存在：{root}")
    if task not in gradle.GRADLE_TASKS:
        raise BuilderError(f"不支持的任务：{task}")
    if not gradle.has_gradle_files(root):
        raise BuilderError(
            "工程里没有 build.gradle / settings.gradle，先用 write_file 写出构建配置。"
        )
    logger: Log = log or (lambda _text: None)

    fixed = project_mod.repair_fences(root)
    for item in fixed:
        logger(f"[预检] 已清理代码围栏：{item}")

    logger("=== 准备构建环境 ===")
    prepared = toolchain.prepare(
        root, log=logger, allow_download=True, extra_jar_dirs=extra_jar_dirs or []
    )
    if prepared.java_home:
        logger(f"[准备] Java {prepared.java_version} · {prepared.java_home}")
    for note in prepared.notes:
        logger(f"[准备] 注意：{note}")

    script: Path | None = None
    try:
        script = mirrors.service(logger).init_script()
        logger(f"[镜像] 已启用本地 Maven 代理：{mirrors.service().base_url}")
    except Exception as exc:  # noqa: BLE001 - 代理起不来就退回直连
        logger(f"[镜像] 本地代理不可用，改为直连：{exc}")

    logger(f"=== {gradle.GRADLE_TASKS[task]}（{task}）{time.strftime('%H:%M:%S')} ===")
    code, message, lines = _run(
        root, task, prepared, script, logger, timeout, cancel
    )
    output = "\n".join(lines)

    # 缺本地 jar / Gradle 版本不够时，自动补一次再重试，别把问题推回给用户。
    if code != 0:
        retry = False
        if deps.is_missing_jar_error(output):
            jars = deps.ensure_local_jars(
                root, log=logger, extra_dirs=extra_jar_dirs or []
            )
            if jars.changed:
                logger("=== 缺少本地依赖，已补进 libs/，正在重试 ===")
                retry = True
        required = gradle.required_gradle(output)
        if required:
            current = wrapper.current_distribution(root)
            if (
                gradle.version_tuple(current) < gradle.version_tuple(required)
                and gradle.version_tuple(required)
                <= gradle.version_tuple(wrapper.DEFAULT_DISTRIBUTION)
            ):
                try:
                    wrapper.ensure_wrapper(root, required)
                    logger(f"=== Wrapper 已升级到 {required}，正在重试 ===")
                    retry = True
                except Exception as exc:  # noqa: BLE001
                    logger(f"[准备] 升级 Wrapper 失败：{exc}")
        if retry:
            code, message, lines = _run(
                root, task, prepared, script, logger, timeout, cancel
            )
            output = "\n".join(lines)

    return BuildResult(
        ok=code == 0,
        code=code,
        message=message,
        output=output,
        jars=_jars(root) if task == "build" else [],
        prepared=prepared,
    )
