"""在线模型/皮肤资源：来源描述、结果归一化与下载防护。"""

from __future__ import annotations

import unittest
from unittest import mock

from dma import artresources
from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.memory import MemoryStore
from dma.skills import SkillStore

from .base import TempDataCase


class ProviderTest(unittest.TestCase):
    def test_providers_cover_expected_sources(self) -> None:
        ids = [item["id"] for item in artresources.providers()]
        for provider_id in (
            "modrinth",
            "littleskin",
            "littleskin_cape",
            "ysm",
            "blockbench",
            "mcmodels",
            "planetminecraft",
        ):
            self.assertIn(provider_id, ids)

    def test_unknown_provider_raises(self) -> None:
        with self.assertRaises(artresources.ArtResourceError):
            artresources.provider("nope")

    def test_browse_url_uses_search_template(self) -> None:
        self.assertEqual(
            artresources.browse_url("mcmodels", "cat"),
            "https://mcmodels.net/?s=cat",
        )
        self.assertEqual(
            artresources.browse_url("planetminecraft", "猫"),
            "https://www.planetminecraft.com/resources/?keywords=%E7%8C%AB",
        )
        self.assertEqual(
            artresources.browse_url("blockbench"),
            "https://www.blockbench.net/gallery",
        )


class SearchNormalizeTest(unittest.TestCase):
    def test_modrinth_rows_are_downloadable(self) -> None:
        payload = {
            "hits": [
                {
                    "project_id": "abc",
                    "slug": "cool-pack",
                    "title": "Cool Pack",
                    "author": "someone",
                    "description": "desc",
                    "icon_url": "https://cdn.modrinth.com/icon.png",
                    "downloads": 1234,
                    "categories": ["decoration"],
                    "license": "MIT",
                }
            ]
        }
        with mock.patch.object(artresources, "_get_json", return_value=payload):
            rows = artresources.search("modrinth", "cool", limit=5)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], "abc")
        self.assertEqual(row["kind"], "resourcepack")
        self.assertEqual(
            row["page_url"], "https://modrinth.com/resourcepack/cool-pack"
        )
        self.assertTrue(row["downloadable"])

    def test_littleskin_uses_keyword_and_limit(self) -> None:
        payload = {
            "data": [
                {"tid": 11, "name": "猫耳", "nickname": "a", "type": "steve", "likes": 3},
                {"tid": 12, "name": "披风", "nickname": "b", "type": "cape"},
            ]
        }
        captured: dict = {}

        def fake(url: str, **_kwargs):
            captured["url"] = url
            return payload

        with mock.patch.object(artresources, "_get_json", side_effect=fake):
            rows = artresources.search("littleskin", "猫", limit=1)
        self.assertIn("keyword=%E7%8C%AB", captured["url"])
        self.assertIn("filter=skin", captured["url"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "11")
        self.assertEqual(rows[0]["preview_url"], "https://littleskin.cn/preview/11")
        self.assertEqual(rows[0]["kind"], "skin")

    def test_ysm_filters_tree_by_name(self) -> None:
        with mock.patch.object(
            artresources,
            "_ysm_tree",
            return_value=["A/Lex_Ninjia.ysm", "B/Other.zip"],
        ):
            rows = artresources.search("ysm", "lex", limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "A/Lex_Ninjia.ysm")
        self.assertEqual(rows[0]["kind"], "ysm")


class DownloadTest(TempDataCase):
    def test_littleskin_download_writes_png(self) -> None:
        dest = self.tmp / "out"
        with mock.patch.object(
            artresources,
            "_get_json",
            return_value={"hash": "deadbeef", "name": "猫皮"},
        ), mock.patch.object(artresources, "_get_bytes", return_value=b"PNG"):
            path = artresources.download_item("littleskin", "42", dest)
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), b"PNG")
        self.assertTrue(path.name.startswith("42_"))

    def test_download_url_rejects_unknown_host(self) -> None:
        with self.assertRaises(artresources.ArtResourceError):
            artresources.download_url("https://example.com/x.png", self.tmp / "out")

    def test_download_url_accepts_whitelisted_host(self) -> None:
        with mock.patch.object(artresources, "_get_bytes", return_value=b"PNG"):
            path = artresources.download_url(
                "https://cdn.modrinth.com/data/x/icon.png", self.tmp / "out"
            )
        self.assertTrue(path.is_file())

    def test_link_provider_download_is_rejected(self) -> None:
        with self.assertRaises(artresources.ArtResourceError):
            artresources.download_item("planetminecraft", "1", self.tmp / "out")


class FormatTest(unittest.TestCase):
    def test_format_lists_download_hint(self) -> None:
        text = artresources.format_results(
            [
                {
                    "provider": "littleskin",
                    "id": "9",
                    "title": "猫",
                    "page_url": "https://littleskin.cn/skinlib/show/9",
                    "downloadable": True,
                }
            ]
        )
        self.assertIn("[猫]", text)
        self.assertIn("online_assets action=download", text)
        self.assertIn("provider=littleskin", text)


class AgentToolTest(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.memory = MemoryStore()
        self.worker = AgentWorker(
            user_text="",
            history=[],
            project=None,
            config=Config(),
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
        )

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def test_search_action_returns_markdown(self) -> None:
        sample = [
            {
                "provider": "littleskin",
                "id": "9",
                "title": "猫",
                "page_url": "https://littleskin.cn/skinlib/show/9",
                "downloadable": True,
            }
        ]
        with mock.patch.object(artresources, "search", return_value=sample):
            text = self.worker._execute(
                ToolCall(
                    "online_assets",
                    {"action": "search", "provider": "littleskin", "query": "猫"},
                ),
                AgentRun(),
            )
        self.assertIn("[猫]", text)
        self.assertIn("provider=littleskin", text)

    def test_download_requires_id(self) -> None:
        text = self.worker._execute(
            ToolCall(
                "online_assets",
                {"action": "download", "provider": "ysm"},
            ),
            AgentRun(),
        )
        self.assertIn("需要 provider 和 id", text)

    def test_link_provider_returns_browse_url(self) -> None:
        text = self.worker._execute(
            ToolCall(
                "online_assets",
                {"action": "download", "provider": "mcmodels", "id": "x"},
            ),
            AgentRun(),
        )
        self.assertIn("https://mcmodels.net/", text)


if __name__ == "__main__":
    unittest.main()
