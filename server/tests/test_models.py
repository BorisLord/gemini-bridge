import unittest

from _helpers import install_registry, seeded_registry
from app.endpoints.chat import GEMINI_MODEL_IDS
from app.main import app
from litestar.testing import TestClient


class TestListModels(unittest.TestCase):
    """`/v1/models` always returns `<model>@<id>` variants — never bare names —
    because the chat endpoint rejects un-routed models. Listing bare names
    would mislead picker UIs into showing unrouteable entries."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_returns_openai_envelope(self):
        with install_registry(seeded_registry()):
            r = self.client.get("/v1/models")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["object"], "list")
        self.assertIsInstance(body["data"], list)

    def test_lists_gemini_ids_with_account_suffix(self):
        with install_registry(seeded_registry()):
            body = self.client.get("/v1/models").json()
        ids = {m["id"] for m in body["data"]}
        # Every base model is exposed only as `<model>@<account_id>`.
        for base in GEMINI_MODEL_IDS:
            self.assertIn(f"{base}@firefox:0", ids)
            self.assertNotIn(base, ids)

    def test_each_item_owned_by_gemini_bridge(self):
        with install_registry(seeded_registry()):
            body = self.client.get("/v1/models").json()
        owners = {m["owned_by"] for m in body["data"]}
        self.assertEqual(owners, {"gemini-bridge"})


if __name__ == "__main__":
    unittest.main()
