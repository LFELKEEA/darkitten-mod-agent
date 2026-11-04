from __future__ import annotations

import unittest
from pathlib import Path

from dma import wrapper
from dma.project import create_project
from dma.ui.build_page import scaffold_request

from .base import TempDataCase


class WrapperTests(TempDataCase):
    def test_assets_present(self) -> None:
        source = wrapper.assets_dir()
        for name in ("gradlew", "gradlew.bat", "gradle-wrapper.jar"):
            self.assertTrue((source / name).is_file(), name)

    def test_ensure_wrapper(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Wrapper Mod",
            mod_id="wrapper_mod",
            mc_version="1.21.1",
            loader="Fabric",
        )
        self.assertFalse(wrapper.has_wrapper(project.root))
        written = wrapper.ensure_wrapper(project.root)
        self.assertIn("gradlew", written)
        self.assertIn("gradlew.bat", written)
        self.assertTrue(wrapper.has_wrapper(project.root))
        jar = project.root / "gradle" / "wrapper" / "gradle-wrapper.jar"
        self.assertEqual(jar.read_bytes()[:2], b"PK")
        props = (project.root / "gradle" / "wrapper" / "gradle-wrapper.properties").read_text(
            encoding="utf-8"
        )
        self.assertIn(f"gradle-{wrapper.DEFAULT_DISTRIBUTION}-bin.zip", props)
        self.assertIn("services.gradle.org", props)
        self.assertEqual(wrapper.current_distribution(project.root), wrapper.DEFAULT_DISTRIBUTION)

    def test_current_distribution_missing(self) -> None:
        self.assertEqual(wrapper.current_distribution(self.tmp), "")

    def test_missing_project(self) -> None:
        with self.assertRaises(wrapper.WrapperError):
            wrapper.ensure_wrapper(self.tmp / "nope")


class ScaffoldRequestTests(TempDataCase):
    def project(self, loader: str):
        return create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo_mod",
            mc_version="1.21.1",
            loader=loader,
        )

    def test_fabric_request(self) -> None:
        text = scaffold_request(self.project("Fabric"))
        self.assertIn("1.21.1", text)
        self.assertIn("demo_mod", text)
        self.assertIn("fabric-loom", text)
        self.assertIn("settings.gradle", text)
        self.assertIn("build.gradle", text)
        self.assertIn("不要猜", text)
        self.assertIn(wrapper.DEFAULT_DISTRIBUTION, text)

    def test_neoforge_request(self) -> None:
        text = scaffold_request(self.project("NeoForge"))
        self.assertIn("NeoGradle", text)
        self.assertIn("NeoForge", text)


if __name__ == "__main__":
    unittest.main()
