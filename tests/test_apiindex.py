"""Offline API signature index tests."""

from __future__ import annotations

import json
import zipfile
import unittest

from dma import apiindex

from .base import TempDataCase

JAVA = """package com.example.api;

public class RubySword extends ToolItem implements Wieldable {
    public static final int MAX_DAMAGE = 512;

    private int charge = 0;

    public RubySword() {
    }

    public int attack(int amount) {
        return amount * 2;
    }

    private void internalTick() {
    }
}
"""


class ApiIndexTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.jar = self.tmp / "fabric-loader-sources.jar"
        with zipfile.ZipFile(self.jar, "w") as archive:
            archive.writestr("com/example/api/RubySword.java", JAVA)
            archive.writestr("com/example/api/Unrelated.txt", "not java")
        self.index = apiindex.build(self.jar, "fabric")

    def test_counts_skip_private_members(self) -> None:
        counts = self.index["counts"]
        self.assertEqual(counts["classes"], 1)
        self.assertEqual(counts["entries"], 1)
        item = self.index["classes"]["com.example.api.RubySword"]
        names = {method["name"] for method in item["methods"]}
        self.assertIn("<init>", names)
        self.assertIn("attack", names)
        self.assertNotIn("internalTick", names)
        self.assertEqual([field["name"] for field in item["fields"]], ["MAX_DAMAGE"])
        self.assertEqual(item["extends"], "ToolItem")
        self.assertIn("Wieldable", item["implements"])

    def test_query_class_method_and_field(self) -> None:
        classes = apiindex.query(self.index, "RubySword", kind="class")
        self.assertEqual(classes[0]["class"], "com.example.api.RubySword")
        self.assertEqual(classes[0]["hit"], "class")

        methods = apiindex.query(self.index, "attack", kind="method")
        self.assertTrue(methods)
        self.assertEqual(methods[0]["name"], "attack")
        self.assertEqual(methods[0]["params"], ["int"])

        fields = apiindex.query(self.index, "MAX_DAMAGE", kind="field")
        self.assertTrue(fields)
        self.assertEqual(fields[0]["type"], "int")

    def test_auto_query_renders_copyable_lines(self) -> None:
        hits = apiindex.query(self.index, "attack")
        text = apiindex.render(hits)
        self.assertIn("com.example.api.RubySword#attack(int)", text)
        self.assertIn("→ int", text)

    def test_index_is_cached_on_disk(self) -> None:
        path = apiindex.index_path("fabric", self.jar)
        self.assertTrue(path.is_file())
        cached = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(cached["schema"], apiindex.SCHEMA)
        again = apiindex.build(self.jar, "fabric")
        self.assertEqual(again["counts"], self.index["counts"])
        loaded = apiindex.load("fabric", self.jar)
        self.assertIsNotNone(loaded)

    def test_stats_reports_source_and_counts(self) -> None:
        stats = apiindex.stats(self.index)
        self.assertEqual(stats["loader"], "fabric")
        self.assertIn("fabric-loader-sources.jar", stats["source"])
        self.assertFalse(stats["truncated"])


if __name__ == "__main__":
    unittest.main()
