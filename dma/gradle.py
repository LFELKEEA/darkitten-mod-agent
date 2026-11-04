from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

GRADLE_TASKS = {
    "build": "编译",
    "runClient": "开发客户端",
    "runServer": "开发服务端",
    "runDatagen": "数据生成",
}

CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

BUILD_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")

WINDOWS_TRUST_OPTION = "-Djavax.net.ssl.trustStoreType=Windows-ROOT"

CERT_ERROR_MARKERS = (
    "PKIX path building failed",
    "unable to find valid certification path",
    "SSLHandshakeException",
)


def is_cert_error(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in CERT_ERROR_MARKERS)


_GRADLE_REQUIRED = (
    re.compile(r"requires at least Gradle (\d+(?:\.\d+){0,2})", re.IGNORECASE),
    re.compile(r"Minimum supported Gradle version is (\d+(?:\.\d+){0,2})", re.IGNORECASE),
)


def required_gradle(text: str) -> str:
    """从报错里取出插件要求的 Gradle 版本，取不到返回空字符串。"""
    for pattern in _GRADLE_REQUIRED:
        match = pattern.search(text or "")
        if match:
            return match.group(1)
    return ""


def version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for item in (value or "").split("."):
        try:
            parts.append(int(item))
        except ValueError:
            break
    return tuple(parts)


def build_env(windows_trust: bool = False, base: dict | None = None) -> dict[str, str]:
    """给 Gradle / Wrapper 准备环境变量。

    加速器和公司代理常用自己的根证书做 HTTPS 拦截，JDK 自带的 cacerts 不认，
    会出现 PKIX path building failed。Windows 证书库里一般已经装了这张根证书，
    所以这里可以通过 JAVA_OPTS 让 JVM 改用 Windows 证书库。
    """
    env = dict(os.environ if base is None else base)
    if not windows_trust or os.name != "nt":
        return env
    current = env.get("JAVA_OPTS", "").strip()
    if WINDOWS_TRUST_OPTION in current:
        return env
    env["JAVA_OPTS"] = f"{current} {WINDOWS_TRUST_OPTION}".strip()
    return env


class GradleError(Exception):
    pass


@dataclass
class Launch:
    argv: list[str]
    cwd: Path
    task: str
    source: str


def find_wrapper(project_root: Path | None) -> Path | None:
    if project_root is None:
        return None
    for name in ("gradlew.bat", "gradlew"):
        item = Path(project_root) / name
        if item.is_file():
            return item
    return None


def has_gradle_files(project_root: Path | None) -> bool:
    if project_root is None:
        return False
    root = Path(project_root)
    return any((root / name).is_file() for name in BUILD_FILES)


def resolve_launch(project_root: Path, task: str, *, system: str | None = None) -> Launch:
    if task not in GRADLE_TASKS:
        raise GradleError(f"不支持的任务：{task}")
    root = Path(project_root)
    if not root.is_dir():
        raise GradleError(f"工程目录不存在：{root}")
    wrapper = find_wrapper(root)
    if wrapper is not None:
        if os.name == "nt":
            argv = ["cmd", "/c", str(wrapper), task]
        else:
            argv = [str(wrapper), task]
        return Launch(argv=argv, cwd=root, task=task, source="wrapper")
    exe = (shutil.which("gradle") or "") if system is None else system
    if exe:
        return Launch(argv=[exe, task], cwd=root, task=task, source="system")
    raise GradleError(
        "工程里没有 gradlew，系统 PATH 里也没有 gradle。"
        "点「生成 Wrapper」补齐 Wrapper，或安装 Gradle 8.x 并加入 PATH。"
    )


def decode_line(raw: bytes) -> str:
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


class GradleRunner:
    """在后台线程跑 Gradle，逐行把输出交给界面。"""

    def __init__(self, on_line: Callable[[str], None], on_done: Callable[[int, str], None]) -> None:
        self._on_line = on_line
        self._on_done = on_done
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, launch: Launch, env: dict[str, str] | None = None) -> None:
        if self.running:
            raise GradleError("已有 Gradle 任务在运行。")
        self._cancel.clear()
        self._thread = threading.Thread(target=self._pump, args=(launch, env), daemon=True)
        self._thread.start()

    def _pump(self, launch: Launch, env: dict[str, str] | None = None) -> None:
        try:
            proc = subprocess.Popen(
                launch.argv,
                cwd=str(launch.cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            self._thread = None
            self._on_done(-1, str(exc))
            return
        self._proc = proc
        if self._cancel.is_set():
            self._kill(proc)
        stream = proc.stdout
        if stream is not None:
            for raw in iter(stream.readline, b""):
                text = decode_line(raw).rstrip("\r\n")
                if text:
                    self._on_line(text)
            stream.close()
        code = proc.wait()
        self._proc = None
        self._thread = None
        self._on_done(code, "")

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            try:
                proc.terminate()
            except OSError:
                pass

    def stop(self) -> None:
        self._cancel.set()
        proc = self._proc
        if proc is not None:
            self._kill(proc)
