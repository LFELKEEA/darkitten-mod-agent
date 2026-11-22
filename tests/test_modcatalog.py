"""模型库扫描、持久化和界面动作的测试。"""

from __future__ import annotations

import base64
import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from .base import TempDataCase
from dma import modcatalog

# 1×1 PNG，用来占位；扫描只检查文件存在。
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def write_project(root: Path) -> None:
    """生成一个覆盖五类条目的最小工程。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / ".dma").mkdir(parents=True, exist_ok=True)
    (root / ".dma" / "project.json").write_text(
        json.dumps(
            {
                "name": "试验模组",
                "mod_id": "testmod",
                "mc_version": "1.20.1",
                "loader": "Forge",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    java = root / "src/main/java/com/testmod"
    registry = java / "registry"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "ModEntities.java").write_text(
        """
package com.testmod.registry;

public final class ModEntities {
    public static final RegistryObject<EntityType<TestMobEntity>> TEST_MOB =
        ENTITIES.register("test_mob", () -> EntityType.Builder.<TestMobEntity>of(
            TestMobEntity::new, MobCategory.MONSTER).sized(0.6F, 1.8F).fireImmune());

    public static void attributes(EntityAttributeCreationEvent event) {
        event.put(ModEntities.TEST_MOB.get(), Monster.createMonsterAttributes()
            .add(Attributes.MAX_HEALTH, 40.0D)
            .add(Attributes.ATTACK_DAMAGE, 7.0D)
            .add(Attributes.MOVEMENT_SPEED, 0.3D)
            .build());
    }
}
""".strip(),
        encoding="utf-8",
    )
    (registry / "ModBlocks.java").write_text(
        """
package com.testmod.registry;

public final class ModBlocks {
    public static final RegistryObject<Block> TEST_ORE =
        register("test_ore", () -> new TestOreBlock(BlockBehaviour.Properties.of()
            .strength(3.0F, 3.0F).lightLevel(state -> 7)));
}
""".strip(),
        encoding="utf-8",
    )
    (registry / "ModItems.java").write_text(
        """
package com.testmod.registry;

public final class ModItems {
    public static final RegistryObject<Item> TEST_GEM =
        ITEMS.register("test_gem", () -> new Item(new Item.Properties()
            .stacksTo(16).rarity(Rarity.RARE)));
}
""".strip(),
        encoding="utf-8",
    )
    (java / "entity").mkdir(parents=True, exist_ok=True)
    (java / "entity" / "TestMobEntity.java").write_text(
        """
package com.testmod.entity;

/** 试验生物：会在暮色里巡逻并追击玩家。 */
public class TestMobEntity extends Monster {
    public TestMobEntity(EntityType<? extends Monster> type, Level level) {
        super(type, level);
    }

    @Override
    protected void registerGoals() {
        this.goalSelector.addGoal(1, new MeleeAttackGoal(this, 1.0D, true));
        this.targetSelector.addGoal(2, new NearestAttackableTargetGoal<>(this, Player.class, true));
        this.goalSelector.addGoal(3, new RandomStrollGoal(this, 0.8D));
    }
}
""".strip(),
        encoding="utf-8",
    )
    (java / "block").mkdir(parents=True, exist_ok=True)
    (java / "block" / "TestOreBlock.java").write_text(
        """
package com.testmod.block;

/** 试验矿石：右键会亮一下，挖掉后掉落宝石。 */
public class TestOreBlock extends Block {
    @Override
    public InteractionResult use(BlockState state, Level level, BlockPos pos,
                                 Player player, InteractionHand hand, BlockHitResult hit) {
        return InteractionResult.SUCCESS;
    }
}
""".strip(),
        encoding="utf-8",
    )
    (java / "event").mkdir(parents=True, exist_ok=True)
    (java / "event" / "SpawnPlacementEvents.java").write_text(
        """
package com.testmod.event;

public final class SpawnPlacementEvents {
    public static void register() {
        SpawnPlacements.register(ModEntities.TEST_MOB.get(),
            SpawnPlacements.Type.ON_GROUND,
            Heightmap.Types.MOTION_BLOCKING_NO_LEAVES,
            (type, level, reason, pos, random) ->
                level.getRawBrightness(pos, 0) < 7 && level.getBlockState(pos.below()).is(ModBlocks.TEST_ORE.get()));
    }
}
""".strip(),
        encoding="utf-8",
    )

    assets = root / "src/main/resources/assets/testmod"
    for folder in ("lang", "textures/entity", "textures/block", "textures/item",
                   "models/block", "models/item", "blockstates"):
        (assets / folder).mkdir(parents=True, exist_ok=True)
    (assets / "textures/entity/test_mob.png").write_bytes(_PNG)
    (assets / "textures/block/test_ore.png").write_bytes(_PNG)
    (assets / "textures/item/test_gem.png").write_bytes(_PNG)
    (assets / "models/block/test_ore.json").write_text(
        json.dumps(
            {
                "parent": "minecraft:block/cube_all",
                "textures": {"all": "testmod:block/test_ore"},
            }
        ),
        encoding="utf-8",
    )
    (assets / "models/item/test_gem.json").write_text(
        json.dumps(
            {"parent": "minecraft:item/generated", "textures": {"layer0": "testmod:item/test_gem"}}
        ),
        encoding="utf-8",
    )
    (assets / "blockstates/test_ore.json").write_text(
        json.dumps({"variants": {"": {"model": "testmod:block/test_ore"}}}),
        encoding="utf-8",
    )
    (assets / "lang/zh_cn.json").write_text(
        json.dumps(
            {
                "entity.testmod.test_mob": "试验生物",
                "block.testmod.test_ore": "试验矿石",
                "item.testmod.test_gem": "试验宝石",
                "biome.testmod.test_biome": "试验群系",
                "dimension.testmod.test_dim": "试验维度",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    data = root / "src/main/resources/data/testmod"
    (data / "worldgen/biome").mkdir(parents=True, exist_ok=True)
    (data / "dimension").mkdir(parents=True, exist_ok=True)
    (data / "dimension_type").mkdir(parents=True, exist_ok=True)
    (data / "worldgen/biome/test_biome.json").write_text(
        json.dumps(
            {
                "temperature": 0.7,
                "downfall": 0.4,
                "effects": {
                    "sky_color": 0x4A3A6A,
                    "fog_color": 0x221A33,
                    "grass_color": 0x6D7F4A,
                    "foliage_color": 0x5A7040,
                    "water_color": 0x2F5F8A,
                },
                "spawners": {
                    "monster": [
                        {"type": "testmod:test_mob", "weight": 10, "minCount": 1, "maxCount": 2}
                    ]
                },
                "features": [["minecraft:test_ore"]],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data / "dimension/test_dim.json").write_text(
        json.dumps(
            {
                "type": "testmod:test_dim_type",
                "generator": {
                    "type": "minecraft:noise",
                    "biome_source": {
                        "type": "minecraft:multi_noise",
                        "biomes": [
                            {
                                "biome": "testmod:test_biome",
                                "parameters": {
                                    "temperature": [0.5, 1.0],
                                    "humidity": [0.0, 0.5],
                                },
                            }
                        ],
                    },
                },
                "structure_sets": ["minecraft:villages"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (data / "dimension_type/test_dim.json").write_text(
        json.dumps(
            {
                "ultrawarm": False,
                "natural": True,
                "coordinate_scale": 1.0,
                "has_skylight": False,
                "has_ceiling": False,
                "ambient_light": 0.1,
                "fixed_time": 18000,
                "min_y": 0,
                "height": 256,
                "logical_height": 256,
                "monster_spawn_light_level": 0,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_model_extras(root: Path) -> None:
    """追加 .bbmodel / .geo.json，验证扫描和预览都能找到它们。"""
    assets = root / "src/main/resources/assets/testmod"
    (assets / "geo").mkdir(parents=True, exist_ok=True)
    (assets / "models/block/bb_ore.bbmodel").write_text(
        json.dumps(
            {
                "meta": {"format_version": "4.5", "model_format": "java_block"},
                "elements": [
                    {
                        "type": "cube",
                        "from": [0, 0, 0],
                        "to": [16, 2, 4],
                        "origin": [8, 0, 2],
                        "rotation": [0, 90, 0],
                        "box_uv": True,
                        "uv_offset": [0, 0],
                    },
                    {
                        "type": "cube",
                        "from": [0, 2, 0],
                        "to": [16, 4, 16],
                        "box_uv": False,
                        "faces": {
                            "up": {"uv": [0, 0, 16, 16]},
                            "down": {"uv": [0, 16, 16, 32]},
                        },
                    },
                ],
                "textures": [
                    {
                        "name": "bb_ore.png",
                        "source": "data:image/png;base64,"
                        + base64.b64encode(_PNG).decode("ascii"),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (assets / "models/item/geo_gem.geo.json").write_text(
        json.dumps(
            {
                "format_version": "1.12.0",
                "minecraft:geometry": [
                    {
                        "description": {"identifier": "geometry.geo_gem"},
                        "bones": [
                            {
                                "name": "body",
                                "cubes": [
                                    {
                                        "origin": [0, 8, 0],
                                        "size": [16, 8, 16],
                                        "uv": [0, 0],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (assets / "geo/test_beast.geo.json").write_text(
        json.dumps({"minecraft:geometry": [{"bones": []}]}),
        encoding="utf-8",
    )


class ModCatalogTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "testmod"
        write_project(self.root)

    def test_scan_all_kinds(self) -> None:
        data = modcatalog.scan(self.root)
        self.assertEqual(
            data["counts"],
            {"entity": 1, "block": 1, "item": 1, "biome": 1, "dimension": 1},
        )
        by_id = {entry["id"]: entry for entry in data["entries"]}

        entity = by_id["entity:test_mob"]
        fields = {item["key"]: item["value"] for item in entity["fields"]}
        self.assertEqual(entity["title"], "试验生物")
        self.assertEqual(fields["阵营"], "敌对")
        self.assertEqual(fields["生命值"], "40.0")
        self.assertEqual(fields["攻击力"], "7.0")
        self.assertIn("试验群系", fields["生成群系"])
        self.assertIn("权重 10", fields["生成方式"])
        self.assertIn("光照 < 7", fields["生成方式"])
        self.assertIn("近战追击", fields["AI 逻辑"])
        self.assertEqual(entity["texture"], "src/main/resources/assets/testmod/textures/entity/test_mob.png")
        self.assertEqual(entity["model"], "")

        block = by_id["block:test_ore"]
        block_fields = {item["key"]: item["value"] for item in block["fields"]}
        self.assertEqual(block["title"], "试验矿石")
        self.assertIn("右键交互", block_fields["交互"])
        self.assertEqual(block_fields["发光等级"], "7")

        item = by_id["item:test_gem"]
        item_fields = {item["key"]: item["value"] for item in item["fields"]}
        self.assertEqual(item["title"], "试验宝石")
        self.assertEqual(item_fields["堆叠上限"], "16")
        self.assertEqual(item_fields["稀有度"], "RARE")

        biome = by_id["biome:test_biome"]
        biome_fields = {entry["key"]: entry["value"] for entry in biome["fields"]}
        self.assertEqual(biome["title"], "试验群系")
        self.assertEqual(biome_fields["所在维度"], "试验维度")
        self.assertIn("约 100.0%", biome_fields["生成概率"])
        self.assertIn("#4a3a6a", biome_fields["天空模样"].lower())

        dimension = by_id["dimension:test_dim"]
        dimension_fields = {entry["key"]: entry["value"] for entry in dimension["fields"]}
        self.assertEqual(dimension["title"], "试验维度")
        self.assertIn("Y 0 ~ 256", dimension_fields["维度大小"])
        self.assertIn("永久夜晚", dimension_fields["天空模样"])

    def test_java_scan_skips_cache_directories(self) -> None:
        cache = self.root / "data" / "gradle" / "caches"
        cache.mkdir(parents=True)
        (cache / "Injected.java").write_text("class Injected {}", encoding="utf-8")

        files = modcatalog._java_files(self.root)
        names = {path.name for path in files}
        self.assertIn("ModEntities.java", names)
        self.assertNotIn("Injected.java", names)

    def test_scan_geo_and_bbmodel(self) -> None:
        _write_model_extras(self.root)
        data = modcatalog.scan(self.root)
        by_id = {entry["id"]: entry for entry in data["entries"]}

        bb_block = by_id["block:bb_ore"]
        self.assertEqual(
            bb_block["model"],
            "src/main/resources/assets/testmod/models/block/bb_ore.bbmodel",
        )
        geo_item = by_id["item:geo_gem"]
        self.assertEqual(
            geo_item["model"],
            "src/main/resources/assets/testmod/models/item/geo_gem.geo.json",
        )
        beast = by_id["entity:test_beast"]
        self.assertEqual(
            beast["model"], "src/main/resources/assets/testmod/geo/test_beast.geo.json"
        )
        self.assertEqual(data["counts"]["entity"], 2)
        self.assertEqual(data["counts"]["block"], 2)
        self.assertEqual(data["counts"]["item"], 2)

    def test_parse_geo_and_bbmodel(self) -> None:
        from dma.ui.paint_page import model_kind, model_texture_images, parse_model

        geo = self.tmp / "beast.geo.json"
        geo.write_text(
            json.dumps(
                {
                    "minecraft:geometry": [
                        {
                            "bones": [
                                {
                                    "name": "head",
                                    "cubes": [
                                        {"origin": [-4, 24, -4], "size": [8, 8, 8], "uv": [0, 0]}
                                    ],
                                }
                            ]
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        boxes = parse_model(geo)
        self.assertEqual(len(boxes), 1)
        self.assertEqual(boxes[0].name, "head")
        self.assertEqual((boxes[0].origin, boxes[0].size), ((-4.0, 24.0, -4.0), (8.0, 8.0, 8.0)))
        self.assertEqual(model_kind(geo), "entity")

        bb = self.tmp / "prop.bbmodel"
        bb.write_text(
            json.dumps(
                {
                    "meta": {"format_version": "4.5", "model_format": "java_block"},
                    "elements": [
                        {
                            "type": "cube",
                            "from": [0, 0, 0],
                            "to": [16, 2, 4],
                            "origin": [8, 0, 2],
                            "rotation": [0, 90, 0],
                            "box_uv": True,
                            "uv_offset": [4, 8],
                        },
                        {"type": "mesh", "from": [0, 0, 0], "to": [1, 1, 1]},
                    ],
                    "textures": [
                        {
                            "name": "skin.png",
                            "source": "data:image/png;base64,"
                            + base64.b64encode(_PNG).decode("ascii"),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        boxes = parse_model(bb)
        self.assertEqual(len(boxes), 1)
        # 现在保留原始尺寸和旋转，交给 3D 视图按旋转角点渲染，
        # 不再把旋转压平成轴对齐包围盒。
        self.assertAlmostEqual(boxes[0].w, 16.0)
        self.assertAlmostEqual(boxes[0].d, 4.0)
        self.assertEqual(boxes[0].rotation, (0.0, 90.0, 0.0))
        self.assertEqual(boxes[0].pivot, (8.0, 0.0, 2.0))
        self.assertEqual(boxes[0].uv, (4, 8))
        self.assertEqual(model_kind(bb), "custom")
        images = model_texture_images(bb)
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0][0], "skin.png")

    def test_page_previews_bbmodel(self) -> None:
        from dma.ui.mod_library_page import ModLibraryPage

        _write_model_extras(self.root)
        if not hasattr(self, "app"):
            from PySide6.QtWidgets import QApplication

            self.app = QApplication.instance() or QApplication([])
        page = ModLibraryPage(_FakeMain())
        try:
            page.resize(1200, 800)
            page.set_project(SimpleNamespace(root=self.root))
            page._reload(select_id="block:bb_ore")
            self.assertIsNotNone(page._preview)
            self.assertEqual(len(page._preview._boxes), 2)
            relative = page._asset_relative(
                {"kind": "block", "key": "bb_ore"}, Path("Thing.BBMODEL"), texture=False
            )
            self.assertTrue(relative.endswith("/geo/bb_ore.bbmodel"))
        finally:
            page.deleteLater()
            self.app.processEvents()

    def test_overrides_and_instruction(self) -> None:
        modcatalog.save_entry(
            self.root,
            "entity:test_mob",
            title="夜行试验体",
            description="只在夜里巡逻。",
            fields={"生命值": "99"},
            custom_fields=[{"key": "性别", "value": "雌性"}],
        )
        data = modcatalog.load(self.root)
        entity = next(item for item in data["entries"] if item["id"] == "entity:test_mob")
        fields = {item["key"]: item["value"] for item in entity["fields"]}
        self.assertEqual(entity["title"], "夜行试验体")
        self.assertEqual(fields["生命值"], "99")
        self.assertEqual(entity["custom_fields"], [{"key": "性别", "value": "雌性"}])

        text = modcatalog.entry_instruction(entity, "让它只在月圆之夜出现")
        self.assertIn("夜行试验体", text)
        self.assertIn("性别：雌性", text)
        self.assertIn("只在月圆之夜出现", text)
        self.assertIn("mod_library.json", text)

    def test_custom_entry_create_and_delete(self) -> None:
        entry = modcatalog.create_custom_entry(
            self.root,
            kind="entity",
            key="moon_walker",
            title="月行者",
            description="在月光下出现。",
            fields=[{"key": "性别", "value": "雄性"}],
        )
        self.assertTrue(entry["custom"])
        self.assertEqual(entry["fields"], [{"key": "性别", "value": "雄性"}])
        data = modcatalog.load(self.root)
        self.assertEqual(data["counts"]["entity"], 2)
        self.assertTrue(any(item["id"] == "entity:moon_walker" for item in data["entries"]))

        with self.assertRaises(ValueError):
            modcatalog.create_custom_entry(
                self.root, kind="entity", key="moon_walker", title="重复"
            )

        self.assertTrue(modcatalog.delete_custom_entry(self.root, "entity:moon_walker"))
        data = modcatalog.load(self.root)
        self.assertEqual(data["counts"]["entity"], 1)
        self.assertFalse(modcatalog.delete_custom_entry(self.root, "entity:moon_walker"))


class _FakeMain:
    def __init__(self) -> None:
        self.chat_page = None

    def open_folder(self, _path) -> None:
        pass

    def status(self, _text: str) -> None:
        pass

    def go_to(self, _page) -> None:
        pass

    def ai_ready(self) -> bool:
        return False


class ModLibraryPageTests(TempDataCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "testmod"
        write_project(self.root)

    def test_project_combo_switches_library_only(self) -> None:
        from types import SimpleNamespace as NS

        from dma import project as project_mod
        from dma.ui.mod_library_page import ModLibraryPage

        second = self.tmp / "second"
        write_project(second)
        project_mod.remember_project(NS(root=self.root, label=lambda: "工程一"))
        project_mod.remember_project(NS(root=second, label=lambda: "工程二"))

        page = ModLibraryPage(_FakeMain())
        try:
            page.set_project(NS(root=self.root))
            labels = [
                page.project_combo.itemText(index)
                for index in range(page.project_combo.count())
            ]
            # 当前工程优先显示工程自身的名字，其余来自最近列表。
            self.assertIn("试验模组", labels)
            self.assertIn("工程二", labels)
            self.assertIn("打开其他工程…", labels)
            self.assertEqual(
                page.project_combo.currentData(), str(self.root.resolve())
            )
            page._set_library_root(second)
            self.assertEqual(page.root, second.resolve())
            self.assertEqual(
                page.project_combo.currentData(), str(second.resolve())
            )
            self.assertTrue(page._entries)
        finally:
            page.deleteLater()
            self.app.processEvents()

    def test_clearing_project_clears_previous_entries(self) -> None:
        from dma.ui.mod_library_page import ModLibraryPage

        page = ModLibraryPage(_FakeMain())
        try:
            page.set_project(SimpleNamespace(root=self.root))
            self.assertGreater(page.list.count(), 0)
            self.assertTrue(page._entries)

            page.set_project(None)
            self.assertEqual(page.list.count(), 0)
            self.assertEqual(page._entries, [])
            self.assertEqual(page._by_id, {})
            self.assertEqual(page.count_label.text(), "0 个")
            self.assertIsNone(page._entry)
            self.assertEqual(page.project_combo.count(), 1)
        finally:
            page.deleteLater()
            self.app.processEvents()

    def test_apply_online_skin_sets_texture(self) -> None:
        from dma.ui.mod_library_page import ModLibraryPage

        page = ModLibraryPage(_FakeMain())
        try:
            page.set_project(SimpleNamespace(root=self.root))
            entry = next(
                item
                for item in modcatalog.load(self.root)["entries"]
                if item["id"] == "entity:test_mob"
            )
            source = self.tmp / "online.png"
            source.write_bytes(_PNG)
            message = page._apply_online_asset(
                entry, source, {"kind": "skin", "title": "在线皮肤"}
            )
            self.assertIn("贴图", message)
            updated = next(
                item
                for item in modcatalog.load(self.root)["entries"]
                if item["id"] == "entity:test_mob"
            )
            self.assertTrue(updated["texture"].endswith("/test_mob.png"))
            self.assertTrue((self.root / updated["texture"]).is_file())
        finally:
            page.deleteLater()
            self.app.processEvents()

    def test_page_creates_starter_model_when_missing(self) -> None:
        from unittest.mock import patch

        from PySide6.QtWidgets import QMessageBox

        from dma.ui.mod_library_page import ModLibraryPage

        page = ModLibraryPage(_FakeMain())
        created: dict[str, str] = {}

        class FakeDialog:
            def __init__(
                self, root, relative, texture=None, parent=None, blank=False
            ) -> None:
                created["relative"] = relative
                created["blank"] = blank

            def exec(self) -> int:
                return 0

        try:
            page.resize(1200, 800)
            page.set_project(SimpleNamespace(root=self.root))
            entry = next(
                item
                for item in modcatalog.load(self.root)["entries"]
                if item["id"] == "entity:test_mob"
            )
            self.assertEqual(entry["model"], "")
            with patch(
                "dma.ui.mod_library_page.QMessageBox.question",
                return_value=QMessageBox.Yes,
            ), patch(
                "dma.ui.mod_library_page.ModelEditorDialog",
                FakeDialog,
            ):
                page._edit_model(entry)
        finally:
            page.deleteLater()
            self.app.processEvents()

        relative = created["relative"]
        self.assertTrue(relative.endswith("/geo/test_mob.bbmodel"))
        self.assertTrue((self.root / relative).is_file())
        saved = next(
            item
            for item in modcatalog.load(self.root)["entries"]
            if item["id"] == "entity:test_mob"
        )
        self.assertEqual(saved["model"], relative)
        self.assertTrue((self.root / saved["texture"]).is_file())

    def test_page_lists_and_saves_fields(self) -> None:
        from dma.ui.mod_library_page import ModLibraryPage

        page = ModLibraryPage(_FakeMain())
        try:
            page.resize(1200, 800)
            page.set_project(SimpleNamespace(root=self.root))
            self.assertEqual(page.list.count(), 5)
            page._set_kind("entity")
            self.assertEqual(page.list.count(), 1)
            self.assertIsNotNone(page._entry)
            self.assertEqual(page._entry["id"], "entity:test_mob")
            self.assertEqual(page._title_edit.text(), "试验生物")

            page._add_custom_row("性别", "雌性")
            self.assertTrue(page._save(page._entry))
            data = modcatalog.load(self.root)
            entity = next(item for item in data["entries"] if item["id"] == "entity:test_mob")
            self.assertEqual(entity["custom_fields"], [{"key": "性别", "value": "雌性"}])
            self.assertEqual(page.list.count(), 1)
        finally:
            page.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
