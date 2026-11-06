from __future__ import annotations

import io
import unittest
import urllib.error
from unittest import mock

from dma import ai


class AIErrorTests(unittest.TestCase):
    def test_timeout_error_mentions_host_and_hint(self) -> None:
        error = urllib.error.URLError(
            TimeoutError(
                "[WinError 10060] 由于连接方在一段时间后没有正确答复或连接的主机没有反应，"
                "连接尝试失败。"
            )
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ai.AIError) as ctx:
                ai._http_json(
                    "https://api.openai.com/v1/chat/completions", {}, {}, 10
                )
        text = str(ctx.exception)
        self.assertIn("api.openai.com", text)
        self.assertIn("10060", text)
        self.assertIn("Base URL", text)

    def test_401_mentions_key_and_base_url(self) -> None:
        error = urllib.error.HTTPError(
            "https://example.com/v1/chat/completions",
            401,
            "Unauthorized",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":{"message":"bad key"}}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ai.AIError) as ctx:
                ai._http_json("https://example.com/v1/chat/completions", {}, {}, 10)
        text = str(ctx.exception)
        self.assertIn("401", text)
        self.assertIn("API Key", text)
        self.assertIn("bad key", text)

    def test_404_mentions_base_url_or_model(self) -> None:
        error = urllib.error.HTTPError(
            "https://example.com/v1/chat/completions",
            404,
            "Not Found",
            {"Content-Type": "application/json"},
            io.BytesIO(b'{"error":"model not found"}'),
        )
        with mock.patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaises(ai.AIError) as ctx:
                ai._http_json("https://example.com/v1/chat/completions", {}, {}, 10)
        text = str(ctx.exception)
        self.assertIn("404", text)
        self.assertIn("模型名", text)


if __name__ == "__main__":
    unittest.main()
