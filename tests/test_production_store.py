import uuid
import unittest
import tracemalloc
from pathlib import Path
from unittest.mock import patch

from production_store import ProductionStore
from official_sources import official_evidence, should_check_official


class ProductionStoreTests(unittest.TestCase):
    def setUp(self):
        self.db_path = Path.cwd() / f".test_state_{uuid.uuid4().hex}.sqlite3"
        self.store = ProductionStore(self.db_path)

    def tearDown(self):
        if self.db_path.exists():
            self.db_path.unlink()

    def test_source_changes_are_hashed_and_idempotent(self):
        first = self.store.upsert_source(url="https://bcn.cl/x", source="BCN", document_type="ley",
            title="Ley", legal_status="vigente", body="Articulo 1")
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.upsert_source(url="https://bcn.cl/x", source="BCN", document_type="ley",
            title="Ley", legal_status="vigente", body="Articulo 1"))
        second = self.store.upsert_source(url="https://bcn.cl/x", source="BCN", document_type="ley",
            title="Ley", legal_status="modificada", body="Articulo 1 modificado")
        self.assertIsNotNone(second)
        self.assertEqual(len(self.store.latest_changes()), 2)

    def test_case_is_isolated_by_client(self):
        case = self.store.create_case("cliente-a", "Fiscalizacion 2026")
        self.assertIsNotNone(self.store.case_for(case["id"], "cliente-a"))
        self.assertIsNone(self.store.case_for(case["id"], "cliente-b"))

    def test_invalid_legal_status_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.upsert_source(url="https://x", source="x", document_type="ley", title="x",
                legal_status="inventado", body="texto")

    def test_reform_query_requires_official_check(self):
        self.assertTrue(should_check_official("¿Cuál es el estado de la nueva reforma tributaria?"))

    def test_source_status_and_effective_date_change_without_new_text(self):
        source = dict(url="https://bcn.cl/test", source="BCN", document_type="ley",
                      title="Norma", legal_status="publicada_pendiente_vigencia", body="Texto identico")
        self.store.upsert_source(**source)
        source.update(legal_status="vigente", effective_date="2026-09-07")
        self.assertIsNotNone(self.store.upsert_source(**source))
        self.assertIsNone(self.store.upsert_source(**source))
        found = self.store.search_official("identico")[0]
        self.assertEqual(found["legal_status"], "vigente")
        self.assertEqual(found["effective_date"], "2026-09-07")
        self.assertEqual(len(self.store.latest_changes()), 2)

    def test_fetch_metadata_refresh_does_not_create_change(self):
        source = dict(url="https://bcn.cl/test", source="BCN", document_type="ley",
                      title="Norma", legal_status="vigente", body="Texto")
        self.store.upsert_source(**source, metadata={"content_type": "text/html"})
        self.assertIsNone(self.store.upsert_source(**source, metadata={"content_type": "text/plain"}))
        with self.store._conn() as conn:
            row = conn.execute("SELECT metadata_json FROM official_sources").fetchone()
        self.assertIn("text/plain", row[0])
        self.assertEqual(len(self.store.latest_changes()), 1)

    def test_search_ranking_limits_and_repealed_sources(self):
        for name, count, status in [("a", 1, "vigente"), ("b", 3, "vigente"),
                                    ("c", 2, "vigente"), ("d", 9, "derogada")]:
            self.store.upsert_source(url=f"https://bcn.cl/{name}", source="BCN", document_type="ley",
                title=name, legal_status=status, body="impuesto " * count)
        results = self.store.search_official("impuesto", limit=2)
        self.assertEqual([r["title"] for r in results], ["b", "c"])
        self.assertEqual(self.store.search_official("impuesto", limit=0), [])
        self.assertEqual(self.store.search_official("impuesto", limit=-1), [])
        self.assertEqual(self.store.search_official("de la"), [])
        self.assertEqual(self.store.search_official("inexistente"), [])

    def test_evidence_contains_matching_passage_without_full_document(self):
        self.store.upsert_source(url="https://bcn.cl/test", source="BCN", document_type="ley",
            title="Norma", legal_status="vigente", body="Introduccion. " * 500 + "credito fiscal aplicable")
        with patch("official_sources.store", self.store):
            text, docs = official_evidence("credito fiscal")
        self.assertIn("credito fiscal aplicable", text)
        self.assertIn("https://bcn.cl/test", text)
        self.assertIn("content_hash", docs[0])
        self.assertNotIn("body", docs[0])
        self.assertLessEqual(len(docs[0]["extract"]), 1800)

    def test_search_memory_is_bounded_for_large_corpus(self):
        # 8 MB de corpus: la consulta debe transferir solo una ventana por fila.
        body = "impuesto " + "x" * 200000
        with self.store._conn() as conn:
            conn.executemany("""INSERT INTO official_sources
                (url,source,document_type,title,legal_status,body,content_hash,retrieved_at)
                VALUES (?,'BCN','ley','Norma','vigente',?,'hash','2026-09-07')""",
                [(f"https://bcn.cl/{i}", body) for i in range(40)])
        tracemalloc.start()
        try:
            results = self.store.search_official("impuesto", limit=6)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(len(results), 6)
        self.assertLess(peak, 2_000_000)


if __name__ == "__main__":
    unittest.main()
