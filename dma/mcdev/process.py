"""通过 gradlew 启动开发服务端/客户端，并管理它们的生命周期。

启动方式、端口、就绪轮询和停止顺序与 yan-mc/minecraft-mod-dev（MIT）保持一致：
- runServer / runClient，端口 39001 / 39002；
- 轮询 /status 直到探针返回对应 side；
- 客户端启动后显式 /connect，失败最多重试 3 次；
- 停止时先发 probe stop/quit，再 taskkill /T /F 兜底。
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import gradle as gradle_mod, probe as probe_mod

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


def _clamp_timeout(value: float | int | None, default: int = 180_000) -> int:
    try:
        number = int(value or default)
    except (TypeError, ValueError):
        number = default
    return max(5000, min(number, 600_000))


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
        timeout_ms: int | None = 180_000,
        wait_join: bool = True,
        join_port: int = 25566,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        if not root.is_dir():
            raise LaunchError(f"工程目录不存在：{root}")
        if kind not in {"server", "client", "both"}:
            raise LaunchError(f"不支持的启动类型：{kind}")
        timeout = _clamp_timeout(timeout_ms)
        results: list[dict] = []
        if kind in {"server", "both"}:
            results.append(self.launch("server", root, timeout))
        if kind in {"client", "both"}:
            if kind == "both":
                # 服务端启动后还有一段世界加载，先让客户端等两秒再握手。
                self._sleep(2.0)
            client = self.launch("client", root, timeout)
            if not wait_join:
                results.append(client)
            else:
                results.append(self._join_client(root, client, timeout, join_port))
        return {"ok": True, "results": results}

    def _join_client(
        self, root: Path, client: dict, timeout: int, join_port: int
    ) -> dict:
        attempts: list[dict] = []
        current = client
        for attempt in range(3):
            if self._cancelled():
                break
            if attempt > 0:
                self._sleep(2.0)
                current = self.launch("client", root, timeout)
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
    def launch(self, kind: str, project_dir: str | Path, timeout_ms: int) -> dict:
        root = Path(str(project_dir)).expanduser()
        port = probe_mod.probe_port(kind)
        if probe_mod.probe_status_once(kind, port, 1.0):
            return {"kind": kind, "alreadyRunning": True, "port": port}
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
        argv = [str(gradlew), task, "--console=plain"]
        if os.name == "nt":
            argv = ["cmd.exe", "/c"] + argv
        self._log_line(f"[mcdev] 启动 {kind}：{' '.join(argv)}")
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
                env=gradle_mod.build_env(),
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
        ready = self._wait_for_probe(kind, port, timeout_ms, process, entry)
        if not ready:
            tail = entry.tail[-2000:]
            self._kill(entry)
            with self._lock:
                self._entries.pop(kind, None)
            raise LaunchError(
                f"探针 {kind} 在 {timeout_ms} ms 内没有就绪。"
                f"\n日志：{log_path}\n最后输出：\n{tail}"
            )
        return {
            "kind": kind,
            "alreadyRunning": False,
            "pid": entry.pid,
            "port": port,
            "logPath": str(log_path),
            "ready": True,
        }

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
