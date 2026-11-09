"""快速生成页：一句话构思直接产出可编译的完整模组工程。"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import paths, studio_bridge
from .widgets import TextPreviewDialog, card, tiny


class _Signals(QObject):
    line = Signal(str)
    done = Signal(object)
    failed = Signal(str)


class StudioPage(QWidget):
    """规格驱动生成器：任意主题 → mod-spec.json → 工程 → 审计/构建/验证/打包。"""

    project_ready = Signal(str)

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._signals = _Signals()
        self._signals.line.connect(self._append)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self._thread: threading.Thread | None = None
        self._busy = False
        self._last_root: Path | None = None
        self._task = ""
        self._build_page()
        self._set_busy(False)

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
        title = QLabel("一键生成任意模组")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "不是模板填空：先按构思生成 mod-spec.json（主题、内容图、进度、数值、资源、验收标准），"
                "再由规格直接落地注册代码、纹理、模型、语言文件、配方、战利品、世界生成与社交系统。"
                "Forge / NeoForge / Fabric 全版本走同一套规格；不支持的组合会明确提示，不假装可用。"
            )
        )
        head.addLayout(box, 1)
        self.open_dir_btn = QPushButton("打开工程目录")
        self.open_dir_btn.setObjectName("Ghost")
        self.open_dir_btn.clicked.connect(self._open_project_dir)
        head.addWidget(self.open_dir_btn)
        outer.addWidget(header)

        form_card = card()
        form = QVBoxLayout(form_card)
        form.setContentsMargins(16, 12, 16, 12)
        form.setSpacing(10)

        self.idea = QPlainTextEdit()
        self.idea.setPlaceholderText(
            "例如：一个以深海遗迹为核心的模组，加入会发光的珊瑚矿、潜水装备、"
            "水下祭坛、可驯服的海龙，以及围绕潮汐能的机器。"
        )
        self.idea.setFixedHeight(92)
        form.addWidget(QLabel("模组构思"))
        form.addWidget(self.idea)

        grid = QFormLayout()
        grid.setSpacing(9)
        self.name = QLineEdit()
        self.name.setPlaceholderText("例如：深渊回响（留空自动命名）")
        self.modid = QLineEdit()
        self.modid.setPlaceholderText("例如：abyss_echo（留空自动生成）")
        self.loader = QComboBox()
        self.loader.addItems(list(studio_bridge.APP_LOADERS))
        self.loader.setCurrentText("Forge")
        self.mc = QComboBox()
        self.mc.setEditable(True)
        self.mc.addItems(studio_bridge.MC_VERSIONS)
        self.mc.setCurrentText("1.20.1")
        self.scope = QComboBox()
        for key, label in studio_bridge.SCOPES:
            self.scope.addItem(label, key)
        self.scope.setCurrentIndex(1)
        self.author = QLineEdit()
        self.author.setPlaceholderText("作者名，留空用 Darkitten Mod Agent")

        self.dest = QLineEdit(str(paths.default_project_root()))
        pick = QPushButton("选择…")
        pick.setObjectName("Ghost")
        pick.clicked.connect(self._pick_dest)
        dest_row = QWidget()
        dest_layout = QHBoxLayout(dest_row)
        dest_layout.setContentsMargins(0, 0, 0, 0)
        dest_layout.setSpacing(6)
        dest_layout.addWidget(self.dest, 1)
        dest_layout.addWidget(pick)

        grid.addRow("模组名称", self.name)
        grid.addRow("Mod ID", self.modid)
        grid.addRow("加载器", self.loader)
        grid.addRow("MC 版本", self.mc)
        grid.addRow("规模", self.scope)
        grid.addRow("作者", self.author)
        grid.addRow("输出目录", dest_row)
        form.addLayout(grid)

        options = QHBoxLayout()
        options.setSpacing(14)
        self.opt_audit = QCheckBox("生成后审计")
        self.opt_audit.setChecked(True)
        self.opt_build = QCheckBox("生成后编译")
        self.opt_build.setChecked(False)
        self.opt_verify = QCheckBox("编译后启动服务端验证")
        self.opt_verify.setChecked(False)
        self.opt_package = QCheckBox("验证后打包")
        self.opt_package.setChecked(False)
        self.opt_mdk = QCheckBox("优先使用官方 MDK")
        self.opt_mdk.setChecked(True)
        self.opt_open = QCheckBox("生成后切换到该工程")
        self.opt_open.setChecked(True)
        for box_widget in (
            self.opt_audit,
            self.opt_build,
            self.opt_verify,
            self.opt_package,
            self.opt_mdk,
            self.opt_open,
        ):
            options.addWidget(box_widget)
        options.addStretch(1)
        form.addLayout(options)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.plan_btn = QPushButton("预览规格")
        self.plan_btn.setObjectName("Ghost")
        self.plan_btn.clicked.connect(self._preview_plan)
        self.generate_btn = QPushButton("生成工程")
        self.generate_btn.setObjectName("Primary")
        self.generate_btn.clicked.connect(self._generate)
        self.audit_btn = QPushButton("审计")
        self.audit_btn.setObjectName("Ghost")
        self.audit_btn.clicked.connect(lambda: self._run_existing("audit"))
        self.build_btn = QPushButton("编译")
        self.build_btn.setObjectName("Ghost")
        self.build_btn.clicked.connect(lambda: self._run_existing("build"))
        self.verify_btn = QPushButton("服务端验证")
        self.verify_btn.setObjectName("Ghost")
        self.verify_btn.clicked.connect(lambda: self._run_existing("verify"))
        self.package_btn = QPushButton("打包")
        self.package_btn.setObjectName("Ghost")
        self.package_btn.clicked.connect(lambda: self._run_existing("package"))
        actions.addWidget(self.plan_btn)
        actions.addWidget(self.generate_btn)
        actions.addSpacing(10)
        actions.addWidget(self.audit_btn)
        actions.addWidget(self.build_btn)
        actions.addWidget(self.verify_btn)
        actions.addWidget(self.package_btn)
        actions.addStretch(1)
        form.addLayout(actions)

        self.status = QLabel("空闲")
        self.status.setObjectName("H2")
        form.addWidget(self.status)
        outer.addWidget(form_card)

        log_card = card()
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(16, 12, 16, 12)
        log_layout.setSpacing(6)
        log_layout.addWidget(QLabel("生成日志"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText(
            "这里会显示版本解析、规格生成、工程落地、审计、Gradle 与验证的实时输出。"
        )
        log_layout.addWidget(self.log, 1)
        outer.addWidget(log_card, 1)

    # ------------------------------------------------------------------
    def _pick_dest(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", self.dest.text())
        if folder:
            self.dest.setText(folder)

    def _append(self, line: str) -> None:
        text = str(line or "").rstrip()
        if not text:
            return
        self.log.appendPlainText(text)
        bar = self.log.verticalScrollBar()
        bar.setValue(bar.maximum())
        self.status.setText(text[-160:])

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for button in (
            self.plan_btn,
            self.generate_btn,
            self.audit_btn,
            self.build_btn,
            self.verify_btn,
            self.package_btn,
        ):
            button.setEnabled(not busy)
        self.open_dir_btn.setEnabled(not busy or self._last_root is not None)

    def _form_values(self) -> dict:
        return {
            "idea": self.idea.toPlainText().strip(),
            "loader": self.loader.currentText().strip(),
            "mc": self.mc.currentText().strip(),
            "name": self.name.text().strip(),
            "modid": self.modid.text().strip(),
            "scope": str(self.scope.currentData() or "medium"),
            "author": self.author.text().strip(),
            "dest_root": self.dest.text().strip(),
        }

    # ------------------------------------------------------------------
    def _preview_plan(self) -> None:
        values = self._form_values()
        try:
            spec = studio_bridge.plan(
                values["idea"],
                values["loader"],
                values["mc"],
                name=values["name"],
                modid=values["modid"],
                scope=values["scope"],
                author=values["author"],
            )
        except studio_bridge.StudioError as exc:
            QMessageBox.warning(self, "规格生成失败", str(exc))
            return
        dialog = TextPreviewDialog(
            f"规格预览 · {spec['identity']['name_zh']}",
            studio_bridge.spec_markdown(spec),
            self,
        )
        dialog.exec()
        self._append(
            f"规格预览：{spec['identity']['name_zh']}，{len(spec['content'])} 个内容节点。"
        )

    def _generate(self) -> None:
        if self._busy:
            return
        values = self._form_values()
        if len(values["idea"]) < 2:
            QMessageBox.warning(self, "缺少构思", "请先写一句模组构思。")
            return
        self.log.clear()
        self._last_root = None
        self._task = "generate"
        self._set_busy(True)
        self.status.setText("正在生成…")
        use_mdk = self.opt_mdk.isChecked()
        audit_after = self.opt_audit.isChecked()
        build_after = self.opt_build.isChecked()
        verify_after = self.opt_verify.isChecked()
        package_after = self.opt_package.isChecked()
        progress = self._signals.line.emit

        def work() -> None:
            try:
                result = studio_bridge.generate(
                    idea=values["idea"],
                    loader=values["loader"],
                    mc=values["mc"],
                    dest_root=values["dest_root"] or None,
                    name=values["name"],
                    modid=values["modid"],
                    scope=values["scope"],
                    author=values["author"],
                    force=True,
                    use_mdk=use_mdk,
                    audit_after=audit_after,
                    build_after=build_after,
                    verify_after=verify_after,
                    package_after=package_after,
                    progress=progress,
                )
            except Exception as exc:  # noqa: BLE001 - 后台任务必须把错误带回界面
                self._signals.failed.emit(str(exc))
            else:
                self._signals.done.emit(result)

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    def _on_done(self, result: object) -> None:
        data = result if isinstance(result, dict) else {}
        root = Path(str(data.get("project") or ""))
        if root.is_dir():
            self._last_root = root
        self._set_busy(False)
        summary = studio_bridge.summarize(data)
        self._append("完成。")
        self._append(summary)
        self.status.setText("生成完成" if self._task == "generate" else "操作完成")
        if self.opt_open.isChecked() and self._last_root is not None:
            self.project_ready.emit(str(self._last_root))
        jar = studio_bridge.primary_jar(self._last_root) if self._last_root else None
        if jar is not None:
            self._append(f"JAR：{jar}")
        else:
            self._append("尚未生成 JAR；需要产物时勾选“生成后编译”。")

    def _on_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status.setText("失败")
        self._append(f"失败：{message}")
        QMessageBox.warning(self, "操作失败", message)

    # ------------------------------------------------------------------
    def _target_root(self) -> Path | None:
        if self._last_root is not None:
            return self._last_root
        # 没有生成过时，尝试使用输出目录 + Mod ID 推断。
        values = self._form_values()
        modid = values["modid"].strip()
        if modid and values["dest_root"]:
            candidate = Path(values["dest_root"]).expanduser() / modid
            if (candidate / "mod-spec.json").is_file():
                return candidate
        current = getattr(self.main, "active_project", None)
        if current is not None and (Path(current.root) / "mod-spec.json").is_file():
            return Path(current.root)
        return None

    def _run_existing(self, kind: str) -> None:
        if self._busy:
            return
        root = self._target_root()
        if root is None:
            QMessageBox.information(
                self,
                "还没有工程",
                "先生成一个工程，或打开一个带 mod-spec.json 的工程。",
            )
            return
        self._last_root = root
        self._task = kind
        self._set_busy(True)
        titles = {
            "audit": "正在审计…",
            "build": "正在编译…",
            "verify": "正在启动服务端验证…",
            "package": "正在打包…",
        }
        self.status.setText(titles.get(kind, "正在运行…"))
        self._append(f"目标工程：{root}")

        def work() -> None:
            try:
                if kind == "audit":
                    report = studio_bridge.audit_project(root, progress=self._signals.line.emit)
                elif kind == "build":
                    report = studio_bridge.build_project(root, progress=self._signals.line.emit)
                elif kind == "verify":
                    report = studio_bridge.verify_project(root, progress=self._signals.line.emit)
                else:
                    report = studio_bridge.package_project(root, progress=self._signals.line.emit)
            except Exception as exc:  # noqa: BLE001 - 后台任务必须把错误带回界面
                self._signals.failed.emit(str(exc))
            else:
                self._signals.done.emit({"project": str(root), kind: report})

        self._thread = threading.Thread(target=work, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------
    def _open_project_dir(self) -> None:
        root = self._target_root()
        if root is None:
            QMessageBox.information(self, "还没有工程", "先生成一个工程。")
            return
        self.main.open_folder(root)

    def set_project(self, project) -> None:
        """主窗口同步当前工程时，把规格工程记为最近目标。"""
        if project is None:
            return
        root = Path(project.root)
        if (root / "mod-spec.json").is_file():
            self._last_root = root
