import unittest

from app.endpoints.chat import GEMINI_MODEL_IDS
from app.main import app
from litestar.testing import TestClient


class TestListModels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_returns_openai_envelope(self):
        r = self.client.get("/v1/models")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["object"], "list")
        self.assertIsInstance(body["data"], list)

    def test_lists_all_gemini_ids(self):
        body = self.client.get("/v1/models").json()
        ids = {m["id"] for m in body["data"]}
        self.assertEqual(ids, set(GEMINI_MODEL_IDS))

    def test_each_item_owned_by_gemini_bridge(self):
        body = self.client.get("/v1/models").json()
        owners = {m["owned_by"] for m in body["data"]}
        self.assertEqual(owners, {"gemini-bridge"})


if __name__ == "__main__":
    unittest.main()
