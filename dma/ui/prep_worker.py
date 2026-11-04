from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .. import toolchain


class PrepareSignals(QObject):
    line = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class PrepareWorker(QObject):
    """在后台准备 JDK / Wrapper / Gradle，日志通过 Qt 信号回到界面线程。"""

    def __init__(
        self,
        project_root: Path,
        allow_download: bool = True,
        extra_jar_dirs: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self.project_root = Path(project_root)
        self.allow_download = allow_download
        self.extra_jar_dirs = list(extra_jar_dirs or [])
        self.signals = PrepareSignals()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            prepared = toolchain.prepare(
                self.project_root,
                log=self.signals.line.emit,
                allow_download=self.allow_download,
                extra_jar_dirs=self.extra_jar_dirs,
            )
        except Exception as exc:  # noqa: BLE001 - 交给界面显示
            self.signals.failed.emit(str(exc))
        else:
            self.signals.done.emit(prepared)
