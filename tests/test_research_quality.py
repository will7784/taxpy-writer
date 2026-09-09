import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from research_quality import (Claim, Findings, ResearchPlan, apply_findings,
                              grounded_claims, relevant, render_applied_report)
from research_agent import run_research

TEXT = ('La norma permite rebajar las sumas efectivamente pagadas por el uso o goce del inmueble. '
        'El retiro corresponde solo a la diferencia positiva entre la presunción y los pagos acreditados.')
SOURCE = {'title': 'Oficio SII — arriendo a accionista', 'url': 'https://www.sii.cl/oficio.htm',
          'official': True, 'content': TEXT, 'full_text': TEXT, 'fetched': True}


class QualityTests(unittest.IsolatedAsyncioTestCase):
    def test_una_and_generic_homepages_are_rejected(self):
        for url in ['https://una.com/', 'https://unausa.org/', 'https://instagram.com/una.usa']:
            self.assertFalse(relevant({'url': url, 'title': 'UNA', 'content': 'United Nations Association'}, ['arriendo', 'accionista']))
        self.assertFalse(relevant({**SOURCE, 'url': 'https://sii.cl/'}, ['arriendo']))
        self.assertTrue(relevant(SOURCE, ['arriendo', 'accionista']))

    def test_official_but_generic_or_off_topic_documents_are_rejected(self):
        generic_news = {**SOURCE, 'title': 'Servicio de Impuestos Internos - Chile'}
        old_supplement = {**SOURCE, 'title': 'SEGUNDA PARTE - LINEA 3'}
        focused_office = {**SOURCE, 'title': 'Oficio SII N° 1787, de 2012 — arriendo a accionista'}
        self.assertFalse(relevant(generic_news, ['arriendo', 'accionista', 'uso goce']))
        self.assertFalse(relevant(old_supplement, ['arriendo', 'accionista', 'uso goce']))
        self.assertTrue(relevant(focused_office, ['arriendo', 'accionista', 'uso goce']))

    def test_search_snippet_cannot_keep_an_off_topic_document_after_download(self):
        misleading = {**SOURCE, 'title': 'Oficio SII N° 999', 'content': 'arriendo accionista uso goce',
                       'full_text': 'Oficio sobre IVA de un transporte público.', 'fetched': True}
        self.assertTrue(relevant(misleading, ['arriendo', 'accionista']))
        self.assertFalse(relevant(misleading, ['arriendo', 'accionista'], full_document=True))

    def test_unrelated_mentions_of_lease_and_shareholder_are_not_a_case_match(self):
        distant = {**SOURCE, 'title': 'Oficio SII N° 999', 'fetched': True,
                   'full_text': 'Arrendamiento de maquinaria. ' + ('norma tributaria ' * 200)
                   + 'Accionista de una compañía distinta.'}
        self.assertFalse(relevant(distant, ['arriendo', 'accionista'], full_document=True))

    def test_invented_quotes_and_unfetched_sources_are_rejected(self):
        claim = Claim(heading='Pago', statement='Las sumas pagadas se descuentan.', source_id=1, quote=TEXT)
        self.assertEqual(len(grounded_claims(Findings(claims=[claim], missing=[]), [SOURCE])), 1)
        self.assertEqual(grounded_claims(Findings(claims=[claim], missing=[]), [{**SOURCE, 'fetched': False}]), [])
        claim.quote = 'El pago a valor de mercado exime automáticamente de toda tributación.'
        self.assertEqual(grounded_claims(Findings(claims=[claim], missing=[]), [SOURCE]), [])

    def test_secondary_headline_cannot_support_a_legal_conclusion(self):
        claim = Claim(heading='Conclusión', statement='El arriendo al accionista es gasto tributario aceptado.',
                      source_id=1, quote=TEXT)
        self.assertEqual(grounded_claims(Findings(claims=[claim], missing=[]),
                         [{**SOURCE, 'official': False}]), [])

    def test_short_or_off_topic_quote_cannot_support_a_claim(self):
        claim = Claim(heading='Conclusión', statement='El arrendamiento exige valor de mercado y contrato.',
                      source_id=1, quote='El contrato de arriendo existe.')
        self.assertEqual(grounded_claims(Findings(claims=[claim], missing=[]), [SOURCE]), [])

    async def test_applied_report_preserves_and_renders_all_audited_claim_indexes(self):
        applied = {
            'direct_answer': 'La respuesta aplicada tiene respaldo comprobado.',
            'facts_considered': ['Existe un antecedente documentado.'],
            'assumptions': [],
            'conclusions': [{
                'status': 'respaldada',
                'text': 'La conclusión se sustenta en los antecedentes revisados.',
                'claim_indexes': list(range(8)),
            }],
            'client_risks': [], 'advisor_risks': [],
            'counterarguments_and_limits': [], 'practical_actions': [], 'missing': [],
        }
        claims = [Claim(heading=f'Conclusión {i}', statement='Antecedente comprobado.',
                        source_id=i + 1, quote=TEXT) for i in range(8)]
        sources = [{**SOURCE, 'title': f'Oficio SII N° {i}', 'url': f'https://sii.cl/{i}'}
                   for i in range(8)]
        model = MagicMock(chat_completion=AsyncMock(return_value=json.dumps(applied)))

        report = await apply_findings(model, 'Consulta tributaria', claims, [], sources)
        reply = render_applied_report(report, claims, sources, missing=['Confirmar fecha del hecho.'])

        self.assertEqual(model.chat_completion.await_count, 1)
        self.assertEqual(report.conclusions[0].claim_indexes, list(range(8)))
        self.assertIn('ClaudIA — Informe de investigación aplicada', reply)
        self.assertIn('Confirmar fecha del hecho.', reply)
        self.assertIn('Oficio SII N° 7', reply)

    async def test_only_reviewed_findings_are_saved(self):
        responses = [
            {'queries': ['arriendo inmueble socio SII', 'uso goce inmueble accionista'], 'concepts': ['arriendo', 'accionista']},
            {'claims': [{'heading': 'Pago', 'statement': 'El pasaje permite descontar pagos.', 'source_id': 1, 'quote': TEXT}], 'missing': ['Verificar normativa actual.']},
            {'supported_claims': [0]},
        ]
        with patch('research_quality.reference_sources', return_value=[]), patch('llm_client.LLMClient') as cls, \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=[SOURCE])), \
             patch('research_agent._scrape_result', AsyncMock(return_value=SOURCE)), \
             patch('research_agent.save_research_to_vault', return_value={'archivos': ['nota'], 'resumen': 'resumen'}) as save:
            cls.return_value.chat_completion = AsyncMock(side_effect=[json.dumps(r) for r in responses])
            cls.return_value.aclose = AsyncMock()
            result = await run_research('Una SpA arrienda al accionista')
        self.assertEqual(result['quality'], 'supported_findings')
        self.assertEqual(result['evidence'][0]['quote'], TEXT)
        self.assertEqual(save.call_args.args[1], [SOURCE])

    async def test_irrelevant_results_never_reach_writer_or_vault(self):
        plan = {'queries': ['arriendo inmueble socio SII', 'uso goce inmueble accionista'], 'concepts': ['arriendo', 'accionista']}
        with patch('research_quality.reference_sources', return_value=[]), patch('llm_client.LLMClient') as cls, \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=[{'url': 'https://una.com/', 'title': 'UNA', 'content': 'Group purchasing organization'}])), \
             patch('research_agent.save_research_to_vault') as save:
            cls.return_value.chat_completion = AsyncMock(return_value=json.dumps(plan))
            cls.return_value.aclose = AsyncMock()
            result = await run_research('Una empresa spa puede arrendar a su accionista')
        self.assertEqual(result['quality'], 'insufficient_evidence')
        self.assertEqual(cls.return_value.chat_completion.await_count, 1)
        save.assert_not_called()

    async def test_semantic_rejection_prevents_publication(self):
        responses = [
            {'queries': ['arriendo inmueble socio SII', 'uso goce inmueble accionista'], 'concepts': ['arriendo', 'accionista']},
            {'claims': [{'heading': 'Pago', 'statement': 'El pago elimina todo impuesto.', 'source_id': 1, 'quote': TEXT}], 'missing': []},
            {'supported_claims': []},
        ]
        with patch('research_quality.reference_sources', return_value=[]), patch('llm_client.LLMClient') as cls, \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=[SOURCE])), \
             patch('research_agent._scrape_result', AsyncMock(return_value=SOURCE)), \
             patch('research_agent.save_research_to_vault') as save:
            cls.return_value.chat_completion = AsyncMock(side_effect=[json.dumps(r) for r in responses])
            cls.return_value.aclose = AsyncMock()
            result = await run_research('Arriendo al accionista')
        self.assertEqual(result['quality'], 'insufficient_evidence')
        self.assertNotIn('El pago elimina todo impuesto.', result['reply'])
        save.assert_not_called()

    async def test_every_relevant_source_reaches_the_evidence_stage(self):
        sources = [{**SOURCE, 'url': f'https://www.sii.cl/oficio-{i}.htm'} for i in range(60)]
        captured = []

        async def inspect_all(model, question, research_sources):
            captured.extend(research_sources)
            return [Claim(heading='Pago', statement='Las sumas pagadas se descuentan.', source_id=60, quote=TEXT)], []

        with patch('research_quality.reference_sources', return_value=[]), \
             patch('research_quality.plan_research', AsyncMock(return_value=ResearchPlan(queries=['uno', 'dos'], concepts=['arriendo', 'accionista']))), \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=sources)), \
             patch('research_agent._scrape_result', AsyncMock(side_effect=sources)), \
             patch('research_quality.substantiate', side_effect=inspect_all), \
             patch('research_agent.save_research_to_vault', return_value={'archivos': [], 'resumen': ''}):
            from llm_client import LLMClient
            with patch('llm_client.LLMClient') as cls:
                cls.return_value.aclose = AsyncMock()
                result = await run_research('Una SpA arrienda al accionista')
        self.assertTrue(result['has_evidence'])
        self.assertEqual(len(captured), 60)

    async def test_quota_failure_continues_with_configured_deepseek(self):
        plan = {'queries': ['arriendo inmueble socio SII', 'uso goce inmueble accionista'], 'concepts': ['arriendo', 'accionista']}
        findings = {'claims': [{'heading': 'Pago', 'statement': TEXT, 'source_id': 1, 'quote': TEXT}], 'missing': []}
        exhausted = MagicMock(provider='openai', chat_completion=AsyncMock(side_effect=RuntimeError('insufficient_quota')), aclose=AsyncMock())
        fallback = MagicMock(provider='deepseek', chat_completion=AsyncMock(side_effect=[
            json.dumps(plan), json.dumps(findings), json.dumps({'supported_claims': [0]})]), aclose=AsyncMock())
        with patch('research_quality.reference_sources', return_value=[]), \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=[SOURCE])), \
             patch('research_agent._scrape_result', AsyncMock(return_value=SOURCE)), \
             patch('research_agent.save_research_to_vault', return_value={'archivos': [], 'resumen': ''}), \
             patch('config.RESEARCH_LLM_PROVIDER', ''), \
             patch('llm_client.LLMClient', side_effect=[exhausted, fallback]) as factory:
            result = await run_research('Una SpA arrienda al accionista')
        self.assertTrue(result['has_evidence'])
        self.assertEqual([call.kwargs['provider'] for call in factory.call_args_list], ['openai', 'deepseek'])
        exhausted.aclose.assert_awaited_once()
        fallback.aclose.assert_awaited_once()
