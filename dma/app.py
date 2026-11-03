from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import paths
from .ui import theme
from .ui.main_window import MainWindow
from .version import APP_AUTHOR, APP_NAME, APP_NAME_CN, APP_VERSION


def _install_excepthook() -> None:
    def handle(exc_type, exc_value, exc_tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            log = paths.data_dir() / "error.log"
            with log.open("a", encoding="utf-8") as handle_file:
                handle_file.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n{text}\n")
        except OSError:
            pass
        box = QMessageBox()
        box.setWindowTitle("程序出错")
        box.setIcon(QMessageBox.Critical)
        box.setText("程序遇到了未处理的错误。")
        box.setDetailedText(text)
        box.exec()

    sys.excepthook = handle


def run() -> int:
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationDisplayName(APP_NAME_CN)
    QApplication.setOrganizationName(APP_AUTHOR)
    QApplication.setApplicationVersion(APP_VERSION)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    icon_path = paths.resource_dir() / "dma.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))

    lock = QLockFile(str(paths.data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None,
            APP_NAME_CN,
            "暗猫模组工坊已经在运行了，请切换到已打开的窗口。",
        )
        return 0

    _install_excepthook()

    from .config import load_config

    theme.apply_theme(app, load_config().font_size)
    window = MainWindow()
    window.show()
    smoke = os.environ.get("DMA_SMOKE_EXIT", "").strip()
    if smoke.isdigit():
        QTimer.singleShot(max(200, int(smoke)), app.quit)
    code = app.exec()
    lock.unlock()
    return code
