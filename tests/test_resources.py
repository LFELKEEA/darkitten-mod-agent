from __future__ import annotations

import json
import unittest

from dma.connectors import Connector, ConnectorError, ConnectorStore, resolve_env
from dma.memory import MemoryStore
from dma.skills import Skill, SkillError, SkillStore

from .base import TempDataCase


class MemoryTests(TempDataCase):
    def test_crud_and_recall(self) -> None:
        store = MemoryStore()
        first = store.add("用 Yarn 1.21.1", project="/p1", kind="decision", tags="映射")
        second = store.add("别用 Mixin 改渲染", project="/p1", kind="pitfall", tags="渲染")
        self.assertEqual(store.count(), 2)
        self.assertEqual(store.get(first)["kind"], "decision")
        items = store.recall("/p1", "Mixin", limit=4)
        self.assertEqual(items[0]["id"], second)
        self.assertIn("渲染", items[0]["tags"])
        store.update(second, "别用 Mixin 改渲染，风险高", tags="渲染,mixin")
        self.assertIn("风险高", store.get(second)["content"])
        self.assertEqual(len(store.list(query="Yarn")), 1)
        store.delete(first)
        self.assertEqual(store.count(), 1)
        target = self.tmp / "export.json"
        store.export_json(target)
        data = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["memories"]), 1)
        store.close()

    def test_empty_rejected(self) -> None:
        store = MemoryStore()
        with self.assertRaises(ValueError):
            store.add("   ")
        store.close()


class SkillTests(TempDataCase):
    def test_builtin_seeded(self) -> None:
        store = SkillStore()
        skills = store.list()
        self.assertGreaterEqual(len(skills), 10)
        ids = {item.id for item in skills}
        self.assertIn("fabric-scaffold", ids)
        self.assertIn("neoforge-scaffold", ids)
        catalog = store.catalog_text()
        self.assertIn("fabric-scaffold", catalog)

    def test_custom_save_disable_delete(self) -> None:
        store = SkillStore()
        store.save(
            Skill(
                id="my-skill",
                name="我的技能",
                description="测试",
                body="正文",
            )
        )
        self.assertEqual(store.get("my-skill").body, "正文\n")
        store.set_enabled("my-skill", False)
        self.assertFalse(store.get("my-skill").enabled)
        self.assertNotIn("my-skill", store.catalog_text())
        self.assertIn("my-skill", store.catalog_text(include_disabled=True))
        store.delete("my-skill")
        self.assertIsNone(store.get("my-skill"))

    def test_builtin_delete_and_reset(self) -> None:
        store = SkillStore()
        with self.assertRaises(SkillError):
            store.delete("fabric-scaffold")
        skill = store.get("fabric-scaffold")
        skill.body = "改坏了"
        store.save(skill)
        self.assertEqual(store.get("fabric-scaffold").body, "改坏了\n")
        store.reset("fabric-scaffold")
        self.assertIn("Fabric 工程搭建", store.get("fabric-scaffold").body)

    def test_invalid_id(self) -> None:
        store = SkillStore()
        with self.assertRaises(SkillError):
            store.save(Skill(id="Bad Id", name="x", description="", body="x"))


class ConnectorTests(TempDataCase):
    def test_builtin_and_crud(self) -> None:
        store = ConnectorStore()
        ids = {item.id for item in store.list()}
        self.assertTrue({"modrinth", "fabric-meta", "github"} <= ids)
        saved = store.save(
            Connector(
                id="My Wiki",
                name="我的百科",
                base_url="https://example.com/api",
                description="测试",
                headers={"Authorization": "Bearer ${DEMO_TOKEN}"},
            )
        )
        self.assertEqual(saved.id, "my-wiki")
        self.assertEqual(store.get("my-wiki").headers["Authorization"], "Bearer ${DEMO_TOKEN}")
        store.set_enabled("my-wiki", False)
        self.assertNotIn("my-wiki", store.catalog_text())
        with self.assertRaises(ConnectorError):
            store.delete("modrinth")
        store.delete("my-wiki")
        self.assertIsNone(store.get("my-wiki"))

    def test_invalid_base_url(self) -> None:
        store = ConnectorStore()
        with self.assertRaises(ConnectorError):
            store.save(Connector(id="bad", name="bad", base_url="ftp://example.com"))

    def test_resolve_env(self) -> None:
        import os

        os.environ["DMA_TEST_TOKEN"] = "secret"
        try:
            self.assertEqual(resolve_env("Bearer ${DMA_TEST_TOKEN}"), "Bearer secret")
            self.assertEqual(resolve_env("${DMA_MISSING_TOKEN}"), "")
        finally:
            os.environ.pop("DMA_TEST_TOKEN", None)


if __name__ == "__main__":
    unittest.main()
