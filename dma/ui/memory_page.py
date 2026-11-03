from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..memory import MemoryStore
from .widgets import card, tiny

KINDS = ["note", "decision", "pitfall", "api", "todo"]


class MemoryDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        content: str = "",
        kind: str = "note",
        tags: str = "",
        scope: bool = True,
        title: str = "新增记忆",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)

        self.kind = QComboBox()
        self.kind.addItems(KINDS)
        self.kind.setCurrentText(kind if kind in KINDS else "note")
        self.tags = QLineEdit(tags)
        self.tags.setPlaceholderText("逗号分隔，例如：映射,1.21.1,注册")
        self.content = QPlainTextEdit()
        self.content.setPlainText(content)
        self.content.setPlaceholderText("例如：这个工程用 Yarn 1.21.1+build.3，不要写 Mojmap 名称。")
        self.scope = QCheckBox("只关联当前工程")
        self.scope.setChecked(scope)

        layout.addWidget(QLabel("类型"))
        layout.addWidget(self.kind)
        layout.addWidget(QLabel("标签"))
        layout.addWidget(self.tags)
        layout.addWidget(QLabel("内容"))
        layout.addWidget(self.content, 1)
        layout.addWidget(self.scope)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        if not self.content.toPlainText().strip():
            QMessageBox.warning(self, "内容为空", "请填写记忆内容。")
            return
        self.accept()


class MemoryPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.store: MemoryStore = main.memory
        self._rows: list[dict] = []
        self._build()
        self.refresh()
        self.main.project_changed.connect(lambda _p: self.refresh())

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        box = QVBoxLayout()
        title = QLabel("记忆库")
        title.setObjectName("H1")
        box.addWidget(title)
        self.summary = tiny("")
        box.addWidget(self.summary)
        head.addLayout(box, 1)
        self.export_btn = QPushButton("导出 JSON")
        self.export_btn.setObjectName("Ghost")
        self.export_btn.clicked.connect(self._export)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete)
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.setObjectName("Ghost")
        self.edit_btn.clicked.connect(self._edit)
        self.add_btn = QPushButton("新增记忆")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add)
        for button in (self.export_btn, self.delete_btn, self.edit_btn, self.add_btn):
            head.addWidget(button)
        root.addWidget(header)

        filters = card()
        bar = QHBoxLayout(filters)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索内容或标签…")
        self.search.textChanged.connect(self.refresh)
        self.scope = QComboBox()
        self.scope.addItem("全部工程", "")
        self.scope.addItem("仅当前工程", "current")
        self.scope.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.scope)
        root.addWidget(filters)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "类型", "内容", "标签", "工程"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 520)
        self.table.setColumnWidth(3, 160)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda _i: self._edit())
        root.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(100)
        self.detail.setPlaceholderText("选中一条记忆查看完整内容")
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.detail)

    def _project_key(self) -> str:
        project = getattr(self.main, "active_project", None)
        return str(project.root) if project is not None else ""

    def refresh(self) -> None:
        project = self._project_key() if self.scope.currentData() == "current" else ""
        if self.scope.currentData() == "current" and not project:
            self._rows = []
        else:
            self._rows = self.store.list(
                project=project, query=self.search.text().strip(), limit=500
            )
        self.table.setRowCount(0)
        for item in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            stamp = str(item.get("updated_at") or "")[:19].replace("T", " ")
            values = [
                stamp,
                str(item.get("kind") or ""),
                str(item.get("content") or "").replace("\n", " ")[:200],
                str(item.get("tags") or ""),
                str(item.get("project") or "全局"),
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(item.get("content") or ""))
                self.table.setItem(row, column, cell)
        project_text = getattr(self.main, "active_project", None)
        current = project_text.root.name if project_text is not None else "未打开工程"
        self.summary.setText(
            f"共 {self.store.count()} 条；当前工程：{current}。助手会在每轮对话前自动召回相关记忆。"
        )
        self.detail.clear()

    def _selected(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return None
        index = rows[0].row()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _show_detail(self) -> None:
        item = self._selected()
        if item is None:
            self.detail.clear()
            return
        self.detail.setPlainText(str(item.get("content") or ""))

    def _add(self) -> None:
        project = getattr(self.main, "active_project", None)
        dialog = MemoryDialog(self, scope=project is not None)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.store.add(
            dialog.content.toPlainText(),
            project=self._project_key() if dialog.scope.isChecked() else "",
            kind=dialog.kind.currentText(),
            tags=dialog.tags.text().strip(),
        )
        self.main.status("记忆已写入")
        self.refresh()

    def _edit(self) -> None:
        item = self._selected()
        if item is None:
            return
        dialog = MemoryDialog(
            self,
            content=str(item.get("content") or ""),
            kind=str(item.get("kind") or "note"),
            tags=str(item.get("tags") or ""),
            scope=bool(item.get("project")),
            title="编辑记忆",
        )
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.store.update(int(item["id"]), dialog.content.toPlainText(), tags=dialog.tags.text())
        self.main.status("记忆已更新")
        self.refresh()

    def _delete(self) -> None:
        item = self._selected()
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "删除记忆",
            "确定删除这条记忆？\n\n" + str(item.get("content") or "")[:200],
        )
        if answer != QMessageBox.Yes:
            return
        self.store.delete(int(item["id"]))
        self.main.status("记忆已删除")
        self.refresh()

    def _export(self) -> None:
        default = f"mod-agent-memory-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
        path, _ = QFileDialog.getSaveFileName(self, "导出记忆库", default, "JSON (*.json)")
        if not path:
            return
        try:
            self.store.export_json(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.main.status(f"记忆已导出：{path}")
