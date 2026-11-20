"""手动模型编辑器：三种格式的读写和界面动作。"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from .base import TempDataCase
from dma.ui.model_editor import EditableCube, load_document, save_document
from dma.ui.paint_page import Box


def write_bbmodel(path: Path, *, cubes: int = 1) -> None:
    elements = []
    for index in range(cubes):
        elements.append(
            {
                "name": f"body{index}",
                "type": "cube",
                "from": [0, 0, 0],
                "to": [8, 8, 8],
                "origin": [4, 4, 4],
                "box_uv": True,
                "uv_offset": [0, 0],
            }
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "meta": {"format_version": "4.5", "model_format": "java_block"},
                "textures": [{"name": "skin.png", "source": "data:image/png;base64,AA=="}],
                "elements": elements,
            }
        ),
        encoding="utf-8",
    )


class ModelDocumentTests(TempDataCase):
    def test_starter_models_are_editable(self) -> None:
        from dma.ui.model_editor import create_starter_model, starter_relative
        from dma.ui.paint_page import HUMANOID_BOXES

        root = self.tmp / "proj"
        block_rel = starter_relative("block", "demo", "stone")
        create_starter_model(
            root, block_rel, kind="block", name="试验方块", modid="demo", key="stone"
        )
        block_doc = load_document(root / block_rel)
        self.assertEqual(block_doc.fmt, "java")
        self.assertEqual(len(block_doc.cubes), 1)
        self.assertEqual(block_doc.cubes[0].box.size, (16.0, 16.0, 16.0))

        entity_rel = starter_relative("entity", "demo", "walkers")
        create_starter_model(
            root, entity_rel, kind="entity", name="试验生物", modid="demo", key="walkers"
        )
        entity_doc = load_document(root / entity_rel)
        self.assertEqual(entity_doc.fmt, "bbmodel")
        self.assertEqual(len(entity_doc.cubes), len(HUMANOID_BOXES))

    def test_bbmodel_roundtrip_preserves_extra_fields(self) -> None:
        root = self.tmp / "proj"
        relative = "src/main/resources/assets/demo/geo/prop.bbmodel"
        path = root / relative
        write_bbmodel(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        data["elements"].append({"type": "mesh", "vertices": {}})
        data["elements"][0]["rotation"] = [0, 45, 0]
        data["elements"][0]["box_uv"] = False
        data["elements"][0]["faces"] = {"north": {"uv": [0, 0, 8, 8]}}
        path.write_text(json.dumps(data), encoding="utf-8")

        doc = load_document(path)
        self.assertEqual(doc.fmt, "bbmodel")
        self.assertEqual(len(doc.cubes), 1)

        box = doc.cubes[0].box
        box.origin = (2.0, 1.0, 0.0)
        box.size = (6.0, 5.0, 4.0)
        box.uv = (4, 2)
        save_document(doc, doc.cubes, root=root, relative=relative)

        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(saved["elements"]), 2)
        self.assertTrue(any(item.get("type") == "mesh" for item in saved["elements"]))
        node = next(item for item in saved["elements"] if item.get("name") == "body0")
        self.assertEqual(node["from"], [2.0, 1.0, 0.0])
        self.assertEqual(node["to"], [8.0, 6.0, 4.0])
        self.assertEqual(node["rotation"], [0, 45, 0])
        self.assertEqual(node["faces"]["north"]["uv"], [4, 2, 12, 10])

    def test_geo_adds_cube_to_same_bone(self) -> None:
        root = self.tmp / "proj"
        relative = "src/main/resources/assets/demo/geo/mob.geo.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "format_version": "1.12.0",
                    "minecraft:geometry": [
                        {
                            "description": {"identifier": "geometry.demo"},
                            "bones": [
                                {
                                    "name": "body",
                                    "cubes": [
                                        {"origin": [0, 0, 0], "size": [8, 8, 8], "uv": [0, 0]}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        doc = load_document(path)
        self.assertEqual(doc.fmt, "geo")
        self.assertEqual(len(doc.cubes), 1)
        doc.cubes[0].box.origin = (1.0, 2.0, 3.0)
        doc.cubes.append(
            EditableCube(
                Box("head", (0, 8, 0), (4, 4, 4), (0, 16)),
                None,
                doc.cubes[0].bone,
                (0, 16),
            )
        )
        save_document(doc, doc.cubes, root=root, relative=relative)

        saved = json.loads(path.read_text(encoding="utf-8"))
        bones = saved["minecraft:geometry"][0]["bones"]
        self.assertEqual(len(bones), 1)
        self.assertEqual(len(bones[0]["cubes"]), 2)
        self.assertEqual(bones[0]["cubes"][0]["origin"], [1.0, 2.0, 3.0])
        self.assertEqual(bones[0]["cubes"][1]["size"], [4.0, 4.0, 4.0])
        self.assertEqual(bones[0]["cubes"][1]["uv"], [0, 16])

    def test_java_parent_only_creates_elements(self) -> None:
        root = self.tmp / "proj"
        relative = "src/main/resources/assets/demo/models/item/gem.json"
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "parent": "minecraft:item/generated",
                    "textures": {"layer0": "demo:item/gem"},
                }
            ),
            encoding="utf-8",
        )
        doc = load_document(path)
        self.assertEqual(doc.fmt, "java")
        self.assertEqual(doc.cubes, [])
        doc.cubes.append(EditableCube(Box("cube", (0, 0, 0), (16, 16, 16), (0, 0))))
        save_document(doc, doc.cubes, root=root, relative=relative)

        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["parent"], "minecraft:item/generated")
        self.assertEqual(len(saved["elements"]), 1)
        element = saved["elements"][0]
        self.assertEqual(element["to"], [16.0, 16.0, 16.0])
        self.assertEqual(element["faces"]["up"]["uv"], [0, 0, 16, 16])
        self.assertIn("north", element["faces"])


class ModelEditorDialogTests(TempDataCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_edits_adds_and_saves(self) -> None:
        from dma.ui.model_editor import ModelEditorDialog

        root = self.tmp / "proj"
        relative = "src/main/resources/assets/demo/geo/prop.bbmodel"
        write_bbmodel(root / relative)
        dialog = ModelEditorDialog(root, relative)
        try:
            self.assertEqual(dialog.list.count(), 1)
            self.assertEqual(dialog.x_spin.value(), 0.0)
            dialog.x_spin.setValue(3.5)
            dialog.w_spin.setValue(10.0)
            self.assertEqual(dialog._cubes[0].box.x, 3.5)
            self.assertEqual(dialog._cubes[0].box.w, 10.0)

            dialog._add_cube()
            self.assertEqual(dialog.list.count(), 2)
            self.assertEqual(dialog.list.currentRow(), 1)
            dialog._delete_cube()
            self.assertEqual(dialog.list.count(), 1)

            dialog._add_cube()
            dialog._save()
            self.assertTrue(dialog.saved)
        finally:
            dialog.deleteLater()
            self.app.processEvents()

        saved = json.loads((root / relative).read_text(encoding="utf-8"))
        self.assertEqual(len(saved["elements"]), 2)
        self.assertEqual(saved["elements"][0]["from"], [3.5, 0.0, 0.0])
        self.assertEqual(saved["elements"][0]["to"], [13.5, 8.0, 8.0])

    def test_view_marks_selected_box(self) -> None:
        from PySide6.QtGui import QImage

        from dma.ui.paint_page import ModelView

        view = ModelView()
        view.resize(400, 400)
        image = QImage(64, 64, QImage.Format_ARGB32)
        image.fill(0xFF808080)
        view.set_image(image)
        view.set_kind("custom")
        view.set_boxes([Box("a", (0, 0, 0), (8, 8, 8), (0, 0))])
        view.set_select_mode(True)
        view.set_selected_box(0)
        view._compute_layout()
        view._render_faces()
        self.assertTrue(view._faces)
        self.assertIn("index", view._faces[0])
        self.assertEqual(view.selected_box(), 0)
        view.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()