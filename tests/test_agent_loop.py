from __future__ import annotations

import unittest
from unittest import mock

from dma.agent import AgentWorker, apply_pending
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
        self.assertEqual(run.pending, [])
        self.assertEqual(run.applied, ["README.md"])
        self.assertEqual((project.root / "README.md").read_text(encoding="utf-8"), "# 新内容\n")

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
