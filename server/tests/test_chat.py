import json
import unittest

from _helpers import install_registry, seeded_registry
from app.main import app
from litestar.testing import TestClient


class TestChatCompletions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_happy_path_returns_openai_shape(self):
        with install_registry(seeded_registry(response_text="Hello from Gemini")):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro-plus@firefox:0",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # Server echoes back the routed model name as sent (suffix preserved).
        self.assertEqual(body["model"], "gemini-3-pro-plus@firefox:0")
        self.assertEqual(body["object"], "chat.completion")
        self.assertEqual(body["choices"][0]["finish_reason"], "stop")
        self.assertEqual(body["choices"][0]["message"]["content"], "Hello from Gemini")

    def test_non_gemini_model_rejected(self):
        # Even with a healthy registry + explicit routing, non-Gemini model ids
        # must 400 before we call the upstream lib.
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "qwen/qwen3-coder:free@firefox:0",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 400)

    def test_bare_model_without_routing_returns_400(self):
        # Routing is mandatory: no header + no `@<id>` suffix → 400.
        with install_registry(seeded_registry()):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("Routing required", r.json()["error"]["message"])


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
        with install_registry(seeded_registry(response_text=resp_text)):
            return self.client.post("/v1/chat/completions", json=body)

    def test_stream_returns_event_stream_content_type(self):
        r = self._post_stream({
            "model": "gemini-3-flash@firefox:0",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/event-stream"))

    def test_stream_emits_done_sentinel_last(self):
        r = self._post_stream({
            "model": "gemini-3-flash@firefox:0",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        })
        frames = _parse_sse(r.text)
        self.assertGreaterEqual(len(frames), 2)
        self.assertEqual(frames[-1], "[DONE]")

    def test_stream_chunks_match_openai_shape(self):
        r = self._post_stream({
            "model": "gemini-3-flash@firefox:0",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }, resp_text="ABC")
        chunks = [json.loads(f) for f in _parse_sse(r.text) if f != "[DONE]"]
        self.assertGreater(len(chunks), 0)
        for c in chunks:
            self.assertEqual(c["object"], "chat.completion.chunk")
            # Echoed model keeps the routing suffix as the client sent it.
            self.assertEqual(c["model"], "gemini-3-flash@firefox:0")
            self.assertEqual(c["choices"][0]["index"], 0)
        self.assertEqual(chunks[0]["choices"][0]["delta"].get("role"), "assistant")
        self.assertTrue(any(c["choices"][0]["delta"].get("content") == "ABC" for c in chunks))
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")

    def test_stream_with_tool_calls_finish_reason_tool_calls(self):
        # Gemini emits a delimited <<TOOL_CALL>> block; bridge re-emits as OpenAI tool_calls.
        tool_text = '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "ls"}}\n<<END>>'
        r = self._post_stream({
            "model": "gemini-3-flash@firefox:0",
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
