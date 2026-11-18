from __future__ import annotations

import unittest
from types import SimpleNamespace

from dma import planner
from dma.agent import AgentWorker


class _Status:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, message: str) -> None:
        self.messages.append(message)


class _Worker:
    def __init__(self, text: str) -> None:
        self.user_text = text
        self.status = _Status()
        self.project = SimpleNamespace(
            meta=SimpleNamespace(
                loader="NeoForge", mc_version="1.21.1", mod_id="deepmod"
            )
        )


class PlannerTest(unittest.TestCase):
    def test_large_plan_has_phases_packages_and_gate(self) -> None:
        plan = planner.build_plan(
            "海洋维度、潜水装备、潮汐机器、BOSS 与任务链",
            loader="neoforge",
            mc="1.21.1",
            scope="large",
        )
        ids = [item["id"] for item in plan["phases"]]
        self.assertIn("bootstrap", ids)
        self.assertIn("reference", ids)
        self.assertIn("registry", ids)
        self.assertIn("assets", ids)
        self.assertIn("verify", ids)
        self.assertGreaterEqual(len(ids), 8)
        self.assertIn("worldgen", plan["packages"])
        self.assertIn("item", plan["packages"])
        self.assertEqual(plan["gate"]["classes"], 40)
        self.assertTrue(plan["online"])

    def test_render_contains_tool_instructions(self) -> None:
        plan = planner.build_plan("自定义 GUI 和网络同步", loader="fabric", mc="1.21.1")
        text = planner.render(plan)
        self.assertIn("大型模组计划", text)
        self.assertIn("resource_ensure", text)
        self.assertIn("mod_reference", text)
        self.assertIn("dev_docs", text)
        self.assertIn("src/main/java", text)

    def test_epic_scope_raises_gate(self) -> None:
        plan = planner.build_plan("无限维度机器网络", scope="epic")
        self.assertEqual(plan["scope"], "epic")
        self.assertGreaterEqual(plan["gate"]["classes"], 80)

    def test_unknown_scope_falls_back_to_large(self) -> None:
        plan = planner.build_plan("一个物品", scope="nonsense")
        self.assertEqual(plan["gate"]["classes"], 40)

    def test_agent_preflight_builds_plan_for_multi_feature_request(self) -> None:
        worker = _Worker("海洋维度、潜水装备、潮汐机器、BOSS 与任务链")
        text = AgentWorker._large_plan_preflight(worker)
        self.assertIn("大型模组计划（自动）", text)
        self.assertIn("worldgen", text)
        self.assertTrue(worker.status.messages)

    def test_agent_preflight_skips_single_feature_request(self) -> None:
        worker = _Worker("加一个物品")
        self.assertEqual(AgentWorker._large_plan_preflight(worker), "")
        self.assertEqual(worker.status.messages, [])


if __name__ == "__main__":
    unittest.main()
