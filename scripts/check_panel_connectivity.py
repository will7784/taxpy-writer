"""Diagnóstico manual de buscador/modelo; no imprime claves ni guarda documentos."""
import asyncio
import json
import sys
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
import config
from http_security import tls_context
from llm_client import LLMClient


async def main():
    if "--research" in sys.argv:
        from fastapi.testclient import TestClient
        import web_server
        question = 'Una empresa spa puede arrendar de manera "real" una propiedad a su unico accionista a una valor de mercado?, cuales son las consecuencias tributarias?'

        def dry_save(query, results, **kwargs):
            return {"query": query, "archivos": [], "resumen": ""}

        traces = []
        original_chat = LLMClient.chat_completion
        async def traced_chat(self, **kwargs):
            answer = await original_chat(self, **kwargs)
            traces.append({'request': kwargs['messages'], 'response': answer})
            return answer

        with patch.object(web_server, "_is_authenticated", return_value=True), \
             patch("research_agent.save_research_to_vault", side_effect=dry_save), \
             patch.object(LLMClient, 'chat_completion', traced_chat):
            # No iniciar el monitor ni otros efectos del ciclo de vida de producción.
            panel = TestClient(web_server.app)
            try:
                response = await asyncio.to_thread(panel.post, "/api/research", data={"query": question})
            finally:
                panel.close()
            result = response.json()
            report = Path(__file__).resolve().parents[1] / 'notes' / 'validacion_claudia.json'
            report.parent.mkdir(exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            report.with_name('validacion_claudia_trace.json').write_text(json.dumps(traces, ensure_ascii=False, indent=2), encoding='utf-8')
            print({"http": response.status_code, "has_evidence": result.get("has_evidence"),
                   "answer_chars": len(result.get("reply", "")), "warnings": result.get("warnings", []),
                   "error": result.get("error"), "quality": result.get('quality'), "saved_documents": False})
            if response.status_code != 200 or not result.get("has_evidence") or result.get("warnings"):
                raise SystemExit(1)
        return
    async with httpx.AsyncClient(verify=tls_context(), timeout=30) as client:
        query = "SpA arriendo inmueble unico accionista valor mercado consecuencias tributarias Chile"
        for options in ({}, {"country": "chile"}, {"include_domains": ["sii.cl", "bcn.cl"]}):
            response = await client.post("https://api.tavily.com/search",
                headers={"Authorization": "Bearer " + config.TAVILY_API_KEY},
                json={"query": query, "max_results": 3, **options})
            data = response.json()
            print({"http": response.status_code, "options": options, "keys": list(data),
                   "results": len(data.get("results", []))})
    model = LLMClient()
    try:
        answer = await asyncio.wait_for(model.chat_completion(
            messages=[{"role": "user", "content": "Responde solamente OK"}], max_tokens=10), timeout=45)
        print({"provider": model.provider, "answer": answer})
    except Exception as exc:
        print({"model_error": type(exc).__name__})
    finally:
        if model._openai:
            await model._openai.close()


if __name__ == "__main__":
    asyncio.run(main())
