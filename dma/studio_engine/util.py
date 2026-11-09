"""Shared utilities: HTTP cache, downloads, subprocess, JDK discovery, JSON."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("MCMOD_CACHE", Path.home() / ".mcmod" / "cache"))
TOOLS_DIR = Path(os.environ.get("MCMOD_TOOLS", Path.home() / ".mcmod" / "tools"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
TOOLS_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "mcmod-studio/1.0 (+local build assistant)"
INSECURE = os.environ.get("MCMOD_INSECURE", "0") in {"1", "true", "yes"}
OFFLINE = os.environ.get("MCMOD_OFFLINE", "0") in {"1", "true", "yes"}


class McmodError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(msg, flush=True)


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slug(text: str, fallback: str = "example") -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9_\- ]+", "", text)
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = fallback
    if not text:
        text = "mod"
    if not text[0].isalpha():
        text = "mod_" + text
    return text[:48]


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_path(name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.\-]+", "_", name)
    return CACHE_DIR / safe


def _ssl_context() -> ssl.SSLContext:
    if INSECURE:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def http_get(
    url: str,
    *,
    ttl: int = 24 * 3600,
    timeout: int = 12,
    retries: int = 0,
    cache_name: str | None = None,
    refresh: bool = False,
) -> bytes:
    """GET with a disk cache. Returns bytes. Raises McmodError on failure."""
    cp = cache_path(cache_name or ("http_" + hashlib.sha1(url.encode()).hexdigest()))
    meta = cp.with_suffix(cp.suffix + ".meta.json")
    fail_meta = cp.with_suffix(cp.suffix + ".fail.json")
    if OFFLINE and not cp.exists():
        raise McmodError(f"offline mode: cache miss for {url}")
    if not refresh and cp.exists():
        info = read_json(meta, {}) or {}
        age = time.time() - float(info.get("fetched", 0))
        if age <= ttl:
            return cp.read_bytes()
    if not refresh:
        fail = read_json(fail_meta, {}) or {}
        if time.time() - float(fail.get("failed", 0)) < 600:
            raise McmodError(f"GET failed recently: {url} ({fail.get('error', 'cached failure')})")

    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                data = resp.read()
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(data)
            write_json(meta, {"url": url, "fetched": time.time(), "size": len(data)})
            with contextlib.suppress(OSError):
                fail_meta.unlink()
            return data
        except Exception as exc:  # noqa: BLE001 - network layer normalizes errors
            last_err = exc
            if attempt < retries:
                time.sleep(0.8 * (attempt + 1))
    with contextlib.suppress(OSError):
        write_json(fail_meta, {"url": url, "failed": time.time(), "error": str(last_err)})
    raise McmodError(f"GET failed: {url} ({last_err})")


def http_get_text(url: str, **kwargs: Any) -> str:
    return http_get(url, **kwargs).decode("utf-8", errors="replace")


def http_get_json(url: str, **kwargs: Any) -> Any:
    raw = http_get(url, **kwargs)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise McmodError(f"Invalid JSON from {url}: {exc}") from exc


def download(
    url: str,
    dest: Path,
    *,
    timeout: int = 120,
    mirrors: Iterable[str] = (),
    refresh: bool = False,
) -> Path:
    """Download a binary file with optional mirror fallbacks."""
    dest = Path(dest)
    if dest.exists() and not refresh and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: Exception | None = None
    for candidate in [url, *mirrors]:
        try:
            req = urllib.request.Request(candidate, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                with tempfile.NamedTemporaryFile(delete=False, dir=str(dest.parent)) as tmp:
                    shutil.copyfileobj(resp, tmp)
                    tmp_path = Path(tmp.name)
            if tmp_path.stat().st_size == 0:
                raise McmodError("empty response")
            tmp_path.replace(dest)
            return dest
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise McmodError(f"download failed: {url} ({last})")


def unzip(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


@dataclass
class RunResult:
    cmd: list[str]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def combined(self) -> str:
        return (self.stdout or "") + ("\n" + self.stderr if self.stderr else "")

    def tail(self, lines: int = 80) -> str:
        return "\n".join(self.combined.splitlines()[-lines:])


def run(
    cmd: list[str] | str,
    *,
    cwd: Path | str | None = None,
    timeout: int = 600,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    shell: bool = False,
    stream: bool = False,
) -> RunResult:
    """Run a command, capture output, never raise on non-zero exit."""
    started = time.time()
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    if stream and not input_text:
        return _run_streaming(cmd, cwd=cwd, timeout=timeout, env=full_env,
                              shell=shell, started=started)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=full_env,
            input=input_text,
            shell=shell,
        )
        return RunResult(
            cmd=cmd if isinstance(cmd, list) else [cmd],
            cwd=str(cwd or ""),
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            duration=time.time() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return RunResult(
            cmd=cmd if isinstance(cmd, list) else [cmd],
            cwd=str(cwd or ""),
            returncode=-9,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
            duration=time.time() - started,
            timed_out=True,
        )
    except FileNotFoundError as exc:
        return RunResult(
            cmd=cmd if isinstance(cmd, list) else [cmd],
            cwd=str(cwd or ""),
            returncode=127,
            stdout="",
            stderr=str(exc),
            duration=time.time() - started,
        )


def _run_streaming(
    cmd: list[str] | str,
    *,
    cwd: Path | str | None,
    timeout: int,
    env: dict[str, str],
    shell: bool,
    started: float,
) -> RunResult:
    """Run a command and echo combined output line by line as it arrives.

    Output is still collected so callers can parse errors and write logs.
    A watchdog kills the process when the timeout is reached.
    """
    cmd_list = cmd if isinstance(cmd, list) else [cmd]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            shell=shell,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        return RunResult(
            cmd=cmd_list, cwd=str(cwd or ""), returncode=127,
            stdout="", stderr=str(exc), duration=time.time() - started,
        )

    timed_out = False

    def _kill() -> None:
        nonlocal timed_out
        timed_out = True
        with contextlib.suppress(Exception):
            proc.kill()

    timer = threading.Timer(max(1, timeout), _kill)
    timer.daemon = True
    timer.start()

    lines: list[str] = []
    try:
        if proc.stdout is None:
            raise RuntimeError("无法读取子进程输出。")
        for line in proc.stdout:
            text = line.rstrip("\r\n")
            lines.append(text)
            print(text, flush=True)
        proc.wait()
    finally:
        timer.cancel()
        with contextlib.suppress(Exception):
            if proc.stdout:
                proc.stdout.close()
        if proc.poll() is None:
            with contextlib.suppress(Exception):
                proc.kill()

    return RunResult(
        cmd=cmd_list,
        cwd=str(cwd or ""),
        returncode=proc.returncode if proc.returncode is not None else -9,
        stdout="\n".join(lines),
        stderr="",
        duration=time.time() - started,
        timed_out=timed_out,
    )


@dataclass
class JavaHome:
    path: Path
    version: str
    major: int
    source: str


def _parse_java_version(text: str) -> tuple[str, int]:
    m = re.search(r'version\s+"([^"]+)"', text)
    if not m:
        m = re.search(r"openjdk\s+([0-9][^\s]*)", text, re.I)
    if not m:
        return ("unknown", 0)
    raw = m.group(1)
    if raw.startswith("1."):
        major = int(raw.split(".")[1]) if len(raw.split(".")) > 1 else 0
    else:
        major = int(re.match(r"(\d+)", raw).group(1)) if re.match(r"(\d+)", raw) else 0
    return raw, major


def java_home_info(path: Path) -> JavaHome | None:
    exe = path / "bin" / ("java.exe" if os.name == "nt" else "java")
    if not exe.exists():
        return None
    res = run([str(exe), "-version"], timeout=20)
    raw, major = _parse_java_version(res.combined)
    if major == 0:
        return None
    return JavaHome(path=path, version=raw, major=major, source="scan")


def find_java_homes() -> list[JavaHome]:
    candidates: list[tuple[Path, str]] = []
    env_home = os.environ.get("JAVA_HOME")
    if env_home:
        candidates.append((Path(env_home), "JAVA_HOME"))
    for base in [
        Path("C:/Program Files/Java"),
        Path("C:/Program Files/Eclipse Adoptium"),
        Path("C:/Program Files/Microsoft"),
        Path("C:/Program Files/Zulu"),
        Path.home() / ".jdks",
        Path.home() / ".gradle" / "jdks",
        Path("D:/developer/Java"),
        Path("/usr/lib/jvm"),
        Path("/Library/Java/JavaVirtualMachines"),
    ]:
        if not base.exists():
            continue
        try:
            for child in base.iterdir():
                if not child.is_dir():
                    continue
                if (child / "bin").exists():
                    candidates.append((child, "common"))
                else:
                    for grand in child.iterdir():
                        if grand.is_dir() and (grand / "Contents" / "Home").exists():
                            candidates.append((grand / "Contents" / "Home", "mac"))
                        elif grand.is_dir() and (grand / "bin").exists():
                            candidates.append((grand, "nested"))
        except OSError:
            continue
    # java on PATH
    which_java = shutil.which("java")
    if which_java:
        candidates.append((Path(which_java).resolve().parent.parent, "PATH"))

    found: dict[str, JavaHome] = {}
    for path, source in candidates:
        try:
            info = java_home_info(path)
        except Exception:  # noqa: BLE001
            info = None
        if info and info.version not in found:
            info.source = source
            found[info.version] = info
    return sorted(found.values(), key=lambda j: j.major, reverse=True)


def pick_java(homes: list[JavaHome], major: int) -> JavaHome | None:
    exact = [j for j in homes if j.major == major]
    if exact:
        return exact[0]
    newer = [j for j in homes if j.major > major]
    return min(newer, key=lambda j: j.major) if newer else None


def which(cmd: str) -> str | None:
    return shutil.which(cmd)


def rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def iter_files(root: Path, suffixes: tuple[str, ...] | None = None) -> Iterable[Path]:
    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file() and (suffixes is None or path.suffix.lower() in suffixes):
            yield path


@contextlib.contextmanager
def temp_dir(prefix: str = "mcmod-"):
    d = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)
