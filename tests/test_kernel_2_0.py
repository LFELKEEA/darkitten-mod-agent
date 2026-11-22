"""Regression tests for the 2.0 kernel, tools and agent integration."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

from dma import ai
from dma.agent import AgentRun, AgentWorker, ToolCall
from dma.config import Config
from dma.connectors import ConnectorStore
from dma.kernel.rules import catalog, coverage, matrix_markdown
from dma.kernel.edit import FileCache, insert_member_text, replace_body_text
from dma.kernel.findings import Report
from dma.kernel.gate import verify
from dma.kernel.spec import ModSpec
from dma.kernel.spec import derive_spec
from dma.kernel.tools import configgen, diagnostics, library, matrix, migration
from dma.kernel.workflow import Task
from dma.kernel_bridge import KernelBridge
from dma.mcdev.process import LaunchManager
from dma.memory import MemoryStore
from dma.project import create_project
from dma.skills import SkillStore
from dma.tool_schema import TOOL_DEFINITIONS

from .base import TempDataCase


# ----------------------------------------------------------------------
# Catalog / schema
# ----------------------------------------------------------------------
class RuleCatalogTests(unittest.TestCase):
    def test_catalog_covers_all_176_items(self) -> None:
        rows = catalog()
        self.assertEqual(len(rows), 176)
        self.assertEqual(len({item.id for item in rows}), 176)
        per_level = {}
        for item in rows:
            per_level[item.level] = per_level.get(item.level, 0) + 1
        self.assertEqual(per_level, {"P0": 12, "P1": 40, "P2": 60, "P3": 44, "TOOL": 20})

    def test_every_rule_has_a_checker_and_automation(self) -> None:
        for item in catalog():
            self.assertTrue(item.checker, f"{item.id} 没有 checker")
            self.assertIn(item.automation, {"auto", "assist", "manual"}, item.id)

    def test_coverage_matches_catalog(self) -> None:
        cov = coverage()
        self.assertEqual(cov["total"]["total"], 176)
        self.assertEqual(cov["total"]["auto"], 136)
        self.assertEqual(cov["total"]["assist"], 40)
        self.assertEqual(cov["total"]["manual"], 0)

    def test_matrix_markdown_lists_every_rule(self) -> None:
        text = matrix_markdown()
        self.assertIn("# DMA 2.0 能力矩阵（176 项）", text)
        for item in catalog():
            self.assertIn(f"| {item.id} |", text)


class ToolSchemaTests(unittest.TestCase):
    def test_tool_definitions_are_unique(self) -> None:
        names = [item["function"]["name"] for item in TOOL_DEFINITIONS]
        self.assertEqual(len(names), 117)
        self.assertEqual(len(names), len(set(names)))

    def test_texture_and_player_tools_are_exposed(self) -> None:
        names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
        for name in ("texture_gen", "entity_texture", "game_playtest",
                     "game_bot_playtest", "game_window", "mcdev_move",
                     "mcdev_goto", "mcdev_look", "mcdev_attack", "mcdev_mine",
                     "mcdev_use_item"):
            self.assertIn(name, names)

    def test_new_kernel_tools_are_exposed(self) -> None:
        names = {item["function"]["name"] for item in TOOL_DEFINITIONS}
        for name in ("asset_library", "config_gen", "debug_scaffold", "matrix_plan", "blockbench"):
            self.assertIn(name, names)
        blockbench = next(
            item for item in TOOL_DEFINITIONS
            if item["function"]["name"] == "blockbench"
        )
        actions = blockbench["function"]["parameters"]["properties"]["action"]["enum"]
        self.assertIn("preview", actions)


# ----------------------------------------------------------------------
# Acceptance gate
# ----------------------------------------------------------------------
class GateTests(TempDataCase):
    def spec(self) -> ModSpec:
        return derive_spec("添加一个物品", "NeoForge", "1.21.1")

    def test_build_task_synthesises_acceptance_items(self) -> None:
        task = Task("T91-build", "构建", "build", acceptance=["build"])
        report = verify(self.spec(), task, self.tmp, build_ok=False)
        self.assertFalse(report.passed)
        self.assertEqual(report.blocking[0].id, "build")
        report = verify(self.spec(), task, self.tmp, build_ok=True)
        self.assertTrue(report.passed)

    def test_verify_runtime_is_warning_only(self) -> None:
        task = Task("T92-verify", "验证", "verify", acceptance=["runtime"])
        report = verify(self.spec(), task, self.tmp, runtime_ok=False)
        self.assertTrue(report.passed)
        check = report.checks[0]
        self.assertFalse(check.required)
        self.assertFalse(check.blocking)

    def test_acceptance_task_blocks_without_manual_confirmation(self) -> None:
        task = Task(
            "T99-acceptance", "验收", "acceptance",
            acceptance=["acceptance", "build", "lint-all", "audit"],
        )
        report = verify(
            self.spec(), task, self.tmp,
            build_ok=True, manual_ok=None, report=Report(),
        )
        self.assertFalse(report.passed)
        self.assertIn("acceptance", {item.id for item in report.blocking})
        report = verify(
            self.spec(), task, self.tmp,
            build_ok=True, manual_ok=True, report=Report(),
        )
        self.assertTrue(report.passed)


# ----------------------------------------------------------------------
# Kernel bridge
# ----------------------------------------------------------------------
class KernelBridgeTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Kernel Mod",
            mod_id="kernel_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.bridge = KernelBridge(
            self.project.root,
            idea="添加一个红宝石物品",
            loader="Forge",
            mc="1.20.1",
            modid="kernel_mod",
            package="com.example.kernel",
        )

    def test_spec_and_workflow_are_persisted(self) -> None:
        self.assertTrue((self.project.root / ".dma" / "spec.json").is_file())
        self.assertTrue((self.project.root / ".dma" / "workflow.json").is_file())
        self.assertIsNotNone(self.bridge.workflow.task("T91-build"))
        prompt = self.bridge.prompt_block()
        self.assertIn("当前应执行：", prompt)

    def test_record_edit_marks_feature_started(self) -> None:
        feature_id = self.bridge.record_edit(
            "src/main/java/com/example/kernel/item/RubyItem.java"
        )
        self.assertEqual(feature_id, "item")
        task = self.bridge.workflow.task("F-item")
        self.assertEqual(task.status, "running")

    def test_finalize_blocks_on_failed_build(self) -> None:
        text = self.bridge.finalize(build_ok=False, test_ok=False)
        self.assertIn("未通过", text)
        self.assertIn("不能标记为完成", text)
        self.assertEqual(
            self.bridge.workflow.task("T91-build").status, "blocked"
        )

    def test_snapshot_rollback_restores_content(self) -> None:
        target = self.project.root / "build.gradle"
        original = target.read_text(encoding="utf-8")
        snapshot = self.bridge.before_build("test")
        self.assertTrue(snapshot)
        target.write_text(original + "\n// changed\n", encoding="utf-8")
        result = self.bridge.rollback(snapshot)
        self.assertTrue(result["ok"], result)
        self.assertEqual(target.read_text(encoding="utf-8"), original)


# ----------------------------------------------------------------------
# Edit helpers
# ----------------------------------------------------------------------
class LintScenarioTests(TempDataCase):
    """lint 必须只看真实源码：快照目录、字符串字面量和注册表作用域。"""

    def _project(self) -> Path:
        root = self.tmp / "mod"
        (root / "src/main/java/com/x").mkdir(parents=True)
        (root / "src/main/resources").mkdir(parents=True)
        (root / "build.gradle").write_text(
            "plugins { id 'net.minecraftforge.gradle' version '[6.0,6.2)' }\n"
            "minecraft {\n"
            "    mappings channel: 'official', version: '1.20.1'\n"
            "}\n",
            encoding="utf-8",
        )
        return root

    def test_snapshot_java_is_not_linted(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        snap = root / ".dma/snapshots/s1/files/src/main/java/com/x"
        snap.mkdir(parents=True)
        (snap / "Bad.java").write_text(
            "package com.x;\npublic class Bad { void a(){ System.out.println(1); } }\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"antipattern"}, with_index=False)
        self.assertNotIn("Bad.java", report.markdown())

    def test_string_literals_survive_comment_mask(self) -> None:
        from dma.kernel.java import mask_comments

        text = '// "gone"\nString s = "keep"; /* "gone2" */\n'
        masked = mask_comments(text)
        self.assertEqual(len(masked), len(text))
        self.assertIn('"keep"', masked)
        self.assertNotIn('"gone"', masked)

    def test_duplicate_id_is_scoped_per_registry(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Blocks.java").write_text(
            'package com.x;\n'
            "class Blocks { static final RegistryObject<?> R = BLOCKS.register(\"foo\", null); }\n",
            encoding="utf-8",
        )
        (base / "Menus.java").write_text(
            'package com.x;\n'
            "class Menus { static final RegistryObject<?> R = MENUS.register(\"foo\", null); }\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"registry"}, with_index=False)
        self.assertNotIn("注册 ID `foo`", report.markdown())

        (base / "Items.java").write_text(
            'package com.x;\n'
            "class Items {\n"
            "    static final RegistryObject<?> A = ITEMS.register(\"bar\", null);\n"
            "    static final RegistryObject<?> B = ITEMS.register(\"bar\", null);\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"registry"}, with_index=False)
        self.assertIn("注册 ID `bar`", report.markdown())

    def test_noise_settings_missing_spawn_target_is_error(self) -> None:
        import json

        from dma.kernel.checks import run_checks

        root = self._project()
        target = root / "src/generated/resources/data/x/worldgen/noise_settings"
        target.mkdir(parents=True)
        payload = {key: {} for key in (
            "sea_level", "disable_mob_generation", "aquifers_enabled",
            "ore_veins_enabled", "legacy_random_source", "default_block",
            "default_fluid", "noise", "noise_router", "surface_rule",
        )}
        (target / "x.json").write_text(json.dumps(payload), encoding="utf-8")
        report = run_checks(root, only={"datapack"}, with_index=False)
        self.assertIn("spawn_target", report.markdown())

        payload["spawn_target"] = []
        (target / "x.json").write_text(json.dumps(payload), encoding="utf-8")
        report = run_checks(root, only={"datapack"}, with_index=False)
        self.assertNotIn("spawn_target", report.markdown())

    def test_helper_registered_item_without_model_is_flagged(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        (root / "src/main/resources/assets/twilight_grove").mkdir(parents=True)
        base = root / "src/main/java/com/x"
        (base / "ModItems.java").write_text(
            "package com.x;\n"
            "class ModItems {\n"
            "    static final DeferredRegister<Item> ITEMS ="
            " DeferredRegister.create(ForgeRegistries.ITEMS, \"twilight_grove\");\n"
            "    static final RegistryObject<Item> A = simple(\"dust\");\n"
            "    static final RegistryObject<Item> B = simple(\"shard\");\n"
            "    private static RegistryObject<Item> simple(String name) {\n"
            "        return ITEMS.register(name, () -> new Item(new Item.Properties()));\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        generated = root / "src/generated/resources/assets/twilight_grove/models/item"
        generated.mkdir(parents=True)
        (generated / "dust.json").write_text('{"parent": "item/generated"}', encoding="utf-8")
        report = run_checks(root, only={"assets"}, with_index=False)
        text = report.markdown()
        self.assertNotIn("物品 dust 缺少物品模型", text)
        self.assertIn("物品 shard 缺少物品模型", text)

    def test_unregistered_model_layer_is_error(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "BugRenderer.java").write_text(
            "package com.x;\n"
            "import net.minecraft.client.model.geom.ModelLayerLocation;\n"
            "class BugRenderer {\n"
            "    static final ModelLayerLocation LAYER ="
            " new ModelLayerLocation(TwilightGrove.id(\"bug\"), \"main\");\n"
            "    BugRenderer(Context context) {\n"
            "        super(context, new BugModel(context.bakeLayer(LAYER)));\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertIn("模型层 bug#main", report.markdown())

        (base / "ClientSetup.java").write_text(
            "package com.x;\n"
            "class ClientSetup {\n"
            "    void layers(EntityRenderersEvent.RegisterLayerDefinitions event) {\n"
            "        event.registerLayerDefinition(BugRenderer.LAYER, BugModel::createBodyLayer);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertNotIn("模型层 bug#main", report.markdown())

    def test_forge_mappings_channel_dsl_is_accepted(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        report = run_checks(root, only={"gradle"}, with_index=False)
        self.assertNotIn("缺少 mappings 配置", report.markdown())

    def test_nested_class_does_not_double_register(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Mods.java").write_text(
            'package com.x;\n'
            "class Mods {\n"
            "    static final RegistryObject<?> A = REG.register(\"spawns\", null);\n"
            "    static final class Inner {}\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"registry"}, with_index=False)
        self.assertNotIn("注册 ID `spawns`", report.markdown())

    def test_severity_filter_accepts_error_keyword(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Bad.java").write_text(
            "package com.x;\npublic class Bad { void a(){ System.out.println(1); } }\n",
            encoding="utf-8",
        )
        everything = run_checks(root, only={"antipattern"}, with_index=False)
        errors_only = run_checks(
            root, only={"antipattern"}, severities={"error"}, with_index=False
        )
        self.assertIn("System.out", everything.markdown())
        self.assertNotIn("System.out", errors_only.markdown())
        self.assertEqual(errors_only.counts()["total"], 0)

    def test_failure_branch_before_consume_is_not_pass_after_consume(self) -> None:
        """失败分支返回 FAIL、成功后消耗并返回 SUCCESS 是正确写法。"""
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Activator.java").write_text(
            'package com.x;\n'
            "class Activator {\n"
            "    public InteractionResult useOn(Context ctx) {\n"
            "        if (bad(ctx)) { return InteractionResult.FAIL; }\n"
            "        ctx.getItemInHand().shrink(1);\n"
            "        return InteractionResult.sidedSuccess(false);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"domain"}, with_index=False)
        self.assertNotIn("P1-27", report.markdown())

    def test_consume_then_pass_is_still_flagged(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Leaky.java").write_text(
            'package com.x;\n'
            "class Leaky {\n"
            "    public InteractionResult useOn(Context ctx) {\n"
            "        ctx.getItemInHand().shrink(1);\n"
            "        return InteractionResult.PASS;\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"domain"}, with_index=False)
        self.assertIn("P1-27", report.markdown())

    def test_same_name_overload_is_not_recursion(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Menu.java").write_text(
            'package com.x;\n'
            "class Menu {\n"
            "    public boolean stillValid(Player p) {\n"
            "        return stillValid(access, p, block);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"stress"}, with_index=False)
        self.assertNotIn("递归调用自己", report.markdown())

    def test_true_recursion_is_flagged(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Loop.java").write_text(
            'package com.x;\n'
            "class Loop {\n"
            "    int f(int n) { return f(n - 1); }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"stress"}, with_index=False)
        self.assertIn("递归调用自己", report.markdown())

    def test_datagen_resource_merge_is_not_duplicate(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        (root / "build.gradle").write_text(
            "plugins { id 'net.minecraftforge.gradle' version '[6.0,6.2)' }\n"
            "minecraft { mappings channel: 'official', version: '1.20.1' }\n"
            "sourceSets.main.resources { srcDir 'src/generated/resources' }\n"
            "processResources { duplicatesStrategy = DuplicatesStrategy.INCLUDE }\n",
            encoding="utf-8",
        )
        main = root / "src/main/resources/assets/x"
        gen = root / "src/generated/resources/assets/x"
        main.mkdir(parents=True)
        gen.mkdir(parents=True)
        (main / "a.json").write_text('{"a": 1}', encoding="utf-8")
        (gen / "a.json").write_text('{"a": 2}', encoding="utf-8")
        report = run_checks(root, only={"packaging"}, with_index=False)
        self.assertNotIn("资源重复", report.markdown())

    def test_javadoc_version_string_is_not_branch(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Doc.java").write_text(
            'package com.x;\n'
            "/** 适配 1.20.1 的渲染路径。 */\n"
            "public class Doc { public void draw() { }\n }\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"version"}, with_index=False)
        self.assertNotIn("P3-28", report.markdown())

    def test_nbt_scalar_default_is_not_contains_issue(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Store.java").write_text(
            'package com.x;\n'
            "class Store {\n"
            "    int value;\n"
            "    public void load(CompoundTag tag) {\n"
            "        value = tag.getInt(\"Value\");\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"serialization"}, with_index=False)
        self.assertNotIn("未校验 contains", report.markdown())

    def test_event_cancel_is_not_flagged(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "Events.java").write_text(
            'package com.x;\n'
            "class Events {\n"
            "    @SubscribeEvent\n"
            "    public void onRightClick(PlayerInteractEvent.RightClickBlock event) {\n"
            "        event.setCanceled(true);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"domain"}, with_index=False)
        self.assertNotIn("取消事件前没有检查可取消性", report.markdown())

    def test_humanoid_default_outer_layers_are_error(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "StalkerModel.java").write_text(
            'package com.x;\n'
            "class StalkerModel extends HumanoidModel<Stalker> {\n"
            "    static LayerDefinition createBodyLayer() {\n"
            "        MeshDefinition mesh = HumanoidModel.createMesh(CubeDeformation.NONE, 0.0F);\n"
            "        PartDefinition root = mesh.getRoot();\n"
            "        root.addOrReplaceChild(\"head\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"body\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_arm\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_arm\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_leg\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_leg\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        return LayerDefinition.create(mesh, 64, 64);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        text = report.markdown()
        self.assertIn("P1-12", text)
        self.assertIn("jacket", text)
        self.assertIn("彩色乱码", text)

    def test_humanoid_cleared_outer_layers_pass(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "StalkerModel.java").write_text(
            'package com.x;\n'
            "class StalkerModel extends HumanoidModel<Stalker> {\n"
            "    static LayerDefinition createBodyLayer() {\n"
            "        MeshDefinition mesh = HumanoidModel.createMesh(CubeDeformation.NONE, 0.0F);\n"
            "        PartDefinition root = mesh.getRoot();\n"
            "        root.addOrReplaceChild(\"head\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"body\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_arm\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_arm\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_leg\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_leg\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"hat\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"jacket\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_sleeve\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_sleeve\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"left_pants\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        root.addOrReplaceChild(\"right_pants\", CubeListBuilder.create(), PartPose.ZERO);\n"
            "        return LayerDefinition.create(mesh, 64, 64);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertNotIn("P1-12", report.markdown())

    def test_duplicate_particle_registration_is_error(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "ClientA.java").write_text(
            "package com.x;\n"
            "class ClientA {\n"
            "    void on(RegisterParticleProvidersEvent event) {\n"
            "        event.registerSpriteSet(ModParticles.SPORE.get(), Provider::new);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        (base / "ClientB.java").write_text(
            "package com.x;\n"
            "class ClientB {\n"
            "    void on(RegisterParticleProvidersEvent event) {\n"
            "        event.registerSpriteSet(ModParticles.SPORE.get(), Provider::new);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        text = report.markdown()
        self.assertIn("P2-15", text)
        self.assertIn("被重复注册", text)
        self.assertIn("NPE", text)

    def test_single_particle_registration_passes(self) -> None:
        from dma.kernel.checks import run_checks

        root = self._project()
        base = root / "src/main/java/com/x"
        (base / "ClientA.java").write_text(
            "package com.x;\n"
            "class ClientA {\n"
            "    void on(RegisterParticleProvidersEvent event) {\n"
            "        event.registerSpriteSet(ModParticles.SPORE.get(), Provider::new);\n"
            "        event.registerSpriteSet(ModParticles.GLOW.get(), Provider::new);\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertNotIn("P2-15", report.markdown())

    def test_flat_entity_texture_is_error(self) -> None:
        from PIL import Image

        from dma.kernel.checks import run_checks

        root = self._project()
        target = root / "src/main/resources/assets/testmod/textures/entity/flat.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (64, 64), (8, 8, 10, 255)).save(target)
        report = run_checks(root, only={"render"}, with_index=False)
        text = report.markdown()
        self.assertIn("接近纯黑", text)

    def test_detailed_entity_texture_passes(self) -> None:
        from dma.kernel.checks import run_checks
        from dma.kernel.tools import texture

        root = self._project()
        target = root / "src/main/resources/assets/testmod/textures/entity/skin.png"
        texture.generate_entity(target, base="#5a4a7a", accent="#66e0d0", seed=2)
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertNotIn("P2-15", report.markdown().split("P2-15", 1)[0])
        self.assertNotIn("接近纯黑", report.markdown())

    def test_flat_item_texture_is_error(self) -> None:
        from PIL import Image

        from dma.kernel.checks import run_checks

        root = self._project()
        target = root / "src/main/resources/assets/testmod/textures/item/blob.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (16, 16), (120, 80, 200, 255)).save(target)
        report = run_checks(root, only={"render"}, with_index=False)
        self.assertIn("纯色方块", report.markdown())


class EditHelperTests(TempDataCase):
    SOURCE = """package a;

public class A {
    public int value() {
        return 1;
    }
}
"""

    def test_replace_method_body(self) -> None:
        updated, result = replace_body_text(
            self.SOURCE, class_name="A", method="value",
            body="return 42;",
        )
        self.assertTrue(result.ok)
        self.assertIn("return 42;", updated)
        self.assertNotIn("return 1;", updated)

    def test_insert_member(self) -> None:
        updated, result = insert_member_text(
            self.SOURCE, class_name="A",
            code="public static final String NAME = \"a\";",
            before="public int value()",
        )
        self.assertTrue(result.ok)
        self.assertIn('public static final String NAME = "a";', updated)
        self.assertLess(updated.index("NAME"), updated.index("public int value()"))

    def test_file_cache_chunks_and_hash(self) -> None:
        path = self.tmp / "Chunk.java"
        path.write_text("\n".join(f"line {i}" for i in range(450)), encoding="utf-8")
        cache = FileCache(chunk_lines=200)
        digest = cache.hash(path)
        self.assertEqual(len(digest), 64)
        first = cache.next_chunk(path)
        second = cache.next_chunk(path)
        third = cache.next_chunk(path)
        self.assertEqual((first["start"], first["end"]), (1, 200))
        self.assertEqual((second["start"], second["end"]), (201, 400))
        self.assertTrue(third["done"])
        self.assertTrue(cache.unchanged(path, digest))


# ----------------------------------------------------------------------
# mcdev prepare honesty
# ----------------------------------------------------------------------
class PrepareHonestyTests(TempDataCase):
    def test_wait_join_false_is_not_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout, **kwargs: {"kind": kind, "pid": 1}
        result = manager.prepare(root, "client", wait_join=False)
        self.assertFalse(result["ok"])
        self.assertFalse(result["results"][0]["joined"])

    def test_not_joined_is_not_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout, **kwargs: {"kind": kind, "pid": 1}
        manager._sleep = lambda seconds: None
        manager._join_client = lambda root, client, timeout, port, **kwargs: {"kind": "client", "joined": False}
        result = manager.prepare(root, "client", wait_join=True)
        self.assertFalse(result["ok"])

    def test_joined_is_ready(self) -> None:
        root = self.tmp / "project"
        root.mkdir()
        manager = LaunchManager()
        manager.launch = lambda kind, path, timeout, **kwargs: {"kind": kind, "pid": 1}
        manager._sleep = lambda seconds: None
        manager._join_client = lambda root, client, timeout, port, **kwargs: {"kind": "client", "joined": True}
        result = manager.prepare(root, "client", wait_join=True)
        self.assertTrue(result["ok"])


# ----------------------------------------------------------------------
# 2.0 tools
# ----------------------------------------------------------------------
class AssetToolTests(TempDataCase):
    def test_asset_library_bootstrap_and_clone(self) -> None:
        library_root = self.tmp / "asset_project"
        library_root.mkdir()
        assets = library.AssetLibrary(library_root)
        written = assets.bootstrap()
        self.assertEqual(len(written), 5)
        self.assertEqual(len(assets.list()), 5)
        dest = self.tmp / "out" / "ruby_block"
        result = assets.clone("cube", dest, texture="kernel_mod:block/ruby", scale=0.5)
        self.assertTrue(Path(result["dest"]).is_file())
        self.assertEqual(Path(result["dest"]).suffix, ".bbmodel")
        self.assertEqual(result["cubes"], 1)

    def test_blockbench_preview_writes_png(self) -> None:
        from dma.kernel.tools import blockbench

        model = blockbench.full_block("preview")
        target = self.tmp / "preview.png"
        try:
            blockbench.preview(model, target, size=(64, 64))
        except RuntimeError as exc:
            self.skipTest(str(exc))
        data = target.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_blockbench_uses_minecraft_box_uv(self) -> None:
        from dma.kernel.tools import blockbench

        # 4x12x4 limb at atlas origin (40,16), the vanilla arm layout.
        limb = blockbench.cube("arm", (0, 0, 0), (4, 12, 4), uv=(40, 16))
        rects = limb.face_uvs((64, 64))
        self.assertEqual(rects["up"], (44, 16, 48, 20))
        self.assertEqual(rects["down"], (48, 16, 52, 20))
        self.assertEqual(rects["east"], (40, 20, 44, 32))
        self.assertEqual(rects["north"], (44, 20, 48, 32))
        self.assertEqual(rects["west"], (48, 20, 52, 32))
        self.assertEqual(rects["south"], (52, 20, 56, 32))
        # Without a UV origin every face still maps the whole texture.
        block = blockbench.cube("block", (0, 0, 0), (16, 16, 16))
        self.assertEqual(block.face_uvs((16, 16))["north"], (0, 0, 16, 16))
        # Vanilla cross plants reuse the whole texture on both planes.
        plant = blockbench.cross_model("plant")
        self.assertIsNone(plant.cubes[0].uv)
        self.assertEqual(plant.cubes[0].face_uvs((16, 16))["north"], (0, 0, 16, 16))

    def test_blockbench_custom_cubes_round_trip_and_export(self) -> None:
        from dma.kernel.tools import blockbench

        model = blockbench.model_from_spec("chair", [
            {"name": "seat", "from": [0, 8, 0], "to": [16, 12, 16], "uv": [0, 0]},
            {"name": "leg", "from": [0, 0, 0], "size": [4, 8, 4], "uv": [32, 0]},
        ], texture_size=(64, 64))
        self.assertEqual(blockbench.validate(model), [])
        path = self.tmp / "chair.bbmodel"
        blockbench.save_bbmodel(model, path)
        loaded = blockbench.load_bbmodel(path)
        self.assertEqual(len(loaded.cubes), 2)
        self.assertEqual(loaded.cubes[1].uv, (32, 0))
        written = blockbench.export(
            loaded, self.tmp, namespace="demo", name="chair", kind="block",
        )
        self.assertTrue(written[0].is_file())
        java = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertEqual(java["texture_size"], [64, 64])
        self.assertEqual(java["textures"]["0"], "demo:block/chair")
        self.assertNotIn("uv", java["elements"][0])
        self.assertEqual(
            java["elements"][0]["faces"]["north"]["uv"], [16, 16, 32, 20]
        )

    def test_blockbench_preview_samples_texture(self) -> None:
        from dma.kernel.tools import blockbench

        try:
            from PIL import Image
        except Exception as exc:  # noqa: BLE001 - optional dependency
            self.skipTest(str(exc))
        atlas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        for y in range(64):
            for x in range(64):
                atlas.putpixel(
                    (x, y),
                    (230, 60, 60, 255) if (x // 8 + y // 8) % 2 == 0
                    else (60, 200, 90, 255),
                )
        texture = self.tmp / "atlas.png"
        atlas.save(texture)
        model = blockbench.model_from_spec("bench", [
            {"name": "top", "from": [0, 8, 0], "to": [16, 12, 16], "uv": [0, 0]},
        ], texture_name=blockbench.texture_data_uri(texture), texture_size=(64, 64))
        target = self.tmp / "bench_preview.png"
        try:
            blockbench.preview(model, target, size=(120, 120))
        except RuntimeError as exc:
            self.skipTest(str(exc))
        image = Image.open(target).convert("RGBA")
        colors = {
            image.getpixel((x, y))
            for y in range(image.height)
            for x in range(image.width)
        }
        self.assertTrue(any(c[0] > 150 and c[1] < 120 for c in colors))
        self.assertTrue(any(c[1] > 100 and c[0] < 150 for c in colors))
        self.assertGreaterEqual(len(colors), 6)

    def test_blockbench_export_converts_euler_to_java_rotation(self) -> None:
        from dma.kernel.tools import blockbench

        model = blockbench.model_from_spec("slope", [
            {"name": "ramp", "from": [0, 0, 0], "to": [16, 4, 16], "uv": [0, 0],
             "origin": [8, 4, 8], "rotation": [0, 22.5, 0]},
        ], texture_size=(64, 64))
        self.assertEqual(blockbench.validate(model), [])
        java = blockbench.export_java_model(model, namespace="demo")
        element = java["elements"][0]
        self.assertEqual(
            element["rotation"],
            {"origin": [8, 4, 8], "axis": "y", "angle": 22.5, "rescale": False},
        )
        self.assertNotIn("origin", element)
        # Multiple axes are not representable in a Java model.
        bad = blockbench.model_from_spec("bad", [
            {"name": "cube", "from": [0, 0, 0], "to": [8, 8, 8],
             "rotation": [10, 20, 0]},
        ])
        self.assertTrue(any("单轴" in issue for issue in blockbench.validate(bad)))
        over = blockbench.model_from_spec("over", [
            {"name": "cube", "from": [0, 0, 0], "to": [8, 8, 8],
             "rotation": [0, 60, 0]},
        ])
        self.assertTrue(any("-45..45" in issue for issue in blockbench.validate(over)))

    def test_configgen_writes_screen_lang_and_schema(self) -> None:
        fields = configgen.parse_schema(
            {
                "fields": [
                    {"key": "enabled", "type": "bool", "default": True, "comment": "开关"},
                    {"key": "speed", "type": "float", "min": 0, "max": 2, "default": 1.0},
                    {"key": "mode", "type": "enum", "values": ["fast", "slow"]},
                ]
            }
        )
        result = configgen.write(
            self.tmp, package="com.example.kernel", modid="kernel_mod",
            fields=fields, mc="1.21.1",
        )
        self.assertEqual(result["fields"], 3)
        self.assertEqual(len(result["files"]), 4)
        java = self.tmp / "src/main/java/com/example/kernel/client/ModConfigScreen.java"
        source = java.read_text(encoding="utf-8")
        self.assertIn("this.renderBackground(graphics, mouseX, mouseY, partialTick);", source)
        self.assertIn('String key = "config.kernel_mod." + entry.getKey();', source)
        self.assertIn("Component.translatable(key)", source)
        lang = json.loads(
            (self.tmp / "src/main/resources/assets/kernel_mod/lang/zh_cn.json").read_text(encoding="utf-8")
        )
        self.assertIn("config.kernel_mod.title", lang)

    def test_diagnostics_scaffold_is_off_by_default(self) -> None:
        result = diagnostics.write(
            self.tmp, package="com.example.kernel", modid="kernel_mod",
            version="1.2.3", kinds=["dump", "crash", "probe"],
        )
        self.assertIn("flags", result["kinds"])
        self.assertFalse(result["enabledByDefault"])
        base = self.tmp / "src/main/java/com/example/kernel/debug"
        flags = (base / "DebugFlags.java").read_text(encoding="utf-8")
        self.assertIn("BUILD_ENABLED = false", flags)
        self.assertIn('"kernel_mod.debug"', flags)
        dump = (base / "DebugDumpCommand.java").read_text(encoding="utf-8")
        self.assertIn(".requires(source -> DebugFlags.enabled())", dump)
        crash = (base / "CrashContext.java").read_text(encoding="utf-8")
        self.assertIn('MOD_VERSION = "1.2.3"', crash)
        probe = (base / "PerfProbe.java").read_text(encoding="utf-8")
        self.assertIn("PATHFINDING", probe)
        self.assertIn("jdk.jfr.Event", probe)
        self.assertTrue((self.tmp / "docs/debug.md").is_file())

    def test_matrix_plan_and_workflow(self) -> None:
        plan = matrix.plan()
        self.assertEqual(len(plan.cells), 18)
        self.assertGreaterEqual(len(plan.supported()), 1)
        text = matrix.workflow(plan)
        self.assertIn("actions/setup-java@v4", text)
        self.assertIn("${{ matrix.os }}", text)
        self.assertNotIn("os: 'ubuntu-latest'\n            os:", text)

    def test_migration_backup_before_write(self) -> None:
        root = self.tmp / "migration"
        source = root / "src/main/java/a/A.java"
        source.parent.mkdir(parents=True)
        source.write_text('String id = "old_mod:ruby";', encoding="utf-8")
        save = root / "run/saves/world/level.dat"
        save.parent.mkdir(parents=True)
        save.write_bytes(b"save-data")

        change = migration.Migration(
            modid="old_mod",
            from_version="1.0.0",
            to_version="1.1.0",
            ids={"old_mod:ruby": "new_mod:ruby"},
        )
        rows = migration.apply(root, change, dry_run=False)
        self.assertTrue(rows)
        self.assertIn("new_mod:ruby", source.read_text(encoding="utf-8"))
        backup = Path(rows[0]["backup"])
        self.assertTrue((backup / "manifest.json").is_file())
        self.assertTrue((backup / "run/saves/world/level.dat").is_file())


# ----------------------------------------------------------------------
# Agent integration
# ----------------------------------------------------------------------
class AgentIntegrationTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.project = create_project(
            self.tmp / "projects",
            name="Agent Mod",
            mod_id="agent_mod",
            mc_version="1.20.1",
            loader="Forge",
            task_type="创建全新模组",
        )
        self.memory = MemoryStore()
        config = Config()
        config.ai.provider = "pollinations"
        config.auto_reference = False  # 单测不访问网络/不下载参考模组
        config.max_steps = 4
        self.worker = AgentWorker(
            user_text="添加一个物品",
            history=[],
            project=self.project,
            config=config,
            skill_store=SkillStore(),
            memory=self.memory,
            connector_store=ConnectorStore(),
        )

    def tearDown(self) -> None:
        self.memory.close()
        super().tearDown()

    def test_native_tool_call_is_parsed(self) -> None:
        self.worker.config.native_tools = True
        payload = {
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "read_file", "arguments": {"path": "build.gradle"}}
            ],
            "message": {},
        }
        with mock.patch("dma.agent.ai.request_chat_tools", return_value=payload):
            text, calls, native = self.worker._chat([])
        self.assertEqual(text, "")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].name, "read_file")
        self.assertEqual(calls[0].call_id, "call_1")
        self.assertIsNotNone(native)

    def test_unsupported_native_falls_back_to_text_protocol(self) -> None:
        self.worker.config.native_tools = True
        reply = '```tool\n{"tool":"list_files","path":"."}\n```'
        with mock.patch(
            "dma.agent.ai.request_chat_tools",
            side_effect=ai.AIToolsUnsupported("no tools"),
        ), mock.patch("dma.agent.ai.request_chat", return_value=reply):
            text, calls, native = self.worker._chat([])
        self.assertIsNone(native)
        self.assertEqual(text, reply)
        self.assertEqual(calls[0].name, "list_files")
        self.assertTrue(self.worker._native_disabled)

    def test_read_file_skips_unchanged_hash(self) -> None:
        run = AgentRun()
        first = self.worker._execute(
            ToolCall(name="read_file", args={"path": "build.gradle"}), run
        )
        match = re.search(r"hash=([0-9a-f]{64})", first)
        self.assertIsNotNone(match)
        second = self.worker._execute(
            ToolCall(
                name="read_file",
                args={"path": "build.gradle", "since_hash": match.group(1)},
            ),
            run,
        )
        self.assertIn("文件未变化", second)

    def test_edit_file_uses_symbol_body_replacement(self) -> None:
        java = self.project.root / "src/main/java/Test.java"
        java.parent.mkdir(parents=True, exist_ok=True)
        java.write_text(
            "package a;\n\npublic class Test {\n"
            "    public int value() {\n        return 1;\n    }\n}\n",
            encoding="utf-8",
        )
        run = AgentRun()
        text = self.worker._execute(
            ToolCall(
                name="edit_file",
                args={"path": "src/main/java/Test.java", "symbol": "Test.value", "new": "return 2;"},
            ),
            run,
        )
        self.assertIn("替换方法体", text)
        self.assertEqual(len(run.pending), 1)
        self.assertIn("return 2;", run.pending[0].content)

    def test_kernel_tools_are_routed(self) -> None:
        for name in ("asset_library", "config_gen", "debug_scaffold", "matrix_plan"):
            self.assertIn(name, __import__("dma.agent", fromlist=["KERNEL_TOOLS"]).KERNEL_TOOLS)

    def test_matrix_plan_tool_returns_cells(self) -> None:
        text = self.worker._execute(
            ToolCall(name="matrix_plan", args={"action": "plan"}), AgentRun()
        )
        data = json.loads(text)
        self.assertEqual(len(data["cells"]), 18)
        self.assertEqual(data["total"], 18)
        self.assertGreaterEqual(data["supported"], 1)


if __name__ == "__main__":
    unittest.main()
