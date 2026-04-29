import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from app.endpoints.chat import (  # noqa: E402
    _build_prompt_from_messages,
    _build_tools_system_prompt,
    _extract_tool_calls,
    _maybe_truncate_tool_result,
    _strip_md_wrappers,
    _trim_messages_to_fit,
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
        # head + marker + tail ≈ cap + 30; 150 of slack for marker variance.
        self.assertLessEqual(len(out), MAX_TOOL_RESULT_CHARS + 150)
        self.assertIn("gemini-bridge truncated", out)

    def test_preserves_head_and_tail(self):
        body = "HEAD-MARKER" + "X" * 50000 + "TAIL-MARKER"
        out, truncated = _maybe_truncate_tool_result(body)
        self.assertTrue(truncated)
        self.assertIn("HEAD-MARKER", out)
        self.assertIn("TAIL-MARKER", out)


class TestStripMdWrappers(unittest.TestCase):
    def test_collapses_identical_url_link(self):
        self.assertEqual(
            _strip_md_wrappers("[https://opencode.ai/docs](https://opencode.ai/docs)"),
            "https://opencode.ai/docs",
        )

    def test_keeps_url_target_when_label_differs(self):
        self.assertEqual(
            _strip_md_wrappers("[opencode docs](https://opencode.ai/docs)"),
            "https://opencode.ai/docs",
        )

    def test_keeps_path_target_when_label_differs(self):
        self.assertEqual(
            _strip_md_wrappers("[entrypoint](src/run.py)"),
            "src/run.py",
        )

    def test_keeps_label_when_target_is_neither_url_nor_path(self):
        self.assertEqual(
            _strip_md_wrappers("[user_42](placeholder)"),
            "user_42",
        )

    def test_strips_code_span(self):
        self.assertEqual(_strip_md_wrappers("`/abs/path/file.py`"), "/abs/path/file.py")

    def test_passthrough_plain_string(self):
        self.assertEqual(_strip_md_wrappers("https://example.com"), "https://example.com")

    def test_does_not_touch_embedded_markdown_in_long_content(self):
        body = "First line.\nSee [docs](https://x) for details.\nEnd."
        self.assertEqual(_strip_md_wrappers(body), body)

    def test_recurses_into_dict_and_list(self):
        out = _strip_md_wrappers({
            "url": "[https://a](https://a)",
            "tags": ["`tag1`", "plain"],
            "nested": {"path": "`/etc/hosts`"},
            "count": 7,
        })
        self.assertEqual(out, {
            "url": "https://a",
            "tags": ["tag1", "plain"],
            "nested": {"path": "/etc/hosts"},
            "count": 7,
        })

    def test_records_changes_when_collector_passed(self):
        changes: list = []
        _strip_md_wrappers({"url": "[https://x](https://x)", "ok": "raw"}, changes)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0], ("[https://x](https://x)", "https://x"))


class TestExtractToolCallsSanitization(unittest.TestCase):
    def test_strips_markdown_url_in_extracted_call(self):
        text = (
            '<<TOOL_CALL>>\n'
            '{"name": "webfetch", "arguments": {"url": "[https://opencode.ai/docs](https://opencode.ai/docs)"}}\n'
            '<<END>>'
        )
        calls = _extract_tool_calls(text, {"webfetch"})
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            json.loads(calls[0]["function"]["arguments"]),
            {"url": "https://opencode.ai/docs"},
        )


class TestTrimMessagesToFit(unittest.TestCase):
    def test_under_cap_passthrough(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        trimmed, dropped = _trim_messages_to_fit(msgs, None, 8000, 1_000_000)
        # Plenty of room — original list returned untouched.
        self.assertEqual(dropped, 0)
        rendered, _, _ = _build_prompt_from_messages(trimmed, None, 8000)
        self.assertIn("User: hi", rendered)
        self.assertIn("Assistant: hello", rendered)

    def test_drops_oldest_until_fits(self):
        big = "X" * 5000
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": f"old {big}"},
            {"role": "assistant", "content": f"old reply {big}"},
            {"role": "user", "content": f"middle {big}"},
            {"role": "assistant", "content": f"middle reply {big}"},
            {"role": "user", "content": "latest question"},
        ]
        trimmed, dropped = _trim_messages_to_fit(msgs, None, 8000, 6000)
        rendered, _, _ = _build_prompt_from_messages(trimmed, None, 8000)
        self.assertLessEqual(len(rendered), 6000)
        # System message is preserved.
        self.assertIn("System: SYS", rendered)
        # Tail kept.
        self.assertIn("latest question", rendered)
        # Placeholder injected.
        self.assertIn("Earlier conversation elided", rendered)
        self.assertGreater(dropped, 0)

    def test_keeps_at_least_last_message(self):
        # Even if the very last message alone is too big, we still emit it.
        msgs = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "X" * 10000},
        ]
        trimmed, _ = _trim_messages_to_fit(msgs, None, 8000, 100)
        roles = [m["role"] for m in trimmed]
        self.assertIn("user", roles)


if __name__ == "__main__":
    unittest.main()
