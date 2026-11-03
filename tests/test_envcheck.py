from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from dma import connectors, envcheck
from dma.config import AIConfig

from .base import TempDataCase


class EnvCheckTests(TempDataCase):
    def test_java_major(self) -> None:
        self.assertEqual(envcheck.java_major("1.8.0_392"), 8)
        self.assertEqual(envcheck.java_major("17.0.10"), 17)
        self.assertEqual(envcheck.java_major("21"), 21)
        self.assertEqual(envcheck.java_major(""), 0)

    def test_java_missing(self) -> None:
        with mock.patch.object(envcheck, "_java_candidates", return_value=[]):
            item = envcheck.check_java()
        self.assertEqual(item.status, envcheck.STATUS_FAIL)
        self.assertEqual(item.key, "java")
        self.assertTrue(item.url)

    def test_java_21_ok(self) -> None:
        fake = Path("C:/fake/bin/java.exe")
        proc = subprocess.CompletedProcess(
            [str(fake), "-version"],
            0,
            "",
            'openjdk version "21.0.2" 2024-01-16\nOpenJDK Runtime Environment\n',
        )
        with mock.patch.object(envcheck, "_java_candidates", return_value=[fake]), mock.patch.object(
            envcheck, "_run", return_value=proc
        ):
            item = envcheck.check_java()
        self.assertEqual(item.status, envcheck.STATUS_OK)
        self.assertIn("Java 21", item.summary)

    def test_java_home_invalid(self) -> None:
        with mock.patch.dict("os.environ", {"JAVA_HOME": str(self.tmp / "missing")}):
            item = envcheck.check_java_home()
        self.assertEqual(item.status, envcheck.STATUS_WARN)

    def test_gradle_wrapper_detected(self) -> None:
        project = self.tmp / "project"
        wrapper = project / "gradle" / "wrapper"
        wrapper.mkdir(parents=True)
        (project / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
        (wrapper / "gradle-wrapper.jar").write_bytes(b"jar")
        (wrapper / "gradle-wrapper.properties").write_text("distributionUrl=x\n", encoding="utf-8")
        with mock.patch.object(envcheck.shutil, "which", return_value=None):
            item = envcheck.check_gradle(project)
        self.assertEqual(item.status, envcheck.STATUS_OK)
        self.assertIn("Wrapper", item.summary)

    def test_gradle_wrapper_incomplete(self) -> None:
        project = self.tmp / "project"
        project.mkdir()
        (project / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
        with mock.patch.object(envcheck.shutil, "which", return_value=None):
            item = envcheck.check_gradle(project)
        self.assertEqual(item.status, envcheck.STATUS_WARN)

    def test_network_summary(self) -> None:
        with mock.patch.object(connectors, "fetch_url", return_value="{}"):
            item = envcheck.check_network()
        self.assertEqual(item.status, envcheck.STATUS_OK)
        self.assertIn("3/3", item.summary)

    def test_ai_key_required(self) -> None:
        item = envcheck.check_ai(AIConfig(provider="openai", api_key=""))
        self.assertEqual(item.status, envcheck.STATUS_FAIL)
        item = envcheck.check_ai(AIConfig(provider="openai", api_key="sk-test"))
        self.assertEqual(item.status, envcheck.STATUS_OK)

    def test_report_text(self) -> None:
        item = envcheck.CheckItem(
            "java", "Java 运行时", envcheck.STATUS_OK, "Java 21 可用", "21.0.2", "无需处理"
        )
        text = envcheck.report_text([item])
        self.assertIn("[通过] Java 运行时", text)
        self.assertIn("Java 21 可用", text)


if __name__ == "__main__":
    unittest.main()
