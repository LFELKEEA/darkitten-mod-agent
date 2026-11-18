"""Contract freeze / drift audit tests."""

from __future__ import annotations

import unittest

from dma.kernel import contract

from .base import TempDataCase

SOURCE = """package com.example;

public class RubySword {
    public static final int DAMAGE = 4;

    public int attack(int amount) {
        return amount + DAMAGE;
    }

    private void secret() {
    }

    private static class Hidden {
        public void nothing() {
        }
    }
}
"""


class ContractTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "project"
        self.java = self.root / "src" / "main" / "java" / "com" / "example" / "RubySword.java"
        self.java.parent.mkdir(parents=True)
        self.java.write_text(SOURCE, encoding="utf-8")
        self.lang = self.root / "src" / "main" / "resources" / "assets" / "demo" / "lang" / "en_us.json"
        self.lang.parent.mkdir(parents=True)
        self.lang.write_text('{"item.demo.ruby_sword": "Ruby Sword"}', encoding="utf-8")

    def test_freeze_and_clean_check(self) -> None:
        frozen = contract.freeze(self.root, note="before refactor")
        counts = frozen["counts"]
        self.assertEqual(counts["classes"], 1)
        self.assertEqual(counts["methods"], 1)  # attack + constructor? private excluded
        self.assertEqual(counts["fields"], 1)
        self.assertEqual(counts["resources"], 1)
        result = contract.check(self.root)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["changed"], [])

    def test_removed_public_method_is_a_violation(self) -> None:
        contract.freeze(self.root)
        self.java.write_text(SOURCE.replace("    public int attack(int amount) {\n        return amount + DAMAGE;\n    }\n", ""), encoding="utf-8")
        result = contract.check(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("attack" in item for item in result["removed"]))
        self.assertIn("存在破坏性变更", contract.report_markdown(result))

    def test_signature_change_is_a_violation(self) -> None:
        contract.freeze(self.root)
        self.java.write_text(
            SOURCE.replace("public int attack(int amount)", "public long attack(int amount)"),
            encoding="utf-8",
        )
        result = contract.check(self.root)
        self.assertFalse(result["ok"])
        self.assertTrue(result["changed"])
        self.assertTrue(any("attack" in item["symbol"] for item in result["changed"]))

    def test_removed_resource_is_reported(self) -> None:
        contract.freeze(self.root)
        self.lang.unlink()
        result = contract.check(self.root)
        self.assertTrue(result["ok"])  # resources are warnings by default
        self.assertTrue(result["resources_removed"])
        self.assertTrue(result["warnings"])
        strict = contract.check(self.root, strict_resources=True)
        self.assertFalse(strict["ok"])

    def test_added_public_surface_is_allowed(self) -> None:
        contract.freeze(self.root)
        self.java.write_text(
            SOURCE.replace(
                "    public int attack(int amount) {",
                "    public int block() {\n        return 1;\n    }\n\n    public int attack(int amount) {",
            ),
            encoding="utf-8",
        )
        result = contract.check(self.root)
        self.assertTrue(result["ok"], result)
        self.assertTrue(any("block" in item for item in result["added"]))

    def test_missing_contract_returns_actionable_reason(self) -> None:
        result = contract.check(self.root)
        self.assertFalse(result["ok"])
        self.assertFalse(result["frozen"])
        self.assertIn("contract_freeze", result["reason"])


if __name__ == "__main__":
    unittest.main()
