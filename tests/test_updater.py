from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path

from dma import updater

from .base import TempDataCase


def build_zip(path: Path, *, wrapper: str = "", traversal: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"{wrapper}/" if wrapper else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix + "DarkittenModAgent.exe", b"MZ" + b"\x00" * 64)
        zf.writestr(prefix + "_internal/base_library.zip", b"PK" + b"\x00" * 32)
        if traversal:
            zf.writestr("../evil.txt", "bad")
    return path


class UpdaterTests(TempDataCase):
    def test_version_compare(self) -> None:
        self.assertTrue(updater.is_newer("0.2.0", "0.1.9"))
        self.assertTrue(updater.is_newer("v1.0", "0.9.9"))
        self.assertFalse(updater.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updater.is_newer("bad", "0.1.0"))
        self.assertEqual(updater.parse_version("v2.3.4"), (2, 3, 4))

    def test_parse_release(self) -> None:
        info = updater.parse_release(
            {
                "mod_agent": {
                    "version": "0.2.0",
                    "download_url": "downloads/ModAgent.zip",
                    "sha256": "a" * 64,
                    "size": 123,
                    "notes": ["修复 A", "- 新增 B"],
                    "released": "2026-11-03",
                }
            },
            "https://example.com/update.json",
        )
        self.assertEqual(info.version, "0.2.0")
        self.assertEqual(info.download_url, "https://example.com/downloads/ModAgent.zip")
        self.assertEqual(info.notes, ["修复 A", "新增 B"])
        with self.assertRaises(updater.UpdateError):
            updater.parse_release({"version": "1.0", "sha256": "xyz"})

    def test_check_for_update_from_file(self) -> None:
        manifest = self.tmp / "mod_agent_release.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": "99.0.0",
                    "download_url": "ModAgent.zip",
                    "sha256": "b" * 64,
                    "size": 100,
                }
            ),
            encoding="utf-8",
        )
        info = updater.check_for_update(manifest.as_uri())
        self.assertIsNotNone(info)
        self.assertEqual(info.version, "99.0.0")
        self.assertTrue(info.download_url.endswith("ModAgent.zip"))

    def test_prepare_flat_and_wrapped(self) -> None:
        flat = build_zip(self.tmp / "flat.zip")
        payload = updater.prepare_update(flat, "0.2.0")
        self.assertTrue((payload / "DarkittenModAgent.exe").is_file())
        self.assertTrue((payload / "_internal").is_dir())

        wrapped = build_zip(self.tmp / "wrapped.zip", wrapper="DarkittenModAgent")
        payload = updater.prepare_update(wrapped, "0.3.0")
        self.assertEqual(payload.name, "DarkittenModAgent")
        self.assertTrue((payload / "DarkittenModAgent.exe").is_file())

    def test_prepare_rejects_bad_package(self) -> None:
        bad = self.tmp / "bad.zip"
        with zipfile.ZipFile(bad, "w") as zf:
            zf.writestr("DarkittenModAgent.exe", b"MZ" + b"\x00" * 8)
        with self.assertRaises(updater.UpdateError):
            updater.prepare_update(bad, "0.2.0")

        traversal = build_zip(self.tmp / "traversal.zip", traversal=True)
        with self.assertRaises(updater.UpdateError):
            updater.prepare_update(traversal, "0.2.0")

        not_zip = self.tmp / "not.zip"
        not_zip.write_bytes(b"not a zip")
        with self.assertRaises(updater.UpdateError):
            updater.prepare_update(not_zip, "0.2.0")

    def test_sha256(self) -> None:
        target = self.tmp / "a.txt"
        target.write_text("abc", encoding="utf-8")
        self.assertEqual(
            updater.sha256_file(target),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )

    def test_download_requires_hash(self) -> None:
        info = updater.UpdateInfo(version="1.0.0", download_url="https://example.com/a.zip")
        with self.assertRaises(updater.UpdateError):
            updater.download_update(info)


if __name__ == "__main__":
    unittest.main()
