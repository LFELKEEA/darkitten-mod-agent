"""Deterministic workflow / acceptance gate bridge tests."""

from __future__ import annotations

import unittest

from dma.kernel_bridge import KernelBridge
from dma.project import create_project

from .base import TempDataCase


class WorkflowBridgeTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Workflow Mod",
            mod_id="workflow_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.bridge = KernelBridge(
            self.project.root,
            idea="添加一个红宝石物品",
            loader="Forge",
            mc="1.20.1",
            modid="workflow_mod",
            package="com.example.workflow",
        )

    def test_status_lists_current_and_next_tasks(self) -> None:
        status = self.bridge.status()
        self.assertTrue(status["ok"])
        self.assertGreater(status["progress"]["total"], 0)
        self.assertTrue(status["next"])
        self.assertEqual(status["next"][0]["id"], "T00-spec")

    def test_complete_task_without_evidence_is_blocked(self) -> None:
        self.bridge.last_build_ok = None
        result = self.bridge.complete_task("T91-build")
        self.assertFalse(result["ok"])
        self.assertTrue(result["blocked"])
        self.assertEqual(
            self.bridge.workflow.task("T91-build").status, "pending"
        )

    def test_complete_task_with_build_evidence_succeeds(self) -> None:
        self.bridge.last_build_ok = True
        result = self.bridge.complete_task("T91-build", evidence=["build=manual"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            self.bridge.workflow.task("T91-build").status, "done"
        )

    def test_force_complete_records_manual_override(self) -> None:
        self.bridge.last_build_ok = False
        result = self.bridge.complete_task("T91-build", force=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(
            self.bridge.workflow.task("T91-build").detail, "人工强制确认"
        )

    def test_failed_task_blocks_downstream(self) -> None:
        blocked = self.bridge.workflow.fail("T00-spec", "需求缺失")
        self.assertIn("T01-scaffold", blocked)
        self.assertEqual(
            self.bridge.workflow.task("T01-scaffold").status, "blocked"
        )

    def test_replan_merges_new_features_and_keeps_status(self) -> None:
        self.bridge.last_build_ok = True
        self.bridge.complete_task("T91-build")
        before_total = len(self.bridge.workflow.tasks)
        result = self.bridge.replan("添加红宝石物品和红宝石剑")
        self.assertTrue(result["ok"], result)
        self.assertGreaterEqual(len(self.bridge.workflow.tasks), before_total)
        self.assertEqual(
            self.bridge.workflow.task("T91-build").status, "done"
        )


if __name__ == "__main__":
    unittest.main()
