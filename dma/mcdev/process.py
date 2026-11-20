"""通过 gradlew 启动开发服务端/客户端，并管理它们的生命周期。

启动方式、端口、就绪轮询和停止顺序与 yan-mc/minecraft-mod-dev（MIT）保持一致：
- runServer / runClient，端口 39001 / 39002；
- 轮询 /status 直到探针返回对应 side；
- 客户端启动后显式 /connect，失败最多重试 3 次；
- 停止时先发 probe stop/quit，再 taskkill /T /F 兜底。
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import threading
import time
from ctypes import wintypes
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
    background: bool = False
    # 后台客户端所在的隐藏 Win32 桌面；普通启动为空。
    desktop_name: str = ""
    desktop_handle: int = 0

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
            "background": self.background,
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

# 启动日志多久没有任何新输出就判定为卡死（依赖解析/连通性检查挂起）。
# 正常 runServer/runClient 每个阶段都会持续打印，5 分钟静默一定是异常。
_STALL_SECONDS = 300.0


def _looks_started(kind: str, text: str) -> bool:
    markers = _STARTED_MARKERS.get(kind, ())
    return any(marker in text for marker in markers)


def _newest_mtime(base: Path, patterns: tuple[str, ...]) -> float:
    newest = 0.0
    if not base.exists():
        return newest
    for pattern in patterns:
        try:
            for item in base.rglob(pattern):
                if not item.is_file():
                    continue
                try:
                    newest = max(newest, item.stat().st_mtime)
                except OSError:
                    continue
        except OSError:
            continue
    return newest


def project_needs_rebuild(root: str | Path) -> bool:
    """开发端是否比当前源码旧。

    runServer/runClient 只在启动时编译一次。源码改过之后继续复用还在跑的
    开发端，验证的其实是上一版 class，结果看起来一切正常但和代码对不上。
    这里比较源码与编译产物的 mtime，由调用方决定是否先停掉重启。
    """
    root = Path(str(root))
    sources = _newest_mtime(root / "src" / "main" / "java", ("*.java", "*.kt"))
    sources = max(
        sources,
        _newest_mtime(
            root / "src" / "main" / "resources", ("*.json", "*.toml", "*.mcmeta", "*.properties")
        ),
    )
    for name in (
        "build.gradle",
        "build.gradle.kts",
        "settings.gradle",
        "settings.gradle.kts",
        "gradle.properties",
    ):
        path = root / name
        if path.is_file():
            try:
                sources = max(sources, path.stat().st_mtime)
            except OSError:
                pass
    if sources <= 0:
        return False
    built = _newest_mtime(root / "build" / "classes" / "java" / "main", ("*.class",))
    built = max(built, _newest_mtime(root / "build" / "resources" / "main", ("*",)))
    if built <= 0:
        return True
    return sources > built + 1.0


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


# ----------------------------------------------------------------------
# 后台启动客户端：游戏窗口会自己抢焦点，启动完成后把焦点还给用户原来的窗口。
# 不模拟任何鼠标/键盘事件，只切前台窗口。
# ----------------------------------------------------------------------
def foreground_window() -> int:
    if os.name != "nt":
        return 0
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except (AttributeError, OSError, ValueError):
        return 0


def _glfw_windows() -> list[int]:
    """当前所有可见的 GLFW 窗口句柄（Minecraft 客户端用它开窗）。"""
    if os.name != "nt":
        return []
    try:
        user32 = ctypes.windll.user32
        rows: list[int] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _visit(hwnd, _lparam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                buffer = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, buffer, 64)
                if buffer.value.startswith("GLFW"):
                    rows.append(int(hwnd))
            except (OSError, ValueError):
                pass
            return True

        user32.EnumWindows(_visit, 0)
        return rows
    except (AttributeError, OSError):
        return []


def _force_foreground(hwnd: int) -> bool:
    """把指定窗口切到前台。

    Windows 只允许当前前台线程改前台窗口，所以先 AttachThreadInput 到游戏
    线程，切完立刻解绑；这里不发送输入事件，不会动鼠标。
    """
    if os.name != "nt" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.IsWindow(hwnd):
            return False
        current = user32.GetForegroundWindow()
        current_thread = user32.GetWindowThreadProcessId(current, None)
        my_thread = kernel32.GetCurrentThreadId()
        attached = False
        if current_thread and current_thread != my_thread:
            attached = bool(
                user32.AttachThreadInput(current_thread, my_thread, True)
            )
        try:
            user32.SetForegroundWindow(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, my_thread, False)
        return int(user32.GetForegroundWindow() or 0) == int(hwnd)
    except (AttributeError, OSError):
        return False


def _restore_focus(
    previous: int,
    baseline: set[int],
    *,
    timeout: float = 90.0,
    poll: float = 0.5,
    settle: float = 0.8,
) -> None:
    """等新 GLFW 窗口出现，如果它抢了前台，就把前台还给 previous。"""
    if not previous:
        return
    deadline = time.time() + max(0.0, timeout)
    while time.time() < deadline:
        time.sleep(poll)
        fresh = [hwnd for hwnd in _glfw_windows() if hwnd not in baseline]
        if not fresh:
            continue
        # GLFW 创建窗口后会再抢一次焦点，等它稳定下来再判断。
        time.sleep(settle)
        if foreground_window() in fresh:
            _force_foreground(previous)
        return


def _show_window(hwnd: int, visible: bool) -> bool:
    """直接显示/隐藏窗口，不激活、不抢焦点。

    这是探针内置隐藏之外的兜底：即使游戏还在初始化、探针尚未加载，也能
    在窗口刚创建时把它从桌面和任务栏上收走。
    """
    if os.name != "nt" or not hwnd:
        return False
    try:
        user32 = ctypes.windll.user32
        # 未声明 argtypes 时 ctypes 会把 Python int 当 32 位 C int，
        # 64 位 HWND 会被截断，导致 ShowWindow 作用到错误句柄。
        handle = wintypes.HWND(hwnd)
        if not user32.IsWindow(handle):
            return False
        # Forge 的早期加载窗口属于另一个线程；同步 ShowWindow 可能等它处理
        # 消息而卡住，改用异步版本，让窗口线程尽快执行隐藏。
        try:
            user32.ShowWindowAsync(handle, 5 if visible else 0)
        except AttributeError:
            user32.ShowWindow(handle, 5 if visible else 0)
        return True
    except (AttributeError, OSError, ValueError):
        return False


# ----------------------------------------------------------------------
# Windows 隐藏桌面：独立桌面上的窗口永远不会映射到用户当前桌面。
# 之前仅靠 GLFW_VISIBLE / ShowWindow 隐藏，窗口创建本身仍会在用户桌面
# 闪一下（DummyProvider 会忽略可见性 hint）。把游戏进程放到隐藏桌面后，
# 从 CreateWindow 开始就不与用户桌面产生任何可见窗口。
# ----------------------------------------------------------------------
_STARTF_USESHOWWINDOW = 0x00000001
_SW_HIDE = 0
_DESKTOP_ALL_ACCESS = 0x000F01FF


def _create_hidden_desktop(tag: str) -> tuple[str, int]:
    """在当前窗口站里创建一个新的隐藏桌面，返回 (名称, 句柄)。"""
    if os.name != "nt":
        return "", 0
    name = f"McdevBg-{os.getpid()}-{tag}-{int(time.time() * 1000)}"
    try:
        user32 = ctypes.windll.user32
        user32.CreateDesktopW.restype = wintypes.HANDLE
        user32.CreateDesktopW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
        ]
        raw = user32.CreateDesktopW(
            name, None, None, 0, _DESKTOP_ALL_ACCESS, None
        )
        value = int(getattr(raw, "value", raw) or 0)
        return (name, value) if value else ("", 0)
    except (AttributeError, OSError, ValueError, TypeError):
        return "", 0


def _close_desktop_handle(handle: int) -> None:
    if os.name != "nt" or not handle:
        return
    try:
        user32 = ctypes.windll.user32
        user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        user32.CloseDesktop.restype = wintypes.BOOL
        user32.CloseDesktop(wintypes.HANDLE(int(handle)))
    except (AttributeError, OSError, ValueError, TypeError):
        pass


def _release_hidden_desktop(entry: LaunchEntry) -> None:
    """关闭隐藏桌面句柄；进程已退出后再关，失败也只是句柄泄漏。"""
    handle = int(entry.desktop_handle or 0)
    entry.desktop_handle = 0
    entry.desktop_name = ""
    _close_desktop_handle(handle)


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class _DesktopProcess:
    """在隐藏桌面上启动的进程，接口只覆盖 subprocess.Popen 被用到的那几项。"""

    def __init__(self, handle: int, pid: int, stdout) -> None:
        self._handle = int(handle or 0)
        self.pid = int(pid or 0)
        self.stdout = stdout
        self.returncode: int | None = None

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if not self._handle:
            return 0
        kernel32 = ctypes.windll.kernel32
        if kernel32.WaitForSingleObject(wintypes.HANDLE(self._handle), 0) == 0x00000102:
            return None  # WAIT_TIMEOUT
        code = wintypes.DWORD()
        kernel32.GetExitCodeProcess(
            wintypes.HANDLE(self._handle), ctypes.byref(code)
        )
        self.returncode = int(code.value)
        self._close_handle()
        return self.returncode

    def wait(self) -> int:
        if self._handle:
            ctypes.windll.kernel32.WaitForSingleObject(
                wintypes.HANDLE(self._handle), 0xFFFFFFFF
            )
        return self.poll() or 0

    def kill(self) -> None:
        if self._handle:
            try:
                ctypes.windll.kernel32.TerminateProcess(
                    wintypes.HANDLE(self._handle), 1
                )
            except OSError:
                pass

    terminate = kill

    def _close_handle(self) -> None:
        handle, self._handle = self._handle, 0
        if handle:
            try:
                ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(handle))
            except OSError:
                pass

    def __del__(self) -> None:  # pragma: no cover - 兜底释放句柄
        try:
            self._close_handle()
        except Exception:  # noqa: BLE001
            pass


def _spawn_on_hidden_desktop(
    argv: list[str] | str,
    cwd: str,
    env: dict[str, str] | None,
    desktop_name: str,
) -> _DesktopProcess:
    """用 CreateProcessW 把子进程（含后代）放到指定桌面。

    subprocess 的 STARTUPINFO 不暴露 lpDesktop，直接 setattr 只会挂在
    Python 对象上、根本不会传给 CreateProcess，所以这里自己调用 Win32。
    """
    import msvcrt

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]

    read_fd, write_fd = os.pipe()
    os.set_inheritable(write_fd, True)
    nul_fd = os.open(os.devnull, os.O_RDONLY)
    os.set_inheritable(nul_fd, True)

    command_line = (
        argv
        if isinstance(argv, str)
        else subprocess.list2cmdline([str(item) for item in argv])
    )
    cmd_buffer = ctypes.create_unicode_buffer(command_line)

    env_buffer = None
    if env is not None:
        items = sorted(
            ((str(k), str(v)) for k, v in env.items()),
            key=lambda kv: kv[0].upper(),
        )
        env_buffer = ctypes.create_unicode_buffer(
            "".join(f"{key}={value}\0" for key, value in items) + "\0"
        )

    startup = _STARTUPINFOW()
    startup.cb = ctypes.sizeof(_STARTUPINFOW)
    startup.lpDesktop = desktop_name
    startup.dwFlags = _STARTF_USESHOWWINDOW | 0x00000100  # STARTF_USESTDHANDLES
    startup.wShowWindow = _SW_HIDE
    startup.hStdInput = wintypes.HANDLE(msvcrt.get_osfhandle(nul_fd))
    startup.hStdOutput = wintypes.HANDLE(msvcrt.get_osfhandle(write_fd))
    startup.hStdError = startup.hStdOutput
    info = _PROCESS_INFORMATION()

    flags = CREATE_NO_WINDOW | 0x00000400  # CREATE_UNICODE_ENVIRONMENT
    created = False
    try:
        created = bool(
            kernel32.CreateProcessW(
                None,
                cmd_buffer,
                None,
                None,
                True,
                flags,
                ctypes.cast(env_buffer, ctypes.c_void_p) if env_buffer else None,
                cwd,
                ctypes.byref(startup),
                ctypes.byref(info),
            )
        )
        error = ctypes.get_last_error()
    finally:
        os.close(write_fd)
        os.close(nul_fd)
    if not created:
        os.close(read_fd)
        raise OSError(error, "CreateProcessW 失败")
    if info.hThread:
        kernel32.CloseHandle(info.hThread)
    stdout = os.fdopen(read_fd, "r", encoding="utf-8", errors="replace")
    return _DesktopProcess(int(info.hProcess or 0), int(info.dwProcessId), stdout)


def _hide_new_windows(baseline: set[int], entry: LaunchEntry) -> None:
    """持续把新出现的 GLFW 窗口隐藏，直到进程退出。

    游戏只要在跑就可能重新创建/显示窗口（切换全屏、崩溃恢复等）。这里不
    模拟键鼠，只对 GLFW 主窗口调用 ShowWindow(SW_HIDE)。
    """
    started = time.time()
    while entry.running:
        # 不记录“已隐藏过”：GLFW/游戏可能在初始化、切全屏或恢复时把同一个
        # 句柄重新显示出来，必须每次都重新压下去，直到进程退出。
        for hwnd in _glfw_windows():
            if hwnd not in baseline:
                _show_window(hwnd, False)
        # 启动阶段用高频轮询，把窗口“闪现”压到几十毫秒内；稳定后降频省 CPU。
        time.sleep(0.03 if time.time() - started < 30.0 else 0.25)


class LaunchManager:
    """持有本次会话启动的进程，工坊关闭时统一清理。"""

    def __init__(self, log=None, cancel: threading.Event | None = None) -> None:
        self._entries: dict[str, LaunchEntry] = {}
        self._lock = threading.RLock()
        self._log = log or (lambda _text: None)
        self._cancel = cancel or threading.Event()

    def set_log(self, log) -> None:
        self._log = log or (lambda _text: None)

    def set_cancel(self, cancel: threading.Event | None) -> threading.Event:
        """切换取消信号；返回切换前的信号，便于调用方用完恢复。"""
        previous = self._cancel
        self._cancel = cancel or threading.Event()
        return previous

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
        background: bool = True,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        if not root.is_dir():
            raise LaunchError(f"工程目录不存在：{root}")
        if kind not in {"server", "client", "both"}:
            raise LaunchError(f"不支持的启动类型：{kind}")
        timeout = _clamp_timeout(timeout_ms)
        started_at = time.time()
        results: list[dict] = []
        if self._cancelled():
            return {"ok": False, "cancelled": True, "results": results}
        try:
            if kind in {"server", "both"}:
                results.append(
                    self.launch(
                        "server",
                        root,
                        timeout,
                        join_port=join_port,
                        wait_probe=wait_probe,
                        background=background,
                    )
                )
                if self._cancelled():
                    return {"ok": False, "cancelled": True, "results": results}
            ok = True
            if kind in {"client", "both"}:
                if kind == "both":
                    # 服务端启动后还有一段世界加载，先让客户端等两秒再握手。
                    self._sleep(2.0)
                    if self._cancelled():
                        return {"ok": False, "cancelled": True, "results": results}
                client = self.launch(
                    "client", root, timeout, wait_probe=wait_probe,
                    background=background,
                )
                if not wait_join:
                    # 没有握手就无法确认已经进世界，不能谎报就绪。
                    client["joined"] = False
                    client["joinVerified"] = False
                    client["note"] = "wait_join=false：未验证客户端是否进入世界"
                    results.append(client)
                    ok = False
                else:
                    joined = self._join_client(
                        root, client, timeout, join_port,
                        wait_probe=wait_probe, background=background,
                    )
                    joined["joinVerified"] = True
                    results.append(joined)
                    ok = bool(
                        joined.get("joined") or joined.get("alreadyRunning")
                    )
            cancelled = self._cancelled()
            return {
                "ok": ok and not cancelled,
                "cancelled": cancelled,
                "results": results,
            }
        finally:
            if self._cancelled():
                # 取消后必须把自己刚拉起来的开发端一起收掉，否则界面上的
                # “停止”会看起来只是停掉了对话，游戏进程还在后台跑。
                self.stop_started_since(started_at)

    def stop_started_since(self, since: float) -> dict:
        """停止 ``since`` 之后由本管理器启动的开发端。

        用于取消一次启动流程：只清理这一次新起的进程，不动用户之前已经
        通过运行面板启动的开发端。
        """
        with self._lock:
            kinds = [
                entry.kind
                for entry in self._entries.values()
                if entry.started_at >= since - 0.5
            ]
        stopped: list[dict] = []
        for kind in kinds:
            try:
                stopped.append(self.stop(kind))
            except Exception as exc:  # noqa: BLE001 - 清理失败不能掩盖取消
                stopped.append(
                    {"kind": kind, "stopped": False, "reason": str(exc)}
                )
        return {"ok": True, "results": stopped}

    def _join_client(
        self,
        root: Path,
        client: dict,
        timeout: int,
        join_port: int,
        *,
        wait_probe: bool = True,
        background: bool = True,
    ) -> dict:
        attempts: list[dict] = []
        current = client
        for attempt in range(3):
            if self._cancelled():
                break
            if attempt > 0:
                self._sleep(2.0)
                current = self.launch(
                    "client", root, timeout, wait_probe=wait_probe,
                    background=background,
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
            if (
                entry is not None
                and not entry.running
                and entry.exit_code is not None
                and (entry.exit_code or 0) != 0
            ):
                # 客户端启动即崩溃时重试没有意义，只会把同一次崩溃再撞三遍。
                current["joined"] = False
                current["crashed"] = True
                current["exitCode"] = entry.exit_code
                current["attempts"] = attempts
                current["tail"] = entry.tail[-3000:]
                current["note"] = "客户端启动即崩溃，已停止重试；按日志尾部修复原因。"
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
        background: bool = True,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        port = probe_mod.probe_port(kind)
        # 复用旧开发端前先确认它没比源码旧，否则验证的是上一版 class。
        stale = project_needs_rebuild(root)
        stopped_stale = False
        with self._lock:
            existing = self._entries.get(kind)
        tracked_running = (
            existing is not None
            and existing.running
            and existing.process is not None
            and existing.process.poll() is None
        )
        if probe_mod.probe_status_once(kind, port, 1.0):
            if not stale:
                return {"kind": kind, "alreadyRunning": True, "stale": False, "port": port}
            self._log_line(
                f"[mcdev] {kind} 开发端比当前源码旧，先停掉再重启，避免验证旧 class"
            )
            if tracked_running:
                self.stop(kind)
            else:
                self._stop_by_probe(kind, port)
            stopped_stale = True
        elif tracked_running:
            # 同一个开发端已经在本次会话里跑着时，不要因为探针缺失又起一份。
            if not stale:
                return {
                    "kind": kind,
                    "alreadyRunning": True,
                    "stale": False,
                    "pid": existing.pid,
                    "port": port,
                    "logPath": existing.log_path,
                    "probeAvailable": wait_probe,
                }
            self._log_line(
                f"[mcdev] {kind} 开发端比当前源码旧，先停掉再重启，避免验证旧 class"
            )
            self.stop(kind)
            stopped_stale = True
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
            if stopped_stale:
                # 刚停掉的旧服务端还在收尾，端口没释放就报 alreadyRunning
                # 会让调用方以为旧进程又活过来了。
                for candidate in {previous_port, game_port}:
                    self._wait_port_closed(candidate, 30.0)
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
        if kind == "client":
            # 没有探针的加载器只能靠运行前写配置；Forge 1.20.1 之后还能用
            # game_language 热切换，但先把 options.txt 固定住更稳。
            client_changed = server_files.ensure_client_language(root)
            if client_changed:
                self._emit(
                    f"[mcdev] 已把客户端语言预置为中文：{'、'.join(client_changed)}"
                )
            # 后台模式：窗口隐藏 + 失焦不暂停。即使探针还没加载，下面也会
            # 用 Win32 把刚创建的 GLFW 窗口从桌面收走。
            bg_changed = server_files.ensure_client_background(
                root, background=background
            )
            if bg_changed:
                self._emit(
                    "[mcdev] 客户端后台模式：窗口不显示在桌面，失焦不暂停"
                    + ("（已写入配置）" if bg_changed else "")
                )
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
        # 后台客户端不能复用用户桌面上的 Gradle daemon：daemon 是常驻进程，
        # 由它拉起的游戏会继承 daemon 的桌面，隐藏桌面前功尽弃。
        if kind == "client" and background:
            base_argv.append("--no-daemon")
        # 与 build_project 保持一致：中文环境下强制 UTF-8 输出；ForgeGradle 6
        # 在 apply 阶段的服务器连通性检查没有超时，国内网络会静默卡住十几分钟，
        # 表现为 Gradle 只打印两行后完全没输出。
        base_argv.append("-Dfile.encoding=UTF-8")
        if gradle_mod._forge_gradle_project(root):
            base_argv.append("-Dnet.minecraftforge.gradle.check.certs=false")
        base_argv.extend(
            gradle_mod.toolchain_args(
                [str(item) for item in envcheck.java_homes()]
            )
        )
        # 开发端启动同样走 Gradle 依赖解析。ModDevGradle 会在插件内部动态加
        # maven.neoforged.net，直连返回 502 时 runServer 会卡在
        # createMinecraftArtifacts；和 run_gradle 一样必须挂 init script。
        init_script = self._mirror_script()
        focus_before = foreground_window() if kind == "client" else 0
        glfw_before = set(_glfw_windows()) if kind == "client" else set()
        for attempt in range(2):
            argv = list(base_argv)
            if init_script is not None:
                argv.extend(["--init-script", str(init_script)])
            self._log_line(f"[mcdev] 启动 {kind}：{' '.join(argv)}")
            argv = windows_command(argv)
            # 每次尝试都新建桌面：上一次失败时 _kill 已经把它关掉了。
            desktop_name = ""
            desktop_handle = 0
            if kind == "client" and background and os.name == "nt":
                desktop_name, desktop_handle = _create_hidden_desktop(
                    f"{stamp}-{attempt}"
                )
                if desktop_handle:
                    self._log_line(
                        f"[mcdev] 后台客户端运行在隐藏桌面 {desktop_name}"
                    )
                else:
                    self._log_line(
                        "[mcdev] 隐藏桌面创建失败，退回窗口隐藏兜底"
                    )
            env = gradle_mod.build_env(slow=init_script is not None)
            try:
                if desktop_name:
                    # subprocess 不暴露 lpDesktop，隐藏桌面必须自己调 CreateProcess。
                    process = _spawn_on_hidden_desktop(
                        argv, str(root), env, desktop_name
                    )
                else:
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
                        env=env,
                    )
            except OSError as exc:
                _close_desktop_handle(desktop_handle)
                raise LaunchError(f"无法启动 gradlew：{exc}") from exc
            entry = LaunchEntry(
                kind=kind,
                project_dir=str(root),
                pid=int(process.pid or 0),
                port=port,
                log_path=str(log_path),
                started_at=time.time(),
                process=process,
                background=bool(background and kind == "client"),
                desktop_name=desktop_name,
                desktop_handle=desktop_handle,
            )
            with self._lock:
                self._entries[kind] = entry
            threading.Thread(
                target=self._pump_output, args=(entry, log_path), daemon=True
            ).start()
            if kind == "client":
                if background:
                    # 不显示窗口、不抢焦点；从窗口创建的第一时间就隐藏。
                    threading.Thread(
                        target=_hide_new_windows,
                        args=(glfw_before, entry),
                        daemon=True,
                    ).start()
                elif focus_before:
                    # 前台模式：runClient 会把游戏窗口弹到最前并抓走鼠标；
                    # 窗口一出现就把前台还给用户原来的程序。
                    threading.Thread(
                        target=_restore_focus,
                        args=(focus_before, glfw_before),
                        daemon=True,
                    ).start()
            if wait_probe:
                ready = self._wait_for_probe(kind, port, timeout_ms, process, entry)
            else:
                ready = self._wait_for_started(kind, timeout_ms, process, entry)
            if ready:
                if kind == "client" and background:
                    # 第二层保险：探针就绪后再让它自己隐藏一次 GLFW 窗口。
                    try:
                        probe_mod.probe_json(
                            "client", "/window",
                            method="POST", payload={"visible": False},
                            port=port, timeout=8.0,
                        )
                    except Exception as exc:  # noqa: BLE001 - 旧探针没有 /window
                        self._log_line(f"[mcdev] 探针窗口隐藏接口不可用：{exc}")
                return {
                    "kind": kind,
                    "alreadyRunning": False,
                    "pid": entry.pid,
                    "port": port,
                    "logPath": str(log_path),
                    "ready": True,
                    "background": bool(background and kind == "client"),
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

    def _stop_by_probe(self, kind: str, port: int) -> None:
        """停掉不在 _entries 里、但探针仍能应答的旧开发端。

        只走探针自带的退出命令，不动进程树；探针没响应时后面按端口占用
        处理，交给调用方判断，绝不贸然 taskkill 不明进程。
        """
        try:
            if kind == "server":
                probe_mod.probe_json(
                    "server",
                    "/command",
                    method="POST",
                    payload={"cmd": "stop"},
                    port=port,
                    timeout=10.0,
                )
            else:
                probe_mod.probe_json(
                    "client", "/quit", method="POST", payload={}, port=port, timeout=10.0
                )
        except Exception as exc:  # noqa: BLE001 - 旧开发端可能刚好自己退出
            self._log_line(f"[mcdev] 停止旧 {kind} 失败：{exc}")
        self._wait_port_closed(port, 20.0)

    def _wait_port_closed(self, port: int, timeout: float = 20.0) -> bool:
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            if self._cancelled():
                return False
            if not _port_open(port):
                return True
            self._sleep(0.5)
        return not _port_open(port)

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
        last_len = -1
        last_change = time.time()
        while time.time() < deadline:
            if self._cancelled():
                return False
            if probe_mod.probe_status_once(kind, port, 2.0):
                return True
            # 探针没装好或没起来时，日志里的就绪标记同样可信。否则正常启动的
            # 服务端会因为探针端口没人应答，被静默看门狗在 5 分钟后误杀。
            if entry.tail and _looks_started(kind, entry.tail):
                self._log_line(
                    f"[mcdev] {kind} 日志已出现就绪标记（探针未响应），按已启动处理"
                )
                return True
            if process.poll() is not None:
                self._log_line(
                    f"[mcdev] {kind} 探针未就绪，进程已退出（{process.returncode}）"
                )
                return False
            current = len(entry.tail or "")
            if current != last_len:
                last_len = current
                last_change = time.time()
            elif _STALL_SECONDS > 0 and time.time() - last_change > _STALL_SECONDS:
                self._log_line(
                    f"[mcdev] {kind} 启动日志 {int(_STALL_SECONDS)} 秒无新输出，判定为卡住"
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
        last_len = -1
        last_change = time.time()
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
            current = len(entry.tail or "")
            if current != last_len:
                last_len = current
                last_change = time.time()
            elif _STALL_SECONDS > 0 and time.time() - last_change > _STALL_SECONDS:
                self._log_line(
                    f"[mcdev] {kind} 启动日志 {int(_STALL_SECONDS)} 秒无新输出，判定为卡住"
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
            # 客户端已经崩了就别再空等窗口就绪，直接把失败交给上层读日志。
            entry = self.get("client")
            if entry is not None and not entry.running and entry.exit_code is not None:
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
        _release_hidden_desktop(entry)
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
            _release_hidden_desktop(entry)
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
        _release_hidden_desktop(entry)
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
