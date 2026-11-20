"""Structured texture generator tests (blocks, items, entity UV atlases)."""

from __future__ import annotations

from pathlib import Path

from .base import TempDataCase

from dma.kernel.tools import texture


class TextureToolTests(TempDataCase):
    def test_palette_is_an_ordered_ramp(self) -> None:
        ramp = texture.palette("#7a6a55", 6)
        self.assertEqual(6, len(ramp))
        luminance = [sum(texture._rgb(color)) for color in ramp]
        self.assertEqual(luminance, sorted(luminance))
        self.assertGreater(luminance[-1] - luminance[0], 80)

    def test_structured_block_styles_are_not_flat(self) -> None:
        for style in ("stone", "dirt", "grass", "planks", "ore", "metal", "crystal",
                      "bricks", "log", "log_top", "sand", "snow", "obsidian", "glow",
                      "mossy", "deepslate", "gravel", "clay"):
            target = self.tmp / f"{style}.png"
            texture.generate(target, texture.TextureSpec(
                size=(16, 16), style=style, base="#7a6a55", accent="#b98ed9", seed=7,
            ))
            review = texture.validate(target)
            self.assertTrue(review["ok"], (style, review["issues"]))
            self.assertGreaterEqual(review["colors"], 4, style)
            self.assertGreater(review["edge_ratio"], 0.10, style)

    def test_leaves_can_be_transparent(self) -> None:
        target = self.tmp / "leaves.png"
        texture.generate(target, texture.TextureSpec(
            size=(16, 16), style="leaves", base="#3f6b33", accent="#2b4a22",
            seed=3, transparent=True,
        ))
        review = texture.validate(target)
        self.assertTrue(review["ok"], review["issues"])
        self.assertTrue(review["transparent"])

    def test_item_categories_all_produce_readable_sprites(self) -> None:
        categories = (
            "ironwood_sword", "ironwood_pickaxe", "ironwood_axe", "ironwood_shovel",
            "ironwood_hoe", "ironwood_helmet", "ironwood_chestplate",
            "ironwood_leggings", "ironwood_boots", "ironwood_ingot", "moonstone",
            "scale_shard", "serpent_fang", "spectral_cloth", "twilight_stick",
            "arcane_dust", "lich_sigil", "lich_staff", "twilight_eye",
            "twilight_berry", "labyrinth_map", "grove_guide", "lich_trophy",
            "lich_bone", "spore_sac", "firefly_jelly", "gloom_mushroom_stew",
            "twilight_shard", "ironwood_bow", "twilight_arrow", "herb_bowl",
            "twilight_potion", "grove_scroll", "silver_ring", "moon_amulet",
            "rusty_key", "raven_feather", "gold_nugget", "twilight_apple",
            "honey_bread", "raw_meat", "wolf_hide", "old_coin",
        )
        for kind in categories:
            target = self.tmp / f"{kind}.png"
            texture.generate_item(target, kind, base="#6f8f6a", accent="#d7c26a", seed=5)
            review = texture.validate(target)
            self.assertTrue(review["ok"], (kind, review["issues"]))
            self.assertGreaterEqual(review["colors"], 3, kind)
            self.assertGreater(review["edge_ratio"], 0.12, kind)

    def test_humanoid_atlas_paints_standard_uv_regions(self) -> None:
        target = self.tmp / "hollow_stalker.png"
        texture.generate_entity(target, base="#b9b3a1", accent="#b53b3b", seed=7,
                                variant="undead")
        review = texture.validate(target, expected_size=(64, 64))
        self.assertTrue(review["ok"], review["issues"])
        self.assertEqual([64, 64], review["size"])
        from PIL import Image

        image = Image.open(target).convert("RGBA")
        # head front face (8..16, 8..16) must contain the eyes and a body
        head_front = [image.getpixel((x, y)) for y in range(8, 16) for x in range(8, 16)]
        self.assertTrue(all(pixel[3] == 255 for pixel in head_front))
        self.assertGreater(len({pixel[:3] for pixel in head_front}), 3)
        # legs and arms are painted too
        self.assertEqual(255, image.getpixel((2, 24))[3])
        self.assertEqual(255, image.getpixel((26, 26))[3])

    def test_entity_boxes_out_of_bounds_are_skipped(self) -> None:
        target = self.tmp / "custom.png"
        texture.generate_entity(target, layout="boxes", base="#445566", accent="#ffaa33",
                                boxes=[{"part": "body", "uv": (0, 0), "size": (8, 8, 4)},
                                       {"part": "bad", "uv": (60, 60), "size": (16, 16, 16)}])
        self.assertTrue(target.is_file())
        review = texture.validate(target, expected_size=(64, 64))
        self.assertTrue(review["ok"], review["issues"])