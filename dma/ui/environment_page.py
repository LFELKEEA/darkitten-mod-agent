from __future__ import annotations

import html
import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import envcheck
from ..envcheck import CheckItem, ToolPaths
from . import theme
from .widgets import card, tiny

STATUS_LABELS = {
    envcheck.STATUS_OK: ("通过", theme.COLORS["ok"]),
    envcheck.STATUS_WARN: ("注意", theme.COLORS["warn"]),
    envcheck.STATUS_FAIL: ("缺失", theme.COLORS["danger"]),
    envcheck.STATUS_INFO: ("提示", theme.COLORS["accent"]),
}


class _CheckWorker(QObject):
    done = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        project_root: Path | None,
        project_dir: Path | None,
        ai,
        tools: ToolPaths,
        network_only: bool = False,
    ) -> None:
        super().__init__()
        self.project_root = project_root
        self.project_dir = project_dir
        self.ai = ai
        self.tools = tools
        self.network_only = network_only

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            if self.network_only:
                items = [envcheck.check_network()]
            else:
                items = envcheck.run_checks(
                    project_root=self.project_root,
                    project_dir=self.project_dir,
                    ai=self.ai,
                    include_network=False,
                    tools=self.tools,
                )
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.done.emit(items)


class EnvironmentPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._worker: _CheckWorker | None = None
        self._items: list[CheckItem] = []
        self._auto_ran = False
        self._context: tuple[Path | None, Path | None, ToolPaths] | None = None
        self._build()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        box = QVBoxLayout()
        title = QLabel("环境自检")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "检查 Java、JAVA_HOME、Gradle、Git、目录写入、资料源连通性和当前模型通道。"
                "Agent 不替你安装 JDK，也不执行 gradlew；这里只告诉你缺什么、怎么补。"
            )
        )
        head.addLayout(box, 1)
        self.copy_btn = QPushButton("复制报告")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_report)
        self.refresh_btn = QPushButton("重新检测")
        self.refresh_btn.setObjectName("Primary")
        self.refresh_btn.clicked.connect(self.run_check)
        head.addWidget(self.copy_btn)
        head.addWidget(self.refresh_btn)
        outer.addWidget(header)

        summary_card = card()
        summary_layout = QHBoxLayout(summary_card)
        summary_layout.setContentsMargins(16, 12, 16, 12)
        summary_box = QVBoxLayout()
        self.summary = QLabel("正在准备检测…")
        self.summary.setObjectName("H2")
        summary_box.addWidget(self.summary)
        self.summary_detail = tiny(
            "首次打开会自动检测。点击「重新检测」可以在安装 JDK、修改环境变量后复查。"
        )
        summary_box.addWidget(self.summary_detail)
        summary_layout.addLayout(summary_box, 1)
        outer.addWidget(summary_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.results_layout = QVBoxLayout(host)
        self.results_layout.setContentsMargins(0, 0, 6, 0)
        self.results_layout.setSpacing(8)
        self.results_layout.addStretch(1)
        scroll.setWidget(host)
        outer.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._auto_ran:
            return
        self._auto_ran = True
        if os.environ.get("DMA_ENV_AUTORUN", "1").strip() == "0":
            return
        QTimer.singleShot(120, self.run_check)

    def run_check(self) -> None:
        if self._worker is not None:
            return
        root_text = str(self.main.config.project_root or "").strip()
        project_root = Path(root_text) if root_text else None
        project = getattr(self.main, "active_project", None)
        project_dir = Path(project.root) if project is not None else None
        # PATH 扫描放在主线程，避免部分 Windows 目录在后台线程 stat 时卡住。
        tools = envcheck.resolve_tools()
        self._context = (project_root, project_dir, tools)
        self._items = []
        self.copy_btn.setEnabled(False)
        worker = _CheckWorker(project_root, project_dir, self.main.config.ai, tools)
        self._worker = worker
        worker.done.connect(self._on_local_done)
        worker.failed.connect(self._on_failed)
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("检测中…")
        self.summary.setText("正在检测…")
        self.summary.setStyleSheet("")
        self.summary_detail.setText("正在检查 Java、Gradle、目录写入和模型配置…")
        worker.start()

    def _start_network(self) -> None:
        if self._context is None:
            return
        project_root, project_dir, tools = self._context
        worker = _CheckWorker(
            project_root,
            project_dir,
            self.main.config.ai,
            tools,
            network_only=True,
        )
        self._worker = worker
        worker.done.connect(self._on_network_done)
        worker.failed.connect(self._on_network_failed)
        worker.start()

    def _reset_button(self) -> None:
        self._worker = None
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("重新检测")

    def _on_local_done(self, items: list[CheckItem]) -> None:
        self._worker = None
        self._items = list(items)
        self._render(items, final=False)
        self.summary_detail.setText(
            "本机检查完成，正在检测 Modrinth / Fabric Meta / GitHub 连通性…"
        )
        self._start_network()

    def _on_network_done(self, items: list[CheckItem]) -> None:
        self._items.extend(items)
        self._render(self._items, final=True)
        self.copy_btn.setEnabled(True)
        self._reset_button()

    def _on_network_failed(self, message: str) -> None:
        self._items.append(
            CheckItem(
                "network",
                "资料源连通性",
                envcheck.STATUS_WARN,
                "检测失败",
                message,
                "可以稍后点「重新检测」重试；离线时仍然可以写代码，但无法自动核对版本和依赖。",
            )
        )
        self._render(self._items, final=True)
        self.copy_btn.setEnabled(True)
        self._reset_button()
        self.main.status(f"资料源检测失败：{message}")

    def _on_failed(self, message: str) -> None:
        self._reset_button()
        self.summary.setText("检测失败")
        self.summary.setStyleSheet(f"color: {theme.COLORS['danger']};")
        self.summary_detail.setText(message)
        self.main.status(f"环境自检失败：{message}")

    def _render(self, items: list[CheckItem], *, final: bool = True) -> None:
        while self.results_layout.count():
            entry = self.results_layout.takeAt(0)
            widget = entry.widget()
            if widget is not None:
                widget.deleteLater()
        for item in items:
            self.results_layout.addWidget(self._make_row(item))
        self.results_layout.addStretch(1)

        failures = sum(1 for item in items if item.status == envcheck.STATUS_FAIL)
        warnings = sum(1 for item in items if item.status == envcheck.STATUS_WARN)
        passed = sum(1 for item in items if item.status == envcheck.STATUS_OK)
        if failures:
            self.summary.setText(f"发现 {failures} 项需要处理")
            self.summary.setStyleSheet(f"color: {theme.COLORS['danger']};")
        elif warnings:
            self.summary.setText(f"有 {warnings} 项需要注意")
            self.summary.setStyleSheet(f"color: {theme.COLORS['warn']};")
        else:
            self.summary.setText("环境检查通过")
            self.summary.setStyleSheet(f"color: {theme.COLORS['ok']};")
        if final:
            self.summary_detail.setText(
                f"通过 {passed} 项，注意 {warnings} 项，缺失 {failures} 项。"
                "提示项不是错误，按需处理即可。"
            )
            self.main.status("环境自检完成")

    def _make_row(self, item: CheckItem) -> QFrame:
        frame = QFrame()
        frame.setObjectName("CardSoft")
        frame.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(frame)
        row.setContentsMargins(14, 11, 14, 11)
        row.setSpacing(12)

        label, color = STATUS_LABELS.get(item.status, ("提示", theme.COLORS["accent"]))
        badge = QLabel(label)
        badge.setAlignment(Qt.AlignCenter)
        badge.setFixedWidth(48)
        badge.setStyleSheet(
            f"color: {color}; border: 1px solid {color}; border-radius: 7px;"
            "padding: 3px 0; font-weight: 600;"
        )
        row.addWidget(badge, 0, Qt.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(3)
        title = QLabel(item.name)
        title.setStyleSheet("font-weight: 600;")
        body.addWidget(title)
        summary = QLabel(item.summary)
        summary.setWordWrap(True)
        body.addWidget(summary)
        if item.detail:
            detail = tiny(item.detail)
            detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
            body.addWidget(detail)
        if item.hint:
            hint = QLabel()
            hint.setObjectName("Tiny")
            hint.setWordWrap(True)
            hint.setTextFormat(Qt.RichText)
            hint.setOpenExternalLinks(True)
            text = html.escape(item.hint)
            if item.url:
                text += f' <a href="{html.escape(item.url)}">打开下载页</a>'
            hint.setText(text)
            body.addWidget(hint)
        row.addLayout(body, 1)
        return frame

    def _copy_report(self) -> None:
        if not self._items:
            return
        QApplication.clipboard().setText(envcheck.report_text(self._items))
        self.main.status("环境自检报告已复制到剪贴板")
