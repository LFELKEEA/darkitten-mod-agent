"""Minecraft 开发探针集成。

能力来自 yan-mc/minecraft-mod-dev（MIT License, Copyright (c) 2026 yan-mc）：
- 回环 HTTP 探针：状态、命令、UI 快照/点击、截图、运行日志；
- 通过 gradlew 启动真实开发服务端/客户端并管理进程；
- 卡片 YAML → 场景步骤 → 断言的执行器；
- Gradle build / GameTest 与探针安装。
"""

from __future__ import annotations

import re
import threading
from pathlib import Path

from . import cards, gradle, probe, process, scenario, templates
from .process import LaunchError, LaunchManager
from .probe import DEFAULT_PORTS, TOKEN, ProbeError

__all__ = [
    "DEFAULT_PORTS",
    "TOKEN",
    "ProbeError",
    "LaunchError",
    "McdevService",
    "cards",
    "gradle",
    "probe",
    "process",
    "scenario",
    "templates",
    "run_dirs",
    "shared",
]

_RUN_DIR_RE = re.compile(
    r"workingDirectory\s+project\.file\(\s*['\"]([^'\"]+)['\"]\s*\)"
)


def run_dirs(root: Path) -> list[Path]:
    """从 build.gradle 里读 run 配置目录，读不到就按常见目录猜。"""
    dirs: list[Path] = []
    seen: set[str] = set()
    gradle_file = Path(root) / "build.gradle"
    if gradle_file.is_file():
        try:
            text = gradle_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        for value in _RUN_DIR_RE.findall(text):
            clean = value.strip().strip("./\\")
            if not clean or clean in seen:
                continue
            seen.add(clean)
            dirs.append(Path(root) / clean)
    if not dirs:
        for name in ("run-server", "run-client", "run"):
            candidate = Path(root) / name
            if candidate.is_dir():
                dirs.append(candidate)
    return dirs


class McdevService:
    def __init__(self, log=None, cancel: threading.Event | None = None) -> None:
        self._log = log or (lambda _text: None)
        self._cancel = cancel or threading.Event()
        self.launches = LaunchManager(self._log, self._cancel)

    def set_log(self, log) -> None:
        """替换日志回调（Agent 每次运行会带上自己的 UI 回调）。"""
        self._log = log or (lambda _text: None)
        self.launches.set_log(self._log)

    # ------------------------------------------------------------------
    def prepare(
        self,
        project_dir: str | Path,
        kind: str,
        *,
        timeout_ms: int | None = 600_000,
        wait_join: bool = True,
        join_port: int = 25566,
        install_probe: bool = True,
    ) -> dict:
        root = Path(str(project_dir)).expanduser()
        probe_ready = templates.supports_probe(root)
        probe_info = {
            "available": probe_ready,
            "reason": "" if probe_ready else "当前加载器/版本不支持游戏内探针，改用编译结果与运行日志验证。",
        }
        if install_probe and probe_ready:
            try:
                probe_info.update(self.ensure_probe(root))
            except Exception as exc:  # noqa: BLE001 - 装探针失败也要说明原因
                raise LaunchError(f"探针安装失败：{exc}") from exc
        result = self.launches.prepare(
            root,
            kind,
            timeout_ms=timeout_ms,
            wait_join=wait_join and probe_ready,
            join_port=join_port,
            wait_probe=probe_ready,
        )
        result["probe"] = probe_info
        return result

    def stop(self, side: str) -> dict:
        kinds = ["client", "server"] if side == "all" else [side]
        return {"ok": True, "results": [self.launches.stop(kind) for kind in kinds]}

    def launch_status(self) -> dict:
        return self.launches.status()

    # ------------------------------------------------------------------
    def ensure_probe(self, project_dir: str | Path) -> dict:
        root = Path(str(project_dir)).expanduser()
        if not templates.supports_probe(root):
            raise LaunchError(
                "游戏内探针只支持 Forge 1.20.1 开发端。当前工程请用 "
                "build / runServer / runClient 的日志和 GameTest 验证。"
            )
        jar = templates.find_probe_jar(root)
        built = False
        if jar is None:
            jar = templates.build_probe(root, log=self._log, cancel=self._cancel)
            built = True
        targets = [item / "mods" for item in run_dirs(root)] or [root / "run" / "mods"]
        info = gradle.install_probe(jar, [str(item) for item in targets])
        info["built"] = built
        return info

    def install_probe(self, probe_jar: str, run_dirs_list: list[str]) -> dict:
        return gradle.install_probe(probe_jar, run_dirs_list)

    def shutdown(self) -> None:
        self.launches.shutdown()

    # ------------------------------------------------------------------
    def scenario(
        self,
        steps: list[dict] | None = None,
        *,
        card: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        translated = {"steps": list(steps or []), "warnings": []}
        if card:
            path = Path(str(card)).expanduser()
            if not path.is_file():
                raise FileNotFoundError(f"卡片不存在：{path}")
            parsed = cards.parse_yaml(path.read_text(encoding="utf-8", errors="replace"))
            translated = cards.card_to_steps(parsed if isinstance(parsed, dict) else {})
        if not translated["steps"]:
            raise ValueError("没有可执行的步骤：请提供 steps 或卡片。")
        if dry_run:
            return {
                "ok": True,
                "dryRun": True,
                "card": card,
                "warnings": translated["warnings"],
                "steps": translated["steps"],
            }
        result = scenario.run_steps(
            translated["steps"],
            cancel=self._cancel,
            warnings=translated["warnings"],
        )
        if card:
            result["card"] = card
        return result


_SHARED: McdevService | None = None
_SHARED_LOCK = threading.Lock()


def shared(log=None, cancel: threading.Event | None = None) -> McdevService:
    """进程内共享一个服务实例：UI 和 Agent 都指向同一批游戏进程。"""
    global _SHARED
    with _SHARED_LOCK:
        if _SHARED is None:
            _SHARED = McdevService(log, cancel)
        elif log is not None:
            _SHARED.set_log(log)
    return _SHARED
