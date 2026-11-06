from __future__ import annotations

import unittest
from unittest import mock

from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.memory import MemoryStore
from dma.porting import Issue, VerifyReport
from dma.project import create_project
from dma.skills import SkillStore

from .base import TempDataCase


class AgentPortingToolTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.memory = MemoryStore()

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def _worker(self, project=None, stage: str = "building") -> AgentWorker:
        config = Config()
        config.ai.provider = "pollinations"
        return AgentWorker(
            user_text="移植这个 JAR",
            history=[],
            project=project,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
            stage=stage,
        )

    def test_port_from_jar_works_without_project_and_switches(self) -> None:
        worker = self._worker(project=None, stage="draft")
        jar = self.tmp / "Demo.jar"
        jar.write_bytes(b"not-a-real-jar")
        project = create_project(
            self.tmp / "projects",
            name="Ported",
            mod_id="ported",
            mc_version="1.21.1",
            loader="NeoForge",
            task_type="移植到其他版本/加载器",
        )
        result = {
            "root": str(project.root),
            "mod_id": "ported",
            "mod_name": "Ported",
            "package": "net.ported",
            "class_name": "PortedMod",
            "class_count": 12,
            "written_resources": 8,
            "item_ids": ["widget"],
            "mca_jar": "mca-neoforge.jar",
            "verify": {
                "issues": [
                    {
                        "level": "error",
                        "code": "mca_item_interact",
                        "title": "道具没有实现对 MCA 村民的交互",
                    }
                ]
            },
        }
        with mock.patch("dma.agent.porting.create_port_project", return_value=result), mock.patch(
            "dma.agent.project_mod.load_project", return_value=project
        ):
            text = worker._execute(
                ToolCall(name="port_from_jar", args={"jar": str(jar), "dependencies": "mca"}),
                AgentRun(),
            )
        self.assertIn("移植工程已生成", text)
        self.assertIn("mca_item_interact", text)
        self.assertIs(worker.project, project)
        self.assertEqual(worker.stage, "building")

    def test_verify_mod_blocks_completion_on_errors(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo",
            mc_version="1.21.1",
            loader="NeoForge",
            task_type="创建全新模组",
        )
        worker = self._worker(project=project, stage="building")
        report = VerifyReport(root=str(project.root))
        report.issues.append(
            Issue(
                level="error",
                code="source_item_not_registered",
                title="源 JAR 的物品 widget 还没有注册",
                detail="",
                path="",
                fix="",
            )
        )
        with mock.patch("dma.agent.porting.verify_project", return_value=report):
            text = worker._execute(ToolCall(name="verify_mod", args={}), AgentRun())
        self.assertIn("验收未通过", text)
        self.assertIn("source_item_not_registered", text)

    def test_verify_mod_reports_success(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo",
            mc_version="1.21.1",
            loader="NeoForge",
            task_type="创建全新模组",
        )
        worker = self._worker(project=project, stage="building")
        with mock.patch(
            "dma.agent.porting.verify_project", return_value=VerifyReport(root=str(project.root))
        ):
            text = worker._execute(ToolCall(name="verify_mod", args={}), AgentRun())
        self.assertIn("验收通过", text)

    def test_verify_mod_requires_project(self) -> None:
        worker = self._worker(project=None)
        text = worker._execute(ToolCall(name="verify_mod", args={}), AgentRun())
        self.assertIn("当前没有打开工程", text)

    def test_port_status_reports_pending_classes(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo",
            mc_version="1.21.1",
            loader="NeoForge",
            task_type="移植到其他版本/加载器",
        )
        source = project.root / "reference/decompiled/OldMod/Hypnosis.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("package oldmod;\npublic class Hypnosis {}\n", encoding="utf-8")
        worker = self._worker(project=project, stage="building")
        text = worker._execute(ToolCall(name="port_status", args={}), AgentRun())
        self.assertIn("未处理：1", text)
        self.assertIn("Hypnosis", text)
        self.assertTrue((project.root / "PORT_STATUS.json").is_file())
        self.assertTrue((project.root / "PORT_EXCLUDE.md").is_file())


if __name__ == "__main__":
    unittest.main()
