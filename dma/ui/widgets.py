from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
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

from .. import catalog as catalog_mod
from .. import launcher_bridge, paths
from ..project import ProjectError, create_project
from . import theme

LOADERS = ["Fabric", "Forge", "NeoForge", "Quilt", "Architectury"]
RUNTIMES = ["双端", "客户端", "服务端"]
TASKS = [
    "创建全新模组",
    "在现有工程上修改/新增功能",
    "修复崩溃/编译报错",
    "移植到其他版本/加载器",
    "重构/优化已有代码",
]
MC_VERSIONS = [
    "26.3",
    "26.2",
    "26.1.2",
    "26.1.1",
    "26.1",
    "1.21.11",
    "1.21.10",
    "1.21.9",
    "1.21.8",
    "1.21.7",
    "1.21.6",
    "1.21.5",
    "1.21.4",
    "1.21.3",
    "1.21.1",
    "1.21",
    "1.20.6",
    "1.20.4",
    "1.20.2",
    "1.20.1",
    "1.19.4",
    "1.19.2",
    "1.18.2",
    "1.16.5",
    "1.12.2",
    "1.8.9",
    "1.7.10",
]


class SelectComboBox(QComboBox):
    """只按选择切换：滚轮经过时不改当前值，避免误改版本和 API。"""

    def wheelEvent(self, event):  # noqa: N802
        event.ignore()


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


class _Worker(QObject):
    """在后台线程执行阻塞调用，结果排队回主线程。"""

    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001 - 统一在界面显示
            self.failed.emit(str(exc))


class NewProjectDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        root: str = "",
        catalog: catalog_mod.Catalog | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self._workers: list[_Worker] = []
        self._launcher_versions: list[str] = []
        self.setWindowTitle("新建模组工程")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("新建模组工程")
        title.setObjectName("H1")
        layout.addWidget(title)
        layout.addWidget(
            tiny(
                "支持 Fabric / Forge / NeoForge / Quilt / Architectury。"
                "版本和依赖都从下拉列表点选，列表来自在线 Mojang 与加载器清单，"
                "并合并启动器里已安装的实例。Fabric 工程还可以指定 Fabric API 版本。"
                "1.20.1 / 1.21.1 会直接生成可编译骨架，其他版本由 AI 核对官方模板后生成构建脚本。"
            )
        )

        form = QFormLayout()
        form.setSpacing(10)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：星尘工具")
        self.mod_id = QLineEdit()
        self.mod_id.setPlaceholderText("例如：stardust_tools（小写字母/数字/下划线）")
        self.version_status = tiny("")
        self.mc_version = SelectComboBox()
        self.mc_version.setEditable(False)
        self.mc_version.setMaxVisibleItems(24)
        self._fill_mc_versions()
        self.mc_version.currentIndexChanged.connect(self._refresh_loader_choices)

        mc_row = QWidget()
        mc_layout = QHBoxLayout(mc_row)
        mc_layout.setContentsMargins(0, 0, 0, 0)
        mc_layout.setSpacing(6)
        mc_layout.addWidget(self.mc_version, 1)
        self.refresh_versions = QPushButton("联网刷新")
        self.refresh_versions.setToolTip(
            "从 Mojang / Fabric / Forge / NeoForge / Quilt 的在线清单重新同步版本。"
        )
        self.refresh_versions.setEnabled(self.catalog is not None)
        self.refresh_versions.clicked.connect(self._sync_versions_online)
        mc_layout.addWidget(self.refresh_versions)

        self.loader = SelectComboBox()
        self.loader.addItems(LOADERS)
        self.loader.currentIndexChanged.connect(self._refresh_loader_choices)
        self.loader_version = SelectComboBox()
        self.loader_version.setEditable(False)
        self.loader_version.setMaxVisibleItems(20)
        self.fabric_api = SelectComboBox()
        self.fabric_api.setEditable(False)
        self.fabric_api.setMaxVisibleItems(20)
        self.runtime = SelectComboBox()
        self.runtime.addItems(RUNTIMES)
        self.task = SelectComboBox()
        self.task.addItems(TASKS)
        self.deps = QLineEdit()
        self.deps.setPlaceholderText("例如：MCA Reborn 7.7.x；没有就留空")
        self._refresh_loader_choices()

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
        form.addRow("MC 版本", mc_row)
        form.addRow("", self.version_status)
        form.addRow("加载器", self.loader)
        form.addRow("加载器版本", self.loader_version)
        form.addRow("Fabric API", self.fabric_api)
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

        self.auto_start = QCheckBox("创建后立即让 AI 实现需求并验证")
        self.auto_start.setChecked(True)
        self.auto_start.setToolTip(
            "勾选后直接改代码并执行 Gradle；运行时用真实开发端探针验证，不再先做规格确认。"
        )
        layout.addWidget(self.auto_start)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建工程")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        line = self.mc_version.lineEdit()
        if line is not None:
            line.editingFinished.connect(self._refresh_loader_choices)
        self._load_launcher_versions()

    @staticmethod
    def _combo_value(combo: QComboBox) -> str:
        data = combo.currentData()
        value = data if data not in (None, "") else combo.currentText()
        return str(value or "").strip()

    def _catalog_ids(self) -> list[str]:
        if self.catalog is None:
            return []
        ids: list[str] = []
        for kind in ("release", "snapshot"):
            try:
                items = self.catalog.minecraft_versions(kind)
            except Exception:  # noqa: BLE001 - 目录损坏时退回内置列表
                items = []
            for item in items:
                vid = str(item.get("id") or "").strip()
                if vid and vid not in ids:
                    ids.append(vid)
        return ids

    def _fill_mc_versions(self) -> None:
        current = self._combo_value(self.mc_version) if hasattr(self, "mc_version") else ""
        ids = self._catalog_ids()
        for vid in self._launcher_versions:
            text = str(vid or "").strip()
            if text and text not in ids:
                ids.append(text)
        if not ids:
            ids = list(MC_VERSIONS)
        self.mc_version.blockSignals(True)
        self.mc_version.clear()
        for vid in ids:
            self.mc_version.addItem(vid, vid)
        self.mc_version.blockSignals(False)
        target = current or ("1.21.1" if "1.21.1" in ids else "1.20.1")
        index = self.mc_version.findData(target)
        if index < 0:
            index = self.mc_version.findText(target)
        if index < 0 and ids:
            index = 0
        if index >= 0:
            self.mc_version.setCurrentIndex(index)
        source = "在线目录" if self._catalog_ids() else "内置兜底"
        extra = "，已合并启动器实例" if self._launcher_versions else ""
        self.version_status.setText(f"{len(ids)} 个版本（{source}{extra}）")

    def _refresh_loader_choices(self, *_args) -> None:
        mc = self._combo_value(self.mc_version)
        loader = self.loader.currentText()
        self._fill_loader_versions(loader, mc)
        self._fill_fabric_api(mc if loader == "Fabric" else "")

    def _fill_loader_versions(self, loader: str, mc: str) -> None:
        self.loader_version.blockSignals(True)
        self.loader_version.clear()
        if loader == "Architectury":
            self.loader_version.addItem("跟随 Architectury 模板")
            self.loader_version.setEnabled(False)
            self.loader_version.blockSignals(False)
            return
        self.loader_version.setEnabled(True)
        key = loader.lower()
        versions: list[str] = []
        default = ""
        if self.catalog is not None:
            try:
                raw = list(self.catalog.loader_versions(key))
                if key == "forge":
                    versions = [
                        item.split("-", 1)[1]
                        for item in raw
                        if mc and item.startswith(mc + "-")
                    ]
                elif key == "neoforge":
                    prefix = catalog_mod._neoforge_prefix(mc)
                    versions = [
                        item for item in raw if item.startswith(prefix + ".")
                    ]
                else:
                    versions = list(raw)
                versions = sorted(
                    {item for item in versions if item},
                    key=catalog_mod._version_key,
                    reverse=True,
                )[:120]
                default = self.catalog.loader_for_mc(key, mc)
            except Exception:  # noqa: BLE001 - 目录缺失时允许手填
                versions = []
                default = ""
        if default and default not in versions:
            versions.insert(0, default)
        for item in versions:
            self.loader_version.addItem(item, item)
        if default:
            index = self.loader_version.findData(default)
            if index >= 0:
                self.loader_version.setCurrentIndex(index)
        self.loader_version.blockSignals(False)

    def _fill_fabric_api(self, mc: str) -> None:
        self.fabric_api.blockSignals(True)
        self.fabric_api.clear()
        enabled = bool(mc) and self.loader.currentText() == "Fabric" and self.catalog is not None
        self.fabric_api.setEnabled(enabled)
        if not enabled:
            self.fabric_api.addItem("仅 Fabric 工程需要")
            self.fabric_api.blockSignals(False)
            return
        versions: list[str] = []
        default = ""
        try:
            raw = list(self.catalog.section("fabric").get("fabric_api") or [])
            versions = sorted(
                {str(item) for item in raw if str(item).endswith("+" + mc)},
                key=catalog_mod._version_key,
                reverse=True,
            )
            default = self.catalog.fabric_api_version(mc)
        except Exception:  # noqa: BLE001
            versions = []
            default = ""
        if default and default not in versions:
            versions.insert(0, default)
        for item in versions:
            self.fabric_api.addItem(item, item)
        if default:
            index = self.fabric_api.findData(default)
            if index >= 0:
                self.fabric_api.setCurrentIndex(index)
        self.fabric_api.blockSignals(False)

    def _load_launcher_versions(self) -> None:
        def work():
            bridge = launcher_bridge.LauncherBridge()
            found: list[str] = []
            for item in bridge.versions():
                base = str(item.base_id or item.id or "").strip()
                if base and base not in found:
                    found.append(base)
            return found

        worker = _Worker(work, self)

        def done(values):
            if isinstance(values, list):
                self._launcher_versions = [str(v) for v in values if str(v).strip()]
                self._fill_mc_versions()

        worker.done.connect(done)
        worker.failed.connect(lambda _message: None)
        self._workers.append(worker)
        worker.start()

    def _sync_versions_online(self) -> None:
        if self.catalog is None:
            return
        self.refresh_versions.setEnabled(False)
        self.version_status.setText("正在同步在线版本清单…")

        def work():
            self.catalog.sync_mojang(force=True)
            for name in ("sync_fabric", "sync_quilt", "sync_forge", "sync_neoforge"):
                try:
                    getattr(self.catalog, name)(force=False)
                except Exception:  # noqa: BLE001 - 单个加载器失败不影响其它
                    pass
            return True

        worker = _Worker(work, self)

        def done(_value):
            self.refresh_versions.setEnabled(True)
            self._fill_mc_versions()
            self._refresh_loader_choices()
            self.version_status.setText(self.version_status.text() + " · 已联网刷新")

        def failed(message):
            self.refresh_versions.setEnabled(True)
            self.version_status.setText("同步失败：" + message)

        worker.done.connect(done)
        worker.failed.connect(failed)
        self._workers.append(worker)
        worker.start()

    def _pick_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择工程根目录", self.root.text())
        if folder:
            self.root.setText(folder)

    def _accept(self) -> None:
        if not self.mod_id.text().strip():
            QMessageBox.warning(self, "缺少信息", "请填写 Mod ID。")
            return
        loader = self.loader.currentText()
        fabric_api = (
            self._combo_value(self.fabric_api) if loader == "Fabric" else ""
        )
        try:
            project = create_project(
                Path(self.root.text().strip() or str(paths.default_project_root())),
                name=self.name.text().strip(),
                mod_id=self.mod_id.text().strip(),
                mc_version=self._combo_value(self.mc_version),
                loader=loader,
                runtime=self.runtime.currentText(),
                dependencies=self.deps.text().strip(),
                loader_version=self._combo_value(self.loader_version)
                if loader != "Architectury"
                else "",
                fabric_api=fabric_api,
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
        layout.addWidget(buttons)


def document_height(text: str, width: int) -> int:
    doc = QTextDocument()
    doc.setPlainText(text)
    doc.setTextWidth(width)
    return int(doc.size().height())
