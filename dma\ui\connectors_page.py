from __future__ import annotations

import json
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..connectors import Connector, ConnectorError, test_connector
from .widgets import card, tiny


class _TestWorker(QObject):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, connector: Connector) -> None:
        super().__init__()
        self.connector = connector

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            text = test_connector(self.connector)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.done.emit(text)


class ConnectorsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.current: Connector | None = None
        self._tester: _TestWorker | None = None
        self._connectors: list[Connector] = []
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        box = QVBoxLayout()
        title = QLabel("连接器")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny("连接器是助手可调用的资料入口。请求里的 ${ENV_NAME} 会从本机环境变量读取，不把密钥写进文件。")
        )
        head.addLayout(box, 1)
        self.new_btn = QPushButton("新建连接器")
        self.new_btn.setObjectName("Primary")
        self.new_btn.clicked.connect(self.new_connector)
        head.addWidget(self.new_btn)
        root.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索连接器…")
        self.search.textChanged.connect(self.refresh)
        left_layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_row)
        left_layout.addWidget(self.list, 1)
        splitter.addWidget(left)

        right = card()
        form = QVBoxLayout(right)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)
        self.name = QLineEdit()
        self.connector_id = QLineEdit()
        self.connector_id.setPlaceholderText("例如 modrinth、my-wiki")
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText("https://api.example.com/v1")
        self.description = QLineEdit()
        self.headers = QPlainTextEdit()
        self.headers.setPlaceholderText('{"Authorization": "Bearer ${MY_TOKEN}"}')
        self.headers.setFixedHeight(100)
        self.enabled = QCheckBox("启用")
        form.addWidget(QLabel("名称"))
        form.addWidget(self.name)
        form.addWidget(QLabel("连接器 ID"))
        form.addWidget(self.connector_id)
        form.addWidget(QLabel("Base URL"))
        form.addWidget(self.base_url)
        form.addWidget(QLabel("说明"))
        form.addWidget(self.description)
        form.addWidget(QLabel("请求头（JSON，可选）"))
        form.addWidget(self.headers)
        form.addWidget(self.enabled)

        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("测试结果会显示在这里")
        self.result.setFixedHeight(120)
        form.addWidget(QLabel("连通性测试"))
        form.addWidget(self.result)
        form.addStretch(1)

        row = QHBoxLayout()
        self.builtin_label = tiny("")
        row.addWidget(self.builtin_label, 1)
        self.test_btn = QPushButton("测试")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.test_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.save_btn)
        form.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([320, 760])
        root.addWidget(splitter, 1)

    def refresh(self) -> None:
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        self._connectors = [
            item
            for item in self.main.connector_store.list()
            if not query
            or query in item.id.lower()
            or query in item.name.lower()
            or query in item.base_url.lower()
        ]
        self.list.blockSignals(True)
        self.list.clear()
        for item in self._connectors:
            suffix = "" if item.enabled else "（停用）"
            if item.builtin:
                suffix += "（内置）"
            row = QListWidgetItem(f"{item.name}{suffix}")
            row.setToolTip(item.base_url)
            self.list.addItem(row)
        self.list.blockSignals(False)
        if self._connectors:
            self.list.setCurrentRow(0)
        else:
            self.current = None
            self._clear_form()

    def _clear_form(self) -> None:
        for widget in (self.name, self.connector_id, self.base_url, self.description):
            widget.clear()
        self.headers.clear()
        self.enabled.setChecked(True)
        self.result.clear()
        self.builtin_label.setText("")
        self.delete_btn.setEnabled(False)

    def _load_row(self, row: int) -> None:
        if row < 0 or row >= len(self._connectors):
            return
        item = self._connectors[row]
        self.current = item
        self.name.setText(item.name)
        self.connector_id.setText(item.id)
        self.connector_id.setReadOnly(item.builtin)
        self.base_url.setText(item.base_url)
        self.description.setText(item.description)
        self.headers.setPlainText(json.dumps(item.headers, ensure_ascii=False, indent=2))
        self.enabled.setChecked(item.enabled)
        self.builtin_label.setText("内置连接器" if item.builtin else "自定义连接器")
        self.delete_btn.setEnabled(not item.builtin)
        self.result.clear()

    def new_connector(self) -> None:
        self.current = None
        self._clear_form()
        self.connector_id.setReadOnly(False)
        self.connector_id.setText("my-connector")
        self.name.setText("新连接器")
        self.delete_btn.setEnabled(False)
        self.name.setFocus()

    def _collect(self) -> Connector | None:
        raw = self.headers.toPlainText().strip()
        headers: dict[str, str] = {}
        if raw:
            try:
                data = json.loads(raw)
            except ValueError as exc:
                QMessageBox.warning(self, "请求头格式错误", f"不是有效的 JSON：{exc}")
                return None
            if not isinstance(data, dict):
                QMessageBox.warning(self, "请求头格式错误", "请求头必须是 JSON 对象。")
                return None
            headers = {str(k): str(v) for k, v in data.items()}
        return Connector(
            id=self.connector_id.text().strip(),
            name=self.name.text().strip() or self.connector_id.text().strip(),
            base_url=self.base_url.text().strip(),
            description=self.description.text().strip(),
            headers=headers,
            enabled=self.enabled.isChecked(),
            builtin=bool(self.current.builtin) if self.current else False,
        )

    def save(self) -> None:
        connector = self._collect()
        if connector is None:
            return
        try:
            saved = self.main.connector_store.save(connector)
        except ConnectorError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.main.status(f"连接器已保存：{saved.id}")
        self.refresh()

    def _delete(self) -> None:
        if self.current is None:
            return
        answer = QMessageBox.question(
            self, "删除连接器", f"确定删除 {self.current.name}？"
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.main.connector_store.delete(self.current.id)
        except ConnectorError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.main.status("连接器已删除")
        self.refresh()

    def _test(self) -> None:
        if self._tester is not None:
            return
        connector = self._collect()
        if connector is None:
            return
        if not connector.base_url.startswith(("http://", "https://")):
            QMessageBox.warning(self, "地址无效", "Base URL 必须以 http:// 或 https:// 开头。")
            return
        self.test_btn.setEnabled(False)
        self.result.setPlainText("正在请求…")
        tester = _TestWorker(connector)
        self._tester = tester
        tester.done.connect(self._on_test_ok)
        tester.failed.connect(self._on_test_fail)
        tester.start()

    def _on_test_ok(self, text: str) -> None:
        self._tester = None
        self.test_btn.setEnabled(True)
        self.result.setPlainText(text or "（空响应）")
        self.main.status("连接器测试成功")

    def _on_test_fail(self, message: str) -> None:
        self._tester = None
        self.test_btn.setEnabled(True)
        self.result.setPlainText(message)
        self.main.status("连接器测试失败")
