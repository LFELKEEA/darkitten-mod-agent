"""Gradle 包装：构建、GameTest、安装探针。"""

from __future__ import annotations

import concurrent.futures
import os
import re
import socket
import subprocess
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path

from ..gradle import windows_command

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# 没有输出时的兜底：首次构建要跑 NeoFormRuntime 合并/反编译，单个任务
# 静默十几分钟很正常，在线构建放宽到 15 分钟；离线模式 60 秒就判定卡死。
DEFAULT_STALL_TIMEOUT = 900.0
OFFLINE_STALL_TIMEOUT = 60.0
HEARTBEAT_SECONDS = 15.0
PREFLIGHT_TIMEOUT = 3.0
PREFLIGHT_LIMIT = 6
GRADLE_FILES = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
_REPO_RE = re.compile(r"https?://[^\s\"'<>()]+")
_NETWORK_MARKERS = (
    "could not resolve",
    "connection timed out",
    "connect timed out",
    "unknownhost",
    "unknown host",
    "connection refused",
    "network is unreachable",
    "no route to host",
    "could not get resource",
    "ssl handshake",
    "pkix path building failed",
    "certificate",
    "certificate_unknown",
)


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


def build_env(
    *, windows_trust: bool = True, slow: bool = False
) -> dict[str, str]:
    """Gradle 子进程环境：固定 JDK、放宽超时、必要时用 Windows 根证书。

    ``slow=True`` 用在镜像代理模式下：上游镜像可能要几十秒才吐第一个字节，
    Gradle 默认 30 秒的 socket 超时会先断开，必须一起放宽。
    """
    from .. import gradle as core_gradle

    java = _preferred_java()
    return core_gradle.build_env(
        windows_trust,
        java_home=java[0] if java else None,
        slow=slow,
    )


def _command(
    project_dir: Path,
    task: str,
    *,
    offline: bool = False,
    init_script: Path | None = None,
) -> list[str] | str:
    gradlew = project_dir / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not gradlew.is_file():
        raise GradleError(
            f"{project_dir} 里没有 {gradlew.name}，无法执行 Gradle。"
            "先补齐 Wrapper，或让助手按当前加载器生成工程骨架。"
        )
    argv = [str(gradlew), task, "--console=plain"]
    if init_script is not None:
        argv.extend(["--init-script", str(init_script)])
    if offline:
        argv.append("--offline")
    # 工程路径含空格时，cmd /c 会截断命令行；windows_command 用
    # cmd /d /s /c 加一层外层引号，保证整条命令完整传给 gradlew。
    return windows_command(argv)


def _env_number(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "on", "yes"}


def _repository_urls(root: Path) -> list[str]:
    """从 Gradle 脚本里挑出 Maven 仓库地址，用于构建前的连通性检查。"""
    urls: list[str] = []
    for name in GRADLE_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _REPO_RE.finditer(text):
            url = match.group(0).rstrip(".,;")
            if url not in urls:
                urls.append(url)
    return urls


def _host_port(url: str) -> tuple[str, int]:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "", 443
    host = parts.hostname or ""
    if parts.port:
        port = parts.port
    else:
        port = 443 if (parts.scheme or "https").lower() == "https" else 80
    return host, port


_PROBE_CACHE: dict[tuple[str, int], tuple[float, bool]] = {}
_PROBE_TTL = 60.0


def _reachable(host: str, port: int, timeout: float = PREFLIGHT_TIMEOUT) -> bool:
    """带 60 秒缓存，避免连续构建反复等 DNS / 握手。"""
    key = (host, port)
    now = time.time()
    cached = _PROBE_CACHE.get(key)
    if cached and now - cached[0] < _PROBE_TTL:
        return cached[1]
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ok = True
    except OSError:
        ok = False
    _PROBE_CACHE[key] = (now, ok)
    return ok


def preflight(root: Path, log=None) -> dict:
    """检查构建脚本里的仓库是否可达；不可达时用 --offline 避免十分钟空等。

    返回 ``{"urls", "unreachable", "hosts", "offline"}``。设置
    ``DMA_GRADLE_PREFLIGHT=0`` 可跳过检查，``DMA_GRADLE_OFFLINE=1`` 可强制离线。
    """
    forced = _env_flag("DMA_GRADLE_OFFLINE")
    if forced:
        if log:
            log("[gradle] DMA_GRADLE_OFFLINE=1，直接使用 --offline。")
        return {"urls": [], "unreachable": [], "hosts": [], "offline": True}
    if os.environ.get("DMA_GRADLE_PREFLIGHT", "").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return {"urls": [], "unreachable": [], "hosts": [], "offline": False}
    urls = _repository_urls(root)[:PREFLIGHT_LIMIT]
    targets: list[tuple[str, str, int]] = []
    seen: set[tuple[str, int]] = set()
    for url in urls:
        host, port = _host_port(url)
        if not host or (host, port) in seen:
            continue
        seen.add((host, port))
        targets.append((url, host, port))
    unreachable: list[str] = []
    if targets:
        workers = min(4, len(targets))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_reachable, host, port): url
                for url, host, port in targets
            }
            for future in concurrent.futures.as_completed(futures):
                url = futures[future]
                try:
                    ok = bool(future.result())
                except Exception:  # noqa: BLE001 - 探测失败按不可达处理
                    ok = False
                if not ok:
                    unreachable.append(url)
    hosts = sorted({_host_port(url)[0] for url in unreachable if _host_port(url)[0]})
    if log and unreachable:
        log(
            "[gradle] 仓库不可达：" + "、".join(hosts)
            + "；尝试切换本地镜像代理。"
        )
    return {
        "urls": urls,
        "unreachable": unreachable,
        "hosts": hosts,
        "offline": bool(unreachable),
    }


def _network_hint(output: str, probe: dict) -> str:
    hosts = list(probe.get("hosts") or [])
    lowered = (output or "").lower()
    hit = any(marker in lowered for marker in _NETWORK_MARKERS)
    if not hosts and not hit:
        return ""
    if hosts:
        head = "构建卡在依赖下载：" + "、".join(hosts) + " 无法访问。"
    else:
        head = "构建在依赖下载阶段失败。"
    if probe.get("mirrored"):
        tail = "已自动启用本地镜像代理；如果仍然失败，请检查网络或系统代理设置。"
    elif probe.get("offline"):
        tail = "已启用 --offline 快速失败；本地缓存缺失时不会继续空等。"
    else:
        tail = (
            "请让这台机器能连上这些 Maven 仓库（系统代理 / VPN / 镜像），"
            "或在新建工程时改用 Forge 1.20.1 / Fabric。"
        )
    return "\n\n[dma] " + head + tail


def _ensure_gradle_distribution(root: Path, log=None) -> None:
    """官方 Gradle 发行版下不动时，先用国内镜像把它装进 Wrapper 缓存。

    只在工程确实带 Wrapper 配置时才做，避免无关目录触发上百 MB 下载。
    """
    if _env_flag("DMA_GRADLE_SKIP_DIST"):
        return
    wrapper_props = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
    if not wrapper_props.is_file():
        return
    try:
        from .. import toolchain

        toolchain.ensure_distribution(root, log=log, allow_download=True)
    except Exception as exc:  # noqa: BLE001 - 预装失败交给 gradlew 自己处理
        if log:
            log(f"[gradle] 预装 Gradle 失败，交给 gradlew 自行下载：{exc}")


class _Watchdog:
    """独立线程盯着子进程：取消、总超时、长时间无输出都能立刻终止。"""

    def __init__(
        self,
        process: subprocess.Popen,
        *,
        task: str,
        timeout: float,
        stall_timeout: float,
        cancel: threading.Event | None,
        log=None,
    ) -> None:
        self.process = process
        self.task = task
        self.timeout = timeout
        self.stall_timeout = stall_timeout
        self.cancel = cancel
        self.log = log
        self.reason = ""
        self.started = time.time()
        self._last_output = self.started
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def touch(self) -> None:
        self._last_output = time.time()

    def _emit(self, text: str) -> None:
        if not self.log:
            return
        try:
            self.log(text)
        except Exception:  # noqa: BLE001 - 日志回调不能影响构建
            pass

    def _run(self) -> None:
        next_beat = self.started + HEARTBEAT_SECONDS
        while not self._stop.wait(0.5):
            now = time.time()
            if self.cancel is not None and self.cancel.is_set():
                self.reason = "已取消。"
                _kill(self.process)
                return
            if self.timeout > 0 and now - self.started > self.timeout:
                self.reason = f"{self.task} 超过 {int(self.timeout)} 秒，已终止。"
                _kill(self.process)
                return
            quiet = now - self._last_output
            if self.stall_timeout > 0 and quiet > self.stall_timeout:
                self.reason = (
                    f"{self.task} 已有 {int(quiet)} 秒没有任何输出，判定为卡死并终止。"
                    "如果是首次构建，请确认能访问 Maven 仓库；"
                    "也可以设置 DMA_GRADLE_OFFLINE=1 先用本地缓存构建。"
                )
                _kill(self.process)
                return
            if now >= next_beat:
                next_beat = now + HEARTBEAT_SECONDS
                self._emit(
                    f"[gradle] 正在执行 {self.task}：已用 {int(now - self.started)} 秒，"
                    f"最近一次输出在 {int(quiet)} 秒前…"
                )


def run_gradle(
    project_dir: str | Path,
    task: str,
    *,
    timeout: int = 900,
    cancel: threading.Event | None = None,
    log=None,
    stall_timeout: float | None = None,
) -> tuple[int, str]:
    root = Path(str(project_dir)).expanduser()
    if not root.is_dir():
        raise GradleError(f"工程目录不存在：{root}")
    probe = preflight(root, log)
    offline = bool(probe["offline"])
    init_script: Path | None = None
    if not _env_flag("DMA_GRADLE_OFFLINE"):
        # NeoGradle / ModDevGradle 会在插件内部动态添加 maven.neoforged.net，
        # 构建脚本里看不到这些 URL，所以不能只看 preflight 的结果：必须每次
        # 都探测官方仓库。全部可达时 prepare_launch_env 返回 None，不启用镜像。
        from .. import mirrors

        hosts = list(probe.get("hosts") or [])
        init_script = mirrors.prepare_launch_env(log=log, hosts=hosts)
        if init_script is not None:
            offline = False
            probe["mirrored"] = True
            if log:
                log("[gradle] 已启用本地镜像代理，继续在线构建。")
        elif offline and log:
            log("[gradle] 镜像代理不可用，改用 --offline 快速失败。")
    probe["offline"] = offline
    if not offline:
        _ensure_gradle_distribution(root, log)
    argv = _command(root, task, offline=offline, init_script=init_script)
    if log:
        try:
            shown = argv if isinstance(argv, str) else " ".join(argv)
            log(f"[gradle] {shown}")
        except Exception:  # noqa: BLE001 - 日志回调不能影响构建
            pass
    if stall_timeout is None:
        default_stall = OFFLINE_STALL_TIMEOUT if offline else DEFAULT_STALL_TIMEOUT
        stall_timeout = _env_number("DMA_GRADLE_STALL_TIMEOUT", default_stall)
    code, output, reason = _run_once(
        argv,
        root,
        task=task,
        timeout=timeout,
        stall_timeout=stall_timeout,
        cancel=cancel,
        log=log,
        slow=init_script is not None,
    )
    if (
        code != 0
        and reason == ""
        and not _env_flag("DMA_GRADLE_OFFLINE")
        and (
            _looks_like_network_failure(output)
            # 已经走镜像还报“找不到模块”，多半是上游镜像抖动过一次，
            # Gradle 把这一次 503 当成了“模块不存在”。换端口重试。
            or (init_script is not None and _looks_like_missing_module(output))
        )
    ):
        # 探测进程能联网、Gradle 用的 JDK 却因为证书链/网关问题拿不到构件时，
        # 上面的主动探测不会启用镜像。这里按失败输出兜底：强制走镜像再试一次。
        from .. import mirrors

        # fresh_port=True 让镜像换一个端口，Gradle 会把它当成新仓库重新解析，
        # 绕开上次构建按 URL 缓存的“模块不存在”结果。
        retry_script = mirrors.prepare_launch_env(
            log=log, force=True, fresh_port=True
        )
        if retry_script is not None:
            probe["mirrored"] = True
            if log:
                log("[gradle] 上次构建卡在网络/证书上，强制走本地镜像重试一次。")
            retry_argv = _command(root, task, offline=False, init_script=retry_script)
            retry_stall = _env_number(
                "DMA_GRADLE_STALL_TIMEOUT", DEFAULT_STALL_TIMEOUT
            )
            retry_code, retry_output, retry_reason = _run_once(
                retry_argv,
                root,
                task=task,
                timeout=timeout,
                stall_timeout=retry_stall,
                cancel=cancel,
                log=log,
                slow=True,
            )
            code = retry_code
            reason = retry_reason
            output = (output + "\n" + retry_output)[-200_000:]
    if reason:
        raise GradleError(reason + _network_hint(output, probe))
    if code != 0:
        output += _network_hint(output, probe)
    return code, output


def _looks_like_network_failure(output: str) -> bool:
    lowered = (output or "").lower()
    return any(marker in lowered for marker in _NETWORK_MARKERS)


def _looks_like_missing_module(output: str) -> bool:
    lowered = (output or "").lower()
    return "could not find " in lowered or "failed to resolve" in lowered


def _run_once(
    argv,
    root: Path,
    *,
    task: str,
    timeout: int,
    stall_timeout: float,
    cancel: threading.Event | None,
    log,
    slow: bool,
) -> tuple[int, str, str]:
    """跑一次 Gradle，返回 (退出码, 输出, Watchdog 终止原因)。"""
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
            env=build_env(slow=slow),
        )
    except OSError as exc:
        raise GradleError(f"无法启动 gradlew：{exc}") from exc
    watchdog = _Watchdog(
        process,
        task=task,
        timeout=float(timeout),
        stall_timeout=float(stall_timeout),
        cancel=cancel,
        log=log,
    )
    watchdog.start()
    collected: list[str] = []
    total = 0
    cap = 200_000
    code = -1
    try:
        if process.stdout is None:
            raise GradleError("无法读取 gradlew 的输出流。")
        for line in process.stdout:
            watchdog.touch()
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
        try:
            code = process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            _kill(process)
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                code = -1
    finally:
        watchdog.stop()
        try:
            process.stdout.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
    output = "".join(collected)
    if os.name == "nt":
        # Windows 控制台偶尔输出 UTF-8 被当成 GBK，保留原样即可，这里只做换行归一。
        output = output.replace("\r\n", "\n")
    return code, output, watchdog.reason


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
    # GameTest 服务器同样是独立服务器，没有 eula.txt 会立刻退出。
    from . import server_files

    server_files.ensure_eula(project_dir, "gametest")
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
