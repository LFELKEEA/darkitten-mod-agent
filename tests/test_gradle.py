from __future__ import annotations

import os
import subprocess
import sys
import threading
import unittest
from pathlib import Path

from dma import gradle

from .base import TempDataCase


class GradleResolveTests(TempDataCase):
    def test_tasks(self) -> None:
        self.assertEqual(
            set(gradle.GRADLE_TASKS),
            {"build", "runClient", "runServer", "runDatagen"},
        )

    def test_wrapper_preferred(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        wrapper = root / ("gradlew.bat" if sys.platform == "win32" else "gradlew")
        wrapper.write_text("@echo off\n", encoding="utf-8")
        launch = gradle.resolve_launch(root, "build", system="gradle")
        self.assertEqual(launch.source, "wrapper")
        self.assertEqual(launch.task, "build")
        self.assertEqual(launch.cwd, root)
        self.assertIn("build", launch.argv)
        self.assertIn(str(wrapper), launch.argv)

    def test_system_fallback(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        launch = gradle.resolve_launch(root, "runClient", system="C:/fake/gradle")
        self.assertEqual(launch.source, "system")
        self.assertEqual(launch.argv, ["C:/fake/gradle", "runClient"])

    @unittest.skipUnless(os.name == "nt", "只有 Windows 需要经过 cmd")
    def test_wrapper_path_with_spaces(self) -> None:
        root = self.tmp / "mod with space"
        root.mkdir()
        wrapper = root / "gradlew.bat"
        wrapper.write_text("@echo off\r\necho WRAPPER_OK %*\r\n", encoding="utf-8")
        init = self.tmp / "mirror with space" / "mirror.gradle"
        init.parent.mkdir()
        init.write_text("", encoding="utf-8")
        launch = gradle.resolve_launch(root, "build", init_script=init)
        self.assertIsNotNone(launch.command)
        self.assertTrue(launch.command.startswith('cmd /d /s /c "'))
        self.assertTrue(launch.command.endswith('"'))
        result = subprocess.run(
            launch.command,
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )
        output = gradle.decode_line(result.stdout)
        self.assertEqual(result.returncode, 0, output)
        self.assertIn("WRAPPER_OK", output)
        self.assertIn("build", output)

    def test_missing_gradle(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        with self.assertRaises(gradle.GradleError):
            gradle.resolve_launch(root, "build", system="")

    def test_unknown_task(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        with self.assertRaises(gradle.GradleError):
            gradle.resolve_launch(root, "clean", system="gradle")

    def test_gradle_files(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        self.assertFalse(gradle.has_gradle_files(root))
        (root / "build.gradle").write_text("plugins {}\n", encoding="utf-8")
        self.assertTrue(gradle.has_gradle_files(root))

    def test_decode_gbk(self) -> None:
        self.assertEqual(gradle.decode_line("中文输出".encode("gbk")), "中文输出")
        self.assertEqual(gradle.decode_line("中文输出".encode("utf-8")), "中文输出")

    def test_network_error_detection(self) -> None:
        self.assertTrue(gradle.is_network_error("Could not download fabric-loom.jar"))
        self.assertTrue(gradle.is_network_error("Connection reset"))
        self.assertTrue(gradle.is_network_error("Read timed out"))
        self.assertFalse(gradle.is_network_error("error: cannot find symbol"))

    def test_cert_error_detection(self) -> None:
        self.assertTrue(
            gradle.is_cert_error(
                "Exception in thread \"main\" javax.net.ssl.SSLHandshakeException: "
                "(certificate_unknown) PKIX path building failed: "
                "sun.security.provider.certpath.SunCertPathBuilderException: "
                "unable to find valid certification path to requested target"
            )
        )
        self.assertFalse(gradle.is_cert_error("BUILD SUCCESSFUL in 12s"))
        self.assertFalse(gradle.is_cert_error(""))

    def test_build_env_windows_trust(self) -> None:
        base = {"JAVA_OPTS": "-Xmx2g"}
        off = gradle.build_env(False, base=base)
        self.assertEqual(off["JAVA_OPTS"], "-Xmx2g")
        if sys.platform == "win32":
            env = gradle.build_env(True, base=base)
            self.assertIn(gradle.WINDOWS_TRUST_OPTION, env["JAVA_OPTS"])
            self.assertTrue(env["JAVA_OPTS"].startswith("-Xmx2g"))
            again = gradle.build_env(True, base=env)
            self.assertEqual(again["JAVA_OPTS"].count(gradle.WINDOWS_TRUST_OPTION), 1)
        else:
            env = gradle.build_env(True, base=base)
            self.assertEqual(env["JAVA_OPTS"], "-Xmx2g")

    def test_required_gradle(self) -> None:
        text = (
            "Plugin net.fabricmc:fabric-loom:1.19.3 requires at least Gradle 9.8.0. "
            "This build uses Gradle 8.14."
        )
        self.assertEqual(gradle.required_gradle(text), "9.8.0")
        self.assertEqual(
            gradle.required_gradle("Minimum supported Gradle version is 8.5."), "8.5"
        )
        self.assertEqual(gradle.required_gradle("BUILD SUCCESSFUL"), "")
        self.assertTrue(gradle.version_tuple("9.8.2") > gradle.version_tuple("8.14"))
        self.assertEqual(gradle.version_tuple("9.8"), (9, 8))


class GradleRunnerTests(TempDataCase):
    def test_collects_output_and_exit_code(self) -> None:
        lines: list[str] = []
        done = threading.Event()
        result: dict[str, object] = {}

        def finished(code: int, message: str) -> None:
            result["code"] = code
            result["message"] = message
            done.set()

        runner = gradle.GradleRunner(lines.append, finished)
        script = "import sys; print('line-1'); print('line-2'); sys.exit(3)"
        runner.start(
            gradle.Launch([sys.executable, "-c", script], Path.cwd(), "build", "system")
        )
        self.assertTrue(done.wait(20), "runner 没有在 20 秒内结束")
        self.assertEqual(result["code"], 3)
        self.assertEqual(result["message"], "")
        self.assertIn("line-1", lines)
        self.assertIn("line-2", lines)
        self.assertFalse(runner.running)

    def test_passes_environment(self) -> None:
        lines: list[str] = []
        done = threading.Event()

        def finished(code: int, message: str) -> None:
            done.set()

        runner = gradle.GradleRunner(lines.append, finished)
        script = "import os; print('PROBE=' + os.environ.get('DMA_PROBE', ''))"
        env = dict(os.environ, DMA_PROBE="trust-me")
        runner.start(
            gradle.Launch([sys.executable, "-c", script], Path.cwd(), "build", "system"),
            env,
        )
        self.assertTrue(done.wait(20), "runner 没有在 20 秒内结束")
        self.assertTrue(any("PROBE=trust-me" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
