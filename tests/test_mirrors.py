from __future__ import annotations

import hashlib
import io
import json
import threading
import time
import unittest
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from dma import mirrors

from .base import TempDataCase


class _RepoHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        files = self.server.files  # type: ignore[attr-defined]
        data = files.get(self.path.lstrip("/"))
        if data is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        start = 0
        raw_range = self.headers.get("Range") or ""
        if raw_range.startswith("bytes="):
            start = int(raw_range[6:].split("-", 1)[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _SlowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        time.sleep(1.0)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


class _QuietServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        return


def _serve(handler, **attrs):
    server = _QuietServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    for key, value in attrs.items():
        setattr(server, key, value)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


class MirrorHelperTests(TempDataCase):
    def test_normalize(self) -> None:
        self.assertEqual(mirrors._normalize("/a/b/c?x=1"), "a/b/c")

    def test_cache_path_keeps_short_and_shortens_long_coordinates(self) -> None:
        short = "com/example/lib/1.0/lib-1.0.jar"
        self.assertEqual(mirrors.cache_path(short), mirrors.repo_cache() / short)
        long = "/".join(["very-long-group-name"] * 12) + "/lib/1.0/lib-1.0.pom"
        target = mirrors.cache_path(long)
        self.assertIn("_long", target.parts)
        self.assertEqual(target.suffix, ".bin")
        self.assertEqual(target, mirrors.cache_path(long))
        self.assertLessEqual(len(str(target)) + len(".part"), mirrors._MAX_CACHE_PATH)

    def test_cache_path_rejects_escape(self) -> None:
        # 绝对路径会被剥掉前导 / 后落到 repo_cache 内，不会写到数据目录外。
        self.assertEqual(mirrors.cache_path("/abs/x.jar"), mirrors.repo_cache() / "abs/x.jar")
        with self.assertRaises(ValueError):
            mirrors.cache_path("../../y")

    def test_needs_mirror(self) -> None:
        text = (
            "Could not GET https://maven.neoforged.net/releases/x\n"
            "java.net.ConnectException: Connection refused"
        )
        self.assertTrue(mirrors.needs_mirror(text))
        self.assertFalse(mirrors.needs_mirror("Could not find foo:bar:1.0."))
        self.assertFalse(mirrors.needs_mirror("Connection refused"))

    def test_module_synthesis(self) -> None:
        version = {
            "id": "1.21.1",
            "javaVersion": {"majorVersion": 21},
            "libraries": [
                {"name": "org.example:common:1.0"},
                {
                    "name": "org.example:winonly:2.0",
                    "rules": [{"action": "allow", "os": {"name": "windows"}}],
                },
                {"name": "org.example:native:3.0:natives-windows"},
            ],
        }
        module = mirrors.minecraft_dependencies_module(version)
        self.assertEqual(len(module["variants"]), 12)
        by_name = {item["name"]: item for item in module["variants"]}
        windows = {dep["module"] for dep in by_name["client-windows-api"]["dependencies"]}
        linux = {dep["module"] for dep in by_name["client-linux-api"]["dependencies"]}
        self.assertIn("winonly", windows)
        self.assertNotIn("winonly", linux)
        native = next(
            dep for dep in by_name["client-windows-api"]["dependencies"] if dep["module"] == "native"
        )
        self.assertEqual(
            native["thirdPartyCompatibility"]["artifactSelector"]["classifier"],
            "natives-windows",
        )
        self.assertEqual(module["component"]["version"], "1.21.1")
        self.assertEqual(
            by_name["client-windows-api"]["attributes"]["org.gradle.jvm.version"], 21
        )

    def test_module_synthesis_uses_minecraft_java_version(self) -> None:
        """1.20.1 的 toolchain 是 17；变体写死 21 会让 compileClasspath 变体不匹配。"""
        version = {
            "id": "1.20.1",
            "javaVersion": {"majorVersion": 17},
            "libraries": [{"name": "org.example:common:1.0"}],
        }
        module = mirrors.minecraft_dependencies_module(version)
        for variant in module["variants"]:
            self.assertEqual(
                variant["attributes"]["org.gradle.jvm.version"], 17, variant["name"]
            )

    def test_pom_runtime_only(self) -> None:
        pom = b"""<?xml version="1.0"?>
<project><dependencies>
  <dependency><groupId>a</groupId><artifactId>b</artifactId><version>1</version></dependency>
  <dependency><groupId>c</groupId><artifactId>d</artifactId><version>2</version>
    <scope>runtime</scope></dependency>
  <dependency><groupId>e</groupId><artifactId>f</artifactId><version>3</version>
    <scope>test</scope></dependency>
  <dependency><groupId>g</groupId><artifactId>h</artifactId><version>4</version>
    <classifier>all</classifier></dependency>
</dependencies></project>"""
        deps = mirrors._pom_dependencies(pom)
        self.assertEqual(deps, [("c", "d", "2", None)])

    def test_merge_jars(self) -> None:
        first = self.tmp / "a.jar"
        second = self.tmp / "b.jar"
        target = self.tmp / "out.jar"
        with zipfile.ZipFile(first, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", "Main-Class: A\n")
            archive.writestr("a.txt", "a")
            archive.writestr("META-INF/services/x", "one\n")
        with zipfile.ZipFile(second, "w") as archive:
            archive.writestr("b.txt", "b")
            archive.writestr("META-INF/services/x", "two\n")
            archive.writestr("META-INF/B.SF", "sig")
        mirrors.merge_jars([first, second], target)
        with zipfile.ZipFile(target) as archive:
            names = set(archive.namelist())
            self.assertIn("a.txt", names)
            self.assertIn("b.txt", names)
            self.assertNotIn("META-INF/B.SF", names)
            manifest = archive.read("META-INF/MANIFEST.MF").decode()
            self.assertIn("Main-Class: A", manifest)
            services = archive.read("META-INF/services/x").decode()
            self.assertIn("one", services)
            self.assertIn("two", services)

    def test_patch_all_jar(self) -> None:
        jar = self.tmp / "x-1.0-all.jar"
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("a.txt", "hello")
        document = {
            "variants": [
                {"name": "shadowRuntimeElements", "files": [{"name": "old.jar", "size": 1}]}
            ]
        }
        mirrors._patch_all_jar(document, jar)
        entry = document["variants"][0]["files"][0]
        data = jar.read_bytes()
        self.assertEqual(entry["name"], "x-1.0-all.jar")
        self.assertEqual(entry["size"], len(data))
        self.assertEqual(entry["sha1"], hashlib.sha1(data).hexdigest())


class MirrorDownloadTests(TempDataCase):
    def setUp(self) -> None:
        super().setUp()
        mirrors._health.forget()
        self.payload = bytes(range(256)) * 64
        self.server, self.base = _serve(_RepoHandler, files={"m/a.jar": self.payload})

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def test_download_from_full_and_missing(self) -> None:
        part = self.tmp / "a.part"
        self.assertIs(mirrors._download_from(self.base, "m/a.jar", part, 10, 2), True)
        self.assertEqual(part.read_bytes(), self.payload)
        self.assertIsNone(mirrors._download_from(self.base, "m/none.jar", self.tmp / "b.part", 10, 2))

    def test_download_from_resumes(self) -> None:
        part = self.tmp / "a.part"
        part.write_bytes(self.payload[:1000])
        self.assertIs(mirrors._download_from(self.base, "m/a.jar", part, 10, 2), True)
        self.assertEqual(part.read_bytes(), self.payload)

    def test_fetch_to_falls_through_sources(self) -> None:
        old_sources = mirrors.SOURCES
        old_central = mirrors.CENTRAL
        mirrors.SOURCES = (("missing", self.base + "/nope"), ("good", self.base))
        try:
            target = self.tmp / "out" / "a.jar"
            self.assertTrue(mirrors.fetch_to("m/a.jar", target, timeout=10, attempts=1))
            self.assertEqual(target.read_bytes(), self.payload)
        finally:
            mirrors.SOURCES = old_sources
            mirrors.CENTRAL = old_central

    def test_open_source_classifies_timeout(self) -> None:
        server, base = _serve(_SlowHandler)
        try:
            response, slot, transient = mirrors._open_source("slow", base, "x", timeout=0.2, attempts=2)
            self.assertIsNone(response)
            self.assertIsNone(slot)
            self.assertTrue(transient)
        finally:
            server.shutdown()
            server.server_close()

    def test_open_source_missing_is_not_transient(self) -> None:
        response, slot, transient = mirrors._open_source("good", self.base, "none", timeout=5, attempts=2)
        self.assertIsNone(response)
        self.assertFalse(transient)

    def test_fetch_races_slow_and_fast_sources(self) -> None:
        slow_server, slow = _serve(_SlowHandler)
        old_sources, old_central = mirrors.SOURCES, mirrors.CENTRAL
        mirrors.SOURCES = (("slowrace", slow), ("fastrace", self.base))
        mirrors.CENTRAL = self.base
        try:
            started = time.monotonic()
            data = mirrors.fetch("m/a.jar")
            elapsed = time.monotonic() - started
            self.assertEqual(data, self.payload)
            self.assertLess(elapsed, 0.9)
        finally:
            mirrors.SOURCES, mirrors.CENTRAL = old_sources, old_central
            slow_server.shutdown()
            slow_server.server_close()


class SourceHealthTests(unittest.TestCase):
    def test_orders_by_speed_and_skips_penalized(self) -> None:
        health = mirrors._SourceHealth()
        sources = (("a", "http://a"), ("b", "http://b"), ("c", "http://c"))
        health.ok("b", 0.1, 1024 * 1024)
        health.ok("a", 5.0, 1024)
        health.fail("c")
        self.assertEqual([name for name, _ in health.order(sources)], ["b", "a"])

    def test_all_penalized_falls_back_to_config_order(self) -> None:
        health = mirrors._SourceHealth()
        sources = (("a", "http://a"), ("b", "http://b"))
        health.fail("a")
        health.fail("b")
        self.assertEqual([name for name, _ in health.order(sources)], ["a", "b"])

    def test_pinned_source_goes_first_for_neoforged_paths(self) -> None:
        health = mirrors._SourceHealth()
        sources = (("bmclapi", "http://b"), ("stellar", "http://s"))
        health.ok("bmclapi", 0.1, 1024 * 1024)
        self.assertEqual(
            [name for name, _ in health.order(sources, "cpw/mods/x.jar")],
            ["bmclapi", "stellar"],
        )
        self.assertEqual(
            [
                name
                for name, _ in health.order(
                    sources, "cpw/mods/x.jar", mirrors._pinned_sources("cpw/mods/x.jar")
                )
            ],
            ["stellar", "bmclapi"],
        )
        self.assertEqual(mirrors._pinned_sources("com/google/guava/guava.jar"), ())

    def test_missing_path_is_skipped_then_reset(self) -> None:
        health = mirrors._SourceHealth()
        sources = (("a", "http://a"), ("b", "http://b"))
        health.miss("a", "m/x.jar")
        self.assertEqual([name for name, _ in health.order(sources, "m/x.jar")], ["b"])
        health.ok("a", 0.1, 1024)
        self.assertEqual([name for name, _ in health.order(sources, "m/x.jar")], ["a", "b"])


class RaceTests(TempDataCase):
    def test_race_returns_fast_source(self) -> None:
        slow_server, slow = _serve(_SlowHandler)
        fast_server, fast = _serve(_RepoHandler, files={"m/a.jar": b"fast"})
        try:
            started = time.monotonic()
            winner, _misses, _failures, incomplete, _unresolved = mirrors._race(
                [("slowrace2", slow), ("fastrace2", fast)], "m/a.jar", mode="bytes"
            )
            self.assertFalse(incomplete)
            elapsed = time.monotonic() - started
            self.assertIsNotNone(winner)
            self.assertEqual(winner[0], "fastrace2")
            self.assertEqual(winner[1], b"fast")
            self.assertLess(elapsed, 0.9)
        finally:
            for server in (slow_server, fast_server):
                server.shutdown()
                server.server_close()

    def test_race_marks_unanswered_source_incomplete(self) -> None:
        server, base = _serve(_SlowHandler)
        try:
            winner, _misses, failures, incomplete, _unresolved = mirrors._race(
                [("slowinc", base)], "m/a.jar", mode="bytes", timeout=0.2, overall=0.2
            )
            self.assertIsNone(winner)
            self.assertEqual(failures, 0)
            self.assertTrue(incomplete)
        finally:
            server.shutdown()
            server.server_close()

    def test_race_reports_missing_when_all_404(self) -> None:
        server, base = _serve(_RepoHandler, files={})
        try:
            winner, misses, failures, incomplete, _unresolved = mirrors._race(
                [("missrace", base)], "m/none.jar", mode="bytes"
            )
            self.assertIsNone(winner)
            self.assertEqual(misses, 1)
            self.assertEqual(failures, 0)
            self.assertFalse(incomplete)
        finally:
            server.shutdown()
            server.server_close()

    def test_race_flags_unanswered_required_source(self) -> None:
        server, base = _serve(_SlowHandler)
        try:
            winner, _misses, failures, incomplete, unresolved = mirrors._race(
                [("slowreq", base)],
                "m/a.jar",
                mode="bytes",
                timeout=0.2,
                overall=0.2,
                required=("slowreq",),
            )
            self.assertIsNone(winner)
            self.assertEqual(failures, 0)
            self.assertTrue(incomplete)
            self.assertTrue(unresolved)
        finally:
            server.shutdown()
            server.server_close()

    def test_race_accepts_404_when_unpinned_source_is_slow(self) -> None:
        miss_server, miss_base = _serve(_RepoHandler, files={})
        slow_server, slow_base = _serve(_SlowHandler)
        try:
            winner, misses, _failures, incomplete, unresolved = mirrors._race(
                [("missfast", miss_base), ("slowother", slow_base)],
                "m/none.jar",
                mode="bytes",
                timeout=2.0,
                overall=0.6,
                stagger=0.1,
                required=("missfast",),
            )
            self.assertIsNone(winner)
            self.assertEqual(misses, 1)
            self.assertTrue(incomplete)
            self.assertFalse(unresolved)
        finally:
            for server in (miss_server, slow_server):
                server.shutdown()
                server.server_close()


class MirrorServiceTests(TempDataCase):
    def test_synthesized_module_sha1_matches_content(self) -> None:
        """合成模块的 .sha1 必须是新内容的哈希，否则 Gradle 会退回旧缓存。"""
        service = mirrors.MirrorService()
        version_json = {
            "id": "1.20.1",
            "javaVersion": {"majorVersion": 17},
            "libraries": [{"name": "org.example:common:1.0"}],
        }
        path = (
            "net/neoforged/minecraft-dependencies/1.20.1/"
            "minecraft-dependencies-1.20.1.module"
        )
        try:
            base = service.start()
            with mock.patch.object(
                mirrors, "mojang_version_json", return_value=version_json
            ):
                module = urllib.request.urlopen(f"{base}/{path}", timeout=5).read()
                sha1 = urllib.request.urlopen(f"{base}/{path}.sha1", timeout=5).read()
        finally:
            service.stop()
        self.assertEqual(sha1.decode().strip(), hashlib.sha1(module).hexdigest())
        self.assertIn(b'"org.gradle.jvm.version": 17', module)

    def test_init_script_and_cache(self) -> None:
        service = mirrors.MirrorService()
        try:
            script = service.init_script()
            text = script.read_text(encoding="utf-8")
            self.assertIn("maven.neoforged.net", text)
            self.assertIn(service.base_url, text)
            self.assertIn("DMAMojang", text)
            service.store("a/b/c.jar", b"data")
            cached = service.cached("a/b/c.jar")
            self.assertIsNotNone(cached)
            self.assertEqual(cached.read_bytes(), b"data")
        finally:
            service.stop()

    def test_init_script_only_rewrites_given_hosts(self) -> None:
        service = mirrors.MirrorService()
        try:
            script = service.init_script(hosts=["maven.neoforged.net"])
            text = script.read_text(encoding="utf-8")
            self.assertIn("def officialHosts = ['maven.neoforged.net']", text)
            self.assertEqual(text.count("officialHosts.contains(u.host)"), 2)
        finally:
            service.stop()

    def test_init_script_feeds_nfrt_artifacts_to_manifest(self) -> None:
        """NFRT 下载器不认 Gradle 仓库，缺的构件必须提前塞进 artifact manifest。"""
        service = mirrors.MirrorService()
        try:
            text = service.init_script(hosts=["maven.neoforged.net"]).read_text(
                encoding="utf-8"
            )
            self.assertIn("addArtifactsToManifest", text)
            for coordinate in (
                "de.oceanlabs.mcp:mcp_config:1.20.1-20230612.114412@zip",
                "net.minecraftforge:installertools:1.2.0:fatjar",
                "net.minecraftforge:installertools:1.3.2:fatjar",
                "net.minecraftforge:mergetool:1.1.7:api",
                "net.minecraftforge:forgeflower:2.0.629.0",
                "net.minecraftforge:ForgeAutoRenamingTool:1.0.2:all",
                "net.minecraftforge:binarypatcher:1.1.1:fatjar",
                "org.jetbrains:annotations:24.0.1",
            ):
                self.assertIn(coordinate, text)
            # 模板用 str.format 渲染，落盘后不能残留双花括号。
            self.assertNotIn("{{", text)
            self.assertNotIn("}}", text)
        finally:
            service.stop()

    def test_unreachable_hosts_reports_failed_probes(self) -> None:
        with mock.patch.object(
            mirrors,
            "host_reachable",
            side_effect=lambda host, **_kwargs: host != "maven.neoforged.net",
        ):
            down = mirrors.unreachable_hosts(
                ["maven.neoforged.net", "maven.minecraftforge.net"]
            )
        self.assertEqual(down, ["maven.neoforged.net"])

    def test_prepare_launch_env_skips_mirror_when_reachable(self) -> None:
        with mock.patch.object(mirrors, "unreachable_hosts", return_value=[]):
            self.assertIsNone(mirrors.prepare_launch_env())

    def test_prepare_launch_env_passes_down_hosts_to_service(self) -> None:
        fake = mock.Mock()
        fake.init_script.return_value = Path("mirror.gradle")
        with mock.patch.object(
            mirrors, "unreachable_hosts", return_value=["maven.neoforged.net"]
        ):
            with mock.patch.object(mirrors, "service", return_value=fake):
                script = mirrors.prepare_launch_env()
        self.assertEqual(script, Path("mirror.gradle"))
        fake.init_script.assert_called_once_with(hosts=["maven.neoforged.net"])

    def test_prepare_launch_env_always_probes_official_hosts(self) -> None:
        """NeoGradle 的仓库 URL 在插件里动态添加，构建脚本扫不到。

        调用方传空 hosts 时也必须探测官方仓库，否则永远拿不到镜像。
        """
        with mock.patch.object(
            mirrors, "unreachable_hosts", return_value=[]
        ) as probe:
            mirrors.prepare_launch_env(hosts=["repo.example.com"])
        candidates = probe.call_args[0][0]
        for host in mirrors.OFFICIAL_HOSTS:
            self.assertIn(host, candidates)
        self.assertIn("repo.example.com", candidates)

    def test_missing_cache_expires_and_clears_on_new_build(self) -> None:
        service = mirrors.MirrorService()
        try:
            service._mark_missing("cpw/mods/modlauncher/11.0.5/modlauncher-11.0.5.pom")
            self.assertTrue(service._is_missing("cpw/mods/modlauncher/11.0.5/modlauncher-11.0.5.pom"))
            service._missing["cpw/mods/modlauncher/11.0.5/modlauncher-11.0.5.pom"] = 0.0
            self.assertFalse(service._is_missing("cpw/mods/modlauncher/11.0.5/modlauncher-11.0.5.pom"))
            service._mark_missing("a/b.jar")
            service.init_script()
            self.assertFalse(service._is_missing("a/b.jar"))
        finally:
            service.stop()

    def test_cached_falls_back_to_gradle_cache(self) -> None:
        from unittest import mock

        coordinate = "net/neoforged/fancymodloader/loader/9.9.99/loader-9.9.99.pom"
        # Gradle 缓存按 group 的点号拆成多级目录。
        folder = self.tmp / ".gradle/caches/modules-2/files-2.1"
        for part in ("net", "neoforged", "fancymodloader", "loader", "9.9.99", "abc123"):
            folder = folder / part
        folder.mkdir(parents=True, exist_ok=True)
        cached_file = folder / "loader-9.9.99.pom"
        cached_file.write_bytes(b"<project/>")
        service = mirrors.MirrorService()
        try:
            with mock.patch("pathlib.Path.home", return_value=self.tmp):
                found = mirrors._gradle_cache_find(coordinate)
                self.assertEqual(found, cached_file)
                result = service.cached(coordinate)
            self.assertIsNotNone(result)
            self.assertEqual(result.read_bytes(), b"<project/>")
        finally:
            service.stop()

    def test_server_swallows_client_disconnects(self) -> None:
        from unittest import mock

        server = mirrors._Server(("127.0.0.1", 0), mirrors._Handler)
        try:
            with mock.patch("sys.stderr") as stderr:
                try:
                    raise ConnectionResetError(10054, "reset")
                except ConnectionResetError:
                    server.handle_error(None, ("127.0.0.1", 0))
                stderr.write.assert_not_called()
        finally:
            server.server_close()

    def test_missing_jar_returns_503_only_for_transient(self) -> None:
        service = mirrors.MirrorService()
        original = mirrors._open_source
        mirrors._open_source = lambda *args, **kwargs: (None, None, True)
        try:
            handler = _FakeHandler()
            service.stream("missing/x.jar", handler)
            self.assertEqual(handler.status, 503)
            self.assertNotIn("missing/x.jar", service._missing)
        finally:
            mirrors._open_source = original
            service.stop()


class _FakeHandler:
    def __init__(self) -> None:
        self.status = 200
        self.command = "GET"
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        return

    def end_headers(self) -> None:
        return

    def _send_bytes(self, data: bytes, status: int = 200) -> None:
        self.status = status

    def _missing(self) -> None:
        self.status = 404


if __name__ == "__main__":
    unittest.main()
