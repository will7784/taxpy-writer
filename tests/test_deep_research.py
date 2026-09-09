import json
import shutil
import time
import unittest
import uuid
from pathlib import Path

from legal_library import catalog_entries
from library_import import _complete_text, _metadata, _official_url, normalize_sii_import_versions
from privacy_guard import redact_for_external
from production_store import ProductionStore
from research_agent import _parse_law_window
from official_sources import _Links, _TableLinks, _status, _uaf_title


class DeepResearchFoundationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd() / (".test_deep_research_" + uuid.uuid4().hex)
        self.root.mkdir()
        self.store = ProductionStore(self.root / "state.sqlite3")

    def tearDown(self):
        for attempt in range(5):
            try:
                shutil.rmtree(self.root)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2)

    def test_catalog_is_official_and_coverage_never_invents_percentages(self):
        self.store.register_library_catalog(catalog_entries())
        coverage = self.store.library_coverage()
        self.assertGreaterEqual(len(coverage), 6)
        self.assertTrue(all(item["coverage"] == "no cuantificable" for item in coverage))

    def test_versions_keep_the_prior_text(self):
        source = dict(url="https://bcn.cl/ley", source="BCN", document_type="ley",
                      title="Ley de prueba", legal_status="vigente")
        self.store.upsert_source(**source, body="Versión primera")
        self.store.upsert_source(**source, body="Versión segunda")
        with self.store._conn() as connection:
            rows = connection.execute("SELECT body FROM source_versions WHERE url=? ORDER BY version", (source["url"],)).fetchall()
        self.assertEqual([row[0] for row in rows], ["Versión primera", "Versión segunda"])

    def test_backup_is_restorable(self):
        self.store.upsert_source(url="https://bcn.cl/backup", source="BCN", document_type="ley",
                                 title="Ley", legal_status="vigente", body="texto")
        result = self.store.backup(self.root / "backups")
        self.assertTrue(Path(result["path"]).exists())
        self.assertTrue(result["restoration_verified"])

    def test_full_documents_are_only_loaded_after_ranking(self):
        self.store.upsert_source(url="https://uaf.cl/a", source="UAF", document_type="circular",
                                 title="Circular UAF", legal_status="vigente", body="lavado activos " * 200)
        result = self.store.full_official_documents("lavado activos")
        self.assertEqual(len(result), 1)
        self.assertGreater(len(result[0]["body"]), 1800)

    def test_external_queries_redact_objective_identifiers(self):
        result = redact_for_external("Juan Pérez, RUT 12.345.678-5, correo a@ejemplo.cl y cuenta 12345678")
        self.assertNotIn("12.345.678-5", result.text)
        self.assertNotIn("a@ejemplo.cl", result.text)
        self.assertNotIn("Juan Pérez", result.text)
        self.assertGreaterEqual(result.replacements, 4)

    def test_public_legal_name_is_not_redacted(self):
        result = redact_for_external("Código Tributario artículo 2")
        self.assertIn("Código Tributario", result.text)

    def test_expired_local_copy_is_marked_historical(self):
        status, end = _parse_law_window("Fin Vigencia: 24-OCT-2025")
        self.assertEqual(status, "version_historica")
        self.assertEqual(end, "24-OCT-2025")

    def test_historical_copy_can_be_applicable_to_the_facts_date(self):
        status, _ = _parse_law_window("Fin Vigencia: 24-OCT-2025", "2025-01-10")
        self.assertEqual(status, "version_aplicable_a_fecha_hechos")

    def test_acceptance_suite_has_the_agreed_thirty_cases(self):
        fixture = Path(__file__).parent / "fixtures" / "research_acceptance_cases.json"
        cases = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(len(cases), 30)
        self.assertEqual({case["area"] for case in cases}, {"tributaria", "civil_comercial", "lavado", "vigencia_evidencia"})

    def test_released_reservation_does_not_consume_run_budget(self):
        run = self.store.create_research_run(query="consulta", budget_usd=1)
        reservation = self.store.reserve_research_usage(run["id"], purpose="prueba", provider="local", estimate_usd=.75)
        self.store.release_research_usage(reservation)
        second = self.store.reserve_research_usage(run["id"], purpose="prueba", provider="local", estimate_usd=.75)
        self.assertTrue(second)

    def test_clarification_is_kept_with_its_run(self):
        run = self.store.create_research_run(query="consulta inicial")
        self.store.append_research_clarification(run["id"], "el pago fue honorario fijo")
        found = self.store.research_run(run["id"])
        self.assertIn("Aclaración posterior", found["query"])
        self.assertEqual(found["messages"][0]["content"], "el pago fue honorario fijo")

    def test_later_portal_error_does_not_erase_indexed_coverage(self):
        self.store.register_library_catalog([{"key": "uaf", "source": "UAF", "document_type": "circular",
                                              "title": "Circular", "url": "https://uaf.cl/circular", "collection": "uaf"}])
        self.store.record_library_attempt("uaf", downloaded=True, indexed=True)
        self.store.record_library_attempt("uaf", downloaded=False, indexed=False, error="portal temporalmente inaccesible")
        coverage = self.store.library_coverage()[0]
        self.assertEqual(coverage["indexed"], 1)
        self.assertEqual(coverage["pending"], 0)

    def test_existing_acj_file_keeps_its_id_and_official_portal(self):
        metadata = _metadata("## Metadata\n- source_type: jurisprudencia_sii\n- jurisprudencia_id: 14020\n\n## Contenido")
        url, location = _official_url(metadata, "contenido")
        self.assertIn("www4.sii.cl", url)
        self.assertIn("14020", location)

    def test_circular_index_card_is_not_treated_as_full_text(self):
        self.assertFalse(_complete_text("## Contenido\nTítulo y resumen\n\n## Fuente", "circular_sii"))

    def test_one_source_can_keep_multiple_article_relationships(self):
        url = "https://sii.cl/acj#1"
        self.store.upsert_source(url=url, source="SII/ACJ", document_type="fallo",
                                 title="Fallo", legal_status="vigente", body="texto")
        self.store.add_source_relation(url, relation_type="relacionado_con", target="Código Tributario; artículo 1")
        self.store.add_source_relation(url, relation_type="relacionado_con", target="Código Tributario; artículo 2")
        self.assertEqual(len(self.store.source_relations(url)), 2)

    def test_artificial_acj_metadata_versions_are_normalized(self):
        url = "https://www4.sii.cl/acjui/internet/#pronunciamiento-1"
        common = "# Fallo\n\n## Contenido\nTexto íntegro de la sentencia."
        self.store.upsert_source(url=url, source="SII/ACJ", document_type="fallo", title="Fallo",
                                 legal_status="vigente", body=common + "\n\n## Metadata\n- articulo_nombre: 1")
        self.store.upsert_source(url=url, source="SII/ACJ", document_type="fallo", title="Fallo",
                                 legal_status="vigente", body=common + "\n\n## Metadata\n- articulo_nombre: 2")
        result = normalize_sii_import_versions(production_store=self.store)
        self.assertEqual(result["removed_artificial_versions"], 1)

    def test_uaf_link_discovery_keeps_pdf_and_its_label(self):
        parser = _Links()
        parser.feed('<a href="/media/documentos/Circular_62.pdf" title="Circular 62">Descargar</a>')
        self.assertEqual(parser.links, [("/media/documentos/Circular_62.pdf", "Circular 62")])
        self.assertEqual(_uaf_title("Descargar", "https://www.uaf.cl/media/Circular_62.pdf", "Circular"), "Circular 62.pdf")

    def test_document_mentioning_repeal_is_not_itself_marked_repealed(self):
        self.assertEqual(_status("circular_uaf", "La Circular anterior fue derogada."), "vigente")

    def test_uaf_sanction_link_retains_resolution_year_from_its_row(self):
        parser = _TableLinks()
        parser.feed('<tr><td>003-2024</td><td>29-04-2025</td><td><a href="/r.pdf">Descargar</a></td></tr>')
        self.assertEqual(parser.links, [("/r.pdf", "Descargar", "003-2024 29-04-2025 Descargar")])


if __name__ == "__main__":
    unittest.main()
