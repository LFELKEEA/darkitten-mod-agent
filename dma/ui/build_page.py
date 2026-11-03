from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import envcheck, gradle
from . import theme
from .widgets import card, tiny


class _RunSignals(QObject):
    line = Signal(str)
    finished = Signal(int, str)


class _JavaProbe(QObject):
    done = Signal(object)

    def __init__(self, tools: envcheck.ToolPaths) -> None:
        super().__init__()
        self.tools = tools

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            item = envcheck.check_java(self.tools.java)
        except Exception:
            return
        self.done.emit(item)


class BuildPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._signals = _RunSignals()
        self._signals.line.connect(self._append)
        self._signals.finished.connect(self._on_finished)
        self.runner = gradle.GradleRunner(self._signals.line.emit, self._signals.finished.emit)
        self._buttons: list[QPushButton] = []
        self._started_at = 0.0
        self._task = ""
        self._stopping = False
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._build()
        if hasattr(main, "project_changed"):
            main.project_changed.connect(self.sync_project)
        self.sync_project(getattr(main, "active_project", None))
        tools = envcheck.resolve_tools()
        self._probe = _JavaProbe(tools)
        self._probe.done.connect(self._on_java)
        self._probe.start()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        box = QVBoxLayout()
        box.setSpacing(4)
        title = QLabel("编译与运行")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "在当前工程目录执行 Gradle：编译产物在 build/libs，开发客户端会自动加载本工程，"
                "游戏目录是工程下的 run/。首次运行需要下载依赖，可能要几分钟。关闭工坊会结束正在运行的任务。"
            )
        )
        head.addLayout(box, 1)
        self.open_project_btn = QPushButton("打开工程目录")
        self.open_project_btn.setObjectName("Ghost")
        self.open_project_btn.clicked.connect(self._open_project)
        head.addWidget(self.open_project_btn)
        outer.addWidget(header)

        toolbar = card()
        bar = QVBoxLayout(toolbar)
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(8)

        project_row = QHBoxLayout()
        project_row.setSpacing(10)
        self.project_label = QLabel("未选择工程")
        self.project_label.setObjectName("H2")
        project_row.addWidget(self.project_label, 1)
        bar.addLayout(project_row)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        specs = [
            ("build", "编译", "Primary", "gradlew build：编译并生成 build/libs 里的 jar"),
            ("runClient", "开发客户端", "", "gradlew runClient：启动带本模组的开发客户端"),
            ("runServer", "开发服务端", "", "gradlew runServer：启动开发服务端，测服务端逻辑"),
            ("runDatagen", "数据生成", "", "gradlew runDatagen：生成配方、模型、标签等资源"),
        ]
        for task, label, style, tip in specs:
            button = QPushButton(label)
            if style:
                button.setObjectName(style)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, t=task, l=label: self._run(t, l))
            buttons.addWidget(button)
            self._buttons.append(button)
        buttons.addStretch(1)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        buttons.addWidget(self.stop_btn)
        bar.addLayout(buttons)

        self.status = QLabel("空闲")
        self.status.setObjectName("H2")
        bar.addWidget(self.status)
        self.detail = tiny(
            "选择工程后点「编译」。如果提示找不到 Gradle，先执行 gradle wrapper 生成 Wrapper，"
            "或安装 Gradle 8.x 并加入 PATH。"
        )
        bar.addWidget(self.detail)
        self.java_hint = tiny("")
        self.java_hint.hide()
        bar.addWidget(self.java_hint)
        outer.addWidget(toolbar)

        log_card = card()
        log_box = QVBoxLayout(log_card)
        log_box.setContentsMargins(16, 12, 16, 12)
        log_box.setSpacing(8)
        log_head = QHBoxLayout()
        log_title = QLabel("运行日志")
        log_title.setObjectName("H2")
        log_head.addWidget(log_title, 1)
        self.copy_btn = QPushButton("复制日志")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy_log)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self._clear_log)
        self.libs_btn = QPushButton("打开 build/libs")
        self.libs_btn.setObjectName("Ghost")
        self.libs_btn.clicked.connect(self._open_libs)
        log_head.addWidget(self.copy_btn)
        log_head.addWidget(self.clear_btn)
        log_head.addWidget(self.libs_btn)
        log_box.addLayout(log_head)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(8000)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.log.setPlaceholderText("点击「编译」开始。Gradle 的输出会实时显示在这里。")
        font = QFont("Consolas", 10)
        font.setStyleHint(QFont.Monospace)
        self.log.setFont(font)
        log_box.addWidget(self.log, 1)
        outer.addWidget(log_card, 1)

    # ------------------------------------------------------------------
    def sync_project(self, project) -> None:
        if project is None:
            self.project_label.setText("未选择工程")
            self.project_label.setStyleSheet(f"color: {theme.COLORS['muted']};")
        else:
            self.project_label.setText(project.label())
            self.project_label.setStyleSheet("")
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        has_project = getattr(self.main, "active_project", None) is not None
        running = self.runner.running
        for button in self._buttons:
            button.setEnabled(has_project and not running)
        self.stop_btn.setEnabled(running)
        self.open_project_btn.setEnabled(has_project)

    def _on_java(self, item) -> None:
        if item is None:
            return
        if item.status in (envcheck.STATUS_WARN, envcheck.STATUS_FAIL):
            color = theme.COLORS["warn"] if item.status == envcheck.STATUS_WARN else theme.COLORS["danger"]
            self.java_hint.setText(f"{item.name}：{item.summary}。{item.hint}")
            self.java_hint.setStyleSheet(f"color: {color};")
            self.java_hint.show()
        elif item.status == envcheck.STATUS_OK:
            self.java_hint.setText(f"{item.name}：{item.summary}")
            self.java_hint.setStyleSheet(f"color: {theme.COLORS['ok']};")
            self.java_hint.show()

    # ------------------------------------------------------------------
    def _run(self, task: str, label: str) -> None:
        if self.runner.running:
            self._status("已有任务在运行，先等它结束或点「停止」。", theme.COLORS["warn"])
            return
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。先在对话页新建工程并写入文件。", theme.COLORS["warn"])
            return
        root = Path(project.root)
        if not gradle.has_gradle_files(root):
            self._status(
                "工程里还没有 Gradle 配置文件。先让助手生成 build.gradle / settings.gradle 并写入。",
                theme.COLORS["warn"],
            )
            return
        try:
            launch = gradle.resolve_launch(root, task)
        except gradle.GradleError as exc:
            self._status(str(exc), theme.COLORS["danger"])
            return
        source = "工程 Wrapper" if launch.source == "wrapper" else "系统 Gradle"
        self._append(f"=== {label}（{source}）{time.strftime('%H:%M:%S')} ===")
        self._append(" ".join(launch.argv))
        self._task = label
        self._stopping = False
        self._started_at = time.monotonic()
        self._status(f"正在{label}…", theme.COLORS["accent"])
        self._timer.start()
        self._refresh_buttons()
        self.main.status(f"正在{label}：{project.label()}")
        try:
            self.runner.start(launch)
        except gradle.GradleError as exc:
            self._timer.stop()
            self._status(str(exc), theme.COLORS["danger"])
            return
        self._refresh_buttons()

    def _stop(self) -> None:
        if not self.runner.running:
            return
        self._stopping = True
        self._append("=== 正在停止任务 ===")
        self.runner.stop()
        self._status("正在停止…", theme.COLORS["warn"])
        self.main.status("正在停止 Gradle 任务")

    def _on_finished(self, code: int, message: str) -> None:
        self._timer.stop()
        self._refresh_buttons()
        if message:
            self._append(f"[启动失败] {message}")
        if self._stopping:
            self._status(f"{self._task}已停止", theme.COLORS["warn"])
            self.detail.setText("任务已停止。修改文件后可以重新运行。")
            self._append(f"=== {self._task}已停止 ===")
            self.main.status(f"{self._task}已停止")
            self._stopping = False
            self._task = ""
            return
        if code == 0:
            self._status(f"{self._task}完成", theme.COLORS["ok"])
            if self._task == "编译":
                self.detail.setText("编译成功，jar 在 build/libs 目录。点「复制日志」可以把结果贴回对话页。")
            else:
                self.detail.setText("任务正常结束。需要继续修改就让助手改文件，再重新运行。")
            self._append(f"=== {self._task}完成（退出码 0）===")
            self.main.status(f"{self._task}完成")
        else:
            self._status(f"{self._task}失败（退出码 {code}）", theme.COLORS["danger"])
            self.detail.setText("把上面的日志复制回对话页，任务类型选「修复崩溃 / 编译报错」，助手会按报错改文件。")
            self._append(f"=== {self._task}失败（退出码 {code}）===")
            self.main.status(f"{self._task}失败，退出码 {code}")
        self._task = ""

    def _tick(self) -> None:
        if not self.runner.running:
            self._timer.stop()
            return
        elapsed = int(time.monotonic() - self._started_at)
        self._status(f"正在{self._task}… {elapsed} 秒", theme.COLORS["accent"])

    def _status(self, text: str, color: str) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {color};")

    def _append(self, line: str) -> None:
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.copy_btn.setEnabled(bool(self.log.toPlainText().strip()))

    def _clear_log(self) -> None:
        self.log.clear()
        self.copy_btn.setEnabled(False)

    def _copy_log(self) -> None:
        text = self.log.toPlainText()
        if not text.strip():
            return
        QApplication.clipboard().setText(text)
        self.main.status("运行日志已复制到剪贴板")

    def _open_libs(self) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。", theme.COLORS["warn"])
            return
        target = Path(project.root) / "build" / "libs"
        if not target.is_dir():
            self._status("build/libs 还不存在，先点「编译」。", theme.COLORS["warn"])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _open_project(self) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。", theme.COLORS["warn"])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(project.root)))

    def shutdown(self) -> None:
        if self.runner.running:
            self.runner.stop()
