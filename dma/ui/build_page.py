from __future__ import annotations

import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import deps, envcheck, gradle, mirrors, project as project_mod, toolchain, wrapper
from . import theme
from .prep_worker import PrepareWorker
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


class _MirrorProbe(QObject):
    done = Signal(bool)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            reachable = mirrors.neoforged_reachable()
        except Exception:  # noqa: BLE001 - 探测失败按不可达处理
            reachable = False
        self.done.emit(reachable)


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
        self._cert_error = False
        self._last_run: tuple[str, str] | None = None
        self._output: list[str] = []
        self._preparing = False
        self._prepare_worker: PrepareWorker | None = None
        self._prepared: toolchain.Prepared | None = None
        self._prepared_root = ""
        self._prepared_at = 0.0
        self._pending_after_prepare: tuple[str, str] | None = None
        self._auto_upgraded = False
        self._network_retries = 0
        self._jar_retried = False
        self._mirror_active = False
        self._mirror_attempted = False
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
        self._mirror_probe = _MirrorProbe()
        self._mirror_probe.done.connect(self._on_mirror_probe)
        self._mirror_probe.start()

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
                "点任务按钮会先自动准备环境：挑一个合适的 JDK、补齐 Wrapper、按插件要求选 Gradle 版本，"
                "把 build.gradle 引用的 libs/*.jar 自动找出来放进工程，然后直接执行。"
                "编译产物在 build/libs，游戏目录是工程下的 run/。"
                "首次编译仍要下载 Minecraft 依赖，时间取决于网络。关闭工坊会结束正在运行的任务。"
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
            ("runDatagen", "数据生成", "", "数据生成：NeoForge 用 runData，Fabric 用 runDatagen，按工程自动选"),
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
            "选择工程后点「编译」即可，缺什么会自动补什么。"
            "也可以先点「一键准备」只装环境、不跑任务。"
        )
        bar.addWidget(self.detail)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.prepare_btn = QPushButton("一键准备")
        self.prepare_btn.setObjectName("Primary")
        self.prepare_btn.setToolTip(
            "自动检测并配置构建环境：优先选 Java 21，缺 JDK 就下载 Temurin 21；"
            "缺 Wrapper 就写入，ModDevGradle / NeoGradle 工程自动切到兼容的 Gradle 8.x；"
            "build.gradle 里引用的 libs/*.jar 会从下载目录、桌面和知识库来源目录自动找出来复制进工程；"
            "再从中科大 / 华为云镜像装好 Gradle 发行包。已就绪的步骤会跳过。"
        )
        self.prepare_btn.clicked.connect(self._prepare_clicked)
        self.assist_btn = QPushButton("让助手补全配置")
        self.assist_btn.setToolTip("切到对话页，让助手按当前 MC 版本和加载器生成 build.gradle / settings.gradle / gradle.properties")
        self.assist_btn.clicked.connect(self._assist_config)
        self.wrapper_btn = QPushButton("生成 Wrapper")
        self.wrapper_btn.setToolTip(
            "把 gradlew、gradlew.bat 和 gradle-wrapper.jar 写入工程，并按构建脚本选择兼容的 Gradle 版本，"
            "不需要本机安装 Gradle"
        )
        self.wrapper_btn.clicked.connect(self._make_wrapper)
        actions.addWidget(self.prepare_btn)
        actions.addWidget(self.assist_btn)
        actions.addWidget(self.wrapper_btn)
        self.cert_box = QCheckBox("使用 Windows 证书库")
        self.cert_box.setToolTip(
            "浏览器能打开、Gradle 却报 PKIX path building failed 时勾选。"
            "加速器、公司代理和部分杀毒软件会用 Windows 里的根证书拦截 HTTPS，"
            "JDK 自带的证书库不认它们；勾选后 Gradle 改用 Windows 证书库。"
        )
        self.cert_box.setChecked(
            bool(getattr(getattr(self.main, "config", None), "gradle_windows_certs", False))
        )
        self.cert_box.toggled.connect(self._on_cert_toggle)
        actions.addWidget(self.cert_box)
        actions.addStretch(1)
        bar.addLayout(actions)

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
        self._prepared = None
        self._prepared_root = ""
        self._prepared_at = 0.0
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        has_project = getattr(self.main, "active_project", None) is not None
        running = self.runner.running
        busy = running or self._preparing
        for button in self._buttons:
            button.setEnabled(has_project and not busy)
        self.stop_btn.setEnabled(running and not self._preparing)
        self.open_project_btn.setEnabled(has_project)
        self.prepare_btn.setEnabled(has_project and not busy)
        self.assist_btn.setEnabled(has_project and not busy)
        self.wrapper_btn.setEnabled(has_project and not busy)

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

    # -- 镜像 -----------------------------------------------------------
    def _on_mirror_probe(self, reachable: bool) -> None:
        if reachable:
            return
        self._enable_mirror("启动检测到 maven.neoforged.net 直连不通")

    def _enable_mirror(self, reason: str) -> None:
        if self._mirror_active:
            return
        try:
            script = mirrors.service(self._signals.line.emit).init_script()
        except Exception as exc:  # noqa: BLE001 - 代理起不来就退回直连
            self._append(f"[镜像] 本地代理启动失败：{exc}")
            return
        self._mirror_active = True
        self._append(f"=== 已切换到镜像模式：{reason} ===")
        self._append(
            "[镜像] NeoForged 的仓库会改走本机代理，代理从国内镜像取包并缓存；"
            "ModDevGradle、NeoForge、NeoFormRuntime 都走这条路。"
        )
        self._append(f"[镜像] init script：{script}")
        self._append(f"[镜像] 代理地址：{mirrors.service().base_url}")

    def _mirror_script(self):
        if not self._mirror_active:
            return None
        try:
            return mirrors.service(self._signals.line.emit).init_script()
        except Exception as exc:  # noqa: BLE001
            self._append(f"[镜像] 初始化失败，改为直连：{exc}")
            self._mirror_active = False
            return None

    # ------------------------------------------------------------------
    def _run(self, task: str, label: str, retry: bool = False) -> None:
        if self.runner.running or self._preparing:
            self._status("已有任务在进行，先等它结束或点「停止」。", theme.COLORS["warn"])
            return
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。先在对话页新建工程并写入文件。", theme.COLORS["warn"])
            return
        root = Path(project.root)
        if not gradle.has_gradle_files(root):
            self._status(
                "工程里还没有 Gradle 配置，正在切到对话页让助手补全；生成后点「编译」即可。",
                theme.COLORS["warn"],
            )
            self._assist_config()
            return
        if retry:
            self._launch(task, label, retry=True)
            return
        self._repair_fences(root)
        self._network_retries = 0
        self._jar_retried = False
        fresh = (
            self._prepared is not None
            and self._prepared_root == str(root)
            and time.monotonic() - self._prepared_at < 120
        )
        if fresh:
            self._launch(task, label)
            return
        self._start_prepare((task, label), reset_upgrade=True)

    def _repair_fences(self, root: Path) -> None:
        """编译前清掉 Markdown 围栏，并补齐 NeoForge 开发运行缺的 mods 声明。"""
        try:
            fixed = project_mod.repair_fences(root)
        except Exception as exc:  # noqa: BLE001 - 修复失败不阻塞编译
            self._append(f"[预检] 代码围栏检查失败：{exc}")
            fixed = []
        if fixed:
            self._append("=== 编译前预检：已清理源文件里的 Markdown 代码围栏 ===")
            for item in fixed:
                self._append(f"[预检] 修正 {item}")
        try:
            scripts = project_mod.repair_build_scripts(root)
        except Exception as exc:  # noqa: BLE001 - 修复失败不阻塞编译
            self._append(f"[预检] 构建脚本检查失败：{exc}")
            scripts = []
        for item in scripts:
            self._append(f"[预检] 已修正构建脚本 {item}（补齐开发运行配置）")
        if fixed or scripts:
            self.main.status("已修正构建脚本，开始编译")

    def _known_jar_dirs(self) -> list[Path]:
        """知识库里导入过的 JAR 所在目录，自动找 libs/ 依赖时优先看这些地方。"""
        store = getattr(self.main, "knowledge", None)
        if store is None:
            return []
        try:
            documents = store.list_documents(limit=500)
        except Exception:  # noqa: BLE001 - 读不到知识库不影响构建
            return []
        dirs: list[Path] = []
        seen: set[str] = set()
        for item in documents:
            source = str(item.get("source") or "").strip()
            if not source:
                continue
            path = Path(source)
            try:
                if not path.is_file():
                    continue
                folder = path.parent
                key = str(folder.resolve()).lower()
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            dirs.append(folder)
        return dirs

    def _prepare_clicked(self) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。先在对话页新建工程。", theme.COLORS["warn"])
            return
        self._start_prepare(None, reset_upgrade=True)

    def _start_prepare(
        self, then: tuple[str, str] | None, *, reset_upgrade: bool = False
    ) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。", theme.COLORS["warn"])
            return
        if self.runner.running or self._preparing:
            return
        if reset_upgrade:
            self._auto_upgraded = False
        self._pending_after_prepare = then
        self._preparing = True
        self._prepared = None
        self._append(f"=== 一键准备环境 {time.strftime('%H:%M:%S')} ===")
        self._status("正在检查并准备 JDK、Gradle…", theme.COLORS["accent"])
        self._refresh_buttons()
        worker = PrepareWorker(Path(project.root), extra_jar_dirs=self._known_jar_dirs())
        worker.signals.line.connect(self._append)
        worker.signals.done.connect(self._on_prepare_done)
        worker.signals.failed.connect(self._on_prepare_failed)
        self._prepare_worker = worker
        worker.start()

    def _on_prepare_done(self, prepared) -> None:
        self._preparing = False
        self._prepare_worker = None
        self._prepared = prepared
        project = getattr(self.main, "active_project", None)
        self._prepared_root = str(Path(project.root)) if project is not None else ""
        self._prepared_at = time.monotonic()
        if prepared.java_home:
            self._append(
                f"[准备] 构建用 Java：{prepared.java_version} · {prepared.java_home}"
            )
        if prepared.gradle_home:
            self._append(
                f"[准备] Gradle：{prepared.gradle_version} · {prepared.gradle_home}"
            )
        for note in prepared.notes:
            self._append(f"[准备] 注意：{note}")
        self._append("=== 环境准备完成 ===")
        self._status("环境已就绪", theme.COLORS["ok"])
        self._refresh_buttons()
        then = self._pending_after_prepare
        self._pending_after_prepare = None
        if then:
            QTimer.singleShot(60, lambda: self._launch(then[0], then[1]))
            return
        self.detail.setText("JDK、Wrapper 和 Gradle 已就绪，点「编译」开始。")
        self.main.status("一键准备完成")

    def _on_prepare_failed(self, message: str) -> None:
        self._preparing = False
        self._prepare_worker = None
        self._pending_after_prepare = None
        self._append(f"[准备失败] {message}")
        self._status("环境准备失败", theme.COLORS["danger"])
        self.detail.setText("检查网络后重试；也可以到「环境自检」页复制详细报告。")
        self._refresh_buttons()
        self.main.status("环境准备失败")

    def _launch(self, task: str, label: str, retry: bool = False) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。", theme.COLORS["warn"])
            return
        root = Path(project.root)
        if not gradle.has_gradle_files(root):
            self._status("工程里还没有 Gradle 配置文件。", theme.COLORS["warn"])
            return
        if self._prepared is not None and not self._prepared.java_home:
            self._status(
                "没有找到可用的 JDK，Gradle 无法启动。点「一键准备」重新下载 JDK 21。",
                theme.COLORS["danger"],
            )
            return
        try:
            launch = gradle.resolve_launch(root, task, init_script=self._mirror_script())
        except gradle.GradleError as exc:
            self._status(str(exc), theme.COLORS["danger"])
            return
        source = "工程 Wrapper" if launch.source == "wrapper" else "系统 Gradle"
        self._output = []
        self._append(f"=== {label}（{source}）{time.strftime('%H:%M:%S')} ===")
        self._append(launch.command or " ".join(launch.argv))
        self._task = label
        self._stopping = False
        if not retry:
            self._cert_error = False
        self._last_run = (task, label)
        self._started_at = time.monotonic()
        self._status(f"正在{label}…", theme.COLORS["accent"])
        self._timer.start()
        self._refresh_buttons()
        self.main.status(f"正在{label}：{project.label()}")
        env = gradle.build_env(self.cert_box.isChecked(), slow=self._mirror_active)
        if self._prepared is not None and self._prepared.java_home:
            env = toolchain.with_java_home(env, self._prepared.java_home)
        try:
            self.runner.start(launch, env)
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
            self._network_retries = 0
            self._status(f"{self._task}完成", theme.COLORS["ok"])
            if self._task == "编译":
                self.detail.setText("编译成功，jar 在 build/libs 目录。点「复制日志」可以把结果贴回对话页。")
            else:
                self.detail.setText("任务正常结束。需要继续修改就让助手改文件，再重新运行。")
            self._append(f"=== {self._task}完成（退出码 0）===")
            self.main.status(f"{self._task}完成")
        else:
            log_text = "\n".join(self._output)
            if (
                not self._mirror_active
                and not self._mirror_attempted
                and self._last_run is not None
                and mirrors.needs_mirror(log_text)
            ):
                task, label = self._last_run
                self._mirror_attempted = True
                self._append("=== 直连 NeoForged 仓库失败 ===")
                self._append(
                    "报错指向 maven.neoforged.net，常见原因是 DNS 污染、加速器劫持或对方临时故障"
                    "（502 / 连接被拒 / 证书链异常）。文件本身没问题，换个入口取包就能继续。"
                )
                self._enable_mirror("依赖下载命中 maven.neoforged.net 但连不通")
                if self._mirror_active:
                    self._network_retries = 0
                    self._cert_error = False
                    self._task = ""
                    self._status("已切换镜像，正在重试…", theme.COLORS["warn"])
                    QTimer.singleShot(400, lambda: self._run(task, label, retry=True))
                    return
            if self._cert_error and self._last_run and not self.cert_box.isChecked():
                task, label = self._last_run
                self._append("=== 证书链校验失败 ===")
                self._append(
                    "原因：本机有程序在拦截 HTTPS（常见于 Watt Toolkit / SteamTools 的 GitHub 加速、"
                    "公司代理或杀毒软件），JDK 自带证书库不认它的根证书。"
                )
                self._append("已自动勾选「使用 Windows 证书库」，正在重试…")
                self._status("证书链校验失败，改用 Windows 证书库重试", theme.COLORS["warn"])
                self._task = ""
                self._cert_error = False
                self.cert_box.setChecked(True)
                QTimer.singleShot(400, lambda: self._run(task, label, retry=True))
                return
            self._status(f"{self._task}失败（退出码 {code}）", theme.COLORS["danger"])
            if (
                deps.is_missing_jar_error(log_text)
                and not self._jar_retried
                and self._last_run is not None
            ):
                project = getattr(self.main, "active_project", None)
                if project is not None:
                    task, label = self._last_run
                    jars = deps.ensure_local_jars(
                        Path(project.root),
                        log=self._append,
                        extra_dirs=self._known_jar_dirs(),
                    )
                    if jars.changed:
                        self._jar_retried = True
                        self._append("=== 缺少本地依赖，已自动补进 libs/ ===")
                        self._status("已自动补上本地 jar，正在重试…", theme.COLORS["warn"])
                        self._task = ""
                        QTimer.singleShot(400, lambda: self._launch(task, label, retry=True))
                        return
                    if jars.missing:
                        self.detail.setText(jars.summary())
            required = gradle.required_gradle(log_text)
            if required:
                project = getattr(self.main, "active_project", None)
                current = wrapper.current_distribution(Path(project.root)) if project else ""
                needs_upgrade = (
                    gradle.version_tuple(current) < gradle.version_tuple(required)
                    and gradle.version_tuple(required)
                    <= gradle.version_tuple(wrapper.DEFAULT_DISTRIBUTION)
                )
                if needs_upgrade and not self._auto_upgraded and project is not None:
                    task, label = self._last_run or ("build", "编译")
                    try:
                        written = wrapper.ensure_wrapper(
                            Path(project.root), wrapper.DEFAULT_DISTRIBUTION
                        )
                    except Exception as exc:  # noqa: BLE001 - 失败时退回提示
                        self._append(f"[自动升级 Wrapper 失败] {exc}")
                    else:
                        self._auto_upgraded = True
                        self._append(
                            f"=== 自动升级 Wrapper：{current or '缺失'} → "
                            f"{wrapper.DEFAULT_DISTRIBUTION} ==="
                        )
                        for item in written:
                            self._append(f"写入 {item}")
                        self._status("已自动升级 Wrapper，正在重新准备并重试…", theme.COLORS["warn"])
                        self._task = ""
                        self._start_prepare((task, label), reset_upgrade=False)
                        return
                if (
                    current
                    and gradle.version_tuple(current) >= gradle.version_tuple(required)
                ):
                    self.detail.setText(
                        f"Gradle 版本已经满足要求（需要 {required}，当前 {current}），"
                        "这次失败更可能是依赖下载、插件或代码问题。把上面的日志复制回对话页排查。"
                    )
                elif gradle.version_tuple(required) <= gradle.version_tuple(
                    wrapper.DEFAULT_DISTRIBUTION
                ):
                    self.detail.setText(
                        f"构建脚本要求 Gradle {required}，工程当前是 {current or '未知版本'}。"
                        "重新点「编译」，程序会自动把 Wrapper 调到兼容版本再试。"
                    )
                else:
                    self.detail.setText(
                        f"构建脚本要求 Gradle {required}，高于内置的 {wrapper.DEFAULT_DISTRIBUTION}。"
                        "让助手把插件降到兼容版本，或手动把 Wrapper 升到该版本。"
                    )
                self._append(
                    f"=== 版本不匹配：需要 Gradle {required}，当前 {current or '未知'} ==="
                )
            elif self._cert_error:
                self.detail.setText(
                    "证书链校验仍然失败：先在加速器里关掉 GitHub 加速，或把它的根证书导入 JDK 的 cacerts。"
                    "急着编译可以在有网的环境手动下载 Gradle 发行包，解开后把 bin 目录临时加入 PATH。"
                )
            elif (
                gradle.is_network_error("\n".join(self._output))
                and self._network_retries < 2
                and self._last_run is not None
            ):
                self._network_retries += 1
                task, label = self._last_run
                self._append(
                    f"=== 依赖下载中断，自动重试（{self._network_retries}/2）==="
                )
                self._append(
                    "已经下完的依赖会保留，重试通常能继续往前走。"
                    "如果连续中断，换网络或给 Gradle 配代理会更稳。"
                )
                self._status("依赖下载中断，正在自动重试…", theme.COLORS["warn"])
                self._task = ""
                QTimer.singleShot(1500, lambda: self._launch(task, label))
                return
            else:
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
        if gradle.is_cert_error(line):
            self._cert_error = True
        self._output.append(line)
        self.log.appendPlainText(line)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.copy_btn.setEnabled(bool(self.log.toPlainText().strip()))

    def _on_cert_toggle(self, checked: bool) -> None:
        config = getattr(self.main, "config", None)
        if config is None:
            return
        if bool(getattr(config, "gradle_windows_certs", False)) == bool(checked):
            return
        config.gradle_windows_certs = bool(checked)
        self.main.save_config()

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

    # ------------------------------------------------------------------
    def _assist_config(self) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。先在对话页新建工程并写入文件。", theme.COLORS["warn"])
            return
        root = Path(project.root)
        if gradle.has_gradle_files(root):
            self._status(
                "工程里已经有 Gradle 配置文件；要改配置请回对话页描述需求。",
                theme.COLORS["warn"],
            )
            return
        chat = getattr(self.main, "chat_page", None)
        if chat is None:
            return
        self._switch_to_chat()
        prompt = scaffold_request(project)
        if not chat.ask(prompt):
            self._status("已切到对话页；确认工程和模型通道后点「发送」。", theme.COLORS["warn"])

    def _switch_to_chat(self) -> None:
        chat = getattr(self.main, "chat_page", None)
        stack = getattr(self.main, "stack", None)
        if chat is None or stack is None:
            return
        for index in range(stack.count()):
            if stack.widget(index) is chat:
                stack.setCurrentIndex(index)
                button = self.main.group.button(index) if hasattr(self.main, "group") else None
                if button is not None:
                    button.setChecked(True)
                return

    def _make_wrapper(self) -> None:
        project = getattr(self.main, "active_project", None)
        if project is None:
            self._status("还没有选择工程。", theme.COLORS["warn"])
            return
        root = Path(project.root)
        if wrapper.has_wrapper(root):
            target = wrapper.compatible_version(root)[0] or wrapper.DEFAULT_DISTRIBUTION
            answer = QMessageBox.question(
                self,
                "覆盖 Wrapper",
                f"工程里已经有 gradlew。要按构建脚本覆盖为 Gradle {target} 的 Wrapper 吗？",
            )
            if answer != QMessageBox.Yes:
                return
        try:
            written = wrapper.ensure_wrapper(root)
        except (wrapper.WrapperError, OSError) as exc:
            self._status(f"生成 Wrapper 失败：{exc}", theme.COLORS["danger"])
            return
        self._append("=== 生成 Gradle Wrapper ===")
        for item in written:
            self._append(f"写入 {item}")
        self._status("Wrapper 已生成", theme.COLORS["ok"])
        if gradle.has_gradle_files(root):
            current = wrapper.current_distribution(root) or wrapper.DEFAULT_DISTRIBUTION
            self.detail.setText(
                f"已写入 gradlew 和 gradle-wrapper.properties（Gradle {current}）。点「编译」会自动准备。"
            )
        else:
            self.detail.setText("Wrapper 已就绪；还缺 build.gradle，点「让助手补全配置」后再编译。")
        self.main.status("Gradle Wrapper 已生成")

    def shutdown(self) -> None:
        if self.runner.running:
            self.runner.stop()
        try:
            mirrors.shutdown()
        except Exception:  # noqa: BLE001
            pass


def scaffold_request(project) -> str:
    meta = project.meta
    loader = meta.loader or "Fabric"
    name = meta.name or meta.mod_id
    lines = [
        f"当前工程缺少 Gradle 配置文件（{name}，Minecraft {meta.mc_version}，{loader}，Mod ID `{meta.mod_id}`）。",
        "只补齐构建配置，不要修改已有源码，也不要编造物品、方块或业务功能。需要写入：",
        "1. settings.gradle（rootProject.name 用 Mod ID）",
        "2. build.gradle",
        "3. gradle.properties",
    ]
    if loader == "NeoForge":
        lines.append("NeoForge 工程使用 NeoGradle 或 ModDevGradle 插件，版本必须与 MC 版本匹配。")
    elif loader == "Architectury":
        lines.append("Architectury 工程保留 common / fabric / forge 多模块结构，插件版本三端一致。")
    else:
        lines.append("Fabric 工程使用 fabric-loom，Java 版本与 MC 版本对应（1.20.5+ 用 Java 21）。")
    lines.extend(
        [
            "所有版本号先查 Fabric Meta / NeoForged 官方仓库确认，不要猜；查不到就写明需要用户确认，不要编造。",
            "「生成 Wrapper」和「一键准备」会按插件自动选 Gradle 版本："
            f"ModDevGradle / NeoGradle 用 {wrapper.MODDEV_DISTRIBUTION}，其它默认 "
            f"{wrapper.DEFAULT_DISTRIBUTION}；如果要用的插件需要更高的 Gradle，在回复里明确说明。",
            "gradlew 和 gradle-wrapper.jar 不用生成；页面上有「生成 Wrapper」按钮，"
            "环境依赖点「一键准备」会自动下载并配置。",
            "按工程相对路径输出完整文件内容，文件进入待写入列表，等用户确认后再写入。",
        ]
    )
    return "\n".join(lines)
