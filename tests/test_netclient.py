from __future__ import annotations

import threading
import time
import unittest
import urllib.request
from unittest import mock

from dma import connectors, netclient


def request(url: str = "https://example.com/file") -> urllib.request.Request:
    return urllib.request.Request(url, headers={"User-Agent": "test"})


class NetClientTests(unittest.TestCase):
    def setUp(self) -> None:
        netclient.reset_blackhole()
        netclient.restore_cancel(None)

    def tearDown(self) -> None:
        netclient.reset_blackhole()
        netclient.restore_cancel(None)

    def test_hard_deadline_interrupts_stuck_request(self) -> None:
        def stuck(*_args, **_kwargs):
            time.sleep(3)
            return object()

        started = time.monotonic()
        with mock.patch.object(netclient.urllib.request, "urlopen", side_effect=stuck):
            with self.assertRaises(netclient.NetTimeout):
                netclient.fetch(request(), timeout=0.4)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_cancel_returns_without_waiting_for_deadline(self) -> None:
        cancel = threading.Event()

        def stuck(*_args, **_kwargs):
            time.sleep(3)
            return object()

        threading.Timer(0.15, cancel.set).start()
        started = time.monotonic()
        with mock.patch.object(netclient.urllib.request, "urlopen", side_effect=stuck):
            with self.assertRaises(netclient.NetCancelled):
                netclient.fetch(request(), timeout=30, cancel=cancel)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_cancel_from_thread_context(self) -> None:
        cancel = threading.Event()
        netclient.bind_cancel(cancel)
        cancel.set()
        with self.assertRaises(netclient.NetCancelled):
            netclient.fetch(request(), timeout=5)

    def test_blackhole_host_fails_fast_after_timeout(self) -> None:
        def stuck(*_args, **_kwargs):
            time.sleep(3)
            return object()

        with mock.patch.object(netclient.urllib.request, "urlopen", side_effect=stuck) as urlopen:
            with self.assertRaises(netclient.NetTimeout):
                netclient.fetch(request("https://cdn.example.test/a.js"), timeout=0.3)
            self.assertEqual(urlopen.call_count, 1)
            started = time.monotonic()
            with self.assertRaises(netclient.NetTimeout):
                netclient.fetch(request("https://cdn.example.test/b.js"), timeout=5)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(urlopen.call_count, 1)

    def test_successful_request_returns_body(self) -> None:
        class Response:
            status = 200
            headers = {"Content-Type": "text/plain"}

            def read(self) -> bytes:
                return b"hello"

            def close(self) -> None:
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc) -> None:
                self.close()

        with mock.patch.object(netclient.urllib.request, "urlopen", return_value=Response()):
            self.assertEqual(netclient.fetch(request(), timeout=5), b"hello")
            body, status, headers = netclient.fetch_info(request(), timeout=5)
        self.assertEqual(body, b"hello")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "text/plain")


class ConnectorFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        netclient.reset_blackhole()

    def tearDown(self) -> None:
        netclient.reset_blackhole()

    def test_jsdelivr_is_rewritten_to_github_api(self) -> None:
        url, headers = connectors._rewrite_blocked_url(
            "https://cdn.jsdelivr.net/gh/neoforged/NeoForge@1.21.1/src/main/java/A.java"
        )
        self.assertEqual(
            url,
            "https://api.github.com/repos/neoforged/NeoForge/contents/"
            "src/main/java/A.java?ref=1.21.1",
        )
        self.assertEqual(headers.get("Accept"), "application/vnd.github.raw")

    def test_raw_github_is_rewritten_to_github_api(self) -> None:
        url, headers = connectors._rewrite_blocked_url(
            "https://raw.githubusercontent.com/owner/repo/main/docs/readme.md"
        )
        self.assertEqual(
            url,
            "https://api.github.com/repos/owner/repo/contents/docs/readme.md?ref=main",
        )
        self.assertEqual(headers.get("Accept"), "application/vnd.github.raw")

    def test_other_urls_are_untouched(self) -> None:
        url, headers = connectors._rewrite_blocked_url("https://api.modrinth.com/v2/search")
        self.assertEqual(url, "https://api.modrinth.com/v2/search")
        self.assertEqual(headers, {})

    def test_timeout_becomes_connector_error(self) -> None:
        with mock.patch.object(
            netclient, "fetch", side_effect=netclient.NetTimeout("目标主机没有响应")
        ):
            with self.assertRaises(connectors.ConnectorError) as ctx:
                connectors.fetch_url("https://example.com/a")
        self.assertIn("超时", str(ctx.exception))

    def test_cancel_becomes_connector_error(self) -> None:
        with mock.patch.object(
            netclient, "fetch", side_effect=netclient.NetCancelled("已取消")
        ):
            with self.assertRaises(connectors.ConnectorError) as ctx:
                connectors.fetch_url("https://example.com/a")
        self.assertIn("取消", str(ctx.exception))

    def test_fetch_url_forwards_cancel_event(self) -> None:
        cancel = threading.Event()
        with mock.patch.object(netclient, "fetch", return_value=b"ok") as fetch:
            self.assertEqual(
                connectors.fetch_url("https://example.com/a", cancel=cancel), "ok"
            )
        self.assertIs(fetch.call_args.kwargs.get("cancel"), cancel)


if __name__ == "__main__":
    unittest.main()
