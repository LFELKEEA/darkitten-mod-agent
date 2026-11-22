"""模型库页面：浏览、编辑、重绘模组里的实体 / 方块 / 物品 / 群系 / 维度。

数据来自 :mod:`dma.modcatalog` 的扫描结果，用户的修改保存在工程内
``.dma/mod_library.json``。这里不直接改模组代码：需要改逻辑时，把条目整理成
一段明确需求交给对话页的 Agent，让 Agent 落到真正的注册、资源和数据文件里。
"""

from __future__ import annotations

import hashlib
import random
import re
import shutil
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import designs, modcatalog
from .. import project as project_mod
from .design_dialog import DesignPickerDialog
from .resource_browser import ResourceBrowserDialog
from .model_editor import (
    ModelEditorDialog,
    create_starter_model,
    starter_relative,
)
from .paint_page import HUMANOID_BOXES, ModelView, PaintDialog, parse_model
from .widgets import SelectComboBox, card, tiny

_KIND_LABEL = dict(modcatalog.KIND_LABELS)
_KIND_ORDER = list(modcatalog.KINDS)


def _parse_fields_text(text: str) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"[:：=]", line, maxsplit=1)
        if len(parts) == 2 and parts[0].strip():
            fields.append({"key": parts[0].strip(), "value": parts[1].strip()})
    return fields


class SkyPreview(QWidget):
    """群系 / 维度天空的示意图：天空、雾和地表颜色。"""

    def __init__(self, entry: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.entry = dict(entry or {})
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        sky = self.entry.get("sky") if isinstance(self.entry.get("sky"), dict) else {}
        sky_color = QColor(str(sky.get("sky") or "#161226"))
        fog_color = QColor(str(sky.get("fog") or "#0b0913"))
        if not sky_color.isValid():
            sky_color = QColor("#161226")
        if not fog_color.isValid():
            fog_color = QColor("#0b0913")
        grass_text = str(sky.get("grass") or "").strip()
        ground_color = QColor(grass_text) if grass_text else QColor()
        if not ground_color.isValid():
            ground_color = fog_color.darker(165)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 10, 10)
        painter.setClipPath(path)

        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0.0, sky_color)
        gradient.setColorAt(0.68, fog_color)
        gradient.setColorAt(1.0, fog_color.darker(135))
        painter.fillRect(self.rect(), gradient)

        if sky_color.lightness() < 95:
            seed = int(
                hashlib.sha1(str(self.entry.get("id") or "sky").encode("utf-8")).hexdigest()[:8],
                16,
            )
            rng = random.Random(seed)
            painter.setPen(Qt.NoPen)
            for _ in range(42):
                x = rng.randrange(0, max(1, self.width()))
                y = rng.randrange(4, max(5, int(self.height() * 0.62)))
                radius = rng.choice([1.0, 1.0, 1.0, 1.6])
                painter.setBrush(QColor(255, 255, 255, rng.randrange(90, 210)))
                painter.drawEllipse(QRectF(x, y, radius * 2, radius * 2))

        horizon = int(self.height() * 0.74)
        painter.fillRect(0, horizon, self.width(), self.height() - horizon, ground_color)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.drawLine(0, horizon, self.width(), horizon)
        painter.setClipping(False)

        painter.setPen(QColor(255, 255, 255, 190))
        painter.drawText(12, 24, f"天空 {sky_color.name().upper()}")
        painter.drawText(12, 44, f"雾 {fog_color.name().upper()}")
        if grass_text:
            painter.drawText(12, 64, f"草地 {ground_color.name().upper()}")
        painter.setPen(QColor(255, 255, 255, 115))
        painter.drawText(12, self.height() - 12, "按当前 worldgen 配置绘制的天空示意")
        painter.end()


class NewEntryDialog(QDialog):
    """新建一个自定义条目。"""

    def __init__(self, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.root = Path(root)
        self.entry: dict | None = None
        self.setWindowTitle("新建条目")
        self.resize(520, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel("新建自定义条目")
        title.setObjectName("H2")
        layout.addWidget(title)
        layout.addWidget(
            tiny(
                "先登记你要的内容；创建后可以用「AI 重新生成外观」做贴图/模型，"
                "或勾选下面的选项，让 Agent 直接把介绍落成注册代码、资源和语言文件。"
            )
        )

        grid = QGridLayout()
        grid.setSpacing(8)
        self.kind = SelectComboBox()
        for kind in _KIND_ORDER:
            self.kind.addItem(_KIND_LABEL[kind], kind)
        self.key = QLineEdit()
        self.key.setPlaceholderText("英文注册名，例如 tide_walker")
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("显示名称，例如 潮汐行者")
        grid.addWidget(QLabel("类别"), 0, 0)
        grid.addWidget(self.kind, 0, 1)
        grid.addWidget(QLabel("注册名"), 1, 0)
        grid.addWidget(self.key, 1, 1)
        grid.addWidget(QLabel("名称"), 2, 0)
        grid.addWidget(self.title_edit, 2, 1)
        layout.addLayout(grid)

        layout.addWidget(QLabel("介绍"))
        self.description = QPlainTextEdit()
        self.description.setFixedHeight(96)
        self.description.setPlaceholderText(
            "这个内容是什么、怎么玩、有什么特性。Agent 会按这段描述实现。"
        )
        layout.addWidget(self.description)

        layout.addWidget(QLabel("初始字段（可选，每行「字段名: 值」）"))
        self.fields = QPlainTextEdit()
        self.fields.setFixedHeight(78)
        self.fields.setPlaceholderText("性别: 雌性\n生命值: 40\n生成群系: 暮光森林")
        layout.addWidget(self.fields)

        self.ask_after = QCheckBox("创建后让 AI 按介绍实现这个内容")
        self.ask_after.setChecked(False)
        layout.addWidget(self.ask_after)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        create = QPushButton("创建")
        create.setObjectName("Primary")
        create.clicked.connect(self._create)
        row.addWidget(cancel)
        row.addWidget(create)
        layout.addLayout(row)

    def _create(self) -> None:
        kind = str(self.kind.currentData() or "entity")
        key = self.key.text().strip().lower()
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key):
            QMessageBox.warning(
                self,
                "注册名不合法",
                "注册名只能使用小写字母、数字和下划线，并以字母开头。",
            )
            return
        try:
            entry = modcatalog.create_custom_entry(
                self.root,
                kind=kind,
                key=key,
                title=self.title_edit.text().strip(),
                description=self.description.toPlainText().strip(),
                fields=_parse_fields_text(self.fields.toPlainText()),
            )
        except Exception as exc:  # noqa: BLE001 - 界面里显示原因
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.entry = entry
        self.accept()


class ModLibraryPage(QWidget):
    """模组模型库：扫描工程、查看外观、编辑介绍并交给 Agent 落地。"""

    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self.root: Path | None = None
        self._meta: dict = {}
        self._entries: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._item_by_id: dict[str, QListWidgetItem] = {}
        self._kind = "all"
        self._entry: dict | None = None
        self._field_rows: list[tuple[str, QLineEdit]] = []
        self._custom_rows: list[tuple[QLineEdit, QLineEdit, QWidget]] = []
        self._title_edit: QLineEdit | None = None
        self._desc: QPlainTextEdit | None = None
        self._note: QLineEdit | None = None
        self._preview: ModelView | None = None
        self._kind_buttons: dict[str, QPushButton] = {}
        self._build_page()

    # ------------------------------------------------------------------
    def _build_page(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 16)
        outer.setSpacing(12)

        header = card()
        head = QVBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        head.setSpacing(6)
        top = QHBoxLayout()
        box = QVBoxLayout()
        box.setSpacing(4)
        title = QLabel("模型库")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "自动读出工程里的实体、方块、物品、群系和维度：生成位置、AI、数值、"
                "交互、天空与维度参数都会列出来。可以改介绍、补字段、手动编辑模型、"
                "绘制贴图、导入模型，或让 AI 重新生成外观、按新设定改逻辑。"
            )
        )
        top.addLayout(box, 1)
        self.new_btn = QPushButton("新建条目")
        self.new_btn.setObjectName("Primary")
        self.new_btn.clicked.connect(self._new_entry)
        self.refresh_btn = QPushButton("重新扫描")
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.clicked.connect(lambda: self._reload())
        self.folder_btn = QPushButton("打开工程目录")
        self.folder_btn.setObjectName("Ghost")
        self.folder_btn.clicked.connect(self._open_folder)
        top.addWidget(self.new_btn, 0, Qt.AlignTop)
        top.addWidget(self.refresh_btn, 0, Qt.AlignTop)
        top.addWidget(self.folder_btn, 0, Qt.AlignTop)
        head.addLayout(top)
        project_row = QHBoxLayout()
        project_row.setSpacing(6)
        project_row.addWidget(tiny("工程"))
        self.project_combo = SelectComboBox()
        self.project_combo.setMinimumWidth(280)
        self.project_combo.setToolTip("按工程查看和编辑；切换只影响模型库，不改对话页工程。")
        self.project_combo.currentIndexChanged.connect(self._on_project_combo)
        project_row.addWidget(self.project_combo)
        self.browse_project_btn = QPushButton("打开其他工程…")
        self.browse_project_btn.setObjectName("Ghost")
        self.browse_project_btn.clicked.connect(self._browse_project)
        project_row.addWidget(self.browse_project_btn)
        project_row.addStretch(1)
        self.online_btn = QPushButton("在线模型 / 皮肤")
        self.online_btn.setObjectName("Ghost")
        self.online_btn.setToolTip(
            "搜索 Modrinth 资源包、LittleSkin 皮肤/披风、YSM 模型；"
            "可以预览并下载到工程。"
        )
        self.online_btn.clicked.connect(lambda: self._open_online())
        project_row.addWidget(self.online_btn)
        head.addLayout(project_row)
        self.project_label = tiny("尚未打开工程")
        head.addWidget(self.project_label)
        outer.addWidget(header)

        filters = card()
        filter_row = QHBoxLayout(filters)
        filter_row.setContentsMargins(12, 8, 12, 8)
        filter_row.setSpacing(6)
        self._kind_group = QButtonGroup(self)
        self._kind_group.setExclusive(True)
        for index, key in enumerate(["all"] + _KIND_ORDER):
            label = "全部" if key == "all" else _KIND_LABEL[key]
            button = QPushButton(f"{label} 0")
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, value=key: self._set_kind(value))
            self._kind_group.addButton(button, index)
            filter_row.addWidget(button)
            self._kind_buttons[key] = button
        self._kind_buttons["all"].setChecked(True)
        filter_row.addStretch(1)
        filter_row.addWidget(tiny("搜索"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("名称、注册名、介绍或字段…")
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(lambda _text: self._refresh_list())
        filter_row.addWidget(self.search)
        outer.addWidget(filters)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(10, 10, 10, 10)
        left_layout.setSpacing(6)
        left_head = QHBoxLayout()
        left_title = QLabel("条目")
        left_title.setObjectName("H2")
        left_head.addWidget(left_title)
        left_head.addStretch(1)
        self.count_label = tiny("0 个")
        left_head.addWidget(self.count_label)
        left_layout.addLayout(left_head)
        self.list = QListWidget()
        self.list.setIconSize(QSize(52, 52))
        self.list.setSpacing(2)
        self.list.currentItemChanged.connect(self._on_current)
        left_layout.addWidget(self.list, 1)
        left.setFixedWidth(336)
        body.addWidget(left)

        right = card()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.NoFrame)
        self.detail_host = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_host)
        self.detail_layout.setContentsMargins(16, 14, 16, 16)
        self.detail_layout.setSpacing(10)
        self.detail_scroll.setWidget(self.detail_host)
        right_layout.addWidget(self.detail_scroll, 1)
        body.addWidget(right, 1)
        outer.addLayout(body, 1)

        self.status_label = tiny("就绪")
        outer.addWidget(self.status_label)

        self._show_empty("先打开一个模组工程，模型库会自动扫描其中的内容。")

    # ------------------------------------------------------------------
    def set_project(self, project) -> None:
        root = Path(project.root).resolve() if project is not None else None
        if root == self.root:
            self._refresh_project_combo()
            return
        self.root = root
        self._reset_library()
        self._refresh_project_combo()
        if root is None:
            self.project_label.setText("尚未打开工程")
            self._show_empty("先打开一个模组工程，模型库会自动扫描其中的内容。")
            return
        self.project_label.setText(f"工程：{root}")
        self._reload()

    def refresh(self) -> None:
        self._reload()

    def _reset_library(self) -> None:
        """切换或关闭工程时清空列表，避免显示上一个工程的条目。"""
        self._meta = {}
        self._entries = []
        self._by_id = {}
        self._item_by_id = {}
        self._entry = None
        if hasattr(self, "list"):
            self.list.blockSignals(True)
            self.list.clear()
            self.list.blockSignals(False)
        if hasattr(self, "count_label"):
            self.count_label.setText("0 个")
        if hasattr(self, "_kind_buttons"):
            for key, button in self._kind_buttons.items():
                label = "全部" if key == "all" else _KIND_LABEL[key]
                button.setText(f"{label} 0")

    def _refresh_project_combo(self) -> None:
        if not hasattr(self, "project_combo"):
            return
        rows: list[tuple[str, str]] = []
        seen: set[str] = set()

        def add(path_value: str, label: str) -> None:
            try:
                path = Path(str(path_value or "")).expanduser().resolve()
            except (OSError, ValueError):
                return
            key = str(path)
            # 只列出真正的模组工程，普通目录不能混进下拉列表。
            if key in seen or not project_mod.looks_like_project(path):
                return
            seen.add(key)
            rows.append((key, str(label or path.name)))

        if self.root is not None:
            name = str(self._meta.get("name") or "").strip()
            add(str(self.root), name or self.root.name)
        for item in project_mod.recent_projects():
            root_value = str(item.get("root") or "")
            if not root_value:
                continue
            label = str(item.get("label") or Path(root_value).name)
            add(root_value, label)
        current = -1
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for index, (path_value, label) in enumerate(rows):
            self.project_combo.addItem(label, path_value)
            if self.root is not None and Path(path_value) == self.root:
                current = index
        self.project_combo.addItem("打开其他工程…", "__browse__")
        if current >= 0:
            self.project_combo.setCurrentIndex(current)
        elif rows:
            self.project_combo.setCurrentIndex(0)
        self.project_combo.blockSignals(False)

    def _on_project_combo(self, _index: int) -> None:
        value = str(self.project_combo.currentData() or "")
        if value == "__browse__":
            self._browse_project()
            return
        if value:
            self._set_library_root(Path(value))

    def _browse_project(self) -> None:
        start = str(self.root.parent) if self.root is not None else str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "选择模组工程", start)
        if not folder:
            self._refresh_project_combo()
            return
        if not project_mod.looks_like_project(folder):
            QMessageBox.warning(
                self,
                "不是模组工程",
                "这个目录里没有工程标记文件，模型库不会扫描它。",
            )
            self._refresh_project_combo()
            return
        self._set_library_root(Path(folder))

    def _set_library_root(self, root: Path) -> None:
        try:
            target = Path(root).expanduser().resolve()
        except (OSError, ValueError):
            return
        if not target.is_dir():
            self.status_label.setText(f"工程目录不存在：{target}")
            self._refresh_project_combo()
            return
        if not project_mod.looks_like_project(target):
            self.status_label.setText(f"目录不是模组工程：{target}")
            self._refresh_project_combo()
            return
        if target == self.root:
            self._refresh_project_combo()
            return
        self.root = target
        self._reset_library()
        self.project_label.setText(f"工程：{target}")
        self._refresh_project_combo()
        self._reload()
        self.status_label.setText(f"已切换到工程：{target}")

    def _open_folder(self) -> None:
        if self.root is None:
            self.status_label.setText("还没有打开工程。")
            return
        self.main.open_folder(self.root)

    # ------------------------------------------------------------------
    def _open_online(self, entry: dict | None = None) -> None:
        if self.root is None:
            QMessageBox.information(
                self, "还没有工程", "先打开一个模组工程，在线资源会下载到它的 reference 目录。"
            )
            return
        callback = None
        if entry is not None and entry.get("kind") in {"entity", "block", "item"}:
            callback = lambda path, row: self._apply_online_asset(entry, path, row)
        dialog = ResourceBrowserDialog(
            self,
            project_root=self.root,
            apply_callback=callback,
            provider="littleskin",
        )
        dialog.exec()

    def _apply_online_asset(
        self, entry: dict, path: Path, row: dict
    ) -> str:
        if self.root is None:
            return f"已下载到 {path}"
        kind = str(row.get("kind") or "")
        name = str(row.get("title") or path.stem)
        if kind in {"skin", "cape"}:
            image = QImage(str(path))
            if image.isNull():
                return f"已下载到 {path}，但这个文件不是可读的图片。"
            relative = self._asset_relative(entry, Path("online.png"), texture=True)
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, "PNG"):
                return f"已下载到 {path}，但无法转换成 PNG。"
            try:
                project_mod.write_bytes(self.root, relative, bytes(buffer.data()))
                modcatalog.save_entry(
                    self.root, str(entry["id"]), texture=relative
                )
            except Exception as exc:  # noqa: BLE001
                return f"已下载到 {path}，但写入工程失败：{exc}"
            entry["texture"] = relative
            self._reload(select_id=str(entry["id"]))
            return f"已把「{name}」应用为「{entry.get('title') or entry.get('key')}」的贴图：{relative}"
        # .ysm / .zip 这类外部模型先放到工程参考目录，不直接塞进 src。
        try:
            relative = Path(".dma") / "downloads" / str(row.get("provider") or "online") / path.name
            target = project_mod.safe_path(self.root, relative.as_posix())
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        except Exception as exc:  # noqa: BLE001
            return f"已下载到 {path}，但写入工程失败：{exc}"
        return (
            f"模型已放到 {relative.as_posix()}。"
            "YSM 模型需要放进 config/ysm/models/ 使用；资源包请解压后参考模型和贴图。"
        )

    # ------------------------------------------------------------------
    def _set_kind(self, kind: str) -> None:
        self._kind = kind
        button = self._kind_buttons.get(kind)
        if button is not None and not button.isChecked():
            button.setChecked(True)
        self._refresh_list()

    def _reload(self, select_id: str | None = None) -> None:
        if self.root is None:
            self._reset_library()
            self._show_empty("先打开一个模组工程，模型库会自动扫描其中的内容。")
            return
        current = select_id
        if current is None and self._entry is not None:
            current = str(self._entry.get("id") or "")
        self.status_label.setText("正在扫描工程…")
        try:
            data = modcatalog.load(self.root)
        except Exception as exc:  # noqa: BLE001 - 扫描失败要在界面里说明
            self._reset_library()
            self._refresh_list()
            self._show_empty(f"扫描失败：{exc}")
            self.status_label.setText(f"扫描失败：{exc}")
            return
        self._meta = dict(data.get("project") or {})
        self._entries = list(data.get("entries") or [])
        self._by_id = {str(item.get("id")): item for item in self._entries}
        counts = dict(data.get("counts") or {})
        for key, button in self._kind_buttons.items():
            label = "全部" if key == "all" else _KIND_LABEL[key]
            count = len(self._entries) if key == "all" else int(counts.get(key) or 0)
            button.setText(f"{label} {count}")
        name = self._meta.get("name") or self.root.name
        modid = self._meta.get("modid") or ""
        extra = " · ".join(bit for bit in (name, modid) if bit)
        self.project_label.setText(f"{extra} · {self.root}")
        self._refresh_list(current)
        self._refresh_project_combo()
        self.status_label.setText(
            f"已扫描 {len(self._entries)} 个条目："
            + "、".join(
                f"{_KIND_LABEL[kind]} {int(counts.get(kind) or 0)}" for kind in _KIND_ORDER
            )
        )

    def _refresh_list(self, select_id: str | None = None) -> None:
        keep = select_id
        if keep is None and self._entry is not None:
            keep = str(self._entry.get("id") or "")
        query = self.search.text().strip().lower()
        self.list.blockSignals(True)
        self.list.clear()
        self._item_by_id = {}
        shown = 0
        for entry in self._entries:
            if self._kind != "all" and entry.get("kind") != self._kind:
                continue
            if query and not self._matches(entry, query):
                continue
            item = QListWidgetItem(self._icon(entry), self._list_text(entry))
            item.setData(Qt.UserRole, str(entry.get("id")))
            item.setToolTip(
                f"{entry.get('id')}\n{str(entry.get('description') or '')[:280]}"
            )
            item.setSizeHint(QSize(0, 68))
            self.list.addItem(item)
            self._item_by_id[str(entry.get("id"))] = item
            shown += 1
        self.count_label.setText(f"{shown} 个")
        self.list.blockSignals(False)

        target = self._item_by_id.get(keep or "")
        if target is None and self.list.count():
            target = self.list.item(0)
        if target is not None:
            self.list.blockSignals(True)
            self.list.setCurrentItem(target)
            self.list.blockSignals(False)
            self._on_current(target, None)
        else:
            self._entry = None
            self._show_empty(
                "没有匹配的条目。可以换个关键词，或点「新建条目」自己添加。"
            )

    def _matches(self, entry: dict, query: str) -> bool:
        parts = [
            str(entry.get("title") or ""),
            str(entry.get("key") or ""),
            str(entry.get("description") or ""),
        ]
        for item in entry.get("fields") or []:
            parts.append(str(item.get("key") or ""))
            parts.append(str(item.get("value") or ""))
        for item in entry.get("custom_fields") or []:
            parts.append(str(item.get("key") or ""))
            parts.append(str(item.get("value") or ""))
        return query in " ".join(parts).lower()

    def _list_text(self, entry: dict) -> str:
        summary = modcatalog.summary(entry)
        title = str(entry.get("title") or entry.get("key"))
        if summary.startswith(title):
            summary = summary[len(title) :].lstrip(" ·")
        description = str(entry.get("description") or "").strip().replace("\n", " ")
        if description:
            summary = f"{summary + ' · ' if summary else ''}{description[:24]}"
        return f"{title}\n{summary}"

    # ------------------------------------------------------------------
    def _on_current(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        entry_id = str(current.data(Qt.UserRole) or "")
        entry = self._by_id.get(entry_id)
        if entry is None:
            return
        self._show_entry(entry)

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                self._clear_layout(child)

    def _show_empty(self, message: str) -> None:
        self._entry = None
        self._preview = None
        self._title_edit = None
        self._desc = None
        self._note = None
        self._field_rows = []
        self._custom_rows = []
        self._clear_layout(self.detail_layout)
        holder = QVBoxLayout()
        holder.setContentsMargins(0, 30, 0, 0)
        label = QLabel(message)
        label.setObjectName("Tiny")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignCenter)
        holder.addWidget(label)
        holder.addStretch(1)
        self.detail_layout.addLayout(holder)

    # ------------------------------------------------------------------
    def _texture_image(self, entry: dict) -> QImage | None:
        relative = str(entry.get("texture") or "").strip()
        if relative and self.root is not None:
            path = (self.root / relative).resolve()
            if path.is_file():
                image = QImage(str(path))
                if not image.isNull():
                    return image
        model = str(entry.get("model") or "").strip()
        if model and self.root is not None and entry.get("kind") in {"entity", "block", "item"}:
            path = (self.root / model).resolve()
            if path.is_file():
                try:
                    from .paint_page import model_texture_images

                    images = model_texture_images(path)
                except Exception:  # noqa: BLE001 - 内嵌贴图读不出来就退回默认图
                    images = []
                if images:
                    return images[0][1]
        return None

    def _fallback_image(self, entry: dict, size: int = 64) -> QImage:
        seed = int(
            hashlib.sha1(
                str(entry.get("id") or entry.get("key") or "entry").encode("utf-8")
            ).hexdigest()[:8],
            16,
        )
        hue = seed % 360
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(QColor.fromHsv(hue, 90, 120))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing, False)
        cell = max(2, size // 8)
        for y in range(0, size, cell):
            for x in range(0, size, cell):
                if (x // cell + y // cell) % 2 == 0:
                    painter.fillRect(
                        x, y, cell, cell, QColor.fromHsv((hue + 18) % 360, 95, 185)
                    )
        painter.setPen(QPen(QColor(0, 0, 0, 70), 1))
        painter.drawRect(0, 0, size - 1, size - 1)
        painter.end()
        return image

    def _sky_icon(self, entry: dict, size: int = 56) -> QImage:
        sky = entry.get("sky") if isinstance(entry.get("sky"), dict) else {}
        sky_color = QColor(str(sky.get("sky") or "#161226"))
        fog_color = QColor(str(sky.get("fog") or "#0b0913"))
        if not sky_color.isValid():
            sky_color = QColor("#161226")
        if not fog_color.isValid():
            fog_color = QColor("#0b0913")
        image = QImage(size, size, QImage.Format_ARGB32)
        painter = QPainter(image)
        gradient = QLinearGradient(0, 0, 0, size)
        gradient.setColorAt(0.0, sky_color)
        gradient.setColorAt(0.72, fog_color)
        gradient.setColorAt(1.0, fog_color.darker(140))
        painter.fillRect(0, 0, size, size, gradient)
        painter.setPen(QPen(QColor(255, 255, 255, 45), 1))
        painter.drawLine(0, int(size * 0.72), size, int(size * 0.72))
        painter.end()
        return image

    def _icon(self, entry: dict) -> QIcon:
        if entry.get("kind") in {"biome", "dimension"}:
            image = self._sky_icon(entry)
        else:
            image = self._texture_image(entry) or self._fallback_image(entry)
        pixmap = QPixmap.fromImage(image).scaled(
            52, 52, Qt.KeepAspectRatio, Qt.FastTransformation
        )
        canvas = QPixmap(56, 56)
        canvas.fill(QColor("#151823"))
        painter = QPainter(canvas)
        painter.drawPixmap((56 - pixmap.width()) // 2, (56 - pixmap.height()) // 2, pixmap)
        painter.end()
        return QIcon(canvas)

    # ------------------------------------------------------------------
    def _show_entry(self, entry: dict) -> None:
        self._entry = entry
        self._clear_layout(self.detail_layout)
        self._field_rows = []
        self._custom_rows = []
        self._preview = None
        self._title_edit = None
        self._desc = None
        self._note = None

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        kind_label = QLabel(_KIND_LABEL.get(str(entry.get("kind")), "条目"))
        kind_label.setObjectName("Tiny")
        kind_label.setStyleSheet(
            "color: #b7a6ff; border: 1px solid #4a4272; border-radius: 6px;"
            "padding: 1px 8px;"
        )
        title_row.addWidget(kind_label)
        if entry.get("custom"):
            custom_label = QLabel("自定义")
            custom_label.setObjectName("Tiny")
            custom_label.setStyleSheet(
                "color: #4ec9a4; border: 1px solid #2f6b5a; border-radius: 6px;"
                "padding: 1px 8px;"
            )
            title_row.addWidget(custom_label)
        title_row.addStretch(1)
        title_box.addLayout(title_row)
        self._title_edit = QLineEdit(str(entry.get("title") or entry.get("key")))
        self._title_edit.setStyleSheet("font-size: 18px; font-weight: 600;")
        title_box.addWidget(self._title_edit)
        title_box.addWidget(tiny(f"注册名：{entry.get('key')}"))
        head.addLayout(title_box, 1)
        self.detail_layout.addLayout(head)

        self.detail_layout.addWidget(self._preview_widget(entry))

        paths = []
        for field, label in (("source", "源码"), ("model", "模型"), ("texture", "贴图")):
            value = str(entry.get(field) or "").strip()
            if value:
                paths.append(f"{label}：{value}")
        if paths:
            self.detail_layout.addWidget(tiny("　".join(paths)))

        # 介绍
        self.detail_layout.addWidget(QLabel("介绍"))
        self._desc = QPlainTextEdit()
        self._desc.setPlainText(str(entry.get("description") or ""))
        self._desc.setFixedHeight(104)
        self._desc.setPlaceholderText(
            "写清楚它是什么、怎么生成、有什么行为。点下面的「让 AI 按描述修改」会据此改代码。"
        )
        self.detail_layout.addWidget(self._desc)

        # 标准字段
        self.detail_layout.addWidget(QLabel("字段"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        fields = list(entry.get("fields") or [])
        for row, item in enumerate(fields):
            key = str(item.get("key") or "")
            label = QLabel(key)
            label.setObjectName("Tiny")
            label.setFixedWidth(104)
            edit = QLineEdit(str(item.get("value") or ""))
            edit.setPlaceholderText("未填写")
            grid.addWidget(label, row, 0)
            grid.addWidget(edit, row, 1)
            self._field_rows.append((key, edit))
        if not fields:
            grid.addWidget(tiny("没有自动识别到字段，可以在下面手动添加。"), 0, 0, 1, 2)
        grid.setColumnStretch(1, 1)
        self.detail_layout.addLayout(grid)

        # 自定义字段
        custom_title = QHBoxLayout()
        custom_title.addWidget(QLabel("自定义字段"))
        custom_title.addStretch(1)
        custom_title.addWidget(tiny("例如：性别、阵营偏好、特殊掉落"))
        self.detail_layout.addLayout(custom_title)
        custom_box = QVBoxLayout()
        custom_box.setSpacing(5)
        self.detail_layout.addLayout(custom_box)
        self._custom_box = custom_box
        for item in entry.get("custom_fields") or []:
            self._add_custom_row(str(item.get("key") or ""), str(item.get("value") or ""))

        add_row = QHBoxLayout()
        add_row.setSpacing(6)
        new_key = QLineEdit()
        new_key.setPlaceholderText("字段名，例如 性别")
        new_value = QLineEdit()
        new_value.setPlaceholderText("值，例如 雌性")
        add_btn = QPushButton("添加字段")
        add_btn.setObjectName("Ghost")
        add_btn.clicked.connect(
            lambda: self._add_custom_row(new_key.text(), new_value.text(), new_key, new_value)
        )
        add_row.addWidget(new_key, 2)
        add_row.addWidget(new_value, 3)
        add_row.addWidget(add_btn)
        self.detail_layout.addLayout(add_row)

        # 补充要求
        note_row = QHBoxLayout()
        note_row.addWidget(tiny("AI 补充要求"))
        self._note = QLineEdit()
        self._note.setPlaceholderText(
            "可选，例如：改成夜间生成、增加一个远程技能、让它和玩家交易"
        )
        note_row.addWidget(self._note, 1)
        self.detail_layout.addLayout(note_row)

        actions = QGridLayout()
        actions.setSpacing(7)
        save_btn = QPushButton("保存修改")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(lambda: self._save(entry))
        paint_btn = QPushButton("手动绘制贴图")
        paint_btn.setObjectName("Ghost")
        paint_btn.clicked.connect(lambda: self._manual_paint(entry))
        edit_model_btn = QPushButton("手动编辑模型")
        edit_model_btn.setObjectName("Primary")
        edit_model_btn.setToolTip(
            "打开模型编辑器：在 3D 视图里选中立方体，修改位置、尺寸、旋转、"
            "轴心、外扩和贴图 UV。"
        )
        edit_model_btn.clicked.connect(lambda: self._edit_model(entry))
        blank_model_btn = QPushButton("新建空白模型")
        blank_model_btn.setObjectName("Ghost")
        blank_model_btn.setToolTip(
            "从零搭模型：先建一个空文件，再用旋转、轴心、外扩、镜像和阵列自己拼。"
        )
        blank_model_btn.clicked.connect(lambda: self._edit_model(entry, blank=True))
        model_btn = QPushButton("导入模型")
        model_btn.setObjectName("Ghost")
        model_btn.clicked.connect(lambda: self._import_model(entry))
        texture_btn = QPushButton("导入贴图")
        texture_btn.setObjectName("Ghost")
        texture_btn.clicked.connect(lambda: self._import_texture(entry))
        regen_btn = QPushButton("AI 重新生成外观")
        regen_btn.setObjectName("Ghost")
        regen_btn.clicked.connect(lambda: self._regenerate_art(entry))
        logic_btn = QPushButton("让 AI 按描述修改")
        logic_btn.setObjectName("Primary")
        logic_btn.clicked.connect(lambda: self._ai_modify(entry))
        delete_btn = QPushButton("删除条目")
        delete_btn.setObjectName("Danger")
        delete_btn.clicked.connect(lambda: self._delete_entry(entry))
        open_model_btn = QPushButton("用外部工具打开模型")
        open_model_btn.setObjectName("Ghost")
        open_model_btn.setToolTip("用系统关联的程序（例如 Blockbench）打开当前模型文件。")
        open_model_btn.clicked.connect(lambda: self._open_model(entry))
        online_btn = QPushButton("从在线资源获取")
        online_btn.setObjectName("Ghost")
        online_btn.setToolTip(
            "搜索 LittleSkin、Modrinth、YSM 等来源；皮肤可直接用作当前条目的贴图。"
        )
        online_btn.clicked.connect(lambda: self._open_online(entry))

        if entry.get("kind") in {"biome", "dimension"}:
            model_btn.setEnabled(False)
            texture_btn.setEnabled(False)
            paint_btn.setEnabled(False)
            edit_model_btn.setEnabled(False)
            blank_model_btn.setEnabled(False)
            regen_btn.setEnabled(False)
            open_model_btn.setEnabled(False)
            online_btn.setEnabled(False)
            hint = "群系 / 维度没有可绘制的模型，天空外观由 worldgen 的颜色和数据决定。"
        else:
            hint = ""
        if not self._abspath(entry.get("model")):
            edit_model_btn.setToolTip(
                "还没有模型文件；点击后会先新建一个可编辑的起始模型。"
            )
            open_model_btn.setEnabled(False)
            open_model_btn.setToolTip("还没有模型文件；可以先用「AI 重新生成外观」或「导入模型」。")
        if not entry.get("custom"):
            delete_btn.setEnabled(False)
            delete_btn.setToolTip("只能删除在这里新建的自定义条目。")

        actions.addWidget(save_btn, 0, 0)
        actions.addWidget(paint_btn, 0, 1)
        actions.addWidget(edit_model_btn, 0, 2)
        actions.addWidget(model_btn, 0, 3)
        actions.addWidget(texture_btn, 1, 0)
        actions.addWidget(regen_btn, 1, 1, 1, 2)
        actions.addWidget(logic_btn, 1, 3)
        actions.addWidget(online_btn, 2, 0)
        actions.addWidget(delete_btn, 2, 1)
        actions.addWidget(blank_model_btn, 2, 2)
        actions.addWidget(open_model_btn, 2, 3)
        self.detail_layout.addLayout(actions)
        if hint:
            self.detail_layout.addWidget(tiny(hint))
        self.detail_layout.addStretch(1)

    def _model_boxes(self, entry: dict):
        relative = str(entry.get("model") or "").strip()
        if not relative or self.root is None:
            return []
        candidate = (self.root / relative).resolve()
        if not candidate.is_file():
            return []
        try:
            return parse_model(candidate)
        except Exception:  # noqa: BLE001 - 模型坏了就退回默认骨架/图标
            return []

    def _preview_widget(self, entry: dict) -> QWidget:
        kind = str(entry.get("kind") or "")
        holder = QFrame()
        holder.setObjectName("CardSoft")
        holder.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        if kind in {"biome", "dimension"}:
            layout.addWidget(SkyPreview(entry))
            swatches = QHBoxLayout()
            sky = entry.get("sky") if isinstance(entry.get("sky"), dict) else {}
            for label, key in (("天空", "sky"), ("雾", "fog"), ("草地", "grass"), ("树叶", "foliage")):
                value = str(sky.get(key) or "").strip()
                if not value:
                    continue
                chip = QLabel(f"{label} {value}")
                chip.setObjectName("Tiny")
                color = QColor(value)
                border = color.name() if color.isValid() else "#2b3142"
                chip.setStyleSheet(
                    f"border: 1px solid {border}; border-radius: 6px; padding: 2px 7px;"
                    f"background: {value if color.isValid() else '#151823'};"
                    "color: #ffffff;"
                )
                swatches.addWidget(chip)
            swatches.addStretch(1)
            layout.addLayout(swatches)
            return holder

        image = self._texture_image(entry) or self._fallback_image(entry)
        boxes = self._model_boxes(entry)
        if kind == "item" and not boxes:
            label = QLabel()
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(250)
            pixmap = QPixmap.fromImage(image).scaled(
                250, 250, Qt.KeepAspectRatio, Qt.FastTransformation
            )
            label.setPixmap(pixmap)
            layout.addWidget(label)
            layout.addWidget(tiny("物品图标预览（有 .bbmodel / .geo.json 时会显示 3D）"), 0, Qt.AlignCenter)
            return holder

        view = ModelView()
        view.setMinimumHeight(280)
        view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if kind == "block" or (kind == "item" and boxes):
            if boxes:
                view.set_kind("custom")
                view.set_boxes(boxes)
            else:
                view.set_kind("block")
        else:
            view.set_kind("entity")
            view.set_boxes(boxes or HUMANOID_BOXES)
        view.set_hint("")
        view.set_image(image)
        self._preview = view
        layout.addWidget(view, 1)
        row = QHBoxLayout()
        row.addWidget(tiny("3D 预览；模型缺失时用默认骨架，不影响贴图编辑。"))
        row.addStretch(1)
        flip = QCheckBox("查看背面")
        flip.toggled.connect(view.set_back_view)
        row.addWidget(flip)
        layout.addLayout(row)
        return holder

    # ------------------------------------------------------------------
    def _add_custom_row(
        self,
        key: str = "",
        value: str = "",
        key_source: QLineEdit | None = None,
        value_source: QLineEdit | None = None,
    ) -> None:
        if not str(key).strip() and key_source is None:
            return
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        key_edit = QLineEdit(str(key).strip())
        key_edit.setPlaceholderText("字段名")
        key_edit.setFixedWidth(150)
        value_edit = QLineEdit(str(value).strip())
        value_edit.setPlaceholderText("值")
        remove = QPushButton("×")
        remove.setObjectName("Ghost")
        remove.setFixedWidth(32)
        remove.setToolTip("删除这个字段")
        entry_ref = (key_edit, value_edit, row)
        remove.clicked.connect(lambda: self._remove_custom_row(entry_ref))
        layout.addWidget(key_edit, 0)
        layout.addWidget(value_edit, 1)
        layout.addWidget(remove, 0)
        self._custom_box.addWidget(row)
        self._custom_rows.append(entry_ref)
        if key_source is not None:
            key_source.clear()
        if value_source is not None:
            value_source.clear()

    def _remove_custom_row(self, ref) -> None:
        self._custom_rows = [item for item in self._custom_rows if item[2] is not ref[2]]
        ref[2].setParent(None)
        ref[2].deleteLater()

    def _collect_custom(self) -> list[dict[str, str]]:
        result: list[dict[str, str]] = []
        for key_edit, value_edit, _row in self._custom_rows:
            key = key_edit.text().strip()
            if key:
                result.append({"key": key, "value": value_edit.text().strip()})
        return result

    def _snapshot(self, entry: dict) -> dict:
        snapshot = dict(entry)
        if self._entry is entry and self._title_edit is not None:
            snapshot["title"] = self._title_edit.text().strip() or entry.get("key")
            if self._desc is not None:
                snapshot["description"] = self._desc.toPlainText().strip()
            snapshot["fields"] = [
                {"key": key, "value": edit.text().strip()}
                for key, edit in self._field_rows
            ]
            snapshot["custom_fields"] = self._collect_custom()
        return snapshot

    def _save(self, entry: dict, *, silent: bool = False) -> bool:
        if self.root is None or self._entry is not entry:
            return False
        if self._title_edit is None:
            return False
        title = self._title_edit.text().strip() or str(entry.get("key"))
        description = self._desc.toPlainText().strip() if self._desc is not None else ""
        fields = {key: edit.text().strip() for key, edit in self._field_rows}
        custom = self._collect_custom()
        try:
            modcatalog.save_entry(
                self.root,
                str(entry["id"]),
                title=title,
                description=description,
                fields=fields,
                custom_fields=custom,
            )
        except Exception as exc:  # noqa: BLE001 - 保存失败给出原因
            self.status_label.setText(f"保存失败：{exc}")
            if not silent:
                QMessageBox.warning(self, "保存失败", str(exc))
            return False
        entry["title"] = title
        entry["description"] = description
        merged: list[dict[str, str]] = []
        for key, edit in self._field_rows:
            merged.append({"key": key, "value": edit.text().strip()})
        entry["fields"] = merged
        entry["custom_fields"] = list(custom)
        item = self._item_by_id.get(str(entry["id"]))
        if item is not None:
            item.setText(self._list_text(entry))
            item.setToolTip(
                f"{entry.get('id')}\n{str(entry.get('description') or '')[:280]}"
            )
        self.status_label.setText(f"已保存：{title}")
        return True

    # ------------------------------------------------------------------
    def _new_entry(self) -> None:
        if self.root is None:
            self.status_label.setText("先打开一个工程，再新建条目。")
            return
        dialog = NewEntryDialog(self.root, self)
        if dialog.exec() != QDialog.Accepted or dialog.entry is None:
            return
        entry = dialog.entry
        self._reload(select_id=str(entry.get("id")))
        self.status_label.setText(f"已新建条目：{entry.get('title')}（{entry.get('id')}）")
        if dialog.ask_after.isChecked():
            self._send_instruction(entry, "")

    def _delete_entry(self, entry: dict) -> None:
        if self.root is None or not entry.get("custom"):
            return
        answer = QMessageBox.question(
            self,
            "删除条目",
            f"确定删除自定义条目「{entry.get('title') or entry.get('key')}」吗？\n\n"
            "只会删除模型库里的这条记录，不会删除工程里已经生成的代码和资源。",
        )
        if answer != QMessageBox.Yes:
            return
        try:
            removed = modcatalog.delete_custom_entry(self.root, str(entry["id"]))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "删除失败", str(exc))
            return
        if not removed:
            self.status_label.setText("这条记录已经不存在了。")
            return
        self._entry = None
        self._reload()
        self.status_label.setText("已删除自定义条目。")

    # ------------------------------------------------------------------
    def _abspath(self, relative: str | None) -> Path | None:
        text = str(relative or "").strip()
        if not text or self.root is None:
            return None
        path = Path(text)
        if not path.is_absolute():
            path = self.root / path
        return path.resolve()

    def _asset_relative(self, entry: dict, source: Path, *, texture: bool) -> str:
        modid = str(self._meta.get("modid") or "modid")
        key = str(entry.get("key") or "entry")
        kind = str(entry.get("kind") or "item")
        if texture:
            folder = {"entity": "entity", "block": "block", "item": "item"}.get(kind, "item")
            return f"src/main/resources/assets/{modid}/textures/{folder}/{key}.png"
        name = source.name.lower()
        if kind == "entity":
            if name.endswith(".geo.json"):
                return f"src/main/resources/assets/{modid}/geo/{key}.geo.json"
            if name.endswith(".bbmodel"):
                return f"src/main/resources/assets/{modid}/geo/{key}.bbmodel"
            return f"src/main/resources/assets/{modid}/models/entity/{key}.json"
        folder = "block" if kind == "block" else "item"
        if name.endswith(".geo.json"):
            return f"src/main/resources/assets/{modid}/geo/{key}.geo.json"
        if name.endswith(".bbmodel"):
            return f"src/main/resources/assets/{modid}/geo/{key}.bbmodel"
        return f"src/main/resources/assets/{modid}/models/{folder}/{key}.json"

    def _ensure_texture(self, entry: dict) -> Path | None:
        existing = self._abspath(entry.get("texture"))
        if existing is not None and existing.is_file():
            return existing
        if self.root is None:
            return None
        image = self._texture_image(entry) or self._fallback_image(entry, 64)
        relative = self._asset_relative(entry, Path("fallback.png"), texture=True)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "PNG"):
            return None
        try:
            target = project_mod.write_bytes(self.root, relative, bytes(buffer.data()))
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText(f"创建贴图失败：{exc}")
            return None
        modcatalog.save_entry(self.root, str(entry["id"]), texture=relative)
        entry["texture"] = relative
        return target

    def _manual_paint(self, entry: dict) -> None:
        if self.root is None:
            return
        texture = self._ensure_texture(entry)
        if texture is None:
            QMessageBox.warning(self, "无法绘制", "没有可用的贴图路径。")
            return
        model = self._abspath(entry.get("model"))
        kind = str(entry.get("kind") or "item")
        dialog = PaintDialog(
            texture,
            kind=kind if kind in {"entity", "block"} else "item",
            model=model if kind != "item" else None,
            parent=self,
        )
        dialog.exec()
        self._reload(select_id=str(entry["id"]))
        self.status_label.setText(f"贴图已更新：{entry.get('title')}")

    def _edit_model(self, entry: dict, *, blank: bool = False) -> None:
        if self.root is None:
            return
        relative = str(entry.get("model") or "").strip()
        path = self._abspath(relative)
        if path is None or not path.is_file():
            if not blank:
                answer = QMessageBox.question(
                    self,
                    "新建模型",
                    "这个条目还没有模型文件。要新建一个可以直接手动编辑的起始模型吗？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if answer != QMessageBox.Yes:
                    return
            kind = str(entry.get("kind") or "item")
            modid = str(self._meta.get("modid") or "modid")
            key = str(entry.get("key") or "entry")
            # 空白模式也要有一个合法的容器文件，先按起始模型建出来再清空列表。
            relative = starter_relative(kind, modid, key)
            try:
                create_starter_model(
                    self.root,
                    relative,
                    kind=kind,
                    name=str(entry.get("title") or key),
                    modid=modid,
                    key=key,
                )
                modcatalog.save_entry(self.root, str(entry["id"]), model=relative)
                entry["model"] = relative
            except Exception as exc:  # noqa: BLE001 - 新建失败要说明原因
                QMessageBox.warning(self, "新建模型失败", str(exc))
                return
            path = self._abspath(relative)
        texture_path = self._ensure_texture(entry)
        texture = QImage(str(texture_path)) if texture_path is not None else QImage()
        if texture.isNull():
            texture = self._texture_image(entry) or self._fallback_image(entry, 64)
        try:
            dialog = ModelEditorDialog(
                self.root, relative, texture, parent=self, blank=blank
            )
        except Exception as exc:  # noqa: BLE001 - 不支持的模型要说明原因
            QMessageBox.warning(
                self,
                "无法编辑模型",
                f"{Path(relative).name} 暂时不能手动编辑：{exc}",
            )
            return
        if dialog.exec():
            self._reload(select_id=str(entry["id"]))
            self.status_label.setText(f"模型已保存：{Path(relative).name}")

    def _open_model(self, entry: dict) -> None:
        path = self._abspath(entry.get("model"))
        if path is None or not path.is_file():
            self.status_label.setText("还没有模型文件，先导入或让 AI 生成外观。")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.status_label.setText(f"无法打开模型文件：{path}")

    def _import_model(self, entry: dict) -> None:
        if self.root is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入模型",
            str(Path.home()),
            "模型文件 (*.geo.json *.bbmodel *.json)",
        )
        if not path:
            return
        source = Path(path)
        relative = self._asset_relative(entry, source, texture=False)
        try:
            project_mod.write_bytes(self.root, relative, source.read_bytes())
            modcatalog.save_entry(self.root, str(entry["id"]), model=relative)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._reload(select_id=str(entry["id"]))
        lowered = source.name.lower()
        note = ""
        if lowered.endswith(".bbmodel"):
            note = "；.bbmodel 可以直接预览和绘制，游戏加载前需导出成 .geo.json 或 Java 模型"
        elif lowered.endswith(".geo.json"):
            note = "；.geo.json 需要对应运行时（如 GeckoLib）才能被游戏加载"
        self.status_label.setText(f"模型已导入：{relative}{note}")

    def _import_texture(self, entry: dict) -> None:
        if self.root is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "导入贴图",
            str(Path.home()),
            "图片 (*.png *.jpg *.jpeg *.bmp *.gif)",
        )
        if not path:
            return
        image = QImage(path)
        if image.isNull():
            QMessageBox.warning(self, "导入失败", "这个图片格式读不出来。")
            return
        relative = self._asset_relative(entry, Path(path), texture=True)
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "PNG"):
            QMessageBox.warning(self, "导入失败", "无法把图片转成 PNG。")
            return
        try:
            project_mod.write_bytes(self.root, relative, bytes(buffer.data()))
            modcatalog.save_entry(self.root, str(entry["id"]), texture=relative)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导入失败", str(exc))
            return
        self._reload(select_id=str(entry["id"]))
        self.status_label.setText(f"贴图已导入：{relative}")

    # ------------------------------------------------------------------
    def _regenerate_art(self, entry: dict) -> None:
        kind = str(entry.get("kind") or "")
        if self.root is None:
            return
        if kind not in {"entity", "block", "item"}:
            return
        if self._save(entry, silent=True) is False and self._entry is entry:
            return
        snapshot = self._snapshot(entry)
        bits = [str(snapshot.get("title") or snapshot.get("key"))]
        description = str(snapshot.get("description") or "").strip()
        if description:
            bits.append(description)
        for item in (snapshot.get("fields") or []) + (snapshot.get("custom_fields") or []):
            text = str(item.get("value") or "").strip()
            if text:
                bits.append(f"{item.get('key')}={text}")
        try:
            session = designs.create_session(
                self.root,
                kind=kind,
                name=str(entry.get("key") or "entry"),
                description="；".join(bits),
                namespace=str(self._meta.get("modid") or "modid"),
                count=3,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "生成失败", str(exc))
            return
        request = designs.DesignRequest(session)
        dialog = DesignPickerDialog(request, self)
        dialog.exec()
        result = request.result or {}
        if result.get("action") != "adopt":
            self.status_label.setText("已取消外观生成。")
            return
        try:
            report = designs.apply_variant(self.root, session, str(result.get("variant")))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "应用失败", str(exc))
            return
        self._reload(select_id=str(entry["id"]))
        notes = "；".join(report.get("notes") or [])
        self.status_label.setText(
            f"已应用外观方案 {result.get('variant')}" + (f"（{notes}）" if notes else "")
        )

    def _ai_modify(self, entry: dict) -> None:
        if self._entry is entry:
            if self._save(entry, silent=True) is False:
                return
        snapshot = self._snapshot(entry)
        note = self._note.text().strip() if self._note is not None else ""
        self._send_instruction(snapshot, note)

    def _send_instruction(self, entry: dict, note: str) -> None:
        main = self.main
        chat = getattr(main, "chat_page", None)
        if chat is None or getattr(chat, "project", None) is None:
            self.status_label.setText("先在对话页打开一个工程，才能让 Agent 修改。")
            return
        if self.root is not None:
            try:
                chat_root = Path(chat.project.root).resolve() if chat.project else None
            except Exception:  # noqa: BLE001
                chat_root = None
            if chat_root != self.root.resolve():
                if getattr(chat, "_worker", None) is not None:
                    self.status_label.setText(
                        "Agent 正在运行另一个工程，等这一轮结束再切换。"
                    )
                    return
                try:
                    chat.open_project_path(str(self.root))
                    chat_root = (
                        Path(chat.project.root).resolve() if chat.project else None
                    )
                except Exception as exc:  # noqa: BLE001
                    self.status_label.setText(f"切换对话页工程失败：{exc}")
                    return
                if chat_root != self.root.resolve():
                    self.status_label.setText(
                        "对话页的工程和模型库不一致，请先在对话页打开当前工程。"
                    )
                    return
        meta = getattr(chat.project, "meta", None)
        missing = meta.missing() if meta is not None and hasattr(meta, "missing") else []
        if missing:
            self.status_label.setText("工程还缺少：" + "、".join(missing) + "，先补齐再让 Agent 修改。")
            return
        if not main.ai_ready():
            self.status_label.setText("AI 通道还没配置，请先到「设置」里填写模型。")
            return
        text = modcatalog.entry_instruction(entry, note)
        if not chat.ask(text):
            self.status_label.setText("Agent 正在运行，等这一轮结束再发修改需求。")
            return
        main.go_to(chat)
        main.status(
            f"已把「{entry.get('title') or entry.get('key')}」的修改需求发给 Agent。"
        )
        self.status_label.setText("已发送给 Agent，可以在「对话」里看进度。")
