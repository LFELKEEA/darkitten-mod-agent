from __future__ import annotations

import unittest
from unittest import mock

from dma import mcdev
from dma.agent import (
    AgentRun,
    AgentWorker,
    BuildOutcome,
    ToolCall,
    _build_report,
    _tool_status,
)
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.memory import MemoryStore
from dma.project import create_project
from dma.skills import SkillStore

from .base import TempDataCase


class BuildToolTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Test Mod",
            mod_id="test_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.memory = MemoryStore()
        config = Config()
        config.ai.provider = "pollinations"
        self.worker = AgentWorker(
            user_text="编译一下",
            history=[],
            project=self.project,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
        )

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def test_build_tool_returns_report(self) -> None:
        libs = self.project.root / "build" / "libs"
        libs.mkdir(parents=True)
        (libs / "test_mod-1.0.0.jar").write_bytes(b"x" * 1234)
        with mock.patch(
            "dma.mcdev.gradle.run_gradle",
            return_value=(0, "BUILD SUCCESSFUL in 6s"),
        ) as run:
            text = self.worker._execute(
                ToolCall(name="build_project", args={"task": "build"}), AgentRun()
            )
        self.assertIn("编译成功", text)
        self.assertIn("test_mod-1.0.0.jar", text)
        self.assertIn("BUILD SUCCESSFUL", text)
        self.assertEqual(run.call_args.args[0], self.project.root)
        self.assertEqual(run.call_args.args[1], "build")
        self.assertEqual(run.call_args.kwargs["timeout"], 900)
        self.assertIs(run.call_args.kwargs["cancel"], self.worker._cancel)

    def test_build_tool_clamps_timeout(self) -> None:
        with mock.patch(
            "dma.mcdev.gradle.run_gradle", return_value=(0, "BUILD SUCCESSFUL")
        ) as run:
            self.worker._execute(
                ToolCall(
                    name="build_project",
                    args={"task": "runGameTestServer", "timeout": 5},
                ),
                AgentRun(),
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 60)
            self.worker._execute(
                ToolCall(name="build_project", args={"timeout": 99999}), AgentRun()
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 1800)

    def test_build_tool_rejects_long_running_task(self) -> None:
        with mock.patch("dma.mcdev.gradle.run_gradle") as run:
            text = self.worker._execute(
                ToolCall(
                    name="build_project",
                    args={"task": "runServer", "timeout": 900},
                ),
                AgentRun(),
            )
        run.assert_not_called()
        self.assertIn("mcdev_prepare", text)

    def test_build_tool_reports_start_failure(self) -> None:
        with mock.patch(
            "dma.mcdev.gradle.run_gradle",
            side_effect=mcdev.gradle.GradleError("工程里没有 gradlew"),
        ):
            text = self.worker._execute(
                ToolCall(name="build_project", args={}), AgentRun()
            )
        self.assertIn("编译没有开始", text)
        self.assertIn("gradlew", text)

    def test_unknown_tool_lists_build_project(self) -> None:
        text = self.worker._execute(ToolCall(name="not_a_tool", args={}), AgentRun())
        self.assertIn("build_project", text)
        self.assertNotIn("fix_build_setup", text)

    def test_status_text(self) -> None:
        status = _tool_status(ToolCall(name="build_project", args={"task": "runClient"}))
        self.assertIn("runClient", status)


class BuildReportTests(unittest.TestCase):
    def test_prompt_requires_self_build(self) -> None:
        from dma.prompt import TOOL_PROTOCOL

        self.assertIn('"tool":"build_project"', TOOL_PROTOCOL)
        self.assertIn('"tool":"mcdev_prepare"', TOOL_PROTOCOL)
        self.assertIn("以真实产物为准", TOOL_PROTOCOL)
        self.assertNotIn("fix_build_setup", TOOL_PROTOCOL)

    def test_failure_keeps_tail_and_marks_truncation(self) -> None:
        result = BuildOutcome(ok=False, code=1, output="x" * 30000)
        text = _build_report(result)
        self.assertIn("编译失败（退出码 1）", text)
        self.assertIn("只保留末尾部分", text)
        self.assertLessEqual(len(text), 24200)

    def test_success_without_jars_explains(self) -> None:
        result = BuildOutcome(ok=True, code=0, output="BUILD SUCCESSFUL")
        text = _build_report(result)
        self.assertIn("没有在 build/libs 里找到 jar", text)


if __name__ == "__main__":
    unittest.main()
