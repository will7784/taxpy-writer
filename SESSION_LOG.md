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

---

# Sesión 2026-07-12 — Incidente en producción: consultor por defecto + deploy roto + Supabase pausado

## Contexto

El usuario probó el bot real en Telegram con una pregunta sobre venta de inmuebles
y la respuesta fue mala (artículo largo tipo blog, con markdown, que inventó una
"exención por vivienda habitual" que no existe en la LIR). A partir de ahí se
diagnosticó y arregló una cadena de problemas reales, todos independientes entre
sí, que se fueron descubriendo uno detrás de otro. Nada de esto estaba pusheado
al arrancar el día — `origin/main` seguía en el commit de antes de la sesión de
ayer (`8e42af6`).

También hubo un pivote de producto explícito del usuario: esto deja de ser un
generador de contenido para blog/LinkedIn — es un consultor tributario
conversacional para vender por suscripción a contadores. El modo escritor
(`writer.write()`) no se borra, se pospone.

## Cadena de problemas encontrados y arreglados (en orden)

1. **Bug de ruteo (causa directa de la respuesta mala).** En
   `telegram_mvp_bot.py:_handle_text`, todo mensaje libre pasaba por
   `writer.detect_content_type(text)`, cuyo default es `"articulo"` — eso
   disparaba el modo escritor (sin `citation_guardrail.guardrail_check()`, sin
   límite de tokens, y con fallback explícito a "escribir con conocimiento
   general" si el RAG fallaba). Los comandos `/manual`, `/articulo`, etc. ya
   estaban apagados, pero el auto-ruteo desde texto libre nunca se desconectó.
   **Fix**: todo mensaje libre va directo a `_process_chat` (árbol → RAG → live
   lookup). `writer.write()` queda intacto para reactivar el modo escritor más
   adelante. Se agregó `guardrail_check()` también en `_process_request` como
   defensa en profundidad.
2. **Árboles de decisión con vigencia/fecha.** El usuario señaló que la
   tributación de venta de inmuebles depende de LA FECHA de adquisición
   (regímenes distintos en distintos años) — se reforzó el prompt de
   `decision_tree_drafter.py` para que genere ramas de fecha cuando el artículo
   mencione reformas/normas transitorias, y se agregó una nota en
   `VALIDACION_ARBOLES.md`.
3. **Rebrand a Impuestia** en el panel admin (`web_server.py`, templates de
   login/dashboard/revisión). "ClaudIA" se mantiene como nombre de la asistente
   dentro del chat (decisión explícita del usuario).
4. **Deploy roto en Railway desde hacía ~un mes.** Revisando los logs reales del
   deploy fallido: `ModuleNotFoundError: No module named 'google'`.
   `llm_client.py` importa el SDK de Gemini sin condición, pero `google-genai`
   nunca se agregó a `requirements.txt`. Railway hacía rollback silencioso al
   último deploy bueno (anterior al soporte dual OpenAI/Gemini) en CADA commit
   desde entonces — nadie lo había notado. **Fix**: se agregó
   `google-genai>=2.0.0` a `requirements.txt`.
5. **Dashboard seguía mostrando el error de NotebookLM** después del rebrand,
   lo que le hizo pensar al usuario que nada se había arreglado. NotebookLM ya
   está deprecado (decisión de hoy), así que se sacó del dashboard entero: ya no
   se llama a `_list_notebooks_from_api()` en `/dashboard`, se sacaron las
   tarjetas de credenciales/cuadernos del template.
6. **`telegram.error.BadRequest: Message to edit not found`** en los logs —
   Telegram a veces no encuentra el mensaje de estado que el bot intenta
   editar/borrar. Se agregaron `_safe_edit()`/`_safe_delete()` (helpers a nivel
   de módulo) que envuelven cada uso de `status_msg.edit_text()`/`.delete()` en
   su propio try/except, aplicado en todo el archivo, no solo en `_process_chat`.
7. **La causa raíz real de ambas respuestas malas (ayer y hoy): el proyecto de
   Supabase estaba PAUSADO** (plan free, se pausa solo tras ~7 días sin
   actividad). El contenido SÍ estaba bien chunkeado (Art. 17 N°8 con varios
   sub-chunks, verificado con SQL directo) — el RAG fallaba porque no podía
   conectarse, no porque le faltara información. El usuario eligió NO pagar
   Supabase Pro por ahora; en su lugar se agregó un **keepalive**: un ping cada
   24h a `document_chunks` corriendo como background task en el `lifespan` de
   FastAPI (`web_server.py`), ya que el proceso de Railway corre 24/7.

`telegram.error.Conflict: terminated by other getUpdates request` apareció varias
veces en los logs durante estos redeploys — parece ser el solape transitorio
normal entre el contenedor viejo terminando y el nuevo arrancando en cada deploy,
no un problema de réplicas (confirmado: 1 réplica, sin proyectos duplicados en
Railway). Se resuelve solo segundos después de cada deploy; no bloqueó las
respuestas reales una vez estable.

## Verificación real (en producción, con el usuario)

Después de todos los fixes + Supabase despierto, se probaron dos preguntas
reales en Telegram y el bot respondió correctamente citando Art. 17 N° 8 (LIR,
DL-824): la exención de 8.000 UF, la opción de impuesto único del 10%, y el
plazo de 1 año (4 años si es por subdivisión/construcción) entre adquisición y
venta. Sin alucinaciones, sin modo escritor, tono conversacional correcto.

**Bug menor encontrado en la verificación** (no bloqueante, queda pendiente):
`citation_guardrail.py` agrega `[WARN] No pude confirmar en las fuentes
consultadas: 'DL-824'` en CADA respuesta que menciona "DL-824" — es un falso
positivo: el patrón `DL[-\s]?(824|825|830)` en `_ARTICLE_PATTERNS` intenta
verificar "824" como si fuera un número de artículo contra `_context_articles`,
pero 824 es el número del decreto, no de un artículo, así que nunca va a
coincidir. La cita en sí es correcta, el warning es ruido.

## Pendientes para mañana

1. **Arrancar los árboles de decisión de verdad** — el usuario quiere empezar
   por venta de inmuebles (Art. 17 N° 8) dado el tema de vigencia que señaló:
   según él, hasta cierto año no se pagaba nada y después se aplicó el tope de
   8.000 UF — hay que confirmar el año exacto del cambio de régimen y modelarlo
   como rama de fecha en el árbol (ver punto 2 de la sesión de hoy).
   `python scripts/draft_tree_cli.py --chunk-uid ley_lir_art_17_n8` (o el
   chunk_uid exacto que corresponda) y revisar en `/review/drafts`.
2. **Falso positivo de `DL-824` en el guardrail** — ajustar
   `citation_guardrail.py` para que el patrón `DL[-\s]?(824|825|830)` no
   intente validarse contra `_context_articles` (ya está cubierto por los
   patrones "Art. X del DL-824" que sí funcionan bien).
3. Pendientes que quedaron de ayer sin resolver: `TAVILY_API_KEY` (Capa 3
   sigue inactiva), correr `scripts/eval_graph_lift.py` cuando haya más
   queries reales acumuladas en `usage_logs`.
4. El frontend para subir documentos y el frontend de "probar respuestas antes
   de que las vea un usuario real" (acordado ayer, pospuesto) — el dashboard ya
   está limpio y listo para que esto se agregue encima.

## Palabras clave para recordar mañana

> "**árboles**" (sin más contexto) → hoy toca modelar árboles de decisión de
> verdad, empezando por venta de inmuebles/Art. 17 N° 8 con su componente de
> vigencia por fecha. Usar `scripts/draft_tree_cli.py` + revisar en
> `/review/drafts`.

## Archivos nuevos/modificados clave (sesión de hoy)

- `telegram_mvp_bot.py` — fix de ruteo (consultor por defecto), guardrail en
  modo escritor, `_safe_edit`/`_safe_delete` en todo el archivo (modificado)
- `decision_tree_drafter.py`, `VALIDACION_ARBOLES.md` — prompt/guía reforzados
  para vigencia/fecha (modificado)
- `web_server.py`, `templates/dashboard.html`, `templates/login.html`,
  `templates/review_*.html`, `config.py` — rebrand a Impuestia (modificado)
- `requirements.txt` — se agregó `google-genai>=2.0.0` (el fix real del deploy
  roto de todo un mes) (modificado)
- `web_server.py` — dashboard sin NotebookLM + keepalive de Supabase cada 24h
  en el `lifespan` de FastAPI (modificado)

---

# Sesión 2026-07-13 — Primeros árboles de decisión reales (LIR + Código Tributario)

## Contexto

Con el bot ya funcionando bien en producción (confirmado con dos preguntas reales
en Telegram sobre venta de inmuebles, citando Art. 17 N° 8 correctamente), el
usuario dio la instrucción de fondo para esta fase: **el Código Tributario es lo
que más le cuesta a los contadores** — la prioridad es construir árboles de
decisión para sus temas más relevantes (mencionó: citaciones, facultad de
tasación, términos de giro, multas). Se le hizo notar que citaciones (árbol 1,
Art. 63) y multas (árbol 5, Art. 97-98) ya estaban cubiertos — no se duplicaron.

Limitación técnica de toda la sesión: este sandbox no tiene acceso real a
internet (mismo bloqueo SSL de siempre), así que `scripts/draft_tree_cli.py`
(que llama al LLM) no se pudo ejecutar. En su lugar, cada árbol se armó **a
mano**, construyendo directamente los objetos `DraftTree`/`DraftNode` de
`schemas.py` en un script Python — pero el contenido de cada uno se extrajo y
verificó contra el **texto real de las leyes** (los PDFs locales de DL-824 y
DL-830, usando `legal_parser.py`), nunca inventado de memoria.

## Árboles nuevos (6 total, todos en `decision_trees/_drafts/`, todos validados)

Cada uno se cargó con el parser real de `decision_engine.py` (mismo que usa el
botón "Aprobar") y se probó CADA camino posible de principio a fin antes de
darlo por terminado.

### LIR (Ley de Impuesto a la Renta, DL-824)
1. **`venta_inmuebles_persona_natural`** (Art. 17 N° 8 letra b) — tope de 8.000
   UF, plazo de 1/4 años, opción Global Complementario/Adicional vs. 10%
   sustitutivo. Tiene un nodo `info` marcado explícitamente **"PENDIENTE DE
   VALIDACIÓN LEGAL"**: el usuario recuerda que antes de cierta reforma no se
   pagaba nada por este concepto — el año exacto del cambio de régimen
   (probablemente Ley 20.780/20.899, 2014-2017) está en disposiciones
   transitorias que no se verificaron, así que NO se inventó una fecha.
2. **`venta_acciones_derechos_sociales`** (Art. 17 N° 8 letra a) — tope de 10
   UTA (de minimis combinado entre letras a/c/d), compensación de pérdidas, y
   la opción de reliquidar el mayor valor como renta devengada repartida hasta
   10 años de tenencia. Sin puntos pendientes.
3. **`boletas_honorarios`** (Art. 42 N° 2 + Art. 74 N° 2) — cubre persona
   natural vs. sociedad de profesionales (con su opción irrevocable de
   tributar en primera categoría), y cuándo aplica la retención. Tiene una
   advertencia explícita: la tasa de retención (17% en el texto actual) viene
   de un cronograma de alza gradual por la Ley 21.133 — se marcó para
   confirmar la tasa vigente del año en curso, no darla por fija.

### Código Tributario (DL-830) — el pedido explícito del usuario
4. **`facultad_tasacion`** (Art. 64) — cuándo el SII puede tasar el precio/valor
   de una operación que difiere notoriamente del mercado; excepción para
   reorganizaciones empresariales con legítima razón de negocios (y la
   contra-excepción si el destino es un territorio de baja/nula tributación);
   caso especial de bienes raíces (tasa y gira de inmediato, sin citación
   previa). 13 nodos, 7 caminos probados.
5. **`termino_de_giro`** (Art. 69) — aviso normal vía carpeta tributaria
   electrónica, término simplificado para PRO-PYME (Art. 14 D LIR), excepción
   de aviso para conversión de empresa individual/fusión con responsabilidad
   solidaria, y la facultad del SII de liquidar de oficio (con aumento de 1
   año en la prescripción) cuando detecta un cierre no informado.
6. **`notificaciones_validas`** (Art. 11 a 15) — correo electrónico (medio por
   defecto desde la Ley 21.713), notificación personal, por cédula, y por
   carta certificada (con el efecto de 3 meses de aumento en la prescripción
   si la carta no se entrega). Es transversal: varios árboles ya existentes
   preguntan "¿fue notificado válidamente?" sin explicar qué significa eso —
   este árbol lo cubre.

## Pendientes para mañana

1. **El usuario va a revisar los 6 borradores** en `/review/drafts` (login
   admin del panel Impuestia) antes de aprobarlos.
2. **Confirmar los dos puntos marcados como pendientes de validación legal**
   antes de aprobar esos árboles: el año de la reforma de 8.000 UF (inmuebles)
   y la tasa de retención vigente del año en curso (honorarios) — buscar en
   LeyChile (BCN) las disposiciones transitorias de Ley 20.780/20.899/21.210
   para el primero, y el cronograma de Ley 21.133 para el segundo.
3. **Seguir construyendo árboles del Código Tributario** — quedan temas
   grandes sin cubrir: Norma General Anti-elusión (Art. 4 bis a quinquies),
   delitos tributarios vs. infracciones simples (dentro de Art. 97, hoy solo
   cubierto de forma genérica por el árbol 5), Revisión de la Actuación
   Fiscalizadora. Evaluar cuáles priorizar con el usuario antes de seguir en
   modo automático.
4. Pendientes de sesiones anteriores sin resolver todavía: `TAVILY_API_KEY`,
   `scripts/eval_graph_lift.py` (falta acumular más queries reales), y el
   frontend de carga de documentos/pruebas de respuesta (pospuesto).

## Palabras clave para recordar mañana

> "**árboles**" (con contexto de continuar) → revisar con el usuario si ya
> aprobó los 6 borradores de `/review/drafts`, resolver los 2 puntos
> pendientes de validación legal, y seguir con más árboles del Código
> Tributario (NGA, delitos tributarios, RAF) en modo automático salvo que algo
> requiera su confirmación.

## Archivos nuevos/modificados clave (sesión de hoy)

- `decision_trees/_drafts/venta_inmuebles_persona_natural.json` (nuevo)
- `decision_trees/_drafts/venta_acciones_derechos_sociales.json` (nuevo)
- `decision_trees/_drafts/boletas_honorarios.json` (nuevo)
- `decision_trees/_drafts/facultad_tasacion.json` (nuevo)
- `decision_trees/_drafts/termino_de_giro.json` (nuevo)
- `decision_trees/_drafts/notificaciones_validas.json` (nuevo)
