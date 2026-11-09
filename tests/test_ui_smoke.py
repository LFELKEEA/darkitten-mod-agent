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
        # 测试里不弹首次启动的快捷方式对话框，也不去联网检查更新。
        window.config.shortcut_offered = True
        window.config.update_url = ""
        window.show()
        self.app.processEvents()
        self.assertEqual(window.stack.count(), 10)
        self.assertIsNotNone(window.chat_page)
        self.assertIsNotNone(window.studio_page)
        self.assertIsNotNone(window.studio_page.generate_btn)
        self.assertIsNotNone(window.tutorial_page)
        self.assertIsNotNone(window.build_page)
        self.assertIsNotNone(window.environment_page)
        self.assertFalse(window.build_page.build_btn.isEnabled())
        self.assertFalse(window.build_page.gametest_btn.isEnabled())
        self.assertFalse(window.build_page.libs_btn.isEnabled())
        self.assertIsNotNone(window.build_page.run_panel)
        self.assertIsNotNone(window.build_page.artifact_panel.view)
        self.assertIsNotNone(window.environment_page.prepare_btn)
        self.assertIsNotNone(window.chat_page.delete_btn)
        self.assertFalse(window.chat_page.delete_btn.isEnabled())
        self.assertIsNotNone(window.chat_page.auto_apply_box)
        self.assertIsNotNone(window.chat_page.auto_build_box)
        self.assertIsNotNone(window.chat_page.continue_btn)
        self.assertEqual(window.chat_page.side_tabs.count(), 3)
        self.assertIsNotNone(window.chat_page.run_panel)
        self.assertTrue(window.chat_page.run_panel.server_btn.isEnabled())
        self.assertIsNotNone(window.chat_page.artifact_panel.view)
        window.go_to(window.studio_page)
        self.assertIs(window.stack.currentWidget(), window.studio_page)
        window.go_to(window.tutorial_page)
        self.assertIs(window.stack.currentWidget(), window.tutorial_page)
        window.go_to(window.build_page)
        self.assertIs(window.stack.currentWidget(), window.build_page)
        self.assertIsNotNone(window.knowledge_page)
        self.assertIsNotNone(window.memory_page)
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
        # 测试里不弹首次启动的快捷方式对话框，也不去联网检查更新。
        window.config.shortcut_offered = True
        window.config.update_url = ""
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

    def test_old_dsml_markup_is_cleaned_when_loading_project(self) -> None:
        from PySide6.QtWidgets import QTextBrowser

        from dma import sessions
        from dma.project import create_project
        from dma.ui import theme
        from dma.ui.main_window import MainWindow

        project = create_project(
            self.tmp,
            name="清理测试",
            mod_id="dsmlclean",
            mc_version="1.21.1",
            loader="NeoForge",
        )
        sessions.save_history(
            project.root,
            [
                {"role": "user", "content": "继续"},
                {
                    "role": "assistant",
                    "content": (
                        '< | DSLM | calls > < | DSLM | invoke name="tool">'
                        '{"tool":"read_file","path":"A.java"}'
                        "</| DSML | parameter> </| DSML | invoke> </| DSML | calls>"
                    ),
                },
                {"role": "assistant", "content": "读完了。"},
            ],
        )
        theme.apply_theme(self.app, 13)
        window = MainWindow()
        # 测试里不弹首次启动的快捷方式对话框，也不去联网检查更新。
        window.config.shortcut_offered = True
        window.config.update_url = ""
        page = window.chat_page
        page._set_project(project, quiet=True)
        self.assertEqual(
            [item["content"] for item in page.history], ["继续", "读完了。"]
        )
        shown = [
            view.toPlainText()
            for bubble in page.chat.bubbles
            for view in bubble.findChildren(QTextBrowser)
        ]
        self.assertFalse(any("DSML" in text or "DSLM" in text for text in shown))
        self.assertEqual(
            [item["content"] for item in sessions.load_history(project.root)],
            ["继续", "读完了。"],
        )
        window.close()
        window.memory.close()
        self.app.processEvents()

    def test_selecting_project_enables_build_buttons(self) -> None:
        from dma.project import create_project
        from dma.ui import theme
        from dma.ui.main_window import MainWindow

        project = create_project(
            self.tmp,
            name="编译按钮测试",
            mod_id="buildbtn",
            mc_version="1.20.1",
            loader="Forge",
        )
        theme.apply_theme(self.app, 13)
        window = MainWindow()
        window.config.shortcut_offered = True
        window.config.update_url = ""
        window.chat_page._set_project(project, quiet=True)
        self.app.processEvents()
        self.assertIs(window.build_page.main.active_project, project)
        for button in (
            window.build_page.build_btn,
            window.build_page.gametest_btn,
            window.build_page.libs_btn,
        ):
            self.assertTrue(button.isEnabled(), button.text())
        self.assertTrue(window.build_page.run_panel.server_btn.isEnabled())
        window.close()
        window.memory.close()
        self.app.processEvents()

    def test_delete_project_checks_build_busy_without_runner_attribute(self) -> None:
        """删除工程前的占用检查要读 BuildPage 的真实状态。

        回归的是 chat_page 里 ``build.runner.running`` 的旧写法：BuildPage
        没有 runner 字段，点“删除工程”会直接抛 AttributeError。
        """
        from unittest import mock

        from dma.project import create_project
        from dma.ui import theme
        from dma.ui import chat_page as chat_page_mod
        from dma.ui.main_window import MainWindow

        project = create_project(
            self.tmp,
            name="删除占用测试",
            mod_id="delbusy",
            mc_version="1.20.1",
            loader="Forge",
        )
        theme.apply_theme(self.app, 13)
        window = MainWindow()
        window.config.shortcut_offered = True
        window.config.update_url = ""
        page = window.chat_page
        page._set_project(project, quiet=True)
        self.assertFalse(window.build_page.is_busy())
        window.build_page._run_busy = True
        self.assertTrue(window.build_page.is_busy())
        with mock.patch.object(chat_page_mod.QMessageBox, "information") as info:
            page.delete_project()
        self.assertEqual(info.call_count, 1)
        self.assertIn("编译进行中", info.call_args.args[1])
        self.assertTrue(project.root.is_dir())
        window.close()
        window.memory.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
