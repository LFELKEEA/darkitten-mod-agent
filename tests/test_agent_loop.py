from __future__ import annotations

import unittest
from unittest import mock

from dma import agent as agent_mod
from dma import builder
from dma.agent import AgentWorker, PendingFile, apply_pending
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.memory import MemoryStore
from dma.project import create_project
from dma.skills import SkillStore

from .base import TempDataCase


def make_project(case: TempDataCase):
    return create_project(
        case.tmp / "projects",
        name="Test Mod",
        mod_id="test_mod",
        mc_version="1.21.1",
        loader="Fabric",
        task_type="创建全新模组",
    )


class AgentLoopTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self._stores: list[MemoryStore] = []

    def tearDown(self) -> None:
        for store in self._stores:
            store.close()
        super().tearDown()

    def worker(self, project, *, auto_apply: bool = False) -> AgentWorker:
        config = Config()
        config.ai.provider = "pollinations"
        config.auto_apply = auto_apply
        config.max_steps = 4
        memory = MemoryStore()
        self._stores.append(memory)
        return AgentWorker(
            user_text="创建一个物品",
            history=[],
            project=project,
            config=config,
            skill_store=SkillStore(),
            memory=memory,
            connector_store=ConnectorStore(),
        )

    def test_tool_write_then_final(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        replies = [
            '先写入口类。\n```tool\n{"tool":"write_file","path":"src/main/java/A.java",'
            '"content":"package a;\\npublic class A {}\\n"}\n```',
            "已经写好入口类，下一步编译。",
        ]
        seen_messages: list[list[dict]] = []

        def fake(ai, messages):
            seen_messages.append(list(messages))
            return replies.pop(0)

        visible: list[str] = []
        worker.assistant.connect(visible.append)
        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()

        self.assertEqual(run.text, "已经写好入口类，下一步编译。")
        self.assertEqual(len(run.pending), 1)
        self.assertEqual(run.pending[0].path, "src/main/java/A.java")
        self.assertIn("package a;", run.pending[0].content)
        self.assertEqual(visible, ["先写入口类。"])
        self.assertEqual(len(seen_messages), 2)
        self.assertIn("[工具结果：write_file]", seen_messages[1][-1]["content"])
        self.assertFalse((project.root / "src/main/java/A.java").exists())

        written = apply_pending(project, run.pending)
        self.assertEqual(written, ["src/main/java/A.java"])
        self.assertTrue((project.root / "src/main/java/A.java").is_file())

    def test_auto_apply(self) -> None:
        project = make_project(self)
        worker = self.worker(project, auto_apply=True)
        reply = (
            '```tool\n{"tool":"write_file","path":"README.md",'
            '"content":"# 新内容\\n"}\n```'
        )
        with mock.patch("dma.agent.ai.request_chat", side_effect=[reply, "完成"]):
            run = worker._loop()
        self.assertEqual(len(run.pending), 1)
        self.assertTrue(run.pending[0].applied)
        self.assertEqual(run.pending[0].path, "README.md")
        self.assertEqual(run.applied, ["README.md"])
        self.assertEqual((project.root / "README.md").read_text(encoding="utf-8"), "# 新内容\n")
        # 已经自动写入的文件再次点“写入全部”时不会重复写。
        self.assertEqual(apply_pending(project, run.pending), [])

    def test_pixel_art_tool_writes_png(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = (
            '```tool\n{"tool":"pixel_art","path":"src/main/resources/assets/test_mod/'
            'textures/item/gem.png","palette":{".":"transparent","a":"#ff0000"},'
            '"rows":["aa","a."]}\n```'
        )
        with mock.patch("dma.agent.ai.request_chat", side_effect=[reply, "纹理已生成。"]):
            run = worker._loop()
        target = project.root / "src/main/resources/assets/test_mod/textures/item/gem.png"
        self.assertTrue(target.is_file())
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(int.from_bytes(data[16:20], "big"), 2)
        self.assertEqual(int.from_bytes(data[20:24], "big"), 2)
        self.assertEqual(run.pending[0].path, target.relative_to(project.root).as_posix())
        self.assertTrue(run.pending[0].applied)

    def test_pixel_art_rejects_bad_rows(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = (
            '```tool\n{"tool":"pixel_art","path":"bad.png","palette":{".":"transparent"},'
            '"rows":["..","."]}\n```'
        )
        with mock.patch("dma.agent.ai.request_chat", side_effect=[reply, "结束"]):
            worker._loop()
        self.assertFalse((project.root / "bad.png").exists())

    def test_apply_pending_skips_auto_written(self) -> None:
        project = make_project(self)
        pending = [
            PendingFile(path="a.txt", content="A", applied=True),
            PendingFile(path="b.txt", content="B"),
        ]
        written = apply_pending(project, pending)
        self.assertEqual(written, ["b.txt"])
        self.assertFalse((project.root / "a.txt").exists())
        self.assertEqual((project.root / "b.txt").read_text(encoding="utf-8"), "B")

    def test_truncated_tool_call_retries(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project)
        replies = [
            '<tool>{"tool":"read_file","path":"build.gradle"',
            '```tool\n{"tool":"read_file","path":"build.gradle"}\n```',
            "读完了。",
        ]
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            return replies.pop(0)

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.text, "读完了。")
        self.assertEqual(len(messages), 3)
        self.assertIn("没有解析成功", messages[1][-1]["content"])
        self.assertIn("plugins {}", messages[2][-1]["content"])

    def test_plan_only_reply_requests_tool(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project)
        replies = [
            "我继续，先读 build.gradle 现状，确认问题根因。",
            '```tool\n{"tool":"read_file","path":"build.gradle"}\n```',
            "读完了，配置正常。",
        ]
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            return replies.pop(0)

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.text, "读完了，配置正常。")
        self.assertEqual(len(messages), 3)
        self.assertIn("没有真正调用工具", messages[1][-1]["content"])
        self.assertIn("plugins {}", messages[2][-1]["content"])

    def test_long_plan_ending_with_read_requests_tool(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project)
        plan = (
            "三个问题，我逐个定位。先读现有代码确认现状，不凭记忆改。\n\n"
            "问题 1：婚姻关系没保留——上一轮我加了 copyRelationsBack，"
            "但日志显示你结婚后解除附身，关系没写回。需要确认 copyRelationsBack "
            "是否真的被调用、CURRENT_TARGET_KEY 是否还在。\n"
            "问题 2：皮化村民复原时名字变成职业名——这是 FamilyTreeNode 的 name "
            "字段被职业覆盖，或者重建村民时 readAdditionalSaveData 读错了键。"
            "需要看 SkinBladeItem 存 NBT 和 PlayerSkinItem 重建村民的代码。\n"
            "问题 3：脱皮应该用皮化之刃——这是交互设计变更，需要改 PlayerSkinItem "
            "的脱皮触发条件。\n"
            "先读二个文件。"
        )
        replies = [
            plan,
            '```tool\n{"tool":"read_file","path":"build.gradle"}\n```',
            "读完了。",
        ]
        calls: list[int] = []

        def fake(ai, items):
            calls.append(1)
            return replies.pop(0)

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.text, "读完了。")
        self.assertEqual(len(calls), 3)

    def test_plan_only_reply_retry_is_limited(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        calls: list[int] = []

        def fake(ai, items):
            calls.append(1)
            return "我先读一下工程结构。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(len(calls), 3)
        self.assertIn("我先读一下工程结构。", run.text)
        self.assertIn("再发送「继续」", run.text)

    def test_finished_summary_not_treated_as_plan(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        calls: list[int] = []

        def fake(ai, items):
            calls.append(1)
            return "已经读取了 build.gradle，确认配置没有问题。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(len(calls), 1)
        self.assertIn("已经读取", run.text)

    def test_unsafe_path_refused(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = (
            '```tool\n{"tool":"write_file","path":"../evil.java","content":"class E {}"}\n```'
        )
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            return reply if len(messages) == 1 else "结束"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.pending, [])
        self.assertIn("写入失败", messages[1][-1]["content"])
        self.assertFalse((self.tmp / "evil.java").exists())

    def test_read_file_tool(self) -> None:
        project = make_project(self)
        (project.root / "note.txt").write_text("hello mod", encoding="utf-8")
        worker = self.worker(project)
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            if len(messages) == 1:
                return '```tool\n{"tool":"read_file","path":"note.txt"}\n```'
            return "读到了 hello mod。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertIn("hello mod", messages[1][-1]["content"])
        self.assertEqual(run.text, "读到了 hello mod。")

    def test_dsml_tool_call_runs_without_visible_markup(self) -> None:
        project = make_project(self)
        (project.root / "note.txt").write_text("hello mod", encoding="utf-8")
        worker = self.worker(project)
        reply = (
            '< | DSLM | calls > < | DSLM | invoke name="tool"> '
            '<tool>{"tool":"read_file","path":"note.txt"} </|DSML|parameter> '
            "</|DSML|invoke></|DSML|calls>"
        )
        messages: list[list[dict]] = []
        visible: list[str] = []

        def fake(ai, items):
            messages.append(list(items))
            return reply if len(messages) == 1 else "读完了。"

        worker.assistant.connect(visible.append)
        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(visible, [])
        self.assertIn("hello mod", messages[1][-1]["content"])
        self.assertEqual(run.text, "读完了。")

    def test_unparsable_dsml_does_not_show_raw_markup(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = (
            '<|DSML|calls><|DSML|invoke name="tool">'
            '{"tool":"read_file", broken}<|/DSML|invoke><|/DSML|calls>'
        )
        calls: list[int] = []

        def fake(ai, items):
            calls.append(1)
            return reply

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(len(calls), 3)
        self.assertNotIn("DSML", run.text)
        self.assertIn("无法解析的工具调用", run.text)

    def test_inline_file_block(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = """### src/main/resources/fabric.mod.json

```json
{"schemaVersion": 1, "id": "test_mod"}
```

完成，文件在右侧等待确认。
"""
        with mock.patch("dma.agent.ai.request_chat", return_value=reply):
            run = worker._loop()
        self.assertEqual(len(run.pending), 1)
        self.assertEqual(run.pending[0].path, "src/main/resources/fabric.mod.json")

    def test_max_steps_message(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        tool = '```tool\n{"tool":"list_files","path":"."}\n```'
        with mock.patch("dma.agent.ai.request_chat", return_value=tool):
            run = worker._loop()
        self.assertIn("轮数上限", run.text)

    def test_unlimited_steps_can_exceed_old_cap(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        worker.config.max_steps = 0
        tool = '```tool\n{"tool":"list_files","path":"."}\n```'
        calls: list[int] = []

        def fake(ai, messages):
            calls.append(1)
            return tool if len(calls) <= 25 else "大工程完成。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.steps, 26)
        self.assertEqual(run.text, "大工程完成。")

    def test_read_file_offset(self) -> None:
        project = make_project(self)
        (project.root / "note.txt").write_text("0123456789abcdef", encoding="utf-8")
        worker = self.worker(project)
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            if len(messages) == 1:
                return (
                    '```tool\n{"tool":"read_file","path":"note.txt",'
                    '"offset":10,"max_chars":6}\n```'
                )
            return "读到了。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertIn("abcdef", messages[1][-1]["content"])
        self.assertNotIn("0123456789", messages[1][-1]["content"].split("abcdef")[0][-20:])
        self.assertEqual(run.text, "读到了。")

    def test_edit_file_replaces_and_auto_applies(self) -> None:
        project = make_project(self)
        (project.root / "note.txt").write_text("hello mod", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        reply = (
            '```tool\n{"tool":"edit_file","path":"note.txt",'
            '"old":"hello","new":"hi"}\n```'
        )
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            return reply if len(messages) == 1 else "改好了。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual((project.root / "note.txt").read_text(encoding="utf-8"), "hi mod")
        self.assertIn("已自动写入", messages[1][-1]["content"])
        self.assertEqual(run.pending[0].reason, "edit")
        self.assertTrue(run.pending[0].applied)

    def test_edit_file_missing_text_guides_read(self) -> None:
        project = make_project(self)
        (project.root / "note.txt").write_text("hello mod", encoding="utf-8")
        worker = self.worker(project)
        reply = (
            '```tool\n{"tool":"edit_file","path":"note.txt",'
            '"old":"absent","new":"x"}\n```'
        )
        messages: list[list[dict]] = []

        def fake(ai, items):
            messages.append(list(items))
            return reply if len(messages) == 1 else "先读一下。"

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            run = worker._loop()
        self.assertEqual(run.pending, [])
        self.assertIn("没有在 note.txt 里找到", messages[1][-1]["content"])

    def test_auto_build_after_write(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        replies = [
            '```tool\n{"tool":"write_file","path":"src/main/java/A.java",'
            '"content":"package a;\\npublic class A {}\\n"}\n```',
            "已经写好入口类。",
        ]
        result = builder.BuildResult(ok=True, code=0, jars=[("test_mod.jar", 1024)])
        with mock.patch("dma.agent.ai.request_chat", side_effect=replies), mock.patch(
            "dma.agent.builder.build_project", return_value=result
        ) as build:
            run = worker._loop()
        self.assertEqual(build.call_count, 1)
        self.assertIn("自动编译通过", run.text)
        self.assertIn("test_mod.jar", run.text)

    def test_auto_build_failure_then_fix_builds_again(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        replies = [
            '```tool\n{"tool":"write_file","path":"src/main/java/A.java",'
            '"content":"package a;\\npublic class A {}\\n"}\n```',
            "已经写好入口类。",
            '```tool\n{"tool":"edit_file","path":"src/main/java/A.java",'
            '"old":"class A {}","new":"class A { /* fixed */ }"}\n```',
            "修好了。",
        ]
        results = [
            builder.BuildResult(ok=False, code=1, output="error: boom"),
            builder.BuildResult(ok=True, code=0, jars=[("test_mod.jar", 2048)]),
        ]
        seen: list[list[dict]] = []

        def fake(ai, items):
            seen.append(list(items))
            return replies.pop(0)

        with mock.patch("dma.agent.ai.request_chat", side_effect=fake), mock.patch(
            "dma.agent.builder.build_project", side_effect=results
        ) as build:
            run = worker._loop()
        self.assertEqual(build.call_count, 2)
        self.assertIn("error: boom", seen[2][-1]["content"])
        self.assertIn("自动编译通过", run.text)
        self.assertIn("test_mod.jar", run.text)

    def test_auto_build_failure_without_changes_reports_log(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        replies = [
            '```tool\n{"tool":"write_file","path":"src/main/java/A.java",'
            '"content":"package a;\\npublic class A {}\\n"}\n```',
            "已经写好。",
            "已经修复了。",
        ]
        result = builder.BuildResult(ok=False, code=1, output="error: still broken")
        with mock.patch("dma.agent.ai.request_chat", side_effect=replies), mock.patch(
            "dma.agent.builder.build_project", return_value=result
        ) as build:
            run = worker._loop()
        self.assertEqual(build.call_count, 1)
        self.assertIn("没有检测到代码改动", run.text)
        self.assertIn("still broken", run.text)

    def test_auto_build_skipped_when_disabled(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        worker.config.auto_build = False
        replies = [
            '```tool\n{"tool":"write_file","path":"A.java","content":"class A {}"}\n```',
            "写完了。",
        ]
        with mock.patch("dma.agent.ai.request_chat", side_effect=replies), mock.patch(
            "dma.agent.builder.build_project"
        ) as build:
            run = worker._loop()
        build.assert_not_called()
        self.assertEqual(run.text, "写完了。")

    def test_auto_build_skipped_for_docs_only(self) -> None:
        project = make_project(self)
        (project.root / "build.gradle").write_text("plugins {}", encoding="utf-8")
        worker = self.worker(project, auto_apply=True)
        replies = [
            '```tool\n{"tool":"write_file","path":"NOTES.md","content":"# 笔记"}\n```',
            "笔记写好了。",
        ]
        with mock.patch("dma.agent.ai.request_chat", side_effect=replies), mock.patch(
            "dma.agent.builder.build_project"
        ) as build:
            run = worker._loop()
        build.assert_not_called()
        self.assertEqual(run.text, "笔记写好了。")
        self.assertTrue(agent_mod._needs_build(["src/main/java/A.java"]))
        self.assertFalse(agent_mod._needs_build(["README.md", "docs/a.png"]))

    def test_compact_messages_folds_old_tool_results(self) -> None:
        messages = [
            {"role": "system", "content": "s"},
            {"role": "user", "content": "u"},
        ]
        for _ in range(6):
            messages.append({"role": "assistant", "content": "调用工具"})
            messages.append({"role": "user", "content": "[工具结果：read_file]\n" + "x" * 100_000})
        with mock.patch("dma.agent.MESSAGE_CHAR_BUDGET", 260_000):
            agent_mod._compact_messages(messages)
        total = sum(len(item["content"]) for item in messages)
        self.assertLessEqual(total, 260_000)
        self.assertIn("已折叠", messages[3]["content"])
        self.assertGreater(len(messages[-1]["content"]), 90_000)

    def test_history_window_strips_old_dsml_markup(self) -> None:
        dsml = (
            '<|DSML|calls><|DSML|invoke name="tool">'
            '{"tool":"read_file","path":"a.java"}'
            "<|/DSML|invoke><|/DSML|calls>"
        )
        history = [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": dsml},
            {"role": "user", "content": "再继续"},
            {"role": "assistant", "content": "读完了。"},
        ]
        window = agent_mod._history_window(history, 10)
        self.assertTrue(all("DSML" not in item["content"] for item in window))
        self.assertEqual([item["content"] for item in window], ["继续", "再继续", "读完了。"])

    def test_history_window_respects_char_budget(self) -> None:
        history = [{"role": "user", "content": "x" * 100} for _ in range(10)]
        with mock.patch("dma.agent.HISTORY_CHAR_BUDGET", 250):
            window = agent_mod._history_window(history, 12)
        self.assertEqual(len(window), 2)
        self.assertEqual(sum(len(item["content"]) for item in window), 200)

    def test_memory_tool(self) -> None:
        project = make_project(self)
        worker = self.worker(project)
        reply = (
            '```tool\n{"tool":"remember","content":"这个工程用 Yarn 映射",'
            '"kind":"decision","tags":"映射"}\n```'
        )
        with mock.patch("dma.agent.ai.request_chat", side_effect=[reply, "记下了"]):
            worker._loop()
        self.assertEqual(worker.memory.count(), 1)
        self.assertIn("Yarn", worker.memory.list()[0]["content"])


if __name__ == "__main__":
    unittest.main()
