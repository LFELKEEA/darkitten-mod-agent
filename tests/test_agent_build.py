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

    def test_completion_gate_builds_after_code_edit(self) -> None:
        self.assertTrue((self.project.root / "build.gradle").is_file())
        self.worker._code_dirty = True
        self.worker._code_edits = 1
        self.worker._build_code_mark = 0
        with mock.patch.object(
            self.worker,
            "_auto_build",
            return_value=(True, "编译成功（退出码 0）。\n产物：test.jar"),
        ) as build:
            text = self.worker._finalize_text("模组已经完成。")
        build.assert_called_once()
        self.assertIn("完成前编译校验", text)
        self.assertIn("编译成功", text)
        self.assertFalse(self.worker._code_dirty)

    def test_completion_gate_reports_failed_build(self) -> None:
        self.worker._code_dirty = True
        self.worker._code_edits = 1
        self.worker._build_code_mark = 0
        with mock.patch.object(
            self.worker,
            "_auto_build",
            return_value=(False, "编译失败（退出码 1）。\nfoo.java:3 找不到符号"),
        ):
            text = self.worker._finalize_text("模组已经完成。")
        self.assertIn("未完成：完成前编译校验失败", text)
        self.assertIn("找不到符号", text)
        self.assertTrue(self.worker._code_dirty)

    def test_completion_gate_skips_clean_run(self) -> None:
        with mock.patch.object(self.worker, "_auto_build") as build:
            text = self.worker._finalize_text("只是一次问答。")
        build.assert_not_called()
        self.assertEqual(text, "只是一次问答。")

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

    def test_game_control_requires_action(self) -> None:
        text = self.worker._execute(
            ToolCall(name="game_control", args={}), AgentRun()
        )
        self.assertIn("缺少 action", text)

    def test_game_control_reports_unknown_action(self) -> None:
        text = self.worker._execute(
            ToolCall(name="game_control", args={"action": "fly"}), AgentRun()
        )
        self.assertIn("未知游戏动作", text)

    def test_game_observe_without_probe_is_readable(self) -> None:
        text = self.worker._execute(
            ToolCall(name="game_observe", args={"port": 1}), AgentRun()
        )
        self.assertIn("游戏控制失败", text)
        self.assertIn("mcdev_prepare", text)

    def test_status_text(self) -> None:
        status = _tool_status(ToolCall(name="build_project", args={"task": "runClient"}))
        self.assertIn("runClient", status)

    def test_build_report_cache_roundtrip(self) -> None:
        """编译结果要落盘，下一轮开局能读回来。"""
        report = "编译失败（退出码 1）。\n\n共解析出 3 个错误\nA.java:1 找不到符号"
        self.worker._save_build_report(report)
        self.assertEqual(self.worker._load_build_report(), report)
        self.assertFalse(self.worker._build_cache_stale())

    def test_build_cache_stale_after_source_edit(self) -> None:
        """缓存之后又改了 java，应该提示先重新编译。"""
        import os
        import time
        from pathlib import Path

        self.worker._save_build_report("编译失败（退出码 1）。\n共解析出 1 个错误")
        src = Path(self.project.root) / "src" / "main" / "java" / "A.java"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("package a;", encoding="utf-8")
        cache = Path(self.project.root) / ".dma" / "last_build.txt"
        past = cache.stat().st_mtime - 10
        os.utime(cache, (past, past))
        now = time.time()
        os.utime(src, (now + 5, now + 5))
        self.assertTrue(self.worker._build_cache_stale())

    def test_resume_injects_cached_build_errors(self) -> None:
        """续做工程时，缓存的错误清单要直接进入第一轮上下文。"""
        from pathlib import Path

        dma_dir = Path(self.project.root) / ".dma"
        dma_dir.mkdir(parents=True, exist_ok=True)
        (dma_dir / "workflow.json").write_text("{}", encoding="utf-8")
        self.worker._save_build_report(
            "编译失败（退出码 1）。\n\n共解析出 2 个错误\n"
            "src/main/java/com/x/ModBlocks.java:12: 错误: 找不到符号"
        )
        seen: list[list[dict]] = []

        def fake(ai, messages, **_kwargs):
            seen.append(list(messages))
            return "本轮结束。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            self.worker._loop()

        joined = "\n".join(str(m.get("content") or "") for m in seen[0])
        self.assertIn("上一轮最后一次编译的结果缓存", joined)
        self.assertIn("ModBlocks.java:12", joined)

    def test_resume_injects_cached_validate_issues(self) -> None:
        """续做时缓存里的资源缺失清单也要带进上下文，避免每轮重扫。"""
        from pathlib import Path

        dma_dir = Path(self.project.root) / ".dma"
        dma_dir.mkdir(parents=True, exist_ok=True)
        (dma_dir / "workflow.json").write_text("{}", encoding="utf-8")
        self.worker._save_validate_report(
            "- **问题总数**: 3\n- **注册项覆盖**: 方块 1；缺失 3 项\n\n"
            "## 注册项资源覆盖\n- assets/x/blockstates/a.json 缺失"
        )
        seen: list[list[dict]] = []

        def fake(ai, messages, **_kwargs):
            seen.append(list(messages))
            return "本轮结束。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            self.worker._loop()

        joined = "\n".join(str(m.get("content") or "") for m in seen[0])
        self.assertIn("上一轮 validate_resources 的缺失清单", joined)
        self.assertIn("blockstates/a.json", joined)

    def test_clean_validate_cache_is_not_injected(self) -> None:
        from pathlib import Path

        dma_dir = Path(self.project.root) / ".dma"
        dma_dir.mkdir(parents=True, exist_ok=True)
        (dma_dir / "workflow.json").write_text("{}", encoding="utf-8")
        self.worker._save_validate_report("未发现问题。")
        seen: list[list[dict]] = []

        def fake(ai, messages, **_kwargs):
            seen.append(list(messages))
            return "本轮结束。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            self.worker._loop()

        joined = "\n".join(str(m.get("content") or "") for m in seen[0])
        self.assertNotIn("上一轮 validate_resources 的缺失清单", joined)


class BuildReportTests(unittest.TestCase):
    def test_prompt_requires_self_build(self) -> None:
        from dma.prompt import TOOL_PROTOCOL

        self.assertIn('"tool":"build_project"', TOOL_PROTOCOL)
        self.assertIn('"tool":"mcdev_prepare"', TOOL_PROTOCOL)
        self.assertIn("以真实产物为准", TOOL_PROTOCOL)
        self.assertNotIn("fix_build_setup", TOOL_PROTOCOL)

    def test_failure_keeps_tail_and_marks_truncation(self) -> None:
        result = BuildOutcome(ok=False, code=1, output="x" * 200000)
        text = _build_report(result)
        self.assertIn("编译失败（退出码 1）", text)
        self.assertIn("相关日志尾部", text)
        self.assertLessEqual(len(text), 96200)

    def test_failure_structured_errors(self) -> None:
        # javac 逐文件错误应被转成结构化清单，精确定位到文件:行，而不是整段日志。
        output = (
            "> Task :compileJava FAILED\n"
            "D:/proj/src/main/java/com/x/PersonalityLogic.java:45: error: cannot find symbol\n"
            "    PersonalitySlimeEntity slime = null;\n"
            "  symbol:   class PersonalitySlimeEntity\n"
            "  location: class PersonalityLogic\n"
            "D:/proj/src/main/java/com/x/ModEntities.java:12: error: incompatible types\n"
            "BUILD FAILED in 5s\n"
        )
        text = _build_report(BuildOutcome(ok=False, code=1, output=output))
        self.assertIn("编译失败，共解析出", text)
        self.assertIn("PersonalityLogic.java:45", text)
        self.assertIn("cannot find symbol", text)
        self.assertIn("ModEntities.java:12", text)
        self.assertIn("错误分布", text)

    def test_failure_structured_errors_chinese_javac(self) -> None:
        """中文 Windows 的 javac 输出“错误:”，也必须转成结构化清单。"""
        output = (
            "> Task :compileJava FAILED\n"
            "D:/proj/src/main/java/com/x/ModBlocks.java:12: 错误: 找不到符号\n"
            "        Block log = ModBlocks.TWILIGHT_LOG.get();\n"
            " 符号:   变量 TWILIGHT_LOG\n"
            " 位置: 类 ModBlocks\n"
            "D:/proj/src/main/java/com/x/ModItems.java:8: 错误: 不兼容的类型\n"
            "BUILD FAILED in 5s\n"
        )
        text = _build_report(BuildOutcome(ok=False, code=1, output=output))
        self.assertIn("编译失败，共解析出", text)
        self.assertIn("ModBlocks.java:12", text)
        self.assertIn("ModItems.java:8", text)
        self.assertIn("找不到符号", text)

    def test_failure_keeps_same_message_for_different_files(self) -> None:
        """同一条“找不到符号”出现在两个文件时，两个位置都要保留。"""
        output = (
            "D:/p/src/main/java/com/x/A.java:3: 错误: 找不到符号\n"
            "D:/p/src/main/java/com/x/B.java:9: 错误: 找不到符号\n"
            "BUILD FAILED in 1s\n"
        )
        text = _build_report(BuildOutcome(ok=False, code=1, output=output))
        self.assertIn("共解析出 2 个错误", text)
        self.assertIn("A.java:3", text)
        self.assertIn("B.java:9", text)

    def test_success_without_jars_explains(self) -> None:
        result = BuildOutcome(ok=True, code=0, output="BUILD SUCCESSFUL")
        text = _build_report(result)
        self.assertIn("没有在 build/libs 里找到 jar", text)

    def test_success_reports_contract_snapshot(self) -> None:
        # 编译成功后应附一份「契约快照」（已声明的类 + 注册 id），帮模型引用一致。
        import tempfile
        from pathlib import Path

        from dma.compile_feedback import success_symbols

        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "src" / "main" / "java" / "com" / "example" / "demo"
            src.mkdir(parents=True)
            (src / "DemoMod.java").write_text(
                "package com.example.demo;\n"
                "public class DemoMod {\n"
                "  public static final String ID = \"demo:example_item\";\n"
                "}\n",
                encoding="utf-8",
            )
            snap = success_symbols(Path(td))
            self.assertIn("com.example.demo", snap)
            self.assertIn("DemoMod", snap)


if __name__ == "__main__":
    unittest.main()
