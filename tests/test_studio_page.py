from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("DMA_DATA_DIR", str(Path(__file__).resolve().parent.parent / ".smoke_data_studio"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class StudioPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        from dma.ui import theme
        from dma.ui.main_window import MainWindow

        cls.app = QApplication.instance() or QApplication([])
        theme.apply_theme(cls.app, 14)
        cls.window = MainWindow()
        # 测试里不弹快捷方式对话框，也不联网检查更新。
        cls.window.config.shortcut_offered = True
        cls.window.config.update_url = ""
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.close()
        cls.window.memory.close()
        cls.app.processEvents()

    def test_page_registered(self) -> None:
        labels = [self.window.group.button(i).text() for i in range(self.window.stack.count())]
        self.assertIn("快速生成", labels)
        self.assertEqual(self.window.stack.currentIndex(), 0)
        index = labels.index("快速生成")
        self.window.go_to(self.window.studio_page)
        self.assertEqual(self.window.stack.currentIndex(), index)

    def test_plan_preview_values(self) -> None:
        page = self.window.studio_page
        page.idea.setPlainText("深海遗迹、发光珊瑚矿、潜水装备")
        page.loader.setCurrentText("Forge")
        page.mc.setCurrentText("1.20.1")
        values = page._form_values()
        self.assertEqual(values["loader"], "Forge")
        self.assertEqual(values["mc"], "1.20.1")
        self.assertEqual(values["scope"], "medium")

    def test_plan_accepts_idea(self) -> None:
        from dma import studio_bridge

        spec = studio_bridge.plan("深海遗迹与潮汐能机器", "Fabric", "1.21.1", scope="small")
        self.assertEqual(spec["target"]["loader"], "fabric")
        self.assertGreaterEqual(len(spec["content"]), 12)
        self.assertIn("identity", spec)


if __name__ == "__main__":
    unittest.main()
