from __future__ import annotations

import unittest
import zipfile
from pathlib import Path

from dma import knowledge as knowledge_mod
from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.knowledge import KnowledgeError, KnowledgeStore, knowledge_context
from dma.memory import MemoryStore
from dma.prompt import build_system_prompt
from dma.skills import SkillStore

from .base import TempDataCase


def make_docx(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", xml)


class KnowledgeStoreTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self._stores: list[KnowledgeStore] = []

    def tearDown(self) -> None:
        for store in self._stores:
            store.close()
        super().tearDown()

    def store(self) -> KnowledgeStore:
        item = KnowledgeStore()
        self._stores.append(item)
        return item

    def test_add_text_and_search(self) -> None:
        store = self.store()
        result = store.add_text(
            "开发规范",
            "这个工程使用 Yarn 映射，注册方块时必须先注册 Block，再注册 BlockItem。\n\n"
            "客户端渲染要放在 client 包里，不要写进服务端。",
        )
        self.assertGreater(result["chunks"], 0)
        hits = store.search("Yarn 映射")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["name"], "开发规范")
        self.assertIn("Yarn", hits[0]["text"])
        self.assertEqual(store.search("完全无关的量子物理"), [])

    def test_scope_filters_project_documents(self) -> None:
        store = self.store()
        store.add_text("公共资料", "Fabric 事件注册通用说明。")
        store.add_text("工程私有", "这个工程的私有方块注册细节。", project="C:/mods/a")
        hits_a = store.search("注册", project="C:/mods/a")
        hits_b = store.search("注册", project="C:/mods/b")
        names_a = {item["name"] for item in hits_a}
        names_b = {item["name"] for item in hits_b}
        self.assertIn("工程私有", names_a)
        self.assertIn("公共资料", names_a)
        self.assertNotIn("工程私有", names_b)
        self.assertEqual([item["name"] for item in store.list_documents(current_only=True, project="C:/mods/b")], ["公共资料"])
        self.assertEqual(len(store.list_documents(current_only=True, project="C:/mods/a")), 2)

    def test_reimport_replaces_same_source(self) -> None:
        store = self.store()
        source = self.tmp / "note.md"
        source.write_text("旧内容 alpha", encoding="utf-8")
        first = store.add_file(source)
        source.write_text("新内容 beta", encoding="utf-8")
        second = store.add_file(source)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(store.count(), 1)
        self.assertEqual(store.search("alpha"), [])
        self.assertTrue(store.search("beta"))

    def test_delete_document(self) -> None:
        store = self.store()
        result = store.add_text("临时资料", "这段内容用于测试删除。")
        store.delete_document(result["id"])
        self.assertEqual(store.count(), 0)
        self.assertEqual(store.chunk_count(), 0)
        self.assertIsNone(store.get_document(result["id"]))

    def test_docx_extraction(self) -> None:
        path = self.tmp / "guide.docx"
        make_docx(path, ["第一段：Fabric 模板", "第二段：Yarn 映射"])
        text, kind = knowledge_mod.extract_text(path)
        self.assertEqual(kind, "docx")
        self.assertIn("第一段：Fabric 模板", text)
        self.assertIn("第二段：Yarn 映射", text)

    def test_text_and_html_extraction(self) -> None:
        source = self.tmp / "page.html"
        source.write_text(
            "<html><style>hidden{}</style><body><h1>模组文档</h1><p>注册物品</p></body></html>",
            encoding="utf-8",
        )
        text, kind = knowledge_mod.extract_text(source)
        self.assertEqual(kind, "html")
        self.assertIn("模组文档", text)
        self.assertIn("注册物品", text)
        self.assertNotIn("hidden", text)

    def test_rejects_binary_and_missing(self) -> None:
        binary = self.tmp / "image.bin"
        binary.write_bytes(b"\x00\x01\x02")
        with self.assertRaises(KnowledgeError):
            knowledge_mod.extract_text(binary)
        with self.assertRaises(KnowledgeError):
            knowledge_mod.extract_text(self.tmp / "missing.txt")

    def test_context_text_mentions_file(self) -> None:
        hits = [{"name": "规范.md", "project": "", "text": "使用 Yarn。"}]
        text = knowledge_context(hits)
        self.assertIn("规范.md", text)
        self.assertIn("全部工程", text)
        self.assertIn("使用 Yarn", text)


class KnowledgeAgentTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.memory = MemoryStore()
        self.knowledge = KnowledgeStore()
        self.knowledge.add_text(
            "项目约定",
            "这个工程固定使用 Yarn 1.21.1+build.3，禁止写 Mojmap 名称。",
        )

    def tearDown(self) -> None:
        self.memory.close()
        self.knowledge.close()
        super().tearDown()

    def worker(self) -> AgentWorker:
        config = Config()
        config.ai.provider = "pollinations"
        config.max_steps = 2
        return AgentWorker(
            user_text="Yarn 映射用哪个版本？",
            history=[],
            project=None,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
            knowledge=self.knowledge,
        )

    def test_auto_context_injected_into_system_prompt(self) -> None:
        seen: list[list[dict]] = []

        def fake(ai, messages):
            seen.append(list(messages))
            return "使用 Yarn 1.21.1+build.3。"

        from unittest import mock

        worker = self.worker()
        with mock.patch("dma.agent.ai.request_chat", side_effect=fake):
            worker._loop()
        system = seen[0][0]["content"]
        self.assertIn("知识库资料", system)
        self.assertIn("Yarn 1.21.1+build.3", system)

    def test_search_and_read_knowledge_tools(self) -> None:
        worker = self.worker()
        run = AgentRun()
        search = worker._execute(
            ToolCall(name="search_knowledge", args={"query": "Yarn 版本"}), run
        )
        self.assertIn("项目约定", search)
        document = self.knowledge.search("Yarn")[0]
        read = worker._execute(
            ToolCall(name="read_knowledge", args={"id": document["document_id"]}), run
        )
        self.assertIn("禁止写 Mojmap", read)

    def test_prompt_contains_knowledge_section(self) -> None:
        prompt = build_system_prompt(
            None,
            SkillStore(),
            knowledge_text="[规范.md] 使用 Yarn。",
        )
        self.assertIn("【知识库资料（用户导入）】", prompt)
        self.assertIn("使用 Yarn", prompt)


if __name__ == "__main__":
    unittest.main()
