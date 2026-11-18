from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from dma import catalog as catalog_mod
from dma import launcher_bridge, modlibrary

from .base import TempDataCase


class CatalogCase(TempDataCase):
    def make_catalog(self) -> catalog_mod.Catalog:
        catalog = catalog_mod.Catalog(self.tmp / "catalog" / "catalog.json")
        catalog._data["sections"] = {
            "fabric": {
                "game": [{"version": "1.21.1", "stable": True}],
                "loader": [
                    {"version": "0.16.9", "stable": True},
                    {"version": "0.15.0", "stable": False},
                ],
                "loader_latest": "0.16.9",
                "fabric_api": ["0.115.0+1.21.1"],
            },
            "quilt": {
                "loader": [{"version": "0.27.0", "stable": True}],
                "loader_latest": "0.27.0",
            },
            "forge": {"versions": ["1.21.1-52.0.1"], "promos": {}},
            "neoforge": {"versions": ["21.1.80"]},
            "toolchain": {"gradle": {"current": "8.10.2"}},
            "changelogs": {
                "fabric": {"releases": [{"tag": "0.16.9", "name": "Fabric Loader"}]}
            },
        }
        catalog._save()
        return catalog

    def test_loader_for_mc(self) -> None:
        catalog = self.make_catalog()
        self.assertEqual(catalog.loader_for_mc("fabric", "1.21.1"), "0.16.9")
        self.assertEqual(catalog.loader_for_mc("quilt", "1.21.1"), "0.27.0")
        self.assertEqual(catalog.loader_for_mc("forge", "1.21.1"), "52.0.1")
        self.assertEqual(catalog.loader_for_mc("neoforge", "1.21.1"), "21.1.80")
        # Fabric / Quilt 的 loader 与游戏版本无关，没有匹配项时返回最新稳定版。
        self.assertEqual(catalog.loader_for_mc("fabric", "1.0"), "0.16.9")

    def test_mdk_and_javadoc_urls(self) -> None:
        catalog = self.make_catalog()
        forge = catalog.mdk("forge", "1.21.1")
        self.assertTrue(forge["url"].endswith("/forge/1.21.1-52.0.1/forge-1.21.1-52.0.1-mdk.zip"))
        self.assertIn("bmclapi2.bangbang93.com", forge["mirror"])
        neoforge = catalog.mdk("neoforge", "1.21.1")
        self.assertTrue(
            neoforge["url"].endswith(
                "NeoForgeMDKs/MDK-1.21.1-ModDevGradle/archive/refs/heads/main.zip"
            )
        )
        self.assertIn("MDK-1.21.1-NeoGradle", " ".join(neoforge["mirrors"]))
        self.assertEqual(neoforge["docs_url"], "https://docs.neoforged.net/")
        fabric = catalog.mdk("fabric", "1.21.1")
        self.assertIn("/refs/heads/1.21.1.zip", fabric["url"])
        self.assertTrue(fabric["template"])

        # Forge / NeoForge 不发布 javadoc.jar，回退到 sources.jar。
        forge_doc = catalog.javadoc("forge", "1.21.1")
        self.assertEqual(forge_doc["kind"], "sources")
        self.assertTrue(forge_doc["url"].endswith("forge-1.21.1-52.0.1-sources.jar"))
        self.assertIn("bmclapi2.bangbang93.com", forge_doc["mirror"])
        neo_doc = catalog.javadoc("neoforge", "1.21.1")
        self.assertEqual(neo_doc["kind"], "sources")
        self.assertTrue(neo_doc["url"].endswith("neoforge-21.1.80-sources.jar"))
        self.assertIn("bmclapi2.bangbang93.com", neo_doc["mirror"])

        quilt = catalog.javadoc("quilt", version="0.27.0")
        self.assertIn("quilt-loader-0.27.0-javadoc.jar", quilt["url"])
        self.assertEqual(quilt["kind"], "javadoc")

    def test_recommendations_and_summary(self) -> None:
        catalog = self.make_catalog()
        info = catalog.recommendations("1.21.1", "fabric")
        self.assertEqual(info["loader_version"], "0.16.9")
        self.assertEqual(info["fabric_api"], "0.115.0+1.21.1")
        all_loaders = catalog.recommendations("1.21.1")
        self.assertEqual(set(all_loaders["loaders"]), {"neoforge", "forge", "fabric", "quilt"})
        summary = catalog.summary()
        self.assertEqual(summary["fabric_loader"], 2)
        self.assertEqual(summary["changelog_loaders"], ["fabric"])
        self.assertEqual(catalog.changelog("fabric")[0]["tag"], "0.16.9")

    def test_snapshot_roundtrip_keeps_unknown_sections(self) -> None:
        catalog = self.make_catalog()
        path = catalog.path
        again = catalog_mod.Catalog(path)
        self.assertEqual(again.section("fabric")["loader_latest"], "0.16.9")
        self.assertTrue(path.is_file())

    def test_neoforge_prefix(self) -> None:
        self.assertEqual(catalog_mod._neoforge_prefix("1.21.1"), "21.1")
        self.assertEqual(catalog_mod._neoforge_prefix("1.20.4"), "20.4")
        self.assertEqual(catalog_mod._neoforge_prefix("26.3"), "26.3")

    def test_quilt_branch(self) -> None:
        self.assertEqual(catalog_mod._quilt_branch("1.20.1"), "1.20.1")
        self.assertEqual(catalog_mod._quilt_branch("1.21"), "1.21")
        self.assertEqual(catalog_mod._quilt_branch("1.21.1"), "1.21")
        self.assertEqual(catalog_mod._quilt_branch("1.20.5"), "1.20.6")
        self.assertEqual(catalog_mod._quilt_branch(""), "1.20.6")

    def test_quilt_loader_falls_back_to_release(self) -> None:
        # Quilt Meta 会把所有 loader 标成 stable=false，需要退回最新的非预览版。
        catalog = self.make_catalog()
        catalog._data["sections"]["quilt"] = {
            "loader": [
                {"version": "0.31.0-beta.6", "stable": False},
                {"version": "0.30.2", "stable": False},
                {"version": "0.29.0", "stable": False},
            ],
            "loader_latest": "",
        }
        catalog._save()
        self.assertEqual(catalog.loader_for_mc("quilt", "1.21.1"), "0.30.2")
        item = catalog.javadoc("quilt", "1.21.1")
        self.assertIn("quilt-loader-0.30.2-javadoc.jar", item["url"])

    def test_latest_release_filters_prerelease(self) -> None:
        self.assertEqual(
            catalog_mod._latest_release(["1.0.0", "1.1.0-beta.2", "1.2.0-rc.1"]), "1.0.0"
        )
        self.assertEqual(catalog_mod._latest_release(["0.30.2", "0.31.0-beta.6"]), "0.30.2")
        self.assertEqual(catalog_mod._latest_release(["0.31.0-beta.6"]), "0.31.0-beta.6")

    def test_sync_changelogs_falls_back_to_maven_and_tags(self) -> None:
        catalog = self.make_catalog()

        def fake_cached(key, url, loader, **kwargs):
            if "/releases" in url:
                if "FabricMC/fabric-loader" in url:
                    return [
                        {
                            "tag_name": "0.20.1",
                            "name": "Fabric Loader 0.20.1",
                            "html_url": "https://example.invalid/fabric",
                            "body": "notes",
                        }
                    ]
                return []
            if "/tags" in url:
                return [{"name": "v9.0.0"}, {"name": "v8.0.0"}]
            if "maven" in url and "neoforge" in url:
                raise RuntimeError("mirror down")
            if "maven" in url:
                return (
                    "<metadata><version>1.0.0</version>"
                    "<version>1.1.0</version></metadata>"
                )
            raise RuntimeError("unexpected url: " + url)

        with mock.patch.object(catalog, "_cached", side_effect=fake_cached):
            entry = catalog.sync_changelogs(force=True)

        self.assertTrue(entry.ok)
        self.assertEqual(entry.count, 1 + 2 + 2 + 2)
        sections = catalog.section("changelogs")
        self.assertEqual(sections["fabric"]["source"], "releases")
        self.assertEqual(sections["quilt"]["source"], "maven")
        self.assertEqual(sections["forge"]["source"], "maven")
        self.assertEqual(sections["neoforge"]["source"], "tags")
        # Maven 版本按版本号倒序，最新的排在最前面。
        self.assertEqual(catalog.changelog("forge")[0]["tag"], "1.1.0")
        self.assertEqual(catalog.changelog("neoforge")[0]["tag"], "v9.0.0")

    def test_cached_any_falls_back_to_mirror(self) -> None:
        catalog = self.make_catalog()
        seen: list[str] = []

        def fake_cached(key, url, loader, **kwargs):
            seen.append(url)
            if url == "https://blocked.invalid":
                raise RuntimeError("blocked")
            return loader("ok")

        with mock.patch.object(catalog, "_cached", side_effect=fake_cached):
            value = catalog._cached_any(
                "demo", ("https://blocked.invalid", "https://mirror.invalid"), lambda text: text
            )
        self.assertEqual(value, "ok")
        self.assertEqual(seen, ["https://blocked.invalid", "https://mirror.invalid"])


class ModLibraryCase(TempDataCase):
    def test_normalize_project(self) -> None:
        item = modlibrary._normalize_project(
            {
                "project_id": "abc",
                "slug": "demo",
                "title": "Demo",
                "project_type": "mod",
                "downloads": "42",
            }
        )
        self.assertEqual(item["id"], "abc")
        self.assertEqual(item["downloads"], 42)
        self.assertEqual(item["url"], "https://modrinth.com/mod/demo")
        self.assertEqual(modlibrary._loader_key("NeoForge"), "neoforge")
        self.assertEqual(modlibrary._loader_key("unknown"), "")

    def test_safe_target(self) -> None:
        root = self.tmp / "src"
        root.mkdir()
        target = modlibrary._safe_target(root, "com/example/Mod.java")
        self.assertTrue(str(target).startswith(str(root.resolve())))
        with self.assertRaises(modlibrary.ModLibraryError):
            modlibrary._safe_target(root, "../../escape.java")
        with self.assertRaises(modlibrary.ModLibraryError):
            modlibrary._safe_target(root, "")

    def test_download_skips_when_hash_matches(self) -> None:
        dest = self.tmp / "mods"
        dest.mkdir()
        jar = dest / "demo.jar"
        jar.write_bytes(b"hello")
        digest = hashlib.sha1(b"hello").hexdigest()
        version = {
            "id": "v1",
            "file": {
                "filename": "demo.jar",
                "url": "https://example.invalid/demo.jar",
                "size": 5,
                "sha1": digest,
            },
        }
        result = modlibrary.download(version, dest_dir=dest)
        self.assertEqual(result, jar)
        self.assertEqual(jar.read_bytes(), b"hello")

    def test_library_summary(self) -> None:
        mods = self.tmp / "mods"
        sources = self.tmp / "sources"
        mods.mkdir()
        sources.mkdir()
        (mods / "a.jar").write_bytes(b"x" * 10)
        (sources / "demo" / "1.0").mkdir(parents=True)
        with mock.patch.object(modlibrary, "mods_dir", return_value=mods), mock.patch.object(
            modlibrary, "sources_dir", return_value=sources
        ):
            summary = modlibrary.library_summary()
        self.assertEqual(summary["mods"], 1)
        self.assertEqual(summary["sources"], 1)
        self.assertEqual(summary["jar_bytes"], 10)


class LauncherBridgeCase(TempDataCase):
    def make_launcher(self) -> Path:
        root = self.tmp / "launcher"
        game = root / "minecraft"
        versions = game / "versions"
        (versions / "1.21.1").mkdir(parents=True)
        (versions / "1.21.1" / "1.21.1.json").write_text(
            json.dumps(
                {
                    "id": "1.21.1",
                    "type": "release",
                    "releaseTime": "2024-08-08T00:00:00+00:00",
                    "javaVersion": {"majorVersion": 21},
                }
            ),
            encoding="utf-8",
        )
        (versions / "fabric-loader-0.16.9-1.21.1").mkdir()
        (versions / "fabric-loader-0.16.9-1.21.1" / "fabric-loader-0.16.9-1.21.1.json").write_text(
            json.dumps(
                {
                    "id": "fabric-loader-0.16.9-1.21.1",
                    "inheritsFrom": "1.21.1",
                    "releaseTime": "2024-09-01T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        (versions / "1.21.1-neoforge-21.1.80").mkdir()
        (versions / "1.21.1-neoforge-21.1.80" / "1.21.1-neoforge-21.1.80.json").write_text(
            json.dumps({"id": "1.21.1-neoforge-21.1.80", "inheritsFrom": "1.21.1"}),
            encoding="utf-8",
        )
        (root / "config.json").write_text(
            json.dumps(
                {
                    "game_dir": str(game),
                    "version_isolation": True,
                    "version_loaders": {"1.21.1": "neoforge"},
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_loader_from_id(self) -> None:
        self.assertEqual(
            launcher_bridge._loader_from_id("fabric-loader-0.16.9-1.21.1"),
            ("fabric", "0.16.9", "1.21.1"),
        )
        self.assertEqual(
            launcher_bridge._loader_from_id("1.21.1-neoforge-21.1.80"),
            ("neoforge", "21.1.80", "1.21.1"),
        )
        self.assertEqual(launcher_bridge._loader_from_id("1.21.1"), ("", "", "1.21.1"))

    def test_files_mode_scan(self) -> None:
        root = self.make_launcher()
        game = root / "minecraft"
        link = launcher_bridge.LauncherLink(
            mode="files", app_dir=str(root), game_dir=str(game)
        )
        bridge = launcher_bridge.LauncherBridge(link)
        items = {item.id: item for item in bridge.versions()}
        self.assertIn("1.21.1", items)
        self.assertIn("fabric-loader-0.16.9-1.21.1", items)
        self.assertTrue(items["fabric-loader-0.16.9-1.21.1"].is_loader_profile)
        self.assertEqual(items["1.21.1-neoforge-21.1.80"].loader, "neoforge")
        self.assertEqual(items["1.21.1"].java_major, 21)
        # loader profile 不出现在推荐实例里
        recommended = {item.id for item in bridge.recommended_instances()}
        self.assertNotIn("fabric-loader-0.16.9-1.21.1", recommended)
        self.assertIn("1.21.1-neoforge-21.1.80", recommended)

    def test_discover_files_mode(self) -> None:
        root = self.make_launcher()
        # 本机可能真的跑着启动器联动桥，这里只允许扫描测试目录，避免环境干扰。
        with mock.patch.object(
            launcher_bridge, "launcher_candidates", return_value=[root]
        ), mock.patch.dict(
            os.environ,
            {"DARKITTEN_LAUNCHER_HOME": str(root), "DARKITTEN_BRIDGE_FILE": str(self.tmp / "missing.json")},
        ):
            link = launcher_bridge.discover()
        self.assertEqual(link.mode, "files")
        self.assertEqual(Path(link.game_dir), root / "minecraft")
        self.assertTrue(link.version_isolation)
        self.assertEqual(link.loaders.get("1.21.1"), "neoforge")

    def test_link_dict(self) -> None:
        link = launcher_bridge.LauncherLink(mode="bridge", url="http://127.0.0.1:1", token="t")
        self.assertTrue(link.online)
        self.assertNotIn("token", link.to_dict())
        offline = launcher_bridge.LauncherLink(mode="files", game_dir="x")
        self.assertFalse(offline.online)


if __name__ == "__main__":
    unittest.main()