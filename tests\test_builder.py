from __future__ import annotations

import threading
import unittest
from pathlib import Path
from unittest import mock

from dma import builder, gradle, toolchain
from dma.gradle import Launch

from .base import TempDataCase


class FakeRunner:
    """替掉真实的 GradleRunner，只按脚本回放输出。"""

    calls: list[dict] = []
    script: list[tuple[int, str, list[str]]] = []

    def __init__(self, on_line, on_done) -> None:
        self.on_line = on_line
        self.on_done = on_done
        self.stopped = False

    def start(self, launch, env) -> None:
        FakeRunner.calls.append({"launch": launch, "env": env})
        code, message, lines = FakeRunner.script.pop(0) if FakeRunner.script else (0, "", [])
        for line in lines:
            self.on_line(line)
        self.on_done(code, message)

    def stop(self) -> None:
        self.stopped = True
        self.on_done(-1, "已结束")


class SlowRunner(FakeRunner):
    def start(self, launch, env) -> None:
        FakeRunner.calls.append({"launch": launch, "env": env})


def _prepared() -> toolchain.Prepared:
    return toolchain.Prepared(
        java_home=Path("C:/jdk21"),
        java_version="21.0.1",
        notes=[],
    )


class BuilderTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        FakeRunner.calls = []
        FakeRunner.script = []
        self.root = self.tmp / "project"
        self.root.mkdir()
        (self.root / "build.gradle").write_text("plugins { id 'java' }\n", encoding="utf-8")
        self.patch = mock.patch.multiple(
            "dma.builder",
            toolchain=mock.Mock(
                Prepared=toolchain.Prepared,
                prepare=mock.Mock(return_value=_prepared()),
            ),
            mirrors=mock.Mock(
                service=mock.Mock(
                    return_value=mock.Mock(
                        init_script=mock.Mock(return_value=Path("C:/mirror.gradle")),
                        base_url="http://127.0.0.1:55878",
                    )
                )
            ),
            gradle=mock.Mock(
                GRADLE_TASKS=gradle.GRADLE_TASKS,
                has_gradle_files=gradle.has_gradle_files,
                resolve_run_task=gradle.resolve_run_task,
                resolve_launch=gradle.resolve_launch,
                build_env=gradle.build_env,
                GradleRunner=FakeRunner,
                required_gradle=mock.Mock(return_value=""),
            ),
            deps=mock.Mock(
                is_missing_jar_error=mock.Mock(return_value=False),
                ensure_local_jars=mock.Mock(),
            ),
            project_mod=mock.Mock(
                repair_fences=mock.Mock(return_value=[]),
                repair_build_scripts=mock.Mock(return_value=[]),
            ),
            wrapper=mock.Mock(),
        )
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        super().tearDown()

    def test_build_success_collects_jars(self) -> None:
        libs = self.root / "build" / "libs"
        libs.mkdir(parents=True)
        (libs / "demo-1.0.0.jar").write_bytes(b"PK\x03\x04demo")
        FakeRunner.script = [(0, "", ["BUILD SUCCESSFUL in 6s"])]

        lines: list[str] = []
        result = builder.build_project(self.root, log=lines.append)

        self.assertTrue(result.ok)
        self.assertEqual(result.code, 0)
        self.assertEqual(
            result.jars, [("build/libs/demo-1.0.0.jar", len(b"PK\x03\x04demo"))]
        )
        self.assertIn("BUILD SUCCESSFUL", result.output)
        self.assertTrue(any("准备构建环境" in line for line in lines))
        self.assertTrue(any("镜像" in line for line in lines))
        self.assertEqual(len(FakeRunner.calls), 1)

    def test_build_failure_keeps_log(self) -> None:
        FakeRunner.script = [
            (1, "", ["error: cannot find symbol", "1 error"])
        ]
        result = builder.build_project(self.root, log=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, 1)
        self.assertIn("cannot find symbol", result.output)
        self.assertEqual(result.jars, [])

    def test_run_task_does_not_report_jars(self) -> None:
        libs = self.root / "build" / "libs"
        libs.mkdir(parents=True)
        (libs / "demo.jar").write_bytes(b"PK")
        FakeRunner.script = [(0, "", ["done"])]
        result = builder.build_project(self.root, "runDatagen")
        self.assertTrue(result.ok)
        self.assertEqual(result.jars, [])

    def test_timeout_returns_error_and_stops(self) -> None:
        self.patch.stop()
        self.patch = mock.patch.multiple(
            "dma.builder",
            toolchain=mock.Mock(
                Prepared=toolchain.Prepared,
                prepare=mock.Mock(return_value=_prepared()),
            ),
            mirrors=mock.Mock(
                service=mock.Mock(
                    return_value=mock.Mock(
                        init_script=mock.Mock(return_value=Path("C:/mirror.gradle")),
                        base_url="http://127.0.0.1:55878",
                    )
                )
            ),
            gradle=mock.Mock(
                GRADLE_TASKS=gradle.GRADLE_TASKS,
                has_gradle_files=gradle.has_gradle_files,
                resolve_run_task=gradle.resolve_run_task,
                resolve_launch=gradle.resolve_launch,
                build_env=gradle.build_env,
                GradleRunner=SlowRunner,
                required_gradle=mock.Mock(return_value=""),
            ),
            deps=mock.Mock(is_missing_jar_error=mock.Mock(return_value=False)),
            project_mod=mock.Mock(
                repair_fences=mock.Mock(return_value=[]),
                repair_build_scripts=mock.Mock(return_value=[]),
            ),
            wrapper=mock.Mock(),
        )
        self.patch.start()
        result = builder.build_project(self.root, timeout=0.05)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, -1)
        self.assertIn("超过", result.message)

    def test_missing_build_files_is_rejected(self) -> None:
        empty = self.tmp / "empty"
        empty.mkdir()
        with self.assertRaises(builder.BuilderError):
            builder.build_project(empty)

    def test_unknown_task_is_rejected(self) -> None:
        with self.assertRaises(builder.BuilderError):
            builder.build_project(self.root, "clean")

    def test_cancel_before_start_is_rejected(self) -> None:
        event = threading.Event()
        event.set()
        with self.assertRaises(builder.BuilderError):
            builder.build_project(self.root, cancel=event)

    def test_fixable_jar_error_retries_once(self) -> None:
        self.patch.stop()
        jars = mock.Mock(changed=True)
        self.patch = mock.patch.multiple(
            "dma.builder",
            toolchain=mock.Mock(
                Prepared=toolchain.Prepared,
                prepare=mock.Mock(return_value=_prepared()),
            ),
            mirrors=mock.Mock(
                service=mock.Mock(
                    return_value=mock.Mock(
                        init_script=mock.Mock(return_value=Path("C:/mirror.gradle")),
                        base_url="http://127.0.0.1:55878",
                    )
                )
            ),
            gradle=mock.Mock(
                GRADLE_TASKS=gradle.GRADLE_TASKS,
                has_gradle_files=gradle.has_gradle_files,
                resolve_run_task=gradle.resolve_run_task,
                resolve_launch=gradle.resolve_launch,
                build_env=gradle.build_env,
                GradleRunner=FakeRunner,
                required_gradle=mock.Mock(return_value=""),
            ),
            deps=mock.Mock(
                is_missing_jar_error=mock.Mock(return_value=True),
                ensure_local_jars=mock.Mock(return_value=jars),
            ),
            project_mod=mock.Mock(
                repair_fences=mock.Mock(return_value=[]),
                repair_build_scripts=mock.Mock(return_value=[]),
            ),
            wrapper=mock.Mock(),
        )
        self.patch.start()
        FakeRunner.script = [
            (1, "", ["could not find libs/foo.jar"]),
            (0, "", ["BUILD SUCCESSFUL"]),
        ]
        result = builder.build_project(self.root)
        self.assertTrue(result.ok)
        self.assertEqual(len(FakeRunner.calls), 2)


if __name__ == "__main__":
    unittest.main()
