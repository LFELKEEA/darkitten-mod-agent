from __future__ import annotations

import unittest
from unittest import mock

from dma import builder
from dma.agent import AgentRun, AgentWorker, ToolCall, _build_report, _tool_status
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
            mc_version="1.21.1",
            loader="NeoForge",
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
            stage="building",
        )

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def test_build_tool_returns_report(self) -> None:
        result = builder.BuildResult(
            ok=True,
            code=0,
            output="BUILD SUCCESSFUL in 6s",
            jars=[("build/libs/test_mod-1.0.0.jar", 1234)],
        )
        with mock.patch("dma.agent.builder.build_project", return_value=result) as run:
            text = self.worker._execute(
                ToolCall(name="build_project", args={"task": "build"}), AgentRun()
            )
        self.assertIn("编译成功", text)
        self.assertIn("test_mod-1.0.0.jar", text)
        self.assertIn("BUILD SUCCESSFUL", text)
        kwargs = run.call_args.kwargs
        self.assertEqual(run.call_args.args[0], self.project.root)
        self.assertEqual(run.call_args.args[1], "build")
        self.assertEqual(kwargs["timeout"], 1800.0)
        self.assertIs(kwargs["cancel"], self.worker._cancel)

    def test_build_tool_clamps_timeout(self) -> None:
        result = builder.BuildResult(ok=True, code=0)
        with mock.patch("dma.agent.builder.build_project", return_value=result) as run:
            self.worker._execute(
                ToolCall(name="build_project", args={"task": "runClient", "timeout": 5}),
                AgentRun(),
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 60.0)
            self.worker._execute(
                ToolCall(name="build_project", args={"timeout": 99999}), AgentRun()
            )
            self.assertEqual(run.call_args.kwargs["timeout"], 3600.0)

    def test_build_tool_reports_start_failure(self) -> None:
        with mock.patch(
            "dma.agent.builder.build_project",
            side_effect=builder.BuilderError("工程里没有 build.gradle"),
        ):
            text = self.worker._execute(
                ToolCall(name="build_project", args={}), AgentRun()
            )
        self.assertIn("编译没有开始", text)
        self.assertIn("build.gradle", text)

    def test_unknown_tool_lists_build_project(self) -> None:
        text = self.worker._execute(ToolCall(name="not_a_tool", args={}), AgentRun())
        self.assertIn("build_project", text)

    def test_status_text(self) -> None:
        status = _tool_status(ToolCall(name="build_project", args={"task": "runClient"}))
        self.assertIn("runClient", status)


class BuildReportTests(unittest.TestCase):
    def test_prompt_requires_self_build(self) -> None:
        from dma.prompt import TOOL_PROTOCOL

        self.assertIn('"tool":"build_project"', TOOL_PROTOCOL)
        self.assertIn("不要说自己没有执行 Gradle 的工具", TOOL_PROTOCOL)
        self.assertNotIn("先让用户点一次编译", TOOL_PROTOCOL)

    def test_failure_keeps_tail_and_marks_truncation(self) -> None:
        result = builder.BuildResult(ok=False, code=1, output="x" * 30000, message="")
        text = _build_report(result)
        self.assertIn("编译失败（退出码 1）", text)
        self.assertIn("只保留末尾部分", text)
        self.assertLessEqual(len(text), 24200)

    def test_success_without_jars_explains(self) -> None:
        result = builder.BuildResult(ok=True, code=0, output="BUILD SUCCESSFUL")
        text = _build_report(result)
        self.assertIn("没有在 build/libs 里找到 jar", text)


if __name__ == "__main__":
    unittest.main()
