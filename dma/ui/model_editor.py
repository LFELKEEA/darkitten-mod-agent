"""手动模型编辑器：选中立方体，修改位置、尺寸和贴图 UV，并写回模型文件。

支持三种来源：
- Blockbench ``.bbmodel``：保留原有旋转、外层、名字等字段；
- Bedrock / GeckoLib ``.geo.json``：写回原来的 bone；
- Java 模型 JSON：写回 ``elements``，只有 ``parent`` 的模型也能从零添加立方体。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import project as project_mod
from .paint_page import HUMANOID_BOXES, Box, ModelView
from .widgets import tiny

FORMAT_LABELS = {
    "bbmodel": "Blockbench (.bbmodel)",
    "geo": "Bedrock / GeckoLib (.geo.json)",
    "java": "Java 模型 JSON",
}


# --------------------------------------------------------------------------- #
# 数据
# --------------------------------------------------------------------------- #
@dataclass
class EditableCube:
    box: Box
    node: dict | None = None
    bone: dict | None = None
    base_uv: tuple[int, int] = (0, 0)


@dataclass
class ModelDocument:
    path: Path
    data: dict[str, Any]
    fmt: str
    cubes: list[EditableCube]


def _floats(value: Any, count: int, default: tuple[float, ...]) -> list[float]:
    result: list[float] = []
    for index in range(count):
        try:
            result.append(float(value[index]))
        except (TypeError, ValueError, IndexError, KeyError):
            result.append(float(default[index]))
    return result


def _ints(value: Any, count: int, default: tuple[int, ...]) -> list[int]:
    result: list[int] = []
    for index in range(count):
        try:
            result.append(int(round(float(value[index]))))
        except (TypeError, ValueError, IndexError, KeyError):
            result.append(int(default[index]))
    return result


def _element_uv(element: dict) -> tuple[int, int]:
    values = _ints(element.get("uv"), 2, (0, 0))
    if values != [0, 0] or isinstance(element.get("uv"), list):
        return values[0], values[1]
    if isinstance(element.get("uv_offset"), list):
        values = _ints(element.get("uv_offset"), 2, (0, 0))
        return values[0], values[1]
    faces = element.get("faces") if isinstance(element.get("faces"), dict) else {}
    candidates = [
        face.get("uv")
        for face in faces.values()
        if isinstance(face, dict) and isinstance(face.get("uv"), list)
    ]
    if candidates:
        return (
            min(_ints(item, 2, (0, 0))[0] for item in candidates),
            min(_ints(item, 2, (0, 0))[1] for item in candidates),
        )
    return 0, 0


def detect_format(path: Path, data: dict) -> str:
    if path.suffix.lower() == ".bbmodel":
        return "bbmodel"
    if path.name.lower().endswith(".geo.json") or "minecraft:geometry" in data:
        return "geo"
    if isinstance(data.get("elements"), list) or "parent" in data:
        return "java"
    raise ValueError("只支持 .bbmodel、.geo.json 或带 elements/parent 的 Java 模型")


def load_document(path: Path) -> ModelDocument:
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("模型文件不是 JSON 对象。")
    fmt = detect_format(source, data)
    cubes: list[EditableCube] = []

    if fmt == "geo":
        geometries = data.get("minecraft:geometry")
        if not isinstance(geometries, list):
            raise ValueError("这个 .geo.json 里没有 minecraft:geometry。")
        for geometry in geometries:
            if not isinstance(geometry, dict):
                continue
            bones = geometry.get("bones")
            if not isinstance(bones, list):
                continue
            for bone in bones:
                if not isinstance(bone, dict):
                    continue
                entries = bone.get("cubes")
                if not isinstance(entries, list):
                    continue
                for cube in entries:
                    if not isinstance(cube, dict):
                        continue
                    origin = _floats(cube.get("origin"), 3, (0, 0, 0))
                    size = _floats(cube.get("size"), 3, (1, 1, 1))
                    uv = _ints(cube.get("uv"), 2, (0, 0))
                    box = Box(
                        str(bone.get("name") or "cube"),
                        (origin[0], origin[1], origin[2]),
                        (size[0], size[1], size[2]),
                        (uv[0], uv[1]),
                    )
                    cubes.append(
                        EditableCube(box, cube, bone, (uv[0], uv[1]))
                    )
        return ModelDocument(source, data, fmt, cubes)

    elements = data.get("elements")
    if not isinstance(elements, list):
        elements = []
        data["elements"] = elements
    for index, element in enumerate(elements):
        if not isinstance(element, dict):
            continue
        if str(element.get("type") or "cube").lower() != "cube":
            continue
        origin = _floats(element.get("from"), 3, (0, 0, 0))
        end = _floats(element.get("to"), 3, (16, 16, 16))
        size = [end[i] - origin[i] for i in range(3)]
        uv = _element_uv(element)
        box = Box(
            str(element.get("name") or f"cube{index + 1}"),
            (origin[0], origin[1], origin[2]),
            (size[0], size[1], size[2]),
            uv,
        )
        cubes.append(EditableCube(box, element, None, uv))
    return ModelDocument(source, data, fmt, cubes)


# --------------------------------------------------------------------------- #
# 保存
# --------------------------------------------------------------------------- #
def _apply_bb_node(node: dict, cube: EditableCube) -> None:
    box = cube.box
    node.setdefault("type", "cube")
    node["from"] = [float(box.origin[0]), float(box.origin[1]), float(box.origin[2])]
    node["to"] = [
        float(box.origin[0] + box.w),
        float(box.origin[1] + box.h),
        float(box.origin[2] + box.d),
    ]
    if box.name:
        node["name"] = box.name
    if isinstance(node.get("uv"), list):
        node["uv"] = [int(box.u), int(box.v)]
    if node.get("box_uv") is not False:
        node["box_uv"] = True
        node["uv_offset"] = [int(box.u), int(box.v)]
        return
    # 逐面 UV：按用户修改的 U/V 统一平移，保留原来的面映射关系。
    delta_u = int(box.u) - cube.base_uv[0]
    delta_v = int(box.v) - cube.base_uv[1]
    faces = node.get("faces")
    if isinstance(faces, dict):
        for face in faces.values():
            if not isinstance(face, dict):
                continue
            values = face.get("uv")
            if isinstance(values, list) and len(values) >= 4:
                face["uv"] = [
                    _ints(values, 4, (0, 0, 0, 0))[0] + delta_u,
                    _ints(values, 4, (0, 0, 0, 0))[1] + delta_v,
                    _ints(values, 4, (0, 0, 0, 0))[2] + delta_u,
                    _ints(values, 4, (0, 0, 0, 0))[3] + delta_v,
                ]
    cube.base_uv = (int(box.u), int(box.v))


def _apply_java_node(node: dict, cube: EditableCube) -> None:
    box = cube.box
    node["from"] = [float(box.origin[0]), float(box.origin[1]), float(box.origin[2])]
    node["to"] = [
        float(box.origin[0] + box.w),
        float(box.origin[1] + box.h),
        float(box.origin[2] + box.d),
    ]
    if isinstance(node.get("uv"), list):
        node["uv"] = [int(box.u), int(box.v)]
    if isinstance(node.get("uv_offset"), list):
        node["uv_offset"] = [int(box.u), int(box.v)]
    faces = node.get("faces")
    if not isinstance(faces, dict) or not faces:
        # 方块/物品贴图通常只有 16x16，新立方体默认每面取整块贴图，
        # 比按 64x64 图集展开的 box UV 更不容易采样到空白区域。
        u, v = int(box.u), int(box.v)
        node["faces"] = {
            face: {"uv": [u, v, u + 16, v + 16]}
            for face in ("north", "east", "south", "west", "up", "down")
        }
        node["uv_offset"] = [u, v]
        cube.base_uv = (u, v)
        return
    if isinstance(node.get("uv"), list):
        cube.base_uv = (int(box.u), int(box.v))
        return
    delta_u = int(box.u) - cube.base_uv[0]
    delta_v = int(box.v) - cube.base_uv[1]
    if delta_u or delta_v:
        for face in faces.values():
            if not isinstance(face, dict):
                continue
            values = face.get("uv")
            if isinstance(values, list) and len(values) >= 4:
                values = _ints(values, 4, (0, 0, 0, 0))
                face["uv"] = [
                    values[0] + delta_u,
                    values[1] + delta_v,
                    values[2] + delta_u,
                    values[3] + delta_v,
                ]
    cube.base_uv = (int(box.u), int(box.v))


def _save_geo(doc: ModelDocument, cubes: list[EditableCube]) -> None:
    geometries = doc.data.get("minecraft:geometry")
    if not isinstance(geometries, list) or not geometries:
        geometries = [
            {
                "description": {"identifier": "geometry.model"},
                "bones": [{"name": "model", "cubes": []}],
            }
        ]
        doc.data["minecraft:geometry"] = geometries
    all_bones: list[dict] = []
    first_bone: dict | None = None
    for geometry in geometries:
        if not isinstance(geometry, dict):
            continue
        bones = geometry.get("bones")
        if not isinstance(bones, list):
            bones = []
            geometry["bones"] = bones
        for bone in bones:
            if not isinstance(bone, dict):
                continue
            bone["cubes"] = []
            all_bones.append(bone)
            if first_bone is None:
                first_bone = bone
    if first_bone is None:
        first_geometry = next(
            (item for item in geometries if isinstance(item, dict)), None
        )
        if first_geometry is None:
            first_geometry = {}
            geometries.append(first_geometry)
        first_bone = {"name": "model", "cubes": []}
        first_geometry.setdefault("bones", []).append(first_bone)
        all_bones.append(first_bone)
    for cube in cubes:
        bone = cube.bone if any(cube.bone is item for item in all_bones) else first_bone
        node = cube.node if isinstance(cube.node, dict) else {}
        box = cube.box
        node["origin"] = [
            float(box.origin[0]),
            float(box.origin[1]),
            float(box.origin[2]),
        ]
        node["size"] = [float(box.w), float(box.h), float(box.d)]
        node["uv"] = [int(box.u), int(box.v)]
        bone.setdefault("cubes", []).append(node)


def save_document(
    doc: ModelDocument,
    cubes: list[EditableCube],
    *,
    root: Path | None = None,
    relative: str | None = None,
) -> None:
    if doc.fmt == "geo":
        _save_geo(doc, cubes)
    elif doc.fmt == "bbmodel":
        others = [
            item
            for item in doc.data.get("elements", [])
            if isinstance(item, dict)
            and str(item.get("type") or "cube").lower() != "cube"
        ]
        nodes: list[dict] = []
        for cube in cubes:
            node = cube.node if isinstance(cube.node, dict) else {}
            _apply_bb_node(node, cube)
            cube.node = node
            nodes.append(node)
        doc.data["elements"] = others + nodes
    else:
        nodes = []
        for cube in cubes:
            node = cube.node if isinstance(cube.node, dict) else {}
            _apply_java_node(node, cube)
            cube.node = node
            nodes.append(node)
        doc.data["elements"] = nodes

    text = json.dumps(doc.data, ensure_ascii=False, indent=2) + "\n"
    if root is not None and relative:
        project_mod.write_text(Path(root), relative, text)
    else:
        doc.path.write_text(text, encoding="utf-8")


def starter_relative(kind: str, modid: str, key: str) -> str:
    """没有模型时，新建文件的默认位置。"""
    if kind == "entity":
        return f"src/main/resources/assets/{modid}/geo/{key}.bbmodel"
    folder = "block" if kind == "block" else "item"
    return f"src/main/resources/assets/{modid}/models/{folder}/{key}.json"


def create_starter_model(
    root: Path,
    relative: str,
    *,
    kind: str,
    name: str,
    modid: str,
    key: str,
) -> None:
    """创建一个可以直接手动编辑的起始模型。

    实体使用人形 .bbmodel，方块/物品使用带立方体元素的 Java 模型，
    这样新建后立刻能在模型库里看到、在编辑器里改。
    """
    if kind == "entity":
        elements = []
        for box in HUMANOID_BOXES:
            elements.append(
                {
                    "name": box.name,
                    "type": "cube",
                    "from": [float(box.x), float(box.y), float(box.z)],
                    "to": [
                        float(box.x + box.w),
                        float(box.y + box.h),
                        float(box.z + box.d),
                    ],
                    "box_uv": True,
                    "uv_offset": [int(box.u), int(box.v)],
                }
            )
        data: dict[str, Any] = {
            "meta": {"format_version": "4.5", "model_format": "modded_entity"},
            "name": name,
            "elements": elements,
            "textures": [],
        }
    else:
        folder = "block" if kind == "block" else "item"
        texture = f"{modid}:{folder}/{key}"
        data = {
            "credit": "由天禧模型库创建",
            "textures": {"particle": texture, "all": texture},
            "elements": [
                {
                    "from": [0.0, 0.0, 0.0],
                    "to": [16.0, 16.0, 16.0],
                    "faces": {
                        face: {"uv": [0, 0, 16, 16], "texture": "#all"}
                        for face in ("north", "east", "south", "west", "up", "down")
                    },
                }
            ],
        }
    project_mod.write_text(
        Path(root),
        relative,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
    )


# --------------------------------------------------------------------------- #
# 编辑器
# --------------------------------------------------------------------------- #
def _placeholder_texture(size: int = 64) -> QImage:
    image = QImage(size, size, QImage.Format_ARGB32)
    painter = QPainter(image)
    cell = max(4, size // 8)
    for y in range(0, size, cell):
        for x in range(0, size, cell):
            dark = ((x // cell) + (y // cell)) % 2 == 0
            painter.fillRect(x, y, cell, cell, QColor("#5a6070" if dark else "#7a8090"))
    painter.setPen(QPen(QColor("#2a2f3e"), 1))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return image


class ModelEditorDialog(QDialog):
    """立方体级别的模型编辑器，保存时写回原文件并自动备份。"""

    def __init__(
        self,
        root: Path,
        relative: str,
        texture: QImage | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._root = Path(root)
        self._relative = str(relative)
        self._path = (self._root / self._relative).resolve()
        self._doc = load_document(self._path)
        self._cubes = self._doc.cubes
        self._current = -1
        self._syncing = False
        self.saved = False

        self.setWindowTitle(f"手动编辑模型 · {self._path.name}")
        self.resize(1180, 760)
        self._build_ui(texture or _placeholder_texture())
        self._refresh_list(0 if self._cubes else -1)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def document(self) -> ModelDocument:
        return self._doc

    # -- 构建界面 --------------------------------------------------------
    def _build_ui(self, texture: QImage) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel(self._path.name)
        title.setObjectName("H2")
        head.addWidget(title)
        head.addWidget(tiny(FORMAT_LABELS[self._doc.fmt]))
        head.addStretch(1)
        self.back_box = QCheckBox("查看背面")
        head.addWidget(self.back_box)
        layout.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(10)
        layout.addLayout(body, 1)

        self.view = ModelView()
        self.view.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.view.set_kind("custom")
        self.view.set_image(texture)
        self.view.set_boxes([cube.box for cube in self._cubes])
        self.view.set_select_mode(True)
        self.view.boxSelected.connect(self._select_index)
        self.back_box.toggled.connect(self.view.set_back_view)
        body.addWidget(self.view, 1)

        panel = QWidget()
        panel.setFixedWidth(370)
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(7)
        body.addWidget(panel)

        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        self.list.currentRowChanged.connect(self._select_index)
        panel_layout.addWidget(QLabel("立方体"))
        panel_layout.addWidget(self.list, 1)

        tools = QHBoxLayout()
        self.add_btn = QPushButton("添加")
        self.add_btn.setObjectName("Primary")
        self.add_btn.clicked.connect(self._add_cube)
        self.copy_btn = QPushButton("复制")
        self.copy_btn.setObjectName("Ghost")
        self.copy_btn.clicked.connect(self._duplicate_cube)
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setObjectName("Danger")
        self.delete_btn.clicked.connect(self._delete_cube)
        tools.addWidget(self.add_btn)
        tools.addWidget(self.copy_btn)
        tools.addWidget(self.delete_btn)
        tools.addStretch(1)
        panel_layout.addLayout(tools)

        order = QHBoxLayout()
        self.up_btn = QPushButton("上移")
        self.up_btn.setObjectName("Ghost")
        self.up_btn.clicked.connect(lambda: self._move_cube(-1))
        self.down_btn = QPushButton("下移")
        self.down_btn.setObjectName("Ghost")
        self.down_btn.clicked.connect(lambda: self._move_cube(1))
        order.addWidget(self.up_btn)
        order.addWidget(self.down_btn)
        order.addStretch(1)
        panel_layout.addLayout(order)

        self.fields = QFrame()
        self.fields.setObjectName("CardSoft")
        self.fields.setAttribute(Qt.WA_StyledBackground, True)
        form = QFormLayout(self.fields)
        form.setContentsMargins(9, 9, 9, 9)
        form.setSpacing(6)

        self.name_edit = QLineEdit()
        self.name_edit.textEdited.connect(self._apply_fields)
        form.addRow("名称", self.name_edit)

        self.x_spin = self._float_spin(-512, 512)
        self.y_spin = self._float_spin(-512, 512)
        self.z_spin = self._float_spin(-512, 512)
        form.addRow("位置 X / Y / Z", self._spin_row(self.x_spin, self.y_spin, self.z_spin))

        self.w_spin = self._float_spin(0.01, 512)
        self.h_spin = self._float_spin(0.01, 512)
        self.d_spin = self._float_spin(0.01, 512)
        form.addRow("尺寸 宽 / 高 / 深", self._spin_row(self.w_spin, self.h_spin, self.d_spin))

        self.u_spin = QSpinBox()
        self.u_spin.setRange(0, 4096)
        self.u_spin.valueChanged.connect(self._apply_fields)
        self.v_spin = QSpinBox()
        self.v_spin.setRange(0, 4096)
        self.v_spin.valueChanged.connect(self._apply_fields)
        form.addRow("贴图 U / V", self._spin_row(self.u_spin, self.v_spin))

        for spin in (self.x_spin, self.y_spin, self.z_spin,
                     self.w_spin, self.h_spin, self.d_spin):
            spin.valueChanged.connect(self._apply_fields)
        panel_layout.addWidget(self.fields)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        info = QWidget()
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 0)
        hints = [
            "点击左侧 3D 视图里的立方体可以选中它。",
            "坐标单位是像素：16 = 1 格；位置指立方体最小角。",
            "修改 U/V 会同步移动贴图上的面；逐面 UV 模型会整体平移。",
        ]
        if self._doc.fmt == "geo":
            hints.append("Bedrock 立方体没有独立名称，名称跟随所属 bone。")
        if self._doc.fmt == "java" and not self._doc.cubes:
            hints.append("这个模型只有 parent，保存后会写入本地 elements，覆盖父模型的外观。")
        for text in hints:
            label = tiny(text)
            label.setWordWrap(True)
            info_layout.addWidget(label)
        info_layout.addStretch(1)
        scroll.setWidget(info)
        panel_layout.addWidget(scroll, 1)
        self._update_actions()

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Ghost")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存模型")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        footer.addWidget(cancel)
        footer.addWidget(save)
        layout.addLayout(footer)

    @staticmethod
    def _float_spin(low: float, high: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(2)
        spin.setSingleStep(0.5)
        spin.setFixedWidth(74)
        return spin

    @staticmethod
    def _spin_row(*widgets: QWidget) -> QWidget:
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for widget in widgets:
            row.addWidget(widget)
        return holder

    # -- 列表和字段 ------------------------------------------------------
    def _refresh_list(self, select: int) -> None:
        self._syncing = True
        self.list.clear()
        for index, cube in enumerate(self._cubes):
            box = cube.box
            item = QListWidgetItem(
                f"{index + 1}. {box.name}  ({box.w:g} × {box.h:g} × {box.d:g})"
            )
            self.list.addItem(item)
        if self._cubes:
            select = max(0, min(len(self._cubes) - 1, select))
        else:
            select = -1
        self.list.setCurrentRow(select)
        self._syncing = False
        self.view.set_boxes([cube.box for cube in self._cubes])
        self._select_index(select)

    def _select_index(self, index: int) -> None:
        if self._syncing:
            return
        if index < 0 or index >= len(self._cubes):
            self._current = -1
            self.fields.setEnabled(False)
            self.view.set_selected_box(-1)
            self._update_actions()
            return
        self._current = index
        self.fields.setEnabled(True)
        self.name_edit.setEnabled(self._doc.fmt == "bbmodel")
        self.name_edit.setToolTip(
            "只有 .bbmodel 有独立立方体名称。"
            if self._doc.fmt != "bbmodel"
            else ""
        )
        cube = self._cubes[index]
        box = cube.box
        self._syncing = True
        self.name_edit.setText(box.name)
        self.x_spin.setValue(box.origin[0])
        self.y_spin.setValue(box.origin[1])
        self.z_spin.setValue(box.origin[2])
        self.w_spin.setValue(box.w)
        self.h_spin.setValue(box.h)
        self.d_spin.setValue(box.d)
        self.u_spin.setValue(box.u)
        self.v_spin.setValue(box.v)
        self._syncing = False
        if self.list.currentRow() != index:
            self.list.setCurrentRow(index)
        self.view.set_selected_box(index)
        self._update_actions()

    def _apply_fields(self, *_args) -> None:
        if self._syncing or self._current < 0:
            return
        cube = self._cubes[self._current]
        box = cube.box
        if self._doc.fmt == "bbmodel":
            box.name = self.name_edit.text().strip() or box.name
        box.origin = (
            self.x_spin.value(),
            self.y_spin.value(),
            self.z_spin.value(),
        )
        box.size = (
            max(0.01, self.w_spin.value()),
            max(0.01, self.h_spin.value()),
            max(0.01, self.d_spin.value()),
        )
        box.uv = (self.u_spin.value(), self.v_spin.value())
        item = self.list.item(self._current)
        if item is not None:
            item.setText(
                f"{self._current + 1}. {box.name}  "
                f"({box.w:g} × {box.h:g} × {box.d:g})"
            )
        self.view.set_boxes([item.box for item in self._cubes])
        self.view.set_selected_box(self._current)

    # -- 编辑动作 --------------------------------------------------------
    def _add_cube(self) -> None:
        base = self._cubes[self._current] if 0 <= self._current < len(self._cubes) else None
        if base is not None:
            origin = base.box.origin
            uv = base.box.uv
        else:
            origin = (0.0, 0.0, 0.0)
            uv = (0, 0)
        cube = EditableCube(
            Box("cube", origin, (8.0, 8.0, 8.0), uv),
            None,
            base.bone if base is not None else None,
            uv,
        )
        self._cubes.append(cube)
        self._refresh_list(len(self._cubes) - 1)

    def _duplicate_cube(self) -> None:
        if self._current < 0:
            return
        source = self._cubes[self._current]
        box = source.box
        cube = EditableCube(
            Box(
                f"{box.name}_copy",
                (box.x + 2.0, box.y, box.z),
                box.size,
                box.uv,
            ),
            None,
            source.bone,
            box.uv,
        )
        self._cubes.insert(self._current + 1, cube)
        self._refresh_list(self._current + 1)

    def _delete_cube(self) -> None:
        if self._current < 0:
            return
        index = self._current
        del self._cubes[index]
        self._refresh_list(min(index, len(self._cubes) - 1))

    def _move_cube(self, delta: int) -> None:
        if self._current < 0:
            return
        target = self._current + delta
        if target < 0 or target >= len(self._cubes):
            return
        self._cubes[self._current], self._cubes[target] = (
            self._cubes[target],
            self._cubes[self._current],
        )
        self._refresh_list(target)

    def _update_actions(self) -> None:
        has_selection = 0 <= self._current < len(self._cubes)
        self.copy_btn.setEnabled(has_selection)
        self.delete_btn.setEnabled(has_selection)
        self.up_btn.setEnabled(has_selection and self._current > 0)
        self.down_btn.setEnabled(
            has_selection and self._current < len(self._cubes) - 1
        )

    # -- 保存 ------------------------------------------------------------
    def _save(self) -> None:
        for index, cube in enumerate(self._cubes):
            if cube.box.w <= 0 or cube.box.h <= 0 or cube.box.d <= 0:
                QMessageBox.warning(
                    self,
                    "尺寸无效",
                    f"第 {index + 1} 个立方体的宽、高、深都必须大于 0。",
                )
                return
        try:
            save_document(
                self._doc,
                self._cubes,
                root=self._root,
                relative=self._relative,
            )
        except Exception as exc:  # noqa: BLE001 - 保存失败要完整反馈
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.saved = True
        self.accept()