"""Sandbox screenshot collection and capture (TOOL-16).

The client runs inside a controlled dev directory; this module turns the
produced screenshots into evidence the agent can inspect, copy into
``artifacts/`` and attach to a release review.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from . import vision


@dataclass
class Screenshot:
    path: str
    size: int = 0
    modified: float = 0.0
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "size": self.size,
            "modified": self.modified,
            "width": self.width,
            "height": self.height,
        }


def screenshot_dirs(game_dir: Path) -> list[Path]:
    root = Path(game_dir)
    rows = [
        root / "screenshots",
        root / "run-client" / "screenshots",
        root / "run" / "screenshots",
        root / ".minecraft" / "screenshots",
    ]
    return [item for item in rows if item.is_dir()]


def find_screenshots(game_dir: Path, *, limit: int = 20, since: float = 0.0) -> list[Screenshot]:
    rows: list[Screenshot] = []
    for folder in screenshot_dirs(game_dir):
        for path in folder.glob("*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < since:
                continue
            rows.append(Screenshot(str(path), stat.st_size, stat.st_mtime))
    rows.sort(key=lambda item: item.modified, reverse=True)
    return rows[:limit]


def latest(game_dir: Path, *, since: float = 0.0) -> Screenshot | None:
    rows = find_screenshots(game_dir, limit=1, since=since)
    return rows[0] if rows else None


def wait_for_new(game_dir: Path, *, timeout: float = 30.0, previous: float | None = None) -> Screenshot | None:
    """Wait until a new screenshot appears, then return it (bounded poll)."""
    deadline = time.monotonic() + max(1.0, timeout)
    baseline = previous if previous is not None else time.time()
    while time.monotonic() < deadline:
        item = latest(game_dir, since=baseline)
        if item:
            return item
        time.sleep(0.5)
    return None


def collect(game_dir: Path, out_dir: Path, *, limit: int = 5, since: float = 0.0) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    items = find_screenshots(game_dir, limit=limit, since=since)
    copied: list[dict] = []
    for index, item in enumerate(items, 1):
        source = Path(item.path)
        target = out / f"screenshot-{index:02d}-{source.name}"
        shutil.copy2(source, target)
        info = item.to_dict()
        info["copy"] = str(target)
        review = vision.review(target, style="screenshot")
        info["review"] = review.to_dict()
        item.width, item.height = review.metrics.get("size", [0, 0])
        info["width"], info["height"] = item.width, item.height
        copied.append(info)
    manifest = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "game_dir": str(game_dir),
        "out_dir": str(out),
        "screenshots": copied,
    }
    (out / "screenshots.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return manifest


def capture(output: Path, *, bbox: tuple[int, int, int, int] | None = None) -> Path:
    """Grab the screen (Windows). Used only when the sandbox owns the desktop."""
    try:
        from PIL import ImageGrab
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("需要 Pillow 才能截图") from exc
    image = ImageGrab.grab(bbox=bbox)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")
    return target


def review_all(folder: Path, *, style: str = "screenshot") -> list[dict]:
    rows: list[dict] = []
    for path in sorted(Path(folder).glob("*.png")):
        rows.append(vision.review(path, style=style).to_dict())
    return rows


def summarize(items: Iterable[dict]) -> str:
    rows = list(items)
    lines = ["# 截图证据", ""]
    if not rows:
        return "\n".join(lines + ["没有找到截图。"]) + "\n"
    lines.extend(["| 文件 | 尺寸 | 评分 | 问题 |", "| --- | --- | --- | --- |"])
    for item in rows:
        review = item.get("review") or {}
        issues = "；".join((review.get("issues") or [])[:2]) or "—"
        lines.append(
            f"| {Path(item.get('copy', item.get('path', ''))).name} | "
            f"{item.get('width', 0)}x{item.get('height', 0)} | "
            f"{review.get('score', 0)} | {issues} |"
        )
    return "\n".join(lines) + "\n"
