"""Tests del endpoint de ingesta automática (/api/ingest/{cliente})."""

import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import config
import ingest
import web_server


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / (".test_ingest_" + uuid.uuid4().hex)
        self.root.mkdir()
        self.client = TestClient(web_server.app)
        # Redirigir get_cliente_dir a un dir temporal (no tocar el vault real).
        self.dir_patch = patch("ingest.get_cliente_dir", return_value=self.root)
        self.dir_patch.start()

    def tearDown(self):
        self.client.close()
        self.dir_patch.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def _post(self, **kwargs):
        return self.client.post("/api/ingest/demo", **kwargs)

    def test_ingest_without_token_accepts_file(self):
        # Sin INGEST_TOKEN configurado => dev abierto. auto=0 -> no dispara LLM.
        res = self._post(files={"material": ("fax/nota.txt", b"Documento")}, data={"auto": "0"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual((self.root / "entrada/fax__nota.txt").read_bytes(), b"Documento")

    def test_ingest_requires_token_when_set(self):
        with patch.object(config, "INGEST_TOKEN", "secreto"):
            no_auth = self._post(files={"material": ("nota.txt", b"x")}, data={"auto": "0"})
            self.assertEqual(no_auth.status_code, 401)
            ok = self._post(files={"material": ("nota.txt", b"x")}, data={"auto": "0"},
                            headers={"X-API-Key": "secreto"})
            self.assertEqual(ok.status_code, 200)

    def test_ingest_rejects_bad_extension(self):
        res = self._post(files={"material": ("malo.exe", b"x")}, data={"auto": "0"})
        self.assertEqual(res.status_code, 400)

    def test_ingest_requires_file_or_url(self):
        # multipart sin archivo ni url -> 400
        res = self._post(data={"auto": "0"})
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
