"""编译运行页：编译、GameTest，以及启动真实的 Forge 开发端。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import mcdev, project as project_mod
from .widgets import card, tiny
from .workbench_panel import ArtifactPanel, RunPanel


class _Signals(QObject):
    line = Signal(str)
    done = Signal(str, bool)
    refresh = Signal()


class BuildPage(QWidget):
    """宿主侧的统一入口：Gradle 与游戏进程都由 mcdev 管理。"""

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.project = None
        self._signals = _Signals()
        self._signals.line.connect(self._append)
        self._signals.done.connect(self._on_done)
        self._signals.refresh.connect(self._refresh_status)
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._run_busy = False
        self._task = ""
        self._build_page()
        if hasattr(main, "project_changed"):
            main.project_changed.connect(self.sync_project)
        self.sync_project(getattr(main, "active_project", None))
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()

    # ------------------------------------------------------------------
    def _build_page(self) -> None:
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
                "支持 Fabric / Forge / NeoForge / Architectury。"
                "编译直接执行 gradlew build；运行会启动真实的开发服务端 / 客户端。"
                "Forge 1.20.1 会安装游戏内探针做状态、界面和场景断言；"
                "其他加载器/版本以运行日志和 GameTest 为准。首次启动要下载依赖，时间取决于网络。"
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
        self.pick_project_btn = QPushButton("选择工程")
        self.pick_project_btn.setObjectName("Ghost")
        self.pick_project_btn.clicked.connect(self._pick_project)
        project_row.addWidget(self.pick_project_btn)
        bar.addLayout(project_row)

        task_row = QHBoxLayout()
        task_row.setSpacing(8)
        self.build_btn = QPushButton("编译")
        self.build_btn.setObjectName("Primary")
        self.build_btn.clicked.connect(lambda: self._run_gradle("build"))
        self.gametest_btn = QPushButton("GameTest")
        self.gametest_btn.setObjectName("Ghost")
        self.gametest_btn.clicked.connect(self._run_gametest)
        self.libs_btn = QPushButton("打开 build/libs")
        self.libs_btn.setObjectName("Ghost")
        self.libs_btn.clicked.connect(self._open_libs)
        task_row.addWidget(self.build_btn)
        task_row.addWidget(self.gametest_btn)
        task_row.addWidget(self.libs_btn)
        task_row.addStretch(1)
        bar.addLayout(task_row)

        self.status = QLabel("空闲")
        self.status.setObjectName("H2")
        bar.addWidget(self.status)
        outer.addWidget(toolbar)

        body = QHBoxLayout()
        body.setSpacing(12)
        run_card = card()
        run_layout = QVBoxLayout(run_card)
        run_layout.setContentsMargins(14, 12, 14, 12)
        self.run_panel = RunPanel()
        self.run_panel.prepare_requested.connect(self._start_mcdev)
        self.run_panel.stop_requested.connect(self._stop_mcdev)
        self.run_panel.refresh_requested.connect(self._refresh_status)
        run_layout.addWidget(self.run_panel)
        body.addWidget(run_card, 1)

        artifact_card = card()
        artifact_layout = QVBoxLayout(artifact_card)
        artifact_layout.setContentsMargins(14, 12, 14, 12)
        self.artifact_panel = ArtifactPanel()
        self.artifact_panel.open_requested.connect(self._open_libs)
        artifact_layout.addWidget(self.artifact_panel)
        body.addWidget(artifact_card, 1)
        outer.addLayout(body, 1)

        log_card = card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(14, 12, 14, 12)
        log_layout.setSpacing(6)
        log_head = QHBoxLayout()
        label = QLabel("任务日志")
        label.setObjectName("H2")
        log_head.addWidget(label)
        log_head.addStretch(1)
        clear = QPushButton("清空")
        clear.setObjectName("Ghost")
        clear.clicked.connect(lambda: self.log.clear())
        stop = QPushButton("停止编译")
        stop.setObjectName("Danger")
        stop.clicked.connect(self._cancel_build)
        log_head.addWidget(clear)
        log_head.addWidget(stop)
        log_layout.addLayout(log_head)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Gradle 输出和开发端日志会显示在这里。")
        self.log.setFixedHeight(180)
        log_layout.addWidget(self.log)
        outer.addWidget(log_card)

    # ------------------------------------------------------------------
    def sync_project(self, project) -> None:
        self.project = project
        enabled = project is not None
        for button in (self.build_btn, self.gametest_btn, self.libs_btn):
            button.setEnabled(enabled)
        if project is None:
            self.project_label.setText("未选择工程")
            self.artifact_panel.update_view([], "", "")
            self.run_panel.set_busy(False, "未启动")
            self.run_panel.update_status({"processes": []})
            if hasattr(self.run_panel, "set_probe_support"):
                self.run_panel.set_probe_support(True)
            return
        meta = project.meta
        self.project_label.setText(
            f"{meta.name or meta.mod_id or project.root.name}"
            f"  ·  {meta.loader or 'Forge'} {meta.mc_version or '1.20.1'}"
            f"  ·  Mod ID {meta.mod_id or '未确认'}"
        )
        probe_ready = mcdev.templates.supports_probe(project.root)
        if hasattr(self.run_panel, "set_probe_support"):
            self.run_panel.set_probe_support(probe_ready)
        self._load_artifacts()
        self._refresh_status()

    def _load_artifacts(self) -> None:
        jars: list[dict] = []
        if self.project is not None:
            libs = self.project.root / "build" / "libs"
            if libs.is_dir():
                try:
                    files = sorted(item for item in libs.glob("*.jar") if item.is_file())
                except OSError:
                    files = []
                for item in files:
                    try:
                        size = item.stat().st_size
                    except OSError:
                        size = 0
                    jars.append({"name": item.name, "size": size, "path": str(item)})
        summary = f"build/libs 里 {len(jars)} 个 JAR" if jars else ""
        self.artifact_panel.update_view(jars, summary, jars[0]["path"] if jars else "")

    # ------------------------------------------------------------------
    def _run_gametest(self) -> None:
        loader = getattr(getattr(self.project, "meta", None), "loader", "")
        self._run_gradle(mcdev.gradle.gametest_task(loader))

    def _run_gradle(self, task: str) -> None:
        if self.project is None:
            QMessageBox.information(self, "还没有工程", "请先新建或打开一个工程。")
            return
        if self._thread is not None and self._thread.is_alive():
            self.main.status("已有任务在运行，先等它结束。")
            return
        root = self.project.root
        is_gametest = task in {"runGameTestServer", "runGametest"}
        label = "GameTest" if is_gametest else "编译"
        self._task = task
        self._cancel.clear()
        self._set_busy(True, f"正在{label}…")
        self.main.status(f"正在{label}：{root}")

        def work() -> None:
            code = -1
            text = ""
            try:
                code, text = mcdev.gradle.run_gradle(
                    root,
                    task,
                    timeout=1800,
                    cancel=self._cancel,
                    log=self._signals.line.emit,
                )
                if is_gametest:
                    summary = mcdev.gradle.gametest_summary(code, text)
                else:
                    summary = "BUILD SUCCESSFUL" if code == 0 else "BUILD FAILED"
                self._signals.done.emit(summary, code == 0)
            except Exception as exc:  # noqa: BLE001 - 失败原因要显示给用户
                self._signals.done.emit(f"任务失败：{exc}", False)

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _cancel_build(self) -> None:
        self._cancel.set()
        self.main.status("已请求停止当前 Gradle 任务。")

    def _on_done(self, summary: str, ok: bool) -> None:
        self._set_busy(False, summary)
        self.main.status(summary)
        self._append(("=== " + summary + " ==="))
        if ok:
            self._load_artifacts()

    def _set_busy(self, busy: bool, text: str) -> None:
        for button in (self.build_btn, self.gametest_btn):
            button.setEnabled(not busy and self.project is not None)
        self.status.setText(text or "空闲")

    # ------------------------------------------------------------------
    def _start_mcdev(self, kind: str) -> None:
        if self.project is None:
            QMessageBox.information(self, "还没有工程", "请先新建或打开一个工程。")
            return
        if self._run_busy:
            self.main.status("开发端还在启动中，先等它就绪。")
            return
        service = mcdev.shared()
        service.set_log(self._signals.line.emit)
        root = self.project.root
        self._run_busy = True
        self.run_panel.set_busy(True, f"正在启动 {kind}…")
        self.main.status(f"正在启动开发端（{kind}），首次运行会先编译并安装探针…")

        def work() -> None:
            try:
                result = service.prepare(root, kind)
                self._signals.line.emit(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
                self._signals.line.emit("=== 开发端已就绪 ===")
            except Exception as exc:  # noqa: BLE001 - 启动失败要显示原因
                self._signals.line.emit(f"启动失败：{exc}")
            finally:
                self._run_busy = False
                self._signals.refresh.emit()

        threading.Thread(target=work, daemon=True).start()

    def _stop_mcdev(self, side: str) -> None:
        service = mcdev.shared()
        service.set_log(self._signals.line.emit)
        self._run_busy = True
        self.run_panel.set_busy(True, "正在停止…")

        def work() -> None:
            try:
                result = service.stop(side)
                self._signals.line.emit(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001 - 停止失败要说明原因
                self._signals.line.emit(f"停止失败：{exc}")
            finally:
                self._run_busy = False
                self._signals.refresh.emit()

        threading.Thread(target=work, daemon=True).start()

    def _refresh_status(self) -> None:
        try:
            payload = mcdev.shared().launch_status()
        except Exception:
            payload = {"processes": []}
        self.run_panel.update_status(payload)
        if not self._run_busy:
            self.run_panel.set_busy(False, "")

    def is_busy(self) -> bool:
        """Gradle 任务或开发端进程是否还占用着当前工程。

        ``_thread`` 结束后仍保留线程对象，所以不能用是否为 None 判断，
        必须看 ``is_alive()``。
        """
        thread = self._thread
        return bool(self._run_busy or (thread is not None and thread.is_alive()))

    # ------------------------------------------------------------------
    def _append(self, line: str) -> None:
        text = str(line).rstrip()
        if text:
            self.log.appendPlainText(text[-4000:])

    def _pick_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "打开模组工程", str(getattr(self.project, "root", "") or Path.home())
        )
        if not folder:
            return
        try:
            project = project_mod.load_project(Path(folder))
        except Exception as exc:  # noqa: BLE001 - 打不开要提示
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        self.sync_project(project)
        self.main.chat_page.open_project_path(str(project.root))

    def _open_project(self) -> None:
        if self.project is None:
            return
        self.main.open_folder(self.project.root)

    def _open_libs(self) -> None:
        if self.project is None:
            return
        target = self.project.root / "build" / "libs"
        self.main.open_folder(target if target.is_dir() else self.project.root)

    def shutdown(self) -> None:
        self._cancel.set()
