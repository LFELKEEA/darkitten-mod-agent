from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dma import devdocs

from .base import TempDataCase


class SourcesItemTest(TempDataCase):
    def test_fabric_sources_use_maven_and_mirror(self) -> None:
        item = devdocs.sources_item("fabric", "1.21.1", "0.16.9")
        self.assertIsNotNone(item)
        self.assertEqual(item["kind"], "sources")
        self.assertTrue(item["url"].endswith("fabric-loader-0.16.9-sources.jar"))
        self.assertIn("bmclapi2.bangbang93.com", item["mirror"])

    def test_fabric_sources_fall_back_to_loader_meta(self) -> None:
        from dma.studio_engine import versions

        with mock.patch.object(devdocs, "_loader_version", return_value=""), \
                mock.patch.object(versions, "_fabric_loader", return_value="0.20.1"):
            item = devdocs.sources_item("fabric", "1.21.1")
        self.assertIsNotNone(item)
        self.assertTrue(item["url"].endswith("fabric-loader-0.20.1-sources.jar"))

    def test_quilt_sources_use_maven(self) -> None:
        item = devdocs.sources_item("quilt", "1.21.1", "0.27.0")
        self.assertTrue(item["url"].endswith("quilt-loader-0.27.0-sources.jar"))

    def test_forge_sources_reuse_catalog(self) -> None:
        catalog = mock.Mock()
        catalog.javadoc.return_value = {
            "loader": "neoforge",
            "kind": "sources",
            "url": "https://example/neoforge-sources.jar",
            "name": "neoforge-sources.jar",
        }
        with mock.patch.object(devdocs, "_catalog", return_value=catalog):
            item = devdocs.sources_item("neoforge", "1.21.1", "21.1.80")
        self.assertEqual(item["kind"], "sources")

    def test_unknown_loader_defaults_to_neoforge_docs(self) -> None:
        self.assertEqual(devdocs._loader("unknown"), "neoforge")


class ArchiveSearchTest(TempDataCase):
    def make_jar(self) -> Path:
        path = self.tmp / "sources.jar"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "com/example/ModBlocks.java",
                "package com.example;\n"
                "public class ModBlocks {\n"
                "  public static final DeferredRegister.Blocks BLOCKS = ...;\n"
                "  public static void register() {}\n}\n",
            )
            archive.writestr(
                "com/example/Other.java",
                "package com.example;\npublic class Other {}\n",
            )
        return path

    def test_inspect_archive_returns_matching_entry(self) -> None:
        hits = devdocs.inspect_archive(self.make_jar(), "DeferredRegister BLOCKS", limit=5)
        self.assertTrue(hits)
        self.assertIn("ModBlocks.java", hits[0]["entry"])
        self.assertIn("DeferredRegister", hits[0]["snippet"])

    def test_lookup_uses_online_sources(self) -> None:
        jar = self.make_jar()
        with mock.patch.object(devdocs, "ensure_sources", return_value=jar):
            result = devdocs.lookup(
                "DeferredRegister", loader="neoforge", mc="1.21.1"
            )
        self.assertTrue(result["hits"])
        self.assertEqual(result["source"], str(jar))
        text = devdocs.render(result)
        self.assertIn("官方文档", text)
        self.assertIn("ModBlocks.java", text)

    def test_lookup_falls_back_to_code_search(self) -> None:
        with mock.patch.object(devdocs, "ensure_sources", return_value=None), mock.patch.object(
            devdocs.research, "search_code", return_value="- owner/repo · src/A.java"
        ):
            result = devdocs.lookup("BlockEntity", loader="fabric", mc="1.21.1")
        self.assertIn("owner/repo", result.get("code_search", ""))
        self.assertIn("owner/repo", devdocs.render(result))


class StatusTest(TempDataCase):
    def test_status_lists_official_pages(self) -> None:
        data = devdocs.status()
        self.assertIn("fabric", data["official"])
        self.assertIn("neoforge", data["official"])
        self.assertIn("root", data)


if __name__ == "__main__":
    unittest.main()
