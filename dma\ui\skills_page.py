from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..skills import BUILTIN_SKILLS, Skill, SkillError
from .widgets import card, tiny


class SkillsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.current: Skill | None = None
        self._build()
        self.refresh()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        title_box = QVBoxLayout()
        title = QLabel("技能")
        title.setObjectName("H1")
        title_box.addWidget(title)
        title_box.addWidget(
            tiny(
                f"内置 {len(BUILTIN_SKILLS)} 个领域工作流，会按当前这句话自动匹配相关技能。"
                "内置技能可以停用或恢复默认，不能删除。"
            )
        )
        head.addLayout(title_box, 1)
        self.new_btn = QPushButton("新建技能")
        self.new_btn.setObjectName("Primary")
        self.new_btn.clicked.connect(self.new_skill)
        folder_btn = QPushButton("打开目录")
        folder_btn.setObjectName("Ghost")
        folder_btn.clicked.connect(self._open_folder)
        head.addWidget(folder_btn)
        head.addWidget(self.new_btn)
        root.addWidget(header)

        splitter = QSplitter(Qt.Horizontal)
        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索技能…")
        self.search.textChanged.connect(self.refresh)
        left_layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._load_row)
        left_layout.addWidget(self.list, 1)
        splitter.addWidget(left)

        right = card()
        form = QVBoxLayout(right)
        form.setContentsMargins(14, 14, 14, 14)
        form.setSpacing(8)

        self.name = QLineEdit()
        self.name.setPlaceholderText("技能名称")
        self.skill_id = QLineEdit()
        self.skill_id.setPlaceholderText("skill-id（小写字母、数字、- . _）")
        self.description = QLineEdit()
        self.description.setPlaceholderText("一句话说明这个技能什么时候用")
        self.enabled = QCheckBox("启用（停用后不进入系统提示）")
        form.addWidget(QLabel("名称"))
        form.addWidget(self.name)
        form.addWidget(QLabel("技能 ID"))
        form.addWidget(self.skill_id)
        form.addWidget(QLabel("说明"))
        form.addWidget(self.description)
        form.addWidget(self.enabled)
        form.addWidget(QLabel("正文（Markdown）"))
        self.body = QPlainTextEdit()
        self.body.setPlaceholderText("写成可执行的工作流：先确认什么、怎么做、交付什么。")
        form.addWidget(self.body, 1)

        row = QHBoxLayout()
        self.builtin_label = tiny("")
        row.addWidget(self.builtin_label, 1)
        self.reset_btn = QPushButton("恢复默认")
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.clicked.connect(self._reset)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete)
        self.save_btn = QPushButton("保存")
        self.save_btn.setObjectName("Primary")
        self.save_btn.clicked.connect(self.save)
        row.addWidget(self.reset_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.save_btn)
        form.addLayout(row)
        splitter.addWidget(right)
        splitter.setSizes([320, 760])
        root.addWidget(splitter, 1)

        self._skills: list[Skill] = []

    def refresh(self) -> None:
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        self._skills = [
            item
            for item in self.main.skill_store.list()
            if not query
            or query in item.id.lower()
            or query in item.name.lower()
            or query in item.description.lower()
        ]
        self.list.blockSignals(True)
        self.list.clear()
        for item in self._skills:
            suffix = "（内置）" if item.builtin else ""
            if not item.enabled:
                suffix += "（停用）"
            row = QListWidgetItem(f"{item.name}{suffix}")
            row.setToolTip(item.description)
            self.list.addItem(row)
        self.list.blockSignals(False)
        if self._skills:
            self.list.setCurrentRow(0)
        else:
            self.current = None
            self._clear_form()

    def _clear_form(self) -> None:
        self.name.clear()
        self.skill_id.clear()
        self.description.clear()
        self.body.clear()
        self.enabled.setChecked(True)
        self.builtin_label.setText("")
        self.delete_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)

    def _load_row(self, row: int) -> None:
        if row < 0 or row >= len(self._skills):
            return
        skill = self._skills[row]
        self.current = skill
        self.name.setText(skill.name)
        self.skill_id.setText(skill.id)
        self.skill_id.setReadOnly(skill.builtin)
        self.description.setText(skill.description)
        self.body.setPlainText(skill.body)
        self.enabled.setChecked(skill.enabled)
        self.builtin_label.setText("内置技能" if skill.builtin else "自定义技能")
        self.delete_btn.setEnabled(not skill.builtin)
        self.reset_btn.setEnabled(skill.builtin)

    def new_skill(self) -> None:
        self.current = None
        self._clear_form()
        self.skill_id.setReadOnly(False)
        self.skill_id.setText("my-skill")
        self.name.setText("新技能")
        self.delete_btn.setEnabled(False)
        self.reset_btn.setEnabled(False)
        self.name.setFocus()

    def save(self) -> None:
        skill_id = self.skill_id.text().strip()
        if self.current is not None:
            skill_id = self.current.id
        skill = Skill(
            id=skill_id,
            name=self.name.text().strip() or skill_id,
            description=self.description.text().strip(),
            body=self.body.toPlainText(),
            enabled=self.enabled.isChecked(),
            builtin=bool(self.current.builtin) if self.current else False,
        )
        try:
            saved = self.main.skill_store.save(skill)
        except SkillError as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.main.status(f"技能已保存：{saved.id}")
        self.refresh()

    def _delete(self) -> None:
        if self.current is None:
            return
        answer = QMessageBox.question(
            self, "删除技能", f"确定删除技能 {self.current.name}？"
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.main.skill_store.delete(self.current.id)
        except SkillError as exc:
            QMessageBox.warning(self, "无法删除", str(exc))
            return
        self.main.status("技能已删除")
        self.refresh()

    def _reset(self) -> None:
        if self.current is None or not self.current.builtin:
            return
        answer = QMessageBox.question(
            self,
            "恢复默认",
            f"用内置内容覆盖 {self.current.name} 的修改？",
        )
        if answer != QMessageBox.Yes:
            return
        self.main.skill_store.reset(self.current.id)
        self.main.status("已恢复默认技能")
        self.refresh()

    def _open_folder(self) -> None:
        import os

        folder = Path(self.main.skill_store.folder)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
