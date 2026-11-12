"""Automated runtime verification.

Three levels:
- server: headless dedicated server smoke test, scans the real log.
- gametest: loader GameTest server task.
- client: launches the real client, waits for the window, captures a screenshot,
  scans the log, then closes it. The screenshot is handed to a vision model by
  the Skill, which is how we catch missing/magenta textures and broken GUI.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from ..gradle import windows_command
from .build import _java_env, ensure_gradle, forge_flags, gradle_command, local_maven_args
from .util import McmodError, read_json, run, write_json, write_text
from .versions import VersionProfile

SEVERE = [
    "failed to load", "could not load", "exception in thread",
    "missing texture", "unable to load", "unknown recipe", "failed to create",
    "crash report", "mixin apply failed", "no such method",
    "classnotfoundexception", "noclassdeffounderror", "failed to start",
    "unable to launch", "duplicate registration", "already registered",
]
IGNORE = [
    "advancement", "experimental", "deprecated", "unsupported", "legacy",
    "can't keep up", "server tick", "preparing spawn area",
    # Debug lines are diagnostic, not failures. Forge logs benign messages
    # such as "Failed to load resource META-INF/jarjar/metadata.json".
    "[debug]", "does not contain dependency information", "jarjar",
]


def _gradle_prefix(project: Path, profile: VersionProfile) -> list[str]:
    return gradle_command(project) or ensure_gradle(profile)


def _run_task_streaming(
    project: Path,
    profile: VersionProfile,
    task: str,
    *,
    timeout: int,
    stop_marker: str = "Done (",
    stop_command: str = "stop\n",
    extra_args: list[str] | None = None,
) -> dict[str, Any]:
    cmd = [*_gradle_prefix(project, profile), task, *local_maven_args(),
           *forge_flags(profile), "--console=plain"]
    if extra_args:
        cmd += extra_args
    env = os.environ.copy()
    env.update(_java_env(profile))
    logdir = project / ".mcmod" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"{task}-{int(time.time())}.log"
    started = time.time()
    lines: list[str] = []
    marker_seen = False
    stop_sent = False
    stop_at = 0.0
    proc: subprocess.Popen[str] | None = None

    def watchdog() -> None:
        """Kill the run even when the server goes silent.

        Iterating stdout blocks forever when the game stops printing, so the
        timeout check inside the read loop can never fire on a stuck server.
        """
        while proc is not None and proc.poll() is None:
            time.sleep(1)
            now = time.time()
            if now - started > timeout:
                lines.append("[mcmod] hard timeout, terminating run")
                _kill_tree(proc.pid)
                return
            if stop_sent and stop_at and now - stop_at > 90:
                lines.append("[mcmod] stop did not take effect in 90s, terminating run")
                _kill_tree(proc.pid)
                return

    try:
        proc = subprocess.Popen(
            windows_command(cmd), cwd=str(project), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
            env=env, bufsize=1,
        )
        threading.Thread(target=watchdog, daemon=True).start()
        if proc.stdout is None:
            raise RuntimeError("无法读取游戏进程输出。")
        for line in proc.stdout:
            lines.append(line.rstrip("\n"))
            if stop_marker in line:
                marker_seen = True
            if marker_seen and not stop_sent:
                time.sleep(3)
                try:
                    if proc.stdin:
                        proc.stdin.write(stop_command)
                        proc.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
                stop_sent = True
                stop_at = time.time()
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            _kill_tree(proc.pid)
    except Exception as exc:  # noqa: BLE001
        lines.append(f"[mcmod] launch failed: {exc}")
    finally:
        if proc and proc.poll() is None:
            _kill_tree(proc.pid)
    elapsed = time.time() - started
    write_text(logfile, "\n".join(lines) + f"\n\n[elapsed={elapsed:.1f}s marker={marker_seen} stop_sent={stop_sent}]\n")
    severe = []
    for line in lines:
        low = line.lower()
        if any(k in low for k in SEVERE) and not any(i in low for i in IGNORE):
            severe.append(line[:500])
    return {
        "task": task,
        "marker_seen": marker_seen,
        "stop_sent": stop_sent,
        "elapsed": round(elapsed, 1),
        "severe": severe[:40],
        "log": str(logfile),
        "tail": "\n".join(lines[-40:]),
    }


def _kill_tree(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, text=True)
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def verify_server(project: Path, profile: VersionProfile, timeout: int = 600) -> dict[str, Any]:
    run_dir = project / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "eula.txt").write_text("eula=true\n", encoding="utf-8")
    props = [
        "online-mode=false",
        # 26.x rejects an empty flat generator-settings; a normal world is the
        # most version-tolerant smoke-test preset.
        "level-type=normal",
        "level-name=verify_world",
        "generate-structures=false",
        "spawn-protection=0",
        "max-tick-time=-1",
        "view-distance=4",
        "simulation-distance=4",
        "allow-nether=false",
        "enable-command-block=true",
    ]
    (run_dir / "server.properties").write_text("\n".join(props) + "\n", encoding="utf-8")
    # Never pass --args=nogui here. DevLaunch (ModDevGradle) treats the first
    # program argument as the main class; ForgeGradle prepends JavaExec args
    # before --launchTarget, and FML then fails with "Failed to find system
    # mod: minecraft". The dedicated server starts headless without the flag.
    extra: list[str] = []
    result = _run_task_streaming(project, profile, "runServer", timeout=timeout, extra_args=extra)
    ok = result["marker_seen"] and not result["severe"]
    result["ok"] = ok
    result["kind"] = "server"
    write_json(project / ".mcmod" / "verify-server.json", result)
    return result


def _has_gametests(project: Path) -> bool:
    src = project / "src"
    if not src.exists():
        return False
    for p in src.rglob("*.java"):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "@GameTest" in text or "GameTestHelper" in text:
            return True
    return False


def verify_gametest(project: Path, profile: VersionProfile, timeout: int = 900) -> dict[str, Any]:
    if not _has_gametests(project):
        result = {
            "ok": True,
            "skipped": True,
            "kind": "gametest",
            "marker_seen": False,
            "severe": False,
            "reason": "工程中没有 GameTest 用例，跳过游戏测试。",
        }
        write_json(project / ".mcmod" / "verify-gametest.json", result)
        return result
    result = _run_task_streaming(project, profile, "runGameTestServer", timeout=timeout,
                                 stop_marker="All required tests passed", stop_command="stop\n")
    ok = result["marker_seen"] and not result["severe"]
    result["ok"] = ok
    result["kind"] = "gametest"
    write_json(project / ".mcmod" / "verify-gametest.json", result)
    return result


def verify_client(
    project: Path,
    profile: VersionProfile,
    *,
    wait_seconds: int = 50,
    timeout: int = 360,
    quick_play: str = "",
) -> dict[str, Any]:
    if os.name != "nt":
        return {"ok": False, "kind": "client", "skipped": True,
                "reason": "当前客户端冒烟实现依赖 Windows 窗口截图；在 CI/无头环境请用 server/gametest。"}
    cmd = [*_gradle_prefix(project, profile), "runClient", *local_maven_args(),
           *forge_flags(profile), "--console=plain"]
    if quick_play:
        cmd += [f"--args=--quickPlayMultiplayer {quick_play}"]
    env = os.environ.copy()
    env.update(_java_env(profile))
    logdir = project / ".mcmod" / "logs"
    shotdir = project / ".mcmod" / "screenshots"
    logdir.mkdir(parents=True, exist_ok=True)
    shotdir.mkdir(parents=True, exist_ok=True)
    logfile = logdir / f"runClient-{int(time.time())}.log"
    shot = shotdir / f"client-{int(time.time())}.png"
    proc = subprocess.Popen(windows_command(cmd), cwd=str(project), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", env=env, bufsize=1)
    lines: list[str] = []
    reader = threading.Thread(target=_drain, args=(proc.stdout, lines), daemon=True)
    reader.start()
    started = time.time()
    window_seen = False
    baseline = set(_minecraft_window_pids())
    game_pid: int | None = None
    try:
        # Wait for a *new* Minecraft window so we never touch a game the user
        # already had open.
        while time.time() - started < timeout:
            for pid in _minecraft_window_pids():
                if pid not in baseline:
                    game_pid = pid
                    break
            if game_pid is not None:
                window_seen = True
                break
            if proc.poll() is not None:
                break
            time.sleep(2)
        if window_seen and game_pid is not None:
            time.sleep(max(5, wait_seconds))
            _capture_window(project, shot, game_pid)
    finally:
        if game_pid is not None:
            _close_minecraft_window(game_pid)
            time.sleep(3)
        if proc.poll() is None:
            _kill_tree(proc.pid)
    # Also read the log file written by Gradle/Minecraft, which is more reliable.
    run_log = project / "run" / "logs" / "latest.log"
    if run_log.exists():
        file_lines = run_log.read_text(encoding="utf-8", errors="replace").splitlines()
        lines.extend(file_lines)
    write_text(logfile, "\n".join(lines) + f"\n\n[elapsed={time.time()-started:.1f}s window={window_seen}]\n")
    severe = []
    for line in lines:
        low = line.lower()
        if any(k in low for k in SEVERE) and not any(i in low for i in IGNORE):
            severe.append(line[:500])
    shot_ok, shot_stats = _screenshot_looks_alive(shot)
    result = {
        "ok": window_seen and shot.exists() and shot_ok and not severe,
        "kind": "client",
        "window_seen": window_seen,
        "screenshot": str(shot) if shot.exists() else "",
        "screenshot_stats": shot_stats,
        "severe": severe[:40],
        "log": str(logfile),
        "tail": "\n".join(lines[-60:]),
    }
    write_json(project / ".mcmod" / "verify-client.json", result)
    return result


def _screenshot_looks_alive(path: Path) -> tuple[bool, dict[str, Any]]:
    """Reject fully black/blank captures. Pillow is optional; skip if absent."""
    if not path.exists():
        return False, {}
    try:
        from PIL import Image, ImageStat  # type: ignore
    except Exception:
        return True, {}
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            stat = ImageStat.Stat(im)
            mean = sum(stat.mean) / 3.0
            std = sum(stat.stddev) / 3.0
            stats = {"size": list(im.size), "mean": round(mean, 1), "stddev": round(std, 1)}
        return (mean > 8.0 or std > 8.0), stats
    except Exception:
        return True, {}


def _drain(pipe, sink: list[str]) -> None:
    if pipe is None:
        return
    try:
        for line in pipe:
            sink.append(line.rstrip("\n"))
    except (OSError, ValueError):
        pass


def _minecraft_window_pids() -> list[int]:
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-Process | Where-Object { $_.MainWindowTitle -match 'Minecraft' } | ForEach-Object { $_.Id }"
    )
    res = run(["powershell", "-NoProfile", "-Command", script], timeout=15)
    pids = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _close_minecraft_window(pid: int) -> None:
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        f"$p=Get-Process -Id {pid};"
        "if($p){ $p.CloseMainWindow() | Out-Null }"
    )
    run(["powershell", "-NoProfile", "-Command", script], timeout=15)


def _capture_window(project: Path, out: Path, pid: int) -> None:
    ps1 = project / ".mcmod" / "capture_window.ps1"
    write_text(ps1, r'''
param([string]$Out, [int]$TargetPid)
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32 {
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int x, int y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
# Make GetWindowRect and CopyFromScreen share the same physical-pixel space
# on HiDPI displays; otherwise the capture is offset by the scale factor.
[Win32]::SetProcessDPIAware() | Out-Null
$p = Get-Process -Id $TargetPid
if (-not $p) { Write-Error "Minecraft window not found"; exit 1 }
$h = $p.MainWindowHandle
if ([Win32]::IsIconic($h)) { [Win32]::ShowWindow($h, 9) | Out-Null }   # SW_RESTORE
# Raise above other windows so CopyFromScreen captures the game, not the IDE.
[Win32]::SetWindowPos($h, [IntPtr]::new(-1), 0, 0, 0, 0, 0x0003) | Out-Null  # HWND_TOPMOST, NOSIZE|NOMOVE
[Win32]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 1500
$rect = New-Object 'Win32+RECT'
[Win32]::GetWindowRect($p.MainWindowHandle, [ref]$rect) | Out-Null
$w = $rect.Right - $rect.Left
$h = $rect.Bottom - $rect.Top
if ($w -le 0 -or $h -le 0) { Write-Error "Invalid window size"; exit 1 }
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
[Win32]::SetWindowPos($h, [IntPtr]::new(-2), 0, 0, 0, 0, 0x0003) | Out-Null  # HWND_NOTOPMOST
Write-Output $Out
''')
    run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
         "-Out", str(out), "-TargetPid", str(pid)], timeout=60)


def summarize(result: dict[str, Any]) -> str:
    kind = result.get("kind", "verify")
    if result.get("skipped"):
        return f"{kind} 验证跳过：{result.get('reason', '')}"
    status = "通过" if result.get("ok") else "未通过"
    lines = [f"{kind} 验证：{status}"]
    if result.get("log"):
        lines.append(f"日志：{result['log']}")
    if result.get("screenshot"):
        lines.append(f"截图：{result['screenshot']}")
    if result.get("screenshot_stats"):
        lines.append(f"截图统计：{result['screenshot_stats']}")
    if result.get("severe"):
        lines.append("严重日志：")
        lines += [f"  - {x}" for x in result["severe"][:12]]
    return "\n".join(lines)
