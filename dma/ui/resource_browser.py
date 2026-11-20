"""在线资源浏览器：搜索、预览并下载模型与皮肤。

界面只负责选择和下载；真正把资源用到条目上由 :mod:`dma.ui.mod_library_page`
传进来的回调完成。所有网络请求都在线程或 Qt 网络层里执行，不阻塞界面。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QImage, QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import artresources, paths, project as project_mod
from .widgets import SelectComboBox, tiny

ApplyCallback = Callable[[Path, dict[str, Any]], str]


class _SearchWorker(QThread):
    finished_rows = Signal(list, str)

    def __init__(self, provider: str, query: str, limit: int, parent=None) -> None:
        super().__init__(parent)
        self.provider = provider
        self.query = query
        self.limit = limit

    def run(self) -> None:
        try:
            rows = artresources.search(self.provider, self.query, limit=self.limit)
        except Exception as exc:  # noqa: BLE001 - 错误展示在界面上
            self.finished_rows.emit([], str(exc))
        else:
            self.finished_rows.emit(rows, "")


class _DownloadWorker(QThread):
    finished_path = Signal(str, str)

    def __init__(
        self, provider: str, item_id: str, dest: Path, parent=None
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.item_id = item_id
        self.dest = dest

    def run(self) -> None:
        try:
            path = artresources.download_item(
                self.provider, self.item_id, self.dest
            )
        except Exception as exc:  # noqa: BLE001
            self.finished_path.emit("", str(exc))
        else:
            self.finished_path.emit(str(path), "")


def _placeholder_icon(seed: str) -> QIcon:
    digest = hashlib.sha1(str(seed).encode("utf-8")).digest()
    hue = digest[0] * 360 // 256
    image = QImage(128, 128, QImage.Format_ARGB32)
    image.fill(QColor.fromHsv(hue, 90, 70))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QColor(255, 255, 255, 90))
    painter.drawRect(8, 8, 111, 111)
    painter.setPen(QColor(255, 255, 255, 190))
    painter.drawText(image.rect(), Qt.AlignCenter, "?")
    painter.end()
    return QIcon(QPixmap.fromImage(image))


class ResourceBrowserDialog(QDialog):
    """按来源搜索在线模型/皮肤，预览后下载到工程。"""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        project_root: Path | None = None,
        apply_callback: ApplyCallback | None = None,
        provider: str = "littleskin",
    ) -> None:
        super().__init__(parent)
        self.project_root = Path(project_root).resolve() if project_root else None
        self.apply_callback = apply_callback
        self._rows: list[dict[str, Any]] = []
        self._items: dict[str, QListWidgetItem] = {}
        self._icon_cache: dict[str, QIcon] = {}
        self._icon_inflight: dict[str, str] = {}
        self._icon_waiters: dict[str, list[str]] = {}
        self._preview_cache: dict[str, QPixmap] = {}
        self._preview_inflight: str = ""
        self._search_worker: _SearchWorker | None = None
        self._download_worker: _DownloadWorker | None = None

        self.setWindowTitle("在线模型与皮肤")
        self.resize(1020, 720)
        self.network = QNetworkAccessManager(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        head = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("在线模型与皮肤")
        title.setObjectName("H2")
        title_box.addWidget(title)
        title_box.addWidget(
            tiny(
                "从社区来源搜索外观资源：Modrinth 资源包、LittleSkin 皮肤/披风、"
                "YSM 社区模型可以预览和下载；其余站点用浏览器浏览。"
                "第三方资源请遵守原作者许可，不要重新分发。"
            )
        )
        head.addLayout(title_box, 1)
        layout.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("来源"))
        self.provider = SelectComboBox()
        for item in artresources.providers():
            self.provider.addItem(str(item["name"]), str(item["id"]))
        index = self.provider.findData(provider)
        if index >= 0:
            self.provider.setCurrentIndex(index)
        bar.addWidget(self.provider, 0)
        self.query = QLineEdit()
        self.query.setPlaceholderText("名称、主题或关键词，例如 猫 / knight / 樱花")
        self.query.returnPressed.connect(self._start_search)
        bar.addWidget(self.query, 1)
        self.search_btn = QPushButton("搜索")
        self.search_btn.setObjectName("Primary")
        self.search_btn.clicked.connect(self._start_search)
        bar.addWidget(self.search_btn)
        self.browse_btn = QPushButton("用浏览器搜索")
        self.browse_btn.setObjectName("Ghost")
        self.browse_btn.clicked.connect(self._open_browse)
        bar.addWidget(self.browse_btn)
        layout.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(12)

        left = QFrame()
        left.setObjectName("CardSoft")
        left.setAttribute(Qt.WA_StyledBackground, True)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(128, 128))
        self.list.setGridSize(QSize(176, 196))
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        self.list.setSpacing(6)
        self.list.setUniformItemSizes(True)
        self.list.currentItemChanged.connect(self._on_current)
        self.list.itemDoubleClicked.connect(lambda _item: self._open_page())
        left_layout.addWidget(self.list, 1)
        left.setMinimumWidth(540)
        body.addWidget(left, 1)

        right = QFrame()
        right.setObjectName("CardSoft")
        right.setAttribute(Qt.WA_StyledBackground, True)
        right.setFixedWidth(330)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(8)
        self.preview = QLabel("选择左侧资源查看预览")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(220)
        self.preview.setStyleSheet(
            "background: #12151f; border: 1px solid #2b3142; border-radius: 8px;"
        )
        right_layout.addWidget(self.preview)
        self.detail_title = QLabel("")
        self.detail_title.setWordWrap(True)
        self.detail_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        right_layout.addWidget(self.detail_title)
        self.detail_meta = tiny("")
        right_layout.addWidget(self.detail_meta)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.detail_layout = QVBoxLayout(host)
        self.detail_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_layout.setSpacing(6)
        self.detail_desc = tiny("")
        self.detail_layout.addWidget(self.detail_desc)
        self.detail_note = tiny("")
        self.detail_layout.addWidget(self.detail_note)
        self.detail_license = tiny("")
        self.detail_layout.addWidget(self.detail_license)
        self.detail_layout.addStretch(1)
        scroll.setWidget(host)
        right_layout.addWidget(scroll, 1)

        buttons = QGridLayout()
        buttons.setSpacing(6)
        self.download_btn = QPushButton("下载到工程")
        self.download_btn.setObjectName("Primary")
        self.download_btn.setEnabled(False)
        self.download_btn.clicked.connect(lambda: self._download(apply=False))
        self.apply_btn = QPushButton("下载并用于当前条目")
        self.apply_btn.setObjectName("Ghost")
        self.apply_btn.setEnabled(False)
        self.apply_btn.clicked.connect(lambda: self._download(apply=True))
        self.page_btn = QPushButton("打开网页")
        self.page_btn.setObjectName("Ghost")
        self.page_btn.setEnabled(False)
        self.page_btn.clicked.connect(self._open_page)
        self.close_btn = QPushButton("关闭")
        self.close_btn.setObjectName("Ghost")
        self.close_btn.clicked.connect(self.accept)
        buttons.addWidget(self.download_btn, 0, 0)
        buttons.addWidget(self.apply_btn, 0, 1)
        buttons.addWidget(self.page_btn, 1, 0)
        buttons.addWidget(self.close_btn, 1, 1)
        right_layout.addLayout(buttons)
        body.addWidget(right)
        layout.addLayout(body, 1)

        self.status_label = tiny("")
        layout.addWidget(self.status_label)

        self.provider.currentIndexChanged.connect(lambda _index: self._start_search())
        self._start_search()

    def closeEvent(self, event) -> None:  # noqa: N802
        for worker in (self._search_worker, self._download_worker):
            if worker is not None and worker.isRunning():
                worker.wait(3000)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    def _current_provider(self) -> dict[str, Any]:
        provider_id = str(self.provider.currentData() or "littleskin")
        try:
            return artresources.provider(provider_id)
        except artresources.ArtResourceError:
            return artresources.providers()[0]

    def _start_search(self) -> None:
        info = self._current_provider()
        provider_id = str(info["id"])
        query = self.query.text().strip()
        if str(info.get("kind")) == "link":
            self._rows = []
            self._items = {}
            self.list.clear()
            self._show_placeholder(
                f"{info['name']} 没有公开搜索接口，已为你打开浏览器搜索页。"
            )
            self._set_status("这个来源需要在浏览器中浏览和下载。")
            self._open_browse()
            return
        if self._search_worker is not None and self._search_worker.isRunning():
            return
        self.search_btn.setEnabled(False)
        self._set_status(f"正在搜索{info['name']}…")
        worker = _SearchWorker(provider_id, query, 30, self)
        worker.finished_rows.connect(self._on_search_done)
        self._search_worker = worker
        worker.start()

    def _on_search_done(self, rows: list, error: str) -> None:
        self.search_btn.setEnabled(True)
        self._search_worker = None
        if error:
            self._show_placeholder(f"搜索失败：{error}")
            self._set_status(f"搜索失败：{error}")
            return
        self._rows = [item for item in rows if isinstance(item, dict)]
        self._items = {}
        self.list.clear()
        for row in self._rows:
            item = QListWidgetItem(str(row.get("title") or row.get("id") or ""))
            item.setData(Qt.UserRole, str(row.get("id") or ""))
            item.setToolTip(str(row.get("description") or row.get("note") or ""))
            item.setSizeHint(QSize(168, 188))
            item.setIcon(
                self._icon_cache.get(str(row.get("preview_url") or ""))
                or _placeholder_icon(str(row.get("id") or row.get("title")))
            )
            self.list.addItem(item)
            item_id = str(row.get("id") or "")
            self._items[item_id] = item
            preview_url = str(row.get("preview_url") or "")
            if preview_url:
                self._want_icon(item_id, preview_url)
        if self._rows:
            self.list.setCurrentRow(0)
            self._set_status(f"找到 {len(self._rows)} 个资源。")
        else:
            self._show_placeholder(
                "没有找到匹配资源。换个关键词，或点「用浏览器搜索」。"
            )
            self._set_status("没有找到匹配资源。")

    def _show_placeholder(self, text: str) -> None:
        self.detail_title.setText("")
        self.detail_meta.setText("")
        self.detail_desc.setText(text)
        self.detail_note.setText("")
        self.detail_license.setText("")
        self.preview.setText("")
        self.preview.setPixmap(QPixmap())
        self.download_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self.page_btn.setEnabled(False)

    def _set_status(self, text: str) -> None:
        self.status_label.setText(text)

    # ------------------------------------------------------------------
    def _request_image(self, url: str, on_done) -> None:
        request = QNetworkRequest(QUrl(url))
        request.setHeader(QNetworkRequest.UserAgentHeader, artresources.USER_AGENT)
        request.setAttribute(
            QNetworkRequest.RedirectPolicyAttribute,
            QNetworkRequest.NoLessSafeRedirectPolicy,
        )
        try:
            request.setTransferTimeout(15000)
        except AttributeError:
            pass
        reply = self.network.get(request)
        reply.finished.connect(lambda: on_done(reply))
        reply.setProperty("imageUrl", url)

    def _want_icon(self, item_id: str, url: str) -> None:
        cached = self._icon_cache.get(url)
        if cached is not None:
            item = self._items.get(item_id)
            if item is not None:
                item.setIcon(cached)
            return
        if url in self._icon_inflight:
            self._icon_waiters.setdefault(url, []).append(item_id)
            return
        self._icon_inflight[url] = url
        self._icon_waiters[url] = [item_id]
        self._request_image(url, self._icon_ready)

    def _icon_ready(self, reply: QNetworkReply) -> None:
        url = str(reply.property("imageUrl") or "")
        raw = bytes(reply.readAll())
        reply.deleteLater()
        self._icon_inflight.pop(url, None)
        image = QImage.fromData(raw)
        if image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(
            128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        icon = QIcon(pixmap)
        self._icon_cache[url] = icon
        for item_id in self._icon_waiters.pop(url, []):
            item = self._items.get(item_id)
            if item is not None:
                item.setIcon(icon)

    def _show_preview(self, url: str) -> None:
        if not url:
            self.preview.setPixmap(QPixmap())
            self.preview.setText("这个来源没有预览图")
            return
        cached = self._preview_cache.get(url)
        if cached is not None:
            self.preview.setText("")
            self.preview.setPixmap(
                cached.scaled(
                    self.preview.width() or 300,
                    self.preview.height() or 220,
                    Qt.KeepAspectRatio,
                    Qt.SmoothTransformation,
                )
            )
            self._preview_inflight = url
            return
        self.preview.setText("正在加载预览…")
        self._preview_inflight = url
        self._request_image(url, self._preview_ready)

    def _preview_ready(self, reply: QNetworkReply) -> None:
        url = str(reply.property("imageUrl") or "")
        raw = bytes(reply.readAll())
        reply.deleteLater()
        if url != self._preview_inflight:
            return
        image = QImage.fromData(raw)
        if image.isNull():
            self.preview.setText("预览图加载失败")
            return
        pixmap = QPixmap.fromImage(image)
        self._preview_cache[url] = pixmap
        self.preview.setText("")
        self.preview.setPixmap(
            pixmap.scaled(
                self.preview.width() or 300,
                self.preview.height() or 220,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        )

    # ------------------------------------------------------------------
    def _current_row(self) -> dict[str, Any] | None:
        current = self.list.currentItem()
        if current is None:
            return None
        item_id = str(current.data(Qt.UserRole) or "")
        for row in self._rows:
            if str(row.get("id") or "") == item_id:
                return row
        return None

    def _on_current(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        row = self._current_row()
        if row is None:
            return
        self.detail_title.setText(str(row.get("title") or row.get("id") or ""))
        meta = " · ".join(
            value
            for value in (
                str(row.get("author") or ""),
                str(row.get("meta") or ""),
                str(row.get("kind") or ""),
            )
            if value
        )
        self.detail_meta.setText(meta)
        self.detail_desc.setText(str(row.get("description") or "（没有描述）"))
        self.detail_note.setText(str(row.get("note") or ""))
        license_note = str(row.get("license") or "")
        provider_note = str(self._current_provider().get("license_note") or "")
        self.detail_license.setText(
            "许可提示：" + (license_note or provider_note or "请查看原项目页面。")
        )
        self._show_preview(str(row.get("preview_url") or ""))
        downloadable = bool(row.get("downloadable"))
        self.download_btn.setEnabled(downloadable)
        self.page_btn.setEnabled(bool(row.get("page_url")))
        kind = str(row.get("kind") or "")
        can_apply = (
            downloadable
            and self.apply_callback is not None
            and self.project_root is not None
            and kind in {"skin", "cape", "ysm"}
        )
        self.apply_btn.setEnabled(can_apply)
        self.apply_btn.setToolTip(
            ""
            if can_apply
            else "先在模型库里选中一个条目，再从这里应用；资源包请解压后参考。"
        )

    def _open_page(self) -> None:
        row = self._current_row()
        if row and row.get("page_url"):
            QDesktopServices.openUrl(QUrl(str(row["page_url"])))

    def _open_browse(self) -> None:
        info = self._current_provider()
        query = self.query.text().strip()
        url = artresources.browse_url(str(info["id"]), query)
        if url:
            QDesktopServices.openUrl(QUrl(url))
            self._set_status(f"已在浏览器打开：{url}")

    # ------------------------------------------------------------------
    def _dest_dir(self, provider_id: str) -> Path | None:
        if self.project_root is None:
            return None
        try:
            relative = Path("reference") / "assets" / provider_id
            target = project_mod.safe_path(self.project_root, relative.as_posix())
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"无法准备下载目录：{exc}")
            return None
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _download(self, *, apply: bool) -> None:
        row = self._current_row()
        if row is None or not row.get("downloadable"):
            return
        if self._download_worker is not None and self._download_worker.isRunning():
            return
        provider_id = str(row.get("provider") or "")
        dest = self._dest_dir(provider_id)
        if dest is None:
            dest = paths.data_dir() / "artresources" / "downloads" / provider_id
            dest.mkdir(parents=True, exist_ok=True)
        self.download_btn.setEnabled(False)
        self.apply_btn.setEnabled(False)
        self._set_status(f"正在下载 {row.get('title')}…")
        worker = _DownloadWorker(provider_id, str(row.get("id") or ""), dest, self)
        worker.finished_path.connect(
            lambda path, error: self._download_done(path, error, row, apply)
        )
        self._download_worker = worker
        worker.start()

    def _download_done(
        self, path: str, error: str, row: dict[str, Any], apply: bool
    ) -> None:
        self._download_worker = None
        self.download_btn.setEnabled(True)
        if error:
            self._set_status(f"下载失败：{error}")
            QMessageBox.warning(self, "下载失败", error)
            return
        saved = Path(path)
        message = f"已下载：{saved}"
        if apply and self.apply_callback is not None:
            try:
                message = self.apply_callback(saved, row)
            except Exception as exc:  # noqa: BLE001
                message = f"已下载到 {saved}，但应用到条目失败：{exc}"
        self._set_status(message)
        self._on_current(self.list.currentItem(), None)
