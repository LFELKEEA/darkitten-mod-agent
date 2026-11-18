"""开发中心：版本清单、开发资料、测试中心和模组学习。

- 版本中心：同步 Mojang / Fabric / Quilt / Forge / NeoForge，读取启动器实例，
  下载 MDK、Javadoc、原版 client/server。
- 开发资料：加载器更新日志、官方文档、在线环境与下载地址。
- 测试中心：直接启动开发服务端/客户端，或用启动器启动某个实例。
- 模组学习：搜索 Modrinth、下载 JAR、反编译成源码。
- 智能参考：按功能在线搜索、下载、反编译成熟模组，直接给出可读代码片段。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import catalog as catalog_mod
from .. import launcher_bridge, mcdev, modlibrary, paths, reference, resources
from . import theme
from .widgets import SelectComboBox, card, tiny

LOADER_OPTIONS = [
    ("原版", ""),
    ("Fabric", "fabric"),
    ("Forge", "forge"),
    ("NeoForge", "neoforge"),
    ("Quilt", "quilt"),
]
CATALOG_LOADERS = ("fabric", "forge", "neoforge", "quilt")


class _Task(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, str)

    def __init__(self, fn, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            result = self.fn(self.progress.emit)
        except Exception as exc:  # noqa: BLE001 - 统一显示
            self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.done.emit(result)


def _open_url(url: str) -> None:
    if url:
        QDesktopServices.openUrl(QUrl(str(url)))


class CenterPage(QWidget):
    log_line = Signal(str)

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.catalog: catalog_mod.Catalog = getattr(main, "catalog", None) or catalog_mod.Catalog()
        self.bridge = launcher_bridge.LauncherBridge()
        self._tasks: list[_Task] = []
        self._instances: list[launcher_bridge.LaunchInstance] = []
        self._hits: list[dict] = []
        self._project = None
        self._run_busy = False
        self.log_line.connect(self._append_test_log)
        self._build()
        if hasattr(main, "project_changed"):
            main.project_changed.connect(self.sync_project)
        self.sync_project(getattr(main, "active_project", None))
        QTimer.singleShot(400, self.refresh_link)
        self._timer = QTimer(self)
        self._timer.setInterval(8000)
        self._timer.timeout.connect(self._tick_status)
        self._timer.start()
        QTimer.singleShot(1200, self.refresh_online_status)

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
        title = QLabel("开发中心")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "版本清单、加载器资料、测试进程和模组源码都在这里统一管理。"
                "启动器在运行时可以直接读取它的实例、下载源和 Mod 学习库。"
            )
        )
        head.addLayout(box, 1)
        self.link_label = QLabel("正在检测启动器…")
        self.link_label.setObjectName("Tiny")
        self.link_label.setWordWrap(True)
        head.addWidget(self.link_label, 1)
        self.refresh_link_btn = QPushButton("刷新联动")
        self.refresh_link_btn.setObjectName("Ghost")
        self.refresh_link_btn.clicked.connect(self.refresh_link)
        self.open_launcher_btn = QPushButton("打开启动器")
        self.open_launcher_btn.setObjectName("Primary")
        self.open_launcher_btn.clicked.connect(self.open_launcher)
        head.addWidget(self.refresh_link_btn)
        head.addWidget(self.open_launcher_btn)
        outer.addWidget(header)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_versions_tab(), "版本中心")
        self.tabs.addTab(self._build_docs_tab(), "开发资料")
        self.tabs.addTab(self._build_test_tab(), "测试中心")
        self.tabs.addTab(self._build_learn_tab(), "模组学习")
        self.tabs.addTab(self._build_reference_tab(), "智能参考")
        outer.addWidget(self.tabs, 1)

    # ------------------------------------------------------------------
    # 版本中心
    def _build_versions_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        splitter = QSplitter(Qt.Horizontal)

        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("启动器实例")
        left_title.setObjectName("H2")
        left_layout.addWidget(left_title)
        left_layout.addWidget(tiny("双击实例可把版本填到右侧，或直接用启动器启动。"))
        self.instance_list = QListWidget()
        self.instance_list.itemDoubleClicked.connect(self._on_instance_double)
        left_layout.addWidget(self.instance_list, 1)
        instance_row = QHBoxLayout()
        self.launch_instance_btn = QPushButton("用启动器启动")
        self.launch_instance_btn.setObjectName("Primary")
        self.launch_instance_btn.clicked.connect(self.launch_selected_instance)
        instance_row.addWidget(self.launch_instance_btn)
        self.open_instance_btn = QPushButton("打开实例目录")
        self.open_instance_btn.setObjectName("Ghost")
        self.open_instance_btn.clicked.connect(self.open_instance_folder)
        instance_row.addWidget(self.open_instance_btn)
        left_layout.addLayout(instance_row)
        splitter.addWidget(left)

        right = card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        right_title = QLabel("开发版本与资料")
        right_title.setObjectName("H2")
        right_layout.addWidget(right_title)
        right_layout.addWidget(
            tiny(
                "从 Mojang 官方 / BMCLAPI / 各加载器元数据同步。"
                "MDK 和 Javadoc 会下载到工坊的 data/catalog，原版文件下载到 data/vanilla。"
            )
        )

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Minecraft"))
        self.version_combo = SelectComboBox()
        self.version_combo.setMaxVisibleItems(24)
        self.version_combo.setMinimumWidth(120)
        self.version_combo.currentTextChanged.connect(lambda _: self._update_recommendation())
        row.addWidget(self.version_combo)
        row.addWidget(QLabel("加载器"))
        self.loader_combo = SelectComboBox()
        for label, value in LOADER_OPTIONS:
            self.loader_combo.addItem(label, value)
        self.loader_combo.currentIndexChanged.connect(lambda _: self._update_recommendation())
        row.addWidget(self.loader_combo)
        row.addStretch(1)
        right_layout.addLayout(row)

        self.recommend_label = QLabel("等待同步版本清单")
        self.recommend_label.setWordWrap(True)
        self.recommend_label.setObjectName("Tiny")
        right_layout.addWidget(self.recommend_label)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.sync_btn = QPushButton("同步全部清单")
        self.sync_btn.setObjectName("Primary")
        self.sync_btn.clicked.connect(self.sync_catalog)
        buttons.addWidget(self.sync_btn)
        self.mdk_btn = QPushButton("下载 MDK")
        self.mdk_btn.setObjectName("Secondary")
        self.mdk_btn.clicked.connect(lambda: self.download_catalog_item("mdk"))
        buttons.addWidget(self.mdk_btn)
        self.javadoc_btn = QPushButton("下载文档 / 源码")
        self.javadoc_btn.setObjectName("Secondary")
        self.javadoc_btn.clicked.connect(lambda: self.download_catalog_item("javadoc"))
        buttons.addWidget(self.javadoc_btn)
        buttons.addStretch(1)
        right_layout.addLayout(buttons)

        vanilla_row = QHBoxLayout()
        vanilla_row.setSpacing(8)
        self.client_btn = QPushButton("下载原版 client.jar")
        self.client_btn.setObjectName("Ghost")
        self.client_btn.clicked.connect(lambda: self.download_vanilla("client"))
        vanilla_row.addWidget(self.client_btn)
        self.server_btn = QPushButton("下载原版 server.jar")
        self.server_btn.setObjectName("Ghost")
        self.server_btn.clicked.connect(lambda: self.download_vanilla("server"))
        vanilla_row.addWidget(self.server_btn)
        self.open_vanilla_btn = QPushButton("打开下载目录")
        self.open_vanilla_btn.setObjectName("Ghost")
        self.open_vanilla_btn.clicked.connect(lambda: self._open_folder(paths.data_dir() / "vanilla"))
        vanilla_row.addWidget(self.open_vanilla_btn)
        vanilla_row.addStretch(1)
        right_layout.addLayout(vanilla_row)

        self.versions_progress = QProgressBar()
        self.versions_progress.setRange(0, 100)
        self.versions_progress.setVisible(False)
        right_layout.addWidget(self.versions_progress)
        self.versions_status = tiny("就绪")
        right_layout.addWidget(self.versions_status)
        right_layout.addStretch(1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter, 1)
        return page

    # ------------------------------------------------------------------
    # 开发资料
    def _build_docs_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(
            tiny(
                "这里查看各加载器的版本记录、官方资料入口和在线环境。"
                "对话页的“自动参考”是另一件事：生成模组时按需求自动搜索并反编译参考模组。"
            )
        )

        online = card()
        online_row = QHBoxLayout(online)
        online_row.setContentsMargins(12, 10, 12, 10)
        online_row.setSpacing(8)
        self.online_status = tiny("在线环境：尚未检查")
        self.online_status.setWordWrap(True)
        online_row.addWidget(self.online_status, 1)
        self.online_check_btn = QPushButton("检查在线环境")
        self.online_check_btn.setObjectName("Ghost")
        self.online_check_btn.clicked.connect(self.refresh_online_status)
        online_row.addWidget(self.online_check_btn)
        self.online_prepare_btn = QPushButton("在线准备 JDK + 目录")
        self.online_prepare_btn.setObjectName("Secondary")
        self.online_prepare_btn.clicked.connect(self.ensure_online_resources)
        online_row.addWidget(self.online_prepare_btn)
        layout.addWidget(online)

        toolbar = card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        bar.addWidget(QLabel("加载器"))
        self.docs_loader = SelectComboBox()
        for label, value in LOADER_OPTIONS:
            if value:
                self.docs_loader.addItem(label, value)
        self.docs_loader.currentIndexChanged.connect(lambda _: self.load_changelog())
        bar.addWidget(self.docs_loader)
        self.docs_refresh = QPushButton("刷新更新日志")
        self.docs_refresh.setObjectName("Primary")
        self.docs_refresh.clicked.connect(self.refresh_changelog)
        bar.addWidget(self.docs_refresh)
        self.docs_open_btn = QPushButton("打开官方发布页")
        self.docs_open_btn.setObjectName("Ghost")
        self.docs_open_btn.clicked.connect(self.open_loader_page)
        bar.addWidget(self.docs_open_btn)
        bar.addStretch(1)
        self.docs_status = tiny("")
        bar.addWidget(self.docs_status)
        layout.addWidget(toolbar)

        self.docs_view = QPlainTextEdit()
        self.docs_view.setReadOnly(True)
        self.docs_view.setPlaceholderText("这里显示加载器的最近发布与更新说明。")
        layout.addWidget(self.docs_view, 1)
        return page

    # ------------------------------------------------------------------
    # 测试中心
    def _build_test_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        toolbar = card()
        bar = QVBoxLayout(toolbar)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        project_row = QHBoxLayout()
        project_row.setSpacing(8)
        self.test_project_label = QLabel("未选择工程")
        self.test_project_label.setObjectName("H2")
        project_row.addWidget(self.test_project_label, 1)
        pick_btn = QPushButton("选择工程")
        pick_btn.setObjectName("Ghost")
        pick_btn.clicked.connect(self._pick_project)
        project_row.addWidget(pick_btn)
        self.test_open_folder = QPushButton("打开工程目录")
        self.test_open_folder.setObjectName("Ghost")
        self.test_open_folder.clicked.connect(self._open_project_folder)
        project_row.addWidget(self.test_open_folder)
        bar.addLayout(project_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(8)
        self.server_btn_test = QPushButton("启动开发服务端")
        self.server_btn_test.setObjectName("Primary")
        self.server_btn_test.clicked.connect(lambda: self.start_mcdev("server"))
        run_row.addWidget(self.server_btn_test)
        self.client_btn_test = QPushButton("启动开发客户端")
        self.client_btn_test.setObjectName("Primary")
        self.client_btn_test.clicked.connect(lambda: self.start_mcdev("client"))
        run_row.addWidget(self.client_btn_test)
        self.both_btn_test = QPushButton("服务端 + 客户端")
        self.both_btn_test.setObjectName("Secondary")
        self.both_btn_test.clicked.connect(lambda: self.start_mcdev("both"))
        run_row.addWidget(self.both_btn_test)
        self.stop_btn_test = QPushButton("停止全部")
        self.stop_btn_test.setObjectName("Ghost")
        self.stop_btn_test.clicked.connect(self.stop_mcdev)
        run_row.addWidget(self.stop_btn_test)
        run_row.addStretch(1)
        bar.addLayout(run_row)

        extra_row = QHBoxLayout()
        extra_row.setSpacing(8)
        self.launch_mc_btn = QPushButton("用启动器启动选中实例")
        self.launch_mc_btn.setObjectName("Secondary")
        self.launch_mc_btn.clicked.connect(self.launch_selected_instance)
        extra_row.addWidget(self.launch_mc_btn)
        self.test_status = tiny("空闲")
        extra_row.addWidget(self.test_status, 1)
        bar.addLayout(extra_row)
        layout.addWidget(toolbar)

        self.test_log = QPlainTextEdit()
        self.test_log.setReadOnly(True)
        self.test_log.setPlaceholderText("开发端启动日志会显示在这里。")
        layout.addWidget(self.test_log, 1)
        return page

    # ------------------------------------------------------------------
    # 模组学习
    def _build_learn_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        toolbar = card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        self.learn_query = QLineEdit()
        self.learn_query.setPlaceholderText("搜索 Modrinth 模组，例如 sodium / create")
        self.learn_query.returnPressed.connect(self.search_mods)
        bar.addWidget(self.learn_query, 1)
        bar.addWidget(QLabel("加载器"))
        self.learn_loader = SelectComboBox()
        for label, value in LOADER_OPTIONS:
            self.learn_loader.addItem(label, value)
        bar.addWidget(self.learn_loader)
        bar.addWidget(QLabel("Minecraft"))
        self.learn_mc = SelectComboBox()
        self.learn_mc.setMaxVisibleItems(24)
        self.learn_mc.setMinimumWidth(110)
        bar.addWidget(self.learn_mc)
        search_btn = QPushButton("搜索")
        search_btn.setObjectName("Primary")
        search_btn.clicked.connect(self.search_mods)
        bar.addWidget(search_btn)
        layout.addWidget(toolbar)

        splitter = QSplitter(Qt.Horizontal)
        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        self.learn_results = QListWidget()
        self.learn_results.currentRowChanged.connect(self._on_mod_selected)
        left_layout.addWidget(self.learn_results, 1)
        splitter.addWidget(left)

        right = card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        self.mod_title = QLabel("选择一个模组")
        self.mod_title.setObjectName("H2")
        self.mod_title.setWordWrap(True)
        right_layout.addWidget(self.mod_title)
        self.mod_desc = tiny("")
        right_layout.addWidget(self.mod_desc)
        right_layout.addWidget(QLabel("版本"))
        self.mod_versions = QComboBox()
        right_layout.addWidget(self.mod_versions)
        self.mod_status = tiny("就绪")
        self.mod_status.setWordWrap(True)
        right_layout.addWidget(self.mod_status)
        self.mod_progress = QProgressBar()
        self.mod_progress.setRange(0, 100)
        self.mod_progress.setVisible(False)
        right_layout.addWidget(self.mod_progress)
        right_layout.addStretch(1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.mod_download_btn = QPushButton("下载 JAR")
        self.mod_download_btn.setObjectName("Secondary")
        self.mod_download_btn.clicked.connect(self.download_mod)
        row.addWidget(self.mod_download_btn)
        self.mod_decompile_btn = QPushButton("反编译学习")
        self.mod_decompile_btn.setObjectName("Primary")
        self.mod_decompile_btn.clicked.connect(self.decompile_mod)
        row.addWidget(self.mod_decompile_btn)
        self.mod_open_btn = QPushButton("打开源码目录")
        self.mod_open_btn.setObjectName("Ghost")
        self.mod_open_btn.clicked.connect(self.open_mod_sources)
        row.addWidget(self.mod_open_btn)
        right_layout.addLayout(row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        return page

    # ------------------------------------------------------------------
    # 智能参考
    def _build_reference_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        toolbar = card()
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(12, 10, 12, 10)
        bar.setSpacing(8)
        self.ref_query = QLineEdit()
        self.ref_query.setPlaceholderText(
            "描述要实现的功能，例如：自定义村民交易界面 / 海洋维度 BOSS / 潮汐机器"
        )
        self.ref_query.returnPressed.connect(self.auto_reference)
        bar.addWidget(self.ref_query, 1)
        bar.addWidget(QLabel("加载器"))
        self.ref_loader = SelectComboBox()
        for label, value in LOADER_OPTIONS:
            if value:
                self.ref_loader.addItem(label, value)
        bar.addWidget(self.ref_loader)
        bar.addWidget(QLabel("Minecraft"))
        self.ref_mc = SelectComboBox()
        self.ref_mc.setMaxVisibleItems(24)
        self.ref_mc.setMinimumWidth(110)
        bar.addWidget(self.ref_mc)
        bar.addWidget(QLabel("参考数量"))
        self.ref_mods = QComboBox()
        self.ref_mods.addItems(["1", "2", "3"])
        bar.addWidget(self.ref_mods)
        self.ref_btn = QPushButton("自动搜索并反编译")
        self.ref_btn.setObjectName("Primary")
        self.ref_btn.clicked.connect(self.auto_reference)
        bar.addWidget(self.ref_btn)
        layout.addWidget(toolbar)

        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.ref_status = tiny("输入功能描述后自动搜索同加载器、同 MC 版本的成熟模组。")
        self.ref_status.setWordWrap(True)
        status_row.addWidget(self.ref_status, 1)
        self.ref_open_btn = QPushButton("打开参考源码目录")
        self.ref_open_btn.setObjectName("Ghost")
        self.ref_open_btn.clicked.connect(self.open_reference_sources)
        status_row.addWidget(self.ref_open_btn)
        layout.addLayout(status_row)

        self.ref_progress = QProgressBar()
        self.ref_progress.setRange(0, 100)
        self.ref_progress.setVisible(False)
        layout.addWidget(self.ref_progress)

        self.ref_view = QPlainTextEdit()
        self.ref_view.setReadOnly(True)
        self.ref_view.setPlaceholderText(
            "这里显示识别到的功能、已反编译的模组、相关类与代码片段。\n"
            "片段只读参考，不要整段复制进工程。"
        )
        layout.addWidget(self.ref_view, 1)
        return page

    # ------------------------------------------------------------------
    # 通用
    def _run(self, fn, done, failed=None, progress=None) -> _Task:
        task = _Task(fn, self)
        task._callbacks = (done, failed)  # type: ignore[attr-defined]
        self._tasks.append(task)
        # 必须连到 CenterPage 的绑定方法，Qt 才会把回调排队到主线程；
        # 直接连 lambda 会在后台线程里改控件。
        task.done.connect(self._task_done)
        task.failed.connect(self._task_failed)
        if progress is not None:
            task.progress.connect(progress)
        task.start()
        return task

    def _task_done(self, result) -> None:
        task = self.sender()
        if task not in self._tasks:
            return
        self._tasks.remove(task)
        callbacks = getattr(task, "_callbacks", None)
        if callbacks and callbacks[0] is not None:
            callbacks[0](result)

    def _task_failed(self, message: str) -> None:
        task = self.sender()
        if task not in self._tasks:
            return
        self._tasks.remove(task)
        callbacks = getattr(task, "_callbacks", None)
        callback = callbacks[1] if callbacks else None
        if callback is not None:
            callback(message)
        else:
            self.main.status("任务失败：" + message)

    def _open_folder(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        self.main.open_folder(path)

    def _tick_status(self) -> None:
        if not self._run_busy:
            self._refresh_test_status()

    # ------------------------------------------------------------------
    # 启动器联动
    def refresh_link(self) -> None:
        self.refresh_link_btn.setEnabled(False)
        self.link_label.setText("正在检测启动器…")

        def work(_progress):
            return self.bridge.about()

        def done(info):
            self.refresh_link_btn.setEnabled(True)
            mode = info.get("mode")
            if info.get("online"):
                self.link_label.setText(
                    f"已连接启动器 v{info.get('version') or '?'} · {info.get('game_dir') or ''}"
                )
            elif mode == "files":
                self.link_label.setText(
                    "启动器未运行，已读取本地版本目录；打开启动器后可下载与反编译。"
                )
            else:
                self.link_label.setText("没有找到 Darkitten 启动器：" + str(info.get("error") or ""))
            self._load_instances()
            self._reload_version_options()

        def failed(message):
            self.refresh_link_btn.setEnabled(True)
            self.link_label.setText("检测启动器失败：" + message)
            self._reload_version_options()

        self._run(work, done, failed=failed)

    def _load_instances(self) -> None:
        self.instance_list.clear()
        self._instances = []

        def work(_progress):
            return self.bridge.recommended_instances()

        def done(items):
            self._instances = list(items or [])
            for item in self._instances:
                label = item.id
                bits = []
                if item.loader:
                    bits.append(item.loader)
                if item.loader_version:
                    bits.append(item.loader_version)
                if item.base_id and item.base_id != item.id and not item.loader:
                    bits.append("基础 " + item.base_id)
                text = label + ("  · " + " / ".join(bits) if bits else "  · 原版")
                entry = QListWidgetItem(text)
                entry.setData(Qt.UserRole, item.id)
                self.instance_list.addItem(entry)
            self.versions_status.setText(f"启动器实例：{len(self._instances)} 个")

        self._run(work, done, failed=lambda m: self.versions_status.setText("读取实例失败：" + m))

    def _selected_instance(self) -> launcher_bridge.LaunchInstance | None:
        row = self.instance_list.currentRow()
        if 0 <= row < len(self._instances):
            return self._instances[row]
        return None

    def _on_instance_double(self, _item) -> None:
        item = self._selected_instance()
        if item is None:
            return
        if item.base_id:
            self._select_text(self.version_combo, item.base_id)
        elif item.id and item.id[:2].isdigit():
            self._select_text(self.version_combo, item.id)
        for index in range(self.loader_combo.count()):
            if self.loader_combo.itemData(index) == item.loader:
                self.loader_combo.setCurrentIndex(index)
                break
        self._update_recommendation()

    def launch_selected_instance(self) -> None:
        item = self._selected_instance()
        if item is None:
            self.main.status("先在左侧选择一个启动器实例。")
            return
        try:
            result = self.bridge.launch(item.id)
        except Exception as exc:  # noqa: BLE001
            self.main.status(f"启动失败：{exc}")
            return
        self.versions_status.setText(f"已请求启动器启动 {result.get('version') or item.id}")

    def open_launcher(self) -> None:
        if self.bridge.link.online:
            try:
                self.bridge.activate()
                return
            except Exception:  # noqa: BLE001
                pass
        home = Path(self.bridge.link.app_dir or "")
        if not home.is_dir():
            self.main.status("没有找到启动器目录。")
            return
        for name in ("DarkittenLauncher.exe", "launch.bat", "启动.bat"):
            target = home / name
            if target.is_file():
                try:
                    if target.suffix.lower() == ".exe":
                        import subprocess

                        subprocess.Popen([str(target)], cwd=str(home))
                    else:
                        import os

                        os.startfile(str(target))  # noqa: S606
                    return
                except Exception as exc:  # noqa: BLE001
                    self.main.status(f"打开启动器失败：{exc}")
                    return
        self._open_folder(home)

    def open_instance_folder(self) -> None:
        item = self._selected_instance()
        if item is None:
            return
        self._open_folder(Path(item.game_dir or item.mods_dir or "."))

    # ------------------------------------------------------------------
    # 版本/资料
    @staticmethod
    def _select_text(combo: QComboBox, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        index = combo.findText(text)
        if index < 0:
            combo.addItem(text)
            index = combo.count() - 1
        combo.setCurrentIndex(index)

    def _reload_version_options(self) -> None:
        """在线读取完整正式版本清单；目录为空时也不只给几个兜底版本。"""

        def work(_progress):
            ids: list[str] = []
            try:
                from ..studio_engine import versions as versions_mod

                ids = [str(vid) for vid in versions_mod.minecraft_release_ids() if vid]
            except Exception:  # noqa: BLE001 - 联网失败时退回本地目录
                ids = []
            for item in self.catalog.minecraft_ids("release"):
                vid = str(item or "")
                if vid and vid not in ids:
                    ids.append(vid)
            return ids

        def done(ids):
            self._apply_version_options(ids)

        self._run(
            work,
            done,
            failed=lambda m: self.versions_status.setText("读取版本清单失败：" + m),
        )

    def _apply_version_options(self, ids: object) -> None:
        values = [str(item) for item in (ids or []) if str(item).strip()]
        if not values:
            from ..studio_bridge import MC_VERSIONS

            values = list(MC_VERSIONS)
        current = self.version_combo.currentText().strip()
        learn_current = self.learn_mc.currentText().strip()
        ref_current = self.ref_mc.currentText().strip()
        for combo, keep in (
            (self.version_combo, current),
            (self.learn_mc, learn_current),
            (self.ref_mc, ref_current),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(values)
            combo.blockSignals(False)
            self._select_text(combo, keep)
            if combo.currentIndex() < 0:
                combo.setCurrentIndex(0)
        self._update_recommendation()

    def _update_recommendation(self) -> None:
        mc = self.version_combo.currentText().strip()
        loader = str(self.loader_combo.currentData() or "")
        if not mc:
            self.recommend_label.setText("等待选择 Minecraft 版本")
            return
        try:
            info = self.catalog.recommendations(mc, loader)
        except Exception as exc:  # noqa: BLE001
            self.recommend_label.setText("读取推荐版本失败：" + str(exc))
            return
        if loader:
            version = info.get("loader_version") or "未同步到匹配版本"
            parts = [f"{loader} {version}"]
            if info.get("fabric_api"):
                parts.append("Fabric API " + info["fabric_api"])
            if info.get("yarn"):
                parts.append("Yarn " + info["yarn"])
            if info.get("quilt_mappings"):
                parts.append("Quilt Mappings " + info["quilt_mappings"])
            self.recommend_label.setText(" · ".join(parts))
        else:
            loaders = info.get("loaders") or {}
            parts = []
            for name in CATALOG_LOADERS:
                item = loaders.get(name) or {}
                version = item.get("loader_version") or ""
                if version:
                    parts.append(f"{name} {version}")
            self.recommend_label.setText(
                ("Minecraft " + mc + " 推荐：" + " · ".join(parts))
                if parts
                else "还没有同步到这个版本的加载器数据，先点“同步全部清单”。"
            )

    def _catalog_item(self, kind: str) -> dict:
        mc = self.version_combo.currentText().strip()
        loader = str(self.loader_combo.currentData() or "")
        if not loader:
            raise RuntimeError("先选择加载器（Fabric / Forge / NeoForge / Quilt）。")
        return self.catalog.mdk(loader, mc) if kind == "mdk" else self.catalog.javadoc(loader, mc)

    def download_catalog_item(self, kind: str) -> None:
        try:
            item = self._catalog_item(kind)
        except Exception as exc:  # noqa: BLE001
            self.main.status(str(exc))
            return
        if not item:
            self.main.status("没有找到对应的下载地址，请先同步版本清单。")
            return
        dest = paths.data_dir() / "catalog" / ("mdk" if kind == "mdk" else "javadoc")
        self.versions_progress.setVisible(True)
        self.versions_progress.setValue(0)
        self.versions_status.setText(f"正在下载 {item.get('name')}…")

        def work(progress):
            return self.catalog.download(item, dest_dir=dest, progress=progress)

        def done(path):
            self.versions_progress.setVisible(False)
            self.versions_status.setText(f"已保存：{path}")
            self.main.status(f"已下载 {Path(path).name}")

        self._run(
            work,
            done,
            failed=lambda m: (self.versions_progress.setVisible(False), self.versions_status.setText("下载失败：" + m)),
            progress=lambda value, text: (self.versions_progress.setValue(value), self.versions_status.setText(text)),
        )

    def download_vanilla(self, kind: str) -> None:
        mc = self.version_combo.currentText().strip()
        if not mc:
            self.main.status("先选择 Minecraft 版本。")
            return
        self.versions_progress.setVisible(True)
        self.versions_progress.setValue(0)
        self.versions_status.setText(f"正在下载 {mc} {kind}…")

        def work(progress):
            return self.bridge.download_vanilla(mc, kind, paths.data_dir() / "vanilla", progress=progress)

        def done(path):
            self.versions_progress.setVisible(False)
            self.versions_status.setText(f"已保存：{path}")

        self._run(
            work,
            done,
            failed=lambda m: (self.versions_progress.setVisible(False), self.versions_status.setText("下载失败：" + m)),
            progress=lambda value, text: (self.versions_progress.setValue(value), self.versions_status.setText(text)),
        )

    def sync_catalog(self) -> None:
        self.sync_btn.setEnabled(False)
        self.versions_progress.setVisible(True)
        self.versions_progress.setValue(0)
        self.versions_status.setText("正在同步版本清单，首次可能需要一两分钟…")

        def work(progress):
            def report(text: str) -> None:
                progress(0, str(text))

            return self.catalog.refresh(report)

        def done(report):
            self.sync_btn.setEnabled(True)
            self.versions_progress.setVisible(False)
            self.versions_status.setText(report.text())
            self._reload_version_options()
            self.load_changelog()
            self.main.status("版本清单同步完成")

        self._run(
            work,
            done,
            failed=lambda m: (
                self.sync_btn.setEnabled(True),
                self.versions_progress.setVisible(False),
                self.versions_status.setText("同步失败：" + m),
            ),
            progress=lambda value, text: self.versions_status.setText(text),
        )

    # ------------------------------------------------------------------
    # 开发资料
    def load_changelog(self) -> None:
        loader = str(self.docs_loader.currentData() or "")
        if not loader:
            return
        entries = self.catalog.changelog(loader)
        if not entries:
            self.docs_status.setText("还没有缓存。")
            self.docs_view.setPlainText(
                "还没有这个加载器的版本记录。点“刷新更新日志”会联网同步："
                "优先 GitHub Releases，其次 Maven 版本库，最后用 GitHub Tags 兜底。"
            )
            return
        blocks = []
        for item in entries[:8]:
            blocks.append(
                "\n".join(
                    [
                        f"# {item.get('name') or item.get('tag') or ''}",
                        f"tag: {item.get('tag') or ''}    发布: {item.get('published') or ''}",
                        str(item.get("body") or "")[:6000],
                        "",
                    ]
                )
            )
        self.docs_view.setPlainText("\n".join(blocks))
        section = self.catalog.section("changelogs").get(loader) or {}
        source = catalog_mod.CHANGELOG_SOURCE_LABEL.get(
            str(section.get("source") or ""), ""
        )
        self.docs_status.setText(
            f"{loader}：{len(entries)} 条记录" + (f"（{source}）" if source else "")
        )

    def refresh_changelog(self) -> None:
        loader = str(self.docs_loader.currentData() or "")
        if not loader:
            return
        label = catalog_mod.LOADER_LABEL.get(loader, loader)
        self.docs_refresh.setEnabled(False)
        self.docs_status.setText(f"正在同步 {label} 版本记录…")
        self.docs_view.setPlainText("正在联网同步，首次可能需要几秒…")

        def work(progress):
            return self.catalog.sync_changelogs(
                lambda text: progress(0, str(text)), force=True
            )

        def done(entry):
            self.docs_refresh.setEnabled(True)
            self.load_changelog()
            if not entry.count:
                self.docs_status.setText(
                    "同步失败：" + (entry.message or "没有取到版本记录")
                )

        self._run(
            work,
            done,
            failed=lambda message: (
                self.docs_refresh.setEnabled(True),
                self.docs_status.setText("同步失败：" + message),
            ),
            progress=lambda _value, text: self.docs_status.setText(str(text)),
        )

    def open_loader_page(self) -> None:
        loader = str(self.docs_loader.currentData() or "")
        urls = {
            "fabric": "https://fabricmc.net/develop/",
            "forge": "https://files.minecraftforge.net/",
            "neoforge": "https://neoforged.net/",
            "quilt": "https://quiltmc.org/",
        }
        _open_url(urls.get(loader, "https://minecraft.net/"))

    # ------------------------------------------------------------------
    # 测试中心
    def sync_project(self, project) -> None:
        self._project = project
        if project is None:
            self.test_project_label.setText("未选择工程")
        else:
            self.test_project_label.setText(str(getattr(project, "root", project)))

    def _pick_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "打开模组工程", str(getattr(self._project, "root", "") or paths.default_project_root())
        )
        if not folder:
            return
        from .. import project as project_mod

        try:
            project = project_mod.load_project(Path(folder))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        self.main.chat_page.open_project_path(str(project.root))

    def _open_project_folder(self) -> None:
        if self._project is not None:
            self._open_folder(Path(self._project.root))

    def _append_test_log(self, text: str) -> None:
        text = str(text or "").rstrip()
        if text:
            self.test_log.appendPlainText(text[-4000:])

    def start_mcdev(self, kind: str) -> None:
        if self._project is None:
            self.main.status("先在对话页打开一个工程。")
            return
        if self._run_busy:
            self.main.status("开发端还在启动中。")
            return
        self._run_busy = True
        self.test_status.setText(f"正在启动 {kind}…")
        self.main.status(f"正在启动开发端（{kind}），首次会先编译。")
        service = mcdev.shared()
        service.set_log(self.log_line.emit)
        root = Path(self._project.root)

        def work(_progress):
            return service.prepare(root, kind)

        def done(result):
            self._run_busy = False
            self._append_test_log(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
            self.test_status.setText("开发端已就绪" if result.get("ok") else "开发端已启动（未完全验证）")
            self._refresh_test_status()

        def failed(message):
            self._run_busy = False
            self._append_test_log("启动失败：" + message)
            self.test_status.setText("启动失败：" + message)

        self._run(work, done, failed=failed)

    def stop_mcdev(self) -> None:
        self._run_busy = True
        self.test_status.setText("正在停止…")
        service = mcdev.shared()

        def work(_progress):
            return service.stop("all")

        def done(result):
            self._run_busy = False
            self._append_test_log(json.dumps(result, ensure_ascii=False, indent=2))
            self.test_status.setText("已停止")
            self._refresh_test_status()

        self._run(work, done, failed=lambda m: (setattr(self, "_run_busy", False), self.test_status.setText("停止失败：" + m)))

    def _refresh_test_status(self) -> None:
        try:
            payload = mcdev.shared().launch_status()
        except Exception:  # noqa: BLE001
            return
        rows = []
        for item in payload.get("processes") or []:
            state = "运行中" if item.get("running") else "已结束"
            rows.append(f"{item.get('kind')} · {state} · PID {item.get('pid')}")
        if rows:
            self.test_status.setText(" | ".join(rows))

    # ------------------------------------------------------------------
    # 在线环境与智能参考
    def refresh_online_status(self) -> None:
        project_root = Path(self._project.root) if self._project is not None else None

        def work(_progress):
            return resources.status(project_root)

        def done(data):
            java = data.get("java") or {}
            gradle = data.get("gradle") or {}
            docs = data.get("docs") or {}
            self.online_status.setText(
                "在线环境：JDK "
                + (str(java.get("version") or "未找到"))
                + " · Gradle "
                + (str(gradle.get("version") or "未准备"))
                + ("（已安装）" if gradle.get("installed") else "")
                + f" · 源码构件 {docs.get('count', 0)} 个"
            )

        self._run(
            work,
            done,
            failed=lambda message: self.online_status.setText("在线环境检查失败：" + message),
        )

    def ensure_online_resources(self) -> None:
        loader = str(self.docs_loader.currentData() or "")
        mc = self.version_combo.currentText().strip()
        project_root = Path(self._project.root) if self._project is not None else None
        self.online_prepare_btn.setEnabled(False)
        self.online_status.setText("正在在线准备 JDK、版本目录与开发资料…")

        def work(progress):
            return resources.ensure(
                "all",
                project_root=project_root,
                loader=loader,
                mc=mc,
                progress=progress,
            )

        def done(data):
            self.online_prepare_btn.setEnabled(True)
            self.online_status.setText(resources.render(data).replace("\n", " · ")[:600])
            self.main.status("在线资源准备完成")

        def failed(message):
            self.online_prepare_btn.setEnabled(True)
            self.online_status.setText("在线准备失败：" + message)

        self._run(work, done, failed=failed)

    def auto_reference(self) -> None:
        query = self.ref_query.text().strip()
        if not query:
            self.ref_status.setText("先输入要实现的功能描述。")
            return
        loader = str(self.ref_loader.currentData() or "")
        mc = self.ref_mc.currentText().strip()
        try:
            max_mods = int(self.ref_mods.currentText() or "2")
        except ValueError:
            max_mods = 2
        self.ref_btn.setEnabled(False)
        self.ref_progress.setVisible(True)
        self.ref_progress.setValue(0)
        self.ref_status.setText("正在识别功能并在线搜索相关模组…")
        self.ref_view.setPlainText("")

        def work(progress):
            return reference.auto_reference(
                query,
                loader=loader,
                mc=mc,
                max_mods=max_mods,
                max_snippets=10,
                progress=progress,
            )

        def done(result):
            self.ref_btn.setEnabled(True)
            self.ref_progress.setVisible(False)
            prepared = list(result.get("prepared") or [])
            snippets = list(result.get("snippets") or [])
            self.ref_status.setText(
                f"已准备 {len(prepared)} 个参考模组 · 找到 {len(snippets)} 个代码片段"
                + (" · 可从右侧打开源码目录" if prepared else "")
            )
            self.ref_view.setPlainText(reference.render_auto(result, snippet_limit=10))
            if prepared:
                self.main.status("智能参考完成，可以打开参考源码目录查看")

        def failed(message):
            self.ref_btn.setEnabled(True)
            self.ref_progress.setVisible(False)
            self.ref_status.setText("智能参考失败：" + message)

        self._run(
            work,
            done,
            failed=failed,
            progress=lambda value, text: (
                self.ref_progress.setValue(int(value)),
                self.ref_status.setText(text),
            ),
        )

    def open_reference_sources(self) -> None:
        self._open_folder(modlibrary.sources_dir())

    # ------------------------------------------------------------------
    # 模组学习
    def search_mods(self) -> None:
        query = self.learn_query.text().strip()
        loader = str(self.learn_loader.currentData() or "")
        mc = self.learn_mc.currentText().strip()
        self.mod_status.setText("正在搜索…")
        self._hits = []
        self.learn_results.clear()

        def work(_progress):
            return self.bridge.search_mods(query, loader=loader, mc=mc, limit=30)

        def done(data):
            self._hits = list(data.get("hits") or [])
            for hit in self._hits:
                title = hit.get("title") or hit.get("slug") or "未命名"
                downloads = int(hit.get("downloads") or 0)
                desc = " ".join(str(hit.get("description") or "").split())
                item = QListWidgetItem(f"{title}\n{downloads:,} 次下载 · {desc}")
                item.setData(Qt.UserRole, hit)
                self.learn_results.addItem(item)
            self.mod_status.setText(f"找到 {len(self._hits)} 个结果")
            if self._hits:
                self.learn_results.setCurrentRow(0)

        self._run(work, done, failed=lambda m: self.mod_status.setText("搜索失败：" + m))

    def _selected_hit(self) -> dict:
        row = self.learn_results.currentRow()
        if 0 <= row < len(self._hits):
            return self._hits[row]
        return {}

    def _on_mod_selected(self, row: int) -> None:
        if row < 0 or row >= len(self._hits):
            return
        hit = self._hits[row]
        self.mod_title.setText(hit.get("title") or hit.get("slug") or "")
        self.mod_desc.setText(" ".join(str(hit.get("description") or "").split()))
        self.mod_versions.clear()
        project = str(hit.get("project_id") or hit.get("slug") or "")
        loader = str(self.learn_loader.currentData() or "")
        mc = self.learn_mc.currentText().strip()
        self.mod_status.setText("正在读取版本…")

        def work(_progress):
            return self.bridge.mod_versions(project, loader=loader, mc=mc)

        def done(items):
            for version in items or []:
                name = version.get("name") or version.get("version_number") or version.get("id")
                games = ", ".join(version.get("game_versions") or [])
                self.mod_versions.addItem(f"{name}  [{games}]", version)
            self.mod_status.setText(f"{len(items or [])} 个可用版本")

        self._run(work, done, failed=lambda m: self.mod_status.setText("读取版本失败：" + m))

    def _mod_context(self) -> tuple[str, str, str, str]:
        hit = self._selected_hit()
        project = str(hit.get("project_id") or hit.get("slug") or "")
        loader = str(self.learn_loader.currentData() or "")
        mc = self.learn_mc.currentText().strip()
        data = self.mod_versions.currentData()
        version = str((data or {}).get("id") or "") if isinstance(data, dict) else ""
        return project, loader, mc, version

    def download_mod(self) -> None:
        project, loader, mc, version = self._mod_context()
        if not project:
            self.mod_status.setText("先搜索并选择一个模组。")
            return
        self.mod_progress.setVisible(True)
        self.mod_progress.setValue(0)
        self.mod_status.setText("正在下载…")

        def work(progress):
            return self.bridge.download_mod(
                project, loader=loader, mc=mc, version=version, target="learn", progress=progress
            )

        def done(result):
            self.mod_progress.setVisible(False)
            self.mod_status.setText("已下载：" + str(result.get("path") or ""))

        self._run(
            work,
            done,
            failed=lambda m: (self.mod_progress.setVisible(False), self.mod_status.setText("下载失败：" + m)),
            progress=lambda value, text: (self.mod_progress.setValue(value), self.mod_status.setText(text)),
        )

    def decompile_mod(self) -> None:
        project, loader, mc, version = self._mod_context()
        if not project:
            self.mod_status.setText("先搜索并选择一个模组。")
            return
        self.mod_progress.setVisible(True)
        self.mod_progress.setValue(0)
        self.mod_status.setText("正在下载并反编译，可能需要几分钟…")

        def work(progress):
            return self.bridge.decompile_mod(
                project, loader=loader, mc=mc, version=version, progress=progress
            )

        def done(result):
            self.mod_progress.setVisible(False)
            source_dir = result.get("source_dir") or result.get("sources") or ""
            self.mod_status.setText(f"反编译完成：{source_dir}")
            self.main.status("模组反编译完成，可以打开源码目录学习")

        self._run(
            work,
            done,
            failed=lambda m: (self.mod_progress.setVisible(False), self.mod_status.setText("反编译失败：" + m)),
            progress=lambda value, text: (self.mod_progress.setValue(value), self.mod_status.setText(text)),
        )

    def open_mod_sources(self) -> None:
        source = ""
        for item in self.bridge.learned():
            title = str(item.get("title") or "")
            if title and title == self.mod_title.text():
                source = str(item.get("source_dir") or "")
                break
        library_sources = modlibrary.sources_dir()
        target = Path(source) if source else library_sources
        self._open_folder(target)