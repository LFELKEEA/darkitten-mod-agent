from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from dma import reference

from .base import TempDataCase


class FeatureDetectTest(unittest.TestCase):
    def test_detects_chinese_and_english_features(self) -> None:
        features = reference.detect_features("做一个自定义村民交易界面和维度")
        ids = {item.id for item in features}
        self.assertIn("villager", ids)
        self.assertIn("gui", ids)
        self.assertIn("worldgen", ids)

    def test_short_alias_uses_word_boundary(self) -> None:
        # ai 不能命中 said / detail 里的 ai。
        self.assertNotIn("entity", {item.id for item in reference.detect_features("he said details")})
        self.assertIn("entity", {item.id for item in reference.detect_features("custom mob ai")})

    def test_queries_prefer_feature_queries(self) -> None:
        features = reference.detect_features("自定义 GUI")
        queries = reference.queries_for("自定义 GUI", features)
        self.assertIn("custom gui screen", queries)


class IndexAndSnippetTest(TempDataCase):
    def make_source(self) -> Path:
        root = self.tmp / "sources" / "demo" / "1.0.0"
        package = root / "com" / "example"
        package.mkdir(parents=True)
        (package / "DemoScreen.java").write_text(
            "\n".join(
                [
                    "package com.example;",
                    "import net.minecraft.client.gui.screens.Screen;",
                    "public class DemoScreen extends Screen {",
                    "    private final ContainerMenu menu;",
                    "    public DemoScreen(ContainerMenu menu) { this.menu = menu; }",
                    "    public void render(int mouseX, int mouseY) { }",
                    "}",
                ]
            ),
            encoding="utf-8",
        )
        (package / "Unrelated.java").write_text(
            "package com.example;\npublic class Unrelated {}\n", encoding="utf-8"
        )
        return root

    def test_index_extracts_classes_and_methods(self) -> None:
        root = self.make_source()
        data = reference.ensure_index(root)
        self.assertEqual(data["file_count"], 2)
        entry = next(
            item for item in data["files"]
            if any("Screen" in name for name in item["classes"])
        )
        self.assertIn("DemoScreen", entry["classes"])
        self.assertIn("render", entry["methods"])

    def test_search_snippets_ranks_relevant_file(self) -> None:
        root = self.make_source()
        hits = reference.search_snippets("container menu screen", roots=[root], limit=3)
        self.assertTrue(hits)
        self.assertIn("DemoScreen.java", hits[0]["relative"])
        self.assertIn("ContainerMenu", hits[0]["snippet"])

    def test_cached_prepare_skips_download(self) -> None:
        root = self.make_source()
        with mock.patch.object(
            reference.modlibrary,
            "project",
            return_value={"slug": "demo", "title": "Demo", "license": "MIT"},
        ), mock.patch.object(
            reference, "_existing_source", return_value=root
        ), mock.patch.object(
            reference.modlibrary, "download"
        ) as download:
            result = reference.prepare_reference("demo")
        self.assertTrue(result["cached"])
        self.assertEqual(result["files"], 2)
        download.assert_not_called()


class AutoReferenceTest(TempDataCase):
    def test_auto_reference_prepares_and_returns_snippets(self) -> None:
        source = self.tmp / "sources" / "demo" / "1.0.0"
        (source / "com" / "example").mkdir(parents=True)
        (source / "com" / "example" / "TradeScreen.java").write_text(
            "package com.example;\npublic class TradeScreen {\n"
            "  public void openTrade() {}\n}\n",
            encoding="utf-8",
        )
        hits = [
            {
                "id": "abc",
                "slug": "demo",
                "title": "Demo Trade",
                "downloads": 100,
                "description": "trading",
            }
        ]
        prepared = [
            {
                "project": {"title": "Demo Trade"},
                "slug": "demo",
                "sources": str(source),
                "files": 1,
                "classes": 1,
                "license": "MIT",
            }
        ]
        with mock.patch.object(
            reference, "search_references", return_value={
                "features": reference.detect_features("村民交易界面"),
                "queries": ["custom villager profession"],
                "candidates": hits,
                "notes": [],
            }
        ), mock.patch.object(
            reference, "prepare_reference", return_value=prepared[0]
        ):
            result = reference.auto_reference("村民交易界面", loader="forge", mc="1.20.1")
        self.assertTrue(result["prepared"])
        self.assertTrue(result["snippets"])
        text = reference.render_auto(result)
        self.assertIn("TradeScreen.java", text)
        self.assertIn("许可证", text)

    def test_auto_reference_notes_failed_prepare(self) -> None:
        with mock.patch.object(
            reference, "search_references", return_value={
                "features": reference.detect_features("自定义维度"),
                "queries": ["custom dimension"],
                "candidates": [{"slug": "broken", "title": "Broken", "downloads": 1}],
                "notes": [],
            }
        ), mock.patch.object(
            reference, "prepare_reference", side_effect=reference.ReferenceError("下载失败")
        ), mock.patch.object(
            reference, "search_snippets", return_value=[]
        ):
            result = reference.auto_reference("自定义维度")
        self.assertEqual(result["prepared"], [])
        self.assertTrue(any("下载失败" in note for note in result["notes"]))


if __name__ == "__main__":
    unittest.main()
