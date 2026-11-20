from __future__ import annotations

import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from dma import mcdev
from dma.mcdev import cards, game, gameplay, gradle as mcdev_gradle, process as mcdev_process
from dma.mcdev import scenario, server_files
from dma.mcdev import templates as mcdev_templates

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


class ProxySelfHealTests(TempDataCase):
    """系统代理拦截 Gradle 仓库时自动切直连(systemProp.java.net.useSystemProxies=false)。"""

    def setUp(self) -> None:
        super().setUp()
        self._patchers: list = []
        self.stop_calls = 0

    def _setup(self, proxy: str | None, blocked: bool) -> Path:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "gradle.properties").write_text("org.gradle.jvmargs=-Xmx2G\n", encoding="utf-8")
        self._patchers.append(mock.patch.object(mcdev_gradle, "_detect_system_proxy", return_value=proxy))
        self._patchers.append(mock.patch.object(mcdev_gradle, "_proxy_blocks_gradle", return_value=blocked))
        self._patchers.append(mock.patch.object(mcdev_gradle, "stop_gradle_daemon", side_effect=self._count_stop))
        for p in self._patchers:
            p.start()
        return root

    def _count_stop(self, root) -> None:
        self.stop_calls += 1

    def tearDown(self) -> None:
        for p in getattr(self, "_patchers", []):
            p.stop()
        super().tearDown()

    def test_no_proxy_leaves_properties_alone(self) -> None:
        root = self._setup(proxy=None, blocked=False)
        mcdev_gradle._proxy_self_heal(root)
        text = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertNotIn("useSystemProxies", text)
        self.assertEqual(self.stop_calls, 0)

    def test_healthy_proxy_leaves_properties_alone(self) -> None:
        root = self._setup(proxy="127.0.0.1:7890", blocked=False)
        mcdev_gradle._proxy_self_heal(root)
        text = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertNotIn("useSystemProxies", text)
        self.assertEqual(self.stop_calls, 0)

    def test_broken_proxy_writes_direct_mode_once(self) -> None:
        root = self._setup(proxy="127.0.0.1:7890", blocked=True)
        logs: list[str] = []
        mcdev_gradle._proxy_self_heal(root, log=logs.append)
        text = (root / "gradle.properties").read_text(encoding="utf-8")
        self.assertIn("systemProp.java.net.useSystemProxies=false", text)
        self.assertTrue(any("useSystemProxies" in line for line in logs))
        self.assertEqual(self.stop_calls, 1)
        # 幂等：已存在该行时不再写、不再停 daemon
        mcdev_gradle._proxy_self_heal(root, log=logs.append)
        self.assertEqual(text.count("useSystemProxies"), 1)
        self.assertEqual(self.stop_calls, 1)

    def test_missing_gradle_properties_skips(self) -> None:
        root = self._setup(proxy="127.0.0.1:7890", blocked=True)
        (root / "gradle.properties").unlink()
        mcdev_gradle._proxy_self_heal(root)
        self.assertFalse((root / "gradle.properties").exists())


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


class LiveFrameTests(unittest.TestCase):
    """实时画面：/frame 直接返回 PNG 字节，视角与界面信息放在响应头里。"""

    def setUp(self) -> None:
        payload = b"\x89PNG\r\n\x1a\n" + b"frame-bytes"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.split("?")[0] != "/frame":
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("X-Mcdev-Width", "854")
                self.send_header("X-Mcdev-Height", "480")
                self.send_header("X-Mcdev-Gui-Width", "427")
                self.send_header("X-Mcdev-Gui-Height", "240")
                self.send_header("X-Mcdev-Yaw", "12.5")
                self.send_header("X-Mcdev-Pitch", "-30.25")
                self.send_header("X-Mcdev-In-World", "true")
                self.send_header(
                    "X-Mcdev-Screen", "net.minecraft.client.gui.screens.TitleScreen"
                )
                self.send_header("X-Mcdev-Language", "zh_cn")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):  # noqa: D401 - 测试里不打印访问日志
                return

        self.payload = payload
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_probe_bytes_keeps_raw_body_and_headers(self) -> None:
        body, headers = mcdev.probe.probe_bytes("client", "/frame", port=self.port)
        self.assertEqual(body, self.payload)
        self.assertEqual(headers["content-type"], "image/png")
        self.assertEqual(headers["x-mcdev-width"], "854")

    def test_frame_parses_metadata(self) -> None:
        item = game.frame(port=self.port)
        self.assertEqual(item.png, self.payload)
        self.assertEqual((item.width, item.height), (854, 480))
        self.assertEqual((item.gui_width, item.gui_height), (427, 240))
        self.assertAlmostEqual(item.yaw, 12.5)
        self.assertAlmostEqual(item.pitch, -30.25)
        self.assertTrue(item.in_world)
        self.assertTrue(item.screen.endswith("TitleScreen"))
        self.assertEqual(item.language, "zh_cn")

    def test_running_client_port_prefers_live_client(self) -> None:
        status = {
            "processes": [
                {"kind": "server", "running": True, "port": 39001},
                {"kind": "client", "running": True, "port": 39002},
            ]
        }
        self.assertEqual(game.running_client_port(status), 39002)
        self.assertIsNone(
            game.running_client_port(
                {"processes": [{"kind": "client", "running": False, "port": 39002}]}
            )
        )
        self.assertIsNone(game.running_client_port(None))


class ProbeCacheTests(TempDataCase):
    """探针源码改动后，旧缓存 jar 必须失效，否则新端点不会生效。"""

    def test_source_fingerprint_gates_cached_probe(self) -> None:
        cache = mcdev_templates._probe_cache()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(b"old-probe")
        # 没有指纹戳：旧缓存不可信。
        self.assertIsNone(mcdev_templates.find_probe_jar(None))
        fingerprint = mcdev_templates.probe_fingerprint()
        self.assertTrue(fingerprint)
        cache.with_name(cache.name + ".sha256").write_text(
            "stale-fingerprint\n", encoding="utf-8"
        )
        self.assertIsNone(mcdev_templates.find_probe_jar(None))
        cache.with_name(cache.name + ".sha256").write_text(
            fingerprint + "\n", encoding="utf-8"
        )
        self.assertEqual(mcdev_templates.find_probe_jar(None), cache)


class LaunchCancelTests(unittest.TestCase):
    """验证“停止”能把取消信号送到启动器，并且只清理本次启动的进程。"""

    def test_set_cancel_switches_event(self) -> None:
        manager = mcdev_process.LaunchManager()
        mine = threading.Event()
        previous = manager.set_cancel(mine)
        self.assertFalse(previous.is_set())
        self.assertFalse(manager._cancelled())
        mine.set()
        self.assertTrue(manager._cancelled())

    def test_stop_started_since_skips_older_entries(self) -> None:
        manager = mcdev_process.LaunchManager()
        old = mcdev_process.LaunchEntry(
            kind="server",
            project_dir=".",
            pid=1,
            port=39001,
            log_path="",
            started_at=100.0,
        )
        new = mcdev_process.LaunchEntry(
            kind="client",
            project_dir=".",
            pid=2,
            port=39002,
            log_path="",
            started_at=200.0,
        )
        manager._entries = {"server": old, "client": new}
        stopped: list[str] = []
        manager.stop = lambda kind: stopped.append(kind) or {  # type: ignore[method-assign]
            "kind": kind,
            "stopped": True,
        }
        manager.stop_started_since(150.0)
        self.assertEqual(stopped, ["client"])

    def test_service_bind_and_restore_cancel(self) -> None:
        service = mcdev.McdevService()
        mine = threading.Event()
        previous = service.set_cancel(mine)
        self.assertIs(service._cancel, mine)
        self.assertIs(service.launches._cancel, mine)
        service.restore_cancel(previous, mine)
        self.assertIsNot(service._cancel, mine)

    def test_restore_does_not_steal_new_binding(self) -> None:
        service = mcdev.McdevService()
        first = threading.Event()
        previous = service.set_cancel(first)
        second = threading.Event()
        service.set_cancel(second)
        service.restore_cancel(previous, first)
        self.assertIs(service._cancel, second)


class BackgroundModeTests(TempDataCase):
    """全后台运行所需的运行文件：创造模式、隐藏窗口、失焦不暂停。"""

    def test_server_properties_default_to_creative(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        server_files.ensure_server_properties(root, 25566)
        text = (root / "run" / "server.properties").read_text(encoding="utf-8")
        self.assertIn("gamemode=creative", text)
        self.assertIn("force-gamemode=true", text)
        self.assertIn("online-mode=false", text)

    def test_probe_options_hide_window_and_disable_pause(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        changed = server_files.ensure_client_background(root, background=True)
        self.assertTrue(changed)
        path = root / "run" / "config" / "mcdev-probe.properties"
        text = path.read_text(encoding="utf-8")
        self.assertIn("background=true", text)
        self.assertIn("pause-on-lost-focus=false", text)
        options = (root / "run" / "options.txt").read_text(encoding="utf-8")
        self.assertIn("pauseOnLostFocus:false", options)
        fml = (root / "run" / "config" / "fml.toml").read_text(encoding="utf-8")
        self.assertIn("earlyWindowControl = false", fml)
        # 幂等：内容一致时不再重复写。
        self.assertEqual(server_files.ensure_client_background(root, background=True), [])

    def test_background_false_really_shows_window(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        server_files.ensure_client_background(root, background=True)
        server_files.ensure_client_background(root, background=False)
        text = (root / "run" / "config" / "mcdev-probe.properties").read_text(
            encoding="utf-8"
        )
        self.assertIn("background=false", text)
        self.assertIn(
            "pauseOnLostFocus:true",
            (root / "run" / "options.txt").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "earlyWindowControl = true",
            (root / "run" / "config" / "fml.toml").read_text(encoding="utf-8"),
        )


class WindowHideTests(unittest.TestCase):
    def test_hide_loop_rehides_window_that_shows_again(self) -> None:
        entry = mcdev_process.LaunchEntry(
            kind="client",
            project_dir=".",
            pid=2,
            port=39002,
            log_path="",
            started_at=time.time(),
        )
        calls: list[tuple[int, bool]] = []

        def fake_show(hwnd: int, visible: bool) -> bool:
            calls.append((hwnd, visible))
            if len(calls) >= 3:
                entry.running = False
            return True

        with mock.patch.object(mcdev_process, "_glfw_windows", return_value=[4242]), \
                mock.patch.object(mcdev_process, "_show_window", side_effect=fake_show):
            mcdev_process._hide_new_windows(set(), entry)

        self.assertGreaterEqual(len(calls), 3)
        self.assertTrue(all(hwnd == 4242 and not visible for hwnd, visible in calls))


class ProbeInstallTests(TempDataCase):
    def test_install_probe_replaces_old_probe_jars(self) -> None:
        root = self.tmp / "mod"
        mods = root / "run" / "mods"
        mods.mkdir(parents=True)
        stale = mods / "mcdev_probe-0.1.0-dev.jar"
        stale.write_bytes(b"old")
        other = mods / "othermod-1.0-dev.jar"
        other.write_bytes(b"keep")
        jar = self.tmp / "mcdev-probe-dev.jar"
        jar.write_bytes(b"new")
        with mock.patch.object(
            mcdev_gradle,
            "_read_manifest",
            return_value="MixinConfigs: mcdev_probe.mixins.json",
        ):
            mcdev_gradle.install_probe(jar, [str(root / "run")])
        self.assertFalse(stale.exists())
        self.assertTrue(other.exists())
        self.assertEqual((mods / jar.name).read_bytes(), b"new")

    def test_stop_gradle_daemon_accepts_string_path(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        (root / "gradlew.bat").write_text("@echo off\n", encoding="utf-8")
        with mock.patch.object(mcdev_gradle.subprocess, "run") as run:
            mcdev_gradle.stop_gradle_daemon(str(root))
        self.assertTrue(run.called)


class BotControlTests(unittest.TestCase):
    """用本地假服务端探针验证假玩家接口与高层动作分派。"""

    def setUp(self) -> None:
        self.requests: list[tuple[str, dict]] = []

        class Handler(BaseHTTPRequestHandler):
            def _reply(self, payload: dict) -> None:
                body = json.dumps({"ok": True, "data": payload}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                test.requests.append((self.path, {}))
                self._reply({"path": self.path})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode("utf-8") if length else "{}"
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                test.requests.append((self.path, payload))
                self._reply({"path": self.path, "received": payload})

            def log_message(self, *args):  # noqa: D401
                return

        test = self
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_spawn_defaults_to_creative(self) -> None:
        mcdev.bot.spawn("Dev", x=1, y=64, z=2, port=self.port)
        path, payload = self.requests[-1]
        self.assertEqual(path, "/bot/spawn")
        self.assertEqual(payload["gamemode"], "creative")
        self.assertEqual(payload["x"], 1.0)

    def test_control_maps_walk_to_move(self) -> None:
        mcdev.bot.control(
            "walk", {"name": "Dev", "dx": 3, "dz": -1, "steps": 12}, port=self.port
        )
        path, payload = self.requests[-1]
        self.assertEqual(path, "/bot/move")
        self.assertEqual(payload["mode"], "walk")
        self.assertEqual(payload["steps"], 12)

    def test_state_and_gamemode_paths(self) -> None:
        mcdev.bot.state("Dev", radius=6, port=self.port)
        self.assertTrue(self.requests[-1][0].startswith("/bot/state?name=Dev"))
        mcdev.bot.gamemode("Dev", "survival", port=self.port)
        path, payload = self.requests[-1]
        self.assertEqual(path, "/bot/gamemode")
        self.assertEqual(payload["gamemode"], "survival")

    def test_window_visibility_payload(self) -> None:
        mcdev.game.set_window_visible(False, port=self.port)
        path, payload = self.requests[-1]
        self.assertEqual(path, "/window")
        self.assertFalse(payload["visible"])


class GameControlTests(unittest.TestCase):
    """用本地假探针验证语言/存档/观察/测试编排，不启动真实游戏。"""

    def setUp(self) -> None:
        state = {
            "language": "en_us",
            "in_world": False,
            "posts": [],
            "difficulty": "peaceful",
            "dayTime": 1000,
            "gameMode": "creative",
            "nearby": [],
        }

        class Handler(BaseHTTPRequestHandler):
            def _send(self, data):
                body = json.dumps({"ok": True, "data": data}, ensure_ascii=False).encode(
                    "utf-8"
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0]
                if path == "/status":
                    self._send(
                        {
                            "side": "client",
                            "inWorld": state["in_world"],
                            "screen": "net.minecraft.client.gui.screens.TitleScreen",
                            "title": "标题",
                            "language": state["language"],
                            "languageSelected": state["language"],
                            "player": "Dev" if state["in_world"] else None,
                        }
                    )
                elif path == "/language":
                    self._send(
                        {
                            "code": state["language"],
                            "selected": state["language"],
                            "javaLocale": "zh_CN",
                            "available": [],
                        }
                    )
                elif path == "/player/state":
                    self._send(
                        {
                            "inWorld": state["in_world"],
                            "name": "Dev",
                            "x": 0.5,
                            "y": 64.0,
                            "z": 0.5,
                            "health": 20.0,
                            "difficulty": state["difficulty"],
                            "dayTime": state["dayTime"],
                            "gameMode": state["gameMode"],
                            "nearby": state["nearby"],
                        }
                    )
                elif path == "/ui/snapshot":
                    self._send(
                        {
                            "screen": None,
                            "title": None,
                            "widgets": [
                                {"text": "单人游戏", "x": 100, "y": 60},
                                {"text": "选项...", "x": 100, "y": 90},
                            ],
                        }
                    )
                elif path == "/log/tail":
                    self._send({"lines": ["[Render thread] test", "Done"]})
                elif path == "/screenshot":
                    self._send(
                        {
                            "path": str(Path(__file__).with_name("missing-shot.png")),
                            "exists": False,
                            "label": "shot",
                            "language": state["language"],
                        }
                    )
                else:
                    self._send({"path": self.path})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                path = self.path.split("?")[0]
                state["posts"].append((path, payload))
                if path == "/language":
                    state["language"] = payload.get("code") or "zh_cn"
                    self._send(
                        {
                            "code": state["language"],
                            "selected": state["language"],
                            "reloading": True,
                        }
                    )
                elif path == "/world/create":
                    state["in_world"] = True
                    self._send(
                        {
                            "created": True,
                            "name": payload.get("name"),
                            "seed": 1,
                            "gamemode": payload.get("gamemode"),
                        }
                    )
                elif path == "/player/key":
                    self._send({"pressed": True, "key": payload.get("key")})
                elif path == "/command":
                    cmd = str(payload.get("cmd") or "").lstrip("/")
                    if cmd.startswith("difficulty "):
                        state["difficulty"] = cmd.split(" ", 1)[1].strip()
                    elif cmd.startswith("time set "):
                        state["dayTime"] = int(cmd.split(" ", 2)[2])
                    elif cmd.startswith("gamemode "):
                        state["gameMode"] = cmd.split(" ", 1)[1].strip()
                    elif cmd.startswith("summon "):
                        entity = cmd.split(" ", 2)[1]
                        state["nearby"].append(
                            {"id": len(state["nearby"]) + 1, "type": entity, "name": entity}
                        )
                    self._send({"sent": True, "cmd": cmd})
                else:
                    self._send({"echo": payload})

            def log_message(self, *args):
                return

        self.state = state
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_set_language_normalizes_and_verifies(self) -> None:
        result = game.set_language("中文", port=self.port, wait=2)
        self.assertTrue(result["verified"])
        self.assertEqual(self.state["language"], "zh_cn")

    def test_create_world_waits_until_in_world(self) -> None:
        result = game.create_world(
            "AI 测试", gamemode="creative", world_type="flat", port=self.port, wait=5
        )
        self.assertTrue(result["created"])
        self.assertTrue(result["inWorld"])
        self.assertTrue(self.state["in_world"])

    def test_observe_collects_state_ui_and_log(self) -> None:
        self.state["in_world"] = True
        result = game.observe(
            port=self.port, screenshot=True, label="check", log_lines=2
        )
        self.assertTrue(result["inWorld"])
        self.assertEqual(result["player"]["name"], "Dev")
        self.assertEqual(len(result["ui"]["widgets"]), 2)
        self.assertEqual(result["log"][-1], "Done")
        self.assertFalse(result["screenshot"]["review"]["ok"])

    def test_run_test_passes_assertions(self) -> None:
        result = game.run_test(
            [
                {"type": "language", "code": "zh_cn"},
                {"type": "world", "name": "测试", "worldType": "flat"},
                {"type": "control", "action": "state"},
                {"type": "assert", "path": "last.name", "op": "eq", "value": "Dev"},
                {"type": "control", "action": "click_text", "text": "单人游戏"},
                {"type": "assert", "path": "last.clicked", "op": "ne", "value": True},
            ],
            port=self.port,
        )
        # click_text 的假探针返回 echo，没有 clicked 字段；ne True 仍然成立。
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["executed"], 6)

    def test_run_test_stops_on_failed_assertion(self) -> None:
        result = game.run_test(
            [
                {"type": "control", "action": "state"},
                {"type": "assert", "path": "last.name", "op": "eq", "value": "Other"},
                {"type": "control", "action": "key", "key": "inventory"},
            ],
            port=self.port,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["executed"], 2)
        self.assertFalse(result["steps"][-1]["ok"])
        self.assertIn("断言失败", result["steps"][-1]["error"])

    def test_set_difficulty_verifies_change(self) -> None:
        self.state["in_world"] = True
        result = game.set_difficulty("normal", port=self.port, wait=2)
        self.assertTrue(result["verified"])
        self.assertEqual(self.state["difficulty"], "normal")

    def test_set_time_and_gamemode(self) -> None:
        self.state["in_world"] = True
        time_result = game.set_time("night", port=self.port, wait=2)
        self.assertTrue(time_result["verified"])
        self.assertEqual(self.state["dayTime"], 13000)
        mode_result = game.set_gamemode("survival", port=self.port, wait=2)
        self.assertTrue(mode_result["verified"])
        self.assertEqual(self.state["gameMode"], "survival")

    def test_spawn_raises_peaceful_to_normal(self) -> None:
        self.state["in_world"] = True
        result = game.spawn(
            "minecraft:zombie", count=2, distance=4, port=self.port
        )
        self.assertEqual(result["spawned"], 2)
        self.assertTrue(result["verified"])
        self.assertEqual(result["difficulty"], "normal")
        self.assertEqual(result["difficultyChanged"], "normal")
        self.assertEqual(len(self.state["nearby"]), 2)

    def test_run_test_stops_when_cancelled(self) -> None:
        event = threading.Event()
        event.set()
        result = game.run_test(
            [{"type": "control", "action": "state"}],
            port=self.port,
            cancel=event,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["steps"]), 1)
        self.assertIn("已取消", result["steps"][0]["error"])

    def test_run_test_supports_difficulty_time_and_spawn(self) -> None:
        self.state["in_world"] = True
        result = game.run_test(
            [
                {"type": "difficulty", "difficulty": "normal"},
                {"type": "time", "time": "night"},
                {"type": "gamemode", "gamemode": "survival"},
                {"type": "spawn", "mob": "minecraft:zombie", "count": 1},
                {"type": "assert", "path": "last.verified", "op": "eq", "value": True},
            ],
            port=self.port,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["executed"], 5)

    def test_ensure_ready_reports_missing_probe(self) -> None:
        with self.assertRaises(game.GameError):
            game.ensure_ready(port=1)


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
        self.assertTrue(mcdev.templates.supports_scaffold("Fabric", "1.21.1"))
        self.assertTrue(mcdev.templates.supports_scaffold("NeoForge", "1.21.1"))
        self.assertFalse(mcdev.templates.supports_scaffold("Fabric", "1.19.2"))
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


class StaleLaunchTests(TempDataCase):
    """源码改过之后不能复用旧开发端，否则验证的是上一版 class。"""

    def _project(self) -> tuple[Path, Path, Path]:
        root = self.tmp / "mod"
        source = root / "src/main/java/a/A.java"
        source.parent.mkdir(parents=True)
        source.write_text("class A {}", encoding="utf-8")
        built = root / "build/classes/java/main/a/A.class"
        built.parent.mkdir(parents=True)
        built.write_bytes(b"x")
        return root, source, built

    def test_needs_rebuild_when_source_newer(self) -> None:
        root, source, built = self._project()
        now = time.time()
        os.utime(built, (now - 100, now - 100))
        os.utime(source, (now, now))
        self.assertTrue(mcdev_process.project_needs_rebuild(root))

    def test_fresh_build_is_reused(self) -> None:
        root, source, built = self._project()
        now = time.time()
        os.utime(source, (now - 100, now - 100))
        os.utime(built, (now, now))
        self.assertFalse(mcdev_process.project_needs_rebuild(root))

    def test_missing_classes_force_rebuild(self) -> None:
        root = self.tmp / "mod2"
        source = root / "src/main/java/A.java"
        source.parent.mkdir(parents=True)
        source.write_text("class A {}", encoding="utf-8")
        self.assertTrue(mcdev_process.project_needs_rebuild(root))

    def test_stale_tracked_server_is_stopped_before_launch(self) -> None:
        root, _, _ = self._project()
        manager = mcdev_process.LaunchManager()
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        manager._entries["server"] = mcdev_process.LaunchEntry(
            kind="server",
            project_dir=str(root),
            pid=1,
            port=39001,
            log_path=str(root / "log.txt"),
            started_at=0.0,
            process=fake_process,
        )
        with (
            mock.patch.object(
                mcdev_process, "project_needs_rebuild", return_value=True
            ),
            mock.patch.object(
                mcdev_process.probe_mod, "probe_status_once", return_value=False
            ),
            mock.patch.object(
                manager, "stop", side_effect=RuntimeError("stop called")
            ) as stop_mock,
        ):
            with self.assertRaises(RuntimeError):
                manager.launch("server", root, 5000)
        stop_mock.assert_called_once_with("server")

    def test_fresh_tracked_server_is_reused(self) -> None:
        root, _, _ = self._project()
        manager = mcdev_process.LaunchManager()
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        manager._entries["server"] = mcdev_process.LaunchEntry(
            kind="server",
            project_dir=str(root),
            pid=1,
            port=39001,
            log_path=str(root / "log.txt"),
            started_at=0.0,
            process=fake_process,
        )
        with (
            mock.patch.object(
                mcdev_process, "project_needs_rebuild", return_value=False
            ),
            mock.patch.object(
                mcdev_process.probe_mod, "probe_status_once", return_value=False
            ),
        ):
            result = manager.launch("server", root, 5000)
        self.assertTrue(result["alreadyRunning"])
        self.assertFalse(result["stale"])


class ClientCrashTests(unittest.TestCase):
    """客户端启动即崩溃时不要连着重试三次，直接交日志。"""

    def _crashed(self) -> mcdev_process.LaunchEntry:
        return mcdev_process.LaunchEntry(
            kind="client", project_dir="", pid=1, port=39002,
            log_path="", started_at=0.0, running=False, exit_code=1,
            tail="java.lang.IllegalArgumentException: No model for layer x#main",
        )

    def test_crashed_client_stops_retrying(self) -> None:
        manager = mcdev_process.LaunchManager()
        manager._entries["client"] = self._crashed()
        with (
            mock.patch.object(manager, "wait_client_ready", return_value=False),
            mock.patch.object(manager, "connect_client"),
            mock.patch.object(manager, "wait_client_join", return_value=(False, "")),
            mock.patch.object(manager, "stop") as stop_mock,
        ):
            result = manager._join_client(Path("."), {"pid": 1}, 5000, 25566)
        self.assertTrue(result.get("crashed"))
        self.assertEqual(result.get("exitCode"), 1)
        self.assertEqual(len(result.get("attempts") or []), 1)
        stop_mock.assert_not_called()

    def test_wait_client_ready_returns_when_process_died(self) -> None:
        manager = mcdev_process.LaunchManager()
        manager._entries["client"] = self._crashed()
        started = time.time()
        self.assertFalse(manager.wait_client_ready(30_000))
        self.assertLess(time.time() - started, 5.0)


class BackgroundFocusTests(unittest.TestCase):
    """客户端必须在后台跑：窗口抢走前台后要还回去，不能接管鼠标。"""

    def test_restore_focus_skips_without_previous(self) -> None:
        with (
            mock.patch.object(mcdev_process, "_glfw_windows", return_value=[11]),
            mock.patch.object(mcdev_process, "_force_foreground") as force_mock,
        ):
            mcdev_process._restore_focus(0, set(), timeout=0.2, poll=0.01, settle=0.01)
        force_mock.assert_not_called()

    def test_restore_focus_ignores_windows_that_pre_existed(self) -> None:
        with (
            mock.patch.object(mcdev_process, "_glfw_windows", return_value=[11]),
            mock.patch.object(mcdev_process, "foreground_window", return_value=11),
            mock.patch.object(mcdev_process, "_force_foreground") as force_mock,
        ):
            mcdev_process._restore_focus(
                99, {11}, timeout=0.2, poll=0.01, settle=0.01
            )
        force_mock.assert_not_called()

    def test_restore_focus_returns_front_when_game_grabs_it(self) -> None:
        with (
            mock.patch.object(mcdev_process, "_glfw_windows", return_value=[11, 12]),
            mock.patch.object(mcdev_process, "foreground_window", return_value=12),
            mock.patch.object(mcdev_process, "_force_foreground", return_value=True) as force_mock,
        ):
            mcdev_process._restore_focus(
                99, {11}, timeout=2.0, poll=0.01, settle=0.01
            )
        force_mock.assert_called_once_with(99)

    def test_restore_focus_does_not_steal_an_unrelated_window(self) -> None:
        with (
            mock.patch.object(mcdev_process, "_glfw_windows", return_value=[12]),
            mock.patch.object(mcdev_process, "foreground_window", return_value=77),
            mock.patch.object(mcdev_process, "_force_foreground") as force_mock,
        ):
            mcdev_process._restore_focus(
                99, set(), timeout=2.0, poll=0.01, settle=0.01
            )
        force_mock.assert_not_called()


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

    def test_stalled_launch_stops_waiting(self) -> None:
        """启动日志长时间无新输出时不能再傻等到超时。"""
        manager = mcdev_process.LaunchManager()
        manager._sleep = lambda _s: None
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        entry = mcdev_process.LaunchEntry(
            kind="server",
            project_dir=str(self.tmp),
            pid=1,
            port=0,
            log_path=str(self.tmp / "log.txt"),
            started_at=0.0,
            tail="To honour the JVM settings...\n",
        )
        with mock.patch.object(mcdev_process, "_STALL_SECONDS", 0.05):
            ready = manager._wait_for_started("server", 60_000, fake_process, entry)
        self.assertFalse(ready)

    def test_wait_for_probe_accepts_started_log(self) -> None:
        """探针没响应但日志已经 Done 时，不能把正常服务端判成失败再杀掉。"""
        manager = mcdev_process.LaunchManager()
        manager._sleep = lambda _s: None
        fake_process = mock.Mock()
        fake_process.poll.return_value = None
        entry = mcdev_process.LaunchEntry(
            kind="server",
            project_dir=str(self.tmp),
            pid=1,
            port=39001,
            log_path=str(self.tmp / "log.txt"),
            started_at=0.0,
            tail='Done (5.130s)! For help, type "help"\n',
        )
        with mock.patch.object(
            mcdev_process.probe_mod, "probe_status_once", return_value=False
        ):
            ready = manager._wait_for_probe("server", 39001, 60_000, fake_process, entry)
        self.assertTrue(ready)

    def test_ensure_probe_installs_into_run_mods(self) -> None:
        """探针必须装进 <run>/mods，不能是 <run>/mods/mods。"""
        root = self.tmp / "mod"
        root.mkdir()
        (root / "build.gradle").write_text(
            "runs {\n    server { workingDirectory project.file('run-server') }\n}\n",
            encoding="utf-8",
        )
        jar = self.tmp / "mcdev-probe-dev.jar"
        jar.write_bytes(b"x")
        seen: dict = {}

        def fake_install(probe_jar, dirs):
            seen["dirs"] = list(dirs)
            return {"targets": []}

        service = mcdev.McdevService()
        with (
            mock.patch.object(mcdev.templates, "supports_probe", return_value=True),
            mock.patch.object(mcdev.templates, "find_probe_jar", return_value=jar),
            mock.patch.object(
                mcdev.gradle, "install_probe", side_effect=fake_install
            ),
        ):
            service.ensure_probe(root)
        self.assertEqual(seen["dirs"], [str(root / "run-server")])

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

    def test_ensure_client_language_replaces_lang_only(self) -> None:
        root = self.tmp / "mod"
        (root / "run").mkdir(parents=True)
        options = root / "run" / "options.txt"
        options.write_text("fov:0.5\nlang:en_us\nrenderDistance:12\n", encoding="utf-8")
        changed = server_files.ensure_client_language(root, "zh_cn")
        self.assertTrue(changed)
        text = options.read_text(encoding="utf-8")
        self.assertIn("fov:0.5", text)
        self.assertIn("renderDistance:12", text)
        self.assertIn("lang:zh_cn", text)
        self.assertNotIn("lang:en_us", text)
        # 幂等：第二次调用不再改动
        self.assertEqual(server_files.ensure_client_language(root, "zh_cn"), [])

    def test_ensure_client_language_creates_missing_file(self) -> None:
        root = self.tmp / "mod"
        root.mkdir()
        server_files.ensure_client_language(root, "zh_cn")
        text = (root / "run" / "options.txt").read_text(encoding="utf-8")
        self.assertIn("lang:zh_cn", text)

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


class GameplayTests(unittest.TestCase):
    def test_playtest_reports_when_world_is_not_ready(self) -> None:
        with mock.patch.object(
            gameplay, "_state", return_value={"inWorld": False, "screen": "TitleScreen"}
        ):
            result = gameplay.playtest()
        self.assertFalse(result["ok"])
        self.assertIn("in_world", result["failed"])
        self.assertEqual("TitleScreen", result["steps"][0]["screen"])

    def test_recorder_only_passes_when_every_step_passes(self) -> None:
        recorder = gameplay._Recorder()
        recorder.add("a", True)
        recorder.add("b", False)
        summary = recorder.summary()
        self.assertFalse(summary["ok"])
        self.assertEqual(["b"], summary["failed"])
        self.assertEqual(1, summary["passed"])

    def test_goto_stops_when_inside_radius(self) -> None:
        states = [
            {"inWorld": True, "x": 0, "z": 0},
            {"inWorld": True, "x": 3, "z": 0},
            {"inWorld": True, "x": 5.2, "z": 0},
        ]
        calls: list[tuple] = []

        def fake_state(_port=None):
            return states.pop(0) if states else {"inWorld": True, "x": 5.2, "z": 0}

        def fake_call(side, path, *, method="GET", payload=None, port=None, timeout=15.0):
            calls.append((path, payload))
            return {"ok": True, "data": {}}

        with mock.patch.object(gameplay, "_state", fake_state), \
             mock.patch.object(gameplay, "_call", side_effect=fake_call), \
             mock.patch.object(gameplay.time, "sleep", return_value=None):
            result = gameplay.goto(5, 0, timeout=10)
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["distance"], 0.9)
        self.assertTrue(any(path == "/player/look" for path, _ in calls))
        self.assertTrue(any(path == "/player/move" for path, _ in calls))

    def test_goto_reports_timeout(self) -> None:
        def fake_call(side, path, *, method="GET", payload=None, port=None, timeout=15.0):
            return {"ok": True, "data": {}}

        clock = iter([0.0, 0.0, 20.0, 40.0, 60.0])
        with mock.patch.object(
            gameplay, "_state", lambda _p=None: {"inWorld": True, "x": 0, "z": 0}
        ), mock.patch.object(gameplay, "_call", side_effect=fake_call), \
             mock.patch.object(gameplay.time, "sleep", return_value=None), \
             mock.patch.object(gameplay.time, "time", lambda: next(clock, 99.0)):
            result = gameplay.goto(100, 0, timeout=5)
        self.assertFalse(result["ok"])
        self.assertEqual("timeout", result["reason"])


if __name__ == "__main__":
    unittest.main()
