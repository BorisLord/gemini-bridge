"""Reasoning output contract: when `gemini-webapi.ModelOutput.thoughts` is
populated (Gemini 2.5 Thinking models), the bridge surfaces it via
`reasoning_content` (DeepSeek-R1 convention adopted by Cline/opencode/Continue).

Non-thinking models leave `thoughts=None` → bridge omits the field entirely so
clients unaware of the convention see the unchanged OpenAI shape."""
import json
import unittest

from _helpers import install_registry, seeded_registry
from app.main import app
from litestar.testing import TestClient


def _parse_sse(payload: str) -> list[str]:
    normalized = payload.replace("\r\n", "\n")
    return [
        frame[len("data: "):]
        for frame in normalized.split("\n\n")
        if frame.startswith("data: ")
    ]


class TestReasoningNonStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_thoughts_surface_as_reasoning_content(self):
        reg = seeded_registry(response_text="42", response_thoughts="Let me think step by step…")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash-thinking@firefox:0",
                    "messages": [{"role": "user", "content": "compute 6*7"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        msg = r.json()["choices"][0]["message"]
        self.assertEqual(msg["content"], "42")
        self.assertEqual(msg["reasoning_content"], "Let me think step by step…")

    def test_no_thoughts_omits_field(self):
        reg = seeded_registry(response_text="42", response_thoughts=None)
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash@firefox:0",
                    "messages": [{"role": "user", "content": "compute 6*7"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        msg = r.json()["choices"][0]["message"]
        self.assertNotIn("reasoning_content", msg)

    def test_empty_thoughts_omits_field(self):
        # Thinking models on a trivial prompt may return thoughts="" — treat as
        # absent so we don't pollute the response with a useless empty key.
        reg = seeded_registry(response_text="42", response_thoughts="")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash-thinking@firefox:0",
                    "messages": [{"role": "user", "content": "compute 6*7"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("reasoning_content", r.json()["choices"][0]["message"])


class TestReasoningStream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_reasoning_chunk_emitted_before_content(self):
        reg = seeded_registry(response_text="42", response_thoughts="Step 1: 6+6+6+6+6+6+6=42")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash-thinking@firefox:0",
                    "messages": [{"role": "user", "content": "compute 6*7"}],
                    "stream": True,
                },
            )
        chunks = [json.loads(f) for f in _parse_sse(r.text) if f != "[DONE]"]
        idxs_reasoning = [
            i for i, c in enumerate(chunks)
            if c["choices"][0]["delta"].get("reasoning_content")
        ]
        idxs_content = [
            i for i, c in enumerate(chunks)
            if c["choices"][0]["delta"].get("content") == "42"
        ]
        self.assertEqual(len(idxs_reasoning), 1)
        self.assertEqual(len(idxs_content), 1)
        self.assertLess(idxs_reasoning[0], idxs_content[0])
        self.assertEqual(
            chunks[idxs_reasoning[0]]["choices"][0]["delta"]["reasoning_content"],
            "Step 1: 6+6+6+6+6+6+6=42",
        )

    def test_no_thoughts_no_reasoning_chunk(self):
        reg = seeded_registry(response_text="42", response_thoughts=None)
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-flash@firefox:0",
                    "messages": [{"role": "user", "content": "compute 6*7"}],
                    "stream": True,
                },
            )
        chunks = [json.loads(f) for f in _parse_sse(r.text) if f != "[DONE]"]
        self.assertFalse(
            any(c["choices"][0]["delta"].get("reasoning_content") for c in chunks),
            "no reasoning_content chunk should appear when thoughts is None",
        )


if __name__ == "__main__":
    unittest.main()
