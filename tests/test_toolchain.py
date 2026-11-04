from __future__ import annotations

import hashlib
import http.server
import os
import threading
import zipfile
from pathlib import Path
from unittest import mock

from dma import envcheck, toolchain, wrapper
from dma.ui.build_page import scaffold_request
from dma.project import create_project

from .base import TempDataCase


def make_jdk(root: Path, version: str) -> Path:
    home = root / f"jdk-{version}"
    (home / "bin").mkdir(parents=True)
    exe = home / "bin" / ("java.exe" if os.name == "nt" else "java")
    exe.write_text("", encoding="utf-8")
    (home / "release").write_text(f'JAVA_VERSION="{version}"\n', encoding="utf-8")
    return home


class DistributionTests(TempDataCase):
    URL = "https://services.gradle.org/distributions/gradle-9.8.2-bin.zip"

    def test_cache_layout_matches_gradle(self) -> None:
        dist = toolchain.distribution_for(self.URL, self.tmp / "gradle-home")
        self.assertIsNotNone(dist)
        assert dist is not None
        self.assertEqual(dist.name, "gradle-9.8.2-bin")
        self.assertEqual(dist.version, "9.8.2")
        self.assertEqual(dist.cache.name, "8wepis63pzuhrlfaiy04bagwu")
        self.assertEqual(dist.home.name, "gradle-9.8.2")
        self.assertEqual(dist.marker.name, "gradle-9.8.2-bin.zip.ok")

    def test_properties_parsing(self) -> None:
        project = self.tmp / "project"
        folder = project / "gradle" / "wrapper"
        folder.mkdir(parents=True)
        (folder / "gradle-wrapper.properties").write_text(
            "distributionUrl=https\\://services.gradle.org/distributions/"
            "gradle-9.8.2-bin.zip\n"
            "distributionSha256Sum=ABCDEF\n",
            encoding="utf-8",
        )
        self.assertEqual(toolchain.distribution_url(project), self.URL)
        self.assertEqual(toolchain.distribution_sha256(project), "abcdef")

    def test_install_zip(self) -> None:
        archive = self.tmp / "gradle-9.8.2-bin.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("gradle-9.8.2/bin/gradle.bat", "@echo off\n")
            zf.writestr("gradle-9.8.2/bin/gradle", "#!/bin/sh\n")
            zf.writestr("gradle-9.8.2/lib/gradle-launcher.jar", "jar")
        dist = toolchain.distribution_for(self.URL, self.tmp / "gradle-home")
        assert dist is not None
        self.assertFalse(dist.installed())
        toolchain.install_distribution_zip(archive, dist)
        self.assertTrue(dist.installed())
        self.assertTrue((dist.home / "bin" / "gradle").is_file())
        self.assertTrue(dist.marker.is_file())


class JavaTests(TempDataCase):
    def test_find_java_prefers_21(self) -> None:
        root = self.tmp / "jdks"
        homes = [
            make_jdk(root, "25.0.1"),
            make_jdk(root, "17.0.10"),
            make_jdk(root, "21.0.9"),
        ]
        install = toolchain.find_java(homes)
        assert install is not None
        self.assertEqual(install.major, 21)
        self.assertEqual(install.version, "21.0.9")

    def test_envcheck_picks_21_over_25(self) -> None:
        root = self.tmp / "jdks"
        homes = [
            make_jdk(root, "25.0.1"),
            make_jdk(root, "21.0.9"),
        ]
        exes = [envcheck.java_executable(home) for home in homes]
        item = envcheck.check_java([exe for exe in exes if exe is not None])
        self.assertEqual(item.status, envcheck.STATUS_OK)
        self.assertIn("Java 21", item.summary)
        self.assertIn("其他可用：Java 25", item.detail)

    def test_with_java_home(self) -> None:
        home = Path("C:/fake/jdk-21")
        env = toolchain.with_java_home({"PATH": "C:/Windows"}, home)
        self.assertEqual(env["JAVA_HOME"], str(home))
        self.assertTrue(env["PATH"].startswith(str(home / "bin")))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # noqa: D102
        pass


class DownloadTests(TempDataCase):
    def test_download_and_verify_hash(self) -> None:
        data = os.urandom(1024 * 1024 + 123)
        source = self.tmp / "payload.bin"
        source.write_bytes(data)
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), lambda *a, **k: _QuietHandler(*a, directory=str(self.tmp), **k)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{source.name}"
            dest = self.tmp / "out" / "payload.bin"
            toolchain.download(
                url, dest, sha256=hashlib.sha256(data).hexdigest()
            )
            self.assertEqual(dest.read_bytes(), data)
            self.assertFalse(dest.with_name(dest.name + ".part").exists())
        finally:
            server.shutdown()
            server.server_close()

    def test_hash_mismatch_rejected(self) -> None:
        data = b"hello" * 100
        source = self.tmp / "payload.bin"
        source.write_bytes(data)
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), lambda *a, **k: _QuietHandler(*a, directory=str(self.tmp), **k)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_port}/{source.name}"
            with self.assertRaises(toolchain.ToolchainError):
                toolchain.download(url, self.tmp / "out" / "payload.bin", sha256="00" * 32)
        finally:
            server.shutdown()
            server.server_close()


class PrepareTests(TempDataCase):
    def test_prepare_writes_wrapper_and_keeps_java(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo_mod",
            mc_version="1.21.1",
            loader="Fabric",
        )
        java_home = make_jdk(self.tmp / "jdks", "21.0.9")
        install = toolchain.JavaInstall(home=java_home, version="21.0.9", major=21)
        with mock.patch.object(toolchain, "ensure_java", return_value=install), mock.patch.object(
            toolchain, "ensure_distribution", return_value=None
        ):
            prepared = toolchain.prepare(project.root, allow_download=False)
        self.assertEqual(prepared.java_version, "21.0.9")
        self.assertTrue(prepared.wrapper_written)
        self.assertTrue(wrapper.has_wrapper(project.root))
        props = project.root / "gradle" / "wrapper" / "gradle-wrapper.properties"
        self.assertIn(wrapper.DEFAULT_DISTRIBUTION, props.read_text(encoding="utf-8"))
        self.assertEqual(prepared.notes, [])

    def test_existing_wrapper_version_is_kept(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo_mod",
            mc_version="1.21.1",
            loader="Fabric",
        )
        wrapper.ensure_wrapper(project.root, "8.14")
        java_home = make_jdk(self.tmp / "jdks", "21.0.9")
        install = toolchain.JavaInstall(home=java_home, version="21.0.9", major=21)
        with mock.patch.object(toolchain, "ensure_java", return_value=install), mock.patch.object(
            toolchain, "ensure_distribution", return_value=None
        ):
            prepared = toolchain.prepare(project.root, allow_download=False)
        self.assertEqual(prepared.wrapper_written, [])
        props = project.root / "gradle" / "wrapper" / "gradle-wrapper.properties"
        self.assertIn("gradle-8.14-bin.zip", props.read_text(encoding="utf-8"))

    def test_scaffold_request_mentions_one_click(self) -> None:
        project = create_project(
            self.tmp / "projects",
            name="Demo",
            mod_id="demo_mod",
            mc_version="1.21.1",
            loader="Fabric",
        )
        text = scaffold_request(project)
        self.assertIn("一键准备", text)


if __name__ == "__main__":
    import unittest

    unittest.main()
