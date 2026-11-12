"""Failure clustering and loop circuit breaker (checklist P0-04).

The agent must not retry the same edit forever.  :class:`LoopGuard` normalises
compiler/runtime errors into signatures, clusters similar failures by
location, rotates repair strategies and finally refuses to continue with a
concrete "we tried A/B/C" report.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DEFAULT_STRATEGIES = [
    "定位并做最小修复（只改报错涉及的符号/签名）",
    "回溯到上一个可编译基线，再重新应用改动",
    "更换实现路径（避开当前 API/注入点）",
    "缩小改动范围，先让最小功能编译通过",
    "生成最小复现用例并隔离问题，再回到主路径",
]

_ADDRESS = re.compile(r"0x[0-9a-fA-F]+")
_LINE_REF = re.compile(r"(\.java|\.kt|\.gradle|\.json)(?::\d+)+(?::\d+)?")
_NUMBERS = re.compile(r"\b\d{2,}\b")
_WHITESPACE = re.compile(r"\s+")


def normalize_signature(message: str, location: str = "") -> str:
    """Turn a messy compiler/runtime message into a stable signature."""
    text = str(message or "").replace("\\", "/")
    text = _ADDRESS.sub("0x?", text)
    text = _LINE_REF.sub(r"\1:?", text)
    text = re.sub(r"at [\w.$]+\([^)]*\)", "at <frame>", text)
    text = _NUMBERS.sub("#", text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    prefix = str(location or "").replace("\\", "/").strip().lower()
    payload = f"{prefix}|{text}"
    return hashlib.sha1(payload.encode("utf-8", "ignore")).hexdigest()[:12]


def _normalized_text(message: str) -> str:
    text = str(message or "").replace("\\", "/")
    text = _ADDRESS.sub("0x?", text)
    text = _LINE_REF.sub(r"\1:?", text)
    text = _NUMBERS.sub("#", text)
    return _WHITESPACE.sub(" ", text).strip().lower()


@dataclass
class Failure:
    signature: str
    kind: str
    message: str
    location: str = ""
    strategy: str = ""
    count: int = 1
    first_at: str = ""
    last_at: str = ""
    strategies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "kind": self.kind,
            "message": self.message,
            "location": self.location,
            "strategy": self.strategy,
            "count": self.count,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "strategies": list(self.strategies),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Failure":
        return cls(
            signature=str(raw.get("signature") or ""),
            kind=str(raw.get("kind") or "unknown"),
            message=str(raw.get("message") or ""),
            location=str(raw.get("location") or ""),
            strategy=str(raw.get("strategy") or ""),
            count=int(raw.get("count") or 1),
            first_at=str(raw.get("first_at") or ""),
            last_at=str(raw.get("last_at") or ""),
            strategies=[str(item) for item in raw.get("strategies") or []],
        )


class LoopGuard:
    """Track failures and decide continue / switch / stop."""

    def __init__(
        self,
        *,
        max_repeats: int = 3,
        max_total: int = 12,
        similarity: float = 0.84,
        strategies: list[str] | None = None,
    ) -> None:
        self.max_repeats = max(1, int(max_repeats))
        self.max_total = max(self.max_repeats, int(max_total))
        self.similarity = float(similarity)
        self.strategies = list(strategies or DEFAULT_STRATEGIES)
        self.failures: list[Failure] = []
        self.total_failures = 0
        self.success_streak = 0
        self.stopped = False
        self.stop_reason = ""

    # ------------------------------------------------------------------
    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _find(self, signature: str, kind: str, location: str, text: str) -> Failure | None:
        for item in self.failures:
            if item.signature == signature:
                return item
            if item.kind != kind:
                continue
            if item.location and location and item.location != location:
                continue
            ratio = difflib.SequenceMatcher(
                None, _normalized_text(item.message), text
            ).ratio()
            if ratio >= self.similarity:
                return item
        return None

    def _next_strategy(self, failure: Failure) -> str:
        for candidate in self.strategies:
            if candidate not in failure.strategies:
                return candidate
        # All strategies tried: reuse the least-used one, but the caller will
        # stop before actually executing it.
        return self.strategies[-1]

    # ------------------------------------------------------------------
    def record_failure(
        self,
        message: str,
        *,
        kind: str = "build",
        location: str = "",
        strategy: str = "",
    ) -> dict[str, Any]:
        if self.stopped:
            return {
                "action": "stop",
                "signature": "",
                "attempt": self.max_repeats,
                "strategy": "",
                "advice": self.stop_reason,
                "tried": self._tried_strategies(),
            }

        text = _normalized_text(message)
        signature = normalize_signature(message, location)
        failure = self._find(signature, kind, location, text)
        self.total_failures += 1
        self.success_streak = 0
        now = self._now()

        if failure is None:
            failure = Failure(
                signature=signature,
                kind=str(kind or "unknown"),
                message=str(message or "").strip()[:2000],
                location=str(location or ""),
                strategy=strategy or self.strategies[0],
                count=1,
                first_at=now,
                last_at=now,
                strategies=[strategy or self.strategies[0]],
            )
            self.failures.append(failure)
        else:
            failure.count += 1
            failure.last_at = now
            failure.message = str(message or "").strip()[:2000]
            if strategy and strategy not in failure.strategies:
                failure.strategies.append(strategy)

        attempt = failure.count
        if attempt >= self.max_repeats or self.total_failures >= self.max_total:
            self.stopped = True
            self.stop_reason = self._stop_message(failure)
            return {
                "action": "stop",
                "signature": failure.signature,
                "attempt": attempt,
                "strategy": "",
                "advice": self.stop_reason,
                "tried": list(failure.strategies),
                "location": failure.location,
            }

        if attempt <= 1:
            next_strategy = failure.strategies[0]
            hint = (
                f"首次出现错误：{failure.location or '（未定位）'}。"
                f"先执行最小修复：{next_strategy}。"
            )
        else:
            next_strategy = self._next_strategy(failure)
            hint = (
                f"同一位置/同类错误已出现 {attempt} 次：{failure.location or '（未定位）'}。"
                f"下一步改用：{next_strategy}。"
            )
        return {
            "action": "switch" if attempt >= 2 else "continue",
            "signature": failure.signature,
            "attempt": attempt,
            "strategy": next_strategy,
            "advice": hint,
            "tried": list(failure.strategies),
            "location": failure.location,
        }

    def _tried_strategies(self) -> list[str]:
        seen: list[str] = []
        for item in self.failures:
            for strategy in item.strategies:
                if strategy not in seen:
                    seen.append(strategy)
        return seen

    def _stop_message(self, failure: Failure) -> str:
        tried = "、".join(failure.strategies) or "（无记录）"
        return (
            f"已连续 {failure.count} 次在同一处失败（{failure.location or '未定位'}）。\n"
            f"已尝试：{tried}。\n"
            f"最后一次错误：{failure.message[:500]}\n"
            "已停止自动修复，建议人工介入：先确认目标 API/映射/注入点，再决定是否回滚或换实现。"
        )

    # ------------------------------------------------------------------
    def record_success(self, *, kind: str = "") -> None:
        # A successful build/verify means the previous failure was actually
        # repaired.  Decrement the session failure budget so long sessions on
        # big mods (100+ files, many distinct errors) don't hit a hard stop
        # after max_total *distinct* errors that were each already fixed.
        self.total_failures = max(0, self.total_failures - 1)
        self.success_streak += 1
        if kind:
            for item in self.failures:
                if item.kind == kind:
                    item.count = 0
        else:
            for item in self.failures:
                item.count = 0
        # If the guard tripped, a run of consecutive successes proves the
        # agent is making progress again: release the latch instead of
        # requiring a manual reset() that nothing ever calls.
        if self.stopped and self.success_streak >= self.max_repeats:
            self.stopped = False
            self.stop_reason = ""

    def reset(self) -> None:
        self.failures.clear()
        self.total_failures = 0
        self.success_streak = 0
        self.stopped = False
        self.stop_reason = ""

    def should_stop(self) -> bool:
        return self.stopped

    def advise(self) -> str:
        if self.stopped:
            return self.stop_reason
        if not self.failures:
            return ""
        latest = self.failures[-1]
        return (
            f"当前阻塞：{latest.message[:300]}"
            f"（已出现 {latest.count} 次，下一步：{self._next_strategy(latest)}）"
        )

    def clusters(self) -> list[dict[str, Any]]:
        rows = [
            {
                "signature": item.signature,
                "kind": item.kind,
                "location": item.location,
                "count": item.count,
                "message": item.message[:300],
                "strategies": list(item.strategies),
            }
            for item in self.failures
            if item.count > 0
        ]
        rows.sort(key=lambda row: row["count"], reverse=True)
        return rows

    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "max_repeats": self.max_repeats,
            "max_total": self.max_total,
            "total_failures": self.total_failures,
            "success_streak": self.success_streak,
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
            "failures": [item.to_dict() for item in self.failures],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LoopGuard":
        guard = cls(
            max_repeats=int(raw.get("max_repeats") or 3),
            max_total=int(raw.get("max_total") or 12),
        )
        guard.total_failures = int(raw.get("total_failures") or 0)
        guard.success_streak = int(raw.get("success_streak") or 0)
        guard.stopped = bool(raw.get("stopped"))
        guard.stop_reason = str(raw.get("stop_reason") or "")
        guard.failures = [
            Failure.from_dict(item)
            for item in raw.get("failures") or []
            if isinstance(item, dict)
        ]
        return guard
