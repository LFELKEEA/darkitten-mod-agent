from __future__ import annotations

import json
import unittest
import zipfile

from dma import spec as spec_mod
from dma import workflow
from dma.spec import parse_spec

from .base import TempDataCase

from .test_spec import full_payload


def write_fake_jar(root, name: str = "test_mod-1.0.0.jar") -> None:
    folder = root / "build" / "libs"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    with zipfile.ZipFile(target, "w") as zf:
        zf.writestr(
            "fabric.mod.json",
            json.dumps(
                {
                    "id": "test_mod",
                    "name": "Test Mod",
                    "depends": {"minecraft": "1.21.1"},
                }
            ),
        )
        zf.writestr(
            "assets/test_mod/lang/zh_cn.json",
            json.dumps({"item.test_mod.gem": "宝石"}, ensure_ascii=False),
        )
        zf.writestr("assets/test_mod/models/item/gem.json", "{}")
        zf.writestr("assets/test_mod/models/block/gem_block.json", "{}")
        zf.writestr("assets/test_mod/blockstates/gem_block.json", "{}")
        zf.writestr("assets/test_mod/textures/item/gem.png", b"\x89PNG\r\n\x1a\n")
        zf.writestr("assets/test_mod/textures/entity/gem_spirit.png", b"\x89PNG")
        zf.writestr("assets/test_mod/sounds.json", json.dumps({"gem.use": {}}))
        zf.writestr("data/test_mod/recipe/gem_from_block.json", "{}")
        zf.writestr("data/test_mod/loot_table/entities/gem_spirit.json", "{}")


class WorkflowTests(TempDataCase):
    def test_plan_tasks_follows_spec(self) -> None:
        spec = parse_spec(full_payload())
        tasks = workflow.plan_tasks(spec)
        ids = [item["id"] for item in tasks]
        self.assertIn("scaffold", ids)
        self.assertIn("assets", ids)
        self.assertIn("recipes", ids)
        self.assertTrue(any(item.startswith("feature_") for item in ids))

        bare = spec_mod.ModSpec(title="空规格", summary="只有目标")
        bare_ids = [item["id"] for item in workflow.plan_tasks(bare)]
        self.assertNotIn("assets", bare_ids)
        self.assertNotIn("recipes", bare_ids)

    def test_confirm_creates_tasks_and_moves_to_building(self) -> None:
        spec = parse_spec(full_payload())
        spec.revision = 1
        spec_mod.save_spec(self.tmp, spec)

        confirmed, state = workflow.confirm(self.tmp, spec)
        self.assertTrue(confirmed.confirmed())
        self.assertEqual(state.stage, "building")
        self.assertTrue(state.tasks)
        self.assertEqual(state.spec_revision, confirmed.revision)

        state = workflow.complete_task(self.tmp, state, "scaffold")
        done, total = state.task_counts()
        self.assertEqual(done, 1)
        self.assertEqual(total, len(state.tasks))
        reloaded = workflow.load_state(self.tmp, confirmed)
        self.assertEqual(reloaded.stage, "building")
        self.assertEqual(reloaded.task_counts()[0], 1)

    def test_load_state_defaults_by_spec_status(self) -> None:
        self.assertEqual(workflow.load_state(self.tmp).stage, "draft")

        spec = spec_mod.ModSpec(title="草稿", summary="玩法")
        spec_mod.save_spec(self.tmp, spec)
        self.assertEqual(workflow.load_state(self.tmp, spec).stage, "review")

        workflow.confirm(self.tmp, spec)
        self.assertEqual(workflow.load_state(self.tmp, spec).stage, "building")

    def test_inspect_real_jar(self) -> None:
        write_fake_jar(self.tmp)
        inventory = workflow.inspect_artifacts(self.tmp)
        self.assertEqual(inventory.jar, "build/libs/test_mod-1.0.0.jar")
        self.assertEqual(inventory.mod_id, "test_mod")
        self.assertEqual(inventory.mod_name, "Test Mod")
        self.assertEqual(inventory.mc_version, "1.21.1")
        self.assertIn("gem", inventory.items)
        self.assertIn("gem_block", inventory.blocks)
        self.assertIn("gem_spirit", inventory.entities)
        self.assertIn("gem_from_block", inventory.recipes)
        self.assertIn("gem", inventory.textures)
        self.assertIn("gem.use", inventory.sounds)
        self.assertEqual(inventory.lang.get("item.test_mod.gem"), "宝石")

        manual = workflow.render_artifact_manual(
            parse_spec(full_payload()), inventory
        )
        self.assertIn("Test Mod · 成品说明书", manual)
        self.assertIn("物品：1 个", manual)
        self.assertIn("宝石（gem）", manual)
        self.assertIn("gem_from_block", manual)
        self.assertIn("由最终 JAR 的真实文件结构生成", manual)

    def test_finalize_build_writes_manual_and_marks_done(self) -> None:
        spec = parse_spec(full_payload())
        spec.revision = 1
        spec_mod.save_spec(self.tmp, spec)
        workflow.confirm(self.tmp, spec)
        write_fake_jar(self.tmp)

        state, inventory, manual = workflow.finalize_build(self.tmp, True, spec)
        self.assertEqual(state.stage, "done")
        self.assertTrue(state.last_build_ok)
        self.assertEqual(state.jar, inventory.jar)
        self.assertTrue((self.tmp / workflow.MANUAL_FILE).is_file())
        self.assertTrue((self.tmp / ".dma" / "manual.md").is_file())
        self.assertIn("成品说明书", manual)
        build_task = next(item for item in state.tasks if item["id"] == "build")
        self.assertEqual(build_task["status"], "done")

        failed, _, _ = workflow.finalize_build(self.tmp, False, spec)
        self.assertEqual(failed.stage, "building")
        self.assertFalse(failed.last_build_ok)

    def test_finalize_without_jar_does_not_mark_done(self) -> None:
        spec = parse_spec(full_payload())
        spec.revision = 1
        spec_mod.save_spec(self.tmp, spec)
        workflow.confirm(self.tmp, spec)
        state, inventory, manual = workflow.finalize_build(self.tmp, True, spec)
        self.assertEqual(inventory.jar, "")
        self.assertEqual(state.stage, "building")
        self.assertTrue(state.last_build_ok)
        self.assertTrue((self.tmp / workflow.MANUAL_FILE).is_file())
        self.assertIn("尚未生成", manual)

    def test_failure_analysis_categories(self) -> None:
        java = workflow.analyze_build_failure(
            "error: invalid target release: 21\nUnsupported class file major version 65"
        )
        self.assertEqual(java.category, "Java 版本")
        self.assertTrue(java.auto_fixable)

        symbol = workflow.analyze_build_failure(
            "Foo.java:12: error: cannot find symbol\n  symbol: class Bar"
        )
        self.assertEqual(symbol.category, "编译")
        self.assertIn("Foo.java:12", symbol.location)

        network = workflow.analyze_build_failure(
            "Could not resolve all dependencies\nPKIX path building failed"
        )
        self.assertEqual(network.category, "依赖下载")

        unknown = workflow.analyze_build_failure("something strange happened")
        self.assertEqual(unknown.category, "未知")
        self.assertIn("失败分析", unknown.render())

    def test_stage_block_reports_progress(self) -> None:
        spec = parse_spec(full_payload())
        workflow.confirm(self.tmp, spec)
        state = workflow.load_state(self.tmp, spec)
        block = workflow.stage_block(state, spec)
        self.assertIn("阶段：施工中", block)
        self.assertIn("需求清单：10/10", block)
        self.assertIn("施工任务：0/", block)


if __name__ == "__main__":
    unittest.main()
