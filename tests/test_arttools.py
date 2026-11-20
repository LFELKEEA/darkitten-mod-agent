from __future__ import annotations

import json
import unittest
from pathlib import Path

from dma.arttools import validate_resources

from .base import TempDataCase


class ResourceCoverageTests(TempDataCase):
    def make_project(self, *, datagen: bool = False) -> Path:
        root = self.tmp / "proj"
        res = root / "src" / "main" / "resources"
        java = root / "src" / "main" / "java" / "com" / "demo" / "registry"
        (res / "META-INF").mkdir(parents=True)
        java.mkdir(parents=True)
        (res / "META-INF" / "mods.toml").write_text(
            'modLoader="javafml"\n[[mods]]\nmodId="demo"\n', encoding="utf-8"
        )
        (java / "ModBlocks.java").write_text(
            "package com.demo.registry;\n"
            "public final class ModBlocks {\n"
            "  public static final DeferredRegister<Block> BLOCKS = null;\n"
            "  public static final RegistryObject<Block> A = register(\"demo_block\", null);\n"
            "  public static final RegistryObject<Block> B = registerNoItem(\"demo_portal\", null);\n"
            "}\n",
            encoding="utf-8",
        )
        (java / "ModItems.java").write_text(
            "package com.demo.registry;\n"
            "public final class ModItems {\n"
            "  public static final DeferredRegister<Item> ITEMS = null;\n"
            "  public static final RegistryObject<Item> I = register(\"demo_item\", null);\n"
            "}\n",
            encoding="utf-8",
        )
        if datagen:
            (java / "DataGen.java").write_text(
                "class DataGen { void on(GatherDataEvent event) {} }", encoding="utf-8"
            )
        return res

    def test_reports_missing_blockstate_item_model_and_lang(self) -> None:
        res = self.make_project()
        assets = res / "assets" / "demo"
        (assets / "blockstates").mkdir(parents=True)
        (assets / "blockstates" / "demo_block.json").write_text("{}", encoding="utf-8")
        (assets / "models" / "item").mkdir(parents=True)
        (assets / "models" / "item" / "demo_item.json").write_text("{}", encoding="utf-8")
        (assets / "lang").mkdir(parents=True)
        (assets / "lang" / "zh_cn.json").write_text(
            json.dumps({"block.demo.demo_block": "示例方块"}), encoding="utf-8"
        )
        (assets / "lang" / "en_us.json").write_text(
            json.dumps({"block.demo.demo_block": "Demo Block"}), encoding="utf-8"
        )

        report = validate_resources(str(res))

        self.assertIn("注册项资源覆盖", report)
        self.assertIn("demo_portal", report)  # 缺 blockstate
        self.assertIn("models/item", report)  # 缺 demo_block 的物品模型
        self.assertIn("缺少", report)
        self.assertNotIn("未发现问题", report)

    def test_datagen_without_generated_assets_still_audits(self) -> None:
        """只有数据生成器但没生成产物时，手写资源照样要检查。"""
        res = self.make_project(datagen=True)
        report = validate_resources(str(res))
        self.assertIn("注册项资源覆盖", report)

    def test_datagen_with_generated_assets_skips_coverage(self) -> None:
        res = self.make_project(datagen=True)
        generated = res.parents[2] / "src" / "generated" / "resources"
        target = generated / "assets" / "demo" / "blockstates"
        target.mkdir(parents=True)
        (target / "demo_block.json").write_text("{}", encoding="utf-8")
        report = validate_resources(str(res))
        self.assertNotIn("注册项资源覆盖", report)


if __name__ == "__main__":
    unittest.main()
