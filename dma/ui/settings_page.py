from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import ai, paths, shortcut, updater
from ..config import PROVIDERS, AIConfig
from ..version import APP_NAME_CN, APP_VERSION, GITHUB_URL, SITE_URL
from .update_dialog import UpdateDialog
from .widgets import card, tiny


class _AITestWorker(QObject):
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, cfg: AIConfig) -> None:
        super().__init__()
        self.cfg = cfg

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            text = ai.test_connection(self.cfg)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            self.done.emit(text)


class _UpdateCheckWorker(QObject):
    found = Signal(object)
    none = Signal()
    failed = Signal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self.url = url

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            info = updater.check_for_update(self.url)
        except Exception as exc:
            self.failed.emit(str(exc))
        else:
            if info is None:
                self.none.emit()
            else:
                self.found.emit(info)


class SettingsPage(QWidget):
    def __init__(self, main, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.main = main
        self._test_worker: _AITestWorker | None = None
        self._check_worker: _UpdateCheckWorker | None = None
        self._build()
        self.load()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        root = QVBoxLayout(host)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)
        scroll.setWidget(host)
        outer.addWidget(scroll)

        head = QLabel("设置")
        head.setObjectName("H1")
        root.addWidget(head)

        # AI ---------------------------------------------------------
        ai_card = card()
        ai_layout = QVBoxLayout(ai_card)
        ai_layout.setContentsMargins(16, 14, 16, 14)
        ai_layout.setSpacing(8)
        ai_title = QLabel("模型通道")
        ai_title.setObjectName("H2")
        ai_layout.addWidget(ai_title)
        ai_layout.addWidget(
            tiny("免费公共通道开箱可用；需要更稳定输出时切换到 OpenAI 兼容接口或本地 Ollama。")
        )
        form = QFormLayout()
        form.setSpacing(9)
        self.provider = QComboBox()
        for key, label in PROVIDERS.items():
            self.provider.addItem(label, key)
        self.base_url = QLineEdit()
        self.base_url.setPlaceholderText(
            "服务商提供的 OpenAI 兼容地址，例如 https://maas.qianwenaiapi.com/compatible-mode/v1"
        )
        self.base_url.setToolTip(
            "填服务商控制台给出的 Base URL。已经填了自定义地址时，切换通道不会改掉它。"
        )
        self.model = QLineEdit()
        self.api_key = QLineEdit()
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("sk-…（只保存在本机 data/config.json）")
        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 1.5)
        self.temperature.setSingleStep(0.05)
        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(512, 32768)
        self.max_tokens.setSingleStep(512)
        self.timeout = QSpinBox()
        self.timeout.setRange(30, 600)
        self.timeout.setSingleStep(10)

        form.addRow("通道", self.provider)
        form.addRow("Base URL", self.base_url)
        form.addRow("模型", self.model)
        form.addRow("API Key", self.api_key)
        form.addRow("温度", self.temperature)
        form.addRow("最大输出", self.max_tokens)
        form.addRow("超时（秒）", self.timeout)
        ai_layout.addLayout(form)
        ai_row = QHBoxLayout()
        self.test_result = tiny("")
        ai_row.addWidget(self.test_result, 1)
        self.test_btn = QPushButton("测试连接")
        self.test_btn.setObjectName("Ghost")
        self.test_btn.clicked.connect(self._test_ai)
        ai_row.addWidget(self.test_btn)
        ai_layout.addLayout(ai_row)
        root.addWidget(ai_card)

        # Behavior ---------------------------------------------------
        behavior = card()
        behavior_layout = QVBoxLayout(behavior)
        behavior_layout.setContentsMargins(16, 14, 16, 14)
        behavior_layout.setSpacing(8)
        behavior_title = QLabel("工作方式")
        behavior_title.setObjectName("H2")
        behavior_layout.addWidget(behavior_title)
        form2 = QFormLayout()
        form2.setSpacing(9)
        root_row = QWidget()
        root_layout = QHBoxLayout(root_row)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(6)
        self.project_root = QLineEdit()
        pick = QPushButton("选择…")
        pick.clicked.connect(self._pick_root)
        root_layout.addWidget(self.project_root, 1)
        root_layout.addWidget(pick)
        self.auto_apply = QCheckBox("生成后不询问，直接写入工程文件（仍会备份）")
        self.max_steps = QSpinBox()
        self.max_steps.setRange(0, 999)
        self.max_steps.setSpecialValueText("不限制")
        self.max_steps.setToolTip("0 表示不限制工具调用轮数；大工程建议 32 以上。")
        self.context_turns = QSpinBox()
        self.context_turns.setRange(2, 200)
        self.context_turns.setToolTip(
            "助手每轮会带上最近多少轮对话。默认 32，最大 200；调大后更不容易忘事，但请求会更大。"
        )
        self.font_size = QSpinBox()
        self.font_size.setRange(11, 20)
        form2.addRow("工程根目录", root_row)
        form2.addRow("工具轮数上限", self.max_steps)
        form2.addRow("上下文轮数", self.context_turns)
        form2.addRow("界面字号", self.font_size)
        behavior_layout.addLayout(form2)
        behavior_layout.addWidget(self.auto_apply)
        behavior_layout.addWidget(
            tiny(
                "默认关闭自动写入：文件先进入右侧待确认列表。"
                "开启后直接写入工程，并在列表里留下“已自动写入”记录，仍会备份。"
            )
        )
        behavior_layout.addWidget(
            tiny("上下文轮数默认 32，最多 200；调大后助手更不容易忘事，但单次请求也会更大。")
        )
        behavior_layout.addWidget(
            tiny("工具轮数上限填 0 表示不限制。大工程建议 32～64；不限制时请留意模型费用和输出时间。")
        )
        root.addWidget(behavior)

        # About ------------------------------------------------------
        about = card()
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(16, 14, 16, 14)
        about_layout.setSpacing(8)
        about_title = QLabel("版本与更新")
        about_title.setObjectName("H2")
        about_layout.addWidget(about_title)
        self.version_label = tiny(f"{APP_NAME_CN} v{APP_VERSION}")
        about_layout.addWidget(self.version_label)
        self.update_label = tiny(f"更新源：{updater.UPDATE_MANIFEST_URL}")
        about_layout.addWidget(self.update_label)
        about_row = QHBoxLayout()
        self.check_btn = QPushButton("检查更新")
        self.check_btn.setObjectName("Primary")
        self.check_btn.clicked.connect(self._check_update)
        shortcut_btn = QPushButton("创建桌面快捷方式")
        shortcut_btn.setObjectName("Ghost")
        shortcut_btn.clicked.connect(self._create_shortcut)
        data_btn = QPushButton("打开数据目录")
        data_btn.setObjectName("Ghost")
        data_btn.clicked.connect(self._open_data)
        site_btn = QPushButton("下载站")
        site_btn.setObjectName("Ghost")
        site_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SITE_URL)))
        github_btn = QPushButton("GitHub")
        github_btn.setObjectName("Ghost")
        github_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        for button in (self.check_btn, shortcut_btn, data_btn, site_btn, github_btn):
            about_row.addWidget(button)
        about_row.addStretch(1)
        about_layout.addLayout(about_row)
        about_layout.addWidget(
            tiny(
                "自动更新会下载 ZIP、校验 SHA-256，再在退出后用后台脚本替换程序文件；"
                "工程目录、技能、连接器、知识库都在 data 下，不会被覆盖。"
            )
        )
        root.addWidget(about)

        save_row = QHBoxLayout()
        self.saved_label = tiny("")
        save_row.addWidget(self.saved_label, 1)
        save_btn = QPushButton("保存设置")
        save_btn.setObjectName("Primary")
        save_btn.clicked.connect(self.save)
        save_row.addWidget(save_btn)
        root.addLayout(save_row)
        root.addStretch(1)

        self.provider.currentIndexChanged.connect(self._provider_changed)

    # ------------------------------------------------------------------
    def load(self) -> None:
        config = self.main.config
        ai_cfg = config.ai
        index = self.provider.findData(ai_cfg.provider)
        self.provider.setCurrentIndex(max(0, index))
        self.base_url.setText(ai_cfg.base_url)
        self.model.setText(ai_cfg.model)
        self.api_key.setText(ai_cfg.api_key)
        self.temperature.setValue(float(ai_cfg.temperature))
        self.max_tokens.setValue(int(ai_cfg.max_tokens))
        self.timeout.setValue(int(ai_cfg.timeout))
        self.project_root.setText(config.project_root)
        self.auto_apply.setChecked(bool(config.auto_apply))
        self.max_steps.setValue(int(config.max_steps))
        self.context_turns.setValue(int(config.context_turns))
        self.font_size.setValue(int(config.font_size))
        self.version_label.setText(
            f"{APP_NAME_CN} v{APP_VERSION}"
            + ("（打包版）" if updater.is_frozen() else "（源码运行）")
        )
        self._provider_changed()

    def _provider_changed(self) -> None:
        provider = self.provider.currentData()
        is_free = provider == "pollinations"
        self.base_url.setEnabled(not is_free)
        self.api_key.setEnabled(provider == "openai")
        current = self.base_url.text().strip().rstrip("/")
        is_default_openai = current == "https://api.openai.com/v1"
        is_local = current.startswith(("http://127.0.0.1", "http://localhost"))
        if is_free:
            if not current:
                self.base_url.setText("https://api.openai.com/v1")
            if not self.model.text().strip() or ":" in self.model.text():
                self.model.setText("openai-fast")
        elif provider == "ollama":
            if not current or is_default_openai:
                self.base_url.setText("http://127.0.0.1:11434/v1")
            if "fast" in self.model.text() or not self.model.text().strip():
                self.model.setText("qwen2.5-coder:7b")
        else:
            # 自定义中转/兼容地址必须原样保留，否则用户每次打开设置都会被改回官方地址。
            if not current or is_local:
                self.base_url.setText("https://api.openai.com/v1")
            if self.model.text().strip() in {"", "openai-fast"} or ":" in self.model.text():
                self.model.setText("gpt-4o-mini")

    def _pick_root(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择工程根目录", self.project_root.text())
        if folder:
            self.project_root.setText(folder)

    def _collect_ai(self) -> AIConfig:
        return AIConfig(
            provider=str(self.provider.currentData() or "pollinations"),
            base_url=self.base_url.text().strip(),
            model=self.model.text().strip(),
            api_key=self.api_key.text().strip(),
            temperature=self.temperature.value(),
            max_tokens=self.max_tokens.value(),
            timeout=self.timeout.value(),
        ).normalized()

    def save(self) -> None:
        config = self.main.config
        config.ai = self._collect_ai()
        config.project_root = self.project_root.text().strip() or str(paths.default_project_root())
        config.auto_apply = self.auto_apply.isChecked()
        config.max_steps = self.max_steps.value()
        config.context_turns = self.context_turns.value()
        config.font_size = self.font_size.value()
        self.main.save_config()
        self.main.apply_theme()
        self.saved_label.setText("已保存")
        self.main.status("设置已保存")
        self.load()

    # ------------------------------------------------------------------
    def _test_ai(self) -> None:
        if self._test_worker is not None:
            return
        self.test_btn.setEnabled(False)
        self.test_result.setText("正在请求…")
        worker = _AITestWorker(self._collect_ai())
        self._test_worker = worker
        worker.done.connect(self._on_test_ok)
        worker.failed.connect(self._on_test_fail)
        worker.start()

    def _on_test_ok(self, text: str) -> None:
        self._test_worker = None
        self.test_btn.setEnabled(True)
        self.test_result.setText(f"连接成功：{text or 'OK'}")
        self.main.status("模型连接成功")

    def _on_test_fail(self, message: str) -> None:
        self._test_worker = None
        self.test_btn.setEnabled(True)
        self.test_result.setText(f"连接失败：{message}")
        self.main.status("模型连接失败")

    # ------------------------------------------------------------------
    def _check_update(self) -> None:
        if self._check_worker is not None:
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("检查中…")
        worker = _UpdateCheckWorker(self.main.config.update_url)
        self._check_worker = worker
        worker.found.connect(self._on_update_found)
        worker.none.connect(self._on_update_none)
        worker.failed.connect(self._on_update_failed)
        worker.start()

    def _reset_check(self) -> None:
        self._check_worker = None
        self.check_btn.setEnabled(True)
        self.check_btn.setText("检查更新")

    def _on_update_found(self, info) -> None:
        self._reset_check()
        self.update_label.setText(f"发现新版本 {info.version}")
        UpdateDialog(self.main, info, self).exec()

    def _on_update_none(self) -> None:
        self._reset_check()
        self.update_label.setText(f"已是最新版本 v{APP_VERSION}")
        self.main.status("已是最新版本")

    def _on_update_failed(self, message: str) -> None:
        self._reset_check()
        self.update_label.setText(f"检查失败：{message}")
        self.main.status(f"检查更新失败：{message}")

    def _create_shortcut(self) -> None:
        try:
            target = shortcut.create_desktop_shortcut()
        except Exception as exc:
            QMessageBox.warning(self, "创建失败", str(exc))
            return
        self.main.status(f"已创建桌面快捷方式：{target}")

    def _open_data(self) -> None:
        try:
            os.startfile(str(paths.data_dir()))
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", str(exc))
