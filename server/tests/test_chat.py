import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fastapi.testclient import TestClient

from app.main import app


class TestChatCompletions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

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

    def test_non_gemini_model_rejected(self):
        r = self.client.post(
            "/v1/chat/completions",
            json={
                "model": "qwen/qwen3-coder:free",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
