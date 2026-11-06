from __future__ import annotations

import json
import unittest
import zipfile

from dma import porting

from .base import TempDataCase


def _png() -> bytes:
    # 1x1 透明 PNG，足够让资源校验认为纹理存在。
    return bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c63000100000500010d0a2db40000000049454e44ae426082"
    )


class PortingTest(TempDataCase):
    def _jar(self, mca_items: bool = False, mca_marker: bool = False) -> "object":
        path = self.tmp / "DemoMod-1.0.0.jar"
        lang_zh = {"item.demomod.widget": "小工具"}
        lang_en = {"item.demomod.widget": "Widget"}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                "fabric.mod.json",
                json.dumps(
                    {
                        "id": "demomod",
                        "name": "Demo Mod",
                        "version": "1.2.3",
                        "depends": {"minecraft": "1.21.11"},
                        "entrypoints": {"main": ["com.demo.DemoMod"]},
                    }
                ),
            )
            def write_item(item_id: str, name_zh: str, name_en: str) -> None:
                zf.writestr(
                    f"assets/demomod/items/{item_id}.json",
                    json.dumps(
                        {"model": {"type": "minecraft:model", "model": f"demomod:item/{item_id}"}}
                    ),
                )
                zf.writestr(
                    f"assets/demomod/models/item/{item_id}.json",
                    json.dumps(
                        {
                            "parent": "minecraft:item/generated",
                            "textures": {"layer0": f"demomod:item/{item_id}"},
                        }
                    ),
                )
                zf.writestr(f"assets/demomod/textures/item/{item_id}.png", _png())
                zf.writestr(
                    f"data/demomod/recipe/{item_id}.json",
                    json.dumps({"type": "minecraft:crafting_shapeless"}),
                )
                lang_zh[f"item.demomod.{item_id}"] = name_zh
                lang_en[f"item.demomod.{item_id}"] = name_en

            write_item("widget", "小工具", "Widget")
            if mca_items:
                write_item("generated_item", "皮化之刃", "Skin Blade")
                write_item("hypnosis_phone", "催眠手机", "Hypnosis Phone")
                write_item("player_skin", "皮物", "Skin")
            zf.writestr(
                "assets/demomod/lang/zh_cn.json",
                json.dumps(lang_zh, ensure_ascii=False).encode("gb18030"),
            )
            zf.writestr(
                "assets/demomod/lang/en_us.json",
                json.dumps(lang_en),
            )
            zf.writestr("com/demo/DemoMod.class", b"not-a-real-class")
            if mca_marker:
                # 模拟作者没有在 fabric.mod.json 声明 MCA，但类里确实引用它。
                zf.writestr("com/demo/McaBridge.class", b"net/conczin/mca/entity/VillagerLike")
        return path

    def test_scan_jar_tracks_root_and_meta_files(self) -> None:
        jar = self._jar()
        scan = porting.scan_jar(jar)
        self.assertIn("fabric.mod.json", scan["root_files"])
        self.assertEqual(scan["mod_id"], "demomod")
        self.assertIn("widget", scan["item_defs_ids"])
        self.assertEqual(scan["lang"]["zh_cn"]["item.demomod.widget"], "小工具")

    def test_create_port_project_converts_resources_and_keeps_utf8(self) -> None:
        jar = self._jar()
        result = porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = result["root"]
        self.assertEqual(result["mod_id"], "demomod")
        model = "src/main/resources/assets/demomod/models/item/widget.json"
        self.assertTrue((self.tmp / "demomod" / model).is_file())
        self.assertTrue(
            (self.tmp / "demomod" / "src/main/resources/assets/demomod/textures/item/widget.png").is_file()
        )
        text = (self.tmp / "demomod" / "src/main/resources/assets/demomod/lang/zh_cn.json").read_text(
            encoding="utf-8"
        )
        self.assertIn("小工具", text)
        self.assertNotIn("\ufffd", text)
        self.assertTrue((self.tmp / "demomod" / "reference/source-jars/DemoMod-1.0.0.jar").is_file())
        self.assertTrue((self.tmp / "demomod" / "PORT_PLAN.md").is_file())
        self.assertTrue((self.tmp / "demomod" / "build.gradle").is_file())
        self.assertIn("21.1.265", (self.tmp / "demomod" / "gradle.properties").read_text(encoding="utf-8"))
        self.assertTrue(root)

    def test_create_port_project_generates_item_registration(self) -> None:
        jar = self._jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = self.tmp / "demomod"
        items = (root / "src/main/java/net/demomod/ModItems.java").read_text(encoding="utf-8")
        self.assertIn('ITEMS.registerItem("widget"', items)
        tabs = (root / "src/main/java/net/demomod/ModCreativeTabs.java").read_text(encoding="utf-8")
        self.assertIn("ModItems.WIDGET.get()", tabs)
        main = (root / "src/main/java/net/demomod/DemomodMod.java").read_text(encoding="utf-8")
        self.assertIn("ModItems.register(modEventBus)", main)
        lang = (root / "src/main/resources/assets/demomod/lang/zh_cn.json").read_text(encoding="utf-8")
        self.assertIn("itemGroup.demomod", lang)
        gradle = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("2.0.115", gradle)
        self.assertNotIn("MODDEV_VERSION_TOKEN", gradle)

    def test_verify_no_longer_flags_auto_registered_items(self) -> None:
        jar = self._jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        report = porting.verify_project(self.tmp / "demomod", write=True)
        codes = {item.code for item in report.issues}
        self.assertNotIn("source_item_not_registered", codes)
        self.assertNotIn("item_model_missing", codes)
        self.assertTrue((self.tmp / "demomod" / "VERIFY.md").is_file())

    def _mca_jar(self):
        path = self.tmp / "mca-neoforge-test.jar"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("META-INF/neoforge.mods.toml", 'modLoader="javafml"\n[[mods]]\nmodId="mca"\n')
        return path

    def test_mca_project_generates_compat_and_flags_missing_jar(self) -> None:
        jar = self._jar(mca_items=True)
        mca = self._mca_jar()
        result = porting.create_port_project(
            jar, self.tmp, target_mc="1.21.1", dependencies="mca"
        )
        root = self.tmp / "demomod"
        self.assertEqual(result["mca_jar"], mca.name)
        self.assertTrue((root / "libs" / mca.name).is_file())
        java = root / "src/main/java/net/demomod"
        compat = (java / "compat/McaCompat.java").read_text(encoding="utf-8")
        self.assertIn("captureVillager", compat)
        self.assertIn("respawnVillager", compat)
        self.assertIn("setEntityDataSet", compat)
        self.assertIn("PlayerDataMessage", compat)
        # 道具、皮肤缓存、事件和客户端 mixin 必须整建制生成。
        blade = (java / "item/SkinBladeItem.java").read_text(encoding="utf-8")
        self.assertIn("McaCompat.captureVillager(target)", blade)
        self.assertTrue((java / "data/SkinData.java").is_file())
        self.assertTrue((java / "client/ClientSkinCache.java").is_file())
        self.assertTrue((java / "event/ServerEvents.java").is_file())
        self.assertTrue((java / "mixin/client/AbstractClientPlayerMixin.java").is_file())
        mixins = json.loads((root / "src/main/resources/demomod.mixins.json").read_text(encoding="utf-8"))
        self.assertEqual(mixins["client"], ["client.AbstractClientPlayerMixin"])
        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertNotIn("mca_jar_missing", codes)
        self.assertNotIn("mca_compat_unused", codes)
        self.assertNotIn("mca_item_interact", codes)

    def test_source_code_reference_auto_enables_mca(self) -> None:
        jar = self._jar(mca_items=True, mca_marker=True)
        self._mca_jar()
        result = porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        self.assertTrue(result["mca_jar"])
        json_text = (
            self.tmp / "demomod" / "src/main/resources/META-INF/neoforge.mods.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('modId="mca"', json_text)
        self.assertTrue(
            (self.tmp / "demomod/src/main/java/net/demomod/compat/McaCompat.java").is_file()
        )

    def test_possession_items_auto_enable_mca_without_bytecode_marker(self) -> None:
        # PoHypSkin 这类生成器模组，类常量池里没有 net/conczin/mca，
        # 只能靠“皮物 + 催眠手机/皮化之刃”的道具组合识别。
        jar = self._jar(mca_items=True)
        self._mca_jar()
        result = porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        self.assertTrue(result["mca_jar"])
        self.assertTrue(
            (self.tmp / "demomod/src/main/java/net/demomod/compat/McaCompat.java").is_file()
        )
        report = porting.verify_project(self.tmp / "demomod")
        self.assertTrue(report.ok, [item.code for item in report.issues])

    def test_mca_verify_requires_entity_interact_hook(self) -> None:
        jar = self._jar(mca_items=True)
        self._mca_jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = self.tmp / "demomod"
        events = root / "src/main/java/net/demomod/event/ServerEvents.java"
        text = events.read_text(encoding="utf-8")
        text = text.replace("PlayerInteractEvent.EntityInteract", "Object")
        text = text.replace("event.setCancellationResult(result);", "")
        text = text.replace("event.setCanceled(true);", "")
        events.write_text(text, encoding="utf-8")
        report = porting.verify_project(root)
        codes = {item.code for item in report.issues}
        self.assertIn("mca_entity_interact_event", codes)

    def test_plain_item_mod_does_not_enable_mca(self) -> None:
        jar = self._jar()
        self._mca_jar()
        result = porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        self.assertEqual(result["mca_jar"], "")

    def test_verify_flags_blank_item_registration(self) -> None:
        jar = self._jar(mca_items=True)
        self._mca_jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1", dependencies="mca")
        root = self.tmp / "demomod"
        java = root / "src/main/java/net/demomod/ModItems.java"
        text = java.read_text(encoding="utf-8")
        text = text.replace(
            "    private ModItems() {",
            '    public static final DeferredItem<Item> PHONE = '
            'ITEMS.registerItem("hypnosis_phone", Item::new, new Item.Properties());\n\n'
            "    private ModItems() {",
        )
        java.write_text(text, encoding="utf-8")
        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertIn("mca_item_blank_behavior", codes)

    def test_mca_verify_flags_missing_neoforge_jar(self) -> None:
        jar = self._jar(mca_items=True)
        self._mca_jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1", dependencies="mca")
        root = self.tmp / "demomod"
        for item in (root / "libs").glob("*.jar"):
            item.unlink()
        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertIn("mca_jar_missing", codes)

    def test_verify_accepts_complete_registration(self) -> None:
        jar = self._jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = self.tmp / "demomod"
        java = root / "src/main/java/net/demomod/ModItems.java"
        java.parent.mkdir(parents=True, exist_ok=True)
        java.write_text(
            "package com.demo;\n"
            "import net.neoforged.neoforge.registries.DeferredRegister;\n"
            "public final class ModItems {\n"
            "  public static final DeferredRegister.Items ITEMS = DeferredRegister.createItems(\"demomod\");\n"
            "  public static final var WIDGET = ITEMS.register(\"widget\", () -> new net.minecraft.world.item.Item(new net.minecraft.world.item.Item.Properties()));\n"
            "}\n",
            encoding="utf-8",
        )
        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertNotIn("source_item_not_registered", codes)
        self.assertNotIn("item_model_missing", codes)
        self.assertNotIn("lang_key_missing", codes)

    def test_port_status_tracks_pending_and_exclusions(self) -> None:
        jar = self._jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = self.tmp / "demomod"
        source = root / "reference/decompiled/DemoMod/Hypnosis.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("package demo;\npublic class Hypnosis {}\n", encoding="utf-8")

        status = porting.build_port_status(root)
        self.assertEqual(status["source_count"], 1)
        self.assertEqual(status["pending"], 1)
        self.assertEqual(status["migrated"], 0)

        (root / "PORT_EXCLUDE.md").write_text(
            "- DemoMod/Hypnosis.java：旧版本调试代码\n", encoding="utf-8"
        )
        status = porting.build_port_status(root)
        self.assertEqual(status["pending"], 0)
        self.assertEqual(status["excluded"], 1)

    def test_verify_flags_unmigrated_source_classes(self) -> None:
        jar = self._jar()
        porting.create_port_project(jar, self.tmp, target_mc="1.21.1")
        root = self.tmp / "demomod"
        source = root / "reference/decompiled/DemoMod/Hypnosis.java"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("package demo;\npublic class Hypnosis {}\n", encoding="utf-8")

        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertIn("source_classes_unmigrated", codes)
        self.assertTrue((root / "PORT_STATUS.json").is_file())
        self.assertTrue((root / "PORT_STATUS.md").is_file())
        status = porting.build_port_status(root)
        self.assertEqual(status["pending"], 1)

        target = root / "src/main/java/net/demomod/Hypnosis.java"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("package net.demomod;\npublic class Hypnosis {}\n", encoding="utf-8")
        report = porting.verify_project(root, write=True)
        codes = {item.code for item in report.issues}
        self.assertNotIn("source_classes_unmigrated", codes)
        self.assertEqual(porting.build_port_status(root)["migrated"], 1)

    def test_mca_project_uses_working_item_classes(self) -> None:
        # 白板 Item 会导致“道具拿在手里无法使用”，有 MCA 时必须换成附身道具。
        text = porting._items_class(
            "net.demo",
            "demomod",
            "DemoMod",
            ["generated_item", "hypnosis_phone", "player_skin", "message_board"],
            True,
        )
        self.assertIn("SkinBladeItem::new", text)
        self.assertIn("HypnosisPhoneItem::new", text)
        self.assertIn("PlayerSkinItem::new", text)
        self.assertIn("import net.demo.item.SkinBladeItem;", text)
        self.assertIn('"message_board", Item::new', text)

        base = porting._possession_item_class("net.demo", "PossessionItem", "附身道具基类。")
        self.assertIn("McaCompat.possess(serverPlayer, target)", base)
        self.assertIn("McaCompat.release(serverPlayer)", base)
        blade = porting._behavior_item_class("net.demo", "SkinBladeItem")
        self.assertIn("extends Item", blade)
        self.assertIn("McaCompat.captureVillager(target)", blade)
        phone = porting._behavior_item_class("net.demo", "HypnosisPhoneItem")
        self.assertIn("McaCompat.possess(serverPlayer, target)", phone)
        self.assertIn("interactLivingEntity", phone)
        skin = porting._behavior_item_class("net.demo", "PlayerSkinItem")
        self.assertIn("implements Equipable", skin)
        self.assertIn("McaCompat.wear(serverPlayer, worn)", skin)
        self.assertNotIn("extends PossessionItem", blade)

        without_mca = porting._items_class(
            "net.demo", "demomod", "DemoMod", ["generated_item"], False
        )
        self.assertIn('"generated_item", Item::new', without_mca)
        self.assertNotIn("import net.demo.item.", without_mca)


if __name__ == "__main__":
    unittest.main()
