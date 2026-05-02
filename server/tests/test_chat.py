import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.main import app
from litestar.testing import TestClient


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


def _parse_sse(payload: str) -> list[str]:
    """Extract data field from each SSE frame. Litestar emits CRLF between frames."""
    normalized = payload.replace("\r\n", "\n")
    return [
        frame[len("data: "):]
        for frame in normalized.split("\n\n")
        if frame.startswith("data: ")
    ]


class TestChatCompletionsStreaming(unittest.TestCase):
    """SSE codepath: stream=True yields text/event-stream framed chunks
    matching OpenAI's chat.completion.chunk schema, terminated by `[DONE]`."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _post_stream(self, body: dict, resp_text: str = "Hello stream"):
        fake_resp = MagicMock()
        fake_resp.text = resp_text
        fake_client = MagicMock()
        fake_client.generate_content = AsyncMock(return_value=fake_resp)
        with patch("app.endpoints.chat.get_gemini_client", return_value=fake_client):
            return self.client.post("/v1/chat/completions", json=body)

    def test_stream_returns_event_stream_content_type(self):
        r = self._post_stream({
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))

    def test_stream_emits_done_sentinel_last(self):
        r = self._post_stream({
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        frames = _parse_sse(r.text)
        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual(frames[-1], "[DONE]")

    def test_stream_chunks_match_openai_shape(self):
        r = self._post_stream({
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }, resp_text="ABC")
        chunks = [json.loads(f) for f in _parse_sse(r.text) if f != "[DONE]"]
        self.assertGreater(len(chunks), 0)
        for c in chunks:
            self.assertEqual(c["object"], "chat.completion.chunk")
            self.assertEqual(c["model"], "gemini-3-flash")
            self.assertEqual(c["choices"][0]["index"], 0)
        self.assertEqual(chunks[0]["choices"][0]["delta"].get("role"), "assistant")
        self.assertTrue(any(c["choices"][0]["delta"].get("content") == "ABC" for c in chunks))
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_stream_with_tool_calls_finish_reason_tool_calls(self):
        # Gemini emits a delimited <<TOOL_CALL>> block; bridge re-emits as OpenAI tool_calls.
        tool_text = '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "ls"}}\n<<END>>'
        r = self._post_stream({
            "model": "gemini-3-flash",
            "messages": [{"role": "user", "content": "run ls"}],
            "stream": True,
            "tools": [{"type": "function", "function": {"name": "bash", "parameters": {}}}],
        }, resp_text=tool_text)
        self.assertEqual(r.status_code, 200)
        chunks = [json.loads(f) for f in _parse_sse(r.text) if f != "[DONE]"]
        self.assertTrue(any("tool_calls" in c["choices"][0]["delta"] for c in chunks))
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "tool_calls")


if __name__ == "__main__":
    unittest.main()
