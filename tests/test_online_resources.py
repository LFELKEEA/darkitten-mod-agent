from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from dma import resources

from .base import TempDataCase


class BootstrapManifestTest(unittest.TestCase):
    def test_manifest_is_online_first(self) -> None:
        data = resources.bootstrap_manifest()
        self.assertEqual(data["policy"], "online-first")
        kinds = {item["kind"] for item in data["resources"]}
        self.assertIn("jdk", kinds)
        self.assertIn("gradle", kinds)
        self.assertIn("mods", kinds)
        self.assertIn("官方源", " ".join(data["notes"]))
        self.assertTrue(data["mirrors"]["gradle"][1].startswith("https://repo.huaweicloud.com"))

    def test_mirror_order_starts_with_official(self) -> None:
        self.assertTrue(resources.MIRRORS["forge"][0].startswith("https://maven.minecraftforge.net"))
        self.assertIn("bmclapi2.bangbang93.com", resources.MIRRORS["forge"][1])

    def test_site_manifest_uses_published_copy(self) -> None:
        with mock.patch.object(
            resources.catalog_mod,
            "fetch_json",
            return_value={"version": "9.9.9", "resources": [{"kind": "jdk"}]},
        ):
            data = resources.site_manifest()
        self.assertEqual(data["source"], "site")
        self.assertEqual(data["version"], "9.9.9")

    def test_site_manifest_falls_back_when_offline(self) -> None:
        with mock.patch.object(
            resources.catalog_mod, "fetch_json", side_effect=OSError("offline")
        ):
            data = resources.site_manifest()
        self.assertEqual(data["source"], "local")
        self.assertEqual(data["policy"], "online-first")


class EnsureTest(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self._site = mock.patch.object(
            resources,
            "site_manifest",
            return_value={
                "version": "3.3.0",
                "source": "local",
                "resources": [{"kind": "jdk"}],
                "mirrors": resources.MIRRORS,
            },
        )
        self._site.start()
        self.addCleanup(self._site.stop)

    def test_status_does_not_download(self) -> None:
        with mock.patch.object(resources.toolchain, "find_java", return_value=None), mock.patch.object(
            resources, "_catalog"
        ) as catalog_factory:
            catalog_factory.return_value.summary.return_value = {}
            data = resources.ensure("status")
        self.assertEqual(data["policy"], "online-first")
        self.assertIsNone(data["java"])

    def test_ensure_jdk_uses_online_toolchain(self) -> None:
        install = mock.Mock()
        install.home = Path("C:/jdk21")
        install.version = "21.0.1"
        install.major = 21
        install.source = "download"
        with mock.patch.object(resources.toolchain, "ensure_java", return_value=install):
            data = resources.ensure("jdk")
        item = data["results"][0]
        self.assertTrue(item["ok"])
        self.assertEqual(item["version"], "21.0.1")
        self.assertEqual(item["source"], "download")

    def test_ensure_gradle_requires_project(self) -> None:
        data = resources.ensure("gradle")
        self.assertFalse(data["results"][0]["ok"])
        self.assertIn("打开工程", data["results"][0]["error"])

    def test_ensure_catalog_refreshes(self) -> None:
        report = mock.Mock()
        report.text.return_value = "目录已更新"
        with mock.patch.object(resources, "_catalog") as catalog_factory:
            catalog_factory.return_value.refresh.return_value = report
            data = resources.ensure("catalog")
        self.assertTrue(data["results"][0]["ok"])
        self.assertIn("目录已更新", data["results"][0]["message"])

    def test_ensure_sources_uses_devdocs(self) -> None:
        with mock.patch.object(
            resources.devdocs, "ensure_sources", return_value=Path("C:/docs/sources.jar")
        ):
            data = resources.ensure("sources", loader="fabric", mc="1.21.1")
        item = data["results"][0]
        self.assertTrue(item["ok"])
        self.assertIn("sources.jar", item["path"])


class RenderTest(unittest.TestCase):
    def test_render_status_mentions_mirrors(self) -> None:
        text = resources.render(
            {
                "policy": "online-first",
                "java": {"version": "21", "home": "C:/jdk"},
                "gradle": {"version": "8.10", "installed": True, "home": "C:/gradle"},
                "catalog": {
                    "minecraft_versions": 923,
                    "fabric_loader": 535,
                    "forge_versions": 5106,
                    "neoforge_versions": 1859,
                },
                "docs": {"count": 2, "bytes": 1024},
            }
        )
        self.assertIn("JDK", text)
        self.assertIn("BMCLAPI", text)


if __name__ == "__main__":
    unittest.main()
