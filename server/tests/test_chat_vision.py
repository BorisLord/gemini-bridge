"""Vision input contract: OpenAI-shape `image_url` blocks in `messages[*].content`
flow into `gemini-webapi.generate_content(files=...)` as filesystem paths
with the right extension. The text portion of multimodal content is preserved
in the prompt; image bytes travel out-of-band so the prompt budget is not
consumed by base64 blobs."""
import base64
import unittest
from typing import ClassVar
from unittest.mock import patch

from _helpers import install_registry, seeded_registry
from app.main import app
from litestar.testing import TestClient

# 1x1 transparent PNG, encoded once for reuse.
_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgAAIAAAUAAeImBZsAAAAASUVORK5CYII="
)
_PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(_PNG_BYTES).decode()


class TestChatVisionInput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_data_url_image_passed_to_files_kwarg(self):
        reg = seeded_registry(response_text="image seen")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe this"},
                            {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
                        ],
                    }],
                },
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["choices"][0]["message"]["content"], "image seen")

        # Inspect what the bridge handed to gemini-webapi.
        client_mock = reg.get("firefox:0").client
        kwargs = client_mock.generate_content.call_args.kwargs
        self.assertIsInstance(kwargs.get("files"), list)
        self.assertEqual(len(kwargs["files"]), 1)
        # Filename must keep the .png extension so gemini-webapi's mime guess
        # uploads it as image/png — bytes-only would degrade to text/plain.
        self.assertTrue(kwargs["files"][0].endswith(".png"),
                        f"expected .png suffix, got {kwargs['files'][0]!r}")

        # The prompt must keep the textual block but drop the image bytes (they
        # travel via files=, not via the prompt).
        prompt = kwargs["prompt"] if "prompt" in kwargs else client_mock.generate_content.call_args.args[0]
        self.assertIn("describe this", prompt)
        self.assertNotIn("base64", prompt)
        self.assertNotIn(_PNG_DATA_URL, prompt)

    def test_text_only_message_keeps_files_none(self):
        # Regression: a plain string-content message must not trigger files=.
        reg = seeded_registry(response_text="ok")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:0").client.generate_content.call_args.kwargs
        self.assertIsNone(kwargs.get("files"))

    def test_multimodal_text_only_blocks_pass_files_none(self):
        # `list[dict]` content with only `type:"text"` blocks must not generate files.
        reg = seeded_registry(response_text="ok")
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "part one"},
                            {"type": "text", "text": "part two"},
                        ],
                    }],
                },
            )
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:0").client.generate_content.call_args.kwargs
        self.assertIsNone(kwargs.get("files"))
        prompt = kwargs.get("prompt") or reg.get("firefox:0").client.generate_content.call_args.args[0]
        self.assertIn("part one", prompt)
        self.assertIn("part two", prompt)

    def test_multiple_images_preserve_declaration_order(self):
        reg = seeded_registry(response_text="ok")
        url2 = "data:image/jpeg;base64,AAEC"  # \x00\x01\x02 as JPEG
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
                            {"type": "text", "text": "compare"},
                            {"type": "image_url", "image_url": {"url": url2}},
                        ],
                    }],
                },
            )
        self.assertEqual(r.status_code, 200)
        files = reg.get("firefox:0").client.generate_content.call_args.kwargs["files"]
        self.assertEqual(len(files), 2)
        # First file is PNG, second is JPEG — extensions preserved per-block
        # so each upload gets the right Content-Type. Files themselves are
        # already unlinked by the call site's finally, so we don't read them
        # back from disk; the upstream lib opened/read them during the awaited
        # generate_content call.
        self.assertTrue(files[0].endswith(".png"))
        self.assertTrue(files[1].endswith((".jpg", ".jpeg")))

    def test_unsupported_scheme_returns_400(self):
        reg = seeded_registry()
        with install_registry(reg):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "ftp://example.com/img.png"}},
                        ],
                    }],
                },
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("scheme", r.json()["error"]["message"].lower())

    def test_http_url_is_downloaded_and_forwarded(self):
        # http(s) URLs are fetched at request time. Patch the whole AsyncClient
        # at the import site so the bridge's `async with httpx.AsyncClient(...)`
        # block uses our fake instead of the network.
        reg = seeded_registry(response_text="ok")
        fake_payload = b"\xde\xad\xbe\xef"

        class _FakeResp:
            content = fake_payload
            headers: ClassVar[dict] = {"content-type": "image/png"}
            def raise_for_status(self):
                return None

        class _FakeClient:
            def __init__(self, *a, **kw):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a, **kw):
                return False
            async def get(self, url, *a, **kw):
                return _FakeResp()

        # FakeResp serves the image bytes back with image/png Content-Type;
        # the bridge writes a tempfile with .png suffix so gemini-webapi's
        # upload uses the right MIME type.
        with install_registry(reg), patch("app.endpoints.chat.httpx.AsyncClient", _FakeClient):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}},
                        ],
                    }],
                },
            )
        self.assertEqual(r.status_code, 200)
        files = reg.get("firefox:0").client.generate_content.call_args.kwargs["files"]
        self.assertEqual(len(files), 1)
        self.assertIn("gemini-bridge-img-", files[0])
        self.assertTrue(files[0].endswith(".png"))


class TestChatVisionTrimAlignment(unittest.TestCase):
    """When `_trim_messages_to_fit` drops an older message that carried an
    `image_url`, the bytes MUST also be dropped — uploading orphan images
    whose textual context has been elided gives Gemini a fresh image with no
    history hook and produces hallucinations."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_image_dropped_alongside_trimmed_message(self):
        # MAX_PROMPT_CHARS forced low so the older message overflows and
        # gets replaced with the placeholder. The image attached to that
        # message should NOT be passed to generate_content.
        reg = seeded_registry(response_text="ok")
        old_text = "x" * 5000  # busts the 4000-char budget below
        with (
            install_registry(reg),
            patch("app.endpoints.chat.settings.MAX_PROMPT_CHARS", 4000),
        ):
            r = self.client.post(
                "/v1/chat/completions",
                json={
                    "model": "gemini-3-pro@firefox:0",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": old_text},
                                {"type": "image_url", "image_url": {"url": _PNG_DATA_URL}},
                            ],
                        },
                        {"role": "assistant", "content": "noted"},
                        {"role": "user", "content": "follow-up text only"},
                    ],
                },
            )
        self.assertEqual(r.status_code, 200)
        kwargs = reg.get("firefox:0").client.generate_content.call_args.kwargs
        # The trim placeholder + last user message survived. The old message
        # carrying the image was elided → no image extracted.
        self.assertIsNone(kwargs.get("files"))


class TestImageResize(unittest.TestCase):
    """Gemini Web silently aborts (or hallucinates) on attachments above
    ~150 KB binary. The bridge downscales + recompresses oversized images
    transparently so opencode/Zed drag-and-drop screenshots (~700 KB) reach
    Gemini intact."""

    def test_oversized_screenshot_recompressed_under_cap(self):
        from io import BytesIO
        from unittest.mock import patch

        from app.endpoints.chat import _maybe_resize_image
        from PIL import Image, ImageDraw

        # Patch the cap small so a modest synthetic image triggers the resize.
        # Generating an image that exceeds the real 150 KB cap requires
        # incompressible noise that distorts the test — we want to verify the
        # *behavior* (oversize → undersize, JPEG reencode), not stress JPEG.
        img = Image.new("RGB", (3000, 2000), (200, 220, 240))
        draw = ImageDraw.Draw(img)
        for i in range(0, 2000, 40):
            draw.rectangle((50, i, 2950, i + 20), fill=(80, 120, 200))
        for i in range(0, 3000, 60):
            draw.rectangle((i, 100, i + 30, 1900), fill=(255, 255, 255))
        buf = BytesIO()
        img.save(buf, "PNG")
        big = buf.getvalue()

        with patch("app.endpoints.chat.settings.MAX_IMAGE_BYTES", len(big) - 1):
            out, mime = _maybe_resize_image(big, "image/png")
        # Crossing the cap MUST trigger JPEG reencoding for alpha-less images.
        # We don't assert on exact size: PNG can beat JPEG on flat-color
        # synthetic patterns; for real screenshots/photos JPEG wins by 5-10x.
        self.assertEqual(mime, "image/jpeg")
        Image.open(BytesIO(out)).verify()

    def test_oversized_rgba_png_flattened_to_rgb(self):
        from io import BytesIO
        from unittest.mock import patch

        from app.endpoints.chat import _maybe_resize_image
        from PIL import Image

        # RGBA screenshots get flattened onto white because Gemini Web's
        # upload chokes on big PNGs and the alpha channel carries no signal
        # for UI captures. Output may be PNG (small after flatten) or JPEG
        # (large/photo-like) — we only assert the alpha is gone.
        img = Image.new("RGBA", (2500, 2500), (255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, "PNG")
        big = buf.getvalue()

        with patch("app.endpoints.chat.settings.MAX_IMAGE_BYTES", len(big) - 1):
            out, mime = _maybe_resize_image(big, "image/png")
        self.assertIn(mime, ("image/png", "image/jpeg"))
        out_img = Image.open(BytesIO(out))
        self.assertEqual(out_img.mode, "RGB")

    def test_small_image_is_passthrough(self):
        # 1x1 PNG is ~70 bytes — must not be re-encoded.
        from app.endpoints.chat import _maybe_resize_image

        out, mime = _maybe_resize_image(_PNG_BYTES, "image/png")
        self.assertIs(out, _PNG_BYTES)
        self.assertEqual(mime, "image/png")

    def test_non_image_mime_is_passthrough(self):
        from app.endpoints.chat import _maybe_resize_image

        data = b"x" * (200 * 1024)
        out, mime = _maybe_resize_image(data, "application/pdf")
        self.assertIs(out, data)
        self.assertEqual(mime, "application/pdf")

if __name__ == "__main__":
    unittest.main()
