from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import paths
from ..project import ProjectError, create_project
from . import theme

LOADERS = ["Fabric", "NeoForge", "Architectury"]
RUNTIMES = ["双端", "客户端", "服务端"]
TASKS = [
    "创建全新模组",
    "在现有工程上修改/新增功能",
    "修复崩溃/编译报错",
    "移植到其他版本/加载器",
    "重构/优化已有代码",
]
MC_VERSIONS = [
    "1.21.4",
    "1.21.1",
    "1.21",
    "1.20.6",
    "1.20.4",
    "1.20.1",
    "1.19.4",
    "1.19.2",
    "1.18.2",
    "1.17.1",
    "1.16.5",
]


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setAttribute(Qt.WA_StyledBackground, True)
    return frame


def tiny(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Tiny")
    label.setWordWrap(True)
    return label


class ChatView(QScrollArea):
    """把每条消息做成独立气泡，Markdown 由 Qt 自己渲染。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._host = QWidget()
        self._host.setObjectName("Window")
        self._layout = QVBoxLayout(self._host)
        self._layout.setContentsMargins(4, 4, 8, 4)
        self._layout.setSpacing(10)
        self._layout.addStretch(1)
        self.setWidget(self._host)
        self.bubbles: list[QFrame] = []

    def add_message(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        bubble = QFrame()
        bubble.setObjectName("CardSoft" if role != "user" else "Card")
        bubble.setAttribute(Qt.WA_StyledBackground, True)
        outer = QVBoxLayout(bubble)
        outer.setContentsMargins(12, 9, 12, 10)
        outer.setSpacing(5)

        header = QHBoxLayout()
        header.setSpacing(8)
        who = QLabel("我" if role == "user" else "恶魔小猫")
        who.setStyleSheet(
            f"color: {theme.COLORS['accent'] if role != 'user' else theme.COLORS['ok']};"
            "font-weight: 600;"
        )
        stamp = QLabel(datetime.now().strftime("%H:%M"))
        stamp.setObjectName("Tiny")
        header.addWidget(who)
        header.addStretch(1)
        header.addWidget(stamp)
        outer.addLayout(header)

        view = QTextBrowser()
        view.setFrameShape(QFrame.NoFrame)
        view.setOpenExternalLinks(True)
        view.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; }")
        if role == "user":
            view.setPlainText(text)
        else:
            view.setMarkdown(text)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        view.setMinimumWidth(200)
        outer.addWidget(view)

        self._layout.insertWidget(self._layout.count() - 1, bubble)
        self.bubbles.append(bubble)
        self._fit(bubble, view)
        self._scroll_bottom()

    def _fit(self, bubble: QFrame, view: QTextBrowser) -> None:
        width = max(320, self.viewport().width() - 60)
        view.document().setTextWidth(width)
        height = view.document().size().height() + 12
        view.setFixedHeight(int(min(560, max(34, height))))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        for bubble in self.bubbles:
            view = bubble.findChild(QTextBrowser)
            if view is not None:
                self._fit(bubble, view)

    def _scroll_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_messages(self) -> None:
        for bubble in self.bubbles:
            bubble.setParent(None)
            bubble.deleteLater()
        self.bubbles.clear()


class NewProjectDialog(QDialog):
    def __init__(self, parent: QWidget | None = None, root: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("新建模组工程")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("新建模组工程")
        title.setObjectName("H1")
        layout.addWidget(title)
        layout.addWidget(
            tiny("MC 版本、加载器、Mod ID 是硬性前置信息，创建后助手才会开始写代码。")
        )

        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：星尘工具")
        self.mod_id = QLineEdit()
        self.mod_id.setPlaceholderText("例如：stardust_tools（小写字母/数字/下划线）")
        self.mc_version = QComboBox()
        self.mc_version.setEditable(True)
        self.mc_version.addItems(MC_VERSIONS)
        self.mc_version.setCurrentText("1.21.1")
        self.loader = QComboBox()
        self.loader.addItems(LOADERS)
        self.runtime = QComboBox()
        self.runtime.addItems(RUNTIMES)
        self.task = QComboBox()
        self.task.addItems(TASKS)
        self.deps = QLineEdit()
        self.deps.setPlaceholderText("例如：Fabric API 0.115.0+1.21.1；没有就留空")

        root_row = QWidget()
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)
        self.root = QLineEdit(root or str(paths.default_project_root()))
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_root)
        root_layout.addWidget(self.root, 1)
        root_layout.addWidget(pick)

        form.addRow("模组名称", self.name)
        form.addRow("Mod ID", self.mod_id)
        form.addRow("MC 版本", self.mc_version)
        form.addRow("加载器", self.loader)
        form.addRow("运行端", self.runtime)
        form.addRow("任务类型", self.task)
        form.addRow("依赖", self.deps)
        form.addRow("工程根目录", root_row)
        layout.addLayout(form)

        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("可选：特殊限制、想要的物品/方块/机器、纹理风格、是否允许 Mixin…")
        self.notes.setFixedHeight(90)
        layout.addWidget(QLabel("补充要求"))
        layout.addWidget(self.notes)

        self.auto_start = QCheckBox("创建后立即让 AI 拆解完整规格（确认后才开始施工）")
        self.auto_start.setChecked(True)
        self.auto_start.setToolTip(
            "勾选后不再追问版本信息，直接按工程信息生成规格说明书和需求清单；"
            "你确认规格后才会写代码、执行 Gradle。"
        )
        layout.addWidget(self.auto_start)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建工程")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择工程根目录", self.root.text())
        if folder:
            self.root.setText(folder)

    def _accept(self) -> None:
        if not self.mod_id.text().strip():
            QMessageBox.warning(self, "缺少信息", "请填写 Mod ID。")
            return
        try:
            project = create_project(
                Path(self.root.text().strip() or str(paths.default_project_root())),
                name=self.name.text().strip(),
                mod_id=self.mod_id.text().strip(),
                mc_version=self.mc_version.currentText().strip(),
                loader=self.loader.currentText(),
                runtime=self.runtime.currentText(),
                dependencies=self.deps.text().strip(),
                task_type=self.task.currentText(),
                notes=self.notes.toPlainText().strip(),
            )
        except (ProjectError, OSError) as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.project = project
        self.accept()


class TextPreviewDialog(QDialog):
    def __init__(self, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        header = QLabel(title)
        header.setObjectName("H2")
        layout.addWidget(header)
        view = QTextBrowser()
        view.setPlainText(text)
        view.setLineWrapMode(QTextBrowser.NoWrap)
        layout.addWidget(view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)


def document_height(text: str, width: int) -> int:
    doc = QTextDocument()
    doc.setPlainText(text)
    doc.setTextWidth(width)
    return int(doc.size().height())
