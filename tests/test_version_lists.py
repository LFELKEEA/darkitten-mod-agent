from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dma import studio_bridge  # noqa: E402
from dma.studio_engine import versions  # noqa: E402

RELEASES = ["26.3", "1.21.9", "1.21.8", "1.21.1", "1.20.2", "1.20.1", "1.12.2"]


class SupportedVersionsTest(unittest.TestCase):
    def test_fabric_filters_by_metadata(self) -> None:
        with mock.patch.object(versions, "minecraft_release_ids", return_value=RELEASES), mock.patch.object(
            versions, "_fabric_games", return_value={"26.3", "1.20.1"}
        ):
            self.assertEqual(versions.supported_mc_ids("fabric"), ["26.3", "1.20.1"])

    def test_forge_parses_artifact_prefix(self) -> None:
        artifacts = ["1.21.9-60.0.1", "1.20.1-47.4.5", "1.7.10-10.13.4.1614"]
        with mock.patch.object(versions, "minecraft_release_ids", return_value=RELEASES), mock.patch.object(
            versions, "_forge_versions", return_value=artifacts
        ):
            self.assertEqual(versions.supported_mc_ids("forge"), ["1.21.9", "1.20.1"])

    def test_neoforge_maps_version_prefix(self) -> None:
        artifacts = ["26.3.0.101", "21.9.0", "20.2.93"]
        with mock.patch.object(versions, "minecraft_release_ids", return_value=RELEASES), mock.patch.object(
            versions, "_neoforge_versions", return_value=artifacts
        ):
            self.assertEqual(versions.supported_mc_ids("neoforge"), ["26.3", "1.21.9", "1.20.2"])

    def test_metadata_failure_keeps_full_list(self) -> None:
        with mock.patch.object(versions, "minecraft_release_ids", return_value=RELEASES), mock.patch.object(
            versions, "_fabric_games", return_value=set()
        ):
            self.assertEqual(versions.supported_mc_ids("fabric"), RELEASES)

    def test_fallback_covers_more_than_nine_versions(self) -> None:
        self.assertGreater(len(studio_bridge.MC_VERSIONS), 9)
        self.assertIn("1.21.11", studio_bridge.MC_VERSIONS)

    def test_bridge_uses_fallback_when_metadata_empty(self) -> None:
        with mock.patch.object(studio_bridge.versions_mod, "supported_mc_ids", return_value=[]):
            self.assertEqual(
                studio_bridge.mc_versions("Forge"),
                studio_bridge.MC_VERSIONS,
            )


if __name__ == "__main__":
    unittest.main()
