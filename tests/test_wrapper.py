from __future__ import annotations

import unittest
from pathlib import Path

from dma import wrapper
from dma.project import create_project

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

    def test_moddev_wrapper_is_switched_to_gradle_8(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="MCA",
            mod_id="mca_test",
            mc_version="1.21.1",
            loader="NeoForge",
        )
        (project.root / "build.gradle").write_text(
            "plugins {\n"
            "    id 'java-library'\n"
            "    id 'net.neoforged.moddev' version '2.0.115'\n"
            "}\n",
            encoding="utf-8",
        )
        wrapper.ensure_wrapper(project.root, "9.8.2")
        self.assertEqual(wrapper.current_distribution(project.root), "9.8.2")

        target, reason = wrapper.compatible_version(project.root)
        self.assertEqual(target, wrapper.MODDEV_DISTRIBUTION)
        self.assertIn("ModDevGradle", reason)

        changed, before, after, _ = wrapper.ensure_compatible(project.root)
        self.assertTrue(changed)
        self.assertEqual(before, "9.8.2")
        self.assertEqual(after, wrapper.MODDEV_DISTRIBUTION)
        self.assertEqual(
            wrapper.current_distribution(project.root), wrapper.MODDEV_DISTRIBUTION
        )

    def test_moddev_keeps_gradle_8(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="MCA",
            mod_id="mca_test",
            mc_version="1.21.1",
            loader="NeoForge",
        )
        (project.root / "build.gradle").write_text(
            "plugins { id 'net.neoforged.moddev' version '2.0.115' }\n",
            encoding="utf-8",
        )
        wrapper.ensure_wrapper(project.root, "8.14")
        changed, _before, _after, _ = wrapper.ensure_compatible(project.root)
        self.assertFalse(changed)
        self.assertEqual(wrapper.current_distribution(project.root), "8.14")

    def test_corrupted_properties_is_repaired(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="MCA",
            mod_id="mca_test",
            mc_version="1.21.1",
            loader="NeoForge",
        )
        (project.root / "build.gradle").write_text(
            "plugins { id 'net.neoforged.moddev' version '2.0.115' }\n",
            encoding="utf-8",
        )
        folder = project.root / "gradle" / "wrapper"
        folder.mkdir(parents=True)
        (folder / "gradle-wrapper.properties").write_text(
            "libs/mca-neoforge-7.7.36-beta.3+1.21.1.jar", encoding="utf-8"
        )
        self.assertFalse(wrapper.properties_valid(project.root))
        changed, before, after, _ = wrapper.ensure_compatible(project.root)
        self.assertTrue(changed)
        self.assertEqual(before, "")
        self.assertEqual(after, wrapper.MODDEV_DISTRIBUTION)
        self.assertTrue(wrapper.properties_valid(project.root))
        text = (folder / "gradle-wrapper.properties").read_text(encoding="utf-8")
        self.assertIn("distributionBase=", text)


if __name__ == "__main__":
    unittest.main()
