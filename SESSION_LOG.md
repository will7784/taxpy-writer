# Sesión 2026-06-04 — Árboles de Decisión Jurídica

## Contexto previo (resumen compactado)
- El proyecto es **Taxpy / ClaudIA**, bot de Telegram para asesoría tributaria chilena.
- Stack: Python 3.13, Supabase pgvector, OpenAI GPT-4o + embeddings.
- Se eliminaron los modos escritor (`/manual`, `/articulo`, `/guion`, `/historia`, `/outline`).
- El bot ahora es **asesor legal de precisión** con fallback a RAG.

---

## Lo que se hizo hoy

### 1. Motor de Árboles de Decisión (`decision_engine.py`)
- **Nuevo módulo completo** en raíz del proyecto (no en subdirectorio).
- Carga árboles desde `decision_trees/codigo_tributario/*.json`.
- Componentes:
  - `DecisionTree` / `DecisionNode` (dataclasses): modelan el árbol.
  - `walk_tree()`: recorrido determinista con facts booleanos.
  - `continue_tree()`: continúa desde un nodo intermedio (para conversaciones).
  - `render_result()`: formatea resultado con diagrama, warnings, pasos.
  - `render_interactive()`: muestra pregunta + opciones numeradas.
  - `interpret_query()`: LLM extrae facts del texto libre del usuario.
  - `find_tree()`: matching por keywords + LLM fallback.

### 2. Creación de 10 árboles JSON
Archivos en `decision_trees/codigo_tributario/`:

| # | Archivo | Tema | Art. CT |
|---|---------|------|---------|
| 1 | `01_citacion_sii.json` | Citación SII para fiscalizar | Art. 63 |
| 2 | `02_liquidacion_giro.json` | Liquidación y Giro de Oficio | Art. 64-65 |
| 3 | `03_renta_presunta.json` | Determinación de Oficio | Art. 59-61 |
| 4 | `04_prescripcion.json` | Prescripción tributaria | Art. 200-201 |
| 5 | `05_infracciones_sanciones.json` | Infracciones y Sanciones | Art. 97-98 |
| 6 | `06_recurso_reposicion.json` | Reposición y Reclamación | Art. 120-122 |
| 7 | `07_intereses_mora.json` | Intereses por Mora | Art. 53-54 |
| 8 | `08_cobranza_embargo.json` | Cobranza y Embargo | Art. 172-177 |
| 9 | `09_secrecy_tributario.json` | Secreto Tributario | Art. 35-37 |
| 10 | `10_convenio_pago.json` | Convenio de Pago | Art. 56, 192 |

Cada árbol tiene nodos de tipo: `decision` (pregunta), `info` (explicación), `result` (respuesta final).

### 3. Integración en el bot (`telegram_mvp_bot.py`)
- `_process_chat()` ahora intenta árbol **antes** de caer a RAG.
- Si llega a resultado → renderiza y envía.
- Si no llega → pregunta de clarificación con opciones numeradas.
- Guarda sesión como `decision_tree_pending`.

### 4. Continuación de conversación
- Nuevo método `_continue_decision_tree()`:
  - Detecta sesión pendiente en `_handle_text()`.
  - Parsea respuesta numérica (1, 2, 3…) como selección de branch.
  - También acepta texto libre y extrae facts vía LLM.
  - Continúa recorrido y llega a resultado o repregunta.

### 5. Fixes críticos
- **Fact extraction vacío**: prompt mejorado con ejemplos in-context + instruction tuning. Ahora extrae `sii_citado: true`, `forma_correcta: true`, etc.
- **Markdown JSON blocks**: se limpian correctamente (```json ... ```).
- **Tree matching**: scoring por keywords arreglado (`liquidacion_giro` ya no matchea `secrecy_tributario`).
- **Windows Unicode**: `PYTHONIOENCODING=utf-8` + `sys.stdout.reconfigure(encoding='utf-8')` para tests.

### 6. Archivo de validación creado
- **`VALIDACION_ARBOLES.md`** — Documento completo con:
  - Índice de los 10 árboles.
  - Cada árbol desglosado: root, nodos, resultados, referencias legales.
  - Guía de validación jurídica.
  - Comando para probar árboles individualmente.

---

## Estado actual del sistema

### Módulos activos
| Módulo | Estado |
|--------|--------|
| `decision_engine.py` | ✅ Funcional y probado |
| `telegram_mvp_bot.py` | ✅ Integrado, compila sin errores |
| `decision_trees/codigo_tributario/*.json` | ✅ 10 árboles creados |
| `VALIDACION_ARBOLES.md` | ✅ Documento de validación listo |
| RAG fallback | ✅ Sigue operativo si no hay árbol |

### Flujo validado end-to-end
Simulación con `citacion_sii`:
1. Usuario: *"me citó el sii"*
2. Bot: pregunta forma de citación (2 opciones)
3. Usuario: *"1"* → avanza a plazo
4. Bot: pregunta plazo (2 opciones)
5. Usuario: *"1"* → avanza a derechos
6. Bot: pregunta si quiere conocer derechos
7. Usuario: *"1"* → llega a `result_derechos`
8. Bot: envía respuesta prevalidada completa + diagrama del camino

### Pendientes para mañana (prioridad)

1. **Validación jurídica de los 10 árboles**
   - El usuario va a usar **Opción 1**: leer `VALIDACION_ARBOLES.md`.
   - Revisar que referencias legales estén correctas (artículo, inciso, numeral).
   - Revisar que interpretaciones sean jurídicamente precisas.
   - Revisar que no falten escenarios relevantes.

2. **Correcciones a árboles (según validación)**
   - Editar directamente los JSON en `decision_trees/codigo_tributario/`.
   - No requiere reiniciar nada más que el bot.

3. **Persistencia de sesiones pendientes (opcional)**
   - Actualmente `decision_tree_pending` vive en memoria (`self._sessions`).
   - Si el bot reinicia, se pierde. Para MVP es aceptable.
   - Si se quiere persistencia, guardar en SQLite (`SessionStore`).

4. **Mejorar extracción de facts (opcional)**
   - Agregar rule-based fallback: si la query contiene "citó" → `sii_citado: true` sin LLM.
   - Agregar más ejemplos in-context al prompt del LLM.

---

## Palabras clave para recordar mañana

> "**arboles**" → continuar con validación jurídica de los 10 árboles JSON.
> El usuario eligió la **Opción 1** (`VALIDACION_ARBOLES.md`).

## Archivos nuevos/modificados clave

- `decision_engine.py` — Motor completo (nuevo)
- `telegram_mvp_bot.py` — Integración + `_continue_decision_tree()` (modificado)
- `decision_trees/codigo_tributario/01_*.json` a `10_*.json` — Árboles JSON (nuevos)
- `VALIDACION_ARBOLES.md` — Documento de validación (nuevo)

---

# Sesión 2026-07-11 — Parser legal universal + router de 3 capas + asistente de árboles

## Contexto previo (resumen compactado)

El usuario quiere convertir el proyecto en un asistente legal/tributario experto:
chunking + grafos + árboles de decisión generados (no solo escritos a mano), con
uso real YA por Telegram (datos reales aunque pocos, no "de prueba" en el sentido
de descartables). Plan completo aprobado en:
`C:\Users\lyf-a\.claude\plans\sequential-booping-glacier.md`

Decisiones clave que el usuario corrigió durante la planificación:
- El grafo **no** se despriorizaba por volumen de datos — se decide con evidencia
  (Fase 2.5), no por cantidad.
- Canal: **Telegram ya** (ya funciona), WhatsApp queda fuera de este plan.

## Lo que se hizo hoy (5 fases, todas completadas)

### Fase 1 — `legal_parser.py` + `schemas.py` (nuevos)
Parser jerárquico único (artículo→numeral→letra) con tabla de patrones regex por
tipo de documento, en vez de funciones a medida por artículo. Primer uso de
Pydantic en el repo (`schemas.py`), sin tocar los `@dataclass` existentes.
Probado contra los PDFs reales de Código Tributario (211 artículos, 466 chunks)
y LIR (128 artículos, 563 chunks). Bugs de datos reales encontrados y corregidos:
- "Artículos Transitorios" renumeran desde 1 → se detectan y prefijan aparte
  (`transitorio_marker` en `DocumentPattern`).
- Art. 1° del decreto promulgatorio choca con Art. 1° del texto anexado → seguro
  anti-colisión de `chunk_uid` (sufijo `_dupN`) en `LegalParser._dedupe_uids()`.

### Fase 2 — Ingesta unificada
`ingest.py::PDFLawParser` tenía su PROPIA lógica de regex/incisos duplicada
(competía con `legal_parser.py` sin que el usuario lo supiera) — se reemplazó
para delegar en `legal_parser.py`. `IngestionPipeline._upsert_chunks` renombrado
a `upsert_chunks` (público, ahora se usa cross-módulo). Nuevo
`scripts/ingest_cli.py` (con `--dry-run`) reemplaza `scripts/initial_ingest.py`
(eliminado, quedaba 100% redundante) y el rol disperso de los `rechunk_*.py`.
`scripts/sync_sii.py` actualizado (docstring) para apuntar al nuevo CLI.

### Fase 2.5 — Evaluación del grafo con evidencia
Hallazgo: `usage_logs` nunca guardaba el texto real de las queries (solo
`query_type`/`tokens_used`) — imposible medir nada hasta ahora. Se agregó:
- `sql/002_usage_logs_query_text.sql` — migración ADITIVA (columna
  `query_text`). **Pendiente: correrla en el SQL Editor de Supabase.**
- `_log_query()` en `telegram_mvp_bot.py` — loguea cada consulta real (best-effort,
  nunca bloquea el chat).
- `scripts/eval_graph_lift.py` — compara RAG puro vs. RAG+`graph_engine.expand_results()`
  sobre queries reales de `usage_logs`; decide si vale la pena invertir en la Fase 5.
  Tiene modo `--demo` para previsualizar el formato sin datos reales.

### Fase 3 — Router de 3 capas
Corrección importante sobre el plan original: LeyChile (BCN) y sii.cl **no
tienen API pública documentada** para esto (verificado con búsqueda web). En vez
de conectores a medida frágiles, se usa **Tavily** con `include_domains=["bcn.cl","sii.cl"]`
(prioriza fuentes oficiales, cae a web abierta si no hay resultados). Nuevo
`live_lookup.py`. Nuevo método `llm_client.chat_completion_structured(schema=...)`
(OpenAI `beta.chat.completions.parse` nativo + Gemini modo JSON con validación
Pydantic) — reemplaza el parseo frágil de `decision_engine.interpret_query()`
como patrón para el resto del código nuevo. Integrado como Paso 3 en
`telegram_mvp_bot.py::_process_chat` (se activa si no hay resultados RAG o el
mejor similarity < `config.RAG_CONFIDENCE_THRESHOLD`, default 0.72).
**Pendiente: conseguir `TAVILY_API_KEY` y ponerla en `.env`** — sin ella esta
capa queda inactiva sin romper nada.

### Fase 4 — Asistente de árboles + UI de revisión
`decision_tree_drafter.py` (nuevo): LLM propone un borrador vía
`chat_completion_structured` con schema Pydantic espejo de
`DecisionNode`/`DecisionTree`, escribe a `decision_trees/_drafts/` (nunca directo
a `codigo_tributario/`). `scripts/draft_tree_cli.py` para generarlos desde un
chunk ya ingestado. UI de revisión agregada a `web_server.py` (rutas `/review/drafts/*`,
reutiliza el mismo login admin) con diagrama Mermaid + JSON editable. "Aprobar"
valida el árbol con el parser real de `decision_engine.py` (`DecisionEngine._parse_tree`)
antes de mover el archivo — probado end-to-end en el navegador (login → lista →
detalle con Mermaid renderizado → guardar edición → aprobar → archivo movido y
validado). De paso se arregló un bug preexistente en `web_server.py` (faltaba
`from pathlib import Path`, hacía fallar en silencio la sync de credenciales de
NotebookLM).

## Limitación del entorno de esta sesión

No se pudo probar contra la API real de OpenAI/Tavily: este sandbox tiene un
bloqueo de red/SSL (`CERTIFICATE_VERIFY_FAILED`) confirmado también al ver el
error de conexión a NotebookLM en el propio dashboard — no es un bug del código
nuevo. La UI de revisión y el parser sí se probaron end-to-end con datos reales
(PDFs de leyes) y un borrador de árbol de prueba (creado a mano, no vía LLM).

## Pendientes para mañana (prioridad)

1. **Correr `sql/002_usage_logs_query_text.sql`** en el SQL Editor de Supabase
   (aditivo, no destructivo) — sin esto `eval_graph_lift.py` no tiene nada que leer.
2. **Conseguir `TAVILY_API_KEY`** si se quiere activar la Capa 3 del router.
3. `pip install -r requirements.txt` (se agregó `pydantic`).
4. Dejar correr el bot unos días con uso real → después correr
   `python scripts/eval_graph_lift.py` para decidir con evidencia si se invierte
   en la Fase 5 (grafo avanzado).
5. Probar `chat_completion_structured` y `live_lookup.search_live` contra las
   APIs reales una vez fuera de este sandbox (Fase 3 no se pudo probar en vivo).
6. Generar el primer borrador real: `python scripts/draft_tree_cli.py --chunk-uid ley_codigo_tributario_art_XX`
   sobre un artículo SIN árbol validado todavía, y revisarlo en `/review/drafts`.

## Palabras clave para recordar mañana

> "**stack**" o "**árboles automáticos**" → continuar el plan de
> `sequential-booping-glacier.md`: correr la migración SQL pendiente, conseguir
> TAVILY_API_KEY, y generar el primer borrador de árbol real con
> `scripts/draft_tree_cli.py`.

## Archivos nuevos/modificados clave (sesión de hoy)

- `legal_parser.py`, `schemas.py` — parser jerárquico universal (nuevos)
- `ingest.py` — `PDFLawParser` delega en `legal_parser`; `upsert_chunks` público (modificado)
- `scripts/ingest_cli.py` — CLI unificado de ingesta (nuevo)
- `scripts/initial_ingest.py` — eliminado (redundante)
- `scripts/eval_graph_lift.py` — evaluación del grafo con evidencia (nuevo)
- `sql/002_usage_logs_query_text.sql` — migración pendiente de correr (nuevo)
- `telegram_mvp_bot.py` — `_log_query()` + Paso 3 (live_lookup) en `_process_chat` (modificado)
- `llm_client.py` — `chat_completion_structured()` (modificado)
- `live_lookup.py` — Capa 3 del router, Tavily (nuevo)
- `config.py` — `TAVILY_API_KEY`, `RAG_CONFIDENCE_THRESHOLD` (modificado)
- `decision_tree_drafter.py`, `scripts/draft_tree_cli.py` — asistente de árboles (nuevos)
- `web_server.py` — rutas `/review/drafts/*` + fix `Path` no importado (modificado)
- `templates/review_drafts.html`, `templates/review_detail.html` — UI de revisión (nuevos)
- `requirements.txt` — se agregó `pydantic` (modificado)
