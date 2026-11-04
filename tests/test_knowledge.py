from __future__ import annotations

import io
import re
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dma import knowledge as knowledge_mod
from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.knowledge import KnowledgeError, KnowledgeStore, knowledge_context
from dma.memory import MemoryStore
from dma.project import Project, ProjectMeta
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


def _utf8(text: str) -> bytes:
    raw = text.encode("utf-8")
    return b"\x01" + len(raw).to_bytes(2, "big") + raw


def make_class() -> bytes:
    pool = b"".join(
        [
            _utf8("com/example/ExampleMod"),       # 1
            b"\x07\x00\x01",                       # 2 Class
            _utf8("java/lang/Object"),             # 3
            b"\x07\x00\x03",                       # 4 Class
            _utf8("MOD_ID"),                       # 5
            _utf8("Ljava/lang/String;"),           # 6
            _utf8("onInitialize"),                 # 7
            _utf8("()V"),                          # 8
            _utf8("examplemod"),                   # 9
            b"\x08\x00\x09",                       # 10 String
            _utf8("examplemod:ruby"),              # 11
            b"\x08\x00\x0b",                       # 12 String
            _utf8("net/minecraft/world/level/Level"),  # 13
            b"\x07\x00\x0d",                       # 14 Class
            _utf8("getBlockState"),                # 15
            _utf8("(Lnet/minecraft/core/BlockPos;)Lnet/minecraft/world/level/block/state/BlockState;"),  # 16
            b"\x0c\x00\x0f\x00\x10",               # 17 NameAndType
            b"\x0a\x00\x0e\x00\x11",               # 18 Methodref
        ]
    )
    body = (
        b"\x00\x21"          # public super
        b"\x00\x02"          # this
        b"\x00\x04"          # super
        b"\x00\x00"          # interfaces
        b"\x00\x01"          # fields
        b"\x00\x19\x00\x05\x00\x06\x00\x00"
        b"\x00\x01"          # methods
        b"\x00\x01\x00\x07\x00\x08\x00\x00"
        b"\x00\x00"          # class attributes
    )
    return b"\xca\xfe\xba\xbe\x00\x00\x00\x3d\x00\x13" + pool + body


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

    def test_join_chunks_matches_overlap_append(self) -> None:
        text = "\n\n".join(f"第 {index} 段：" + "内容" * 300 for index in range(40))
        chunks = knowledge_mod.split_chunks(text)
        expected = ""
        for chunk in chunks:
            expected = knowledge_mod._append_overlap(expected, chunk)
        body, truncated = knowledge_mod._join_chunks(chunks)
        self.assertFalse(truncated)
        self.assertEqual(body, expected)

    def test_get_document_preview_stops_at_limit(self) -> None:
        store = self.store()
        text = "\n\n".join(f"第 {index} 段：" + "内容" * 300 for index in range(40))
        result = store.add_text("长文", text)
        full = store.get_document(result["id"])
        self.assertFalse(full["truncated"])
        self.assertIn("第 39 段", full["text"])
        preview = store.get_document(result["id"], limit_chars=500)
        self.assertTrue(preview["truncated"])
        self.assertEqual(len(preview["text"]), 500)
        self.assertTrue(preview["text"].startswith("第 0 段："))
        self.assertEqual(preview["text"], full["text"][:500])
        exact = store.get_document(result["id"], limit_chars=len(full["text"]))
        self.assertFalse(exact["truncated"])
        self.assertEqual(exact["text"], full["text"])

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

    def test_jar_extraction_includes_metadata_classes_and_lang(self) -> None:
        path = self.tmp / "examplemod-1.0.0.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "fabric.mod.json",
                '{"id":"examplemod","entrypoints":{"main":["com.example.ExampleMod"]}}',
            )
            archive.writestr(
                "assets/examplemod/lang/zh_cn.json",
                '{"block.examplemod.ruby":"红宝石块"}',
            )
            archive.writestr("com/example/ExampleMod.class", make_class())
            archive.writestr("assets/examplemod/textures/block/ruby.png", b"\x89PNG\x00\x01")
            archive.writestr("META-INF/jars/library.jar", b"PK\x03\x04nested")
        with mock.patch("dma.knowledge._decompile_jar", return_value=([], "")):
            text, kind = knowledge_mod.extract_text(path)
        self.assertEqual(kind, "jar")
        self.assertIn("模组 JAR：examplemod-1.0.0.jar", text)
        self.assertIn("## 模组速览", text)
        self.assertIn("## 类索引", text)
        self.assertLess(text.index("## 模组速览"), text.index("## 文件：fabric.mod.json"))
        self.assertIn("fabric.mod.json", text)
        self.assertIn('"block.examplemod.ruby"', text)
        self.assertIn("红宝石块", text)
        self.assertIn("class com.example.ExampleMod", text)
        self.assertIn("onInitialize()V", text)
        self.assertIn("MOD_ID", text)
        self.assertIn("examplemod:ruby", text)
        self.assertIn(
            "net.minecraft.world.level.Level.getBlockState", text
        )
        self.assertIn("已跳过 1 个内嵌 jar", text)
        self.assertNotIn("PNG", text)

    def test_jar_appends_decompiled_source(self) -> None:
        path = self.tmp / "sourcemod.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", '{"id":"sourcemod"}')
            archive.writestr("com/example/ExampleMod.class", make_class())
        sources = [
            (
                "com/example/ExampleMod.java",
                "package com.example;\npublic class ExampleMod { public void onInitialize() {} }",
            )
        ]
        with mock.patch("dma.knowledge._decompile_jar", return_value=(sources, "")):
            text, kind = knowledge_mod.extract_text(path)
        self.assertEqual(kind, "jar")
        self.assertIn("反编译源码（Vineflower", text)
        self.assertIn("## 源码：com/example/ExampleMod.java", text)
        self.assertIn("public class ExampleMod", text)
        self.assertIn("只用于理解实现", text)

    def test_jar_reports_missing_jdk_note(self) -> None:
        path = self.tmp / "nojdk.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", '{"id":"nojdk"}')
            archive.writestr("com/example/NoJdk.class", make_class())
        note = "本机没有找到 JDK，未生成反编译源码。"
        with mock.patch("dma.knowledge._decompile_jar", return_value=([], note)):
            text, kind = knowledge_mod.extract_text(path)
        self.assertEqual(kind, "jar")
        self.assertIn("nojdk", text)
        self.assertIn(note, text)

    def test_jar_with_broken_class_still_reads_metadata(self) -> None:
        path = self.tmp / "broken.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", '{"id":"brokenmod"}')
            archive.writestr("com/example/Broken.class", b"\xca\xfe\xba\xbe\x00")
        with mock.patch("dma.knowledge._decompile_jar", return_value=([], "")):
            text, kind = knowledge_mod.extract_text(path)
        self.assertEqual(kind, "jar")
        self.assertIn("brokenmod", text)
        self.assertNotIn("class com.example.Broken", text)

    def test_jar_is_searchable_and_reports_kind(self) -> None:
        path = self.tmp / "searchmod.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "META-INF/mods.toml",
                'modId="searchmod"\ndescription="Adds a ruby tool set"',
            )
        store = self.store()
        with mock.patch("dma.knowledge._decompile_jar", return_value=([], "")):
            result = store.add_file(path)
        self.assertEqual(result["name"], "searchmod.jar")
        hits = store.search("ruby tool")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["name"], "searchmod.jar")
        self.assertEqual(store.list_documents()[0]["kind"], "jar")

    def test_chinese_term_hint_matches_english_identifier(self) -> None:
        store = self.store()
        store.add_text(
            "反编译源码",
            "class BodyPossession { void startPossession() {} }",
        )
        hits = store.search("附身是怎么实现的")
        self.assertTrue(hits)
        self.assertIn("BodyPossession", hits[0]["text"])

    def test_jar_overview_returned_when_query_misses(self) -> None:
        path = self.tmp / "overview.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("fabric.mod.json", '{"id":"overviewmod"}')
            archive.writestr("com/example/ExampleMod.class", make_class())
        store = self.store()
        with mock.patch("dma.knowledge._decompile_jar", return_value=([], "")):
            store.add_file(path)
        self.assertEqual(store.search("完全无关的量子物理"), [])
        overview = store.overview_hits()
        self.assertEqual(len(overview), 1)
        self.assertIn("## 模组速览", overview[0]["text"])

    def test_legacy_jar_document_gets_synthesized_index(self) -> None:
        store = self.store()
        store.add_text(
            "old.jar",
            "# 模组 JAR：old.jar\n\n## 类：com.example.OldMod\n方法：\n  - onInitialize()V",
            kind="jar",
            source="old.jar",
        )
        overview = store.overview_hits()
        self.assertEqual(len(overview), 1)
        self.assertIn("## 类索引", overview[0]["text"])
        self.assertIn("OldMod", overview[0]["text"])

    def test_decompile_jar_sources_includes_nested_modules(self) -> None:
        nested = io.BytesIO()
        with zipfile.ZipFile(nested, "w") as archive:
            archive.writestr("cn/test/Extra.class", b"\xca\xfe\xba\xbe")
        path = self.tmp / "mod.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("com/example/Main.class", b"\xca\xfe\xba\xbe")
            archive.writestr("META-INF/jars/extra.jar", nested.getvalue())

        def fake_decompile(target, progress=None, label=""):
            if Path(target).name.startswith("nested-"):
                return [("cn/test/Extra.java", "package cn.test; class Extra {}")], ""
            return [("com/example/Main.java", "package com.example; class Main {}")], ""

        with mock.patch("dma.knowledge._decompile_jar", side_effect=fake_decompile):
            files, notes = knowledge_mod.decompile_jar_sources(path)
        paths = {item["path"] for item in files}
        self.assertEqual(paths, {"com/example/Main.java", "cn/test/Extra.java"})
        origins = {item["origin"] for item in files}
        self.assertIn("mod.jar", origins)
        self.assertIn("META-INF/jars/extra.jar", origins)
        self.assertIn("共生成 2 个 Java 文件。", notes)

    def test_write_decompiled_tree_creates_index(self) -> None:
        root = self.tmp / "project"
        (root / "src" / "main" / "java").mkdir(parents=True)
        files = [
            {
                "path": "com/example/A.java",
                "text": "package com.example; class A {}",
                "origin": "a.jar",
            }
        ]
        result = knowledge_mod.write_decompiled_tree(
            root,
            "reference/decompiled/a",
            files,
            jar_name="a.jar",
            notes=["测试备注"],
        )
        self.assertEqual(result["written"], 1)
        target = root / "reference/decompiled/a/com/example/A.java"
        self.assertIn("class A", target.read_text(encoding="utf-8"))
        index = (root / "reference/decompiled/a/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("a.jar", index)
        self.assertIn("测试备注", index)
        with self.assertRaises(knowledge_mod.KnowledgeError):
            knowledge_mod.write_decompiled_tree(
                root, "../outside", files, jar_name="a.jar"
            )

    def test_rejects_binary_and_missing(self) -> None:
        binary = self.tmp / "image.bin"
        binary.write_bytes(b"\x00\x01\x02")
        with self.assertRaises(KnowledgeError):
            knowledge_mod.extract_text(binary)
        with self.assertRaises(KnowledgeError):
            knowledge_mod.extract_text(self.tmp / "missing.txt")

    def test_context_text_mentions_file(self) -> None:
        hits = [
            {
                "name": "规范.md",
                "project": "",
                "text": "使用 Yarn。",
                "document_id": 7,
            }
        ]
        text = knowledge_context(hits)
        self.assertIn("规范.md", text)
        self.assertIn("全部工程", text)
        self.assertIn("编号 7", text)
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

    def test_read_knowledge_supports_query_and_paging(self) -> None:
        worker = self.worker()
        run = AgentRun()
        document = self.knowledge.add_text(
            "大文件",
            "开头内容。" + ("填充文字。" * 60000) + "\n唯一标记B在这里。",
        )
        matched = worker._execute(
            ToolCall(
                name="read_knowledge",
                args={"id": document["id"], "query": "唯一标记B"},
            ),
            run,
        )
        self.assertIn("唯一标记B", matched)
        self.assertIn("[条目", matched)
        first_page = worker._execute(
            ToolCall(name="read_knowledge", args={"id": document["id"], "offset": 0}),
            run,
        )
        self.assertIn("继续读请把 offset", first_page)
        self.assertIn("全文", first_page)

    def test_read_knowledge_reads_whole_source_file(self) -> None:
        methods = "\n".join(f"   public void method{i}() {{}}" for i in range(80))
        text = (
            "# 模组 JAR：mod.jar\n\n## 类：com.example.BodyPossession\n方法：\n"
            "  - public void start()V\n\n"
            "## 源码：com/example/BodyPossession.java\n"
            "package com.example;\n\npublic class BodyPossession {\n"
            + methods
            + "\n}\n\n"
            "## 源码：com/example/Other.java\npackage com.example;\nclass Other {}\n"
        )
        document = self.knowledge.add_text(
            "mod.jar", text, kind="jar", source="mod.jar"
        )
        result = self.worker()._execute(
            ToolCall(
                name="read_knowledge",
                args={"id": document["id"], "file": "BodyPossession.java"},
            ),
            AgentRun(),
        )
        self.assertIn("## 源码：com/example/BodyPossession.java", result)
        self.assertIn("method79", result)
        self.assertNotIn("class Other", result)

    def test_read_knowledge_source_pages_with_offset(self) -> None:
        methods = "\n".join(f"   public void method{i}() {{}}" for i in range(12000))
        text = (
            "## 源码：com/example/Big.java\npackage com.example;\n"
            + methods
            + "\n"
        )
        document = self.knowledge.add_text(
            "mod.jar", text, kind="jar", source="mod.jar"
        )
        worker = self.worker()
        first = worker._execute(
            ToolCall(
                name="read_knowledge",
                args={"id": document["id"], "file": "Big.java"},
            ),
            AgentRun(),
        )
        self.assertIn("offset=", first)
        self.assertIn("method0", first)
        self.assertNotIn("method11999", first)
        offset = int(re.findall(r"offset=(\d+)", first)[-1])
        second = worker._execute(
            ToolCall(
                name="read_knowledge",
                args={"id": document["id"], "file": "Big.java", "offset": offset},
            ),
            AgentRun(),
        )
        self.assertIn("method11999", second)

    def test_read_knowledge_unknown_file_lists_sources(self) -> None:
        document = self.knowledge.add_text(
            "mod.jar",
            "## 源码：com/example/Alpha.java\npackage com.example;\nclass Alpha {}\n",
            kind="jar",
            source="mod.jar",
        )
        result = self.worker()._execute(
            ToolCall(
                name="read_knowledge",
                args={"id": document["id"], "file": "Missing.java"},
            ),
            AgentRun(),
        )
        self.assertIn("没有找到", result)
        self.assertIn("com/example/Alpha.java", result)

    def test_read_source_prefers_source_over_class_summary(self) -> None:
        document = self.knowledge.add_text(
            "mod.jar",
            "## 类：com.example.BodyPossession\n方法：\n  - public void start()V\n"
            "\n## 源码：com/example/BodyPossession.java\n"
            "package com.example;\nclass BodyPossession { void start() {} }\n",
            kind="jar",
            source="mod.jar",
        )
        result = self.knowledge.read_source(document["id"], "BodyPossession.java")
        self.assertIsNotNone(result)
        self.assertEqual(result["kind"], "源码")
        self.assertIn("package com.example;", result["text"])

    def test_read_knowledge_warns_about_truncated_import(self) -> None:
        document = self.knowledge.add_text(
            "old.jar",
            "## 类：com.example.Old\n方法：\n  - void run()V\n\n"
            "（内容超过上限，已截断）",
            kind="jar",
            source="old.jar",
        )
        result = self.worker()._execute(
            ToolCall(name="read_knowledge", args={"id": document["id"]}),
            AgentRun(),
        )
        self.assertIn("重新导入", result)
        self.assertIn("旧版本导入", result)

    def test_document_hits_include_source_body(self) -> None:
        filler = "\n".join(f"   int value{i} = {i};" for i in range(300))
        document = self.knowledge.add_text(
            "mod.jar",
            "## 源码：com/example/Alpha.java\npackage com.example;\n"
            + filler
            + "\n\n## 源码：com/example/Beta.java\npackage com.example;\nclass Beta {}\n",
            kind="jar",
            source="mod.jar",
        )
        hits = self.knowledge.document_hits(
            document["id"], "Alpha.java", limit=1, budget=8000
        )
        self.assertTrue(hits)
        self.assertIn("package com.example;", hits[0]["text"])
        self.assertIn("value139", hits[0]["text"])
        self.assertNotIn("class Beta", hits[0]["text"])

    def test_search_returns_whole_source_file(self) -> None:
        filler = "\n".join(f"   int value{i} = {i};" for i in range(400))
        text = (
            "## 源码：com/example/Big.java\npackage com.example;\nclass Big {\n"
            + filler
            + "\n   void start() {}\n}\n\n"
            "## 源码：com/example/Other.java\npackage com.example;\nclass Other {}\n"
        )
        self.knowledge.add_text("mod.jar", text, kind="jar", source="mod.jar")
        hits = self.knowledge.search("start", project="", limit=4, budget=200_000)
        self.assertTrue(hits)
        joined = "\n".join(hit["text"] for hit in hits)
        self.assertIn("## 源码：com/example/Big.java", joined)
        self.assertIn("value0", joined)
        self.assertIn("void start()", joined)
        self.assertNotIn("class Other", joined)

    def test_search_knowledge_falls_back_to_jar_overview(self) -> None:
        self.knowledge.add_text(
            "mod.jar",
            "# 模组 JAR：mod.jar\n\n## 模组速览\n- Fabric：id=testmod\n\n"
            "## 类索引（共 1 个类）\ncom.example: TestMod",
            kind="jar",
            source="mod.jar",
        )
        worker = self.worker()
        result = worker._execute(
            ToolCall(name="search_knowledge", args={"query": "完全无关的量子物理"}),
            AgentRun(),
        )
        self.assertIn("模组速览", result)
        self.assertIn("TestMod", result)

    def test_decompile_jar_tool_writes_reference_tree(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        jar = self.tmp / "tool.jar"
        jar.write_bytes(b"PK\x03\x04")
        document = self.knowledge.add_text(
            "tool.jar", "# jar", kind="jar", source=str(jar)
        )
        project = Project(
            root=root,
            meta=ProjectMeta(
                name="测试",
                mod_id="testmod",
                mc_version="1.21.1",
                loader="Fabric",
            ),
        )
        config = Config()
        config.ai.provider = "pollinations"
        config.max_steps = 2
        worker = AgentWorker(
            user_text="把 tool.jar 反编译进工程",
            history=[],
            project=project,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
            knowledge=self.knowledge,
        )
        files = [
            {
                "path": "com/example/A.java",
                "text": "package com.example; class A {}",
                "origin": "tool.jar",
            }
        ]
        with mock.patch(
            "dma.agent.decompile_jar_sources", return_value=(files, ["ok"])
        ):
            result = worker._execute(
                ToolCall(
                    name="decompile_jar",
                    args={
                        "knowledge_id": document["id"],
                        "target": "reference/decompiled/tool",
                    },
                ),
                AgentRun(),
            )
        self.assertIn("反编译完成", result)
        self.assertTrue(
            (root / "reference/decompiled/tool/com/example/A.java").is_file()
        )
        self.assertTrue((root / "reference/decompiled/tool/INDEX.md").is_file())

    def test_prompt_mentions_decompiled_reference(self) -> None:
        root = self.tmp / "proj"
        (root / "reference" / "decompiled").mkdir(parents=True)
        (root / "reference/decompiled/INDEX.md").write_text("x", encoding="utf-8")
        project = Project(
            root=root,
            meta=ProjectMeta(
                name="测试",
                mod_id="testmod",
                mc_version="1.21.1",
                loader="Fabric",
            ),
        )
        prompt = build_system_prompt(project, SkillStore())
        self.assertIn("reference/decompiled/INDEX.md", prompt)

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
