from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import paths, toolchain
from .version import APP_EXE, APP_VERSION, UPDATE_MANIFEST_URL

MAX_MANIFEST_BYTES = 512 * 1024
MAX_UPDATE_BYTES = 512 * 1024 * 1024
MAX_EXTRACT_BYTES = 900 * 1024 * 1024
ProgressCallback = Callable[[int, str], None]

# GitHub 的 release 资产在国内经常只有几十 KB/s。带 SHA-256 校验的加速入口
# 排在最前面，校验不通过会自动退回官方地址，不会把损坏的文件装进去。
UPDATE_ACCELERATORS = ("https://gh-proxy.com/",)


class UpdateError(Exception):
    pass


@dataclass
class UpdateInfo:
    version: str
    download_url: str = ""
    sha256: str = ""
    size: int = 0
    released: str = ""
    page_url: str = ""
    notes: list[str] = field(default_factory=list)
    manifest_url: str = UPDATE_MANIFEST_URL

    @property
    def size_text(self) -> str:
        return format_size(self.size)


def format_size(size: int) -> str:
    value = max(0, int(size or 0))
    if value >= 1024 * 1024:
        return f"{value / 1024 / 1024:.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.0f} KB"
    return f"{value} B"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def can_self_update() -> bool:
    return is_frozen() and os.name == "nt"


def install_dir() -> Path:
    return paths.app_dir()


def updates_dir() -> Path:
    return paths.updates_dir()


def parse_version(value: str) -> tuple[int, ...]:
    text = str(value or "").strip().lstrip("vV")
    match = re.match(r"(\d+(?:\.\d+)*)", text)
    if not match:
        raise UpdateError(f"无法识别版本号：{value!r}")
    parts = [int(item) for item in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(remote: str, current: str) -> bool:
    try:
        return parse_version(remote) > parse_version(current)
    except UpdateError:
        return False


def _notes(value: object) -> list[str]:
    if isinstance(value, str):
        raw = value.splitlines()
    elif isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        return []
    result: list[str] = []
    for line in raw:
        text = str(line).strip().lstrip("-*•").strip()
        if text:
            result.append(text)
    return result[:40]


def _url_join(base: str, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return urllib.parse.urljoin(base, text)


def parse_release(data: dict, manifest_url: str = UPDATE_MANIFEST_URL) -> UpdateInfo:
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确。")
    payload = data.get("mod_agent") if isinstance(data.get("mod_agent"), dict) else data
    version = str(
        payload.get("version")
        or payload.get("latest_version")
        or data.get("latest_version")
        or ""
    ).strip()
    if not version:
        raise UpdateError("更新清单缺少版本号。")
    digest = str(payload.get("sha256") or data.get("sha256") or "").strip().lower()
    if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("更新清单里的 SHA-256 格式不正确。")
    try:
        size = int(payload.get("size") or data.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return UpdateInfo(
        version=version.lstrip("vV"),
        download_url=_url_join(
            manifest_url,
            payload.get("download_url") or payload.get("url") or "",
        ),
        sha256=digest,
        size=max(0, size),
        released=str(payload.get("released") or ""),
        page_url=_url_join(
            manifest_url,
            payload.get("page_url") or payload.get("release_url") or "",
        ),
        notes=_notes(payload.get("notes")),
        manifest_url=manifest_url,
    )


def fetch_manifest(url: str, timeout: int = 20) -> dict:
    text = str(url or "").strip() or UPDATE_MANIFEST_URL
    if urllib.parse.urlparse(text).scheme.lower() not in ("http", "https", "file"):
        raise UpdateError("更新源地址必须以 http://、https:// 或 file:// 开头。")
    request = urllib.request.Request(
        text, headers={"User-Agent": "DarkittenModAgent-Updater/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(MAX_MANIFEST_BYTES + 1)
    except Exception as exc:
        raise UpdateError(f"无法连接更新源：{exc}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise UpdateError("更新清单过大。")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise UpdateError(f"更新清单不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新清单格式不正确。")
    return data


def check_for_update(url: str = UPDATE_MANIFEST_URL, timeout: int = 20) -> UpdateInfo | None:
    manifest_url = str(url or "").strip() or UPDATE_MANIFEST_URL
    release = parse_release(fetch_manifest(manifest_url, timeout), manifest_url)
    if not is_newer(release.version, APP_VERSION):
        return None
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址。")
    if not release.sha256:
        raise UpdateError("更新清单缺少 SHA-256，已停止更新以保护安全。")
    return release


def sha256_file(path: Path, cancel_event: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise UpdateError("已取消。")
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def download_update(
    release: UpdateInfo,
    *,
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if not release.download_url:
        raise UpdateError("更新清单缺少下载地址。")
    if not release.sha256:
        raise UpdateError("更新清单缺少 SHA-256，已停止下载。")
    cancel_event = cancel_event or threading.Event()
    progress = progress_cb or (lambda _p, _m: None)
    folder = updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"DarkittenModAgent-{release.version}.zip"

    if dest.is_file():
        try:
            if sha256_file(dest, cancel_event).lower() == release.sha256.lower():
                progress(100, "更新包已存在且校验通过")
                return dest
        except UpdateError:
            raise
        except Exception:
            pass
        _remove(dest)

    progress(1, f"正在下载 {release.version}…")
    mirrors = [
        prefix + release.download_url
        for prefix in UPDATE_ACCELERATORS
        if release.download_url.startswith("https://github.com/")
    ]

    def on_progress(done: int, total: int) -> None:
        if cancel_event.is_set():
            return
        if total:
            percent = max(1, min(99, int(done * 100 / total)))
            progress(
                percent,
                f"{done / 1024 / 1024:.1f}/{total / 1024 / 1024:.1f} MB",
            )
        else:
            progress(50, f"已下载 {done / 1024 / 1024:.1f} MB")

    try:
        toolchain.download(
            release.download_url,
            dest,
            sha256=release.sha256,
            mirrors=mirrors,
            progress=on_progress,
            cancel=cancel_event,
        )
    except toolchain.DownloadCancelled as exc:
        _remove(dest)
        raise UpdateError("已取消。") from exc
    except Exception as exc:
        _remove(dest)
        raise UpdateError(f"下载失败：{exc}") from exc

    if dest.stat().st_size > MAX_UPDATE_BYTES:
        _remove(dest)
        raise UpdateError("更新包体积异常，已停止。")
    if release.size and dest.stat().st_size != release.size:
        _remove(dest)
        raise UpdateError(
            f"更新包大小不对（期望 {release.size}，实际 {dest.stat().st_size}）。"
        )
    if sha256_file(dest, cancel_event).lower() != release.sha256.lower():
        _remove(dest)
        raise UpdateError("更新包 SHA-256 校验失败，已删除损坏文件。")
    progress(100, "更新包下载完成")
    return dest


def _remove(path: Path) -> None:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _safe_extract(archive: Path, target: Path) -> None:
    target = Path(target)
    try:
        with zipfile.ZipFile(archive) as zf:
            total = sum(int(item.file_size) for item in zf.infolist())
            if total > MAX_EXTRACT_BYTES:
                raise UpdateError("更新包解压后体积异常，已停止。")
            root = target.resolve()
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if not name or name.endswith("/"):
                    continue
                pure = Path(name)
                if pure.is_absolute() or ".." in pure.parts:
                    raise UpdateError("更新包包含不安全的路径。")
                destination = (target / pure).resolve()
                if destination != root and root not in destination.parents:
                    raise UpdateError("更新包包含不安全的路径。")
            zf.extractall(target)
    except UpdateError:
        raise
    except zipfile.BadZipFile as exc:
        raise UpdateError("更新包不是有效的 ZIP 文件。") from exc
    except Exception as exc:
        raise UpdateError(f"解压更新包失败：{exc}") from exc


def _payload_root(staging: Path) -> Path:
    entries = list(Path(staging).iterdir())
    dirs = [item for item in entries if item.is_dir()]
    if len(dirs) == 1 and not any(item.is_file() for item in entries):
        return dirs[0]
    return Path(staging)


def prepare_update(archive: Path, version: str) -> Path:
    archive = Path(archive)
    if not archive.is_file():
        raise UpdateError("更新包不存在。")
    staging = updates_dir() / f"staging-{version or 'latest'}"
    _remove(staging)
    staging.mkdir(parents=True, exist_ok=True)
    _safe_extract(archive, staging)
    payload = _payload_root(staging)
    exe = payload / APP_EXE
    if not exe.is_file():
        raise UpdateError(f"更新包缺少 {APP_EXE}。")
    if not (payload / "_internal").is_dir():
        raise UpdateError("更新包缺少 _internal 目录。")
    try:
        with exe.open("rb") as handle:
            if handle.read(2) != b"MZ":
                raise UpdateError(f"更新包里的 {APP_EXE} 不是有效的 Windows 程序。")
    except OSError as exc:
        raise UpdateError(f"读取更新包失败：{exc}") from exc
    return payload


def apply_script_text() -> str:
    return r'''param(
  [Parameter(Mandatory=$true)][int]$AppPid,
  [Parameter(Mandatory=$true)][string]$Source,
  [Parameter(Mandatory=$true)][string]$Install,
  [Parameter(Mandatory=$true)][string]$ExeName,
  [Parameter(Mandatory=$true)][int]$Restart,
  [Parameter(Mandatory=$true)][string]$LogPath
)

$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) {
  try {
    $dir = Split-Path -Parent $LogPath
    if (-not (Test-Path -LiteralPath $dir)) {
      New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) -Encoding UTF8
  } catch {}
}

function Remove-Path([string]$Path) {
  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
  }
}

function Move-WithRetry([string]$From, [string]$To) {
  for ($i = 0; $i -lt 60; $i++) {
    try {
      Move-Item -LiteralPath $From -Destination $To -Force -ErrorAction Stop
      return $true
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  return $false
}

try {
  Write-Log "waiting for pid $AppPid"
  $deadline = (Get-Date).AddSeconds(180)
  while ((Get-Date) -lt $deadline) {
    if (-not (Get-Process -Id $AppPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 400
  }
  Start-Sleep -Milliseconds 800

  $exe = Join-Path $Install $ExeName
  $internal = Join-Path $Install '_internal'
  $srcExe = Join-Path $Source $ExeName
  $srcInternal = Join-Path $Source '_internal'
  if (-not (Test-Path -LiteralPath $srcExe)) { throw 'new executable is missing' }
  if (-not (Test-Path -LiteralPath $srcInternal)) { throw 'new _internal is missing' }

  $newExe = "$exe.new"
  $newInternal = "$internal.new"
  Remove-Path $newExe
  Remove-Path $newInternal
  Copy-Item -LiteralPath $srcExe -Destination $newExe -Force
  Copy-Item -LiteralPath $srcInternal -Destination $newInternal -Recurse -Force

  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $backup = Join-Path $Install ("data\updates\backup-" + $stamp)
  New-Item -ItemType Directory -Force -Path $backup | Out-Null

  $oldExe = "$exe.old"
  $oldInternal = "$internal.old"
  Remove-Path $oldExe
  Remove-Path $oldInternal

  if (Test-Path -LiteralPath $exe) {
    if (-not (Move-WithRetry $exe $oldExe)) { throw 'cannot move old executable' }
  }
  if (Test-Path -LiteralPath $internal) {
    if (-not (Move-WithRetry $internal $oldInternal)) { throw 'cannot move old _internal' }
  }
  if (-not (Move-WithRetry $newExe $exe)) { throw 'cannot install new executable' }
  if (-not (Move-WithRetry $newInternal $internal)) { throw 'cannot install new _internal' }

  if (Test-Path -LiteralPath $oldExe) {
    Move-Item -LiteralPath $oldExe -Destination (Join-Path $backup $ExeName) -Force -ErrorAction SilentlyContinue
  }
  if (Test-Path -LiteralPath $oldInternal) {
    Move-Item -LiteralPath $oldInternal -Destination (Join-Path $backup '_internal') -Force -ErrorAction SilentlyContinue
  }

  Remove-Path $Source
  if ($Restart -eq 1) {
    Start-Process -FilePath $exe -WorkingDirectory $Install
  }
  exit 0
} catch {
  Write-Log ("ERROR: " + $_.Exception.Message)
  try {
    $exe = Join-Path $Install $ExeName
    $internal = Join-Path $Install '_internal'
    $oldExe = "$exe.old"
    $oldInternal = "$internal.old"
    if ((Test-Path -LiteralPath $oldExe) -and -not (Test-Path -LiteralPath $exe)) {
      Move-Item -LiteralPath $oldExe -Destination $exe -Force
    }
    if ((Test-Path -LiteralPath $oldInternal) -and -not (Test-Path -LiteralPath $internal)) {
      Move-Item -LiteralPath $oldInternal -Destination $internal -Force
    }
    if ($Restart -eq 1 -and (Test-Path -LiteralPath $exe)) {
      Start-Process -FilePath $exe -WorkingDirectory $Install
    }
  } catch {}
  exit 1
}
'''


def write_apply_script() -> Path:
    folder = updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    script = folder / "apply_mod_agent_update.ps1"
    script.write_text(apply_script_text(), encoding="utf-8")
    return script


def _apply_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )


def _apply_command(
    script: Path,
    payload_root: Path,
    install: Path,
    log_path: Path,
    restart: bool,
    pid: int,
) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(script),
        "-AppPid",
        str(pid),
        "-Source",
        str(Path(payload_root).resolve()),
        "-Install",
        str(Path(install).resolve()),
        "-ExeName",
        APP_EXE,
        "-Restart",
        "1" if restart else "0",
        "-LogPath",
        str(log_path.resolve()),
    ]


def read_apply_log(limit: int = 400) -> str:
    path = updates_dir() / "apply.log"
    try:
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
    except OSError:
        return ""
    return " / ".join(lines[-3:])[-limit:]


def _wait_script_started(log_path: Path, timeout: float = 3.0) -> bool:
    deadline = timeout
    while deadline > 0:
        try:
            if log_path.is_file() and log_path.stat().st_size > 0:
                return True
        except OSError:
            pass
        time.sleep(0.15)
        deadline -= 0.15
    return False


_apply_process: subprocess.Popen | None = None


def schedule_apply(
    payload_root: Path,
    *,
    restart: bool = True,
    pid: int | None = None,
) -> Path:
    if not can_self_update():
        raise UpdateError("自动更新只在打包后的 Windows 版本可用。")
    global _apply_process
    install = install_dir()
    script = write_apply_script()
    log_path = updates_dir() / "apply.log"
    _remove(log_path)
    command = _apply_command(
        script,
        payload_root,
        install,
        log_path,
        restart,
        os.getpid() if pid is None else int(pid),
    )
    last_error = ""
    for flags in (_apply_flags(), 0):
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(install),
                creationflags=flags,
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception as exc:
            last_error = str(exc)
            continue
        _apply_process = proc
        if _wait_script_started(log_path):
            return script
        if proc.poll() is None:
            return script
        last_error = "更新脚本没有启动"
    tail = read_apply_log()
    detail = last_error or "未知原因"
    if tail:
        detail = f"{detail}（{tail}）"
    raise UpdateError(f"启动更新脚本失败：{detail}")
