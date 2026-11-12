from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from dma import mcdev
from dma.mcdev import cards, gradle as mcdev_gradle, process as mcdev_process
from dma.mcdev import scenario, server_files

from .base import TempDataCase


class ToolchainTests(TempDataCase):
    def test_required_java_major_reads_build_scripts(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "java.toolchain.languageVersion = JavaLanguageVersion.of(21)\n",
            encoding="utf-8",
        )
        self.assertEqual(mcdev_gradle.required_java_major(root), 21)

    def test_required_java_major_supports_jvm_toolchain(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle.kts").write_text(
            "kotlin { jvmToolchain(17) }\n", encoding="utf-8"
        )
        self.assertEqual(mcdev_gradle.required_java_major(root), 17)

    def test_check_toolchain_fails_fast_when_jdk_missing(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "java.toolchain.languageVersion = JavaLanguageVersion.of(99)\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            mcdev_gradle, "installed_java_majors", return_value={17: Path("x")}
        ):
            with self.assertRaises(mcdev_gradle.GradleError):
                mcdev_gradle.check_toolchain(root)

    def test_toolchain_args_disable_auto_download(self) -> None:
        args = mcdev_gradle.toolchain_args([r"D:\Java\jdk-21"])
        self.assertIn("-Porg.gradle.java.installations.auto-download=false", args)
        self.assertIn(
            r"-Porg.gradle.java.installations.paths=D:\Java\jdk-21", args
        )


class ScenarioAssertTests(unittest.TestCase):
    def test_path_lookup(self) -> None:
        value = {"data": {"items": [{"id": "a"}], "ok": True}}
        self.assertEqual(scenario.get_path(value, "data.ok"), True)
        self.assertEqual(scenario.get_path(value, "data.items.0.id"), "a")
        self.assertIsNone(scenario.get_path(value, "data.missing"))

    def test_ops(self) -> None:
        check = scenario.check_assert
        self.assertTrue(check("eq", 1, 1))
        self.assertTrue(check("ne", 1, 2))
        self.assertTrue(check("contains", ["a", "b"], "b"))
        self.assertTrue(check("contains", "stardust_tools:stardust", "stardust"))
        self.assertTrue(check("gt", 3, 2))
        self.assertTrue(check("gte", 2, 2))
        self.assertTrue(check("lt", 1, 2))
        self.assertTrue(check("lte", 2, 2))
        self.assertTrue(check("exists", "x", None))
        self.assertFalse(check("exists", None, None))
        self.assertTrue(check("eq", "1", 1))
        self.assertFalse(check("eq", "1", 2))


class CardTests(unittest.TestCase):
    def test_parse_simple_card(self) -> None:
        text = (
            "name: smoke\n"
            "setup:\n"
            "  commands:\n"
            "    - time set day\n"
            "actions:\n"
            "  - command: weather clear\n"
        )
        card = cards.parse_yaml(text)
        steps = cards.card_to_steps(card)["steps"]
        self.assertEqual(steps[0]["type"], "command")
        self.assertEqual(steps[0]["cmd"], "time set day")
        self.assertEqual(steps[1]["cmd"], "weather clear")

    def test_inline_comment_is_stripped(self) -> None:
        text = (
            "setup:\n"
            "  commands:\n"
            "    - time set day # 设为白天\n"
            "actions:\n"
            "  - command: setblock 0 64 0 stone # 放方块\n"
        )
        steps = cards.card_to_steps(cards.parse_yaml(text))["steps"]
        self.assertEqual([item["cmd"] for item in steps], ["time set day", "setblock 0 64 0 stone"])
        # 不带空格的井号属于值本身，不能被当成注释切掉。
        self.assertEqual(cards.parse_yaml("a: b#c"), {"a": "b#c"})

    def test_card_to_steps_rejects_non_mapping(self) -> None:
        result = cards.card_to_steps(None)
        self.assertEqual(result["steps"], [])
        self.assertTrue(result["warnings"])

    def test_unsupported_yaml_is_reported(self) -> None:
        with self.assertRaises(ValueError):
            cards.parse_yaml("name: |\n  a\n  b\n")
        with self.assertRaises(ValueError):
            cards.parse_yaml("a: &x 1\nb: *x\n")

    def test_dry_run_requires_steps(self) -> None:
        service = mcdev.McdevService()
        with self.assertRaises(ValueError):
            service.scenario(steps=[], dry_run=True)


class ProbeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = json.dumps(
                    {"ok": True, "data": {"side": "server", "path": self.path}}
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):  # noqa: D401 - 测试里不打印访问日志
                return

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_probe_json_roundtrip(self) -> None:
        result = mcdev.probe.probe_json("server", "/status", port=self.port)
        self.assertTrue(result["ok"])
        self.assertEqual(result["data"]["side"], "server")
        self.assertEqual(result["data"]["path"], "/status")

    def test_probe_reports_connection_error(self) -> None:
        with self.assertRaises(mcdev.ProbeError):
            mcdev.probe.probe_json("client", "/status", port=1, timeout=0.5)


class TemplateTests(TempDataCase):
    def test_create_forge_project(self) -> None:
        root = self.tmp / "demo_mod"
        mcdev.templates.create_forge_project(
            root, mod_id="demo_mod", mod_name="Demo Mod"
        )
        self.assertTrue((root / "build.gradle").is_file())
        self.assertTrue((root / "gradle.properties").is_file())
        self.assertTrue((root / "gradlew.bat").is_file())
        build = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("net.minecraftforge.gradle", build)
        self.assertIn("filteringCharset = 'UTF-8'", build)
        props = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("demo_mod", props)
        self.assertIn("1.20.1", props)
        sources = list((root / "src" / "main" / "java").rglob("*.java"))
        self.assertTrue(any(item.name == "ModMain.java" for item in sources))
        sample = root / "scenarios" / "smoke.yml"
        self.assertTrue(sample.is_file())
        parsed = mcdev.cards.parse_yaml(sample.read_text(encoding="utf-8"))
        translated = mcdev.cards.card_to_steps(parsed)
        self.assertEqual(translated["warnings"], [])
        kinds = [step["type"] for step in translated["steps"]]
        self.assertIn("command", kinds)
        self.assertIn("block", kinds)
        self.assertIn("assert", kinds)

    def test_create_fabric_project(self) -> None:
        root = self.tmp / "fabric_mod"
        mcdev.templates.create_fabric_project(
            root,
            mod_id="demo_mod",
            mod_name="Demo Fabric",
            package="com.example.demo",
        )
        build = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("fabric-loom", build)
        props = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("yarn_mappings=1.20.1", props)
        meta = json.loads(
            (root / "src" / "main" / "resources" / "fabric.mod.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(meta["id"], "demo_mod")
        self.assertEqual(meta["entrypoints"]["main"], ["com.example.demo.DemoModMod"])
        entry = (
            root / "src" / "main" / "java" / "com" / "example" / "demo"
            / "DemoModMod.java"
        )
        self.assertTrue(entry.is_file())
        self.assertFalse(
            (root / "src" / "main" / "java" / "com" / "example" / "demo" / "ModMain.java").exists()
        )
        source = entry.read_text(encoding="utf-8")
        self.assertIn("class DemoModMod", source)
        self.assertIn("implements ModInitializer", source)
        self.assertNotIn("net.minecraftforge", source)

    def test_create_neoforge_project(self) -> None:
        root = self.tmp / "neo_mod"
        mcdev.templates.create_neoforge_project(
            root,
            mod_id="demo_mod",
            mod_name="Demo Neo",
            package="com.example.demo",
        )
        build = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("net.neoforged.moddev.legacyforge", build)
        self.assertIn('neoForgeVersion = "${minecraft_version}-${neo_version}"', build)
        self.assertIn("filteringCharset = 'UTF-8'", build)
        props = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("neo_version=47.1.106", props)
        toml = (
            root / "src" / "main" / "resources" / "META-INF" / "mods.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('modId="forge"', toml)
        self.assertIn("mandatory=true", toml)
        entry = (
            root / "src" / "main" / "java" / "com" / "example" / "demo"
            / "DemoModMod.java"
        )
        self.assertTrue(entry.is_file())
        self.assertFalse(
            (root / "src" / "main" / "java" / "com" / "example" / "demo" / "ModMain.java").exists()
        )
        source = entry.read_text(encoding="utf-8")
        self.assertIn("class DemoModMod", source)
        self.assertIn("IEventBus", source)
        self.assertIn("MinecraftForge.EVENT_BUS", source)
        self.assertNotIn("net.neoforged.neoforge", source)

    def test_restore_incompatible_neoforge_scaffold(self) -> None:
        root = self.tmp / "neo_bad"
        mcdev.templates.create_neoforge_project(
            root,
            mod_id="demo_mod",
            mod_name="Demo",
            package="com.example.demo",
        )
        (root / "build.gradle").write_text(
            "plugins {\n"
            "    id 'net.neoforged.gradle.userdev' version '7.0.145'\n"
            "}\n"
            "dependencies {\n"
            "    implementation 'net.neoforged:neoforge:47.1.106'\n"
            "}\n",
            encoding="utf-8",
        )
        (root / ".dma").mkdir(exist_ok=True)
        (root / ".dma" / "project.json").write_text(
            json.dumps(
                {
                    "loader": "NeoForge",
                    "mc_version": "1.20.1",
                    "mod_id": "demo_mod",
                    "name": "Demo",
                }
            ),
            encoding="utf-8",
        )
        entry = (
            root / "src" / "main" / "java" / "com" / "example" / "demo"
            / "DemoModMod.java"
        )
        old = entry.with_name("ModMain.java")
        old.write_text(entry.read_text(encoding="utf-8"), encoding="utf-8")

        notice = mcdev.templates.restore_incompatible_scaffold(root)

        self.assertIn("NeoGradle 7", notice)
        build = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertIn("net.neoforged.moddev.legacyforge", build)
        self.assertNotIn("userdev", build)
        self.assertTrue(entry.is_file())
        self.assertFalse(old.exists())
        backups = list((root / ".dma" / "backups").glob("scaffold-restore-*"))
        self.assertTrue(backups)
        self.assertIn(
            "net.neoforged.gradle.userdev",
            (backups[0] / "build.gradle").read_text(encoding="utf-8"),
        )

    def test_restore_keeps_valid_scaffold(self) -> None:
        root = self.tmp / "neo_good"
        mcdev.templates.create_neoforge_project(
            root, mod_id="demo_mod", mod_name="Demo"
        )
        (root / ".dma").mkdir(exist_ok=True)
        (root / ".dma" / "project.json").write_text(
            json.dumps({"loader": "NeoForge", "mc_version": "1.20.1"}),
            encoding="utf-8",
        )
        before = (root / "build.gradle").read_text(encoding="utf-8")
        self.assertEqual(mcdev.templates.restore_incompatible_scaffold(root), "")
        self.assertEqual(
            (root / "build.gradle").read_text(encoding="utf-8"), before
        )

    def test_create_scaffold_dispatch(self) -> None:
        for loader in ("Forge", "Fabric", "NeoForge"):
            root = self.tmp / loader.lower()
            mcdev.templates.create_scaffold(
                loader,
                root,
                mod_id="demo_mod",
                mod_name=f"Demo {loader}",
                package="com.example.demo",
            )
            self.assertTrue((root / "build.gradle").is_file(), loader)
            self.assertTrue((root / "gradlew.bat").is_file(), loader)

    def test_create_scaffold_rejects_unsupported(self) -> None:
        root = self.tmp / "arch"
        with self.assertRaises(ValueError):
            mcdev.templates.create_scaffold(
                "Architectury", root, mod_id="demo_mod", mod_name="Demo"
            )
        with self.assertRaises(ValueError):
            mcdev.templates.create_scaffold(
                "Forge",
                root,
                mod_id="demo_mod",
                mod_name="Demo",
                mc_version="1.21.1",
            )

    def test_supports_probe(self) -> None:
        forge = self.tmp / "forge"
        (forge / ".dma").mkdir(parents=True)
        (forge / ".dma" / "project.json").write_text(
            json.dumps({"loader": "Forge", "mc_version": "1.20.1"}), encoding="utf-8"
        )
        self.assertTrue(mcdev.templates.supports_probe(forge))

        fabric = self.tmp / "fabric"
        (fabric / ".dma").mkdir(parents=True)
        (fabric / ".dma" / "project.json").write_text(
            json.dumps({"loader": "Fabric", "mc_version": "1.20.1"}),
            encoding="utf-8",
        )
        self.assertFalse(mcdev.templates.supports_probe(fabric))

        neo = self.tmp / "neo"
        (neo / ".dma").mkdir(parents=True)
        (neo / ".dma" / "project.json").write_text(
            json.dumps({"loader": "NeoForge", "mc_version": "1.20.1"}),
            encoding="utf-8",
        )
        self.assertFalse(mcdev.templates.supports_probe(neo))

    def test_supports_scaffold(self) -> None:
        self.assertTrue(mcdev.templates.supports_scaffold("Fabric", "1.20.1"))
        self.assertFalse(mcdev.templates.supports_scaffold("Fabric", "1.21.1"))
        self.assertFalse(mcdev.templates.supports_scaffold("Architectury", "1.20.1"))

    def test_gametest_task_per_loader(self) -> None:
        self.assertEqual(mcdev.gradle.gametest_task("Fabric"), "runGametest")
        self.assertEqual(mcdev.gradle.gametest_task("fabric"), "runGametest")
        self.assertEqual(mcdev.gradle.gametest_task("Forge"), "runGameTestServer")
        self.assertEqual(mcdev.gradle.gametest_task("NeoForge"), "runGameTestServer")
        self.assertEqual(mcdev.gradle.gametest_task(""), "runGameTestServer")

    def test_run_dirs_reads_build_gradle(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "runs {\n    server { workingDirectory project.file('run-server') }\n}\n",
            encoding="utf-8",
        )
        self.assertEqual(mcdev.run_dirs(root), [root / "run-server"])

    def test_launch_status_empty(self) -> None:
        service = mcdev.McdevService()
        status = service.launch_status()
        self.assertEqual(status.get("processes"), [])


class NoProbeLaunchTests(TempDataCase):
    def test_run_dir_prefers_run(self) -> None:
        root = self.tmp / "mod"
        (root / "run").mkdir(parents=True)
        (root / "run-server").mkdir()
        self.assertEqual(server_files.run_dir_for(root), root / "run")

    def test_run_dir_defaults_to_run_before_first_launch(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        self.assertEqual(server_files.run_dir_for(root), root / "run")
        self.assertEqual(server_files.run_dir_for(root, "client"), root / "run")

    def test_started_markers(self) -> None:
        self.assertTrue(mcdev_process._looks_started("server", "Done (3.586s)!"))
        self.assertTrue(
            mcdev_process._looks_started("server", "For help, type \"help\"")
        )
        self.assertFalse(mcdev_process._looks_started("server", "BUILD FAILED"))
        self.assertTrue(
            mcdev_process._looks_started("client", "Backend library: LWJGL")
        )

    def test_server_port_reads_run_properties(self) -> None:
        root = self.tmp / "mod"
        (root / "run").mkdir(parents=True)
        (root / "run" / "server.properties").write_text(
            "server-port=25577\n", encoding="utf-8"
        )
        self.assertEqual(server_files.server_port(root), 25577)

    def test_parse_netstat_pid(self) -> None:
        text = (
            "  TCP    0.0.0.0:25565          0.0.0.0:0              "
            "LISTENING       4252\n"
            "  TCP    [::]:25565             [::]:0                 "
            "LISTENING       4252\n"
            "  TCP    127.0.0.1:25565        127.0.0.1:50000        "
            "ESTABLISHED     9999\n"
        )
        self.assertEqual(mcdev_process._parse_netstat_pid(text, 25565), 4252)
        self.assertIsNone(mcdev_process._parse_netstat_pid(text, 25566))

    def test_launch_does_not_duplicate_when_port_in_use(self) -> None:
        root = self.tmp / "mod"
        (root / "run").mkdir(parents=True)
        (root / "run" / "server.properties").write_text(
            "server-port=25566\n", encoding="utf-8"
        )
        manager = mcdev_process.LaunchManager()
        with mock.patch.object(
            mcdev_process.probe_mod, "probe_status_once", return_value=False
        ), mock.patch.object(mcdev_process, "_port_open", return_value=True):
            result = manager.launch("server", root, 5000)
        self.assertTrue(result["alreadyRunning"])
        self.assertEqual(result["gamePort"], 25566)

    def test_prepare_skips_probe_wait_when_unsupported(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        service = mcdev.McdevService()
        with mock.patch.object(
            mcdev.templates, "supports_probe", return_value=False
        ), mock.patch.object(
            mcdev_process.LaunchManager,
            "prepare",
            return_value={"ok": True, "results": []},
        ) as prepare:
            result = service.prepare(root, "server")
        self.assertFalse(prepare.call_args.kwargs["wait_probe"])
        self.assertFalse(result["probe"]["available"])

    def test_launch_uses_mirror_init_script(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        (root / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
        script = self.tmp / "mirror.gradle"
        script.write_text("// mirror\n", encoding="utf-8")
        captured: dict = {}

        class FakeProc:
            pid = 4321
            returncode = None
            stdout = None

            def poll(self):
                return None

            def wait(self):
                return 0

            def kill(self):
                pass

        def fake_popen(argv, **_kwargs):
            captured["argv"] = argv
            return FakeProc()

        manager = mcdev_process.LaunchManager()
        with mock.patch.object(
            mcdev_process.probe_mod, "probe_status_once", return_value=False
        ), mock.patch.object(
            mcdev_process, "_port_open", return_value=False
        ), mock.patch.object(
            mcdev_process.gradle_mod, "check_toolchain"
        ), mock.patch.object(
            mcdev_process.gradle_mod, "toolchain_args", return_value=[]
        ), mock.patch(
            "dma.mirrors.prepare_launch_env", return_value=script
        ) as prepare, mock.patch.object(
            mcdev_process.subprocess, "Popen", side_effect=fake_popen
        ), mock.patch.object(
            mcdev_process.LaunchManager, "_wait_for_started", return_value=True
        ):
            result = manager.launch("server", root, 5000, wait_probe=False)
        self.assertTrue(result["ready"])
        self.assertTrue(result["mirrored"])
        prepare.assert_called_once()
        argv = captured["argv"]
        rendered = argv if isinstance(argv, str) else " ".join(argv)
        self.assertIn("--init-script", rendered)
        self.assertIn(str(script), rendered)


if __name__ == "__main__":
    unittest.main()
