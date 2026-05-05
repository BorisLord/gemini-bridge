"""Compression contract:
- Plain JSON responses above the size threshold get gzipped.
- SSE streaming MUST NOT be gzipped (per-chunk compression buffers content
  and breaks live-stream semantics behind reverse proxies).
"""
import unittest

from _helpers import install_registry, seeded_registry
from app.main import app
from litestar.testing import TestClient


class TestCompression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_models_payload_is_gzipped(self):
        # /v1/models renders >500 bytes (default minimum_size) → must be gzipped.
        # Force >1 account so the per-account variants pad the payload past the
        # gzip threshold even on a freshly imported app.
        with install_registry(seeded_registry(account_count=2, with_clients=False)):
            r = self.client.get("/v1/models", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers.get("content-encoding"), "gzip")

    def test_sse_stream_is_not_gzipped(self):
        # Streaming chat completions must remain unencoded — Litestar's
        # CompressionConfig excludes ^/v1/chat/completions$ for this reason.
        with install_registry(seeded_registry(response_text="Hello stream")):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash@firefox:0",
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
