import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services import fallback  # noqa: E402


class TestChatCompletions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        fallback._state["enabled"] = False
        fallback._state["api_key"] = None
        fallback._state["model"] = "qwen/qwen3-coder:free"
        fallback._STICKY_UNTIL = None

    def test_happy_path_returns_openai_shape(self):
        fake_resp = MagicMock()
        fake_resp.text = "Hello from Gemini"
        fake_client = MagicMock()
        fake_client.generate_content = AsyncMock(return_value=fake_resp)
        with patch("app.endpoints.chat.get_gemini_client", return_value=fake_client):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro-plus",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["model"], "gemini-3-pro-plus")
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["choices"][0]["message"]["content"], "Hello from Gemini")
        # No fallback engaged — header must be absent.
        self.assertNotIn("X-Bridge-Fallback", r.headers)

    def test_quota_error_engages_openrouter_fallback(self):
        fallback._state["enabled"] = True
        fallback._state["api_key"] = "sk-or-test"

        fake_client = MagicMock()
        fake_client.generate_content = AsyncMock(
            side_effect=RuntimeError("Gemini status: 429 too many requests")
        )
        or_resp = {
            "id": "chatcmpl-test",
            "object": "chat.completion",
            "created": 0,
            "model": "qwen/qwen3-coder:free",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "Hello from OpenRouter"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }
        with patch("app.endpoints.chat.get_gemini_client", return_value=fake_client), \
             patch("app.endpoints.chat.call_openrouter_fallback",
                   new=AsyncMock(return_value=or_resp)):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro-plus",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            r.headers.get("X-Bridge-Fallback"),
            "openrouter:qwen/qwen3-coder:free:quota",
        )
        body = r.json()
        self.assertEqual(body["model"], "gemini-3-pro-plus→openrouter:qwen/qwen3-coder:free")
        self.assertEqual(body["choices"][0]["message"]["content"], "Hello from OpenRouter")


if __name__ == "__main__":
    unittest.main()
