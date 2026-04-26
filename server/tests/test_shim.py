"""Tests for the OpenAI tool-calling shim in app.endpoints.chat.

Run with: cd server && .venv/bin/python -m pytest tests/test_shim.py -v
(or just `python -m unittest tests.test_shim`)
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app.endpoints.chat import (  # noqa: E402
    _build_tools_system_prompt,
    _extract_tool_calls,
    _maybe_truncate_tool_result,
    MAX_TOOL_RESULT_CHARS,
)


class TestExtractToolCalls(unittest.TestCase):
    def test_strict_format(self):
        text = '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "ls"}}\n<<END>>'
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "bash")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"command": "ls"})

    def test_missing_end_marker(self):
        text = '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "npm install"}}\n'
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "bash")

    def test_two_parallel_calls(self):
        text = (
            '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "a"}}\n<<END>>\n'
            '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "b"}}\n<<END>>'
        )
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "a")
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["command"], "b")

    def test_two_calls_second_missing_end(self):
        text = (
            '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "a"}}\n<<END>>\n'
            '<<TOOL_CALL>>\n{"name": "bash", "arguments": {"command": "b"}}\n'
        )
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(len(calls), 2)

    def test_plain_text_no_call(self):
        self.assertEqual(_extract_tool_calls("just a regular response", {"bash"}), [])

    def test_legacy_text_format_mapped_to_bash(self):
        text = "[tool_call:ls for path '/foo']"
        calls = _extract_tool_calls(text, {"bash", "read"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "bash")

    def test_legacy_text_format_unknown_name_dropped(self):
        text = "[tool_call:nonexistent for x]"
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(calls, [])

    def test_id_is_unique_per_call(self):
        text = '<<TOOL_CALL>>{"name":"bash","arguments":{}}<<END>><<TOOL_CALL>>{"name":"bash","arguments":{}}<<END>>'
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["id"], calls[1]["id"])

    def test_invalid_json_skipped(self):
        text = '<<TOOL_CALL>>{not valid json}<<END>>'
        calls = _extract_tool_calls(text, {"bash"})
        self.assertEqual(calls, [])


class TestBuildToolsSystemPrompt(unittest.TestCase):
    def test_uses_real_tool_name_as_example(self):
        prompt = _build_tools_system_prompt(
            [{"name": "bash", "description": "Run shell", "parameters": {"type": "object"}}]
        )
        self.assertIn('"name": "bash"', prompt)
        self.assertIn("<<TOOL_CALL>>", prompt)
        self.assertIn("CRITICAL", prompt)

    def test_handles_nested_function_wrapper(self):
        prompt = _build_tools_system_prompt(
            [{"type": "function", "function": {"name": "read", "description": "Read file", "parameters": {}}}]
        )
        self.assertIn("read", prompt)

    def test_warns_against_legacy_format(self):
        prompt = _build_tools_system_prompt([{"name": "bash"}])
        self.assertIn("[tool_call:", prompt)
        self.assertIn("not parsed", prompt)


class TestTruncateToolResult(unittest.TestCase):
    def test_small_unchanged(self):
        out, truncated = _maybe_truncate_tool_result("short")
        self.assertFalse(truncated)
        self.assertEqual(out, "short")

    def test_large_truncated_to_cap(self):
        big = "X" * (MAX_TOOL_RESULT_CHARS * 8)
        out, truncated = _maybe_truncate_tool_result(big)
        self.assertTrue(truncated)
        self.assertLess(len(out), MAX_TOOL_RESULT_CHARS + 200)
        self.assertIn("gemini-bridge truncated", out)

    def test_preserves_head_and_tail(self):
        body = "HEAD-MARKER" + "X" * 50000 + "TAIL-MARKER"
        out, truncated = _maybe_truncate_tool_result(body)
        self.assertTrue(truncated)
        self.assertIn("HEAD-MARKER", out)
        self.assertIn("TAIL-MARKER", out)


if __name__ == "__main__":
    unittest.main()
