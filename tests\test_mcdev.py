from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from dma import mcdev
from dma.mcdev import cards, scenario

from .base import TempDataCase


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
        entry = root / "src" / "main" / "java" / "com" / "example" / "demo" / "ModMain.java"
        self.assertTrue(entry.is_file())
        source = entry.read_text(encoding="utf-8")
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
        self.assertIn("net.neoforged.gradle.userdev", build)
        props = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("neo_version=47.1.106", props)
        toml = (
            root / "src" / "main" / "resources" / "META-INF" / "mods.toml"
        ).read_text(encoding="utf-8")
        self.assertIn('modId="neoforge"', toml)
        entry = root / "src" / "main" / "java" / "com" / "example" / "demo" / "ModMain.java"
        source = entry.read_text(encoding="utf-8")
        self.assertIn("IEventBus", source)
        self.assertIn("NeoForge.EVENT_BUS", source)

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


if __name__ == "__main__":
    unittest.main()
