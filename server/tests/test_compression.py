"""Compression contract:
- Plain JSON responses above the size threshold get gzipped.
- SSE streaming MUST NOT be gzipped (per-chunk compression buffers content
  and breaks live-stream semantics behind reverse proxies).
"""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from litestar.testing import TestClient


class TestCompression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_models_payload_is_gzipped(self):
        # /v1/models renders >500 bytes (default minimum_size) → must be gzipped.
        r = self.client.get("/v1/models", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-encoding"), "gzip")

    def test_sse_stream_is_not_gzipped(self):
        # Streaming chat completions must remain unencoded — Litestar's
        # CompressionConfig excludes ^/v1/chat/completions$ for this reason.
        fake_resp = MagicMock()
        fake_resp.text = "Hello stream"
        fake_client = MagicMock()
        fake_client.generate_content = AsyncMock(return_value=fake_resp)
        with patch("app.endpoints.chat.get_gemini_client", return_value=fake_client):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"Accept-Encoding": "gzip"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertNotEqual(r.headers.get("content-encoding"), "gzip")
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))


if __name__ == "__main__":
    unittest.main()
