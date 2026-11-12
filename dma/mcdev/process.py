"""通过 gradlew 启动开发服务端/客户端，并管理它们的生命周期。

启动方式、端口、就绪轮询和停止顺序与 yan-mc/minecraft-mod-dev（MIT）保持一致：
- runServer / runClient，端口 39001 / 39002；
- 轮询 /status 直到探针返回对应 side；
- 客户端启动后显式 /connect，失败最多重试 3 次；
- 停止时先发 probe stop/quit，再 taskkill /T /F 兜底。
"""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..gradle import windows_command
from .. import envcheck
from . import gradle as gradle_mod, probe as probe_mod, server_files

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class LaunchError(RuntimeError):
    pass


@dataclass
class LaunchEntry:
    kind: str
    project_dir: str
    pid: int
    port: int
    log_path: str
    started_at: float
    process: subprocess.Popen | None = field(default=None, repr=False)
    running: bool = True
    exit_code: int | None = None
    error: str = ""
    tail: str = ""
    joined: bool | None = None
    last_screen: str = ""

    def status(self) -> dict:
        return {
            "kind": self.kind,
            "pid": self.pid,
            "projectDir": self.project_dir,
            "port": self.port,
            "logPath": self.log_path,
            "startedAt": int(self.started_at * 1000),
            "running": self.running,
            "exitCode": self.exit_code,
            "joined": self.joined,
            "lastScreen": self.last_screen,
            "error": self.error,
        }


def _clamp_timeout(value: float | int | None, default: int = 600_000) -> int:
    try:
        number = int(value or default)
    except (TypeError, ValueError):
        number = default
    return max(5000, min(number, 600_000))


# 没有探针的加载器只能从 Gradle 输出里判断开发端是否真的起来了。
_STARTED_MARKERS = {
    "server": ("Done (", "For help, type", "Starting Minecraft server on"),
    "client": ("Backend library:", "Setting user:", "Sound engine started"),
}


def _looks_started(kind: str, text: str) -> bool:
    markers = _STARTED_MARKERS.get(kind, ())
    return any(marker in text for marker in markers)


def _port_open(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _parse_netstat_pid(text: str, port: int) -> int | None:
    suffix = f":{int(port)}"
    for line in str(text or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(suffix):
            continue
        if parts[3].upper() != "LISTENING":
            continue
        try:
            return int(parts[4])
        except ValueError:
            continue
    return None


def _pid_on_port(port: int) -> int | None:
    if os.name != "nt":
        return None
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return _parse_netstat_pid(result.stdout, port)


class LaunchManager:
    """持有本次会话启动的进程，工坊关闭时统一清理。"""

    def __init__(self, log=None, cancel: threading.Event | None = None) -> None:
        self._entries: dict[str, LaunchEntry] = {}
        self._lock = threading.RLock()
        self._log = log or (lambda _text: None)
        self._cancel = cancel or threading.Event()

    def set_log(self, log) -> None:
        self._log = log or (lambda _text: None)

    # ------------------------------------------------------------------
    def _emit(self, text: str) -> None:
        try:
            self._log(str(text))
        except Exception:  # noqa: BLE001 - 日志失败不影响启动
            pass

    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    def status(self) -> dict:
        with self._lock:
            return {"processes": [entry.status() for entry in self._entries.values()]}

    def get(self, kind: str) -> LaunchEntry | None:
        with self._lock:
            return self._entries.get(kind)

    # ------------------------------------------------------------------
    def prepare(
        self,
        project_dir: str | Path,
        kind: str,
        *,
        timeout_ms: int | None = 600_000,
        wait_join: bool = True,
        join_port: int = 25566,
        wait_probe: bool = True,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        if not root.is_dir():
            raise LaunchError(f"工程目录不存在：{root}")
        if kind not in {"server", "client", "both"}:
            raise LaunchError(f"不支持的启动类型：{kind}")
        timeout = _clamp_timeout(timeout_ms)
        results: list[dict] = []
        if kind in {"server", "both"}:
            results.append(
                self.launch(
                    "server", root, timeout, join_port=join_port, wait_probe=wait_probe
                )
            )
        ok = True
        if kind in {"client", "both"}:
            if kind == "both":
                # 服务端启动后还有一段世界加载，先让客户端等两秒再握手。
                self._sleep(2.0)
            client = self.launch("client", root, timeout, wait_probe=wait_probe)
            if not wait_join:
                # 没有握手就无法确认已经进世界，不能谎报就绪。
                client["joined"] = False
                client["joinVerified"] = False
                client["note"] = "wait_join=false：未验证客户端是否进入世界"
                results.append(client)
                ok = False
            else:
                joined = self._join_client(
                    root, client, timeout, join_port, wait_probe=wait_probe
                )
                joined["joinVerified"] = True
                results.append(joined)
                ok = bool(joined.get("joined") or joined.get("alreadyRunning"))
        return {"ok": ok, "results": results}

    def _join_client(
        self,
        root: Path,
        client: dict,
        timeout: int,
        join_port: int,
        *,
        wait_probe: bool = True,
    ) -> dict:
        attempts: list[dict] = []
        current = client
        for attempt in range(3):
            if self._cancelled():
                break
            if attempt > 0:
                self._sleep(2.0)
                current = self.launch(
                    "client", root, timeout, wait_probe=wait_probe
                )
            self.wait_client_ready(min(timeout, 60_000))
            self.connect_client(join_port)
            joined, screen = self.wait_client_join(min(timeout, 90_000))
            attempts.append(
                {
                    "attempt": attempt + 1,
                    "pid": current.get("pid"),
                    "logPath": current.get("logPath"),
                    "joined": joined,
                    "lastScreen": screen,
                }
            )
            entry = self.get("client")
            if entry is not None:
                entry.joined = joined
                entry.last_screen = screen
            if joined:
                current["joined"] = True
                current["lastScreen"] = screen
                current["attempts"] = attempts
                if attempt > 0:
                    current["retry"] = True
                return current
            self.stop("client")
        return {
            "kind": "client",
            "joined": False,
            "retry": len(attempts) > 1,
            "lastScreen": attempts[-1]["lastScreen"] if attempts else "",
            "attempts": attempts,
        }

    # ------------------------------------------------------------------
    def launch(
        self,
        kind: str,
        project_dir: str | Path,
        timeout_ms: int,
        *,
        join_port: int = 25566,
        wait_probe: bool = True,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        port = probe_mod.probe_port(kind)
        if probe_mod.probe_status_once(kind, port, 1.0):
            return {"kind": kind, "alreadyRunning": True, "port": port}
        # 同一个开发端已经在本次会话里跑着时，不要因为探针缺失又起一份。
        with self._lock:
            existing = self._entries.get(kind)
        if (
            existing is not None
            and existing.running
            and existing.process is not None
            and existing.process.poll() is None
        ):
            return {
                "kind": kind,
                "alreadyRunning": True,
                "pid": existing.pid,
                "port": port,
                "logPath": existing.log_path,
                "probeAvailable": wait_probe,
            }
        server_files_changed: list[str] = []
        if kind == "server":
            # 先读当前配置里的端口，再补齐 eula / server.properties；否则
            # 把端口改成 join_port 后就看不出旧服务端还占着哪个端口。
            previous_port = server_files.server_port(root, join_port)
            server_files_changed = server_files.ensure_server_files(root, join_port)
            if server_files_changed:
                self._emit(
                    f"[mcdev] 已准备服务端运行文件：{'、'.join(server_files_changed)}"
                )
            # 上一次工坊退出后留下的服务端不在 _entries 里；端口已占用时
            # 再启动一份只会得到 Address already in use，然后反复重试。
            game_port = server_files.server_port(root, join_port)
            for candidate in {previous_port, game_port}:
                if _port_open(candidate):
                    return {
                        "kind": kind,
                        "alreadyRunning": True,
                        "port": port,
                        "gamePort": candidate,
                        "serverFiles": server_files_changed,
                        "note": f"端口 {candidate} 已有服务端在监听，没有重复启动。",
                    }
        gradlew = root / ("gradlew.bat" if os.name == "nt" else "gradlew")
        if not gradlew.is_file():
            raise LaunchError(
                f"工程里没有 {gradlew.name}。先让助手补齐 Wrapper 或生成 Forge 工程骨架。"
            )
        log_dir = root / ".mcdev"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"launch-{kind}-{stamp}.log"
        task = "runServer" if kind == "server" else "runClient"
        gradle_mod.check_toolchain(root)
        base_argv = [str(gradlew), task, "--console=plain"]
        base_argv.extend(
            gradle_mod.toolchain_args(
                [str(item) for item in envcheck.java_homes()]
            )
        )
        # 开发端启动同样走 Gradle 依赖解析。ModDevGradle 会在插件内部动态加
        # maven.neoforged.net，直连返回 502 时 runServer 会卡在
        # createMinecraftArtifacts；和 run_gradle 一样必须挂 init script。
        init_script = self._mirror_script()
        for attempt in range(2):
            argv = list(base_argv)
            if init_script is not None:
                argv.extend(["--init-script", str(init_script)])
            self._log_line(f"[mcdev] 启动 {kind}：{' '.join(argv)}")
            argv = windows_command(argv)
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
                    env=gradle_mod.build_env(slow=init_script is not None),
                )
            except OSError as exc:
                raise LaunchError(f"无法启动 gradlew：{exc}") from exc
            entry = LaunchEntry(
                kind=kind,
                project_dir=str(root),
                pid=int(process.pid or 0),
                port=port,
                log_path=str(log_path),
                started_at=time.time(),
                process=process,
            )
            with self._lock:
                self._entries[kind] = entry
            threading.Thread(
                target=self._pump_output, args=(entry, log_path), daemon=True
            ).start()
            if wait_probe:
                ready = self._wait_for_probe(kind, port, timeout_ms, process, entry)
            else:
                ready = self._wait_for_started(kind, timeout_ms, process, entry)
            if ready:
                return {
                    "kind": kind,
                    "alreadyRunning": False,
                    "pid": entry.pid,
                    "port": port,
                    "logPath": str(log_path),
                    "ready": True,
                    "probeAvailable": wait_probe,
                    "mirrored": init_script is not None,
                    "note": (
                        ""
                        if wait_probe
                        else "当前加载器没有游戏内探针，已按进程/日志判断启动；"
                        "运行情况请查看游戏窗口或 run 目录日志。"
                    ),
                }
            tail = entry.tail[-4000:]
            self._kill(entry)
            with self._lock:
                self._entries.pop(kind, None)
            if (
                attempt == 0
                and init_script is None
                and gradle_mod._looks_like_network_failure(tail)
            ):
                # 主动探测没判定为不通，但 Gradle 实际下载失败（证书链/网关
                # 差异）。强制走本地镜像再起一次，别把网络问题报成代码问题。
                self._log_line(
                    "[mcdev] 依赖下载失败，强制切换本地镜像代理后重试一次。"
                )
                init_script = self._mirror_script(force=True)
                if init_script is not None:
                    continue
            reason = (
                f"探针 {kind} 在 {timeout_ms} ms 内没有就绪。"
                if wait_probe
                else f"{kind} 进程在 {timeout_ms} ms 内退出或没有启动起来。"
            )
            raise LaunchError(
                f"{reason}\n日志：{log_path}\n最后输出：\n{tail}"
            )

    def _mirror_script(self, *, force: bool = False) -> Path | None:
        """官方 Maven 不通时返回本地镜像的 Gradle init script。"""
        from .. import mirrors

        try:
            return mirrors.prepare_launch_env(
                log=self._log_line, force=force, fresh_port=force
            )
        except Exception as exc:  # noqa: BLE001 - 镜像失败时退回直连
            self._log_line(f"[mcdev] 本地镜像不可用，继续直连官方仓库：{exc}")
            return None

    def _pump_output(self, entry: LaunchEntry, log_path: Path) -> None:
        process = entry.process
        if process is None or process.stdout is None:
            return
        tail = ""
        try:
            stream = log_path.open("a", encoding="utf-8", newline="")
        except OSError:
            stream = None
        try:
            for line in process.stdout:
                tail = (tail + line)[-8000:]
                entry.tail = tail
                if stream is not None:
                    try:
                        stream.write(line)
                        stream.flush()
                    except OSError:
                        stream = None
            code = process.wait()
        except Exception:  # noqa: BLE001 - 读流失败不影响退出码记录
            code = process.poll()
        finally:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        entry.running = False
        entry.exit_code = code
        self._log_line(f"[mcdev] {entry.kind} 退出，代码 {code}")

    def _wait_for_probe(
        self,
        kind: str,
        port: int,
        timeout_ms: int,
        process: subprocess.Popen,
        entry: LaunchEntry,
    ) -> bool:
        deadline = time.time() + max(1000, timeout_ms) / 1000.0
        while time.time() < deadline:
            if self._cancelled():
                return False
            if probe_mod.probe_status_once(kind, port, 2.0):
                return True
            if process.poll() is not None:
                self._log_line(
                    f"[mcdev] {kind} 探针未就绪，进程已退出（{process.returncode}）"
                )
                return False
            self._sleep(2.0)
        return False

    def _wait_for_started(
        self,
        kind: str,
        timeout_ms: int,
        process: subprocess.Popen,
        entry: LaunchEntry,
    ) -> bool:
        """没有探针时的就绪判断。

        runServer/runClient 是常驻任务，不能用“进程退出”当作成功，也不能
        因为探针端口不存在就把正常运行的游戏杀掉。这里优先认 Gradle 输出
        里的启动标记；到截止时间进程仍活着就按“已启动”处理，交给用户看窗口。
        """
        deadline = time.time() + max(5000, timeout_ms) / 1000.0
        while time.time() < deadline:
            if self._cancelled():
                return False
            if _looks_started(kind, entry.tail):
                return True
            if process.poll() is not None:
                self._log_line(
                    f"[mcdev] {kind} 进程已退出（{process.returncode}），启动失败"
                )
                return False
            self._sleep(1.0)
        if process.poll() is None:
            self._log_line(
                f"[mcdev] {kind} 未出现明确就绪标记，但进程仍在运行；"
                "按已启动处理，请查看游戏窗口或 run 日志确认。"
            )
            return True
        return False

    def wait_client_ready(self, timeout_ms: int) -> bool:
        deadline = time.time() + max(5000, timeout_ms) / 1000.0
        while time.time() < deadline:
            if self._cancelled():
                return False
            try:
                body = probe_mod.probe_json("client", "/status", timeout=2.0)
            except Exception:  # noqa: BLE001 - 客户端还在加载
                self._sleep(0.5)
                continue
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                screen = str(data.get("screen") or "")
                if data.get("player"):
                    return True
                if "TitleScreen" in screen or "AccessibilityOnboardingScreen" in screen:
                    return True
            self._sleep(0.5)
        return False

    def connect_client(self, join_port: int) -> None:
        port = max(1, min(int(join_port or 25566), 65535))
        try:
            probe_mod.probe_json(
                "client",
                f"/connect?host=127.0.0.1&port={port}",
                timeout=10.0,
            )
        except Exception:  # noqa: BLE001 - 已在世界里时 /connect 会失败，等 join 轮询兜底
            pass

    def wait_client_join(self, timeout_ms: int) -> tuple[bool, str]:
        deadline = time.time() + max(5000, timeout_ms) / 1000.0
        last_screen = ""
        while time.time() < deadline:
            if self._cancelled():
                return False, last_screen
            try:
                body = probe_mod.probe_json("client", "/status", timeout=2.0)
            except Exception:  # noqa: BLE001 - 继续等
                self._sleep(2.0)
                continue
            data = body.get("data") if isinstance(body, dict) else None
            if isinstance(data, dict):
                screen = str(data.get("screen") or "")
                last_screen = screen
                if screen.endswith("DisconnectedScreen"):
                    return False, last_screen
                if data.get("player") and not any(
                    name in screen
                    for name in (
                        "ConnectScreen",
                        "DisconnectedScreen",
                        "ReceivingLevelScreen",
                    )
                ):
                    return True, last_screen
            self._sleep(2.0)
        return False, last_screen

    # ------------------------------------------------------------------
    def stop(self, kind: str) -> dict:
        with self._lock:
            entry = self._entries.get(kind)
        if entry is None:
            return {"kind": kind, "stopped": False, "reason": "not tracked"}
        warnings: list[str] = []
        try:
            if kind == "server":
                probe_mod.probe_json(
                    "server",
                    "/command",
                    method="POST",
                    payload={"cmd": "stop"},
                    port=entry.port,
                    timeout=10.0,
                )
            else:
                probe_mod.probe_json(
                    "client", "/quit", method="POST", payload={}, port=entry.port
                )
        except Exception as exc:  # noqa: BLE001 - 探针可能已经断开
            warnings.append(str(exc))
        self._sleep(2.0)
        if entry.running:
            warnings.extend(self._kill(entry))
        with self._lock:
            self._entries.pop(kind, None)
        return {
            "kind": kind,
            "stopped": True,
            "pid": entry.pid,
            "logPath": entry.log_path,
            "warnings": warnings,
        }

    def _kill(self, entry: LaunchEntry) -> list[str]:
        warnings: list[str] = []
        process = entry.process
        if process is None:
            return warnings
        try:
            if os.name == "nt" and entry.pid:
                subprocess.run(
                    ["taskkill", "/PID", str(entry.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=CREATE_NO_WINDOW,
                    timeout=20,
                )
            else:
                process.kill()
        except Exception as exc:  # noqa: BLE001 - 进程可能已经退出
            warnings.append(str(exc))
        if os.name == "nt" and entry.kind == "server":
            # Gradle 启动的游戏进程是守护进程的子进程，taskkill /T 只杀
            # gradlew 那棵树时它可能活下来；按实际监听端口再补一刀。
            port = server_files.server_port(entry.project_dir)
            pid = _pid_on_port(port)
            if pid and pid != entry.pid:
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        creationflags=CREATE_NO_WINDOW,
                        timeout=20,
                    )
                except Exception as exc:  # noqa: BLE001
                    warnings.append(str(exc))
        # 停掉残留的 Gradle daemon：taskkill 只能杀 gradlew 的进程树，
        # daemon 是独立常驻进程，不停掉它「停止」后仍会在后台烧 CPU / 下载。
        try:
            gradle_mod.stop_gradle_daemon(entry.project_dir)
        except Exception as exc:  # noqa: BLE001
            warnings.append(str(exc))
        entry.running = False
        return warnings

    def shutdown(self) -> None:
        with self._lock:
            kinds = list(self._entries)
        for kind in kinds:
            try:
                self.stop(kind)
            except Exception:  # noqa: BLE001 - 关闭时不再抛错
                pass

    @staticmethod
    def _sleep(seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def _log_line(self, text: str) -> None:
        self._emit(text)
