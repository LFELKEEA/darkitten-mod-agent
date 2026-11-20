from __future__ import annotations

import unittest

from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.memory import MemoryStore
from dma.skills import SkillStore

from .base import TempDataCase


class AgentScaffoldToolTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.memory = MemoryStore()

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def _worker(self, project=None) -> AgentWorker:
        config = Config()
        config.ai.provider = "pollinations"
        return AgentWorker(
            user_text="新建一个 Forge 模组",
            history=[],
            project=project,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
        )

    def test_scaffold_without_project_creates_forge_skeleton(self) -> None:
        worker = self._worker(project=None)
        events: list[str] = []
        worker.project_created.connect(events.append)
        target = self.tmp / "projects" / "demo_mod"
        text = worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={
                    "mod_id": "demo_mod",
                    "name": "Demo Mod",
                    "package": "com.example.demo",
                    "dest": str(target),
                },
            ),
            AgentRun(),
        )
        self.assertIn("Forge 工程已创建", text)
        self.assertTrue((target / "build.gradle").is_file())
        self.assertTrue(
            (target / "src/main/java/com/example/demo/ModMain.java").is_file()
        )
        self.assertIsNotNone(worker.project)
        self.assertEqual(worker.project.meta.loader, "Forge")
        self.assertEqual(events, [str(target)])
        # 工程创建后任务图必须绑到真实目录，否则 workflow_* 只在内存里自嗨。
        self.assertIsNotNone(worker.kernel)
        self.assertEqual(worker.kernel.root, target.resolve())
        self.assertTrue(worker._project_dirty)

    def test_scaffold_refuses_existing_project_without_overwrite(self) -> None:
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        first = worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={"mod_id": "demo_mod", "dest": str(target)},
            ),
            AgentRun(),
        )
        self.assertIn("Forge 工程已创建", first)
        second = worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={"mod_id": "demo_mod", "dest": str(target)},
            ),
            AgentRun(),
        )
        self.assertIn("overwrite=true", second)

    def test_write_file_reports_unchanged_content(self) -> None:
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={"mod_id": "demo_mod", "dest": str(target)},
            ),
            AgentRun(),
        )
        call = ToolCall(
            name="write_file",
            args={"path": "src/main/java/com/example/demo/Note.java", "content": "package com.example.demo;\n\npublic final class Note {}\n"},
        )
        first = worker._execute(call, AgentRun())
        self.assertIn("已自动写入", first)
        second = worker._execute(call, AgentRun())
        self.assertIn("内容未变化", second)
        self.assertIn("不要重复写入", second)

    def test_edit_file_rejects_identical_old_and_new(self) -> None:
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={"mod_id": "demo_mod", "dest": str(target)},
            ),
            AgentRun(),
        )
        text = worker._execute(
            ToolCall(
                name="edit_file",
                args={
                    "path": "src/main/java/com/example/demo/ModMain.java",
                    "old": "package com.example.demo;",
                    "new": "package com.example.demo;",
                },
            ),
            AgentRun(),
        )
        self.assertIn("完全相同", text)
        self.assertIn("不会有任何变化", text)

    def test_edit_file_failure_shows_real_excerpt(self) -> None:
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={
                    "mod_id": "demo_mod",
                    "package": "com.example.demo",
                    "dest": str(target),
                },
            ),
            AgentRun(),
        )
        text = worker._execute(
            ToolCall(
                name="edit_file",
                args={
                    "path": "src/main/java/com/example/demo/ModMain.java",
                    "old": "public final class ModMain {\n    public static final String MODIDX = \"demo_mod\";",
                    "new": "public final class ModMain {\n    public static final String MODID = \"demo_mod\";",
                },
            ),
            AgentRun(),
        )
        self.assertIn("没有在", text)
        self.assertIn("文件里对应位置实际是", text)
        self.assertIn("public final class ModMain {", text)
        self.assertIn('MODID = "demo_mod";', text)

    def test_write_file_rejects_numbered_duplicate(self) -> None:
        """修不好就新建 Note2.java 会让同一个类出现多份副本，必须拒绝。"""
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={
                    "mod_id": "demo_mod",
                    "package": "com.example.demo",
                    "dest": str(target),
                },
            ),
            AgentRun(),
        )
        original = "src/main/java/com/example/demo/Note.java"
        worker._execute(
            ToolCall(
                name="write_file",
                args={"path": original, "content": "package com.example.demo;\n\npublic final class Note {}\n"},
            ),
            AgentRun(),
        )
        text = worker._execute(
            ToolCall(
                name="write_file",
                args={
                    "path": "src/main/java/com/example/demo/Note2.java",
                    "content": "package com.example.demo;\n\npublic final class Note2 {}\n",
                },
            ),
            AgentRun(),
        )
        self.assertIn("不要新建副本文件", text)
        self.assertIn("Note.java", text)

    def test_edit_file_blocks_back_and_forth_toggle(self) -> None:
        """A→B 后再 B→A 必须被拦截，否则模型会在两种写法间无限切换。"""
        worker = self._worker(project=None)
        target = self.tmp / "projects" / "demo_mod"
        worker._execute(
            ToolCall(
                name="scaffold_forge_project",
                args={
                    "mod_id": "demo_mod",
                    "package": "com.example.demo",
                    "dest": str(target),
                },
            ),
            AgentRun(),
        )
        path = "src/main/java/com/example/demo/Note.java"
        worker._execute(
            ToolCall(
                name="write_file",
                args={
                    "path": path,
                    "content": (
                        "package com.example.demo;\n\n"
                        "public final class Note {\n"
                        "    static final String TAG = \"net.minecraft.tags.BlockTags\";\n"
                        "}\n"
                    ),
                },
            ),
            AgentRun(),
        )
        forward = worker._execute(
            ToolCall(
                name="edit_file",
                args={
                    "path": path,
                    "old": "net.minecraft.tags.BlockTags",
                    "new": "net.minecraft.world.level.block.BlockTags",
                },
            ),
            AgentRun(),
        )
        self.assertIn("已自动写入", forward)
        backward = worker._execute(
            ToolCall(
                name="edit_file",
                args={
                    "path": path,
                    "old": "net.minecraft.world.level.block.BlockTags",
                    "new": "net.minecraft.tags.BlockTags",
                },
            ),
            AgentRun(),
        )
        self.assertIn("来回震荡", backward)
        self.assertIn("build_project", backward)

    def test_scenario_dry_run_translates_steps(self) -> None:
        worker = self._worker(project=None)
        text = worker._execute(
            ToolCall(
                name="mcdev_scenario",
                args={"steps": [{"type": "status", "side": "server"}], "dry_run": True},
            ),
            AgentRun(),
        )
        self.assertIn('"dryRun": true', text)
        self.assertIn('"type": "status"', text)


if __name__ == "__main__":
    unittest.main()
