from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dma.agent import AgentRun, AgentWorker, ToolCall


class StudioAgentToolTest(unittest.TestCase):
    def _worker(self, project_root: str) -> AgentWorker:
        worker = AgentWorker.__new__(AgentWorker)
        worker.project = None
        worker.config = SimpleNamespace(project_root=project_root)
        worker._cancel = None
        worker._build_log = lambda text: None
        worker.project_created = SimpleNamespace(emit=lambda path: None)
        return worker

    def test_studio_plan_dispatch(self) -> None:
        worker = self._worker(tempfile.mkdtemp(prefix="dma-agent-"))
        call = ToolCall(
            "studio_plan",
            {"idea": "深海遗迹与潮汐能机器", "loader": "Forge", "mc": "1.20.1", "scope": "small"},
        )
        text = worker._execute(call, AgentRun())
        self.assertIn("mod-spec.json", text) or self.assertIn("模组 ID", text)
        self.assertIn("内容总表", text)

    def test_studio_generate_dispatch(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="dma-agent-gen-"))
        worker = self._worker(str(root))
        call = ToolCall(
            "studio_generate",
            {
                "idea": "发光蘑菇森林与采集烹饪",
                "name": "Glow Grove",
                "loader": "Fabric",
                "mc": "1.21.1",
                "scope": "small",
                "audit": True,
            },
        )
        text = worker._execute(call, AgentRun())
        self.assertIn("工程目录", text)
        self.assertTrue(list(root.glob("*/mod-spec.json")))


if __name__ == "__main__":
    unittest.main()
