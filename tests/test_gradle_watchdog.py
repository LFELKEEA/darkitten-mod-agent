from __future__ import annotations

import os
import subprocess
import threading
import time
import unittest
from unittest import mock

from dma.gradle import windows_command
from dma.mcdev import gradle

from .base import TempDataCase


class _BlockingStream:
    """模拟“子进程还活着，但一行输出都没有”的 stdout。"""

    def __init__(self) -> None:
        self._closed = threading.Event()

    def __iter__(self):
        return self

    def __next__(self):
        self._closed.wait()
        raise StopIteration

    def close(self) -> None:
        self._closed.set()


class _HangingProcess:
    pid = 424242

    def __init__(self) -> None:
        self.stdout = _BlockingStream()

    def wait(self, timeout=None):  # noqa: ARG002 - 签名要与 Popen 一致
        return -9


class _EmptyStream:
    def __iter__(self):
        return iter(())

    def close(self) -> None:
        return None


class _QuietProcess:
    pid = 424243

    def __init__(self) -> None:
        self.stdout = _EmptyStream()

    def wait(self, timeout=None):  # noqa: ARG002
        return 0


class GradleWatchdogTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        self.root = self.tmp / "mod"
        self.root.mkdir()
        (self.root / ("gradlew.bat" if os.name == "nt" else "gradlew")).write_text(
            "@echo off\n", encoding="utf-8"
        )
        # 这些用例只关心看门狗，不要让镜像探测真的去连网络。
        patcher = mock.patch("dma.mirrors.prepare_launch_env", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _run(self, process: _HangingProcess, **kwargs):
        lines: list[str] = []
        with mock.patch.object(gradle.subprocess, "Popen", return_value=process):
            with mock.patch.object(
                gradle, "_kill", side_effect=lambda p, root=None: p.stdout.close()
            ):
                with self.assertRaises(gradle.GradleError) as ctx:
                    gradle.run_gradle(self.root, "build", log=lines.append, **kwargs)
        return str(ctx.exception), lines

    def test_no_output_hits_stall_timeout(self) -> None:
        process = _HangingProcess()
        started = time.time()
        message, lines = self._run(process, timeout=60, stall_timeout=0.5)
        self.assertLess(time.time() - started, 10.0)
        self.assertIn("没有任何输出", message)
        self.assertIn("DMA_GRADLE_OFFLINE", message)
        self.assertTrue(lines)

    def test_cancel_works_without_output(self) -> None:
        process = _HangingProcess()
        cancel = threading.Event()
        threading.Timer(0.3, cancel.set).start()
        started = time.time()
        message, _ = self._run(
            process, timeout=60, stall_timeout=30, cancel=cancel
        )
        self.assertLess(time.time() - started, 10.0)
        self.assertIn("已取消", message)

    def test_heartbeat_is_logged_while_waiting(self) -> None:
        process = _HangingProcess()
        with mock.patch.object(gradle, "HEARTBEAT_SECONDS", 0.2):
            message, lines = self._run(process, timeout=60, stall_timeout=0.8)
        self.assertIn("没有任何输出", message)
        self.assertTrue(
            any("正在执行 build：已用" in line for line in lines),
            lines,
        )


class GradlePreflightTests(TempDataCase):
    def test_unreachable_repository_switches_to_offline(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "repositories {\n"
            "    maven { url 'https://maven.invalid.example/releases' }\n"
            "}\n",
            encoding="utf-8",
        )
        with mock.patch.object(gradle, "_reachable", return_value=False) as probe:
            result = gradle.preflight(root)
        self.assertTrue(result["offline"])
        self.assertEqual(result["hosts"], ["maven.invalid.example"])
        probe.assert_called_once_with("maven.invalid.example", 443)

    def test_reachable_repository_keeps_online_mode(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "repositories { maven { url 'https://maven.example.com/releases' } }\n",
            encoding="utf-8",
        )
        with mock.patch.object(gradle, "_reachable", return_value=True):
            result = gradle.preflight(root)
        self.assertFalse(result["offline"])
        self.assertEqual(result["unreachable"], [])

    def test_offline_flag_is_added_to_command(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / ("gradlew.bat" if os.name == "nt" else "gradlew")).write_text(
            "@echo off\n", encoding="utf-8"
        )
        argv = gradle._command(root, "build", offline=True)
        self.assertIn("--offline", argv)
        self.assertNotIn("--offline", gradle._command(root, "build"))

    def test_network_hint_names_unreachable_host(self) -> None:
        hint = gradle._network_hint(
            "Could not resolve plugin",
            {
                "hosts": ["maven.neoforged.net"],
                "unreachable": ["https://maven.neoforged.net/releases"],
                "offline": True,
            },
        )
        self.assertIn("maven.neoforged.net", hint)
        self.assertIn("--offline", hint)

    def test_network_hint_mentions_mirror_when_mirrored(self) -> None:
        hint = gradle._network_hint(
            "Could not resolve plugin",
            {"hosts": ["maven.neoforged.net"], "mirrored": True},
        )
        self.assertIn("镜像", hint)
        self.assertNotIn("--offline", hint)

    def test_network_hint_silent_for_code_errors(self) -> None:
        hint = gradle._network_hint("error: cannot find symbol", {"hosts": []})
        self.assertEqual(hint, "")

    def _project(self) -> "object":
        root = self.tmp / "mod"
        root.mkdir(exist_ok=True)
        (root / ("gradlew.bat" if os.name == "nt" else "gradlew")).write_text(
            "@echo off\n", encoding="utf-8"
        )
        return root

    def test_init_script_is_passed_to_gradle(self) -> None:
        root = self._project()
        init_script = self.tmp / "mirror.gradle"
        init_script.write_text("// mirror", encoding="utf-8")
        argv = gradle._command(root, "build", init_script=init_script)
        self.assertIn("--init-script", argv)
        self.assertIn(str(init_script), argv)

    def test_unreachable_hosts_switch_to_mirror_not_offline(self) -> None:
        root = self._project()
        init_script = self.tmp / "mirror.gradle"
        init_script.write_text("// mirror", encoding="utf-8")
        probe = {
            "urls": ["https://maven.neoforged.net/releases"],
            "unreachable": ["https://maven.neoforged.net/releases"],
            "hosts": ["maven.neoforged.net"],
            "offline": True,
        }
        with mock.patch.object(gradle, "preflight", return_value=dict(probe)):
            with mock.patch.object(gradle, "_ensure_gradle_distribution") as dist:
                with mock.patch(
                    "dma.mirrors.prepare_launch_env", return_value=init_script
                ) as prepare:
                    with mock.patch.object(
                        gradle.subprocess, "Popen", return_value=_QuietProcess()
                    ) as popen:
                        code, _output = gradle.run_gradle(root, "build")
        argv = popen.call_args[0][0]
        self.assertEqual(code, 0)
        self.assertIn("--init-script", argv)
        self.assertIn(str(init_script), argv)
        self.assertNotIn("--offline", argv)
        prepare.assert_called_once()
        dist.assert_called_once()

    def test_mirror_enabled_for_neogradle_project_without_repo_urls(self) -> None:
        """NeoGradle 的仓库 URL 不在构建脚本里，preflight 扫不到也必须启用镜像。"""
        root = self._project()
        (root / "build.gradle").write_text(
            "plugins { id 'net.neoforged.gradle.userdev' version '7.0.145' }\n",
            encoding="utf-8",
        )
        init_script = self.tmp / "mirror.gradle"
        init_script.write_text("// mirror", encoding="utf-8")
        probe = {"urls": [], "unreachable": [], "hosts": [], "offline": False}
        with mock.patch.object(gradle, "preflight", return_value=dict(probe)):
            with mock.patch.object(gradle, "_ensure_gradle_distribution"):
                with mock.patch(
                    "dma.mirrors.prepare_launch_env", return_value=init_script
                ) as prepare:
                    with mock.patch.object(
                        gradle.subprocess, "Popen", return_value=_QuietProcess()
                    ) as popen:
                        code, _output = gradle.run_gradle(root, "build")
        argv = popen.call_args[0][0]
        self.assertEqual(code, 0)
        self.assertIn("--init-script", argv)
        self.assertIn(str(init_script), argv)
        self.assertNotIn("--offline", argv)
        prepare.assert_called_once()

    def test_windows_command_keeps_paths_with_spaces(self) -> None:
        """工程路径含空格时，cmd.exe 不能把 gradlew 路径截断。"""
        argv = [
            r"D:\Darkitten Launcher\DarkittenModAgent\.bootstrap\gradlew.bat",
            "build",
            "--console=plain",
            "--init-script",
            r"C:\Users\李涵\AppData\temp dir\mirror.gradle",
        ]
        command = windows_command(argv)
        if os.name != "nt":
            self.assertEqual(command, argv)
            return
        self.assertIsInstance(command, str)
        prefix = 'cmd.exe /d /s /c "'
        self.assertTrue(command.startswith(prefix), command)
        self.assertTrue(command.endswith('"'), command)
        # cmd /s 会剥掉最外层引号，剩下的必须与 list2cmdline 完全一致。
        inner = command[len(prefix):-1]
        self.assertEqual(inner, subprocess.list2cmdline(argv))
        self.assertIn('"D:\\Darkitten Launcher\\', inner)

    def test_mirror_failure_falls_back_to_offline(self) -> None:
        root = self._project()
        probe = {
            "urls": ["https://maven.neoforged.net/releases"],
            "unreachable": ["https://maven.neoforged.net/releases"],
            "hosts": ["maven.neoforged.net"],
            "offline": True,
        }
        with mock.patch.object(gradle, "preflight", return_value=dict(probe)):
            with mock.patch("dma.mirrors.prepare_launch_env", return_value=None):
                with mock.patch.object(
                    gradle.subprocess, "Popen", return_value=_QuietProcess()
                ) as popen:
                    code, _output = gradle.run_gradle(root, "build")
        argv = popen.call_args[0][0]
        self.assertEqual(code, 0)
        self.assertIn("--offline", argv)


if __name__ == "__main__":
    unittest.main()
