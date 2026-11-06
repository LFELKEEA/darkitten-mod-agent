from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import project as project_mod
from .. import sessions
from .. import spec as spec_mod
from .. import workflow as workflow_mod
from ..agent import (
    AgentWorker,
    PendingFile,
    apply_pending,
    strip_tool_blocks,
)
from ..prompt import initial_project_request, requirement_form_text
from .widgets import ChatView, NewProjectDialog, TextPreviewDialog, card, tiny
from .workbench_panel import ArtifactPanel, SpecPanel


class ChatPage(QWidget):
    status = Signal(str)

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.project = None
        self.history: list[dict[str, str]] = []
        self._worker: AgentWorker | None = None
        self._pending: list[PendingFile] = []
        self.spec = None
        self.workflow = workflow_mod.WorkflowState()
        self.artifact_inventory: dict = {}
        self.artifact_manual = ""
        self._build()
        self._restore_project()
        self.main.update_checked.connect(self._on_update_checked)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        head.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(3)
        self.project_label = QLabel("尚未打开工程")
        self.project_label.setObjectName("H2")
        self.project_meta = tiny("先在左上角新建或打开一个模组工程。")
        self.project_path = tiny("")
        info.addWidget(self.project_label)
        info.addWidget(self.project_meta)
        info.addWidget(self.project_path)
        head.addLayout(info, 1)

        self.recent_btn = QPushButton("最近")
        self.recent_btn.setObjectName("Ghost")
        self.recent_btn.clicked.connect(self._show_recent)
        self.new_btn = QPushButton("新建工程")
        self.new_btn.setObjectName("Primary")
        self.new_btn.clicked.connect(self.new_project)
        self.open_btn = QPushButton("打开工程")
        self.open_btn.clicked.connect(self.open_project)
        self.delete_btn = QPushButton("删除工程")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.setEnabled(False)
        self.delete_btn.clicked.connect(self.delete_project)
        for button in (self.recent_btn, self.new_btn, self.open_btn, self.delete_btn):
            head.addWidget(button)
        root.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        self.chat = ChatView()
        splitter.addWidget(self.chat)

        side = card()
        side.setMinimumWidth(320)
        side.setMaximumWidth(470)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 10, 10, 10)
        side_layout.setSpacing(6)

        self.side_tabs = QTabWidget()
        self.spec_panel = SpecPanel()
        self.spec_panel.confirm_requested.connect(self._confirm_spec)
        self.spec_panel.replan_requested.connect(self._replan)
        self.side_tabs.addTab(self.spec_panel, "规格")

        files_tab = QWidget()
        files_layout = QVBoxLayout(files_tab)
        files_layout.setContentsMargins(2, 8, 2, 2)
        files_layout.setSpacing(6)
        pending_head = QHBoxLayout()
        pending_title = QLabel("待写入文件")
        pending_title.setObjectName("H2")
        self.pending_count = tiny("0 个")
        pending_head.addWidget(pending_title)
        pending_head.addStretch(1)
        pending_head.addWidget(self.pending_count)
        files_layout.addLayout(pending_head)
        self.pending_hint = tiny(
            "助手生成的文件先放在这里，确认后再写入工程；覆盖前会自动备份。"
        )
        files_layout.addWidget(self.pending_hint)

        self.pending_tree = QTreeWidget()
        self.pending_tree.setHeaderLabels(["文件", "来源"])
        self.pending_tree.setRootIsDecorated(False)
        self.pending_tree.setColumnWidth(0, 180)
        self.pending_tree.itemSelectionChanged.connect(self._preview_pending)
        files_layout.addWidget(self.pending_tree, 1)

        self.pending_preview = QPlainTextEdit()
        self.pending_preview.setReadOnly(True)
        self.pending_preview.setPlaceholderText("选中文件预览内容")
        self.pending_preview.setFixedHeight(180)
        files_layout.addWidget(self.pending_preview)

        buttons = QHBoxLayout()
        self.apply_btn = QPushButton("写入全部")
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(self._apply_all)
        self.discard_btn = QPushButton("清空")
        self.discard_btn.setObjectName("Ghost")
        self.discard_btn.clicked.connect(self._discard_pending)
        self.view_btn = QPushButton("查看")
        self.view_btn.setObjectName("Ghost")
        self.view_btn.clicked.connect(self._open_preview)
        buttons.addWidget(self.apply_btn)
        buttons.addWidget(self.discard_btn)
        buttons.addWidget(self.view_btn)
        files_layout.addLayout(buttons)
        self.side_tabs.addTab(files_tab, "待写入")

        self.artifact_panel = ArtifactPanel()
        self.artifact_panel.open_requested.connect(self._open_artifact_folder)
        self.side_tabs.addTab(self.artifact_panel, "成品")
        side_layout.addWidget(self.side_tabs, 1)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([760, 320])
        root.addWidget(splitter, 1)

        composer = card()
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(12, 10, 12, 10)
        compose.setSpacing(8)
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText(
            "描述功能需求，或贴上报错日志。Ctrl+Enter 发送。"
        )
        self.input.setFixedHeight(88)
        compose.addWidget(self.input)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.auto_apply_box = QCheckBox("自动写入")
        self.auto_apply_box.setToolTip("生成的文件直接写进工程；覆盖前仍会备份。")
        self.auto_apply_box.toggled.connect(self._on_auto_apply)
        self.auto_build_box = QCheckBox("自动编译")
        self.auto_build_box.setToolTip(
            "每轮写入后自动执行 Gradle build；失败时让模型按日志修一轮。"
        )
        self.auto_build_box.toggled.connect(self._on_auto_build)
        self.form_btn = QPushButton("插入需求单")
        self.form_btn.setObjectName("Ghost")
        self.form_btn.clicked.connect(self._insert_form)
        self.clear_btn = QPushButton("清空对话")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self._clear_chat)
        self.continue_btn = QPushButton("继续")
        self.continue_btn.setObjectName("Ghost")
        self.continue_btn.setToolTip("上一轮停在半路时，让它接着执行。")
        self.continue_btn.clicked.connect(self._continue)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.auto_apply_box)
        row.addWidget(self.auto_build_box)
        row.addWidget(self.form_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(self.continue_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.send_btn)
        compose.addLayout(row)
        root.addWidget(composer)

        self.input.installEventFilter(self)
        self._sync_mode_boxes()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if (
                event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() & Qt.ControlModifier
            ):
                self._send()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        self._sync_mode_boxes()

    def _sync_mode_boxes(self) -> None:
        for box, value in (
            (self.auto_apply_box, self.main.config.auto_apply),
            (
                self.auto_build_box,
                self.main.config.auto_build and self.main.config.auto_apply,
            ),
        ):
            box.blockSignals(True)
            box.setChecked(bool(value))
            box.blockSignals(False)
        self.auto_build_box.setEnabled(bool(self.main.config.auto_apply))

    def _on_auto_apply(self, checked: bool) -> None:
        self.main.config.auto_apply = bool(checked)
        self.main.save_config()
        self._sync_mode_boxes()
        self._refresh_pending_hint()
        self.main.status(
            "自动写入已开启：文件直接落盘，覆盖前备份。"
            if checked
            else "自动写入已关闭：生成的文件会先进入待写入列表。"
        )

    def _on_auto_build(self, checked: bool) -> None:
        self.main.config.auto_build = bool(checked)
        self.main.save_config()
        self.main.status(
            "自动编译已开启：每轮写入后自动执行 Gradle build。"
            if checked
            else "自动编译已关闭。"
        )

    def _continue(self) -> None:
        if self.project is None:
            QMessageBox.information(self, "还没有工程", "请先新建或打开一个工程。")
            return
        self.ask("继续。请接着上一步把没做完的事情执行完；如果已经完成，直接给出最终结论。")

    def _refresh_pending_hint(self) -> None:
        if not self.pending_tree.topLevelItemCount():
            self.pending_hint.setText(
                "助手生成的文件先放在这里，确认后再写入工程；覆盖前会自动备份。"
                if not self.main.config.auto_apply
                else "当前是自动写入模式：生成后直接写进工程，并在这里留下记录；覆盖前会自动备份。"
            )
            return
        self._set_pending(self._pending)

    # ------------------------------------------------------------------
    def _load_workflow(self) -> None:
        """从工程里恢复规格、施工阶段和成品清单。"""
        if self.project is None:
            self.spec = None
            self.workflow = workflow_mod.WorkflowState()
            self.artifact_inventory = {}
            self.artifact_manual = ""
            self._refresh_workbench()
            return
        root = self.project.root
        self.spec = spec_mod.load_spec(root)
        self.workflow = workflow_mod.load_state(root, self.spec)
        manual_path = root / workflow_mod.MANUAL_FILE
        if manual_path.is_file():
            try:
                self.artifact_manual = manual_path.read_text(
                    encoding="utf-8", errors="replace"
                )
            except OSError:
                self.artifact_manual = ""
        if self.workflow.jar:
            try:
                inventory = workflow_mod.inspect_artifacts(root)
                self.artifact_inventory = inventory.to_dict()
            except Exception:
                self.artifact_inventory = {}
        self._refresh_workbench()

    def _refresh_workbench(self) -> None:
        self.spec_panel.update_view(self.spec, self.workflow)
        self.artifact_panel.update_view(
            self.artifact_inventory,
            self.artifact_manual,
            self.workflow.jar if self.workflow is not None else "",
        )

    def reload_workflow(self) -> None:
        """供编译页在手动构建完成后刷新规格与成品页。"""
        self._load_workflow()

    def _on_spec_changed(self, spec) -> None:
        if spec is not None:
            self.spec = spec
        self._refresh_workbench()

    def _on_workflow_changed(self, state) -> None:
        if state is not None:
            self.workflow = state
        self._refresh_workbench()

    def _on_artifact_changed(self, payload) -> None:
        if isinstance(payload, dict):
            inventory = payload.get("inventory")
            if isinstance(inventory, dict):
                self.artifact_inventory = inventory
            manual_path = payload.get("manual") or ""
            if self.project is not None and manual_path:
                path = self.project.root / manual_path
                try:
                    self.artifact_manual = path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    pass
        self._refresh_workbench()
        if self.artifact_manual:
            self.side_tabs.setCurrentIndex(2)

    def _open_artifact_folder(self) -> None:
        if self.project is None:
            return
        target = self.project.root / "build" / "libs"
        if not target.is_dir():
            target = self.project.root
        self.main.open_folder(target)

    def _confirm_spec(self) -> None:
        if self.project is None or self.spec is None:
            return
        if self._worker is not None:
            self.main.status("上一轮还在运行，先等它完成或点停止。")
            return
        if self.spec.confirmed():
            self.main.status("规格已经确认过了。")
            return
        try:
            self.spec, self.workflow = workflow_mod.confirm(
                self.project.root, self.spec
            )
        except Exception as exc:
            QMessageBox.warning(self, "确认失败", str(exc))
            return
        self._refresh_workbench()
        self.main.status("规格已确认，开始施工")
        self.ask(
            "规格已确认，开始施工。请严格按任务清单执行：先搭工程骨架和注册内容，"
            "再实现玩法、生成纹理与模型、补配方和语言文件，最后调用 build_project。"
            "每完成一项调用 complete_task；编译失败按失败分析修，不要重新规划。"
        )

    def _replan(self) -> None:
        self.side_tabs.setCurrentIndex(0)
        self.input.setFocus()
        self.main.status("在输入框补充或修改要求，发送后会更新规格。")

    # ------------------------------------------------------------------
    def _stage_for_message(self) -> str:
        if self.spec is None:
            if self.project is not None and (
                (self.project.root / "build.gradle").is_file()
                or (self.project.root / "build.gradle.kts").is_file()
                or any(
                    (self.project.root / "src").rglob("*.java")
                    if (self.project.root / "src").is_dir()
                    else []
                )
            ):
                return "done"
            return "planning"
        if not self.spec.confirmed():
            return "planning"
        if self.workflow.stage == "done":
            return "done"
        return "building"

    # ------------------------------------------------------------------
    def _restore_project(self) -> None:
        saved = str(self.main.config.last_project or "").strip()
        if saved and Path(saved).is_dir():
            try:
                project = project_mod.load_project(Path(saved))
            except Exception:
                project = None
            if project is not None:
                self._set_project(project, quiet=True)
                return
        self._show_empty()

    def _show_empty(self) -> None:
        self.project = None
        self.delete_btn.setEnabled(False)
        self.project_label.setText("尚未打开工程")
        self.project_meta.setText("先在右上角新建或打开一个模组工程。")
        self.project_path.setText("")
        self.chat.clear_messages()
        self.chat.add_message(
            "assistant",
            "我是恶魔小猫工坊，只写 Minecraft Java 模组。\n\n"
            "开始前请先新建一个工程，确认 **MC 版本、加载器、Mod ID** 三项信息。"
            "之后我会先把需求拆成规格；确认规格后才会写代码，最后读取真实 JAR 生成成品说明。",
        )
        self._set_pending([])
        self._load_workflow()

    def _set_project(self, project, quiet: bool = False) -> None:
        self.project = project
        self.delete_btn.setEnabled(True)
        self.main.config.last_project = str(project.root)
        self.main.save_config()
        project_mod.remember_project(project)
        meta = project.meta
        self.project_label.setText(meta.name or meta.mod_id or project.root.name)
        self.project_meta.setText(
            f"{meta.loader}  ·  MC {meta.mc_version or '未确认'}  ·  "
            f"{getattr(meta, 'runtime', '') or '双端'}  ·  "
            f"Mod ID {meta.mod_id or '未确认'}  ·  {meta.task_type}"
        )
        self.project_path.setText(str(project.root))
        self.history = sessions.load_history(project.root)
        self.chat.clear_messages()
        if self.history:
            cleaned: list[dict[str, str]] = []
            for item in self.history:
                role = item.get("role", "assistant")
                content = str(item.get("content") or "")
                if role == "assistant":
                    content = strip_tool_blocks(content)
                    if not content:
                        continue
                cleaned.append({"role": role, "content": content})
            if len(cleaned) != len(self.history):
                self.history = cleaned
                sessions.save_history(project.root, self.history)
            for item in self.history:
                self.chat.add_message(item["role"], item["content"])
            if not quiet:
                self.main.status(f"已载入 {project.root.name} 的会话记录")
        else:
            self.chat.add_message(
                "assistant",
                f"工程已就绪：**{meta.name or meta.mod_id}**\n\n"
                f"- 加载器：{meta.loader}\n- MC 版本：{meta.mc_version}\n"
                f"- Mod ID：`{meta.mod_id}`\n\n"
                "把需求写清楚，我会先给你目录树和完整文件。"
                + (
                    "当前是自动写入模式，文件会直接落盘，并在右侧列表留下记录。"
                    if self.main.config.auto_apply
                    else "文件会进入右侧待写入列表，确认后才会落盘。"
                ),
            )
        self._set_pending([])
        self._load_workflow()
        self.main.project_changed.emit(project)

    def new_project(self) -> None:
        dialog = NewProjectDialog(self, root=str(self.main.config.project_root))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.main.config.project_root = str(dialog.root.text().strip())
        auto_start = bool(dialog.auto_start.isChecked())
        if auto_start:
            self.main.config.auto_apply = True
            self.main.config.auto_build = True
        self.main.save_config()
        self._sync_mode_boxes()
        self._set_project(dialog.project)
        self.main.status(f"工程已创建：{dialog.project.root}")
        if not auto_start:
            self.input.setFocus()
            return
        if not self.main.ai_ready():
            self.main.status("工程已创建。请先在「设置」里配置模型，再点「继续」开始。")
            return
        QTimer.singleShot(0, lambda: self.ask(initial_project_request(dialog.project)))

    def open_project(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "打开模组工程",
            str(self.main.config.project_root or Path.home()),
        )
        if not folder:
            return
        try:
            project = project_mod.load_project(Path(folder))
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        self._set_project(project)
        self.main.status(f"已打开工程：{project.root}")

    def delete_project(self) -> None:
        project = self.project
        if project is None:
            QMessageBox.information(self, "还没有工程", "请先打开要删除的工程。")
            return
        if self._worker is not None:
            QMessageBox.information(
                self, "任务进行中", "助手还在生成内容，请先停止或等本轮结束后再删除。"
            )
            return
        build = getattr(self.main, "build_page", None)
        if build is not None and getattr(build.runner, "running", False):
            QMessageBox.information(
                self, "编译进行中", "请先在「编译运行」页停止 Gradle 任务，再删除工程。"
            )
            return
        name = project.meta.name or project.meta.mod_id or project.root.name
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("删除工程")
        box.setText(f"确定要删除工程「{name}」吗？")
        box.setInformativeText(
            f"目录：{project.root}\n\n"
            "工程文件夹会被移到 Windows 回收站，之后可以从回收站还原；"
            "最近工程和对话记录会一并清除。"
        )
        delete_button = box.addButton("移到回收站", QMessageBox.DestructiveRole)
        cancel_button = box.addButton("取消", QMessageBox.RejectRole)
        box.setDefaultButton(cancel_button)
        box.exec()
        if box.clickedButton() is not delete_button:
            return
        try:
            destination = project_mod.delete_project(project.root)
        except Exception as exc:
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        self.main.config.last_project = ""
        self.main.save_config()
        self._show_empty()
        self.main.project_changed.emit(None)
        if destination is None:
            self.main.status(f"工程已移到回收站：{project.root}")
        else:
            self.main.status(f"工程已移到：{destination}")

    def ask(self, text: str) -> bool:
        """外部页面调用：填入输入框并发送，返回是否真的启动了任务。"""
        text = (text or "").strip()
        if not text:
            return False
        if self._worker is not None:
            self.main.status("上一轮还在运行，先等它完成或点停止。")
            return False
        self.input.setPlainText(text)
        self._send()
        return self._worker is not None

    def _show_recent(self) -> None:
        menu = QMenu(self)
        rows = project_mod.recent_projects()
        if not rows:
            menu.addAction("暂无最近工程").setEnabled(False)
        for item in rows[:12]:
            root = str(item.get("root") or "")
            if not root or not Path(root).is_dir():
                continue
            action = menu.addAction(str(item.get("label") or root))
            action.triggered.connect(lambda _=False, path=root: self._open_path(path))
        menu.exec(self.recent_btn.mapToGlobal(self.recent_btn.rect().bottomLeft()))

    def _open_path(self, path: str) -> None:
        try:
            project = project_mod.load_project(Path(path))
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        self._set_project(project)

    # ------------------------------------------------------------------
    def _insert_form(self) -> None:
        if self.project is None:
            QMessageBox.information(self, "还没有工程", "请先新建或打开一个工程。")
            return
        text = requirement_form_text(self.project)
        prefix = self.input.toPlainText().strip()
        self.input.setPlainText((text + "\n\n" + prefix).strip())
        self.input.setFocus()

    def _clear_chat(self) -> None:
        if self._worker is not None:
            QMessageBox.information(self, "正在运行", "请先停止当前生成任务。")
            return
        if not self.history and not self.chat.bubbles:
            return
        answer = QMessageBox.question(
            self,
            "清空对话",
            "确定清空当前工程的对话记录？工程文件、知识库和长期记忆不会受影响。",
        )
        if answer != QMessageBox.Yes:
            return
        if self.project is not None:
            sessions.clear_history(self.project.root)
        self.history.clear()
        self.chat.clear_messages()
        self._set_pending([])
        self.main.status("对话记录已清空")

    # ------------------------------------------------------------------
    def _send(self) -> None:
        if self._worker is not None:
            self.main.status("上一轮还在运行，先等它完成或点停止。")
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self.project is None:
            QMessageBox.information(self, "还没有工程", "请先新建或打开一个工程。")
            return
        missing = self.project.meta.missing()
        if missing:
            QMessageBox.warning(
                self,
                "信息不完整",
                "还缺少：" + "、".join(missing) + "。请重新创建工程补齐信息。",
            )
            return
        if not self.main.ai_ready():
            QMessageBox.warning(
                self,
                "模型未配置",
                "当前 AI 通道还没有配置好，请到“设置”里填写 API Key 或切换到可用通道。",
            )
            return
        self.input.clear()
        self.chat.add_message("user", text)
        self.history.append({"role": "user", "content": text})
        sessions.save_history(self.project.root, self.history)
        self._set_pending([])
        self._set_running(True)

        stage = self._stage_for_message()
        worker = AgentWorker(
            user_text=text,
            history=self.history[:-1],
            project=self.project,
            config=self.main.config,
            skill_store=self.main.skill_store,
            memory=self.main.memory,
            connector_store=self.main.connector_store,
            knowledge=self.main.knowledge,
            stage=stage,
        )
        self._worker = worker
        worker.status.connect(self.main.status)
        worker.assistant.connect(self._on_assistant)
        worker.pending_changed.connect(self._set_pending)
        worker.spec_changed.connect(self._on_spec_changed)
        worker.workflow_changed.connect(self._on_workflow_changed)
        worker.artifact_changed.connect(self._on_artifact_changed)
        worker.done.connect(self._on_done)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        worker.start()

    def _set_running(self, running: bool) -> None:
        self.send_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.new_btn.setEnabled(not running)
        self.open_btn.setEnabled(not running)
        self.recent_btn.setEnabled(not running)
        self.delete_btn.setEnabled(not running and self.project is not None)
        self.continue_btn.setEnabled(not running)
        self.auto_apply_box.setEnabled(not running)
        self.auto_build_box.setEnabled(not running and bool(self.main.config.auto_apply))
        self.spec_panel.setEnabled(not running)
        self.send_btn.setText("生成中…" if running else "发送")

    def _on_assistant(self, text: str) -> None:
        self.chat.add_message("assistant", text)
        self.history.append({"role": "assistant", "content": text})
        if self.project is not None:
            sessions.save_history(self.project.root, self.history)

    def _on_done(self, text: str) -> None:
        text = (text or "").strip()
        if text:
            self.chat.add_message("assistant", text)
            self.history.append({"role": "assistant", "content": text})
            if self.project is not None:
                sessions.save_history(self.project.root, self.history)
        if self.project is not None:
            self._load_workflow()
        self.main.status("本轮完成")

    def _on_failed(self, message: str) -> None:
        self.chat.add_message("assistant", f"**出错了：** {message}")
        self.main.status(message)

    def _on_finished(self) -> None:
        self._worker = None
        self._set_running(False)

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self.main.status("正在停止…")
            self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _set_pending(self, items: list[PendingFile]) -> None:
        self._pending = list(items)
        self.pending_tree.clear()
        for item in self._pending:
            source = "已自动写入" if item.applied else (item.reason or "生成")
            row = QTreeWidgetItem([item.path, source])
            row.setToolTip(0, item.path)
            row.setToolTip(1, source)
            self.pending_tree.addTopLevelItem(row)
        unwritten = [item for item in self._pending if not item.applied]
        if self._pending and len(unwritten) != len(self._pending):
            self.pending_count.setText(
                f"{len(self._pending)} 个 · {len(unwritten)} 待写入"
            )
        else:
            self.pending_count.setText(f"{len(self._pending)} 个")
        if self.main.config.auto_apply:
            self.pending_hint.setText(
                "当前是自动写入模式：生成后直接写进工程，并在这里留下记录；覆盖前会自动备份。"
            )
        else:
            self.pending_hint.setText(
                "助手生成的文件先放在这里，确认后再写入工程；覆盖前会自动备份。"
            )
        self.apply_btn.setEnabled(bool(unwritten))
        self.discard_btn.setEnabled(bool(self._pending))
        self.view_btn.setEnabled(bool(self._pending))
        if self._pending:
            self.pending_tree.setCurrentItem(self.pending_tree.topLevelItem(0))
            self._show_preview(0)
        else:
            self.pending_preview.clear()

    def _preview_pending(self) -> None:
        rows = self.pending_tree.selectedItems()
        if not rows:
            return
        self._show_preview(self.pending_tree.indexOfTopLevelItem(rows[0]))

    def _show_preview(self, index: int) -> None:
        if 0 <= index < len(self._pending):
            self.pending_preview.setPlainText(self._pending[index].content)

    def _open_preview(self) -> None:
        rows = self.pending_tree.selectedItems()
        if not rows:
            return
        index = self.pending_tree.indexOfTopLevelItem(rows[0])
        if 0 <= index < len(self._pending):
            item = self._pending[index]
            TextPreviewDialog(item.path, item.content, self).exec()

    def _apply_all(self) -> None:
        if self.project is None or not self._pending:
            return
        unwritten = [item for item in self._pending if not item.applied]
        if not unwritten:
            QMessageBox.information(
                self, "已经写入", "列表里的文件都已自动写入工程，不需要再次写入。"
            )
            return
        answer = QMessageBox.question(
            self,
            "写入工程",
            f"将写入 {len(unwritten)} 个文件到：\n{self.project.root}\n\n"
            "已存在的文件会先备份到 data/backups。继续？",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            written = apply_pending(self.project, unwritten)
        except Exception as exc:
            QMessageBox.warning(self, "写入失败", str(exc))
            return
        self.chat.add_message(
            "assistant",
            "已写入 " + str(len(written)) + " 个文件：\n\n"
            + "\n".join(f"- `{item}`" for item in written),
        )
        self.history.append(
            {
                "role": "assistant",
                "content": "已写入文件：" + "、".join(written),
            }
        )
        sessions.save_history(self.project.root, self.history)
        self._set_pending([])
        self.main.status(f"已写入 {len(written)} 个文件")
        self.main.project_changed.emit(self.project)

    def _discard_pending(self) -> None:
        if not self._pending:
            return
        if any(item.applied for item in self._pending):
            message = "清空列表？已自动写入的文件已经落盘，清空不会删除它们。"
        else:
            message = "丢弃这些未写入的文件？"
        answer = QMessageBox.question(self, "清空待写入", message)
        if answer == QMessageBox.Yes:
            self._set_pending([])
            self.main.status("已清空待写入列表")

    # ------------------------------------------------------------------
    def _on_update_checked(self, info) -> None:
        if info is not None:
            self.main.status(f"发现新版本 {info.version}，可在设置里更新")
