from __future__ import annotations

import unittest
from unittest import mock

from dma import research

from .base import TempDataCase


class ResearchTest(TempDataCase):
    def test_parse_repo_accepts_url_and_plain_name(self) -> None:
        self.assertEqual(
            research.parse_repo("https://github.com/Luke100000/minecraft-comes-alive"),
            "Luke100000/minecraft-comes-alive",
        )
        self.assertEqual(
            research.parse_repo("Luke100000/minecraft-comes-alive.git"),
            "Luke100000/minecraft-comes-alive",
        )
        with self.assertRaises(research.ResearchError):
            research.parse_repo("not a repo")

    def test_repo_tree_filters_prefix(self) -> None:
        payload = {
            "tree": [
                {"type": "blob", "path": "common/src/main/java/A.java"},
                {"type": "tree", "path": "common/src/main/java"},
                {"type": "blob", "path": "neoforge/src/main/java/B.java"},
            ]
        }
        with mock.patch.object(research, "_json", return_value=payload):
            rows = research.repo_tree(
                "Luke100000/minecraft-comes-alive",
                ref="8.1.7+1.21.1",
                path="common",
            )
        self.assertEqual(rows, ["common/src/main/java/A.java"])

    def test_repo_file_rejects_unsafe_path(self) -> None:
        with self.assertRaises(research.ResearchError):
            research.repo_file("owner/repo", "../../etc/passwd")

    def test_search_code_falls_back_to_repos(self) -> None:
        with mock.patch.object(
            research, "grep_search", side_effect=research.ResearchError("429")
        ), mock.patch.object(
            research,
            "repo_search",
            return_value=[
                {
                    "repo": "owner/demo",
                    "url": "https://github.com/owner/demo",
                    "stars": 12,
                    "language": "Java",
                    "description": "demo",
                }
            ],
        ):
            text = research.search_code("interactLivingEntity", kind="auto")
        self.assertIn("owner/demo", text)
        self.assertIn("代码搜索不可用", text)

    def test_reference_bundle_renders_only_matching_files(self) -> None:
        tree = ["src/Player.java", "src/Other.kt", "README.md"]
        with mock.patch.object(research, "repo_tree", return_value=tree), mock.patch.object(
            research, "repo_file", return_value="class Player {}"
        ):
            bundle = research.fetch_reference_bundle(
                "owner/demo", ["player"], ref="main", max_files=2
            )
        self.assertEqual([item.path for item in bundle.files], ["src/Player.java"])
        self.assertIn("class Player", bundle.render())


if __name__ == "__main__":
    unittest.main()
