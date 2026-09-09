"""Tests de la salida a Notion (notion_writer.py + endpoint /api/notion/publish)."""

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import config
import notion_writer
import web_server


class _FakeResp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self.text = "{}"
        self._data = data or {}

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None, headers=None):
        return self._resp

    async def patch(self, url, json=None, headers=None):
        return _FakeResp(200, {})


class NotionWriterTests(unittest.TestCase):
    def test_make_blocks_converts_markdown(self):
        blocks = notion_writer.make_blocks("# H1\n## H2\n### H3\n- viñeta\n1. numerado\n\nTexto.")
        types = [b["type"] for b in blocks]
        self.assertIn("heading_1", types)
        self.assertIn("heading_2", types)
        self.assertIn("heading_3", types)
        self.assertIn("bulleted_list_item", types)
        self.assertIn("numbered_list_item", types)
        self.assertIn("paragraph", types)

    def test_publish_page_success(self):
        resp = _FakeResp(200, {"id": "pg-1", "url": "https://notion.so/pg-1"})
        with patch.object(config, "NOTION_API_KEY", "secret"), patch.object(
            config, "NOTION_DATABASE_ID", "db-1"
        ), patch.object(notion_writer.httpx, "AsyncClient", return_value=_FakeClient(resp)):
            url = asyncio.run(notion_writer.publish_page("Título", "# Hola"))
        self.assertEqual(url, "https://notion.so/pg-1")

    def test_publish_page_requires_credentials(self):
        with patch.object(config, "NOTION_API_KEY", ""), patch.object(config, "NOTION_DATABASE_ID", "db-1"):
            with self.assertRaises(ValueError):
                asyncio.run(notion_writer.publish_page("Título", "C"))


class NotionEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(web_server.app)

    def tearDown(self):
        self.client.close()

    def test_requires_token_when_set(self):
        with patch.object(config, "INGEST_TOKEN", "secreto"):
            resp = self.client.post("/api/notion/publish", json={"titulo": "T", "contenido": "C"})
        self.assertEqual(resp.status_code, 401)

    def test_publishes_when_allowed(self):
        with patch.object(config, "INGEST_TOKEN", ""), patch.object(
            notion_writer, "publish_page", AsyncMock(return_value="https://notion.so/x")
        ):
            resp = self.client.post("/api/notion/publish", json={"titulo": "T", "contenido": "C"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["url"], "https://notion.so/x")

    def test_requires_title_and_content(self):
        with patch.object(config, "INGEST_TOKEN", ""):
            resp = self.client.post("/api/notion/publish", json={"titulo": "", "contenido": ""})
        self.assertEqual(resp.status_code, 400)


if __name__ == "__main__":
    unittest.main()
