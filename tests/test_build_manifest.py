from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build


class ReleaseManifestTest(unittest.TestCase):
    def test_manifest_is_online_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "DarkittenModAgent-test.zip"
            archive.write_bytes(b"zip")
            with mock.patch.object(build, "DIST", root):
                target = build.write_manifest(archive)
                bootstrap = build.write_bootstrap()
            data = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(data["kind"], "online")
            self.assertEqual(data["policy"], "online-first")
            self.assertEqual(data["bundled"], [])
            self.assertTrue(data["bootstrap_url"].endswith("mod_agent_bootstrap.json"))
            self.assertEqual(
                {item["kind"] for item in data["online_resources"]},
                {"jdk", "gradle", "catalog", "sources", "mods"},
            )
            boot = json.loads(bootstrap.read_text(encoding="utf-8"))
            self.assertEqual(boot["policy"], "online-first")
            self.assertEqual(boot["version"], build.APP_VERSION)


if __name__ == "__main__":
    unittest.main()