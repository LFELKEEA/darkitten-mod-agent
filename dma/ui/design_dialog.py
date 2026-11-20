"""设计方案选择窗口：挑选、重新生成、只改图案或上传自己的方案。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import designs
from .paint_page import PaintDialog
from .widgets import tiny

_KIND_LABEL = {"entity": "生物", "item": "物品图标", "block": "方块"}


class _VariantCard(QFrame):
    chosen = Signal(str)

    def __init__(self, variant: dict, session: designs.DesignSession, parent=None) -> None:
        super().__init__(parent)
        self.variant = variant
        self.setObjectName("PickCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(196)
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        preview = QLabel()
        preview.setAlignment(Qt.AlignCenter)
        preview.setFixedHeight(168)
        pixmap = QPixmap(str(session.dir / variant["preview"]))
        if pixmap.isNull():
            preview.setText("预览不可用")
            preview.setObjectName("Tiny")
        else:
            preview.setPixmap(
                pixmap.scaled(
                    168, 168, Qt.KeepAspectRatio, Qt.FastTransformation
                )
            )
        layout.addWidget(preview)

        title = QLabel(str(variant.get("label") or variant.get("id")))
        title.setStyleSheet("font-weight: 600;")
        layout.addWidget(title)
        note = variant.get("description") or "由当前描述生成"
        layout.addWidget(tiny(note[:80]))
        if variant.get("uploaded"):
            badge = QLabel("自己上传")
            badge.setObjectName("Tiny")
            badge.setStyleSheet("color: #b7a6ff;")
            layout.addWidget(badge)
        elif variant.get("edited"):
            badge = QLabel("手动绘制")
            badge.setObjectName("Tiny")
            badge.setStyleSheet("color: #4ec9a4;")
            layout.addWidget(badge)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.chosen.emit(str(self.variant["id"]))
        super().mousePressEvent(event)


class DesignPickerDialog(QDialog):
    """展示一次设计会话的候选方案，最终把选择结果交回 Agent 线程。"""

    def __init__(self, request: designs.DesignRequest, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.request = request
        self.session = request.session
        self._selected = self.session.variants[0]["id"] if self.session.variants else ""
        self._submitted = False
        self._cards: list[_VariantCard] = []

        self.setWindowTitle("选择设计方案")
        self.resize(880, 660)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        kind = _KIND_LABEL.get(self.session.kind, self.session.kind)
        title = QLabel(f"给「{self.session.name}」选择{kind}方案")
        title.setObjectName("H2")
        layout.addWidget(title)
        sub = tiny(
            f"当前共 {len(self.session.variants)} 个候选。点击卡片选择；"
            "可以补一段更详细的描述后重新生成，或者保留模型只换图案，"
            "也可以直接上传自己的 PNG / 模型文件。"
        )
        layout.addWidget(sub)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        self.cards_row = QHBoxLayout(holder)
        self.cards_row.setContentsMargins(0, 4, 0, 4)
        self.cards_row.setSpacing(10)
        self.cards_row.addStretch(1)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(250)
        layout.addWidget(scroll, 1)

        self.hint = tiny("补充描述（可选）：配色、材质、花纹、耳朵/尾巴等细节。")
        layout.addWidget(self.hint)
        self.extra_input = QPlainTextEdit()
        self.extra_input.setFixedHeight(62)
        self.extra_input.setPlaceholderText(
            "例如：红黑配色、金色描边、长耳朵、发光的绿色眼睛、身上有裂纹"
        )
        layout.addWidget(self.extra_input)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.regen_btn = QPushButton("重新生成")
        self.regen_btn.setObjectName("Ghost")
        self.regen_btn.clicked.connect(self._regenerate)
        self.retexture_btn = QPushButton("只改图案")
        self.retexture_btn.setObjectName("Ghost")
        self.retexture_btn.setToolTip("保留当前选中的模型，只重新生成贴图和预览。")
        self.retexture_btn.clicked.connect(self._retexture)
        self.paint_btn = QPushButton("手动绘制")
        self.paint_btn.setObjectName("Ghost")
        self.paint_btn.setToolTip("用内置画布直接改这套方案的贴图，可以点模型面落笔。")
        self.paint_btn.clicked.connect(self._manual_paint)
        self.upload_btn = QPushButton("上传自己的方案")
        self.upload_btn.setObjectName("Ghost")
        self.upload_btn.clicked.connect(self._upload)
        self.status = tiny("")
        row.addWidget(self.regen_btn)
        row.addWidget(self.retexture_btn)
        row.addWidget(self.paint_btn)
        row.addWidget(self.upload_btn)
        row.addWidget(self.status, 1)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setObjectName("Danger")
        self.cancel_btn.clicked.connect(self._cancel)
        self.adopt_btn = QPushButton("采用这个方案")
        self.adopt_btn.setObjectName("Primary")
        self.adopt_btn.clicked.connect(self._adopt)
        self.adopt_btn.setDefault(True)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.adopt_btn)
        layout.addLayout(row)

        self._refresh()

        self._poll = QTimer(self)
        self._poll.setInterval(300)
        self._poll.timeout.connect(self._check_cancel)
        self._poll.start()

    # ------------------------------------------------------------------
    def _clear_cards(self) -> None:
        for widget in self._cards:
            widget.setParent(None)
            widget.deleteLater()
        self._cards.clear()

    def _refresh(self) -> None:
        self._clear_cards()
        while self.cards_row.count():
            item = self.cards_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for variant in self.session.variants:
            item = _VariantCard(variant, self.session)
            item.chosen.connect(self._select)
            self._cards.append(item)
            self.cards_row.addWidget(item)
        self.cards_row.addStretch(1)
        if not any(v["id"] == self._selected for v in self.session.variants):
            self._selected = self.session.variants[0]["id"] if self.session.variants else ""
        self._paint_selection()

    def _paint_selection(self) -> None:
        for item in self._cards:
            selected = str(item.variant["id"]) == self._selected
            item.setProperty("selected", "true" if selected else "false")
            item.style().unpolish(item)
            item.style().polish(item)
        self.adopt_btn.setEnabled(bool(self._selected))

    def _select(self, variant_id: str) -> None:
        self._selected = variant_id
        self._paint_selection()

    # ------------------------------------------------------------------
    def _regenerate(self) -> None:
        extra = self.extra_input.toPlainText().strip()
        try:
            self.session.generate(extra=extra)
        except Exception as exc:  # noqa: BLE001 - 界面里给出原因
            self.status.setText(f"重新生成失败：{exc}")
            return
        self._selected = self.session.variants[0]["id"]
        self._refresh()
        self.status.setText(f"已重新生成 {len(self.session.variants)} 个方案。")

    def _retexture(self) -> None:
        if not self._selected:
            return
        extra = self.extra_input.toPlainText().strip()
        try:
            self.session.retexture(self._selected, extra=extra)
        except Exception as exc:  # noqa: BLE001
            self.status.setText(f"只改图案失败：{exc}")
            return
        self._refresh()
        self.status.setText(f"已保留模型 {self._selected}，只更新了图案。")

    def _manual_paint(self) -> None:
        if not self._selected:
            return
        item = self.session.variant(self._selected)
        rel = str(item.get("texture") or "").strip()
        if not rel:
            QMessageBox.information(
                self,
                "不能绘制",
                "这个方案只有模型，还没有贴图。先重新生成，或上传一张 PNG 再画。",
            )
            return
        texture = self.session.dir / rel
        model_rel = str(item.get("model") or "").strip()
        # 物品/方块的 model.json 只是贴图引用，没有可绘制的立方体。
        model = (
            self.session.dir / model_rel
            if model_rel and self.session.kind == "entity"
            else None
        )
        dialog = PaintDialog(texture, kind=self.session.kind, model=model, parent=self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            self.session.update_texture(self._selected, texture)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._refresh()
        self.status.setText(f"已保存手动绘制：方案 {self._selected}。")

    def _upload(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "上传自己的方案",
            str(Path.home()),
            "方案文件 (*.png *.json *.bbmodel *.geo);;PNG 贴图 (*.png);;模型文件 (*.json *.bbmodel *.geo)",
        )
        if not path:
            return
        extra = self.extra_input.toPlainText().strip()
        try:
            item = self.session.adopt_upload(path, label=extra)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "上传失败", str(exc))
            return
        self._selected = item["id"]
        self._refresh()
        self.status.setText(f"已加入你的方案 {item['id']}。")

    # ------------------------------------------------------------------
    def _adopt(self) -> None:
        if not self._selected:
            return
        self._submitted = True
        self._poll.stop()
        self.request.submit(
            {
                "action": "adopt",
                "variant": self._selected,
                "session": self.session.id,
            }
        )
        self.accept()

    def _cancel(self) -> None:
        if self._submitted:
            return
        self._submitted = True
        self._poll.stop()
        self.request.submit({"action": "cancel", "session": self.session.id})
        self.reject()

    def _check_cancel(self) -> None:
        if self.request.cancelled():
            self._submitted = True
            self._poll.stop()
            self.reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._submitted:
            self._submitted = True
            self.request.submit({"action": "cancel", "session": self.session.id})
        super().closeEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._cancel()
            return
        super().keyPressEvent(event)
