"""Tests del gateway MCP (mcp_server.py).

Verifica que las tools del backend quedan registradas para el agente/harness
externo y que el middleware de autenticación bearer funciona (rechaza tokens
inválidos/ausentes y deja pasar el correcto).
"""

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

import config
import mcp_server


EXPECTED_TOOLS = {
    "listar_clientes", "listar_casos", "procesar_caso", "buscar_jurisprudencia",
    "buscar_notas", "obtener_norma", "investigar", "escribir_informe", "publicar_notion",
}


class McpServerTests(unittest.TestCase):
    def test_expected_tools_registered(self):
        tools = asyncio.run(mcp_server.mcp.list_tools())
        names = {t.name for t in tools}
        self.assertTrue(EXPECTED_TOOLS.issubset(names),
                        f"Faltan tools: {EXPECTED_TOOLS - names}")

    def test_mount_path_returns_prefix(self):
        mount_path, sub_app = mcp_server.build_mcp_app()
        self.assertTrue(mount_path.startswith("/"))
        self.assertIsNotNone(sub_app)

    def test_auth_rejects_missing_or_wrong_token(self):
        async def inner(scope, receive, send):
            await JSONResponse({"ok": True})(scope, receive, send)

        mw = mcp_server._BearerAuthMiddleware(inner, token="secreto-test")
        app = FastAPI()

        async def dummy(scope, receive, send):
            await mw(scope, receive, send)

        app.mount("/x", dummy)
        client = TestClient(app)

        self.assertEqual(client.get("/x").status_code, 401)
        self.assertEqual(
            client.get("/x", headers={"Authorization": "Bearer otro"}).status_code,
            401,
        )

    def test_auth_accepts_valid_token(self):
        seen = []

        async def inner(scope, receive, send):
            seen.append(1)
            await JSONResponse({"ok": True})(scope, receive, send)

        mw = mcp_server._BearerAuthMiddleware(inner, token="secreto-test")
        app = FastAPI()

        async def dummy(scope, receive, send):
            await mw(scope, receive, send)

        app.mount("/x", dummy)
        client = TestClient(app)

        resp = client.get("/x", headers={"Authorization": "Bearer secreto-test"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(seen)

    def test_auth_disabled_when_no_token(self):
        async def inner(scope, receive, send):
            await JSONResponse({"ok": True})(scope, receive, send)

        mw = mcp_server._BearerAuthMiddleware(inner, token="")
        app = FastAPI()

        async def dummy(scope, receive, send):
            await mw(scope, receive, send)

        app.mount("/x", dummy)
        client = TestClient(app)

        # Sin token configurado, no se exige auth (solo dev).
        self.assertEqual(client.get("/x").status_code, 200)


if __name__ == "__main__":
    unittest.main()
