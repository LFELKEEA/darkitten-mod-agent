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
