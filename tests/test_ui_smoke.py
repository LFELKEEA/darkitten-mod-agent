from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from .base import TempDataCase


class UiSmokeTests(TempDataCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("DMA_ENV_AUTORUN", "0")
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_builds(self) -> None:
        from dma.ui import theme
        from dma.ui.main_window import MainWindow

        theme.apply_theme(self.app, 13)
        window = MainWindow()
        window.show()
        self.app.processEvents()
        self.assertEqual(window.stack.count(), 7)
        self.assertIsNotNone(window.chat_page)
        self.assertIsNotNone(window.build_page)
        self.assertIsNotNone(window.environment_page)
        self.assertFalse(window.build_page.stop_btn.isEnabled())
        self.assertFalse(window.build_page.assist_btn.isEnabled())
        self.assertFalse(window.build_page.wrapper_btn.isEnabled())
        self.assertFalse(window.build_page.prepare_btn.isEnabled())
        self.assertFalse(window.build_page.cert_box.isChecked())
        self.assertIsNotNone(window.environment_page.prepare_btn)
        self.assertIsNotNone(window.chat_page.delete_btn)
        self.assertFalse(window.chat_page.delete_btn.isEnabled())
        self.assertIsNotNone(window.knowledge_page)
        self.assertTrue(window.knowledge_page.import_btn.isEnabled())
        self.assertFalse(window.knowledge_page.import_scope.isEnabled())
        for index in range(window.stack.count()):
            window.stack.setCurrentIndex(index)
            self.app.processEvents()
        window.chat_page.chat.add_message("assistant", "**测试**\n\n```java\nclass A {}\n```")
        self.app.processEvents()
        self.assertEqual(len(window.chat_page.chat.bubbles), 2)
        window.close()
        window.memory.close()
        self.app.processEvents()

    def test_pending_panel_shows_auto_written_files(self) -> None:
        from dma.agent import PendingFile
        from dma.ui import theme
        from dma.ui.main_window import MainWindow

        theme.apply_theme(self.app, 13)
        window = MainWindow()
        window.config.auto_apply = True
        window.chat_page._set_pending(
            [
                PendingFile(
                    path="src/main/java/A.java",
                    content="class A {}",
                    applied=True,
                )
            ]
        )
        page = window.chat_page
        self.assertEqual(page.pending_tree.topLevelItemCount(), 1)
        self.assertEqual(page.pending_tree.topLevelItem(0).text(1), "已自动写入")
        self.assertFalse(page.apply_btn.isEnabled())
        self.assertTrue(page.discard_btn.isEnabled())
        self.assertIn("自动写入", page.pending_hint.text())
        window.close()
        window.memory.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
