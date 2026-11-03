from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config as config_mod
from .. import paths, shortcut, updater
from ..connectors import ConnectorStore
from ..memory import MemoryStore
from ..skills import SkillStore
from ..version import APP_NAME_CN, APP_VERSION
from . import theme
from .chat_page import ChatPage
from .connectors_page import ConnectorsPage
from .memory_page import MemoryPage
from .settings_page import SettingsPage
from .skills_page import SkillsPage
from .update_dialog import UpdateDialog
from .widgets import tiny


class _StartupUpdateWorker(QObject):
    found = Signal(object)
    failed = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            info = updater.check_for_update(self.url)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            if info is not None:
                self.found.emit(info)


class MainWindow(QMainWindow):
    project_changed = Signal(object)
    update_checked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.version = APP_VERSION
        self.config = config_mod.load_config()
        self.skill_store = SkillStore()
        self.memory = MemoryStore()
        self.connector_store = ConnectorStore()
        self.active_project = None
        self._startup_worker = None
        self._update_info = None

        self.setWindowTitle(f"{APP_NAME_CN} v{APP_VERSION}")
        icon_path = paths.resource_dir() / "dma.ico"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(self.config.window_width, self.config.window_height)
        self.setMinimumSize(1080, 720)
        self._build()
        self.active_project = self.chat_page.project
        self.project_changed.connect(self._track_project)
        QTimer.singleShot(800, self._offer_shortcut)
        QTimer.singleShot(2200, self._check_update_startup)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        central = QWidget()
        central.setObjectName("Window")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setAttribute(Qt.WA_StyledBackground, True)
        sidebar.setFixedWidth(212)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(14, 18, 14, 14)
        side.setSpacing(8)

        logo = QLabel("恶魔小猫")
        logo.setObjectName("H1")
        logo.setStyleSheet(f"color: {theme.COLORS['accent']};")
        side.addWidget(logo)
        side.addWidget(tiny("MOD AGENT · Minecraft Java"))
        side.addSpacing(14)

        self.stack = QStackedWidget()
        self.chat_page = ChatPage(self)
        self.skills_page = SkillsPage(self)
        self.connectors_page = ConnectorsPage(self)
        self.memory_page = MemoryPage(self)
        self.settings_page = SettingsPage(self)
        pages = [
            ("对话", self.chat_page),
            ("技能", self.skills_page),
            ("连接器", self.connectors_page),
            ("记忆库", self.memory_page),
            ("设置", self.settings_page),
        ]
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for index, (label, page) in enumerate(pages):
            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self.stack.setCurrentIndex(i))
            self.group.addButton(button, index)
            side.addWidget(button)
            self.stack.addWidget(page)
        self.group.button(0).setChecked(True)

        side.addStretch(1)
        self.update_banner = QPushButton("")
        self.update_banner.setObjectName("Primary")
        self.update_banner.hide()
        self.update_banner.clicked.connect(self._open_update)
        side.addWidget(self.update_banner)
        side.addWidget(tiny(f"v{APP_VERSION}"))
        side.addWidget(tiny("只写 Minecraft Java 模组"))

        layout.addWidget(sidebar)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().showMessage("就绪")

    # ------------------------------------------------------------------
    def status(self, text: str) -> None:
        self.statusBar().showMessage(str(text), 12000)

    def save_config(self) -> None:
        try:
            config_mod.save_config(self.config)
        except Exception as exc:
            self.status("保存配置失败：" + str(exc))

    def apply_theme(self) -> None:
        app = QApplication.instance()
        if app is not None:
            theme.apply_theme(app, self.config.font_size)

    def ai_ready(self) -> bool:
        ai = self.config.ai
        if ai.provider == "openai":
            return bool(ai.api_key.strip())
        return True

    def _track_project(self, project) -> None:
        self.active_project = project
        if project is not None:
            self.config.project_root = str(project.root.parent)
            self.save_config()

    def request_restart_for_update(self) -> None:
        QTimer.singleShot(300, QApplication.quit)

    # ------------------------------------------------------------------
    def _offer_shortcut(self) -> None:
        if self.config.shortcut_offered:
            return
        self.config.shortcut_offered = True
        self.save_config()
        answer = QMessageBox.question(
            self,
            "创建桌面快捷方式",
            "第一次打开恶魔小猫工坊。要在桌面创建一个快捷方式吗？\n\n"
            "以后也可以在“设置”里重新创建。",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            shortcut.create_desktop_shortcut()
        except Exception as exc:
            QMessageBox.warning(self, "快捷方式创建失败", str(exc))
            return
        self.status("桌面快捷方式已创建")

    # ------------------------------------------------------------------
    def _check_update_startup(self) -> None:
        if not str(self.config.update_url or "").strip():
            return
        worker = _StartupUpdateWorker(self.config.update_url)
        self._startup_worker = worker
        worker.found.connect(self._on_startup_update)
        worker.failed.connect(lambda _m: None)
        worker.start()

    def _on_startup_update(self, info) -> None:
        self._startup_worker = None
        self._update_info = info
        self.update_banner.setText(f"发现新版本 {info.version}")
        self.update_banner.show()
        self.status(f"发现新版本 {info.version}")
        self.update_checked.emit(info)
        answer = QMessageBox.question(
            self,
            "发现新版本",
            f"恶魔小猫工坊 {info.version} 已发布（当前 {APP_VERSION}）。\n\n现在查看更新内容？",
        )
        if answer == QMessageBox.Yes:
            self._open_update()

    def _open_update(self) -> None:
        info = self._update_info
        if info is None:
            if self._startup_worker is not None:
                self.status("正在检查更新…")
                return
            try:
                info = updater.check_for_update(self.config.update_url)
            except Exception as exc:
                QMessageBox.warning(self, "检查更新失败", str(exc))
                return
            if info is None:
                QMessageBox.information(self, "检查更新", "当前已经是最新版本。")
                return
            self._update_info = info
        UpdateDialog(self, info, self).exec()

    # ------------------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802
        self.config.window_width = max(980, self.width())
        self.config.window_height = max(680, self.height())
        self.save_config()
        try:
            self.memory.close()
        except Exception:
            pass
        super().closeEvent(event)
