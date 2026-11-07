from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..spec import ModSpec, checklist, checklist_score, render_manual
from ..workflow import STAGE_HINTS, STAGE_LABELS, WorkflowState

from .widgets import tiny


class SpecPanel(QWidget):
    """规格确认面板：阶段、需求 n/10、说明书、确认按钮。"""

    confirm_requested = Signal()
    replan_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.stage_label = QLabel("需求采集")
        self.stage_label.setObjectName("H2")
        self.score_label = tiny("需求 0/10")
        head.addWidget(self.stage_label)
        head.addStretch(1)
        head.addWidget(self.score_label)
        layout.addLayout(head)

        self.hint = tiny(STAGE_HINTS["draft"])
        layout.addWidget(self.hint)

        self.progress = QProgressBar()
        self.progress.setRange(0, 10)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(7)
        layout.addWidget(self.progress)

        self.checklist = QTreeWidget()
        self.checklist.setHeaderLabels(["需求", "状态"])
        self.checklist.setRootIsDecorated(False)
        self.checklist.setColumnWidth(0, 250)
        self.checklist.setMaximumHeight(190)
        layout.addWidget(self.checklist)

        self.manual = QTextBrowser()
        self.manual.setOpenExternalLinks(False)
        self.manual.setPlaceholderText("规格提交后会显示在这里。")
        layout.addWidget(self.manual, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.replan_btn = QPushButton("重新规划")
        self.replan_btn.setObjectName("Ghost")
        self.replan_btn.setToolTip("回到输入框，补充要求后重新生成规格。")
        self.replan_btn.clicked.connect(self.replan_requested)
        self.confirm_btn = QPushButton("确认规格并开始施工")
        self.confirm_btn.setObjectName("Primary")
        self.confirm_btn.clicked.connect(self.confirm_requested)
        buttons.addWidget(self.replan_btn)
        buttons.addWidget(self.confirm_btn, 1)
        layout.addLayout(buttons)

        self._spec: ModSpec | None = None
        self._state = WorkflowState()
        self.update_view(None, WorkflowState())

    def update_view(self, spec: ModSpec | None, state: WorkflowState | None) -> None:
        self._spec = spec
        self._state = state or WorkflowState()
        stage = self._state.stage if self._state.stage in STAGE_LABELS else "draft"
        self.stage_label.setText(STAGE_LABELS[stage])
        self.hint.setText(STAGE_HINTS.get(stage, ""))
        passed, total = checklist_score(spec) if spec is not None else (0, 10)
        self.score_label.setText(f"需求 {passed}/{total}")
        self.progress.setRange(0, total)
        self.progress.setValue(passed)

        self.checklist.clear()
        for item in checklist(spec) if spec is not None else []:
            row = QTreeWidgetItem([item.label, "已覆盖" if item.ok else "待补充"])
            row.setToolTip(0, item.detail)
            row.setToolTip(1, item.detail)
            if not item.ok:
                row.setForeground(1, QBrush(Qt.gray))
            self.checklist.addTopLevelItem(row)

        if spec is None or spec.empty():
            self.manual.setMarkdown(
                "还没有规格。\n\n在左下角用一句话描述你想做的模组，"
                "AI 会先拆成完整规格，确认后才会开始写代码。"
            )
            self.confirm_btn.setVisible(False)
            self.replan_btn.setVisible(False)
            return
        self.manual.setMarkdown(render_manual(spec))
        review = stage == "review" and not spec.confirmed()
        self.confirm_btn.setVisible(review)
        self.confirm_btn.setEnabled(review)
        self.replan_btn.setVisible(True)
        self.replan_btn.setEnabled(True)


class ArtifactPanel(QWidget):
    """成品面板：真实 JAR 巡检结果与说明书。"""

    open_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.title = QLabel("成品说明书")
        self.title.setObjectName("H2")
        layout.addWidget(self.title)

        self.summary = tiny("编译通过后会读取最终 JAR，识别真实注册内容、配方和纹理。")
        layout.addWidget(self.summary)

        self.view = QTextBrowser()
        self.view.setPlaceholderText("还没有成品。")
        self.view.setOpenExternalLinks(False)
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.open_btn = QPushButton("打开 jar 所在目录")
        self.open_btn.setObjectName("Primary")
        self.open_btn.clicked.connect(self.open_requested)
        self.open_btn.setEnabled(False)
        row.addWidget(self.open_btn)
        layout.addLayout(row)

    def update_view(
        self,
        inventory: dict | None = None,
        manual: str = "",
        jar: str = "",
    ) -> None:
        if not inventory and not manual:
            self.summary.setText(
                "编译通过后会读取最终 JAR，识别真实注册内容、配方和纹理。"
            )
            self.view.setMarkdown("还没有成品。")
            self.open_btn.setEnabled(False)
            return
        if inventory:
            self.summary.setText(
                f"{inventory.get('mod_name') or inventory.get('mod_id') or '模组'} · "
                f"{inventory.get('content_count', 0)} 个注册内容 · "
                f"{len(inventory.get('recipes') or [])} 个配方 · "
                f"{len(inventory.get('textures') or [])} 张纹理"
            )
        else:
            self.summary.setText("已生成成品说明书。")
        self.view.setMarkdown(manual or "还没有成品说明书。")
        self.open_btn.setEnabled(bool(jar))
