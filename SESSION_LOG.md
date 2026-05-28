# Sesión 2026-05-27 — Resumen de cambios

## Commits realizados (orden cronológico)

1. `425f676` — Unificación de agent.md + sinónimos legales PRO-PYME
2. `cd18f85` — Corrección PRO-PYME (PIME es pronunciación errónea en audio)
3. `3a654c2` — Regla de dominio: fuerza Art. 192 CT en queries PYME+intereses
4. `a982f7b` — Inyectar agent.md en modo conversación (corregía PRO-PIME)
5. `2e79db2` — Contexto enriquecido con "marcas" (LEY, ARTÍCULO, ARCHIVO)
6. `b023e67` — Aumentar contexto enviado al LLM (CONVERSATION_TOP_K: 8→20)
7. `6d6031e` — Soporte dual OpenAI / Google Gemini 1.5 Pro
8. `699cc24` — Re-chunking Art. 14 LIR separando letras A-E (15 chunks)
9. `8fb5498` — Notas de dominio + prompt más proactivo
10. `ca37d4c` — GraphRAG híbrido completo (grafo de conocimiento)

## Estado actual del sistema

### Base de datos
- **Chunks totales:** ~2.934 (incluye 15 nuevos del Art. 14 LIR)
- **Leyes:** DL-824 (LIR), DL-825 (IVA), DL-830 (CT)
- **Tabla `knowledge_graph`:** ❌ NO CREADA AÚN (pendiente ejecutar SQL en Supabase)
- **Relaciones críticas:** Hardcodeadas en `critical_relations.py` pero no insertadas
- **Backfill de grafo:** ❌ NO EJECUTADO AÚN

### Pendientes para mañana (prioridad)

1. **Crear tabla `knowledge_graph` en Supabase**
   - Ejecutar SQL en Supabase SQL Editor (está en `sql/supabase_rag_schema.sql`)

2. **Deploy en Railway**
   - Push `ca37d4c` ya está en GitHub; Railway debería deployar automático

3. **Ejecutar backfill del grafo**
   - `python scripts/backfill_graph.py` (local o en Railway)
   - Costo: ~$1.20 con GPT-4o-mini
   - Tiempo: ~30-60 minutos

4. **Activar Gemini (opcional pero recomendado)**
   - Agregar `GEMINI_API_KEY` en variables de Railway
   - Requiere API key de https://aistudio.google.com/app/apikey

### Cómo probar que todo funciona

Preguntarle a ClaudIA:
> "¿Hay algún beneficio de rebaja de interés para el régimen PRO-PYME?"

Respuesta esperada:
> "Sí. Según el **Art. 192 del Código Tributario**, para los contribuyentes del régimen PRO-PYME —regulado por el **Art. 14 letra D de la LIR**— no se aplican intereses sobre las cuotas de convenios de hasta 18 meses."

## Archivos nuevos/modificados clave

- `agent.md` — Instrucciones unificadas del agente
- `rag_engine.py` — Búsqueda híbrida + domain rules + GraphRAG + notas de dominio
- `telegram_mvp_bot.py` — Modo conversación con agent.md + prompt proactivo
- `writer.py` — Soporte dual LLM (OpenAI/Gemini)
- `llm_client.py` — Wrapper unificado de LLM
- `critical_relations.py` — Relaciones hardcodeadas del grafo
- `graph_engine.py` — Navegación del grafo
- `graph_extractor.py` — Extracción automática de relaciones
- `ingest.py` — Integración de extracción en pipeline
- `scripts/backfill_graph.py` — Backfill de chunks existentes
- `scripts/rechunk_art14_lir.py` — Re-chunking del Art. 14
