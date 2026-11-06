from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from dma.config import AIConfig, Config
from dma.ui.settings_page import SettingsPage

from .base import TempDataCase


class _FakeMain:
    def __init__(self, config: Config) -> None:
        self.config = config

    def save_config(self) -> None:
        pass

    def apply_theme(self) -> None:
        pass

    def status(self, text: str) -> None:
        pass


class SettingsPageTests(TempDataCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_custom_base_url_is_preserved(self) -> None:
        config = Config().normalized()
        config.ai = AIConfig(
            provider="openai",
            base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
            model="deepseek-v4.1-flash-1017",
            api_key="sk-test",
        ).normalized()
        page = SettingsPage(_FakeMain(config))
        self.assertEqual(page.base_url.text(), config.ai.base_url)

        page.provider.setCurrentIndex(page.provider.findData("ollama"))
        page.provider.setCurrentIndex(page.provider.findData("openai"))
        self.assertEqual(page.base_url.text(), config.ai.base_url)
        self.assertEqual(page.model.text(), config.ai.model)
        page.close()

    def test_save_keeps_custom_base_url(self) -> None:
        config = Config().normalized()
        config.ai = AIConfig(
            provider="openai",
            base_url="https://maas.qianwenaiapi.com/compatible-mode/v1",
            model="deepseek-v4.1-flash-1017",
            api_key="sk-test",
        ).normalized()
        page = SettingsPage(_FakeMain(config))
        page.base_url.setText("https://example.com/compatible-mode/v1")
        page.save()
        self.assertEqual(config.ai.base_url, "https://example.com/compatible-mode/v1")
        self.assertEqual(page.base_url.text(), "https://example.com/compatible-mode/v1")
        page.close()

    def test_empty_base_url_gets_default(self) -> None:
        config = Config().normalized()
        config.ai = AIConfig(
            provider="openai", base_url="", model="gpt-4o-mini", api_key="sk-test"
        ).normalized()
        page = SettingsPage(_FakeMain(config))
        self.assertEqual(page.base_url.text(), "https://api.openai.com/v1")
        page.close()


if __name__ == "__main__":
    unittest.main()
