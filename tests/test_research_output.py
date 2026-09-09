"""Regresiones de JSON incompleto, preservación de fuentes y coste de reintentos."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import httpx
from openai import AsyncOpenAI
from pydantic import ValidationError

import config
from llm_client import LLMClient, LLMOutputTruncatedError
from research_agent import run_research
from research_quality import (AppliedReport, Claim, Findings, ResearchOutputError,
                              apply_findings, structured)


TEXT = ('La norma permite rebajar las sumas efectivamente pagadas por el uso o goce del inmueble. '
        'El retiro corresponde solo a la diferencia positiva entre la presunción y los pagos acreditados.')


def applied_report(count=8):
    return {'direct_answer': 'Las sumas pagadas deben revisarse según los antecedentes.',
            'facts_considered': [], 'assumptions': [],
            'conclusions': [{'status': 'respaldada', 'text': TEXT, 'claim_indexes': list(range(count))}],
            'client_risks': [], 'advisor_risks': [], 'counterarguments_and_limits': [],
            'practical_actions': [], 'missing': []}


def transport_client(responses, captured):
    """Cliente real contra respuestas HTTP locales: no usa claves ni la red."""
    replies = iter(responses)

    def respond(request):
        captured.append(json.loads(request.content))
        content, finish = next(replies)
        return httpx.Response(200, json={
            'id': 'local-test', 'object': 'chat.completion', 'created': 0, 'model': 'deepseek-chat',
            'choices': [{'index': 0, 'finish_reason': finish,
                         'message': {'role': 'assistant', 'content': content}}],
        })

    client = LLMClient.__new__(LLMClient)
    client._provider, client._model, client._gemini = 'deepseek', 'deepseek-chat', None
    client._openai = AsyncOpenAI(api_key='test-only', base_url='https://test.invalid', max_retries=0,
                               http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    return client


class ResearchOutputTests(unittest.IsolatedAsyncioTestCase):
    async def test_length_signal_is_retried_even_when_json_happens_to_parse(self):
        captured = []
        complete = json.dumps(applied_report())
        model = transport_client([(complete, 'length'), (complete, 'stop')], captured)
        self.addAsyncCleanup(model.aclose)
        budget = MagicMock()
        budget.reserve.side_effect = ['first', 'second']
        payload = {'claims': [{'index': i} for i in range(8)]}
        result = await structured(model, AppliedReport, 'Devuelve JSON.', payload, 4500,
                                  budget=budget, purpose='informe_aplicado', final_stage=True)
        self.assertEqual(result.conclusions[0].claim_indexes, list(range(8)))
        self.assertEqual([r['max_tokens'] for r in captured], [4500, 8000])
        self.assertTrue(all(r['response_format'] == {'type': 'json_object'} for r in captured))
        self.assertEqual(captured[0]['messages'][1], captured[1]['messages'][1])
        self.assertEqual(budget.commit.call_args_list, [call('first'), call('second')])
        self.assertTrue(all(c.kwargs['final_stage'] for c in budget.reserve.call_args_list))
        budget.release.assert_not_called()

    async def test_persistent_truncation_stops_after_three_generations(self):
        model = MagicMock(chat_completion=AsyncMock(return_value='{"claims":[{"quote":"private-value'))
        budget = MagicMock()
        budget.reserve.side_effect = ['one', 'two', 'three']
        with self.assertLogs('research_quality', level='WARNING') as logs:
            with self.assertRaisesRegex(ResearchOutputError, 'verificacion_fuentes.*3 intentos'):
                await structured(model, Findings, 'JSON', {'sources': []}, 8000,
                                 budget=budget, purpose='verificacion_fuentes')
        self.assertEqual(model.chat_completion.await_count, 3)
        self.assertEqual(budget.commit.call_count, 3)
        budget.release.assert_not_called()
        self.assertNotIn('private-value', '\n'.join(logs.output))
        self.assertTrue(all(c.kwargs['max_tokens'] == 8000 for c in model.chat_completion.call_args_list))

    async def test_applied_stage_does_not_multiply_exhausted_json_retries(self):
        model = MagicMock(chat_completion=AsyncMock(return_value='{"direct_answer":"incompleta'))
        claims = [Claim(heading='Pago', statement=TEXT, quote=TEXT, source_id=1)]
        with self.assertRaises(ResearchOutputError):
            await apply_findings(model, 'Consulta', claims, [], [{'title': 'Fuente', 'url': 'https://sii.cl/a'}])
        self.assertEqual(model.chat_completion.await_count, 3)

    async def test_retry_cannot_bypass_budget(self):
        model = MagicMock(chat_completion=AsyncMock(return_value='{"claims":['))
        budget = MagicMock()
        budget.reserve.side_effect = ['one', RuntimeError('Presupuesto agotado')]
        with self.assertRaisesRegex(RuntimeError, 'Presupuesto agotado'):
            await structured(model, Findings, 'JSON', {}, budget=budget)
        self.assertEqual(model.chat_completion.await_count, 1)
        budget.commit.assert_called_once_with('one')
        budget.release.assert_not_called()

    async def test_transport_error_and_cancellation_release_reservation(self):
        for failure in (RuntimeError('insufficient_quota'), asyncio.CancelledError(), TimeoutError()):
            with self.subTest(error=type(failure).__name__):
                model = MagicMock(chat_completion=AsyncMock(side_effect=failure))
                budget = MagicMock()
                budget.reserve.return_value = 'pending'
                with self.assertRaises(type(failure)):
                    await structured(model, Findings, 'JSON', {}, budget=budget)
                self.assertEqual(model.chat_completion.await_count, 1)
                budget.release.assert_called_once_with('pending')
                budget.commit.assert_not_called()

    async def test_rate_limit_wait_keeps_one_reservation(self):
        model = MagicMock(chat_completion=AsyncMock(side_effect=[
            RuntimeError('429 rate limit'), '{"claims":[],"missing":[]}',
        ]))
        budget = MagicMock()
        budget.reserve.return_value = 'pending'
        with patch('research_quality.asyncio.sleep', AsyncMock()) as sleep:
            await structured(model, Findings, 'JSON', {}, budget=budget)
        sleep.assert_awaited_once()
        budget.reserve.assert_called_once()
        budget.commit.assert_called_once_with('pending')
        budget.release.assert_not_called()

    async def test_structural_errors_are_not_accepted_as_valid_json(self):
        model = MagicMock(chat_completion=AsyncMock(return_value='{"claims":[{}],"missing":[]}'))
        with self.assertRaises(ValidationError):
            await structured(model, Findings, 'JSON', {})

    async def test_text_mode_and_extended_http_timeout(self):
        model = LLMClient.__new__(LLMClient)
        model._provider, model._model, model._gemini = 'deepseek', 'deepseek-chat', None
        model._openai = MagicMock()
        create = model._openai.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
            choices=[SimpleNamespace(finish_reason='length', message=SimpleNamespace(content='Texto'))]))
        result = await model.chat_completion(messages=[], timeout=240)
        self.assertEqual(result, 'Texto')
        self.assertNotIn('response_format', create.call_args.kwargs)
        with self.assertRaises(LLMOutputTruncatedError):
            await model.chat_completion(messages=[], timeout=240, json_mode=True)
        self.assertEqual(create.call_args.kwargs['timeout'], 240)

    async def test_gemini_json_mode_detects_max_tokens(self):
        model = LLMClient.__new__(LLMClient)
        model._provider, model._model, model._openai = 'gemini', 'gemini-test', None
        model._gemini = MagicMock()
        generate = model._gemini.models.generate_content
        generate.return_value = SimpleNamespace(text='{"claims":[]}', candidates=[
            SimpleNamespace(finish_reason=SimpleNamespace(name='MAX_TOKENS'))])
        with self.assertRaises(LLMOutputTruncatedError):
            await model.chat_completion(messages=[{'role': 'user', 'content': 'JSON'}], json_mode=True)
        self.assertEqual(generate.call_args.kwargs['config'].response_mime_type, 'application/json')

    async def test_research_recovers_both_stages_and_publishes_all_eight_sources(self):
        sources = [{'title': f'Oficio SII N° {i} — arriendo a accionista',
                    'url': f'https://www.sii.cl/oficio-{i}.htm', 'content': TEXT, 'full_text': TEXT,
                    'official': True, 'fetched': True} for i in range(1, 9)]
        findings = {'claims': [{'heading': f'Pago {i}', 'statement': TEXT, 'source_id': i, 'quote': TEXT}
                               for i in range(1, 9)], 'missing': []}
        report = applied_report()
        # Mismo tipo de error comunicado en producción, pasado por el cliente HTTP real.
        truncated = '{"claims":[],"missing":["' + ('x' * 16024)
        captured = []
        model = transport_client([
            (json.dumps({'queries': ['arriendo accionista', 'pagos acreditados'],
                         'concepts': ['arriendo', 'accionista']}), 'stop'),
            (truncated, 'stop'),  # El proveedor no siempre informa el corte.
            (json.dumps(findings), 'stop'),
            (json.dumps({'supported_claims': list(range(8))}), 'stop'),
            ('{"direct_answer":"respuesta incompleta', 'length'),
            (json.dumps(report), 'stop'),
        ], captured)
        budget = MagicMock()
        budget.reserve.side_effect = [f'call-{i}' for i in range(6)]
        with patch('llm_client.LLMClient', return_value=model), \
             patch('research_quality.reference_sources', return_value=[]), \
             patch('research_agent._stored_official_sources', return_value=[]), \
             patch('research_agent.live_lookup.search_live', AsyncMock(return_value=sources)), \
             patch('research_agent._scrape_result', AsyncMock(side_effect=lambda s: s)), \
             patch('research_agent.save_research_to_vault', return_value={'archivos': [], 'resumen': ''}) as save:
            result = await run_research('Una SpA arrienda al accionista', _budget=budget)
        self.assertEqual(result['quality'], 'complete')
        self.assertEqual(result['warnings'], [])
        self.assertIn('Informe de investigación aplicada', result['reply'])
        self.assertEqual(result['report']['conclusions'][0]['claim_indexes'], list(range(8)))
        self.assertEqual({s['url'] for s in result['sources']}, {s['url'] for s in sources})
        self.assertEqual(len(save.call_args.args[1]), 8)
        self.assertEqual(len(result['evidence']), 8)
        for source in sources:
            self.assertIn(source['url'], result['reply'])
        self.assertEqual(captured[1]['messages'][1], captured[2]['messages'][1])
        self.assertEqual(captured[4]['messages'][1], captured[5]['messages'][1])
        for index in (1, 2, 4, 5):
            self.assertEqual(captured[index]['max_tokens'], config.RESEARCH_MAX_OUTPUT_TOKENS)
        self.assertEqual(budget.commit.call_count, 6)
        budget.release.assert_not_called()


if __name__ == '__main__':
    unittest.main()
