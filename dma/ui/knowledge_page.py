from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import toolchain
from ..knowledge import SUPPORTED_FILTER, KnowledgeStore
from .widgets import card, tiny

PREVIEW_CHARS = 40000


class KnowledgePage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.store: KnowledgeStore = main.knowledge
        self._rows: list[dict] = []
        self._build()
        self.refresh()
        self.main.project_changed.connect(lambda _p: self.refresh())

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        box = QVBoxLayout()
        title = QLabel("知识库")
        title.setObjectName("H1")
        box.addWidget(title)
        self.summary = tiny("")
        box.addWidget(self.summary)
        head.addLayout(box, 1)
        self.import_btn = QPushButton("导入文件")
        self.import_btn.setObjectName("Primary")
        self.import_btn.clicked.connect(self.import_files)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.clicked.connect(self.refresh)
        for button in (self.import_btn, self.delete_btn, self.refresh_btn):
            head.addWidget(button)
        root.addWidget(header)

        filters = card()
        bar = QHBoxLayout(filters)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索文件名、来源或正文…")
        self.search.textChanged.connect(self.refresh)
        self.scope = QComboBox()
        self.scope.addItem("全部资料", "all")
        self.scope.addItem("仅当前工程", "current")
        self.scope.currentIndexChanged.connect(self.refresh)
        self.import_scope = QCheckBox("导入到当前工程")
        self.import_scope.setToolTip("勾选后只有当前工程能检索到这些文件；不勾选则所有工程共用。")
        bar.addWidget(self.search, 1)
        bar.addWidget(self.scope)
        bar.addWidget(self.import_scope)
        root.addWidget(filters)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["文件", "类型", "范围", "字数", "片段", "导入时间"])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setColumnWidth(0, 300)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 90)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 70)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._show_detail)
        root.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(150)
        self.detail.setPlaceholderText("选中一个文件查看已提取的正文；助手检索时读取的就是这份内容。")
        root.addWidget(self.detail)

    # ------------------------------------------------------------------
    def _project(self):
        return getattr(self.main, "active_project", None)

    def _project_key(self) -> str:
        project = self._project()
        return str(project.root) if project is not None else ""

    def refresh(self) -> None:
        project = self._project()
        current_only = self.scope.currentData() == "current"
        if current_only and project is None:
            self._rows = []
        else:
            self._rows = self.store.list_documents(
                project=self._project_key(),
                current_only=current_only,
                query=self.search.text().strip(),
                limit=500,
            )
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for item in self._rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            stamp = str(item.get("updated_at") or "")[:19].replace("T", " ")
            values = [
                str(item.get("name") or ""),
                str(item.get("kind") or ""),
                "当前工程" if item.get("project") else "全部工程",
                f"{int(item.get('chars') or 0):,}",
                str(item.get("chunk_count") or 0),
                stamp,
            ]
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(item.get("source") or item.get("name") or ""))
                self.table.setItem(row, column, cell)
        self.table.blockSignals(False)
        self.import_scope.setEnabled(project is not None)
        if project is None:
            self.import_scope.setChecked(False)
        current = project.root.name if project is not None else "未打开工程"
        self.summary.setText(
            f"已导入 {self.store.count()} 个文件、{self.store.chunk_count()} 个片段；"
            f"当前工程：{current}。助手会在每轮对话前自动检索相关资料，也能用工具按需再查。"
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
        document = self.store.get_document(int(item["id"]))
        if document is None:
            self.detail.clear()
            return
        source = str(document.get("source") or "")
        header = f"文件：{document.get('name')}\n"
        if source:
            header += f"来源：{source}\n"
        header += f"范围：{'当前工程' if document.get('project') else '全部工程'}"
        body = str(document.get("text") or "")
        if len(body) > PREVIEW_CHARS:
            body = body[:PREVIEW_CHARS] + "\n\n…（预览已截断）"
        self.detail.setPlainText(f"{header}\n\n{body}")

    # ------------------------------------------------------------------
    def import_files(self) -> None:
        project = self._project()
        if self.import_scope.isChecked() and project is None:
            QMessageBox.information(self, "还没有工程", "勾选了「导入到当前工程」，请先打开一个工程。")
            return
        start = str(project.root) if project is not None else str(Path.home())
        files, _ = QFileDialog.getOpenFileNames(self, "导入到知识库", start, SUPPORTED_FILTER)
        if not files:
            return
        if any(Path(name).suffix.lower() == ".jar" for name in files):
            java = toolchain.find_java()
            if java is None or java.major < 17:
                choice = QMessageBox.question(
                    self,
                    "没有可用的 JDK",
                    "本机没有 JDK 17 或更高版本。JAR 仍然会提取元数据和类结构，"
                    "但不会生成 Java 源码。\n\n"
                    "可以先去「环境」页点「一键准备」装好 JDK，再重新导入。\n\n"
                    "现在仍然导入吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if choice != QMessageBox.StandardButton.Yes:
                    return
        scope = self._project_key() if self.import_scope.isChecked() else ""
        progress = QProgressDialog("正在读取文件…", "取消", 0, len(files), self)
        progress.setWindowTitle("导入知识库")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        imported: list[str] = []
        errors: list[str] = []
        for index, filename in enumerate(files):
            progress.setValue(index)
            progress.setLabelText(f"正在读取：{Path(filename).name}")
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            def report(message: str) -> None:
                progress.setLabelText(message)
                QApplication.processEvents()

            try:
                result = self.store.add_file(
                    Path(filename), project=scope, progress=report
                )
            except Exception as exc:
                errors.append(f"{Path(filename).name}：{exc}")
            else:
                imported.append(f"{result['name']}（{result['chars']} 字）")
        progress.setValue(len(files))
        progress.close()
        self.refresh()
        if imported:
            self.main.status(f"已导入 {len(imported)} 个文件到知识库")
        if errors:
            QMessageBox.warning(
                self,
                "部分文件没有导入",
                "\n".join(errors[:10]) + ("\n…" if len(errors) > 10 else ""),
            )
        elif not imported and not progress.wasCanceled():
            self.main.status("没有导入任何文件")

    def _delete(self) -> None:
        item = self._selected()
        if item is None:
            QMessageBox.information(self, "没有选中文件", "先在列表里选中要删除的资料。")
            return
        answer = QMessageBox.question(
            self,
            "删除知识库文件",
            f"确定从知识库删除「{item.get('name')}」？\n\n"
            "只会删除知识库里提取的正文，不会动原始文件。",
        )
        if answer != QMessageBox.Yes:
            return
        self.store.delete_document(int(item["id"]))
        self.main.status(f"已从知识库删除：{item.get('name')}")
        self.refresh()
