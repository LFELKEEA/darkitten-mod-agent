"""手动绘图页：像素画布 + UV 图集 + 模型/方块 3D 预览。

参考 Blockbench 的 Paint 模式：
- 可以直接在 UV 图集上画，也可以点 3D 预览里的模型面反查 UV 后落笔；
- 工具栏提供铅笔、橡皮、油漆桶、取色、直线，支持画笔大小、撤销/重做；
- 实体自带原版 64x64 人形 UV 分区提示，也能加载 .geo.json / 模型 JSON；
- 方块预览是一个等距立方体，三个可见面都能直接上色。
"""

from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QImage,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .theme import COLORS
from .widgets import card, tiny

TRANSPARENT = QColor(0, 0, 0, 0)
TOOLS = (
    ("pencil", "铅笔", "P"),
    ("eraser", "橡皮", "E"),
    ("fill", "油漆桶", "G"),
    ("picker", "取色", "I"),
    ("line", "直线", "L"),
)
_PALETTE = [
    "#e8eaf2", "#9aa0b5", "#4a4f66", "#1b1b24",
    "#ef6b7a", "#e0b45c", "#8fd46a", "#4ec9a4",
    "#5ac8e0", "#5a8cff", "#8b7cff", "#d98cff",
    "#8a5a3c", "#c8a06a", "#f2d8a8", "#ffffff",
]
# Extended swatches: neutral ramps, then warm / green / cyan / blue / violet /
# earth columns.  Enough range to shade any mob or block without opening the
# colour dialog every other pixel.
_PALETTE_EXTRA = [
    "#000000", "#26262e", "#3d3d4a", "#5d5d6e", "#6f6f80", "#8a8a9a", "#b8b8c4", "#d8d8e0",
    "#5a2a2a", "#8a3a3a", "#c04a4a", "#e87070", "#8a4a1a", "#c46a2a", "#e89a4a", "#f2c27a",
    "#2a4a2a", "#3a6a3a", "#4f8f4a", "#76b85a", "#a8d06a", "#d0e08a", "#1a4a4a", "#2a6a6a",
    "#2a4a7a", "#3a6aa8", "#4a8ad0", "#6ab0e8", "#4a2a6a", "#6a3a9a", "#8a5ac0", "#b08ae0",
    "#6a2a4a", "#9a3a6a", "#c05a9a", "#d88ac0", "#4a3a2a", "#6a5a3a", "#8a7a5a", "#b0a080",
    "#5a3a2a", "#7a5a3a", "#9a7a4a", "#e8d0a0", "#f2e8d0", "#2a3a2a", "#3a4a3a", "#4a5a4a",
]
_RECENT_SLOTS = 12


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
@dataclass
class Box:
    """一个 Minecraft 立方体：origin 为最小角，size 为边长，uv 为图集起点。"""

    name: str
    origin: tuple[float, float, float]
    size: tuple[float, float, float]
    uv: tuple[int, int]
    faces: dict[str, QRect] | None = None
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    pivot: tuple[float, float, float] | None = None
    inflate: float = 0.0

    @property
    def x(self) -> float:
        return self.origin[0]

    @property
    def y(self) -> float:
        return self.origin[1]

    @property
    def z(self) -> float:
        return self.origin[2]

    @property
    def w(self) -> float:
        return self.size[0]

    @property
    def h(self) -> float:
        return self.size[1]

    @property
    def d(self) -> float:
        return self.size[2]

    @property
    def u(self) -> int:
        return self.uv[0]

    @property
    def v(self) -> int:
        return self.uv[1]

    def center(self) -> tuple[float, float, float]:
        return (
            self.origin[0] + self.size[0] / 2.0,
            self.origin[1] + self.size[1] / 2.0,
            self.origin[2] + self.size[2] / 2.0,
        )

    def rotation_pivot(self) -> tuple[float, float, float]:
        return self.pivot if self.pivot is not None else self.center()


def rotate_point(
    point: tuple[float, float, float],
    pivot: tuple[float, float, float],
    rotation: tuple[float, float, float],
) -> tuple[float, float, float]:
    """Rotate a world point around ``pivot`` with Blockbench XYZ order."""
    if not any(abs(float(value)) > 1e-6 for value in rotation):
        return point
    cx, cy, cz = (float(value) for value in pivot)
    rx, ry, rz = (math.radians(float(value)) for value in rotation)
    ax, sa = math.cos(rx), math.sin(rx)
    ay, sy = math.cos(ry), math.sin(ry)
    az, sz = math.cos(rz), math.sin(rz)
    x, y, z = point[0] - cx, point[1] - cy, point[2] - cz
    y, z = y * ax - z * sa, y * sa + z * ax
    x, z = x * ay + z * sy, -x * sy + z * ay
    x, y = x * az - y * sz, x * sz + y * az
    return x + cx, y + cy, z + cz


def box_corner_points(box: Box) -> list[tuple[float, float, float]]:
    """Eight world-space corners after inflate + rotation."""
    inflate = float(box.inflate or 0.0)
    x0, y0, z0 = box.x - inflate, box.y - inflate, box.z - inflate
    x1 = box.x + box.w + inflate
    y1 = box.y + box.h + inflate
    z1 = box.z + box.d + inflate
    pivot = box.rotation_pivot()
    rotation = box.rotation or (0.0, 0.0, 0.0)
    return [
        rotate_point((x, y, z), pivot, rotation)
        for x in (x0, x1)
        for y in (y0, y1)
        for z in (z0, z1)
    ]


HUMANOID_BOXES = [
    Box("头", (-4, 24, -4), (8, 8, 8), (0, 0)),
    Box("帽/发", (-4, 24, -4), (8, 8, 8), (32, 0)),
    Box("身体", (-4, 12, -2), (8, 12, 4), (16, 16)),
    Box("右臂", (-8, 12, -2), (4, 12, 4), (40, 16)),
    Box("左臂", (4, 12, -2), (4, 12, 4), (32, 48)),
    Box("右腿", (-4, 0, -2), (4, 12, 4), (0, 16)),
    Box("左腿", (0, 0, -2), (4, 12, 4), (16, 48)),
    Box("外套", (-4, 12, -2), (8, 12, 4), (16, 32)),
    Box("右袖", (-8, 12, -2), (4, 12, 4), (40, 32)),
    Box("左袖", (4, 12, -2), (4, 12, 4), (48, 48)),
    Box("右裤", (-4, 0, -2), (4, 12, 4), (0, 32)),
    Box("左裤", (0, 0, -2), (4, 12, 4), (0, 48)),
]

# UV 分区提示：(名称, 矩形, 颜色)
HUMANOID_GUIDES = [
    ("头", QRect(0, 0, 32, 16), "#8b7cff"),
    ("帽", QRect(32, 0, 32, 16), "#d98cff"),
    ("身体", QRect(16, 16, 24, 16), "#4ec9a4"),
    ("右臂", QRect(40, 16, 16, 16), "#5ac8e0"),
    ("右腿", QRect(0, 16, 16, 16), "#5a8cff"),
    ("外套", QRect(16, 32, 24, 16), "#e0b45c"),
    ("右袖", QRect(40, 32, 16, 16), "#e0b45c"),
    ("右裤", QRect(0, 32, 16, 16), "#e0b45c"),
    ("左腿", QRect(16, 48, 16, 16), "#5a8cff"),
    ("左臂", QRect(32, 48, 16, 16), "#5ac8e0"),
    ("左裤", QRect(0, 48, 16, 16), "#e0b45c"),
    ("左袖", QRect(48, 48, 16, 16), "#e0b45c"),
]


def _blank_image(width: int, height: int) -> QImage:
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(TRANSPARENT)
    return image


def _brush_points(points: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    return [(x, y) for x, y in points]


def _line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    error = dx - dy
    x, y = x0, y0
    result = [(x, y)]
    guard = 0
    while (x != x1 or y != y1) and guard < 4096:
        guard += 1
        double = error * 2
        if double > -dy:
            error -= dy
            x += sx
        if double < dx:
            error += dx
            y += sy
        result.append((x, y))
    return result


_OVERLAY_NAMES = {"帽/发", "外套", "右袖", "左袖", "右裤", "左裤"}

# 原版人形左右肢体的 UV 配对，镜像绘制时使用。
_MIRROR_NAMES = {
    "右臂": "左臂",
    "左臂": "右臂",
    "右腿": "左腿",
    "左腿": "右腿",
    "右袖": "左袖",
    "左袖": "右袖",
    "右裤": "左裤",
    "左裤": "右裤",
}


def _is_overlay(name: str) -> bool:
    text = str(name or "").strip().lower()
    if str(name or "").strip() in _OVERLAY_NAMES:
        return True
    return any(word in text for word in ("overlay", "hat", "jacket", "sleeve", "pants"))


def infer_kind(path: str | Path) -> str:
    text = str(path).replace("\\", "/").lower()
    if "/textures/entity/" in text or "/geo/" in text:
        return "entity"
    if "/textures/block/" in text:
        return "block"
    return "item"


# ---------------------------------------------------------------------------
# 像素画布
# ---------------------------------------------------------------------------
class PixelCanvas(QWidget):
    edited = Signal()
    colorPicked = Signal(str)
    cursorMoved = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = _blank_image(16, 16)
        self._dirty = False
        self._zoom = 18
        self._tool = "pencil"
        self._color = QColor("#e8eaf2")
        self._brush = 1
        self._show_grid = True
        self._guides: list[tuple[str, QRect, QColor]] = []
        self._mirror: Callable[[int, int], tuple[int, int] | None] | None = None
        self._undo: list[QImage] = []
        self._redo: list[QImage] = []
        self._drawing = False
        self._stroke_open = False
        self._last: tuple[int, int] | None = None
        self._line_start: tuple[int, int] | None = None
        self._line_preview: list[tuple[int, int]] = []
        self._hover: tuple[int, int] | None = None
        self._clip: QRect | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- 文档 ------------------------------------------------------------
    def image(self) -> QImage:
        return self._image

    def set_image(self, image: QImage, *, reset_history: bool = True) -> None:
        self._image = image.convertToFormat(QImage.Format_ARGB32)
        self._dirty = False
        self._undo.clear()
        self._redo.clear()
        self._hover = None
        self.fit()
        self.update()

    def load(self, path: str | Path) -> None:
        image = QImage(str(path))
        if image.isNull():
            raise ValueError(f"无法读取贴图：{path}")
        self.set_image(image)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not self._image.save(str(target), "PNG"):
            raise OSError(f"保存失败：{target}")
        self._dirty = False

    def is_dirty(self) -> bool:
        return self._dirty

    def size_px(self) -> tuple[int, int]:
        return self._image.width(), self._image.height()

    # -- 状态 ------------------------------------------------------------
    def set_tool(self, tool: str) -> None:
        self._tool = tool
        self._line_preview.clear()
        self.update()

    def tool(self) -> str:
        return self._tool

    def set_color(self, color: str | QColor) -> None:
        value = QColor(color)
        if value.isValid():
            self._color = value

    def color(self) -> QColor:
        return QColor(self._color)

    def set_brush(self, size: int) -> None:
        self._brush = max(1, min(16, int(size)))

    def brush(self) -> int:
        return self._brush

    def set_grid(self, show: bool) -> None:
        self._show_grid = bool(show)
        self.update()

    def set_guides(self, guides: list[tuple[str, QRect, QColor]]) -> None:
        self._guides = list(guides)
        self.update()

    def set_mirror(self, mirror: Callable[[int, int], tuple[int, int] | None] | None) -> None:
        """镜像落笔：返回另一个应同步上色的像素坐标，None 表示不镜像。"""
        self._mirror = mirror

    def set_zoom(self, zoom: int) -> None:
        self._zoom = max(1, min(64, int(zoom)))
        self.update()

    def zoom(self) -> int:
        return self._zoom

    def fit(self) -> None:
        width, height = self.size_px()
        avail_w = max(80, self.width() - 24)
        avail_h = max(80, self.height() - 24)
        self._zoom = max(1, min(40, min(avail_w // max(1, width), avail_h // max(1, height))))
        self.update()

    # -- 撤销 ------------------------------------------------------------
    def begin_stroke(self) -> None:
        if not self._stroke_open:
            self._push_undo()
            self._stroke_open = True

    def end_stroke(self) -> None:
        self._stroke_open = False
        self._last = None

    def _push_undo(self) -> None:
        self._undo.append(self._image.copy())
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self) -> None:
        if not self._undo:
            return
        self._redo.append(self._image.copy())
        self._image = self._undo.pop()
        self._hover = None
        self._dirty = True
        self.edited.emit()
        self.update()

    def redo(self) -> None:
        if not self._redo:
            return
        self._undo.append(self._image.copy())
        self._image = self._redo.pop()
        self._hover = None
        self._dirty = True
        self.edited.emit()
        self.update()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    # -- 绘制 ------------------------------------------------------------
    def paint_pixel(self, x: int, y: int, *, erase: bool = False, clip: QRect | None = None,
                    push: bool = False) -> None:
        if push:
            self.begin_stroke()
        target = TRANSPARENT if erase or self._tool == "eraser" else self._color
        clip = clip or self._clip
        half = (self._brush - 1) // 2
        changed = False
        points: list[tuple[int, int]] = []
        for oy in range(-half, half + 1):
            for ox in range(-half, half + 1):
                px, py = x + ox, y + oy
                if not self._inside(px, py):
                    continue
                if clip is not None and not clip.contains(px, py):
                    continue
                points.append((px, py))
        if self._mirror is not None:
            for px, py in list(points):
                mapped = self._mirror(px, py)
                if mapped is not None and self._inside(*mapped):
                    points.append(mapped)
        for px, py in points:
            current = self._image.pixelColor(px, py)
            if current.rgba() != target.rgba():
                self._image.setPixelColor(px, py, target)
                changed = True
        if changed:
            self._dirty = True
            self.edited.emit()
            self.update()

    def _inside(self, x: int, y: int) -> bool:
        return 0 <= x < self._image.width() and 0 <= y < self._image.height()

    def _paint_at(self, x: int, y: int) -> None:
        if not self._inside(x, y):
            return
        if self._tool == "fill":
            self._push_undo()
            self._flood_fill(x, y)
        else:
            self.paint_pixel(x, y, erase=self._tool == "eraser")

    def _flood_fill(self, x: int, y: int) -> None:
        target = self._image.pixelColor(x, y)
        fill = TRANSPARENT if self._tool == "eraser" else self._color
        if target.rgba() == fill.rgba():
            return
        width, height = self._image.width(), self._image.height()
        stack = [(x, y)]
        seen: set[tuple[int, int]] = set()
        while stack:
            cx, cy = stack.pop()
            if (cx, cy) in seen or not (0 <= cx < width and 0 <= cy < height):
                continue
            seen.add((cx, cy))
            if self._image.pixelColor(cx, cy).rgba() != target.rgba():
                continue
            self._image.setPixelColor(cx, cy, fill)
            stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))
        self._dirty = True
        self.edited.emit()
        self.update()

    def fill_rect(self, rect: QRect, *, erase: bool = False) -> None:
        """用当前颜色填满一个矩形（模型上的一个面）。"""
        self.fill_rects([rect], erase=erase)

    def fill_rects(self, rects: list[QRect], *, erase: bool = False) -> None:
        """一次填满多个面，只产生一步撤销。"""
        target = TRANSPARENT if erase or self._tool == "eraser" else self._color
        self.begin_stroke()
        changed = False
        for rect in rects:
            for y in range(rect.top(), rect.bottom() + 1):
                for x in range(rect.left(), rect.right() + 1):
                    if not self._inside(x, y):
                        continue
                    if self._image.pixelColor(x, y).rgba() != target.rgba():
                        self._image.setPixelColor(x, y, target)
                        changed = True
                    if self._mirror is not None:
                        mapped = self._mirror(x, y)
                        if mapped is not None and self._inside(*mapped):
                            if self._image.pixelColor(*mapped).rgba() != target.rgba():
                                self._image.setPixelColor(mapped[0], mapped[1], target)
                                changed = True
        self.end_stroke()
        if changed:
            self._dirty = True
            self.edited.emit()
            self.update()

    def _pick(self, x: int, y: int) -> None:
        if not self._inside(x, y):
            return
        color = self._image.pixelColor(x, y)
        if color.alpha() == 0:
            value = "#00000000"
        else:
            value = color.name(QColor.HexArgb) if color.alpha() < 255 else color.name()
        self.colorPicked.emit(value)

    # -- 坐标 ------------------------------------------------------------
    def _image_rect(self) -> QRect:
        width = self._image.width() * self._zoom
        height = self._image.height() * self._zoom
        return QRect(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )

    def _to_pixel(self, pos: QPointF | QPoint) -> tuple[int, int] | None:
        rect = self._image_rect()
        x = int((pos.x() - rect.x()) // self._zoom)
        y = int((pos.y() - rect.y()) // self._zoom)
        if 0 <= x < self._image.width() and 0 <= y < self._image.height():
            return x, y
        return None

    # -- 事件 ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["bg"]))
        rect = self._image_rect()
        self._paint_checkerboard(painter, rect)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.drawImage(rect, self._image)

        if self._guides and self._zoom >= 4:
            painter.setRenderHint(QPainter.Antialiasing, False)
            for label, guide, color in self._guides:
                r = QRect(
                    rect.x() + guide.x() * self._zoom,
                    rect.y() + guide.y() * self._zoom,
                    guide.width() * self._zoom,
                    guide.height() * self._zoom,
                )
                pen = QPen(color, 1, Qt.DashLine)
                painter.setPen(pen)
                painter.drawRect(r.adjusted(0, 0, -1, -1))
                if self._zoom >= 6:
                    painter.setPen(QPen(color, 1))
                    painter.drawText(r.adjusted(3, 1, 0, 0), Qt.AlignLeft | Qt.AlignTop, label)

        if self._show_grid and self._zoom >= 8:
            painter.setPen(QPen(QColor(255, 255, 255, 34), 1))
            for x in range(self._image.width() + 1):
                px = rect.x() + x * self._zoom
                painter.drawLine(px, rect.top(), px, rect.bottom())
            for y in range(self._image.height() + 1):
                py = rect.y() + y * self._zoom
                painter.drawLine(rect.left(), py, rect.right(), py)

        if self._line_preview and self._line_start is not None:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self._color)
            for x, y in self._line_preview:
                painter.drawRect(
                    rect.x() + x * self._zoom,
                    rect.y() + y * self._zoom,
                    self._zoom,
                    self._zoom,
                )
            painter.setBrush(Qt.NoBrush)

        if self._hover is not None:
            hx, hy = self._hover
            painter.setPen(QPen(QColor(COLORS["accent"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(
                rect.x() + hx * self._zoom,
                rect.y() + hy * self._zoom,
                self._zoom,
                self._zoom,
            )

        painter.end()

    def _paint_checkerboard(self, painter: QPainter, rect: QRect) -> None:
        cell = max(4, self._zoom)
        painter.save()
        painter.setClipRect(rect)
        for y in range(rect.top(), rect.bottom() + 1, cell):
            for x in range(rect.left(), rect.right() + 1, cell):
                dark = ((x - rect.left()) // cell + (y - rect.top()) // cell) % 2 == 0
                painter.fillRect(
                    QRect(x, y, cell, cell),
                    QColor("#222633") if dark else QColor("#2a2f3e"),
                )
        painter.restore()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            point = self._to_pixel(event.position())
            if point is not None:
                self._pick(*point)
            return
        if event.button() != Qt.LeftButton:
            return
        point = self._to_pixel(event.position())
        if point is None:
            return
        self.setFocus()
        x, y = point
        if self._tool == "picker":
            self._pick(x, y)
            return
        if self._tool == "line":
            self._line_start = point
            self._line_preview = [point]
            self.update()
            return
        self.begin_stroke()
        self._drawing = True
        self._last = point
        self._paint_at(x, y)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        point = self._to_pixel(event.position())
        self._hover = point
        if point is None:
            self.cursorMoved.emit(-1, -1)
            self.update()
            return
        self.cursorMoved.emit(*point)
        if self._line_start is not None and self._tool == "line":
            self._line_preview = _line_points(*self._line_start, *point)
            self.update()
            return
        if self._drawing and self._last is not None:
            for px, py in _line_points(*self._last, *point):
                self._paint_at(px, py)
            self._last = point
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        if self._tool == "line" and self._line_start is not None:
            point = self._to_pixel(event.position())
            if point is not None:
                self.begin_stroke()
                for px, py in _line_points(*self._line_start, *point):
                    self.paint_pixel(px, py, erase=self._tool == "eraser")
            self._line_start = None
            self._line_preview = []
            self.end_stroke()
            self.update()
            return
        if self._drawing:
            self._drawing = False
            self.end_stroke()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = None
        self.cursorMoved.emit(-1, -1)
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = 1 if event.angleDelta().y() > 0 else -1
        self.set_zoom(self._zoom + step * (1 if self._zoom < 8 else 2))
        self._hover = self._to_pixel(event.position())
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        if self._zoom <= 2:
            self.fit()
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# 3D 预览
# ---------------------------------------------------------------------------
_FACE_NORMALS = {
    "top": (0.0, 1.0, 0.0),
    "bottom": (0.0, -1.0, 0.0),
    "front": (0.0, 0.0, 1.0),
    "back": (0.0, 0.0, -1.0),
    "right": (1.0, 0.0, 0.0),
    "left": (-1.0, 0.0, 0.0),
}
_FACE_ORDER = ("top", "bottom", "front", "back", "right", "left")


class ModelView(QWidget):
    """自由旋转的 3D 预览，操作方式对齐 Blockbench。

    - 左键在空白处拖动：旋转视角；中键或右键拖动：平移；滚轮以光标为中心缩放。
    - 按面做背面剔除 + 深度排序，任意角度都能看清模型的前后关系。
    - 可以按立方体隐藏部件，单独查看/绘制某一层（例如只显示身体）。
    - 原来在模型面上直接绘制、右键取色、Shift 填面的能力保持不变。
    """

    pixelPaint = Signal(int, int, QRect, bool)  # atlas x, y, face uv rect, erase
    pixelPick = Signal(int, int)
    faceFill = Signal(QRect, bool)
    boxFill = Signal(object, bool)
    strokeEnd = Signal()
    boxSelected = Signal(int)
    viewChanged = Signal()

    DEFAULT_YAW = -32.0
    DEFAULT_PITCH = 22.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image: QImage | None = None
        self._boxes: list[Box] = list(HUMANOID_BOXES)
        self._kind = "entity"
        self._back = False
        self._yaw = self.DEFAULT_YAW
        self._pitch = self.DEFAULT_PITCH
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self._fit_scale = 6.0
        self._scale = 6.0
        self._center = QPointF(0, 0)
        self._proj_center = (0.0, 0.0)
        self._faces: list[dict] = []
        self._hover_face: dict | None = None
        self._hover_uv: tuple[int, int] | None = None
        self._drawing = False
        self._hide_overlay = False
        self._hidden: set[int] = set()
        self._select_mode = False
        self._selected_box = -1
        self._drag = ""
        self._press = QPointF()
        self._last = QPointF()
        self._moved = False
        self._hint = "左键拖动旋转 · 中键平移 · 滚轮缩放 · 点模型面即可上色"
        self.setMouseTracking(True)
        self.setMinimumSize(220, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def set_kind(self, kind: str) -> None:
        self._kind = kind
        if self._select_mode:
            self._hint = "左键点选立方体 · 空白拖动旋转 · 中键平移 · 滚轮缩放"
        else:
            self._hint = (
                "直接点图标即可上色 · 右键取色"
                if kind == "item"
                else "左键拖动旋转 · 中键平移 · 滚轮缩放 · 点面即可上色"
            )
        if kind == "entity":
            self._boxes = list(HUMANOID_BOXES)
            self._hidden.clear()
        self.update()

    def set_hint(self, text: str) -> None:
        """预览场景可以关掉操作提示，避免看起来像可编辑画布。"""
        self._hint = str(text or "")
        self.update()

    def set_select_mode(self, enabled: bool) -> None:
        """模型编辑模式：左键只做立方体选择，不往贴图上落笔。"""
        self._select_mode = bool(enabled)
        if self._select_mode:
            self._selected_box = -1
            self._hint = "左键点选立方体 · 空白拖动旋转 · 中键平移 · 滚轮缩放"
        elif self._kind == "item":
            self._hint = "直接点图标即可上色 · 右键取色"
        else:
            self._hint = "左键拖动旋转 · 中键平移 · 滚轮缩放 · 点面即可上色"
        self.update()

    def set_selected_box(self, index: int) -> None:
        index = int(index)
        if index < 0 or index >= len(self._boxes):
            index = -1
        if index != self._selected_box:
            self._selected_box = index
            self.update()

    def selected_box(self) -> int:
        return self._selected_box

    def set_boxes(self, boxes: list[Box]) -> None:
        self._boxes = list(boxes)
        # 换模型后旧的隐藏下标没有意义了。
        self._hidden = {index for index in self._hidden if index < len(self._boxes)}
        self.update()

    def set_back_view(self, back: bool) -> None:
        self._back = bool(back)
        self.update()

    def set_hide_overlay(self, hide: bool) -> None:
        self._hide_overlay = bool(hide)
        self.update()

    def back_view(self) -> bool:
        return self._back

    # -- 相机 ------------------------------------------------------------
    def set_camera(self, yaw: float | None = None, pitch: float | None = None) -> None:
        """设置视角角度；yaw 绕竖轴，pitch 为俯视角度。"""
        if yaw is not None:
            self._yaw = float(yaw) % 360.0
        if pitch is not None:
            self._pitch = max(-89.0, min(89.0, float(pitch)))
        self.update()
        self.viewChanged.emit()

    def set_view(self, name: str) -> None:
        """预设视角：blockbench 风格的 3/4 视角，以及正/侧/顶视图。"""
        presets = {
            "default": (self.DEFAULT_YAW, self.DEFAULT_PITCH),
            "front": (0.0, 0.0),
            "back": (180.0, 0.0),
            "side": (-90.0, 0.0),
            "top": (0.0, 89.0),
        }
        if name in presets:
            # 预设视角是绝对角度，先清掉旧的「背面」标记，避免叠加 180°。
            self._back = False
        yaw, pitch = presets.get(str(name), presets["default"])
        self.set_camera(yaw, pitch)

    def view_angles(self) -> tuple[float, float]:
        return self._yaw, self._pitch

    def reset_view(self) -> None:
        self._back = False
        self._yaw = self.DEFAULT_YAW
        self._pitch = self.DEFAULT_PITCH
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()
        self.viewChanged.emit()

    # -- 部件显隐 --------------------------------------------------------
    def set_hidden_indices(self, indices: Iterable[int]) -> None:
        self._hidden = {
            int(index) for index in indices if 0 <= int(index) < len(self._boxes)
        }
        self.update()

    def set_box_visible(self, index: int, visible: bool) -> None:
        index = int(index)
        if visible:
            self._hidden.discard(index)
        elif 0 <= index < len(self._boxes):
            self._hidden.add(index)
        self.update()

    def hidden_indices(self) -> set[int]:
        return set(self._hidden)

    def set_solo(self, index: int) -> None:
        """只看某一个立方体；index < 0 时恢复全部。"""
        index = int(index)
        if 0 <= index < len(self._boxes):
            self._hidden = {
                item for item in range(len(self._boxes)) if item != index
            }
        else:
            self._hidden.clear()
        self.update()

    def fit(self) -> None:
        self._zoom = 1.0
        self._pan = QPointF(0.0, 0.0)
        self.update()

    # -- 投影 ------------------------------------------------------------
    def _camera(self) -> tuple[float, float]:
        """返回 (yaw, pitch) 弧度；「看背面」相当于绕竖轴再转 180°。"""
        yaw = self._yaw + (180.0 if self._back else 0.0)
        return math.radians(yaw), math.radians(self._pitch)

    def _camera_point(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        """世界坐标 → 相机坐标：x 向右，y 向上，z 越大越靠近观察者。"""
        yaw, pitch = self._camera()
        ca, sa = math.cos(yaw), math.sin(yaw)
        ce, se = math.cos(pitch), math.sin(pitch)
        x1 = x * ca + z * sa
        z1 = -x * sa + z * ca
        y2 = y * ce - z1 * se
        z2 = y * se + z1 * ce
        return x1, y2, z2

    def _project(self, x: float, y: float, z: float) -> QPointF:
        camera_x, camera_y, _depth = self._camera_point(x, y, z)
        return QPointF(
            self._center.x() + camera_x * self._scale,
            self._center.y() - camera_y * self._scale,
        )

    def _scene_boxes(self) -> list[Box]:
        if self._kind == "block":
            return [Box("方块", (0, 0, 0), (16, 16, 16), (0, 0))]
        return self._boxes

    def _compute_layout(self) -> None:
        boxes = self._scene_boxes()
        # 只看某个部件时按它重新取景，单独检查一层不会被缩成一个小点。
        visible = [
            box for index, box in enumerate(boxes) if index not in self._hidden
        ]
        if self._kind != "block" and self._hide_overlay:
            visible = [box for box in visible if not _is_overlay(box.name)]
        if visible:
            boxes = visible
        if boxes:
            # 旋转过的立方体不能再拿 origin/size 当包围盒，必须按八个角点取。
            points = [point for box in boxes for point in box_corner_points(box)]
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            zs = [point[2] for point in points]
            x_low, x_high = min(xs), max(xs)
            y_low, y_high = min(ys), max(ys)
            z_low, z_high = min(zs), max(zs)
            center = (
                (x_low + x_high) / 2,
                (y_low + y_high) / 2,
                (z_low + z_high) / 2,
            )
            extent = max(x_high - x_low, y_high - y_low, z_high - z_low, 1.0)
        else:
            center = (0.0, 0.0, 0.0)
            extent = 16.0
        avail = max(60.0, min(self.width() - 26, self.height() - 26))
        # 用包围盒的最长边定缩放，旋转时不跟着放大缩小；隐藏部件也不跳。
        self._fit_scale = max(0.5, avail / (extent * 1.28))
        self._scale = self._fit_scale * self._zoom
        camera_x, camera_y, _depth = self._camera_point(*center)
        self._proj_center = (camera_x, camera_y)
        base_x = self.width() / 2 + self._pan.x()
        base_y = self.height() / 2 + self._pan.y() - 6.0
        self._center = QPointF(
            base_x - camera_x * self._scale,
            base_y + camera_y * self._scale,
        )

    # -- 面 --------------------------------------------------------------
    def _face_rects(self, box: Box) -> dict[str, QRect]:
        u, v, w, h, d = box.u, box.v, int(box.w), int(box.h), int(box.d)
        rects = {
            "top": QRect(u + d, v, w, d),
            "bottom": QRect(u + d + w, v, w, d),
            "right": QRect(u, v + d, d, h),
            "front": QRect(u + d, v + d, w, h),
            "left": QRect(u + d + w, v + d, d, h),
            "back": QRect(u + 2 * d + w, v + d, w, h),
        }
        if box.faces:
            # Java models may give every face its own UV rect instead of a
            # box-UV origin; painted pixels must land on those exact rects.
            rects.update(box.faces)
        return rects

    def _box_quads(
        self, box: Box
    ) -> dict[str, tuple[tuple[float, float, float], ...]]:
        """六个面的世界坐标顶点，按贴图的「左上、右上、右下、左下」排。"""
        # 角点顺序：x0/x1 外层，y0/y1 中层，z0/z1 内层。
        points = box_corner_points(box)
        # 旋转只会改变坐标，不会改变拓扑；下标始终对应原来的八个角。
        return {
            "top": (points[2], points[6], points[7], points[3]),
            "bottom": (points[1], points[5], points[4], points[0]),
            "front": (points[3], points[7], points[5], points[1]),
            "back": (points[6], points[2], points[0], points[4]),
            "right": (points[7], points[6], points[4], points[5]),
            "left": (points[2], points[3], points[1], points[0]),
        }

    def _face_transform(self, quad: list[QPointF], rect: QRect) -> QTransform:
        p0 = quad[0]
        ux = (quad[1].x() - p0.x()) / max(1, rect.width())
        uy = (quad[1].y() - p0.y()) / max(1, rect.width())
        vx = (quad[3].x() - p0.x()) / max(1, rect.height())
        vy = (quad[3].y() - p0.y()) / max(1, rect.height())
        return QTransform(ux, uy, vx, vy, p0.x(), p0.y())

    def _face_visible(self, quad, center) -> bool:
        """旋转后的面法线朝外且朝向观察者时才绘制。"""
        p0, p1, p3 = quad[0], quad[1], quad[3]
        ux, uy, uz = p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]
        vx, vy, vz = p3[0] - p0[0], p3[1] - p0[1], p3[2] - p0[2]
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        length = math.sqrt(nx * nx + ny * ny + nz * nz)
        if length <= 1e-9:
            return False
        # 叉积方向跟顶点绕向有关，用「面心 - 盒心」把它统一到朝外。
        fx = sum(point[0] for point in quad) / 4.0 - center[0]
        fy = sum(point[1] for point in quad) / 4.0 - center[1]
        fz = sum(point[2] for point in quad) / 4.0 - center[2]
        if nx * fx + ny * fy + nz * fz < 0.0:
            nx, ny, nz = -nx, -ny, -nz
        nx, ny, nz = nx / length, ny / length, nz / length
        yaw, pitch = self._camera()
        ca, sa = math.cos(yaw), math.sin(yaw)
        ce, se = math.cos(pitch), math.sin(pitch)
        x1 = nx * ca + nz * sa
        z1 = -nx * sa + nz * ca
        z2 = ny * se + z1 * ce
        return z2 > 1e-6

    def _render_faces(self) -> None:
        self._faces = []
        if self._image is None:
            return
        full_face = QRect(0, 0, self._image.width(), self._image.height())
        boxes = self._scene_boxes()
        collected: list[tuple] = []
        for box_index, box in enumerate(boxes):
            if box_index in self._hidden:
                continue
            if self._kind != "block" and self._hide_overlay and _is_overlay(box.name):
                continue
            rects = self._face_rects(box)
            quads = self._box_quads(box)
            center = box.center()
            if box.rotation != (0.0, 0.0, 0.0):
                corners_all = box_corner_points(box)
                center = (
                    sum(point[0] for point in corners_all) / 8.0,
                    sum(point[1] for point in corners_all) / 8.0,
                    sum(point[2] for point in corners_all) / 8.0,
                )
            for face_order, name in enumerate(_FACE_ORDER):
                corners = quads[name]
                if not self._face_visible(corners, center):
                    continue
                rect = full_face if self._kind == "block" else rects.get(name)
                if rect is None or rect.width() <= 0 or rect.height() <= 0:
                    continue
                depth = sum(
                    self._camera_point(*corner)[2] for corner in corners
                ) / 4.0
                quad = [self._project(*corner) for corner in corners]
                collected.append(
                    (depth, face_order, box_index, name, rect, quad)
                )
        # 远面先画、近面后画；同一深度时基础层先画，外层（帽子/外套）覆盖在上面。
        collected.sort(key=lambda item: item[:3])
        for _depth, _order, index, name, rect, quad in collected:
            self._faces.append(
                {
                    "box": boxes[index],
                    "name": name,
                    "rect": rect,
                    "quad": quad,
                    "transform": self._face_transform(quad, rect),
                    "index": index,
                    "depth": _depth,
                }
            )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(COLORS["bg_alt"]))
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self._image is None:
            painter.setPen(QColor(COLORS["muted"]))
            painter.drawText(self.rect(), Qt.AlignCenter, "打开贴图后在这里预览")
            painter.end()
            return
        if self._kind == "item":
            self._paint_item(painter)
            painter.end()
            return

        self._compute_layout()
        painter.setRenderHint(QPainter.Antialiasing, False)
        self._render_faces()
        image = self._image
        for face in self._faces:
            rect = face["rect"]
            source = image.copy(
                max(0, rect.x()),
                max(0, rect.y()),
                max(1, min(rect.width(), image.width() - max(0, rect.x()))),
                max(1, min(rect.height(), image.height() - max(0, rect.y()))),
            )
            painter.save()
            painter.setTransform(face["transform"])
            painter.drawImage(QPointF(0, 0), source)
            painter.restore()

        hover = self._hover_face
        if hover is not None:
            painter.setPen(QPen(QColor(COLORS["accent"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(QPolygonF(hover["quad"]))
            if self._hover_uv is not None:
                self._paint_uv_marker(painter, hover, self._hover_uv)

        if self._select_mode and self._selected_box >= 0:
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(COLORS["accent"]), 3))
            for face in self._faces:
                if face.get("index") == self._selected_box:
                    painter.drawPolygon(QPolygonF(face["quad"]))

        painter.setPen(QColor(COLORS["muted"]))
        if self._hint:
            painter.drawText(10, 18, self._hint)
        painter.end()

    def _item_geometry(self) -> tuple[int, int, int] | None:
        image = self._image
        if image is None:
            return None
        zoom = max(1, min(16, min((self.width() - 48) // max(1, image.width()),
                                  (self.height() - 48) // max(1, image.height()))))
        w, h = image.width() * zoom, image.height() * zoom
        return (self.width() - w) // 2, (self.height() - h) // 2, zoom

    def _item_pixel(self, pos: QPointF) -> tuple[int, int] | None:
        geometry = self._item_geometry()
        if geometry is None or self._image is None:
            return None
        x, y, zoom = geometry
        px = int((pos.x() - x) // zoom)
        py = int((pos.y() - y) // zoom)
        if 0 <= px < self._image.width() and 0 <= py < self._image.height():
            return px, py
        return None

    def _paint_item(self, painter: QPainter) -> None:
        image = self._image
        geometry = self._item_geometry()
        if image is None or geometry is None:
            return
        x, y, zoom = geometry
        w, h = image.width() * zoom, image.height() * zoom
        painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
        painter.setRenderHint(QPainter.Antialiasing, False)
        cell = max(4, zoom // 2)
        for cy in range(y, y + h, cell):
            for cx in range(x, x + w, cell):
                dark = ((cx - x) // cell + (cy - y) // cell) % 2 == 0
                painter.fillRect(QRect(cx, cy, cell, cell),
                                 QColor("#222633") if dark else QColor("#2a2f3e"))
        painter.drawImage(QRect(x, y, w, h), image)
        if self._hover_uv is not None:
            hx, hy = self._hover_uv
            painter.setPen(QPen(QColor(COLORS["accent"]), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(QRect(x + hx * zoom, y + hy * zoom, zoom, zoom))
        painter.setPen(QColor(COLORS["muted"]))
        if self._hint:
            painter.drawText(10, 18, self._hint)

    def _paint_uv_marker(self, painter: QPainter, face: dict, uv: tuple[int, int]) -> None:
        rect = face["rect"]
        lx = uv[0] - rect.x()
        ly = uv[1] - rect.y()
        if not (0 <= lx < rect.width() and 0 <= ly < rect.height()):
            return
        transform = face["transform"]
        point = transform.map(QPointF(lx + 0.5, ly + 0.5))
        painter.setBrush(QColor(COLORS["accent"]))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(point, 3.5, 3.5)

    # -- 命中测试 --------------------------------------------------------
    def _hit(self, pos: QPointF) -> tuple[dict, int, int] | None:
        for face in reversed(self._faces):
            inverse, ok = face["transform"].inverted()
            if not ok:
                continue
            local = inverse.map(pos)
            rect = face["rect"]
            if 0 <= local.x() < rect.width() and 0 <= local.y() < rect.height():
                return face, rect.x() + int(local.x()), rect.y() + int(local.y())
        return None

    def _ensure_faces(self) -> None:
        if not self._faces and self._image is not None:
            self._compute_layout()
            self._render_faces()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._press = event.position()
        self._last = event.position()
        self._moved = False
        if self._kind == "item":
            point = self._item_pixel(event.position())
            if point is None:
                return
            if event.button() == Qt.RightButton:
                self.pixelPick.emit(*point)
                return
            if event.button() != Qt.LeftButton:
                return
            if event.modifiers() & Qt.ShiftModifier:
                self.faceFill.emit(
                    QRect(0, 0, self._image.width(), self._image.height()), False
                )
                return
            self._drawing = True
            self.pixelPaint.emit(
                point[0],
                point[1],
                QRect(0, 0, self._image.width(), self._image.height()),
                False,
            )
            return
        if event.button() in (Qt.MiddleButton, Qt.RightButton):
            # 右键轻点仍然取色，拖起来就是平移，和 Blockbench 的中键一致。
            self._drag = "pan"
            self.setCursor(Qt.ClosedHandCursor)
            return
        if event.button() != Qt.LeftButton:
            return
        self._ensure_faces()
        hit = self._hit(event.position())
        if hit is None:
            self._drag = "orbit"
            self.setCursor(Qt.ClosedHandCursor)
            return
        if self._select_mode:
            index = int(hit[0].get("index", -1))
            if index >= 0:
                self.set_selected_box(index)
                self.boxSelected.emit(index)
            return
        face, ux, uy = hit
        if event.modifiers() & Qt.ShiftModifier:
            if event.modifiers() & Qt.ControlModifier:
                rects = list(self._face_rects(face["box"]).values())
                self.boxFill.emit(rects, False)
            else:
                self.faceFill.emit(QRect(face["rect"]), False)
            return
        self._drawing = True
        self.pixelPaint.emit(ux, uy, QRect(face["rect"]), False)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag:
            delta = event.position() - self._last
            if not self._moved and (
                event.position() - self._press
            ).manhattanLength() > 3:
                self._moved = True
            if self._drag == "orbit":
                # 拖动方向跟着模型走：往右拖模型往右转，往下拖看到更多顶面。
                self._yaw = (self._yaw + delta.x() * 0.55) % 360.0
                self._pitch = max(
                    -89.0, min(89.0, self._pitch + delta.y() * 0.45)
                )
            else:
                self._pan = self._pan + delta
            self._last = event.position()
            self.update()
            self.viewChanged.emit()
            return
        if self._kind == "item":
            point = self._item_pixel(event.position())
            self._hover_uv = point
            if point is not None and self._drawing and self._image is not None:
                self.pixelPaint.emit(
                    point[0],
                    point[1],
                    QRect(0, 0, self._image.width(), self._image.height()),
                    False,
                )
            self.update()
            return
        self._ensure_faces()
        hit = self._hit(event.position())
        if hit is None:
            changed = self._hover_face is not None
            self._hover_face = None
            self._hover_uv = None
            if changed:
                self.update()
            return
        face, ux, uy = hit
        self._hover_face = face
        self._hover_uv = (ux, uy)
        if self._drawing:
            self.pixelPaint.emit(ux, uy, QRect(face["rect"]), False)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._drawing:
            self._drawing = False
            self.strokeEnd.emit()
        if self._drag:
            if event.button() == Qt.RightButton and not self._moved:
                if self._kind == "item":
                    point = self._item_pixel(event.position())
                    if point is not None:
                        self.pixelPick.emit(*point)
                elif not self._select_mode:
                    self._ensure_faces()
                    hit = self._hit(event.position())
                    if hit is not None:
                        self.pixelPick.emit(hit[1], hit[2])
            self._drag = ""
            self._moved = False
            self.setCursor(Qt.ArrowCursor)
            self.update()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_face = None
        self._hover_uv = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._kind == "item":
            super().wheelEvent(event)
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        factor = 1.14 if step > 0 else 1 / 1.14
        self._compute_layout()
        old_scale = max(0.001, self._scale)
        self._zoom = max(0.15, min(14.0, self._zoom * factor))
        new_scale = self._fit_scale * self._zoom
        point = event.position()
        base = QPointF(self.width() / 2, self.height() / 2 - 6.0)
        # 以光标为中心缩放：光标下的模型点保持不动。
        self._pan = (point - base) - (point - base - self._pan) * (
            new_scale / old_scale
        )
        self._compute_layout()
        self.update()
        self.viewChanged.emit()
        event.accept()


# ---------------------------------------------------------------------------
# 小工具：新建 / 选择工程贴图
# ---------------------------------------------------------------------------
class NewCanvasDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("新建贴图")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("物品图标（16×16）", "item")
        self.kind.addItem("方块（16×16，可平铺）", "block")
        self.kind.addItem("实体皮肤（64×64 UV）", "entity")
        self.kind.addItem("自定义", "custom")
        self.width = QSpinBox()
        self.width.setRange(8, 256)
        self.width.setValue(16)
        self.height = QSpinBox()
        self.height.setRange(8, 256)
        self.height.setValue(16)
        self.kind.currentIndexChanged.connect(self._sync)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(QLabel("宽"))
        row_layout.addWidget(self.width)
        row_layout.addWidget(QLabel("高"))
        row_layout.addWidget(self.height)
        form.addRow("类型", self.kind)
        form.addRow("尺寸", row)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _sync(self) -> None:
        key = str(self.kind.currentData())
        if key == "entity":
            self.width.setValue(64)
            self.height.setValue(64)
        elif key in {"item", "block"}:
            self.width.setValue(16)
            self.height.setValue(16)
        for widget in (self.width, self.height):
            widget.setEnabled(key == "custom")

    def values(self) -> tuple[str, int, int]:
        return str(self.kind.currentData() or "item"), self.width.value(), self.height.value()


class AssetPickerDialog(QDialog):
    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("从工程打开贴图")
        self.resize(620, 460)
        self._root = Path(project_root)
        layout = QVBoxLayout(self)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("搜索文件名…")
        self.filter.textChanged.connect(self._apply_filter)
        layout.addWidget(self.filter)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self._paths: list[Path] = []
        base = self._root / "src" / "main" / "resources" / "assets"
        if base.is_dir():
            for path in sorted(base.glob("*/textures/**/*.png")):
                if any(part in {"gui", "models", "particle"} for part in path.parts):
                    continue
                self._paths.append(path)
        for path in self._paths:
            try:
                relative = path.relative_to(self._root)
            except ValueError:
                relative = path
            item = QListWidgetItem(str(relative).replace("\\", "/"))
            item.setData(Qt.UserRole, str(path))
            self.list.addItem(item)
        if not self._paths:
            self.list.addItem("当前工程还没有贴图")
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        buttons = QDialogButtonBox(QDialogButtonBox.Open | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for row in range(self.list.count()):
            item = self.list.item(row)
            item.setHidden(bool(needle) and needle not in item.text().lower())

    def selected(self) -> Path | None:
        item = self.list.currentItem()
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        return Path(str(value)) if value else None


# ---------------------------------------------------------------------------
# 绘图页
# ---------------------------------------------------------------------------
class PaintPage(QWidget):
    """完整的像素绘图工作台。"""

    def __init__(self, main=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._path: Path | None = None
        self._kind = "item"
        self._model_path: Path | None = None
        self._syncing = False
        self._build()
        self._new_document("item", 16, 16)
        self._install_shortcuts()

    # -- 界面 ------------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        outer.setSpacing(10)

        header = card()
        head = QHBoxLayout(header)
        head.setContentsMargins(16, 12, 16, 12)
        head.setSpacing(8)
        box = QVBoxLayout()
        box.setSpacing(3)
        title = QLabel("手动绘图")
        title.setObjectName("H1")
        box.addWidget(title)
        box.addWidget(
            tiny(
                "像 Blockbench 的 Paint 模式：左边画 UV/像素图，右边是模型或方块预览，"
                "可以直接点模型面落笔。3D 预览支持拖动旋转、中键平移、滚轮缩放，"
                "也能按部件单独显示某一层。加载 .geo.json / .bbmodel / Java 模型会自动识别类型，"
                "多贴图模型会先让你选一张。画完保存回 PNG，Agent 会继续用它。"
            )
        )
        head.addLayout(box, 1)
        for text, callback in (
            ("新建", self._new_clicked),
            ("打开", self._open_clicked),
            ("从工程打开", self._open_project_clicked),
            ("保存", self._save_clicked),
            ("另存为", self._save_as_clicked),
        ):
            button = QPushButton(text)
            button.setObjectName("Ghost" if text not in {"保存"} else "Primary")
            button.clicked.connect(callback)
            head.addWidget(button)
        outer.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(10)
        outer.addLayout(body, 1)

        # 左：工具 + 画布
        left = card()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 10, 12, 10)
        left_layout.setSpacing(8)

        tools = QHBoxLayout()
        tools.setSpacing(4)
        self.tool_buttons: dict[str, QPushButton] = {}
        for key, label, _shortcut in TOOLS:
            button = QPushButton(label)
            button.setObjectName("Ghost")
            button.setCheckable(True)
            button.setToolTip(f"{label}（{key}）")
            button.clicked.connect(lambda _=False, k=key: self._set_tool(k))
            tools.addWidget(button)
            self.tool_buttons[key] = button
        self.tool_buttons["pencil"].setChecked(True)

        tools.addSpacing(8)
        tools.addWidget(QLabel("画笔"))
        self.brush = QSpinBox()
        self.brush.setRange(1, 16)
        self.brush.valueChanged.connect(lambda value: self.canvas.set_brush(value))
        tools.addWidget(self.brush)

        tools.addSpacing(8)
        self.color_btn = QPushButton("颜色")
        self.color_btn.setFixedWidth(64)
        self.color_btn.clicked.connect(self._pick_color)
        tools.addWidget(self.color_btn)
        tools.addStretch(1)

        self.undo_btn = QPushButton("撤销")
        self.undo_btn.setObjectName("Ghost")
        self.undo_btn.clicked.connect(self.canvas_undo)
        self.redo_btn = QPushButton("重做")
        self.redo_btn.setObjectName("Ghost")
        self.redo_btn.clicked.connect(self.canvas_redo)
        tools.addWidget(self.undo_btn)
        tools.addWidget(self.redo_btn)
        left_layout.addLayout(tools)

        palette_grid = QGridLayout()
        palette_grid.setSpacing(3)
        for index, color in enumerate(_PALETTE + _PALETTE_EXTRA):
            row, col = divmod(index, 16)
            palette_grid.addWidget(self._palette_swatch(color), row, col)
        left_layout.addLayout(palette_grid)

        recent_row = QHBoxLayout()
        recent_row.setSpacing(3)
        recent_row.addWidget(tiny("最近"))
        self._recent_colors: list[str] = []
        self.recent_buttons: list[QToolButton] = []
        for index in range(_RECENT_SLOTS):
            swatch = QToolButton()
            swatch.setFixedSize(20, 20)
            swatch.setEnabled(False)
            swatch.setStyleSheet(
                f"QToolButton {{ background: transparent; border: 1px dashed {COLORS['line']};"
                " border-radius: 4px; }"
            )
            swatch.clicked.connect(lambda _=False, i=index: self._pick_recent(i))
            recent_row.addWidget(swatch)
            self.recent_buttons.append(swatch)
        recent_row.addStretch(1)
        left_layout.addLayout(recent_row)

        ramp_row = QHBoxLayout()
        ramp_row.setSpacing(4)
        ramp_row.addWidget(tiny("色阶"))
        self.ramp_buttons: list[QToolButton] = []
        for index in range(7):
            swatch = QToolButton()
            swatch.setFixedSize(22, 22)
            swatch.setToolTip("从暗到亮的同色阶，点一下切换")
            swatch.clicked.connect(lambda _=False, i=index: self._pick_ramp(i))
            ramp_row.addWidget(swatch)
            self.ramp_buttons.append(swatch)
        ramp_row.addStretch(1)
        self.grid_box = QCheckBox("网格")
        self.grid_box.setChecked(True)
        self.grid_box.stateChanged.connect(
            lambda state: self.canvas.set_grid(bool(state))
        )
        ramp_row.addWidget(self.grid_box)
        self.guide_box = QCheckBox("UV 分区")
        self.guide_box.setChecked(True)
        self.guide_box.stateChanged.connect(self._apply_guides)
        ramp_row.addWidget(self.guide_box)
        self.mirror_box = QCheckBox("镜像")
        self.mirror_box.setToolTip("左右对称落笔：实体按左/右肢体配对，物品按图标中轴对称。")
        self.mirror_box.stateChanged.connect(self._apply_mirror)
        ramp_row.addWidget(self.mirror_box)
        left_layout.addLayout(ramp_row)

        self.canvas = PixelCanvas()
        self.canvas.edited.connect(self._canvas_changed)
        self.canvas.colorPicked.connect(self._set_color)
        self.canvas.cursorMoved.connect(self._cursor_moved)
        left_layout.addWidget(self.canvas, 1)
        body.addWidget(left, 1)

        # 右：预览 + 信息
        right = card()
        right.setFixedWidth(320)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 10, 12, 10)
        right_layout.setSpacing(8)
        preview_title = QLabel("预览")
        preview_title.setObjectName("H2")
        right_layout.addWidget(preview_title)

        parts_row = QHBoxLayout()
        parts_row.setSpacing(6)
        parts_row.addWidget(QLabel("部件"))
        self.parts_combo = QComboBox()
        self.parts_combo.addItem("全部显示", -1)
        self.parts_combo.setToolTip(
            "只显示某个部件，方便绘制被外层挡住的区域；选「全部显示」恢复。"
        )
        self.parts_combo.currentIndexChanged.connect(self._apply_part_filter)
        parts_row.addWidget(self.parts_combo, 1)
        right_layout.addLayout(parts_row)

        self.preview = ModelView()
        self.preview.pixelPaint.connect(self._paint_from_model)
        self.preview.pixelPick.connect(self._pick_from_model)
        self.preview.faceFill.connect(self._fill_from_model)
        self.preview.boxFill.connect(self._fill_box_from_model)
        self.preview.strokeEnd.connect(self._end_model_stroke)
        right_layout.addWidget(self.preview, 1)

        preview_tools = QHBoxLayout()
        preview_tools.setSpacing(6)
        self.overlay_box = QCheckBox("显示外层")
        self.overlay_box.setChecked(True)
        self.overlay_box.setToolTip("关闭后隐藏帽子、外套、袖子、裤腿，方便直接画里面的基础层。")
        self.overlay_box.stateChanged.connect(
            lambda state: self.preview.set_hide_overlay(not bool(state))
        )
        preview_tools.addWidget(self.overlay_box)
        self.back_btn = QPushButton("看背面")
        self.back_btn.setObjectName("Ghost")
        self.back_btn.setCheckable(True)
        self.back_btn.clicked.connect(self._toggle_back)
        preview_tools.addWidget(self.back_btn)
        preview_tools.addStretch(1)
        right_layout.addLayout(preview_tools)

        model_tools = QHBoxLayout()
        model_tools.setSpacing(6)
        self.model_btn = QPushButton("加载模型…")
        self.model_btn.setObjectName("Ghost")
        self.model_btn.clicked.connect(self._load_model)
        model_tools.addWidget(self.model_btn)
        self.fit_btn = QPushButton("适配")
        self.fit_btn.setObjectName("Ghost")
        self.fit_btn.clicked.connect(self._fit_all)
        model_tools.addWidget(self.fit_btn)
        self.reset_view_btn = QPushButton("重置视角")
        self.reset_view_btn.setObjectName("Ghost")
        self.reset_view_btn.setToolTip("回到 Blockbench 风格的 3/4 视角")
        self.reset_view_btn.clicked.connect(self.preview.reset_view)
        model_tools.addWidget(self.reset_view_btn)
        model_tools.addStretch(1)
        right_layout.addLayout(model_tools)

        self.kind_hint = tiny("物品图标：16×16，透明背景，4~8 色。")
        right_layout.addWidget(self.kind_hint)
        self.file_label = tiny("尚未保存到文件")
        self.file_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        right_layout.addWidget(self.file_label)
        body.addWidget(right)

        status = QHBoxLayout()
        status.setSpacing(14)
        self.cursor_label = tiny("坐标 -")
        self.size_label = tiny("16×16")
        self.hint_label = tiny(
            "左键画，Shift+左键填整面，Ctrl+Shift+左键填整个立方体，右键取色；"
            "空白拖动旋转，中键平移，滚轮缩放；P/E/G/I/L 切换工具。"
        )
        status.addWidget(self.cursor_label)
        status.addWidget(self.size_label)
        status.addWidget(self.hint_label, 1)
        outer.addLayout(status)

    def _install_shortcuts(self) -> None:
        for key, label, shortcut in TOOLS:
            action = QAction(label, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(lambda _=False, k=key: self._set_tool(k))
            self.addAction(action)
        undo = QAction("撤销", self)
        undo.setShortcut(QKeySequence.Undo)
        undo.triggered.connect(self.canvas_undo)
        self.addAction(undo)
        redo = QAction("重做", self)
        redo.setShortcut(QKeySequence.Redo)
        redo.triggered.connect(self.canvas_redo)
        self.addAction(redo)

    # -- 文档 ------------------------------------------------------------
    def _new_document(self, kind: str, width: int, height: int, *, load: QImage | None = None) -> None:
        self._kind = kind
        self._path = None
        self._model_path = None
        image = load.convertToFormat(QImage.Format_ARGB32) if load is not None else _blank_image(width, height)
        self.canvas.set_image(image)
        self.canvas.set_grid(self.grid_box.isChecked())
        self.preview.set_kind(kind)
        self.preview.set_boxes(HUMANOID_BOXES if kind == "entity" else [])
        self.preview.set_image(self.canvas.image())
        self._refresh_parts()
        self._apply_guides()
        self.size_label.setText(f"{image.width()}×{image.height()}")
        self.file_label.setText("尚未保存到文件")
        self.kind_hint.setText(self._hint_for(kind))
        # 自定义模型随时可以加载：方块/物品模型同样需要按 UV 面绘制。
        self.model_btn.setEnabled(True)
        self.back_btn.setEnabled(kind in {"entity", "block", "custom"})
        self.undo_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.mirror_box.setEnabled(kind in {"entity", "item"})
        self._set_color(self.canvas.color().name())
        self._apply_mirror()

    def _hint_for(self, kind: str) -> str:
        if kind == "entity":
            return "实体皮肤：64×64 UV 图集。先铺主色，再按面加明暗，最后画眼睛和纹样。"
        if kind == "block":
            return "方块：16×16 可平铺，左右/上下边缘要接得上，避免大块纯色。"
        if kind == "custom":
            return "自定义模型：按立方体盒式 UV 绘制，可在 3D 预览里直接点面上色。"
        return "物品图标：16×16，透明背景，外描边 + 左上高光 + 右下阴影，4~8 色。"

    def _new_clicked(self) -> None:
        if not self._confirm_discard():
            return
        dialog = NewCanvasDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        kind, width, height = dialog.values()
        inferred = {"item": "item", "block": "block", "entity": "entity", "custom": "item"}[kind]
        self._new_document(inferred, width, height)

    def _open_clicked(self) -> None:
        if not self._confirm_discard():
            return
        path, _filter = QFileDialog.getOpenFileName(
            self, "打开贴图", str(Path.home()), "PNG 贴图 (*.png)"
        )
        if path:
            self.open_path(Path(path))

    def _open_project_clicked(self) -> None:
        project = getattr(self.main, "active_project", None) if self.main is not None else None
        if project is None:
            QMessageBox.information(self, "没有工程", "先在对话页打开或生成一个工程。")
            return
        dialog = AssetPickerDialog(Path(project.root), self)
        if dialog.exec() != QDialog.Accepted:
            return
        selected = dialog.selected()
        if selected is not None:
            self.open_path(selected)

    def open_path(self, path: str | Path) -> None:
        target = Path(path)
        try:
            image = QImage(str(target))
            if image.isNull():
                raise ValueError("不是有效的 PNG")
        except Exception as exc:  # noqa: BLE001 - 界面给出原因
            QMessageBox.warning(self, "打开失败", str(exc))
            return
        kind = infer_kind(target)
        self._new_document(kind, image.width(), image.height(), load=image)
        self._path = target
        self.file_label.setText(str(target))
        if kind == "entity" and image.width() == 64 and image.height() == 64:
            self._load_model_from_project(target)
        self._fit_all()

    def _save_clicked(self) -> None:
        if self._path is None:
            self._save_as_clicked()
            return
        try:
            self.canvas.save(self._path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        if self.main is not None:
            self.main.status(f"已保存贴图：{self._path}")

    def _save_as_clicked(self) -> None:
        start = str(self._path or Path.home() / "texture.png")
        path, _filter = QFileDialog.getSaveFileName(
            self, "保存贴图", start, "PNG 贴图 (*.png)"
        )
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        try:
            self.canvas.save(target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self._path = target
        self.file_label.setText(str(target))
        if self.main is not None:
            self.main.status(f"已保存贴图：{target}")

    def _confirm_discard(self) -> bool:
        if not self.canvas.is_dirty():
            return True
        answer = QMessageBox.question(
            self, "放弃未保存的修改？", "当前画布有未保存的修改，继续会丢失。"
        )
        return answer == QMessageBox.Yes

    # -- 画布同步 --------------------------------------------------------
    def _canvas_changed(self) -> None:
        self.preview.set_image(self.canvas.image())
        self.undo_btn.setEnabled(self.canvas.can_undo())
        self.redo_btn.setEnabled(self.canvas.can_redo())

    def canvas_undo(self) -> None:
        self.canvas.undo()
        self._canvas_changed()

    def canvas_redo(self) -> None:
        self.canvas.redo()
        self._canvas_changed()

    def _set_tool(self, tool: str) -> None:
        self.canvas.set_tool(tool)
        for key, button in self.tool_buttons.items():
            button.setChecked(key == tool)

    def _ramp_colors(self, base: QColor) -> list[QColor]:
        hue, sat, val, alpha = base.getHsvF()
        if hue < 0:
            hue = 0.0
        result: list[QColor] = []
        for t in (0.0, 0.16, 0.33, 0.5, 0.67, 0.83, 1.0):
            local_hue = (hue + (0.5 - t) * 0.10) % 1.0 if sat > 0.01 else hue
            local_sat = max(0.0, min(1.0, sat * (1.16 - 0.34 * t)))
            local_val = max(0.0, min(1.0, val * (0.45 + 1.06 * t)))
            result.append(QColor.fromHsvF(local_hue, local_sat, local_val, alpha))
        return result

    def _palette_swatch(self, color: str) -> QToolButton:
        swatch = QToolButton()
        swatch.setFixedSize(20, 20)
        swatch.setToolTip(color.upper())
        swatch.setStyleSheet(
            f"QToolButton {{ background: {color}; border: 1px solid {COLORS['line']};"
            " border-radius: 4px; }}"
            f"QToolButton:hover {{ border: 2px solid {COLORS['accent']}; }}"
        )
        swatch.clicked.connect(lambda _=False, c=color: self._set_color(c))
        return swatch

    def _pick_recent(self, index: int) -> None:
        if 0 <= index < len(self._recent_colors):
            self._set_color(self._recent_colors[index])

    def _remember_color(self, value: QColor) -> None:
        if value.alpha() == 0 or not hasattr(self, "recent_buttons"):
            return
        name = value.name().upper()
        if name in self._recent_colors:
            self._recent_colors.remove(name)
        self._recent_colors.insert(0, name)
        del self._recent_colors[_RECENT_SLOTS:]
        for index, button in enumerate(self.recent_buttons):
            if index < len(self._recent_colors):
                color = self._recent_colors[index]
                text_color = "#111111" if QColor(color).lightness() > 150 else "#ffffff"
                button.setEnabled(True)
                button.setToolTip(color)
                button.setStyleSheet(
                    f"QToolButton {{ background: {color}; color: {text_color};"
                    f" border: 1px solid {COLORS['line']}; border-radius: 4px; }}"
                    f"QToolButton:hover {{ border: 2px solid {COLORS['accent']}; }}"
                )
            else:
                button.setEnabled(False)
                button.setToolTip("")
                button.setStyleSheet(
                    f"QToolButton {{ background: transparent;"
                    f" border: 1px dashed {COLORS['line']}; border-radius: 4px; }}"
                )

    def _set_color(self, color: str) -> None:
        value = QColor(color)
        if not value.isValid():
            return
        self.canvas.set_color(value)
        if value.alpha() == 0:
            self.color_btn.setText("透明")
            self.color_btn.setStyleSheet("")
        else:
            self.color_btn.setText(value.name().upper())
            text_color = "#111111" if value.lightness() > 150 else "#ffffff"
            self.color_btn.setStyleSheet(
                f"background: {value.name()}; color: {text_color}; border-radius: 8px;"
            )
        self._remember_color(value)
        self._paint_ramp(value)

    def _paint_ramp(self, base: QColor) -> None:
        if not self.ramp_buttons:
            return
        for swatch, color in zip(self.ramp_buttons, self._ramp_colors(base)):
            swatch.setStyleSheet(
                f"QToolButton {{ background: {color.name()};"
                f" border: 1px solid {COLORS['line']}; border-radius: 4px; }}"
                f"QToolButton:hover {{ border: 2px solid {COLORS['accent']}; }}"
            )
            swatch.setToolTip(color.name().upper())

    def _pick_ramp(self, index: int) -> None:
        colors = self._ramp_colors(self.canvas.color())
        if 0 <= index < len(colors):
            self._set_color(colors[index].name())

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(self.canvas.color(), self, "选择颜色")
        if color.isValid():
            self._set_color(color.name(QColor.HexArgb) if color.alpha() < 255 else color.name())

    def _cursor_moved(self, x: int, y: int) -> None:
        self.cursor_label.setText("坐标 -" if x < 0 else f"坐标 {x}, {y}")

    # -- 模型 ------------------------------------------------------------
    def _paint_from_model(self, x: int, y: int, rect: QRect, erase: bool) -> None:
        self.canvas.begin_stroke()
        self.canvas.paint_pixel(x, y, erase=erase, clip=rect)
        self._canvas_changed()

    def _end_model_stroke(self) -> None:
        self.canvas.end_stroke()

    def _fill_from_model(self, rect: QRect, erase: bool) -> None:
        self.canvas.fill_rect(rect, erase=erase)
        self._canvas_changed()

    def _fill_box_from_model(self, rects: object, erase: bool) -> None:
        self.canvas.fill_rects(list(rects or []), erase=erase)
        self._canvas_changed()

    # -- 镜像 ------------------------------------------------------------
    def _mirror_partner(self, box: Box) -> Box:
        name = _MIRROR_NAMES.get(box.name)
        if not name:
            return box
        for item in self.preview._boxes:
            if item.name == name:
                return item
        return box

    def _mirror_pixel(self, x: int, y: int) -> tuple[int, int] | None:
        if self._kind != "entity":
            return None
        for box in self.preview._boxes:
            for face_name, rect in self.preview._face_rects(box).items():
                if not rect.contains(x, y):
                    continue
                partner = self._mirror_partner(box)
                target = self.preview._face_rects(partner)[face_name]
                local_x = x - rect.x()
                local_y = y - rect.y()
                return (
                    target.x() + target.width() - 1 - local_x,
                    target.y() + local_y,
                )
        return None

    def _mirror_item_pixel(self, x: int, y: int) -> tuple[int, int] | None:
        width = self.canvas.image().width()
        return width - 1 - x, y

    def _apply_mirror(self, *_args) -> None:
        if not self.mirror_box.isChecked():
            self.canvas.set_mirror(None)
        elif self._kind == "entity":
            self.canvas.set_mirror(self._mirror_pixel)
        elif self._kind == "item":
            self.canvas.set_mirror(self._mirror_item_pixel)
        else:
            self.canvas.set_mirror(None)

    def _pick_from_model(self, x: int, y: int) -> None:
        if 0 <= x < self.canvas.image().width() and 0 <= y < self.canvas.image().height():
            color = self.canvas.image().pixelColor(x, y)
            self._set_color(color.name(QColor.HexArgb) if color.alpha() < 255 else color.name())

    def _toggle_back(self) -> None:
        self.preview.set_back_view(self.back_btn.isChecked())
        self.back_btn.setText("看正面" if self.back_btn.isChecked() else "看背面")

    def _refresh_parts(self) -> None:
        """把当前模型的立方体填进「部件」下拉框。"""
        current = self.parts_combo.currentData()
        self._syncing = True
        self.parts_combo.clear()
        self.parts_combo.addItem("全部显示", -1)
        for index, box in enumerate(self.preview._boxes):
            self.parts_combo.addItem(f"{index + 1}. {box.name}", index)
        target = 0
        for position in range(self.parts_combo.count()):
            if self.parts_combo.itemData(position) == current:
                target = position
                break
        self.parts_combo.setCurrentIndex(target)
        self._syncing = False
        self.parts_combo.setEnabled(bool(self.preview._boxes))
        self._apply_part_filter()

    def _apply_part_filter(self, *_args) -> None:
        if self._syncing:
            return
        value = self.parts_combo.currentData()
        index = int(value) if value is not None else -1
        if 0 <= index < len(self.preview._boxes):
            self.preview.set_solo(index)
        else:
            self.preview.set_hidden_indices(set())

    def _fit_all(self) -> None:
        self.canvas.fit()
        self.preview.fit()
        self.preview.update()

    def _apply_guides(self) -> None:
        if self._kind == "entity" and self.canvas.image().width() == 64 \
                and self.canvas.image().height() == 64 and self.guide_box.isChecked():
            guides = [(label, rect, QColor(color)) for label, rect, color in HUMANOID_GUIDES]
            self.canvas.set_guides(guides)
        else:
            self.canvas.set_guides([])

    def _load_model(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "加载模型",
            str(Path.home()),
            "模型 (*.geo.json *.bbmodel *.json);;所有文件 (*)",
        )
        if path:
            self._load_model_path(Path(path))

    def _load_model_path(self, path: Path, *, embedded: bool = True,
                         texture_index: int | None = None) -> None:
        try:
            boxes = parse_model(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "模型解析失败", str(exc))
            return
        if not boxes:
            QMessageBox.information(self, "没有立方体", "这个模型里没有可绘制的 cube。")
            return
        kind = model_kind(path)
        image: QImage | None = None
        if embedded:
            textures = model_texture_images(path)
            if textures:
                selected = 0
                if texture_index is not None:
                    selected = max(0, min(len(textures) - 1, texture_index))
                elif len(textures) > 1:
                    labels = [
                        f"{name}（{img.width()}×{img.height()}）"
                        for name, img in textures
                    ]
                    choice, ok = QInputDialog.getItem(
                        self, "选择模型贴图", "这个模型包含多张贴图：",
                        labels, 0, False,
                    )
                    if not ok:
                        return
                    selected = labels.index(choice)
                image = textures[selected][1]
        if image is not None:
            if self.canvas.is_dirty() and not self._confirm_discard():
                return
            self._new_document(kind, image.width(), image.height(), load=image)
            self._path = None
            self.file_label.setText(f"模型内嵌贴图：{path.name}")
        self._model_path = path
        self.preview.set_kind(kind)
        self.preview.set_boxes(boxes)
        self._refresh_parts()
        self._kind = kind
        self.back_btn.setEnabled(True)
        self._apply_guides()
        self._apply_mirror()
        suffix = "实体模型" if kind == "entity" else "自定义模型"
        self.kind_hint.setText(
            f"已加载{suffix}：{path.name}（{len(boxes)} 个立方体）"
        )
        self.preview.update()

    def _load_model_from_project(self, texture: Path) -> None:
        """打开 64x64 实体贴图时，尝试找同名 geo 模型。"""
        project = getattr(self.main, "active_project", None) if self.main is not None else None
        roots: list[Path] = []
        if project is not None:
            roots.append(Path(project.root) / "src" / "main" / "resources" / "assets")
        roots.append(texture.parent.parent.parent)
        stem = texture.stem
        for base in roots:
            if not base.is_dir():
                continue
            for candidate in (
                base.glob(f"*/geo/{stem}.geo.json"),
                base.glob(f"*/geo/{stem}.bbmodel"),
                base.glob(f"*/geo/{stem}.json"),
                base.glob(f"*/models/entity/{stem}.geo.json"),
                base.glob(f"*/models/entity/{stem}.bbmodel"),
                base.glob(f"*/models/entity/{stem}.json"),
            ):
                for path in candidate:
                    try:
                        boxes = parse_model(path)
                    except Exception:
                        continue
                    if boxes:
                        kind = model_kind(path)
                        self.preview.set_kind(kind)
                        self.preview.set_boxes(boxes)
                        self._refresh_parts()
                        self._kind = kind
                        self._model_path = path
                        self.back_btn.setEnabled(True)
                        self._apply_guides()
                        self._apply_mirror()
                        self.kind_hint.setText(f"已匹配模型：{path.name}")
                        return

    # -- 供设计窗口调用 ---------------------------------------------------
    def load_texture(self, path: str | Path, *, kind: str = "", model: str | Path | None = None) -> None:
        self.open_path(path)
        if kind:
            self._kind = kind
            self.preview.set_kind(kind if kind in {"entity", "block", "custom"} else "item")
            if kind == "entity":
                self.preview.set_boxes(HUMANOID_BOXES)
            self.preview.set_image(self.canvas.image())
            self._refresh_parts()
            self.back_btn.setEnabled(kind in {"entity", "block", "custom"})
            self._apply_guides()
            self._apply_mirror()
        if model and kind != "item":
            self._load_model_path(Path(model), embedded=False)


# ---------------------------------------------------------------------------
# 模型解析
# ---------------------------------------------------------------------------
def model_texture_images(path: Path) -> list[tuple[str, QImage]]:
    """读取模型内嵌/同目录引用贴图，返回 [(键, 图)]。

    .bbmodel 可以带多张贴图（皮肤、外层、发光层），绘制时让用户挑选，
    而不是永远拿第一张。
    """
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - 不是 JSON 就当没有贴图
        return []
    textures = data.get("textures") if isinstance(data, dict) else None
    # Java / .geo.json 用 {键: 路径}，Blockbench .bbmodel 用
    # [{name, source}, ...]；两种都读，外层皮肤和发光层才不会丢。
    items: list[tuple[str, str]] = []
    if isinstance(textures, dict):
        for key, value in textures.items():
            if isinstance(value, dict):
                items.append((str(key), str(value.get("source") or value.get("path") or "")))
            else:
                items.append((str(key), str(value or "")))
    elif isinstance(textures, list):
        for index, texture in enumerate(textures):
            if isinstance(texture, dict):
                source = texture.get("source") or texture.get("path") or ""
                name = texture.get("name") or texture.get("id") or f"texture{index}"
                items.append((str(name), str(source or "")))
            elif isinstance(texture, str):
                items.append((f"texture{index}", texture))
    result: list[tuple[str, QImage]] = []
    for key, value in items:
        text = str(value or "").strip()
        image: QImage | None = None
        if text.startswith("data:image/"):
            _header, _, payload = text.partition(",")
            try:
                raw = base64.b64decode(payload)
            except Exception:  # noqa: BLE001
                continue
            image = QImage.fromData(raw)
        elif text:
            candidate = Path(text)
            if not candidate.is_absolute():
                candidate = Path(path).parent / candidate
            if candidate.is_file():
                image = QImage(str(candidate))
        if image is not None and not image.isNull():
            result.append((str(key), image.convertToFormat(QImage.Format_ARGB32)))
    return result


def model_texture_image(path: Path) -> QImage | None:
    """第一张内嵌贴图；保留给只关心单贴图的调用方。"""
    images = model_texture_images(path)
    return images[0][1] if images else None


def model_kind(path: Path) -> str:
    """实体模型用 entity（含人形 UV 提示），其余立方体模型用 custom。"""
    source = Path(path)
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "entity"
    if not isinstance(data, dict):
        return "entity"
    if "minecraft:geometry" in data:
        return "entity"
    if source.suffix.lower() == ".bbmodel":
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        fmt = str(meta.get("model_format") or "").lower()
        if fmt in {"bedrock", "modded_entity", "geckolib"}:
            return "entity"
        return "custom"
    if isinstance(data.get("elements"), list):
        return "custom"
    return "entity"




def parse_model(path: Path) -> list[Box]:
    """把 .geo.json、.bbmodel 或 Java 方块/实体模型解析成可绘制的立方体。"""
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    boxes: list[Box] = []
    if isinstance(data, dict) and "minecraft:geometry" in data:
        for geometry in data.get("minecraft:geometry", []):
            for bone in geometry.get("bones", []):
                for cube in bone.get("cubes", []):
                    origin = cube.get("origin") or [0, 0, 0]
                    size = cube.get("size") or [1, 1, 1]
                    uv = cube.get("uv") or [0, 0]
                    rotation = cube.get("rotation") or [0, 0, 0]
                    pivot = cube.get("pivot")
                    boxes.append(
                        Box(
                            str(bone.get("name") or "cube"),
                            (float(origin[0]), float(origin[1]), float(origin[2])),
                            (float(size[0]), float(size[1]), float(size[2])),
                            (int(uv[0]), int(uv[1])),
                            rotation=(
                                float(rotation[0]),
                                float(rotation[1]),
                                float(rotation[2]),
                            ),
                            pivot=tuple(float(value) for value in pivot[:3])
                            if isinstance(pivot, list) and len(pivot) >= 3
                            else None,
                            inflate=float(cube.get("inflate") or 0.0),
                        )
                    )
        return boxes
    if isinstance(data, dict) and "elements" in data:
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        bbmodel = source.suffix.lower() == ".bbmodel" or "model_format" in meta
        for index, element in enumerate(data.get("elements", [])):
            if not isinstance(element, dict):
                continue
            # .bbmodel 可能混有 mesh / locator；只有 cube 能转成立方体预览。
            element_type = str(element.get("type") or "cube").lower()
            if element_type != "cube":
                continue
            frm = [float(value) for value in element.get("from") or [0, 0, 0]]
            to = [float(value) for value in element.get("to") or [16, 16, 16]]
            inflate = float(element.get("inflate") or 0.0)
            pivot: tuple[float, float, float] | None = None
            rotation = (0.0, 0.0, 0.0)
            if bbmodel:
                origin = element.get("origin")
                if isinstance(origin, list) and len(origin) >= 3:
                    pivot = tuple(float(value) for value in origin[:3])
                values = element.get("rotation")
                if isinstance(values, list) and len(values) >= 3:
                    rotation = tuple(float(value) for value in values[:3])
            else:
                # Java 模型只支持单轴 ±45°：{"origin": [...], "axis": "x", "angle": 45}
                values = element.get("rotation")
                if isinstance(values, dict):
                    axis = str(values.get("axis") or "y").lower()
                    angle = float(values.get("angle") or 0.0)
                    euler = [0.0, 0.0, 0.0]
                    if axis in ("x", "y", "z"):
                        euler["xyz".index(axis)] = angle
                    rotation = (euler[0], euler[1], euler[2])
                    origin = values.get("origin")
                    if isinstance(origin, list) and len(origin) >= 3:
                        pivot = tuple(float(value) for value in origin[:3])
            size = [float(to[i]) - float(frm[i]) for i in range(3)]
            faces = element.get("faces") or {}
            uv = element.get("uv_offset") or [0, 0]
            box_uv = element.get("uv")
            has_box_uv = isinstance(box_uv, list) and len(box_uv) >= 2
            if has_box_uv:
                uv = box_uv
            elif not (bbmodel and element.get("box_uv")):
                # Blockbench 的 box_uv=True 只有 uv_offset，不要再用面 UV
                # 去覆盖它；Java 模型则常靠每面独立 UV。
                candidates = [
                    face.get("uv")
                    for face in faces.values()
                    if isinstance(face, dict) and face.get("uv")
                ]
                if candidates:
                    uv = [
                        min(int(item[0]) for item in candidates),
                        min(int(item[1]) for item in candidates),
                    ]
            # Java models without box UV give one rect per face.  Painting on
            # the 3D preview has to write into that exact rect, so keep them.
            face_rects: dict[str, QRect] = {}
            if not has_box_uv and not element.get("box_uv"):
                for source, target in (
                    ("up", "top"), ("down", "bottom"), ("east", "right"),
                    ("south", "front"), ("west", "left"), ("north", "back"),
                ):
                    face = faces.get(source)
                    values = face.get("uv") if isinstance(face, dict) else None
                    if isinstance(values, list) and len(values) >= 4:
                        x1, y1, x2, y2 = (int(value) for value in values[:4])
                        face_rects[target] = QRect(
                            min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)
                        )
            boxes.append(
                Box(
                    f"cube{index}",
                    (float(frm[0]), float(frm[1]), float(frm[2])),
                    (max(1.0, size[0]), max(1.0, size[1]), max(1.0, size[2])),
                    (uv[0], uv[1]),
                    face_rects or None,
                    rotation=rotation,
                    pivot=pivot,
                    inflate=inflate,
                )
            )
        return boxes
    raise ValueError("只支持 .geo.json、.bbmodel 或带 elements 的 Java 模型")


# ---------------------------------------------------------------------------
# 设计窗口用的绘图对话框
# ---------------------------------------------------------------------------
class PaintDialog(QDialog):
    def __init__(self, image: str | Path, *, kind: str = "item",
                 model: str | Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("手动绘制")
        self.resize(1080, 720)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.page = PaintPage(None, self)
        layout.addWidget(self.page, 1)
        self.page.load_texture(image, kind=kind, model=model)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("Danger")
        cancel.clicked.connect(self.reject)
        save = QPushButton("保存并返回")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        row.addWidget(cancel)
        row.addWidget(save)
        layout.addLayout(row)
        self._image = Path(image)

    def _save(self) -> None:
        try:
            self.page.canvas.save(self._image)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.accept()
