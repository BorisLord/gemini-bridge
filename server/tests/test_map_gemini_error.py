"""`_map_gemini_error` translates `gemini_webapi.exceptions` (and the
generic `APIError` fallbacks) into OpenAI-shape HTTPException status codes
the chat handler raises. Pin the mapping so an upstream rename or a new
exception class can't silently downgrade a 429 to a 502."""
import unittest

from app.endpoints.chat import _map_gemini_error
from gemini_webapi.exceptions import APIError, ModelInvalid, TemporarilyBlocked, UsageLimitExceeded
from gemini_webapi.exceptions import TimeoutError as GeminiTimeoutError


class TestMapGeminiErrorTyped(unittest.TestCase):
    # `AuthError` is intentionally absent: the lib raises it only during
    # `client.init()` / `_1PSIDTS` rotation — never from `generate_content`,
    # so it never reaches `_map_gemini_error` from the chat path.

    def test_timeout_to_504(self):
        self.assertEqual(_map_gemini_error(GeminiTimeoutError("zombie")).status_code, 504)

    def test_usage_limit_to_429(self):
        self.assertEqual(_map_gemini_error(UsageLimitExceeded("quota")).status_code, 429)

    def test_temporarily_blocked_to_429(self):
        self.assertEqual(_map_gemini_error(TemporarilyBlocked("captcha")).status_code, 429)

    def test_model_invalid_to_400(self):
        self.assertEqual(_map_gemini_error(ModelInvalid("bad-model")).status_code, 400)


class TestMapGeminiErrorStringFallback(unittest.TestCase):
    """Untyped `APIError` carries the HTTP status in the message — the lib
    doesn't promote every code to a typed subclass. Keep these regressions."""

    def test_status_401_string_to_401(self):
        self.assertEqual(_map_gemini_error(APIError("Status: 401 Unauthorized")).status_code, 401)

    def test_status_403_string_to_401(self):
        self.assertEqual(_map_gemini_error(APIError("Status: 403 Forbidden")).status_code, 401)

    def test_status_302_sorry_to_429(self):
        self.assertEqual(
            _map_gemini_error(APIError("Status: 302 redirect to /sorry/index")).status_code, 429
        )

    def test_status_429_string_to_429(self):
        self.assertEqual(_map_gemini_error(APIError("Status: 429 too many requests")).status_code, 429)

    def test_quota_keyword_to_429(self):
        self.assertEqual(_map_gemini_error(APIError("daily quota reached")).status_code, 429)

    def test_unknown_to_502(self):
        self.assertEqual(_map_gemini_error(APIError("something weird happened")).status_code, 502)

    def test_image_generation_error_to_502(self):
        # Bridge doesn't expose image gen, but a Gem with image capabilities can
        # surface this. Falling through to 502 is intentional.
        from gemini_webapi.exceptions import ImageGenerationError
        self.assertEqual(_map_gemini_error(ImageGenerationError("nope")).status_code, 502)


if __name__ == "__main__":
    unittest.main()
