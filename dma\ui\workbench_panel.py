from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .widgets import tiny


class RunPanel(QWidget):
    """开发运行面板：启动真实服务端/客户端，显示进程与探针状态。"""

    prepare_requested = Signal(str)
    stop_requested = Signal(str)
    refresh_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel("开发运行")
        self.title.setObjectName("H2")
        self.state_label = tiny("未启动")
        head.addWidget(self.title)
        head.addStretch(1)
        head.addWidget(self.state_label)
        layout.addLayout(head)

        self._probe_support = True
        self.hint = tiny(
            "启动的是真实开发端。Forge 1.20.1 首次运行会先编译并安装开发探针，可能要几分钟。"
        )
        layout.addWidget(self.hint)

        row1 = QHBoxLayout()
        row1.setSpacing(6)
        self.server_btn = QPushButton("启动双端")
        self.server_btn.setObjectName("Primary")
        self.server_btn.setToolTip("启动开发服务端，再启动客户端并自动连接。")
        self.server_btn.clicked.connect(lambda: self.prepare_requested.emit("both"))
        self.only_server_btn = QPushButton("仅服务端")
        self.only_server_btn.setObjectName("Ghost")
        self.only_server_btn.clicked.connect(lambda: self.prepare_requested.emit("server"))
        self.client_btn = QPushButton("仅客户端")
        self.client_btn.setObjectName("Ghost")
        self.client_btn.clicked.connect(lambda: self.prepare_requested.emit("client"))
        row1.addWidget(self.server_btn, 1)
        row1.addWidget(self.only_server_btn)
        row1.addWidget(self.client_btn)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.stop_btn = QPushButton("停止全部")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.clicked.connect(lambda: self.stop_requested.emit("all"))
        self.refresh_btn = QPushButton("刷新状态")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.clicked.connect(self.refresh_requested)
        row2.addWidget(self.stop_btn)
        row2.addWidget(self.refresh_btn, 1)
        layout.addLayout(row2)

        self.status_view = QPlainTextEdit()
        self.status_view.setReadOnly(True)
        self.status_view.setPlaceholderText("进程、端口和就绪状态会显示在这里。")
        layout.addWidget(self.status_view, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("启动日志")
        self.log.setFixedHeight(150)
        layout.addWidget(self.log)

    def set_probe_support(self, supported: bool) -> None:
        self._probe_support = supported
        if supported:
            self.hint.setText(
                "启动的是真实开发端。Forge 1.20.1 首次运行会先编译并安装开发探针，可能要几分钟。"
            )
        else:
            self.hint.setText(
                "当前加载器/版本不安装游戏内探针。开发端会正常启动，"
                "请用任务日志、mcdev_run_dump 的启动日志和 GameTest 判断结果。"
            )

    def set_busy(self, busy: bool, text: str = "") -> None:
        for button in (self.server_btn, self.only_server_btn, self.client_btn):
            button.setEnabled(not busy)
        self.state_label.setText(text or ("启动中…" if busy else "未启动"))
        if busy:
            self.hint.setText(
                "正在准备开发端：先编译工程，再安装探针并等待端口就绪。"
                if self._probe_support
                else "正在编译并启动开发端；首次运行要下载依赖，请留意日志。"
            )
        elif self._probe_support:
            self.hint.setText(
                "启动的是真实开发端。Forge 1.20.1 首次运行会先编译并安装开发探针，可能要几分钟。"
            )
        else:
            self.hint.setText(
                "当前加载器/版本不安装游戏内探针。开发端会正常启动，"
                "请用任务日志、mcdev_run_dump 的启动日志和 GameTest 判断结果。"
            )

    def append_log(self, text: str) -> None:
        if not text:
            return
        self.log.appendPlainText(text[-2000:])

    def update_status(self, payload: dict | None) -> None:
        processes = (payload or {}).get("processes") or []
        if not processes:
            self.status_view.setPlainText("当前没有由工坊启动的开发端进程。")
            self.state_label.setText("未启动")
            return
        lines: list[str] = []
        running = 0
        for item in processes:
            kind = str(item.get("kind") or "")
            alive = bool(item.get("running"))
            running += 1 if alive else 0
            joined = item.get("joined")
            screen = str(item.get("lastScreen") or "")
            lines.append(
                f"{kind}: {'运行中' if alive else '已退出'}"
                f" · PID {item.get('pid') or '-'}"
                + (
                    f" · 探针端口 {item.get('port') or '-'}"
                    if self._probe_support
                    else ""
                )
                + (f" · 已进服" if joined else "")
                + (f" · 当前界面 {screen}" if screen else "")
            )
            if item.get("exitCode") not in (None, 0):
                lines.append(f"  退出码：{item.get('exitCode')}")
            if item.get("error"):
                lines.append(f"  错误：{item.get('error')}")
            log_path = str(item.get("logPath") or "")
            if log_path:
                lines.append(f"  日志：{log_path}")
        self.status_view.setPlainText("\n".join(lines))
        self.state_label.setText(f"{running} 个进程运行中" if running else "已退出")


class ArtifactPanel(QWidget):
    """成品面板：build/libs 里的真实产物。"""

    open_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.title = QLabel("构建产物")
        self.title.setObjectName("H2")
        layout.addWidget(self.title)

        self.summary = tiny("编译通过后，这里会列出 build/libs 里的真实 JAR。")
        layout.addWidget(self.summary)

        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setPlaceholderText("还没有产物。")
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.open_btn = QPushButton("打开 jar 所在目录")
        self.open_btn.setObjectName("Primary")
        self.open_btn.clicked.connect(self.open_requested)
        self.open_btn.setEnabled(False)
        row.addWidget(self.open_btn)
        layout.addLayout(row)

    def update_view(
        self,
        jars: list[dict] | None = None,
        summary: str = "",
        jar: str = "",
    ) -> None:
        jars = list(jars or [])
        if not jars:
            self.summary.setText("编译通过后，这里会列出 build/libs 里的真实 JAR。")
            self.view.setPlainText("还没有产物。")
            self.open_btn.setEnabled(False)
            return
        self.summary.setText(summary or f"{len(jars)} 个产物")
        lines = []
        for item in jars:
            name = str(item.get("name") or "")
            size = item.get("size")
            lines.append(f"- {name}" + (f"（{size} 字节）" if size is not None else ""))
        self.view.setPlainText("\n".join(lines))
        self.open_btn.setEnabled(bool(jar or jars))
