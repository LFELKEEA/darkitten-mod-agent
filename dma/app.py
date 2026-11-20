from __future__ import annotations

import hashlib
import os
import sys
import time
import traceback
from datetime import datetime

from PySide6.QtCore import QLockFile, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import paths
from .ui import theme
from .ui.main_window import MainWindow
from .version import APP_AUTHOR, APP_NAME, APP_NAME_CN, APP_VERSION


def _instance_name() -> str:
    """每个安装目录一个本地服务名，避免多份安装互相干扰。"""
    digest = hashlib.sha1(
        str(paths.data_dir().resolve()).encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:12]
    return f"DarkittenModAgent-activate-{digest}"


def _activate_running_instance(timeout_ms: int = 400) -> bool:
    """把「激活窗口」发给已经运行的实例；没有实例时返回 False。"""
    socket = QLocalSocket()
    socket.connectToServer(_instance_name())
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"activate\n")
    socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return True


def _wait_for_running_instance(timeout_ms: int = 3000) -> bool:
    """对方可能还在启动中，给它一点时间把激活服务监听起来。"""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        if _activate_running_instance(300):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.15)


def _serve_activation(window: MainWindow) -> QLocalServer | None:
    """监听再次启动的请求。进程退出时命名管道由系统回收，不会留下死锁。"""
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.UserAccessOption)
    if not server.listen(_instance_name()):
        return None

    def handle() -> None:
        while server.hasPendingConnections():
            connection = server.nextPendingConnection()
            if connection is None:
                continue
            connection.waitForReadyRead(120)
            window.activate_from_second_launch()
            connection.disconnectFromServer()

    server.newConnection.connect(handle)
    return server


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
    # Gradle 和镜像代理统一使用程序目录下的缓存，不往 C 盘用户目录里放。
    os.environ.setdefault("GRADLE_USER_HOME", str(paths.gradle_home()))
    temp = str(paths.temp_dir())
    for name in ("TEMP", "TMP", "TMPDIR"):
        os.environ[name] = temp

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

    # 先叫醒已运行的实例，而不是弹一句「已经在运行」就退出。
    if _activate_running_instance():
        return 0

    lock = QLockFile(str(paths.data_dir() / "app.lock"))
    # 不做「超过多少秒就算过期」的判断：程序可能一直开着，按时间判定会误删活进程的锁。
    # 只有确认持锁进程已经退出时，下面才移除残留锁。
    lock.setStaleLockTime(0)
    if not lock.tryLock(150):
        if _wait_for_running_instance():
            return 0
        # 等了 3 秒仍没有实例响应：多半是上次崩溃留下的锁，确认进程已退出再清理。
        if not (lock.removeStaleLockFile() and lock.tryLock(150)):
            QMessageBox.information(
                None,
                APP_NAME_CN,
                "恶魔小猫工坊已经在运行了，但没能把窗口叫到前面。"
                "请在任务栏里找到它，或结束残留进程后重试。",
            )
            return 0

    _install_excepthook()

    from .config import load_config

    theme.apply_theme(app, load_config().font_size)
    window = MainWindow()
    window.show()
    # 先让窗口完成第一次绘制，再扫描工程、同步模型库，避免启动时出现白屏等待。
    QTimer.singleShot(400, window.start_initial_sync)
    server = _serve_activation(window)
    smoke = os.environ.get("DMA_SMOKE_EXIT", "").strip()
    if smoke.isdigit():
        QTimer.singleShot(max(200, int(smoke)), app.quit)
    code = app.exec()
    lock.unlock()
    return code
