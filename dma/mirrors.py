from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import paths

NEOFORGED_HOST = "maven.neoforged.net"
USER_AGENT = "DarkittenModAgent/1.0 (+local build proxy)"
MOJANG_LIBS = "https://libraries.minecraft.net/"

# 按顺序尝试。前两个是国内加速，速度快；插件门户只用来兜住 ModDevGradle；
# Stellar 的元数据最全但很慢；最后一个兜底。
SOURCES: tuple[tuple[str, str], ...] = (
    # Mojang 官方库文件直连通常可用，而且没有 bmclapi 的预签名跳转问题
    # （HEAD 会被 S3 以 403 拒绝）。放在最前面当权威源。
    ("mojanglibs", MOJANG_LIBS),
    ("bmclapi", "https://bmclapi2.bangbang93.com/maven"),
    ("nyist", "https://mirror4.nyist.edu.cn/bmclapi"),
    ("tencent", "https://mirrors.cloud.tencent.com/nexus/repository/maven-public"),
    ("huawei", "https://repo.huaweicloud.com/repository/maven"),
    ("aliyun", "https://maven.aliyun.com/repository/public"),
    ("portal", "https://plugins.gradle.org/m2"),
    ("stellar", "https://repo.stellardrift.ca/maven/internal"),
    ("minguncle", "https://mirror.minguncle.cn/maven.neoforged.net"),
)

CENTRAL = "https://repo.maven.apache.org/maven2"

# 这些官方仓库直连不通时，构建会卡在依赖解析。自动切到本地镜像代理。
OFFICIAL_HOSTS: tuple[str, ...] = (
    "maven.neoforged.net",
    "maven.minecraftforge.net",
    "maven.fabricmc.net",
)
_HOST_CACHE: dict[str, tuple[float, bool]] = {}
_HOST_TTL = 60.0
# 合并 NeoFormRuntime 时只需要快源；慢的备用源留给 Gradle 自己按需拉。
FAST_SOURCE_NAMES = ("mojanglibs", "bmclapi", "nyist", "portal")
VERSION_MANIFEST = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

NFR_MAIN_CLASS = "net.neoforged.neoform.runtime.cli.Main"

UNREACHABLE_MARKERS = (
    "502 bad gateway",
    "503 service unavailable",
    "connection refused",
    "connect timed out",
    "connection timed out",
    "read timed out",
    "remote host terminated",
    "software caused connection abort",
    "temporary failure in name resolution",
    "unknownhost",
    "pkix path building failed",
    "sslhandshakeexception",
    "unexpected end of file",
    "premature end of content-length",
)


def cache_dir() -> Path:
    path = paths.data_dir() / "mirrors"
    path.mkdir(parents=True, exist_ok=True)
    return path


def repo_cache() -> Path:
    path = cache_dir() / "repo"
    path.mkdir(parents=True, exist_ok=True)
    return path


# Windows 默认 MAX_PATH 是 260，Gradle 插件坐标很容易超长。超长时 open()
# 直接抛 OSError，客户端只会看到“发完响应头就断开”，Gradle 会反复重试。
_MAX_CACHE_PATH = 235
# 远端 POM 正常只有几十 KB，解析前设上限，避免恶意/异常文件撑爆内存。
_MAX_POM_BYTES = 4 * 1024 * 1024


def cache_path(maven_path: str) -> Path:
    raw_base = repo_cache()
    base = raw_base.resolve()
    # pathlib 遇到绝对路径会丢掉左边的基址，'..' 也能穿出去；镜像路径
    # 全部来自网络元数据，必须限制在 repo_cache 内。
    cleaned = str(maven_path or "").replace("\\", "/").lstrip("/")
    target = (base / cleaned).resolve()
    if not target.is_relative_to(base):
        raise ValueError(f"非法的 Maven 缓存路径：{maven_path}")
    # 返回未解析的路径：Windows 的 8.3 短文件名（RUNNER~1）只在 resolve()
    # 后出现，调用方和测试都期望看到 repo_cache() 原本的写法。
    plain = raw_base / cleaned
    if len(str(target)) + len(".part") <= _MAX_CACHE_PATH:
        return plain
    digest = hashlib.sha1(maven_path.encode("utf-8"), usedforsecurity=False).hexdigest()
    return raw_base / "_long" / digest[:2] / f"{digest}.bin"


def _gradle_cache_find(maven_path: str) -> Path | None:
    """从本机 Gradle / Maven 缓存里找同名构件。

    国内镜像有时缺 FancyModLoader、NeoForm 这类构件，但用户机器上别的
    模组工程早就下过。直接复用本地缓存，比让用户改仓库或挂代理可靠。
    """
    parts = [item for item in maven_path.split("/") if item]
    if len(parts) < 4:
        return None
    filename = parts[-1]
    version = parts[-2]
    artifact = parts[-3]
    group = ".".join(parts[:-3])
    # 新版 Gradle 把 group 直接当成一个带点号的目录；旧版/ Maven 用多级目录。
    relatives = [
        Path(group, artifact, version),
        Path(*group.split("."), artifact, version),
    ]
    roots: list[Path] = [
        paths.gradle_home() / "caches" / "modules-2" / "files-2.1",
        # 旧版本把缓存放在用户目录，迁移期间继续兼容读取。
        Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1",
    ]
    for root in roots:
        for relative in relatives:
            folder = root / relative
            if not folder.is_dir():
                continue
            hits = sorted(folder.glob(f"*/{filename}"))
            for hit in hits:
                if hit.is_file() and hit.stat().st_size > 0:
                    return hit
    for relative in relatives:
        m2 = Path.home() / ".m2" / "repository" / relative / filename
        if m2.is_file() and m2.stat().st_size > 0:
            return m2
    return None


def _import_from_gradle_cache(
    maven_path: str, service: "MirrorService"
) -> Path | None:
    try:
        digest_request = maven_path.endswith(".sha1")
        base = maven_path[:-5] if digest_request else maven_path
        if digest_request:
            existing = cache_path(base)
            if existing.is_file() and existing.stat().st_size > 0:
                service.store(
                    maven_path,
                    hashlib.sha1(existing.read_bytes(), usedforsecurity=False)
                    .hexdigest()
                    .encode(),
                )
                return cache_path(maven_path)
            target = _gradle_cache_find(base)
            if target is None:
                return None
            data = target.read_bytes()
            service.store(base, data)
            service.store(maven_path, hashlib.sha1(data, usedforsecurity=False).hexdigest().encode())
            return cache_path(maven_path)
        target = _gradle_cache_find(base)
        if target is None:
            return None
        service.store(base, target.read_bytes())
        service._log(f"[镜像] 复用本地缓存 {maven_path}")
        return cache_path(base)
    except OSError:
        return None


def _normalize(path: str) -> str:
    path = path.split("?", 1)[0].strip("/")
    return path


def _human(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _open(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(request, timeout=timeout)


# Stellar 的元数据最全，但同时连接多了会直接卡住不返回。给每个源单独限流，
# 拿不到名额的请求排队，总比大家一起去把源拖死好。
# Stellar 对并发很敏感：同时开 6 个连接就经常整批超时（实测 12 个并发里
# 一半要 60 秒才失败），而 Neoforged 的构件只有它一个源，超时一次就会让
# 整个仓库被 Gradle 禁用。宁可排队慢一点，也不要把它压垮。
_SOURCE_LIMITS = {"stellar": 2, "minguncle": 2}
_slots: dict[str, threading.Semaphore] = {}
_slots_guard = threading.Lock()


def _source_slot(name: str) -> threading.Semaphore:
    with _slots_guard:
        slot = _slots.get(name)
        if slot is None:
            slot = threading.Semaphore(_SOURCE_LIMITS.get(name, 8))
            _slots[name] = slot
        return slot


def _open_source(
    name: str,
    base: str,
    maven_path: str,
    timeout: float = 20.0,
    attempts: int = 2,
    headers: dict[str, str] | None = None,
) -> tuple[object | None, threading.Semaphore | None, bool]:
    """打开上游文件。

    返回 (response, 占用的名额, 是否临时故障)。response 为 None 时：
    第三个值为 False 表示源明确没有这个文件（404/403/410），True 表示超时、
    断连这类临时问题，可以过一会儿再试，不能当成“不存在”。
    """
    slot = _source_slot(name)
    url = f"{base}/{maven_path}"
    transient = False
    for attempt in range(attempts):
        if attempt:
            time.sleep(min(0.8 * attempt, 3.0))
        slot.acquire()
        try:
            request = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, **(headers or {})}
            )
            response = urllib.request.urlopen(request, timeout=timeout)
            return response, slot, False
        except urllib.error.HTTPError as exc:
            slot.release()
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (403, 404, 410):
                return None, None, False
            transient = True
        except Exception:  # noqa: BLE001 - 超时/连接被重置，重试
            slot.release()
            transient = True
    return None, None, transient


class _SourceHealth:
    """记录每个源的速度和故障，决定先试哪个。

    maven.neoforged.net 的几个备用源质量差别很大：nyist 通常几百 KB/s，
    minguncle 可能 30 秒才回响应，Stellar 会直接握手超时。以前按固定顺序
    逐个试，一个 404 也要等十秒，一轮构建几十个请求就拖成二十分钟。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._speed: dict[str, float] = {}
        self._penalty: dict[str, float] = {}
        self._streak: dict[str, int] = {}
        self._absent: dict[tuple[str, str], float] = {}

    def order(self, sources, path: str = "", pin: tuple[str, ...] = ()) -> list[tuple[str, str]]:
        now = time.time()
        with self._lock:
            ready: list[tuple[int, float, int, str, str]] = []
            for index, (name, base) in enumerate(sources):
                if self._absent.get((name, path), 0.0) > now:
                    continue
                if self._penalty.get(name, 0.0) > now:
                    continue
                rank = 0 if name in pin else 1
                ready.append((rank, self._speed.get(name, 0.0), index, name, base))
        ready.sort(key=lambda item: (item[0], -item[1], item[2]))
        ordered = [(name, base) for _rank, _speed, _index, name, base in ready]
        if ordered:
            return ordered
        pinned = [(name, base) for name, base in sources if name in pin]
        rest = [(name, base) for name, base in sources if name not in pin]
        return pinned + rest

    def ok(self, name: str, seconds: float, size: int) -> None:
        speed = size / max(seconds, 0.05) / 1024.0
        with self._lock:
            previous = self._speed.get(name)
            self._speed[name] = speed if previous is None else previous * 0.6 + speed * 0.4
            self._streak[name] = 0
            self._penalty.pop(name, None)
            for key in [key for key in self._absent if key[0] == name]:
                self._absent.pop(key, None)

    def seen(self, name: str, seconds: float) -> None:
        """只知道响应时间、没有文件大小时，用一个固定权重估算速度。"""
        self.ok(name, seconds, 64 * 1024)

    def miss(self, name: str, path: str) -> None:
        with self._lock:
            self._absent[(name, path)] = time.time() + _ABSENT_TTL

    def fail(self, name: str, transient: bool = True, cap: float = 600.0) -> None:
        """记录一次失败并短暂避开该源。

        权威源（Stellar）不能因为一次超时就被排除几分钟：Neoforged 的构件
        只有它有，排除了就只能给 Gradle 回 503。所以调用方传一个很小的
        ``cap``，让它几秒后就能重新参与竞争。
        """
        if not transient:
            return
        with self._lock:
            streak = self._streak.get(name, 0) + 1
            self._streak[name] = streak
            wait = min(20.0 * (2 ** (streak - 1)), cap)
            self._penalty[name] = time.time() + wait
            self._speed[name] = 0.0

    def forget(self) -> None:
        with self._lock:
            self._speed.clear()
            self._penalty.clear()
            self._streak.clear()
            self._absent.clear()


_health = _SourceHealth()


class _Abandoned(Exception):
    pass


def _close_quietly(response) -> None:
    try:
        response.close()
    except Exception:  # noqa: BLE001
        pass


_RACE_TIMEOUT = 30.0
_RACE_OVERALL = 60.0
_RACE_STAGGER = 0.3


def _race(
    sources: list[tuple[str, str]],
    maven_path: str,
    *,
    mode: str = "stream",
    need: int = 1 << 16,
    timeout: float = _RACE_TIMEOUT,
    overall: float = _RACE_OVERALL,
    headers: dict[str, str] | None = None,
    stagger: float = _RACE_STAGGER,
    required: tuple[str, ...] = (),
    authoritative: bool = False,
):
    """并发打开多个源，返回最先拿到数据的那个。

    mode:
      stream - 读够 need 字节就停，response 留给调用方继续读；
      bytes  - 直接读完整个文件并关闭 response；
      head   - 只取内容长度，顺便确认文件存在。
    返回 ((name, payload, response, slot), misses, failures, incomplete,
    unresolved)。incomplete 表示还有源没来得及回答（排队或超时）；
    unresolved 表示 required 里的权威源没有给出明确答复，这两种情况都不能
    断定文件不存在。

    authoritative=True 时（maven-metadata.xml 这类无法从其它源补齐的内容），
    非权威源的响应先挂起，等权威源表态：权威源也拿到才算数。否则国内镜像
    里那份过期了五年的 maven-metadata.xml 会先返回，让 Gradle 误判“没有
    符合范围的版本”。权威源明确 404 时才退回其它源；权威源临时故障则宁
    可回 503 让 Gradle 重试，也不能返回过期元数据。

    response 为 None 表示调用方不需要继续读。
    """
    results: queue.Queue = queue.Queue()
    stop = threading.Event()
    deadline = time.monotonic() + overall
    slot_value = need if mode != "bytes" else 0

    def worker(name: str, base: str) -> None:
        response = None
        slot = None
        started = time.monotonic()
        try:
            response, held, bad = _open_source(
                name,
                base,
                maven_path,
                timeout=timeout,
                # 权威源重试：Stellar 偶发握手超时，重试一次就能拿到，
                # 不能因为一次抖动就让整个构件解析失败。
                attempts=3 if name in required else 1,
                headers=headers,
            )
            if response is None:
                results.put(("fail" if bad else "miss", name, None, None, None))
                return
            slot = held
            if stop.is_set():
                raise _Abandoned()
            if mode == "bytes":
                payload = response.read()
                _close_quietly(response)
                response = None
                if not payload:
                    raise _Abandoned()
                slot.release()
                slot = None
                _health.ok(name, time.monotonic() - started, len(payload))
            elif mode == "head":
                response.read(1)
                length = response.headers.get("Content-Length")
                content_range = response.headers.get("Content-Range") or ""
                if "/" in content_range:
                    total = content_range.rsplit("/", 1)[-1]
                    if total.isdigit():
                        length = total
                _close_quietly(response)
                response = None
                slot.release()
                slot = None
                if length is None:
                    raise _Abandoned()
                payload = length
                _health.seen(name, time.monotonic() - started)
            else:
                payload = response.read(slot_value)
                if not payload:
                    raise _Abandoned()
            if stop.is_set() and response is not None:
                raise _Abandoned()
        except _Abandoned:
            if response is not None:
                _close_quietly(response)
            if slot is not None:
                slot.release()
            results.put(("late", name, None, None, None))
            return
        except urllib.error.HTTPError as exc:
            _close_quietly(exc)
            if slot is not None:
                slot.release()
            results.put(("fail", name, None, None, None))
            return
        except Exception:  # noqa: BLE001 - 单个源失败不影响其它源
            if response is not None:
                _close_quietly(response)
            if slot is not None:
                slot.release()
            results.put(("fail", name, None, None, None))
            return
        results.put(("ok", name, payload, response, slot))

    launched = 0
    pending = 0
    winner = None
    held = None
    misses = 0
    failures = 0
    answered: set[str] = set()
    required_names = set(required)
    required_pending = set(required)
    required_failed = False
    while winner is None:
        now = time.monotonic()
        if launched < len(sources) and now < deadline:
            name, base = sources[launched]
            threading.Thread(target=worker, args=(name, base), daemon=True).start()
            launched += 1
            pending += 1
            wait = stagger if launched < len(sources) else max(0.0, deadline - now)
        else:
            if pending == 0 or now >= deadline:
                break
            wait = max(0.0, deadline - now)
        try:
            item = results.get(timeout=min(wait, max(0.0, deadline - time.monotonic())))
        except queue.Empty:
            continue
        pending -= 1
        status, name, payload, response, slot = item
        if status == "ok":
            answered.add(name)
            if authoritative and name not in required_names:
                # 先压着，等权威源表态；只留第一份，后面多余的直接关掉。
                if held is None:
                    held = item
                else:
                    if response is not None:
                        _close_quietly(response)
                    if slot is not None:
                        slot.release()
            else:
                stop.set()
                winner = (name, payload, response, slot)
                if held is not None:
                    if held[3] is not None:
                        _close_quietly(held[3])
                    if held[4] is not None:
                        held[4].release()
                    held = None
        elif status == "miss":
            misses += 1
            answered.add(name)
            _health.miss(name, maven_path)
        elif status == "fail":
            failures += 1
            if name in required_names:
                required_failed = True
            # 失败不等于“答复过”：权威源（Stellar 等）超时/重置时不能
            # 把它当成 404，否则会误把存在的构件记成缺失。
            _health.fail(name, cap=10.0 if name in required else 600.0)
        if authoritative:
            required_pending.discard(name)
            if winner is None and not required_pending:
                if held is not None and not required_failed:
                    # 权威源明确说没有，其它源的内容才可信。
                    stop.set()
                    winner = held
                    held = None
                    break
                # 权威源故障：不能用过期副本冒充正确结果，退出等 503。
                break
    if winner is None and held is not None and not (authoritative and required_failed):
        winner = held
        held = None
    if held is not None:
        if held[3] is not None:
            _close_quietly(held[3])
        if held[4] is not None:
            held[4].release()
        held = None
    stop.set()
    # 胜出后其它线程可能刚好也拿到数据，短时间清掉，避免连接和名额泄漏。
    end = time.monotonic() + 0.3
    while True:
        try:
            item = results.get(timeout=max(0.001, end - time.monotonic()))
        except queue.Empty:
            break
        if item[0] == "ok":
            if item[3] is not None:
                _close_quietly(item[3])
            if item[4] is not None:
                item[4].release()
    incomplete = winner is None and (pending > 0 or launched < len(sources))
    # 权威源（例如 Neoforged 相关路径上的 Stellar）没答复就不能当 404，
    # 但普通路径只要有一个源明确回了 404，就可以放心告诉 Gradle 去下一个仓库。
    unresolved = winner is None and any(name not in answered for name in required)
    return winner, misses, failures, incomplete, unresolved


def fetch(maven_path: str, timeout: float = 20.0, attempts: int = 2) -> bytes | None:
    """并发从各镜像取一个 Maven 路径，全部失败返回 None。"""
    maven_path = _normalize(maven_path)
    ordered = _health.order(
        SOURCES + (("central", CENTRAL),), maven_path, _pinned_sources(maven_path)
    )
    winner, _misses, _failures, _incomplete, _unresolved = _race(
        ordered,
        maven_path,
        mode="bytes",
        timeout=min(timeout, 30.0),
        overall=min(timeout + 5.0, 60.0),
        required=_pinned_sources(maven_path),
    )
    if winner is None:
        return None
    name, data, _response, _slot = winner
    return data


def _download_from(
    base: str,
    maven_path: str,
    part: Path,
    timeout: float,
    attempts: int,
    name: str = "",
) -> bool | None:
    """从同一个源断点续传。True=完整拿到，False=源有问题，None=源没有这个文件。"""
    url = f"{base}/{maven_path}"
    started = time.monotonic()
    for _ in range(attempts):
        have = part.stat().st_size if part.is_file() else 0
        headers = {"User-Agent": USER_AGENT}
        if have:
            headers["Range"] = f"bytes={have}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                total = response.headers.get("Content-Length")
                if have and status != 206:
                    have = 0
                mode = "ab" if have else "wb"
                with open(part, mode) as handle:
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        handle.write(chunk)
                if total is not None and not have:
                    expected = int(total)
                    if part.stat().st_size < expected:
                        continue
                if name:
                    _health.ok(name, time.monotonic() - started, part.stat().st_size)
                return True
        except urllib.error.HTTPError as exc:
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            if exc.code in (403, 404, 410):
                if name:
                    _health.miss(name, maven_path)
                return None
            continue
        except Exception:  # noqa: BLE001 - 断了就带着已下载的部分重试
            continue
    if name:
        _health.fail(name)
    return False


def fetch_to(
    maven_path: str,
    target: Path,
    timeout: float = 20.0,
    attempts: int = 3,
    fast_only: bool = False,
) -> bool:
    maven_path = _normalize(maven_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    part.unlink(missing_ok=True)
    pool = [(name, base) for name, base in SOURCES if not fast_only or name in FAST_SOURCE_NAMES]
    pool.append(("central", CENTRAL))
    for name, base in _health.order(pool, maven_path, _pinned_sources(maven_path)):
        result = _download_from(base, maven_path, part, timeout, attempts, name=name)
        if result is True:
            os.replace(part, target)
            return True
        if result is None:
            # 源明确没有这个文件，保留已下载的部分给下一个源续传。
            continue
    part.unlink(missing_ok=True)
    return False


def neoforged_reachable(timeout: float = 6.0) -> bool:
    """能不能直连 maven.neoforged.net。被 DNS 污染时这里会是 False。"""
    url = f"https://{NEOFORGED_HOST}/releases/net/neoforged/neoform-runtime/maven-metadata.xml"
    try:
        with _open(url, timeout) as response:
            response.read(64)
            return 200 <= int(getattr(response, "status", 200)) < 500
    except Exception:  # noqa: BLE001
        return False


# 探测时必须访问一个“一定存在”的构件元数据。很多网络环境会在仓库根路径
# 直接返回 404 或假 200，却把真正的构件请求卡死，只用根路径会误判为可达。
_PROBE_PATHS: dict[str, str] = {
    "maven.neoforged.net": "/releases/net/neoforged/forge/maven-metadata.xml",
    "maven.minecraftforge.net": "/net/minecraftforge/forge/maven-metadata.xml",
    "maven.fabricmc.net": "/net/fabricmc/fabric-loader/maven-metadata.xml",
}


def host_reachable(host: str, port: int = 443, timeout: float = 8.0) -> bool:
    """真正建一次 HTTP(S) 连接来探测官方仓库，结果缓存 60 秒。

    只做 TCP 握手会漏掉三类“假连通”：证书链校验失败、TLS 被中间设备
    重置、网关接受了连接却不转发任何数据。这三种情况下 Gradle 一个构件
    都拿不到，但 TCP 探测却报告“可达”，镜像永远不会启用。

    对已知的官方仓库探测一个真实存在的 maven-metadata.xml：根路径经常被
    网关伪造（返回 404 或假 200），不能作为判断依据。
    """
    now = time.time()
    cached = _HOST_CACHE.get(host)
    if cached is not None and now < cached[0]:
        return cached[1]
    scheme = "https" if port == 443 else "http"
    path = _PROBE_PATHS.get(host, "/")
    url = f"{scheme}://{host}:{port}{path}"
    reachable = False
    try:
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read(1)
        reachable = True
    except urllib.error.HTTPError as exc:
        # 已知仓库的元数据 404/5xx 表示仓库不可用；其他主机只要响应了
        # HTTP 状态码，就说明 TLS 与 HTTP 都通了。
        reachable = host not in _PROBE_PATHS
    except Exception:  # noqa: BLE001 - 证书、超时、重置都按不可达处理
        reachable = False
    _HOST_CACHE[host] = (now + _HOST_TTL, reachable)
    return reachable


def unreachable_hosts(
    hosts: list[str] | tuple[str, ...] | None = None, timeout: float = 8.0
) -> list[str]:
    """返回连不上的官方仓库主机列表。空列表表示都能直连，不需要镜像。"""
    candidates = [
        str(host).strip()
        for host in (hosts or OFFICIAL_HOSTS)
        if str(host).strip()
    ]
    if not candidates:
        return []
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
        results = list(
            pool.map(lambda host: host_reachable(host, timeout=timeout), candidates)
        )
    return [host for host, ok in zip(candidates, results) if not ok]


def forget_host_cache() -> None:
    _HOST_CACHE.clear()


def needs_mirror(text: str) -> bool:
    lowered = (text or "").lower()
    if NEOFORGED_HOST not in lowered:
        return False
    return any(marker in lowered for marker in UNREACHABLE_MARKERS)


# ----------------------------------------------------------------------
# vanilla 依赖清单


def mojang_version_json(version: str, timeout: float = 12.0) -> dict:
    cached = cache_dir() / "mojang" / f"{version}.json"
    if cached.is_file():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    manifest = cache_dir() / "mojang" / "version_manifest.json"
    data: dict | None = None
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
    if data is None:
        data = _first_json(
            (
                VERSION_MANIFEST,
                "https://bmclapi2.bangbang93.com/mc/game/version_manifest_v2.json",
            ),
            timeout,
        )
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps(data), encoding="utf-8")
    url = ""
    for item in data.get("versions", []):
        if item.get("id") == version:
            url = item.get("url") or ""
            break
    candidates = [url] if url else []
    candidates.append(f"https://bmclapi2.bangbang93.com/version/{version}/json")
    version_data = _first_json(candidates, timeout)
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(version_data), encoding="utf-8")
    return version_data


def _first_json(urls: list[str] | tuple[str, ...], timeout: float) -> dict:
    last: Exception | None = None
    for url in urls:
        try:
            with _open(url, timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - 换下一个源
            last = exc
    raise ValueError(f"Mojang 版本清单获取失败：{last}")


def _split_coordinate(name: str) -> tuple[str, str, str, str | None]:
    parts = name.split(":")
    classifier = parts[3] if len(parts) > 3 else None
    return parts[0], parts[1], parts[2], classifier


def _rule_allows(rules: list | None, os_name: str) -> bool:
    if not rules:
        return True
    allowed = False
    for rule in rules:
        condition = rule.get("os")
        if condition is None:
            matched = True
        else:
            matched = condition.get("name") in (None, os_name)
            if "arch" in condition:
                matched = matched and condition["arch"] == "x86_64"
        if matched:
            allowed = rule.get("action") == "allow"
    return allowed


def minecraft_dependencies_module(version_json: dict) -> dict:
    """把 Mojang 的版本清单翻译成 Gradle 模块元数据。

    NeoFormRuntime 只会下载“不在构建清单里”的库；把整张清单做成一个模块以后，
    Gradle 会负责把所有库从能连通的源下载好，NFR 就不会再去碰被污染的主机。
    """
    version = str(version_json.get("id") or "")
    # 变体必须声明这个 Minecraft 版本自己的 Java 目标版本（1.20.1 是 17）。
    # 写死 21 时，toolchain 17 的工程在 compileClasspath 上会报
    # “No matching variant … compatible with Java 21 … needed Java 17”。
    try:
        java_version = int(version_json["javaVersion"]["majorVersion"])
    except (KeyError, TypeError, ValueError):
        java_version = 17
    variants: list[dict] = []
    for distribution in ("client", "server"):
        for os_name in ("windows", "linux", "macos"):
            deps: list[dict] = []
            for library in version_json.get("libraries", []):
                if not _rule_allows(library.get("rules"), os_name):
                    continue
                group, module, lib_version, classifier = _split_coordinate(library["name"])
                entry: dict = {
                    "group": group,
                    "module": module,
                    # ModDevGradle 的 NonStrictDependencyTransform 只认 strictly：
                    # 它把 strictVersion 读出来再 require，未设置时会把版本清空，
                    # 依赖就变成 "group:module:" 无法解析。
                    "version": {"strictly": lib_version},
                }
                if classifier:
                    entry["thirdPartyCompatibility"] = {
                        "artifactSelector": {
                            "name": module,
                            "type": "jar",
                            "extension": "jar",
                            "classifier": classifier,
                        }
                    }
                deps.append(entry)
            for usage in ("java-api", "java-runtime"):
                variants.append(
                    {
                        "name": f"{distribution}-{os_name}-{'api' if usage == 'java-api' else 'runtime'}",
                        "attributes": {
                            "org.gradle.usage": usage,
                            "org.gradle.jvm.version": java_version,
                            "net.neoforged.distribution": distribution,
                            "net.neoforged.operatingsystem": os_name,
                        },
                        "dependencies": deps,
                    }
                )
    return {
        "formatVersion": "1.1",
        "component": {
            "group": "net.neoforged",
            "module": "minecraft-dependencies",
            "version": version,
            "attributes": {"org.gradle.status": "release"},
        },
        "variants": variants,
    }


# ----------------------------------------------------------------------
# NeoFormRuntime 的 -all.jar


def _pom_dependencies(xml_bytes: bytes) -> list[tuple[str, str, str, str | None]]:
    if len(xml_bytes) > _MAX_POM_BYTES:
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag[: root.tag.index("}") + 1]
    result: list[tuple[str, str, str, str | None]] = []
    for dep in root.iter(f"{ns}dependency"):
        group = (dep.findtext(f"{ns}groupId") or "").strip()
        artifact = (dep.findtext(f"{ns}artifactId") or "").strip()
        version = (dep.findtext(f"{ns}version") or "").strip()
        classifier = (dep.findtext(f"{ns}classifier") or "").strip() or None
        scope = (dep.findtext(f"{ns}scope") or "compile").strip()
        optional = (dep.findtext(f"{ns}optional") or "").strip().lower() == "true"
        if not group or not artifact or not version or optional:
            continue
        if "$" in version or scope in ("test", "provided", "system"):
            continue
        # 只合并 runtime 依赖；compile 里那几个是完整的工具 jar（jst / mergetool /
        # DiffPatch），NFR 会从 Gradle 生成的文件清单里直接拿到，塞进来只会让 jar
        # 从 4MB 涨到 60MB。
        if scope != "runtime":
            continue
        result.append((group, artifact, version, classifier))
    return result


def _main_class(jar: Path) -> str:
    try:
        with zipfile.ZipFile(jar) as archive:
            text = archive.read("META-INF/MANIFEST.MF").decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return NFR_MAIN_CLASS
    buffer = ""
    for line in text.splitlines():
        if line.startswith(" "):
            buffer += line[1:]
            continue
        if buffer.lower().startswith("main-class:"):
            return buffer.split(":", 1)[1].strip()
        buffer = line
    if buffer.lower().startswith("main-class:"):
        return buffer.split(":", 1)[1].strip()
    return NFR_MAIN_CLASS


def merge_jars(parts: list[Path], target: Path) -> None:
    """把主 jar 和依赖合租成一个可执行 jar，行为等价于 Gradle shadow 的 -all.jar。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    main_class = _main_class(parts[0])
    seen: set[str] = set()
    services: dict[str, list[str]] = {}
    tmp = target.with_suffix(target.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        manifest = f"Manifest-Version: 1.0\r\nMain-Class: {main_class}\r\n\r\n"
        archive.writestr("META-INF/MANIFEST.MF", manifest)
        seen.add("META-INF/MANIFEST.MF")
        for part in parts:
            with zipfile.ZipFile(part) as source:
                for info in source.infolist():
                    name = info.filename
                    if name.endswith("/"):
                        continue
                    if name == "META-INF/MANIFEST.MF" or name == "module-info.class":
                        continue
                    if name.startswith("META-INF/") and name.upper().endswith(
                        (".SF", ".DSA", ".RSA", "/INDEX.LIST")
                    ):
                        continue
                    if name.startswith("META-INF/services/"):
                        services.setdefault(name, []).append(
                            source.read(name).decode("utf-8", "replace")
                        )
                        continue
                    if name in seen:
                        continue
                    seen.add(name)
                    archive.writestr(name, source.read(name))
        for name, chunks in services.items():
            providers: list[str] = []
            for chunk in chunks:
                for line in chunk.splitlines():
                    entry = line.split("#", 1)[0].strip()
                    if entry and entry not in providers:
                        providers.append(entry)
            if providers:
                archive.writestr(name, "\n".join(providers) + "\n")
    os.replace(tmp, target)


def _bundled_neoform(version: str, suffix: str) -> Path | None:
    """程序自带的 NeoFormRuntime 文件。maven.neoforged.net 直连不通时，
    这个 300KB 的 jar 只能从很慢的备用源拿，所以内置一份。"""
    path = paths.resource_dir() / "neoform" / f"neoform-runtime-{version}{suffix}"
    return path if path.is_file() else None


def _bundled_loom_stub() -> Path | None:
    """fabric-loom-native 在镜像上拿不到，NeoFormRuntime 只用到它的两个静态方法。"""
    path = paths.resource_dir() / "neoform" / "loom-stub.jar"
    return path if path.is_file() else None


def _jar_has(path: Path, entry: str) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return entry in archive.namelist()
    except Exception:  # noqa: BLE001
        return False


def _jar_ok(path: Path) -> bool:
    try:
        with zipfile.ZipFile(path) as archive:
            return bool(archive.namelist())
    except Exception:  # noqa: BLE001
        return False


def _ensure_nfr_all_jar(group: str, module: str, version: str, log: Callable[[str], None]) -> Path | None:
    base = group.replace(".", "/")
    folder = repo_cache() / base / module / version
    target = folder / f"{module}-{version}-all.jar"
    stub = _bundled_loom_stub()
    stub_entry = "net/fabricmc/loom/nativeplatform/LoomNativePlatform.class"
    # 先把 POM 拿到手：判断已合成的 jar 是否缺依赖时要用到依赖清单。
    pom: bytes | None = None
    bundled_pom = _bundled_neoform(version, ".pom")
    if bundled_pom is not None:
        pom = bundled_pom.read_bytes()
    else:
        pom = fetch(f"{base}/{module}/{version}/{module}-{version}.pom")
    deps = list(_pom_dependencies(pom)) if pom else []
    need_picocli = any(artifact == "picocli" for _g, artifact, _v, _c in deps)
    if target.is_file() and target.stat().st_size > 0 and _jar_ok(target):
        # 旧版本合成的 jar 缺 fabric-loom-native 的类（CLI 一启动就
        # NoClassDefFoundError），也可能缺 picocli 等依赖，需要重做一次。
        has_stub = stub is None or _jar_has(target, stub_entry)
        has_picocli = not need_picocli or _jar_has(target, "picocli/CommandLine.class")
        if has_stub and has_picocli:
            return target
    plain = folder / f"{module}-{version}.jar"
    if not (plain.is_file() and _jar_ok(plain)):
        bundled = _bundled_neoform(version, ".jar")
        if bundled is not None:
            log(f"[镜像] 使用内置的 {module} {version}")
            plain.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(bundled, plain)
        else:
            log(f"[镜像] 下载 {group}:{module}:{version} 主 jar（备用源较慢，会断点续传）")
            if not fetch_to(f"{base}/{module}/{version}/{module}-{version}.jar", plain, timeout=45, attempts=8):
                return None
        if not _jar_ok(plain):
            plain.unlink(missing_ok=True)
            return None
    parts = [plain]
    for dep_group, dep_artifact, dep_version, classifier in deps:
        dep_base = dep_group.replace(".", "/")
        suffix = f"-{classifier}" if classifier else ""
        dep_path = repo_cache() / dep_base / dep_artifact / dep_version / (
            f"{dep_artifact}-{dep_version}{suffix}.jar"
        )
        if not (dep_path.is_file() and _jar_ok(dep_path)):
            log(f"[镜像] 下载 {dep_group}:{dep_artifact}:{dep_version}{suffix}")
            url = f"{dep_base}/{dep_artifact}/{dep_version}/{dep_artifact}-{dep_version}{suffix}.jar"
            # 快源优先；拿不到再走全部源（.jar 是硬依赖，缺一个就启动不了）。
            if not fetch_to(url, dep_path, timeout=30, attempts=2, fast_only=True):
                if not fetch_to(url, dep_path, timeout=45, attempts=3):
                    log(f"[镜像] 跳过 {dep_artifact}{suffix}（留给 Gradle 处理）")
                    continue
        parts.append(dep_path)
    if stub is not None:
        parts.append(stub)
    log(f"[镜像] 合并 {module}-{version}-all.jar（{len(parts)} 个 jar）")
    try:
        merge_jars(parts, target)
    except Exception as exc:  # noqa: BLE001
        log(f"[镜像] 合并失败：{exc}")
        target.unlink(missing_ok=True)
        return None
    return target


_NFR_PLAIN = re.compile(r"^net/neoforged/neoform-runtime/([^/]+)/neoform-runtime-(\1)\.(pom|jar)$")
_NFR_ARTIFACT = re.compile(r"^net/neoforged/neoform-runtime/([^/]+)/neoform-runtime-(\1)-all\.jar$")
_NFR_MODULE = re.compile(r"^net/neoforged/neoform-runtime/([^/]+)/neoform-runtime-(\1)\.module$")
_MCD_MODULE = re.compile(r"^net/neoforged/minecraft-dependencies/([^/]+)/minecraft-dependencies-(\1)\.module$")

# Gradle 模块元数据是可选文件：拿不到时会退回读 POM。权威源抖动时如果
# 回 503，Gradle 会直接判整个模块解析失败；回 404 反而能正常走 POM。
_OPTIONAL_SUFFIXES = (".module", ".sha1", ".md5", ".sha256", ".sha512")


def _is_optional(maven_path: str) -> bool:
    return maven_path.lower().endswith(_OPTIONAL_SUFFIXES)


# ----------------------------------------------------------------------
# 本地 Maven 代理


_MISS_TTL = 30.0
_ABSENT_TTL = 60.0
MIRROR_PORT = 55878
# 合成元数据的结构版本。改了 minecraft-dependencies 之类的生成逻辑就必须
# 递增：Gradle 按仓库 URL 缓存模块元数据，URL 不变就永远用旧副本（例如
# 之前写死 Java 21 的变体），升级程序后仍然复现“No matching variant”。
SYNTH_SCHEMA = 3

# NeoForged 自己的仓库（maven.neoforged.net）被墙时，只有 Stellar 镜像是完整的。
# 这些路径先问 Stellar，避免前面几个只镜像 Mojang 的源返回 404 把整个解析带偏。
_PINNED_PREFIXES = {
    "cpw/": ("stellar",),
    "net/neoforged/": ("stellar",),
    "net/minecraftforge/": ("stellar",),
    "io/codechicken/": ("stellar",),
    "org/vineflower/": ("stellar",),
    # Gradle 自己的工具链解析插件只在插件门户上，先问门户可以少绕一圈。
    "org/gradle/toolchains/": ("portal",),
}


def _pinned_sources(path: str) -> tuple[str, ...]:
    for prefix, names in _PINNED_PREFIXES.items():
        if path.startswith(prefix):
            return names
    return ()

# Gradle 探测依赖时会频繁建立/关闭连接，客户端先断开属于正常现象。
_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # Windows 下 SO_REUSEADDR 允许两个进程抢同一个端口，连接会被老进程接走，
    # 结果就是新构建用到旧进程里过期的 404 缓存。这里宁可绑定失败换端口。
    allow_reuse_address = False

    def handle_error(self, request, client_address) -> None:
        error = sys.exc_info()[1]
        if isinstance(error, _DISCONNECT_ERRORS):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "DarkittenMaven/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - 不写 stderr
        return

    # -- 输出 ---------------------------------------------------------
    def _send_bytes(self, data: bytes, status: int = 200) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)
        except _DISCONNECT_ERRORS:
            pass

    def _send_file(self, path: Path) -> None:
        try:
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as handle:
                shutil.copyfileobj(handle, self.wfile, 1 << 16)
        except _DISCONNECT_ERRORS:
            pass
        except OSError:
            self._send_bytes(b"", 500)

    def _missing(self) -> None:
        self._send_bytes(b"", 404)

    # -- 路由 ---------------------------------------------------------
    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        service: MirrorService = self.server.service  # type: ignore[attr-defined]
        raw = self.path.split("?", 1)[0]
        prefix = "/releases/"
        if raw.startswith("/releases/s"):
            head, _, rest = raw[len("/releases/s") :].partition("/")
            if head.isdigit() and rest:
                prefix = f"/releases/s{head}/"
        if raw.startswith(prefix):
            maven_path = _normalize(raw[len(prefix) :])
        else:
            self._missing()
            return
        if not maven_path:
            self._missing()
            return
        if ".." in maven_path.split("/") or "\\" in maven_path or ":" in maven_path:
            self._missing()
            return
        # 先走本地合成：minecraft-dependencies / neoform-runtime 的元数据由
        # 程序自己生成，旧版本留在缓存里的副本可能已经过期，不能被 cache 抢先命中。
        try:
            data = service.synthesize(maven_path)
        except Exception:  # noqa: BLE001 - 代理不能因为单个请求挂掉
            data = None
        if data is not None:
            service.store(maven_path, data)
            self._send_bytes(data)
            return
        # 合成内容会随程序版本变化，缓存里旧内容的 .sha1 不能继续用。
        # Gradle 校验不一致时会退回按旧哈希缓存的构件，于是升级后仍然
        # 拿到过期元数据（例如把 Java 21 写死的 minecraft-dependencies）。
        if maven_path.endswith(".sha1"):
            synth = None
            try:
                synth = service.synthesize(maven_path[: -len(".sha1")])
            except Exception:  # noqa: BLE001
                synth = None
            if synth is not None:
                digest = hashlib.sha1(synth, usedforsecurity=False).hexdigest().encode("ascii")
                service.store(maven_path, digest)
                self._send_bytes(digest)
                return
        # maven-metadata.xml 决定动态版本（4.0.+ 这类）能解析出哪些版本，
        # 国内镜像上的这份文件经常几年不更新。每次都重新问上游，不能直接
        # 用本地缓存，否则 Gradle 会在“没有匹配版本”上失败。
        if not maven_path.endswith("maven-metadata.xml"):
            cached = service.cached(maven_path)
            if cached is not None:
                self._send_file(cached)
                return
        if self.command == "HEAD":
            service.head(maven_path, self)
            return
        service.stream(maven_path, self)


class MirrorService:
    """把 maven.neoforged.net 换成本地代理，代理按需从国内镜像抓取并缓存。"""

    def __init__(
        self, log: Callable[[str], None] | None = None, *, fresh_port: bool = False
    ) -> None:
        self._log = log or (lambda _text: None)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._missing: dict[str, float] = {}
        self._script: Path | None = None
        # 重试构建时换一个端口，等于给 Gradle 换了一个仓库 URL：
        # 上次构建记下的“模块不存在”是按 URL 缓存的，端口不变就永远
        # 不会重新问镜像，必须靠新端口绕开这份缓存。
        self._fresh_port = fresh_port

    # -- 生命周期 -----------------------------------------------------
    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("镜像代理还没启动")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/releases/s{SYNTH_SCHEMA}"

    def start(self) -> str:
        if self._server is not None:
            return self.base_url
        # 正常构建用固定端口，代理地址在每次构建之间保持一致，Gradle 的
        # 依赖解析缓存可以继续复用。重试构建（fresh_port）则直接用临时端口，
        # 让 Gradle 按新的仓库 URL 重新解析。
        server = None
        candidates = (0,) if self._fresh_port else (
            MIRROR_PORT,
            *range(MIRROR_PORT + 1, MIRROR_PORT + 16),
            0,
        )
        for port in candidates:
            try:
                server = _Server(("127.0.0.1", port), _Handler)
                break
            except OSError:
                continue
        if server is None:
            raise OSError("找不到可用的本地镜像端口")
        server.service = self  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        self._log(f"[镜像] 本地代理已启动：{self.base_url}")
        return self.base_url

    def stop(self) -> None:
        if self._server is None:
            return
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:  # noqa: BLE001
            pass
        self._server = None
        self._thread = None

    # -- 404 缓存 -------------------------------------------------------
    def _is_missing(self, maven_path: str) -> bool:
        expires = self._missing.get(maven_path)
        if expires is None:
            return False
        if expires <= time.time():
            self._missing.pop(maven_path, None)
            return False
        return True

    def _mark_missing(self, maven_path: str) -> None:
        self._missing[maven_path] = time.time() + _MISS_TTL

    def forget_missing(self) -> None:
        """新一次构建开始时清掉 404 缓存，让上游短暂缺失的包重新有机会下载。"""
        self._missing.clear()

    # -- init script ---------------------------------------------------
    def init_script(self, hosts: list[str] | tuple[str, ...] | None = None) -> Path:
        """生成 Gradle init script。

        hosts 只包含“当前连不上”的官方仓库，避免把本来能直连的仓库也绕进代理。
        """
        active = [
            str(host).strip()
            for host in (hosts or OFFICIAL_HOSTS)
            if str(host).strip()
        ]
        hosts_literal = "[" + ", ".join(
            "'" + host.replace("'", "") + "'" for host in active
        ) + "]"
        self.forget_missing()
        base = self.start()
        target = cache_dir() / "mirror.gradle"
        content = _INIT_TEMPLATE.format(
            mirror=base, mojang=MOJANG_LIBS, hosts=hosts_literal
        )
        try:
            if target.is_file() and target.read_text(encoding="utf-8") == content:
                return target
        except OSError:
            pass
        target.write_text(content, encoding="utf-8")
        self._script = target
        return target

    # -- 解析 ---------------------------------------------------------
    def cached(self, maven_path: str) -> Path | None:
        cache = cache_path(maven_path)
        if cache.is_file() and cache.stat().st_size > 0:
            return cache
        imported = _import_from_gradle_cache(maven_path, self)
        if imported is not None:
            return imported
        return None

    def store(self, maven_path: str, data: bytes) -> Path:
        cache = cache_path(maven_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(cache.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, cache)
        return cache

    def stream(self, maven_path: str, handler: _Handler) -> None:
        """并发试源，先把首块转给 Gradle，剩下的边下边缓存。"""
        if self._is_missing(maven_path):
            handler._missing()
            return
        cache = cache_path(maven_path)
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(cache.suffix + ".part")
        self._log(f"[镜像] 获取 {maven_path}")
        started = time.monotonic()
        pinned = _pinned_sources(maven_path)
        # maven-metadata.xml 用来解析版本区间，内容不完整就会报“找不到匹配
        # 4.0.+”这类错误（bmclapi 上 forgespi 的列表停在 2.0.0）。所以
        # neoforged / forge / cpw 的元数据仍以 Stellar 为准，只是把等待时间
        # 压到 45 秒内，避免一个文件卡三分钟让构建看起来像死机。
        metadata = maven_path.endswith("maven-metadata.xml")
        ordered = _health.order(
            SOURCES + (("central", CENTRAL),), maven_path, pinned
        )
        winner, misses, failures, incomplete, unresolved = _race(
            ordered,
            maven_path,
            mode="stream",
            required=pinned,
            # Stellar 承接的 neoforged 构件并发多了会排队；权威源内部会重试
            # 三次，所以要给足总时长（Gradle 侧的 socket 超时是 5 分钟）。
            timeout=15.0 if metadata else (30.0 if pinned else _RACE_TIMEOUT),
            overall=45.0 if metadata else (180.0 if pinned else _RACE_OVERALL),
            authoritative=bool(pinned) and metadata,
        )
        if winner is None:
            tmp.unlink(missing_ok=True)
            # 非权威路径（普通 Maven Central 库）没有完整性问题，退回缓存
            # 副本比让 Gradle 空等更好；neoforged 路径必须等 Stellar，
            # 缓存里可能正好是别的镜像那份过期列表。
            if metadata and not pinned:
                cached_meta = self.cached(maven_path)
                if cached_meta is not None:
                    self._log(f"[镜像] {maven_path} 上游暂时不可用，使用缓存副本")
                    handler._send_file(cached_meta)
                    return
            if _is_optional(maven_path) or not (
                unresolved or (misses == 0 and (failures or incomplete))
            ):
                if unresolved and _is_optional(maven_path):
                    self._log(
                        f"[镜像] {maven_path} 拿不准是否存在，按可选项处理，交给 POM 解析"
                    )
                self._mark_missing(maven_path)
                handler._missing()
            else:
                # 权威源没答复，或者根本没有源明确说“没有”，别记成 404。
                self._log(f"[镜像] {maven_path} 上游暂时不可用，稍后重试")
                handler._send_bytes(b"", 503)
            return
        name, first, response, slot = winner
        try:
            length = response.headers.get("Content-Length")
            try:
                handler.send_response(200)
                handler.send_header("Content-Type", "application/octet-stream")
                if length:
                    handler.send_header("Content-Length", length)
                else:
                    handler.close_connection = True
                handler.end_headers()
            except _DISCONNECT_ERRORS:
                tmp.unlink(missing_ok=True)
                return
            try:
                with open(tmp, "wb") as handle:
                    handle.write(first)
                    handler.wfile.write(first)
                    while True:
                        chunk = response.read(1 << 16)
                        if not chunk:
                            break
                        handler.wfile.write(chunk)
                        handle.write(chunk)
            except _DISCONNECT_ERRORS:
                tmp.unlink(missing_ok=True)
                return
            except Exception:  # noqa: BLE001 - 头都发出去了，只能断开让 Gradle 重试
                tmp.unlink(missing_ok=True)
                handler.close_connection = True
                return
            if tmp.is_file() and tmp.stat().st_size > 0:
                os.replace(tmp, cache)
            size = cache.stat().st_size if cache.is_file() else 0
            _health.ok(name, time.monotonic() - started, size)
            if size >= 1 << 20:
                speed = size / max(time.monotonic() - started, 0.05) / 1024 / 1024
                self._log(f"[镜像] {maven_path} · {_human(size)} · {speed:.1f} MB/s（{name}）")
            return
        finally:
            _close_quietly(response)
            if slot is not None:
                slot.release()

    def head(self, maven_path: str, handler: _Handler) -> None:
        """Gradle 有时用 HEAD 探测文件是否存在，不能一律回 404。"""
        if self._is_missing(maven_path):
            handler._missing()
            return
        started = time.monotonic()
        pinned = _pinned_sources(maven_path)
        metadata = maven_path.endswith("maven-metadata.xml")
        ordered = _health.order(
            SOURCES + (("central", CENTRAL),), maven_path, pinned
        )
        winner, misses, failures, incomplete, unresolved = _race(
            ordered,
            maven_path,
            mode="head",
            timeout=15.0 if metadata else 30.0,
            overall=45.0 if metadata else (180.0 if pinned else 45.0),
            headers={"Range": "bytes=0-0"},
            stagger=0.2,
            required=pinned,
        )
        if winner is None:
            if metadata and not pinned:
                cached_meta = self.cached(maven_path)
                if cached_meta is not None:
                    handler.send_response(200)
                    handler.send_header("Content-Type", "application/octet-stream")
                    handler.send_header("Content-Length", str(cached_meta.stat().st_size))
                    handler.end_headers()
                    return
            if _is_optional(maven_path):
                # .module / 校验和缺失时 Gradle 会退回 POM，不能因为上游
                # 抖动就 503 把整个模块判死。
                self._mark_missing(maven_path)
                handler._missing()
                return
            if unresolved or (misses == 0 and (failures or incomplete)):
                handler._send_bytes(b"", 503)
            else:
                self._mark_missing(maven_path)
                handler._missing()
            return
        name, length, _response, _slot = winner
        _health.seen(name, time.monotonic() - started)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/octet-stream")
        handler.send_header("Content-Length", length)
        handler.end_headers()

    def synthesize(self, maven_path: str) -> bytes | None:
        if not (
            _MCD_MODULE.match(maven_path)
            or _NFR_PLAIN.match(maven_path)
            or _NFR_ARTIFACT.match(maven_path)
            or _NFR_MODULE.match(maven_path)
        ):
            return None
        with self._lock_for(maven_path):
            # 合成内容的结构由当前程序版本决定，旧缓存里的副本可能已经过期，
            # 不能直接复用，否则升级后仍会拿到错误的元数据。
            return self._synthesize(maven_path)

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def _synthesize(self, maven_path: str) -> bytes | None:
        match = _NFR_PLAIN.match(maven_path)
        if match:
            version, suffix = match.group(1), "." + match.group(3)
            bundled = _bundled_neoform(version, suffix)
            if bundled is not None:
                self._log(f"[镜像] 使用内置的 neoform-runtime {version}{suffix}")
                return bundled.read_bytes()

        match = _MCD_MODULE.match(maven_path)
        if match:
            version = match.group(1)
            try:
                data = minecraft_dependencies_module(mojang_version_json(version))
            except Exception as exc:  # noqa: BLE001
                self._log(f"[镜像] 生成 vanilla 依赖清单失败：{exc}")
                return None
            self._log(f"[镜像] 生成 minecraft-dependencies {version}（{len(data['variants'][0]['dependencies'])} 个库）")
            return json.dumps(data, indent=2).encode("utf-8")

        match = _NFR_MODULE.match(maven_path)
        if match:
            version = match.group(1)
            all_jar = _ensure_nfr_all_jar("net.neoforged", "neoform-runtime", version, self._log)
            if all_jar is None:
                return None
            bundled = _bundled_neoform(version, ".module")
            raw = bundled.read_bytes() if bundled is not None else fetch(maven_path)
            if raw is None:
                return None
            try:
                document = json.loads(raw.decode("utf-8"))
            except ValueError:
                return None
            _patch_all_jar(document, all_jar)
            return json.dumps(document, indent=2).encode("utf-8")

        match = _NFR_ARTIFACT.match(maven_path)
        if match:
            version = match.group(1)
            all_jar = _ensure_nfr_all_jar("net.neoforged", "neoform-runtime", version, self._log)
            if all_jar is None:
                return None
            return all_jar.read_bytes()
        return None


def _patch_all_jar(document: dict, all_jar: Path) -> None:
    """把 shadow 变体指向我们自己合成的 -all.jar，并更新校验值。"""
    import hashlib

    data = all_jar.read_bytes()
    entry = {
        "name": all_jar.name,
        "url": all_jar.name,
        "size": len(data),
        "sha512": hashlib.sha512(data).hexdigest(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sha1": hashlib.sha1(data, usedforsecurity=False).hexdigest(),
        "md5": hashlib.md5(data, usedforsecurity=False).hexdigest(),
    }
    for variant in document.get("variants", []):
        if variant.get("name") != "shadowRuntimeElements":
            continue
        files = variant.get("files") or []
        if files:
            files[0] = entry
        else:
            variant["files"] = [entry]
        return


_INIT_TEMPLATE = """\
beforeSettings {{ settings ->
    def mirrorUrl = '{mirror}'
    def mojangUrl = '{mojang}'
    // Route Mojang libs through the local proxy as well: bmclapi redirects
    // to a presigned S3 URL, and Gradle's HEAD probe gets 403 there.
    def mojangMirror = '{mirror}'
    def officialHosts = {hosts}
    def rewrite = {{ repo ->
        if (repo instanceof org.gradle.api.artifacts.repositories.MavenArtifactRepository) {{
            def u = repo.url
            if (u != null && officialHosts.contains(u.host)) {{
                repo.url = mirrorUrl
                repo.allowInsecureProtocol = true
            }} else if (u != null && u.host == 'libraries.minecraft.net') {{
                repo.url = mojangMirror
                repo.allowInsecureProtocol = true
            }}
        }}
    }}
    settings.pluginManagement.repositories.all {{ repo -> rewrite(repo) }}
    settings.pluginManagement.repositories.maven {{ name = 'DMAMirror'; url = mirrorUrl; allowInsecureProtocol = true }}
    settings.pluginManagement.repositories.maven {{ name = 'DMAMojangMirror'; url = mojangMirror; allowInsecureProtocol = true }}
    settings.pluginManagement.repositories.maven {{ name = 'DMAMojang'; url = mojangUrl }}
    settings.dependencyResolutionManagement.repositories.all {{ repo -> rewrite(repo) }}
}}

allprojects {{
    def mirrorUrl = '{mirror}'
    def mojangUrl = '{mojang}'
    // Route Mojang libs through the local proxy as well: bmclapi redirects
    // to a presigned S3 URL, and Gradle's HEAD probe gets 403 there.
    def mojangMirror = '{mirror}'
    def officialHosts = {hosts}
    def rewrite = {{ repo ->
        if (repo instanceof org.gradle.api.artifacts.repositories.MavenArtifactRepository) {{
            def u = repo.url
            if (u != null && officialHosts.contains(u.host)) {{
                repo.url = mirrorUrl
                repo.allowInsecureProtocol = true
            }} else if (u != null && u.host == 'libraries.minecraft.net') {{
                repo.url = mojangMirror
                repo.allowInsecureProtocol = true
            }}
        }}
    }}
    repositories.all {{ repo -> rewrite(repo) }}
    repositories.maven {{ name = 'DMAMirror'; url = mirrorUrl; allowInsecureProtocol = true }}
    repositories.maven {{ name = 'DMAMojangMirror'; url = mojangMirror; allowInsecureProtocol = true }}
    repositories.maven {{ name = 'DMAMojang'; url = mojangUrl }}

    // NeoFormRuntime downloads artifacts that are missing from its manifest with
    // its own HTTP client, using a hardcoded https://maven.neoforged.net base.
    // That client ignores Gradle repositories (and --offline), so a blocked host
    // stalls the build. The NeoForm / MCP configuration for 1.20.1 asks for the
    // artifacts below; resolve them through this project (i.e. through the
    // mirror) and hand the local files to NFRT so it never has to hit the
    // network itself.
    tasks.matching {{ it.name == 'createMinecraftArtifacts' }}.configureEach {{ task ->
        if (task.metaClass.respondsTo(task, 'addArtifactsToManifest')) {{
            def prj = task.project
            def coords = [
                'de.oceanlabs.mcp:mcp_config:1.20.1-20230612.114412@zip',
                'net.minecraftforge:installertools:1.2.0:fatjar',
                'net.minecraftforge:installertools:1.3.2:fatjar',
                'net.minecraftforge:mergetool:1.1.7:api',
                'net.minecraftforge:mergetool:1.1.7:fatjar',
                'net.minecraftforge:forgeflower:2.0.629.0',
                'net.minecraftforge:ForgeAutoRenamingTool:1.0.2:all',
                'net.minecraftforge:binarypatcher:1.1.1:fatjar',
                'org.jetbrains:annotations:24.0.1',
            ]
            // One detached configuration per coordinate: Gradle collapses
            // module version conflicts inside a single configuration, so
            // requesting both installertools 1.2.0 and 1.3.2 there would
            // leave only 1.3.2 in the manifest. NFRT then falls back to its
            // hardcoded downloader for 1.2.0 and stalls on the blocked host.
            coords.each {{ coordinate ->
                def cfg = prj.configurations.detachedConfiguration()
                cfg.transitive = false
                cfg.dependencies.addAllLater(prj.provider {{
                    [prj.dependencies.create(coordinate)]
                }})
                task.addArtifactsToManifest(cfg)
            }}
        }}
    }}
    buildscript {{
        def bsRewrite = rewrite
        repositories.all {{ repo -> bsRewrite(repo) }}
        repositories.maven {{ name = 'DMAMirror'; url = mirrorUrl; allowInsecureProtocol = true }}
        repositories.maven {{ name = 'DMAMojangMirror'; url = mojangMirror; allowInsecureProtocol = true }}
        repositories.maven {{ name = 'DMAMojang'; url = mojangUrl }}
    }}
}}
"""


_service: MirrorService | None = None
_service_lock = threading.Lock()


def service(
    log: Callable[[str], None] | None = None, *, fresh_port: bool = False
) -> MirrorService:
    global _service
    with _service_lock:
        if fresh_port and _service is not None:
            _service.stop()
            _service = None
        if _service is None:
            _service = MirrorService(log, fresh_port=fresh_port)
        elif log is not None:
            _service._log = log
        return _service


def prepare_launch_env(
    env: dict[str, str] | None = None,
    log: Callable[[str], None] | None = None,
    hosts: list[str] | tuple[str, ...] | None = None,
    *,
    force: bool = False,
    fresh_port: bool = False,
) -> Path | None:
    """官方仓库连不上时启动本地代理并返回 init script 路径。

    全部官方仓库都能直连时返回 None，构建保持走官方源。代理启动失败也返回
    None，不阻塞构建——让 Gradle 自己报网络错误，比直接中断更好排查。

    传入的 hosts 会与官方仓库列表合并探测：NeoGradle 这类插件在运行时才
    添加 maven.neoforged.net，构建脚本里扫不到，不能只依赖调用方传的列表。

    ``force=True`` 跳过探测，直接把全部官方仓库改写到本地镜像。用于构建
    已经因证书/网络问题失败、需要立刻重试的场景（例如探测进程能联网，但
    Gradle 用的 JDK 信任库不认这台机器的中间证书）。
    """
    candidates: list[str] = []
    for host in (*OFFICIAL_HOSTS, *(hosts or ())):
        text = str(host).strip()
        if text and text not in candidates:
            candidates.append(text)
    down = candidates if force else unreachable_hosts(candidates)
    if not down:
        return None
    if log is not None:
        if force:
            log("[镜像] 强制切换到本地镜像代理重试：" + "、".join(down))
        else:
            log(
                "[镜像] 官方仓库直连不通：" + "、".join(down) + "，自动切换本地镜像代理"
            )
    try:
        return service(log, fresh_port=fresh_port).init_script(hosts=down)
    except Exception as exc:  # noqa: BLE001 - 代理不可用时退回官方源
        if log is not None:
            log(f"[镜像] 本地代理启动失败，继续直连官方仓库：{exc}")
        return None


def shutdown() -> None:
    global _service
    with _service_lock:
        if _service is not None:
            _service.stop()
            _service = None
