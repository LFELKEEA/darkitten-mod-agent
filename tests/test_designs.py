from __future__ import annotations

import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from dma import arttools, designs


class DesignSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _session(self, kind: str, name: str, description: str) -> designs.DesignSession:
        return designs.create_session(
            self.root,
            kind=kind,
            name=name,
            description=description,
            namespace="demo",
            count=3,
        )

    def test_generates_preview_and_texture_for_every_kind(self) -> None:
        for kind, name, text in (
            ("entity", "ember_cat", "火焰猫，红色毛发，绿色眼睛"),
            ("item", "star_sword", "星光剑，蓝紫色"),
            ("block", "moss_crystal", "苔藓水晶方块"),
        ):
            session = self._session(kind, name, text)
            self.assertEqual(len(session.variants), 3)
            for variant in session.variants:
                for key in ("preview", "texture"):
                    path = session.dir / variant[key]
                    self.assertTrue(path.is_file(), path)
                    width, height = arttools._read_png_size(path.read_bytes())
                    self.assertGreater(width, 0)
                    self.assertGreater(height, 0)
                self.assertTrue((session.dir / variant["model"]).is_file())

    def test_apply_writes_expected_paths(self) -> None:
        entity = self._session("entity", "ember_cat", "火焰猫")
        applied = designs.apply_variant(self.root, entity, "B")
        self.assertIn(
            "src/main/resources/assets/demo/textures/entity/ember_cat.png",
            applied["written"],
        )
        self.assertIn(
            "src/main/resources/assets/demo/geo/ember_cat.geo.json",
            applied["written"],
        )

        block = self._session("block", "moss_crystal", "苔藓水晶方块")
        applied = designs.apply_variant(self.root, block, "A")
        self.assertIn(
            "src/main/resources/assets/demo/blockstates/moss_crystal.json",
            applied["written"],
        )
        self.assertIn(
            "src/main/resources/assets/demo/models/block/moss_crystal.json",
            applied["written"],
        )

    def test_retexture_keeps_model(self) -> None:
        session = self._session("entity", "ember_cat", "火焰猫")
        variant = session.variants[1]
        model = variant["model"]
        texture = (session.dir / variant["texture"]).read_bytes()
        time.sleep(0.01)
        session.retexture(variant["id"], extra="金色描边")
        self.assertEqual(session.variants[1]["model"], model)
        self.assertNotEqual((session.dir / variant["texture"]).read_bytes(), texture)

    def test_upload_png_becomes_a_variant(self) -> None:
        session = self._session("item", "star_sword", "星光剑")
        source = self.root / "mine.png"
        source.write_bytes((session.dir / session.variants[0]["texture"]).read_bytes())
        item = session.adopt_upload(source, label="我的贴图")
        self.assertTrue(item["uploaded"])
        self.assertEqual(item["id"], "D")
        self.assertTrue((session.dir / item["texture"]).is_file())

    def test_session_round_trips(self) -> None:
        session = self._session("entity", "ember_cat", "火焰猫")
        loaded = designs.load_session(self.root, session.id)
        self.assertEqual(len(loaded.variants), 3)
        self.assertEqual(loaded.name, "ember_cat")

    def test_request_submit_and_cancel(self) -> None:
        session = self._session("entity", "ember_cat", "火焰猫")
        request = designs.DesignRequest(session)
        request.submit({"action": "adopt", "variant": "B"})
        self.assertEqual(request.wait(1)["variant"], "B")

        cancel = threading.Event()
        cancel.set()
        request = designs.DesignRequest(session, cancel=cancel)
        self.assertEqual(request.wait(1)["action"], "cancel")


if __name__ == "__main__":
    unittest.main()
