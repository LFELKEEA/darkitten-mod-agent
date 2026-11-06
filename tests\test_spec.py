from __future__ import annotations

import unittest

from dma import spec as spec_mod
from dma.spec import ModSpec, parse_spec

from .base import TempDataCase


def full_payload() -> dict:
    return {
        "spec": {
            "模组名称": "星尘工具",
            "简介": "用星尘升级工具，让挖矿和战斗更顺手",
            "mod_id": "stardust_tools",
            "游戏版本": "1.21.1",
            "加载器": "fabric",
            "运行端": "both",
            "注册内容": [
                {"type": "物品", "id": "stardust_pickaxe", "name": "星尘镐", "desc": "挖矿更快"},
                {"type": "方块", "id": "stardust_block", "name": "星尘块", "desc": "装饰方块"},
            ],
            "玩法": ["右键蓄力，释放星尘冲击", "夜间自动发光"],
            "获取方式": ["工作台合成", "星尘块掉落星尘"],
            "数值": ["耐久 2000", "蓄力冷却 8 秒"],
            "美术资源": ["物品纹理 16x16", "方块纹理 16x16"],
            "验收标准": ["物品能在背包找到", "配方可合成"],
            "风险": ["无前置模组", "双端都要测试"],
            "依赖": ["Fabric API"],
        }
    }


class SpecTests(TempDataCase):
    def test_parse_chinese_aliases_and_normalize(self) -> None:
        spec = parse_spec(full_payload())
        self.assertEqual(spec.title, "星尘工具")
        self.assertEqual(spec.mod_id, "stardust_tools")
        self.assertEqual(spec.mc_version, "1.21.1")
        self.assertEqual(spec.loader, "Fabric")
        self.assertEqual(spec.runtime, "双端")
        self.assertEqual(len(spec.content), 2)
        self.assertEqual(spec.content[0]["name"], "星尘镐")
        self.assertIn("工作台合成", spec.obtain)

    def test_checklist_requires_all_ten_items(self) -> None:
        spec = parse_spec(full_payload())
        passed, total = spec_mod.checklist_score(spec)
        self.assertEqual(total, 10)
        self.assertEqual(passed, 10)
        self.assertEqual(spec_mod.missing_items(spec), [])

        empty = ModSpec()
        passed, total = spec_mod.checklist_score(empty)
        self.assertEqual((passed, total), (0, 10))
        self.assertEqual(len(spec_mod.missing_items(empty)), 10)

    def test_project_defaults_fill_identity(self) -> None:
        class Meta:
            name = "默认名称"
            mod_id = "default_mod"
            mc_version = "1.20.1"
            loader = "NeoForge"
            runtime = "客户端"

        class Project:
            meta = Meta()

        spec = parse_spec({"summary": "只有一个玩法"}, project=Project())
        self.assertEqual(spec.title, "默认名称")
        self.assertEqual(spec.mod_id, "default_mod")
        self.assertEqual(spec.mc_version, "1.20.1")
        self.assertEqual(spec.loader, "NeoForge")
        self.assertEqual(spec.runtime, "客户端")

    def test_apply_patch_keeps_old_fields_and_revision(self) -> None:
        spec = parse_spec(full_payload())
        spec.revision = 2
        old_features = list(spec.features)
        patched = spec_mod.apply_patch(
            spec,
            {"游戏版本": "1.20.1", "玩法": ["右键蓄力，释放星尘冲击", "新增连锁挖矿"]},
        )
        self.assertEqual(patched.revision, 3)
        self.assertEqual(patched.mc_version, "1.20.1")
        self.assertEqual(patched.mod_id, "stardust_tools")
        self.assertEqual(patched.features, ["右键蓄力，释放星尘冲击", "新增连锁挖矿"])
        self.assertNotEqual(old_features, patched.features)
        self.assertEqual(spec.mc_version, "1.21.1")

    def test_save_load_and_confirm_roundtrip(self) -> None:
        spec = parse_spec(full_payload())
        spec.revision = 1
        spec_mod.save_spec(self.tmp, spec)
        loaded = spec_mod.load_spec(self.tmp)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.title, "星尘工具")
        self.assertEqual(loaded.features, spec.features)

        confirmed = spec_mod.confirm_spec(self.tmp, loaded)
        self.assertTrue(confirmed.confirmed())
        self.assertEqual(confirmed.revision, 2)
        again = spec_mod.load_spec(self.tmp)
        assert again is not None
        self.assertTrue(again.confirmed())

    def test_render_manual_contains_sections_and_checklist(self) -> None:
        spec = parse_spec(full_payload())
        manual = spec_mod.render_manual(spec)
        self.assertIn("# 星尘工具 · 规格说明书", manual)
        self.assertIn("需求完成度：**10/10**", manual)
        self.assertIn("星尘镐", manual)
        self.assertIn("- [x] 核心玩法目标", manual)
        self.assertIn("## 验收标准", manual)

    def test_diff_reports_changes(self) -> None:
        spec = parse_spec(full_payload())
        patched = spec_mod.apply_patch(
            spec,
            {"游戏版本": "1.20.1", "玩法": ["右键蓄力，释放星尘冲击"]},
        )
        diff = spec_mod.diff_spec(spec, patched)
        self.assertTrue(any("游戏版本" in line for line in diff))
        self.assertTrue(any("移除玩法功能" in line for line in diff))


if __name__ == "__main__":
    unittest.main()
