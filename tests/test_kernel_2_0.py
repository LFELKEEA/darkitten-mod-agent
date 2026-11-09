"""Regression tests for the 2.0 kernel, tools and agent integration."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from dma import ai
from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.kernel.rules import catalog, coverage, matrix_markdown
from dma.kernel.edit import FileCache, insert_member_text, replace_body_text
from dma.kernel.findings import Report
from dma.kernel.gate import verify
from dma.kernel.spec import ModSpec
from dma.kernel.spec import derive_spec
from dma.kernel.tools import configgen, diagnostics, library, matrix, migration
from dma.kernel.workflow import Task
from dma.kernel_bridge import KernelBridge
from dma.mcdev.process import LaunchManager
from dma.memory import MemoryStore
from dma.project import create_project
from dma.skills import SkillStore
from dma.tool_schema import TOOL_DEFINITIONS

from .base import TempDataCase


# ----------------------------------------------------------------------
# Catalog / schema
# ----------------------------------------------------------------------
class RuleCatalogTests(unittest.TestCase):
    def test_catalog_covers_all_176_items(self) -> None:
        rows = catalog()
        self.assertEqual(len(rows), 176)
        self.assertEqual(len({item.id for item in rows}), 176)
        per_level = {}
        for item in rows:
            per_level[item.level] = per_level.get(item.level, 0) + 1
        self.assertEqual(per_level, {"P0": 12, "P1": 40, "P2": 60, "P3": 44, "TOOL": 20})

    def test_every_rule_has_a_checker_and_automation(self) -> None:
        for item in catalog():
            self.assertTrue(item.checker, f"{item.id} 没有 checker")
            self.assertIn(item.automation, {"auto", "assist", "manual"}, item.id)

    def test_coverage_matches_catalog(self) -> None:
        cov = coverage()
        self.assertEqual(cov["total"]["total"], 176)
        self.assertEqual(cov["total"]["auto"], 136)
        self.assertEqual(cov["total"]["assist"], 40)
        self.assertEqual(cov["total"]["manual"], 0)

    def test_matrix_markdown_lists_every_rule(self) -> None:
        text = matrix_markdown()
        self.assertIn("# DMA 2.0 能力矩阵（176 项）", text)
        for item in catalog():
            self.assertIn(f"| {item.id} |", text)


class ToolSchemaTests(unittest.TestCase):
    def test_tool_definitions_are_unique(self) -> None:
        names = [item["function"]["name"] for item in TOOL_DEFINITIONS]
        self.assertEqual(len(names), 59)
        self.assertEqual(len(names), len(set(names)))

    def test_new_kernel_tools_are_exposed(self) -> None:
        names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
        for name in ("asset_library", "config_gen", "debug_scaffold", "matrix_plan", "blockbench"):
            self.assertIn(name, names)
        blockbench = next(
            item for item in TOOL_DEFINITIONS
            if item["function"]["name"] == "blockbench"
        )
        actions = blockbench["function"]["parameters"]["properties"]["action"]["enum"]
        self.assertIn("preview", actions)


# ----------------------------------------------------------------------
# Acceptance gate
# ----------------------------------------------------------------------
class GateTests(TempDataCase):
    def spec(self) -> ModSpec:
        return derive_spec("添加一个物品", "NeoForge", "1.21.1")

    def test_build_task_synthesises_acceptance_items(self) -> None:
        task = Task("T91-build", "构建", "build", acceptance=["build"])
        report = verify(self.spec(), task, self.tmp, build_ok=False)
        self.assertFalse(report.passed)
        self.assertEqual(report.blocking[0].id, "build")
        report = verify(self.spec(), task, self.tmp, build_ok=True)
        self.assertTrue(report.passed)

    def test_verify_runtime_is_warning_only(self) -> None:
        task = Task("T92-verify", "验证", "verify", acceptance=["runtime"])
        report = verify(self.spec(), task, self.tmp, runtime_ok=False)
        self.assertTrue(report.passed)
        check = report.checks[0]
        self.assertFalse(check.required)
        self.assertFalse(check.blocking)

    def test_acceptance_task_blocks_without_manual_confirmation(self) -> None:
        task = Task(
            "T99-acceptance", "验收", "acceptance",
            acceptance=["acceptance", "build", "lint-all", "audit"],
        )
        report = verify(
            self.spec(), task, self.tmp,
            build_ok=True, manual_ok=None, report=Report(),
        )
        self.assertFalse(report.passed)
        self.assertIn("acceptance", {item.id for item in report.blocking})
        report = verify(
            self.spec(), task, self.tmp,
            build_ok=True, manual_ok=True, report=Report(),
        )
        self.assertTrue(report.passed)


# ----------------------------------------------------------------------
# Kernel bridge
# ----------------------------------------------------------------------
class KernelBridgeTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Kernel Mod",
            mod_id="kernel_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.bridge = KernelBridge(
            self.project.root,
            idea="添加一个红宝石物品",
            loader="Forge",
            mc="1.20.1",
            modid="kernel_mod",
            package="com.example.kernel",
        )

    def test_spec_and_workflow_are_persisted(self) -> None:
        self.assertTrue((self.project.root / ".dma" / "spec.json").is_file())
        self.assertTrue((self.project.root / ".dma" / "workflow.json").is_file())
        self.assertIsNotNone(self.bridge.workflow.task("T91-build"))
        prompt = self.bridge.prompt_block()
        self.assertIn("当前应执行：", prompt)

    def test_record_edit_marks_feature_started(self) -> None:
        feature_id = self.bridge.record_edit(
            "src/main/java/com/example/kernel/item/RubyItem.java"
        )
        self.assertEqual(feature_id, "item")
        task = self.bridge.workflow.task("F-item")
        self.assertEqual(task.status, "running")

    def test_finalize_blocks_on_failed_build(self) -> None:
        text = self.bridge.finalize(build_ok=False, test_ok=False)
        self.assertIn("未通过", text)
        self.assertIn("不能标记为完成", text)
        self.assertEqual(
            self.bridge.workflow.task("T91-build").status, "blocked"
        )

    def test_snapshot_rollback_restores_content(self) -> None:
        target = self.project.root / "build.gradle"
        original = target.read_text(encoding="utf-8")
        snapshot = self.bridge.before_build("test")
        self.assertTrue(snapshot)
        target.write_text(original + "\n// changed\n", encoding="utf-8")
        result = self.bridge.rollback(snapshot)
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_text(encoding="utf-8"), original)


# ----------------------------------------------------------------------
# Edit helpers
# ----------------------------------------------------------------------
class EditHelperTests(TempDataCase):
    SOURCE = """package a;

public class A {
    public int value() {
        return 1;
    }
}
"""

    def test_replace_method_body(self) -> None:
        updated, result = replace_body_text(
            self.SOURCE, class_name="A", method="value",
            body="return 42;",
        )
        self.assertTrue(result.ok)
        self.assertIn("return 42;", updated)
        self.assertNotIn("return 1;", updated)

    def test_insert_member(self) -> None:
        updated, result = insert_member_text(
            self.SOURCE, class_name="A",
            code="public static final String NAME = \"a\";",
            before="public int value()",
        )
        self.assertTrue(result.ok)
        self.assertIn('public static final String NAME = "a";', updated)
        self.assertLess(updated.index("NAME"), updated.index("public int value()"))

    def test_file_cache_chunks_and_hash(self) -> None:
        path = self.tmp / "Chunk.java"
        path.write_text("\n".join(f"line {i}" for i in range(450)), encoding="utf-8")
        cache = FileCache(chunk_lines=200)
        digest = cache.hash(path)
        self.assertEqual(len(digest), 64)
        first = cache.next_chunk(path)
        second = cache.next_chunk(path)
        third = cache.next_chunk(path)
        self.assertEqual((first["start"], first["end"]), (1, 200))
        self.assertEqual((second["start"], second["end"]), (201, 400))
        self.assertTrue(third["done"])
        self.assertTrue(cache.unchanged(path, digest))


# ----------------------------------------------------------------------
# mcdev prepare honesty
# ----------------------------------------------------------------------
class PrepareHonestyTests(TempDataCase):
    def test_wait_join_false_is_not_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout: {"kind": kind, "pid": 1}
        result = manager.prepare(root, "client", wait_join=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["results"][0]["joined"])

    def test_not_joined_is_not_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout: {"kind": kind, "pid": 1}
        manager._sleep = lambda seconds: None
        manager._join_client = lambda root, client, timeout, port: {"kind": "client", "joined": False}
        result = manager.prepare(root, "client", wait_join=True)
        self.assertFalse(result["ok"])

    def test_joined_is_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout: {"kind": kind, "pid": 1}
        manager._sleep = lambda seconds: None
        manager._join_client = lambda root, client, timeout, port: {"kind": "client", "joined": True}
        result = manager.prepare(root, "client", wait_join=True)
        self.assertTrue(result["ok"])


# ----------------------------------------------------------------------
# 2.0 tools
# ----------------------------------------------------------------------
class AssetToolTests(TempDataCase):
    def test_asset_library_bootstrap_and_clone(self) -> None:
        library_root = self.tmp / "asset_project"
        library_root.mkdir()
        assets = library.AssetLibrary(library_root)
        written = assets.bootstrap()
        self.assertEqual(len(written), 5)
        self.assertEqual(len(assets.list()), 5)
        dest = self.tmp / "out" / "ruby_block"
        result = assets.clone("cube", dest, texture="kernel_mod:block/ruby", scale=0.5)
        self.assertTrue(Path(result["dest"]).is_file())
        self.assertEqual(Path(result["dest"]).suffix, ".bbmodel")
        self.assertEqual(result["cubes"], 1)

    def test_blockbench_preview_writes_png(self) -> None:
        from dma.kernel.tools import blockbench

        model = blockbench.full_block("preview")
        target = self.tmp / "preview.png"
        try:
            blockbench.preview(model, target, size=(64, 64))
        except RuntimeError as exc:
            self.skipTest(str(exc))
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_configgen_writes_screen_lang_and_schema(self) -> None:
        fields = configgen.parse_schema(
            {
                "fields": [
                    {"key": "enabled", "type": "bool", "default": True, "comment": "开关"},
                    {"key": "speed", "type": "float", "min": 0, "max": 2, "default": 1.0},
                    {"key": "mode", "type": "enum", "values": ["fast", "slow"]},
                ]
            }
        )
        result = configgen.write(
            self.tmp, package="com.example.kernel", modid="kernel_mod",
            fields=fields, mc="1.21.1",
        )
        self.assertEqual(result["fields"], 3)
        self.assertEqual(len(result["files"]), 4)
        java = self.tmp / "src/main/java/com/example/kernel/client/ModConfigScreen.java"
        source = java.read_text(encoding="utf-8")
        self.assertIn("this.renderBackground(graphics, mouseX, mouseY, partialTick);", source)
        self.assertIn('String key = "config.kernel_mod." + entry.getKey();', source)
        self.assertIn("Component.translatable(key)", source)
        lang = json.loads(
            (self.tmp / "src/main/resources/assets/kernel_mod/lang/zh_cn.json").read_text(encoding="utf-8")
        )
        self.assertIn("config.kernel_mod.title", lang)

    def test_diagnostics_scaffold_is_off_by_default(self) -> None:
        result = diagnostics.write(
            self.tmp, package="com.example.kernel", modid="kernel_mod",
            version="1.2.3", kinds=["dump", "crash", "probe"],
        )
        self.assertIn("flags", result["kinds"])
        self.assertFalse(result["enabledByDefault"])
        base = self.tmp / "src/main/java/com/example/kernel/debug"
        flags = (base / "DebugFlags.java").read_text(encoding="utf-8")
        self.assertIn("BUILD_ENABLED = false", flags)
        self.assertIn('"kernel_mod.debug"', flags)
        dump = (base / "DebugDumpCommand.java").read_text(encoding="utf-8")
        self.assertIn(".requires(source -> DebugFlags.enabled())", dump)
        crash = (base / "CrashContext.java").read_text(encoding="utf-8")
        self.assertIn('MOD_VERSION = "1.2.3"', crash)
        probe = (base / "PerfProbe.java").read_text(encoding="utf-8")
        self.assertIn("PATHFINDING", probe)
        self.assertIn("jdk.jfr.Event", probe)
        self.assertTrue((self.tmp / "docs/debug.md").is_file())

    def test_matrix_plan_and_workflow(self) -> None:
        plan = matrix.plan()
        self.assertEqual(len(plan.cells), 18)
        self.assertGreaterEqual(len(plan.supported()), 1)
        text = matrix.workflow(plan)
        self.assertIn("actions/setup-java@v4", text)
        self.assertIn("${{ matrix.os }}", text)
        self.assertNotIn("os: 'ubuntu-latest'\n            os:", text)

    def test_migration_backup_before_write(self) -> None:
        root = self.tmp / "migration"
        source = root / "src/main/java/a/A.java"
        source.parent.mkdir(parents=True)
        source.write_text('String id = "old_mod:ruby";', encoding="utf-8")
        save = root / "run/saves/world/level.dat"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"save-data")

        change = migration.Migration(
            modid="old_mod",
            from_version="1.0.0",
            to_version="1.1.0",
            ids={"old_mod:ruby": "new_mod:ruby"},
        )
        rows = migration.apply(root, change, dry_run=False)
        self.assertTrue(rows)
        self.assertIn("new_mod:ruby", source.read_text(encoding="utf-8"))
        backup = Path(rows[0]["backup"])
        self.assertTrue((backup / "manifest.json").is_file())
        self.assertTrue((backup / "run/saves/world/level.dat").is_file())


# ----------------------------------------------------------------------
# Agent integration
# ----------------------------------------------------------------------
class AgentIntegrationTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Agent Mod",
            mod_id="agent_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.memory = MemoryStore()
        config = Config()
        config.ai.provider = "pollinations"
        config.max_steps = 4
        self.worker = AgentWorker(
            user_text="添加一个物品",
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

    def test_native_tool_call_is_parsed(self) -> None:
        self.worker.config.native_tools = True
        payload = {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "read_file", "arguments": {"path": "build.gradle"}}
            ],
            "message": {},
        }
        with mock.patch("dma.agent.ai.request_chat_tools", return_value=payload):
            text, calls, native = self.worker._chat([])
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "read_file")
        self.assertEqual(calls[0].call_id, "call_1")
        self.assertIsNotNone(native)

    def test_unsupported_native_falls_back_to_text_protocol(self) -> None:
        self.worker.config.native_tools = True
        reply = '```tool\n{"tool":"list_files","path":"."}\n```'
        with mock.patch(
            "dma.agent.ai.request_chat_tools",
            side_effect=ai.AIToolsUnsupported("no tools"),
        ), mock.patch("dma.agent.ai.request_chat", return_value=reply):
            text, calls, native = self.worker._chat([])
        self.assertIsNone(native)
        self.assertEqual(text, reply)
        self.assertEqual(calls[0].name, "list_files")
        self.assertTrue(self.worker._native_disabled)

    def test_read_file_skips_unchanged_hash(self) -> None:
        run = AgentRun()
        first = self.worker._execute(
            ToolCall(name="read_file", args={"path": "build.gradle"}), run
        )
        match = re.search(r"hash=([0-9a-f]{64})", first)
        self.assertIsNotNone(match)
        second = self.worker._execute(
            ToolCall(
                name="read_file",
                args={"path": "build.gradle", "since_hash": match.group(1)},
            ),
            run,
        )
        self.assertIn("文件未变化", second)

    def test_edit_file_uses_symbol_body_replacement(self) -> None:
        java = self.project.root / "src/main/java/Test.java"
        java.parent.mkdir(parents=True, exist_ok=True)
        java.write_text(
            "package a;\n\npublic class Test {\n"
            "    public int value() {\n        return 1;\n    }\n}\n",
            encoding="utf-8",
        )
        run = AgentRun()
        text = self.worker._execute(
            ToolCall(
                name="edit_file",
                args={"path": "src/main/java/Test.java", "symbol": "Test.value", "new": "return 2;"},
            ),
            run,
        )
        self.assertIn("替换方法体", text)
        self.assertEqual(len(run.pending), 1)
        self.assertIn("return 2;", run.pending[0].content)

    def test_kernel_tools_are_routed(self) -> None:
        for name in ("asset_library", "config_gen", "debug_scaffold", "matrix_plan"):
            self.assertIn(name, __import__("dma.agent", fromlist=["KERNEL_TOOLS"]).KERNEL_TOOLS)

    def test_matrix_plan_tool_returns_cells(self) -> None:
        text = self.worker._execute(
            ToolCall(name="matrix_plan", args={"action": "plan"}), AgentRun()
        )
        data = json.loads(text)
        self.assertEqual(len(data["cells"]), 18)
        self.assertEqual(data["total"], 18)
        self.assertGreaterEqual(data["supported"], 1)


if __name__ == "__main__":
    unittest.main()
