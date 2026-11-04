from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import project as project_mod
from .. import sessions
from ..agent import AgentWorker, PendingFile, apply_pending
from ..prompt import requirement_form_text
from .widgets import ChatView, NewProjectDialog, TextPreviewDialog, card, tiny


class ChatPage(QWidget):
    status = Signal(str)

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.project = None
        self.history: list[dict[str, str]] = []
        self._worker: AgentWorker | None = None
        self._pending: list[PendingFile] = []
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
        side.setMinimumWidth(270)
        side.setMaximumWidth(420)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(12, 12, 12, 12)
        side_layout.setSpacing(8)
        pending_head = QHBoxLayout()
        pending_title = QLabel("待写入文件")
        pending_title.setObjectName("H2")
        self.pending_count = tiny("0 个")
        pending_head.addWidget(pending_title)
        pending_head.addStretch(1)
        pending_head.addWidget(self.pending_count)
        side_layout.addLayout(pending_head)
        side_layout.addWidget(
            tiny("助手生成的文件先放在这里，确认后再写入工程；覆盖前会自动备份。")
        )

        self.pending_tree = QTreeWidget()
        self.pending_tree.setHeaderLabels(["文件", "来源"])
        self.pending_tree.setRootIsDecorated(False)
        self.pending_tree.setColumnWidth(0, 180)
        self.pending_tree.itemSelectionChanged.connect(self._preview_pending)
        side_layout.addWidget(self.pending_tree, 1)

        self.pending_preview = QPlainTextEdit()
        self.pending_preview.setReadOnly(True)
        self.pending_preview.setPlaceholderText("选中文件预览内容")
        self.pending_preview.setFixedHeight(180)
        side_layout.addWidget(self.pending_preview)

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
        side_layout.addLayout(buttons)
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
        self.form_btn = QPushButton("插入需求单")
        self.form_btn.setObjectName("Ghost")
        self.form_btn.clicked.connect(self._insert_form)
        self.clear_btn = QPushButton("清空对话")
        self.clear_btn.setObjectName("Ghost")
        self.clear_btn.clicked.connect(self._clear_chat)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("Danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        self.send_btn = QPushButton("发送")
        self.send_btn.setObjectName("Primary")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.form_btn)
        row.addWidget(self.clear_btn)
        row.addStretch(1)
        row.addWidget(self.stop_btn)
        row.addWidget(self.send_btn)
        compose.addLayout(row)
        root.addWidget(composer)

        self.input.installEventFilter(self)

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
            "之后我会按工程结构生成完整文件，所有写入都会先进入右侧待确认列表。",
        )
        self._set_pending([])

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
            f"Mod ID {meta.mod_id or '未确认'}  ·  {meta.task_type}"
        )
        self.project_path.setText(str(project.root))
        self.history = sessions.load_history(project.root)
        self.chat.clear_messages()
        if self.history:
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
                "把需求写清楚，我会先给你目录树和完整文件。文件会进入右侧待写入列表，"
                "确认后才会落盘。",
            )
        self._set_pending([])
        self.main.project_changed.emit(project)

    def new_project(self) -> None:
        dialog = NewProjectDialog(self, root=str(self.main.config.project_root))
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        self.main.config.project_root = str(dialog.root.text().strip())
        self._set_project(dialog.project)
        self.main.status(f"工程已创建：{dialog.project.root}")
        self.input.setFocus()

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
            "确定清空当前工程的对话记录？工程文件和记忆库不会受影响。",
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

        worker = AgentWorker(
            user_text=text,
            history=self.history[:-1],
            project=self.project,
            config=self.main.config,
            skill_store=self.main.skill_store,
            memory=self.main.memory,
            connector_store=self.main.connector_store,
        )
        self._worker = worker
        worker.status.connect(self.main.status)
        worker.assistant.connect(self._on_assistant)
        worker.pending_changed.connect(self._set_pending)
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
            row = QTreeWidgetItem([item.path, item.reason or "生成"])
            row.setToolTip(0, item.path)
            self.pending_tree.addTopLevelItem(row)
        self.pending_count.setText(f"{len(self._pending)} 个")
        self.apply_btn.setEnabled(bool(self._pending))
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
        answer = QMessageBox.question(
            self,
            "写入工程",
            f"将写入 {len(self._pending)} 个文件到：\n{self.project.root}\n\n"
            "已存在的文件会先备份到 data/backups。继续？",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            written = apply_pending(self.project, self._pending)
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
        answer = QMessageBox.question(self, "清空待写入", "丢弃这些未写入的文件？")
        if answer == QMessageBox.Yes:
            self._set_pending([])
            self.main.status("已清空待写入列表")

    # ------------------------------------------------------------------
    def _on_update_checked(self, info) -> None:
        if info is not None:
            self.main.status(f"发现新版本 {info.version}，可在设置里更新")
