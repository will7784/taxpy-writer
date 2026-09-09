import unittest
import uuid
import shutil
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import live_lookup
import research_agent
import web_server


class SearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_country_is_context_not_a_result_filter(self):
        client = AsyncMock()
        client.post.return_value = httpx.Response(200, json={"results": []},
            request=httpx.Request("POST", live_lookup.TAVILY_URL))
        await live_lookup._tavily_search(client, "arriendo accionista", include_domains=None, max_results=3)
        payload = client.post.call_args.kwargs["json"]
        self.assertNotIn("country", payload)
        self.assertIn("chile", payload["query"])

    async def test_tls_verification_stays_enabled(self):
        import ssl
        from http_security import tls_context
        context = tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    async def test_secondary_sources_are_added_and_labeled(self):
        official = {"title": "SII", "url": "https://www.sii.cl/a", "content": "Norma"}
        secondary = {"title": "Comentario", "url": "https://example.org/a", "content": "Comentario"}
        with patch.object(live_lookup.config, "TAVILY_API_KEY", "test"), patch.object(
            live_lookup, "_tavily_search", AsyncMock(side_effect=[[official], [official, secondary]])
        ) as search:
            results = await live_lookup.search_live("consulta", strict=True)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["official"])
        self.assertFalse(results[1]["official"])
        self.assertIsNone(search.call_args.kwargs["include_domains"])

    async def test_missing_key_is_visible(self):
        with patch.object(live_lookup.config, "TAVILY_API_KEY", ""):
            with self.assertRaises(live_lookup.SearchUnavailable):
                await live_lookup.search_live("consulta", strict=True)

    async def test_search_http_error_is_not_empty_results(self):
        response = httpx.Response(401, request=httpx.Request("POST", live_lookup.TAVILY_URL))
        client = AsyncMock()
        client.post.return_value = response
        with self.assertRaisesRegex(live_lookup.SearchUnavailable, "HTTP 401"):
            await live_lookup._tavily_search(client, "consulta", include_domains=None, max_results=3)



class PanelTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / (".test_panel_" + uuid.uuid4().hex)
        self.root.mkdir()
        self.client = TestClient(web_server.app)
        self.auth = patch.object(web_server, "_is_authenticated", return_value=True)
        self.auth.start()
        self.path = patch.object(web_server, "get_cliente_dir", return_value=self.root)
        self.path.start()

    def tearDown(self):
        self.client.close()
        self.path.stop()
        self.auth.stop()
        self.assertEqual(self.root.resolve().parent, Path.cwd().resolve())
        for attempt in range(5):
            try:
                shutil.rmtree(self.root)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)

    def test_folder_upload_duplicate_and_traversal(self):
        url = "/api/cliente/demo/entrada/upload"
        response = self.client.post(url, files={"material": ("Carpeta/sub/contrato.txt", b"Documento")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual((self.root / "entrada/Carpeta__sub__contrato.txt").read_bytes(), b"Documento")
        self.assertEqual(self.client.post(url, files={"material": ("Carpeta/sub/contrato.txt", b"Otro")}).status_code, 409)
        self.assertEqual(self.client.post(url, files={"material": ("../fuera.txt", b"Otro")}).status_code, 400)
        self.assertEqual(self.client.post(url, files={"material": ("script.exe", b"Otro")}).status_code, 400)

    def test_folder_upload_accepts_images_for_ocr(self):
        url = "/api/cliente/demo/entrada/upload"
        # El Co-Work acepta imagenes (se OCRearan al procesar).
        response = self.client.post(url, files={"material": ("Carpeta/foto.png", b"\\x89PNG\\r\\n\\x1a\\n")})
        self.assertEqual(response.status_code, 200)
        self.assertTrue((self.root / "entrada/Carpeta__foto.png").exists())

    def test_upload_requires_login(self):
        with patch.object(web_server, "_is_authenticated", return_value=False):
            response = self.client.post("/api/cliente/demo/entrada/upload", files={"material": ("a.txt", b"a")})
        self.assertEqual(response.status_code, 401)

    def test_search_failure_is_displayable(self):
        with patch.object(research_agent, "run_research", AsyncMock(side_effect=live_lookup.SearchUnavailable("Buscador no disponible"))):
            response = self.client.post("/api/research", data={"query": "consulta"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "Buscador no disponible")

    def test_research_page_renders(self):
        with patch.object(web_server, "list_clientes", return_value=[]):
            response = self.client.get("/research")
        self.assertEqual(response.status_code, 200)
        self.assertIn("res-form", response.text)

    def test_empty_document_records_visible_error(self):
        import cowork_manager
        document = self.root / "vacio.txt"
        document.write_text("", encoding="utf-8")
        with patch.object(cowork_manager, "get_cliente_dir", return_value=self.root), \
             patch.object(cowork_manager, "escanear_entrada", return_value=[document]):
            import asyncio
            jobs = asyncio.run(cowork_manager.procesar_entrada_cliente("demo"))
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].estado, "error")
        self.assertIn("No se extrajo texto", jobs[0].error_msg)
        self.assertTrue(document.exists())


if __name__ == "__main__":
    unittest.main()
