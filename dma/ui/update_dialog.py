from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import updater
from .widgets import tiny


class _InstallWorker(QObject):
    progress = Signal(int, str)
    failed = Signal(str)
    done = Signal()

    def __init__(self, info: updater.UpdateInfo) -> None:
        super().__init__()
        self.info = info
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            archive = updater.download_update(
                self.info,
                progress_cb=lambda p, m: self.progress.emit(p, m),
                cancel_event=self._cancel,
            )
            payload = updater.prepare_update(archive, self.info.version)
            updater.schedule_apply(payload, restart=True)
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.done.emit()


class UpdateDialog(QDialog):
    def __init__(self, main, info: updater.UpdateInfo, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.info = info
        self._worker: _InstallWorker | None = None
        self.setWindowTitle("检查更新")
        self.setMinimumWidth(560)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)
        title = QLabel(f"发现新版本 {self.info.version}")
        title.setObjectName("H1")
        layout.addWidget(title)
        meta = tiny(
            f"当前版本 {self.main.version}  ·  "
            f"更新包 {self.info.size_text if self.info.size else '未知大小'}"
            + (f"  ·  {self.info.released}" if self.info.released else "")
        )
        layout.addWidget(meta)

        notes = QTextBrowser()
        notes.setMinimumHeight(180)
        notes.setMarkdown(
            "\n".join(f"- {item}" for item in self.info.notes) or "（本次更新没有附带说明）"
        )
        layout.addWidget(notes, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = tiny("")
        layout.addWidget(self.status)

        row = QHBoxLayout()
        if self.info.page_url:
            page = QPushButton("打开发布页")
            page.setObjectName("Ghost")
            page.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(self.info.page_url))
            )
            row.addWidget(page)
        row.addStretch(1)
        self.cancel_btn = QPushButton("关闭")
        self.cancel_btn.clicked.connect(self._close)
        row.addWidget(self.cancel_btn)
        self.install_btn = QPushButton("下载并安装")
        self.install_btn.setObjectName("Primary")
        self.install_btn.clicked.connect(self._install)
        if not updater.can_self_update():
            self.install_btn.setEnabled(False)
            self.status.setText("当前是源码运行，自动更新只在打包后的 Windows 版本可用。")
        row.addWidget(self.install_btn)
        layout.addLayout(row)

    def _close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        self.reject()

    def _install(self) -> None:
        if self._worker is not None:
            return
        answer = QMessageBox.question(
            self,
            "安装更新",
            f"将下载 {self.info.version}，校验 SHA-256 后退出程序并替换文件，随后自动重启。\n\n继续？",
        )
        if answer != QMessageBox.Yes:
            return
        self.install_btn.setEnabled(False)
        self.cancel_btn.setText("取消")
        self.progress.show()
        self.progress.setValue(1)
        worker = _InstallWorker(self.info)
        self._worker = worker
        worker.progress.connect(self._on_progress)
        worker.failed.connect(self._on_failed)
        worker.done.connect(self._on_done)
        worker.start()

    def _on_progress(self, percent: int, message: str) -> None:
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status.setText(message)

    def _on_failed(self, message: str) -> None:
        self._worker = None
        self.install_btn.setEnabled(True)
        self.cancel_btn.setText("关闭")
        self.status.setText(message)
        QMessageBox.warning(self, "更新失败", message)

    def _on_done(self) -> None:
        self.status.setText("更新已准备好，程序会退出并自动替换文件。")
        QMessageBox.information(
            self,
            "准备重启",
            "更新包已下载并通过校验。点击确定后程序退出，更新脚本会在后台替换文件并重新启动。",
        )
        self.accept()
        self.main.request_restart_for_update()
