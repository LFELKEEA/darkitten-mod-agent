"""开发端画面窗口：实时看后台游戏、随时暂停/关闭，并可选接管鼠标键盘。

后台客户端跑在独立桌面，桌面上看不到它的窗口，所以这里用探针的 /frame
端点拉画面（不落盘），再把窗口里的鼠标键盘换算成探针动作。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QEvent, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QPainter
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..mcdev import game as game_mod
from ..mcdev.game import running_client_port
from .widgets import tiny

# 探针按键名（与 ClientProbeServer#resolveKeyMapping 一致）。
_HOLD_KEYS = {
    Qt.Key_W: "forward",
    Qt.Key_A: "left",
    Qt.Key_S: "back",
    Qt.Key_D: "right",
    Qt.Key_Space: "jump",
    Qt.Key_Shift: "sneak",
    Qt.Key_Control: "sprint",
}
_TAP_KEYS = {
    Qt.Key_E: "inventory",
    Qt.Key_Q: "drop",
    Qt.Key_F: "swap_offhand",
    Qt.Key_Tab: "player_list",
    Qt.Key_T: "chat",
}
_DIGIT_KEYS = {
    getattr(Qt, f"Key_{index}"): str(index) for index in range(1, 10)
}

# 鼠标灵敏度：像素换算成角度，接近原版手感。
_LOOK_SENSITIVITY = 0.15
# 指针离画面边缘小于这个像素时把光标拉回中心，保证可以一直转身。
_EDGE_MARGIN = 24


class _FrameWorker(QThread):
    """按固定间隔抓帧；连接失败时降速重试，不阻塞界面。"""

    frame_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, port: int | None, interval: float, parent=None) -> None:
        super().__init__(parent)
        self._port = port
        self._interval = max(0.05, float(interval))
        self._stop = threading.Event()

    def run(self) -> None:  # noqa: D401 - QThread 入口
        misses = 0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                item = game_mod.frame(port=self._port, timeout=3.0)
            except Exception as exc:  # noqa: BLE001 - 探针未就绪要继续等
                misses += 1
                if misses <= 3 or misses % 20 == 0:
                    self.failed.emit(str(exc)[:400])
                self._stop.wait(min(1.6, self._interval * 3))
                continue
            misses = 0
            if self._stop.is_set():
                break
            self.frame_ready.emit(item)
            elapsed = time.monotonic() - started
            self._stop.wait(max(0.02, self._interval - elapsed))

    def stop(self) -> None:
        self._stop.set()
        self.wait(4000)


class FrameCanvas(QWidget):
    """保持宽高比显示画面，并把鼠标动作换算成画面像素坐标。"""

    moved = Signal(float, float)
    clicked = Signal(int, int, int)
    scrolled = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._image = QImage()
        self._last = None
        self._captured = False
        self._last_warp = 0.0

    # -- 显示 ---------------------------------------------------------
    def set_image(self, image: QImage) -> None:
        self._image = image
        self.update()

    def has_image(self) -> bool:
        return not self._image.isNull()

    def image_rect(self) -> QRect:
        if self._image.isNull() or self.width() <= 0 or self.height() <= 0:
            return QRect()
        scale = min(
            self.width() / self._image.width(),
            self.height() / self._image.height(),
        )
        width = max(1, int(self._image.width() * scale))
        height = max(1, int(self._image.height() * scale))
        return QRect(
            (self.width() - width) // 2,
            (self.height() - height) // 2,
            width,
            height,
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#07090e"))
        rect = self.image_rect()
        if self._image.isNull():
            painter.setPen(QColor("#9299ad"))
            painter.drawText(self.rect(), Qt.AlignCenter, "等待画面…")
        else:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawImage(rect, self._image)
        painter.end()

    # -- 鼠标 ---------------------------------------------------------
    def set_captured(self, captured: bool) -> None:
        self._captured = bool(captured)
        self._last = None
        self.setCursor(Qt.BlankCursor if captured else Qt.ArrowCursor)
        if captured:
            self.setFocus(Qt.OtherFocusReason)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        pos = event.position().toPoint()
        if self._last is not None:
            delta_x = pos.x() - self._last.x()
            delta_y = pos.y() - self._last.y()
            if delta_x or delta_y:
                self.moved.emit(float(delta_x), float(delta_y))
        self._last = pos
        if self._captured:
            self._recenter_if_needed(pos)
        super().mouseMoveEvent(event)

    def _recenter_if_needed(self, pos) -> None:
        rect = self.image_rect()
        if rect.isEmpty():
            return
        near_edge = (
            pos.x() - rect.left() < _EDGE_MARGIN
            or rect.right() - pos.x() < _EDGE_MARGIN
            or pos.y() - rect.top() < _EDGE_MARGIN
            or rect.bottom() - pos.y() < _EDGE_MARGIN
        )
        if not near_edge:
            return
        now = time.monotonic()
        if now - self._last_warp < 0.4:
            return
        self._last_warp = now
        center = rect.center()
        # 直接跳光标会产生一次假位移，下一帧前把参照点清空。
        self._last = None
        QCursor.setPos(self.mapToGlobal(center))

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self.setFocus(Qt.MouseFocusReason)
        rect = self.image_rect()
        pos = event.position().toPoint()
        if not self._image.isNull() and rect.contains(pos) and rect.width() > 0:
            pixel_x = int((pos.x() - rect.x()) * self._image.width() / rect.width())
            pixel_y = int((pos.y() - rect.y()) * self._image.height() / rect.height())
            button = {
                Qt.LeftButton: 0,
                Qt.RightButton: 1,
                Qt.MiddleButton: 2,
            }.get(event.button(), 0)
            self.clicked.emit(pixel_x, pixel_y, button)
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        steps = int(event.angleDelta().y() / 120)
        if steps:
            self.scrolled.emit(steps)
        event.accept()


class GameViewWindow(QWidget):
    """独立的画面窗口：可以一直开着，也可以随时关掉。"""

    status_message = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowTitle("游戏画面 · Minecraft 开发端")
        self.resize(1100, 700)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel("后台开发端")
        self.title.setObjectName("H2")
        self.status = tiny("未连接")
        head.addWidget(self.title)
        head.addWidget(self.status, 1)

        self.fps_box = QComboBox()
        for value, label in ((1, "1 帧/秒"), (2, "2 帧/秒"), (3, "3 帧/秒"), (5, "5 帧/秒")):
            self.fps_box.addItem(label, value)
        self.fps_box.setCurrentIndex(2)
        self.fps_box.currentIndexChanged.connect(self._restart_worker)
        head.addWidget(self.fps_box)

        self.monitor_btn = QPushButton("暂停画面")
        self.monitor_btn.setObjectName("Ghost")
        self.monitor_btn.clicked.connect(self._toggle_monitor)
        head.addWidget(self.monitor_btn)

        self.shot_btn = QPushButton("保存截图")
        self.shot_btn.setObjectName("Ghost")
        self.shot_btn.clicked.connect(self._save_shot)
        head.addWidget(self.shot_btn)

        self.close_btn = QPushButton("关闭画面")
        self.close_btn.setObjectName("Ghost")
        self.close_btn.clicked.connect(self.close)
        head.addWidget(self.close_btn)
        layout.addLayout(head)

        self.canvas = FrameCanvas(self)
        layout.addWidget(self.canvas, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.control_box = QCheckBox("操控画面")
        self.control_box.setToolTip(
            "勾选后鼠标移动转视角，左键攻击、右键使用手持物品，"
            "WASD/空格/Shift 移动；界面打开时点击会转发到游戏界面。"
        )
        self.control_box.toggled.connect(self._toggle_control)
        foot.addWidget(self.control_box)
        self.hint = tiny("画面通过本地探针读取，不落盘；勾选操控后才会把操作发给游戏。")
        foot.addWidget(self.hint, 1)
        layout.addLayout(foot)

        self._worker: _FrameWorker | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dma-view")
        self._port: int | None = None
        self._root: Path | None = None
        self._paused = False
        self._frame = None
        self._yaw = 0.0
        self._pitch = 0.0
        self._in_world = False
        self._screen = ""
        self._gui_width = 0
        self._gui_height = 0
        self._frame_stamp = 0.0
        self._fps = 0.0
        self._look_dirty = False
        self._pressed: set[str] = set()
        self._shot_running = False
        self._slot = 1

        self._look_timer = QTimer(self)
        self._look_timer.setInterval(33)
        self._look_timer.timeout.connect(self._flush_look)
        self._look_timer.start()

        self.status_message.connect(self.status.setText)
        self.canvas.installEventFilter(self)
        self.canvas.moved.connect(self._on_moved)
        self.canvas.clicked.connect(self._on_clicked)
        self.canvas.scrolled.connect(self._on_scrolled)

    # -- 对外 ---------------------------------------------------------
    def show_for(self, project_root: str | Path, port: int | None) -> None:
        root = Path(str(project_root)).expanduser() if project_root else None
        port = int(port) if port else None
        changed = port != self._port or root != self._root
        self._root = root
        self._port = port
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        if changed or self._worker is None or self._paused:
            self._paused = False
            self.monitor_btn.setText("暂停画面")
            self._restart_worker()

    def stop(self) -> None:
        self._stop_worker()
        self._release_keys()
        self.canvas.set_captured(False)
        self.control_box.setChecked(False)

    # -- 抓帧 ---------------------------------------------------------
    def _interval(self) -> float:
        try:
            return 1.0 / max(1, int(self.fps_box.currentData() or 3))
        except (TypeError, ValueError):
            return 1 / 3

    def _restart_worker(self) -> None:
        self._stop_worker()
        if self._port is None:
            self.status.setText("还没有运行中的客户端")
            return
        self._frame_stamp = 0.0
        self._fps = 0.0
        self._worker = _FrameWorker(self._port, self._interval(), self)
        self._worker.frame_ready.connect(self._on_frame)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()
        self.status.setText("连接中…")

    def _stop_worker(self) -> None:
        worker = self._worker
        self._worker = None
        if worker is not None:
            worker.stop()

    def _toggle_monitor(self) -> None:
        if self._paused:
            self._paused = False
            self.monitor_btn.setText("暂停画面")
            self._restart_worker()
            return
        self._paused = True
        self.monitor_btn.setText("继续画面")
        self._stop_worker()
        self.status.setText("已暂停（画面停在最后一帧）")

    def _on_frame(self, item) -> None:
        image = QImage.fromData(item.png, "PNG")
        if image.isNull():
            self.status.setText("画面解码失败")
            return
        now = time.monotonic()
        if self._frame_stamp:
            delta = now - self._frame_stamp
            if delta > 0:
                instant = 1.0 / delta
                self._fps = instant if not self._fps else self._fps * 0.7 + instant * 0.3
        self._frame_stamp = now
        self._frame = item
        self._yaw = float(item.yaw)
        self._pitch = float(item.pitch)
        self._in_world = bool(item.in_world)
        self._screen = str(item.screen or "")
        self._gui_width = int(item.gui_width or image.width())
        self._gui_height = int(item.gui_height or image.height())
        self.canvas.set_image(image)
        self._refresh_status(item.width or image.width(), item.height or image.height())

    def _refresh_status(self, width: int, height: int) -> None:
        parts = [f"{width}×{height}", f"{self._fps:.1f} fps"]
        parts.append("已进服" if self._in_world else "未进服")
        if self._screen:
            parts.append(self._short_screen(self._screen))
        else:
            parts.append(f"视角 {self._yaw:.0f}° / {self._pitch:.0f}°")
        if self.control_box.isChecked():
            parts.append("操控中")
        self.status.setText(" · ".join(parts))

    @staticmethod
    def _short_screen(name: str) -> str:
        text = name.rsplit(".", 1)[-1]
        return text or "界面"

    def _on_failed(self, message: str) -> None:
        text = message.strip()
        if "连接失败" in text:
            text = "等待探针（开发端还在启动或已退出）"
        self.status.setText(text[:120])

    # -- 截图 ---------------------------------------------------------
    def _save_shot(self) -> None:
        if self._port is None or self._shot_running:
            return
        self._shot_running = True
        self.status.setText("正在截图…")
        port = self._port

        def work() -> None:
            try:
                result = game_mod.screenshot(label="live", port=port, review=False)
                path = str(result.get("path") or "")
                message = f"截图已保存：{path}"
            except Exception as exc:  # noqa: BLE001 - 截图失败只影响提示
                message = f"截图失败：{exc}"
            self._shot_running = False
            self.status_message.emit(message[:300])

        threading.Thread(target=work, daemon=True).start()

    # -- 操控 ---------------------------------------------------------
    def _toggle_control(self, enabled: bool) -> None:
        self.canvas.set_captured(bool(enabled))
        if enabled:
            self.hint.setText(
                "鼠标移动转视角，左键攻击、右键使用手持物品，WASD/空格/Shift 移动，"
                "数字键切快捷栏，Esc 关闭界面。"
            )
        else:
            self._release_keys()
            self.hint.setText("画面通过本地探针读取，不落盘；勾选操控后才会把操作发给游戏。")
        self._look_dirty = False

    def _active(self) -> bool:
        return self._port is not None and self._in_world and not self._screen

    def _on_moved(self, delta_x: float, delta_y: float) -> None:
        if not self.control_box.isChecked() or not self._active():
            return
        self._yaw += delta_x * _LOOK_SENSITIVITY
        self._pitch = max(-90.0, min(90.0, self._pitch + delta_y * _LOOK_SENSITIVITY))
        self._look_dirty = True

    def _flush_look(self) -> None:
        if not self._look_dirty or not self._active():
            return
        if not self.control_box.isChecked():
            self._look_dirty = False
            return
        self._look_dirty = False
        port, yaw, pitch = self._port, self._yaw, self._pitch
        self._submit(lambda: game_mod.look(yaw=yaw, pitch=pitch, port=port))

    def _on_clicked(self, pixel_x: int, pixel_y: int, button: int) -> None:
        if not self.control_box.isChecked() or self._port is None:
            return
        port = self._port
        if self._screen:
            gui_x, gui_y = self._to_gui(pixel_x, pixel_y)
            self._submit(lambda: game_mod.click(gui_x, gui_y, button=button, port=port))
            return
        if not self._in_world:
            return
        if button == 0:
            self._submit(lambda: game_mod.attack(port=port))
        else:
            self._submit(lambda: game_mod.use_item(port=port))

    def _to_gui(self, pixel_x: int, pixel_y: int) -> tuple[int, int]:
        frame = self._frame
        width = frame.width if frame is not None and frame.width else self.canvas.width()
        height = frame.height if frame is not None and frame.height else self.canvas.height()
        gui_width = self._gui_width or width
        gui_height = self._gui_height or height
        if not width or not height:
            return pixel_x, pixel_y
        return (
            int(pixel_x * gui_width / width),
            int(pixel_y * gui_height / height),
        )

    def _on_scrolled(self, steps: int) -> None:
        if not self.control_box.isChecked() or not self._active():
            return
        port = self._port
        slot = ((getattr(self, "_slot", 1) - 1 + steps) % 9) + 1
        self._slot = slot
        self._submit(lambda: game_mod.key(str(slot), port=port))

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        if obj is self.canvas and event.type() in (QEvent.KeyPress, QEvent.KeyRelease):
            if self._handle_key(event):
                return True
        return super().eventFilter(obj, event)

    def _handle_key(self, event) -> bool:
        if not self.control_box.isChecked() or self._port is None:
            return False
        if event.isAutoRepeat():
            return True
        key = event.key()
        pressed = event.type() == QEvent.KeyPress
        port = self._port

        if key == Qt.Key_Escape:
            if pressed and self._screen:
                self._submit(lambda: game_mod.close_screen(port=port))
            return pressed
        if self._screen:
            return False
        name = _HOLD_KEYS.get(key)
        if name:
            if pressed and name not in self._pressed:
                self._pressed.add(name)
                self._submit(lambda: game_mod.key(name, action="press", port=port))
            elif not pressed and name in self._pressed:
                self._pressed.discard(name)
                self._submit(lambda: game_mod.key(name, action="release", port=port))
            return True
        if pressed:
            tap = _TAP_KEYS.get(key) or _DIGIT_KEYS.get(key)
            if tap:
                self._submit(lambda: game_mod.key(tap, port=port))
                if tap in "123456789":
                    self._slot = int(tap)
                return True
        return False

    def _release_keys(self) -> None:
        if not self._pressed or self._port is None:
            self._pressed.clear()
            return
        port = self._port
        names = list(self._pressed)
        self._pressed.clear()
        for name in names:
            self._submit(lambda n=name: game_mod.key(n, action="release", port=port))

    def _submit(self, task) -> None:
        def run() -> None:
            try:
                task()
            except Exception as exc:  # noqa: BLE001 - 操作失败只体现在状态栏
                self.status_message.emit(f"操作失败：{str(exc)[:120]}")

        try:
            self._executor.submit(run)
        except RuntimeError:
            return

    # -- 生命周期 -----------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        self._stop_worker()
        self._release_keys()
        self.canvas.set_captured(False)
        self.control_box.setChecked(False)
        event.accept()
