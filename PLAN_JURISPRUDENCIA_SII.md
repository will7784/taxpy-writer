# Plan — Replicar base de fallos TTA + oficios/circulares (competir con Oficio&Circular)

> Creado: 2026-08-30 · Tras verificar la API ACJ del SII (NO hay geobloqueo, ver `agent.md` §10).

## Contexto / hechos ya verificados (no re-investigar)

- La API ACJ del SII responde desde Colombia con HTTP 200 y datos reales. **No hay geobloqueo.**
- Base: `https://www4.sii.cl/acjui/services/data/internetService/`
- Formato request: métodos `list*` → **PLANO** (`{namespace, conversationId, transactionId, page}`);
  métodos `find*`/`get*` → **ENVUELTO** (`{metaData:{...}, data:{...}}`). Sin cookie/sesión.
- Cuerpos normativos: `1`=Código Tributario, `2`=LIR, `3`=IVA (hay ~95).
- La app pública solo expone **Jurisprudencia Judicial** (fallos TTA). Los **oficios** (instancia
  administrativa) son intranet-only; para circulares usar el scraper web `sii_circulares.py`.

## Fase 1 — Corregir el scraper ACJ (`scrapers/sii_acj.py`)

1. **Arreglar formato de request** (bug confirmado): `list_cuerpos_normativos()` y
   `listTiposInstancia` deben mandar body PLANO, no `{metaData, data:{}}`. Hoy `list_cuerpos_normativos`
   manda envuelto → devuelve 400 `Unrecognized field "metaData"`.
2. **Revisar `_post()`**: tras corregir la URL base, el `ACJ_BASE_URL` ya es correcto; validar que los
   paths por método coincidan (cuerpos-normativos, find-articulos, find-pronunciamientos,
   pronunciamientos/get-full).
3. **Agregar `list_tipos_instancia()`** (falta; útil para confirmar judicial vs administrativa).
4. **Probar con un script de smoke test**: listar cuerpos → findArticulos(2) → findPronunciamientos
   (art 21) → getFullPronunciamiento. Verificar que cada paso devuelve 200 y datos.

## Fase 2 — Extracción correcta de metadata del fallo (lo que nos iguala al competidor)

En `pron_to_md()` capturar, desde `getFullPronunciamiento`, y escribir como frontmatter + cuerpo:

- `tipoCodigo.nombre` + `codigoPronunciamiento` → **RIT** (p. ej. "GR-08-00068-2017").
- `instancia.nombre` → **tribunal**; `fecha` → fecha.
- `decision.nombre` (acoge/rechaza/ha lugar en parte) y `resultado.texto`.
- `ruc`, `partes` → **carátula / partes**.
- `contenido.resumen` / `extracto` / `sentencia` (texto del fallo).
- `pronunciamientosArticulos[]` → ley + artículo (para el índice invertido `article_index.py`).
- `urlDocumento` → link a fuente (para citas verificables).

Cada .md debe llevar frontmatter `tipo: jurisprudencia`, `articulo`, `ley`, `rit`, `tribunal`,
`fecha` para que `article_index.py` lo indexe automáticamente.

## Fase 3 — Pipeline de sincronización completo (`scripts/sync_sii.py`)

5. Recorrer los 3 cuerpos prioritarios (1, 2, 3) → todos sus artículos → pronunciamientos.
6. **Paginación**: `findPronunciamientos` puede paginar; manejar `page`/`max_results` y no cortar.
7. **Politeness + resiliencia**: delay entre requests, retry con backoff, guardar estado
   (`_sync_state.json`) para reanudar sin repetir. Guardar JSON crudo además del .md.
8. **Dedupe** por `content_hash` / `pron_id` (ya parcial en sync_sii.py).
9. Tras el sync: correr `scripts/build_article_index.py` para reindexar.

## Fase 4 — Integración en el agente (citar fallos en las respuestas)

10. Verificar que `context_router`/`prompt_builder` (en `context_rag/`) inyectan jurisprudencia desde
    `article_index` junto con la ley (hoy `article_index` ya escanea `documents/jurisprudencia_sii*`).
11. Hacer que las respuestas citen el fallo con **RIT + carátula + link**, formato chileno listo para
    copiar (diferenciador clave del competidor).
12. Actualizar `agent.md` §3 (reglas de cita) para fallos TTA con RIT/carátula.

## Fase 5 — Circulares y oficios (fuente administrativa)

13. Circulares: usar `sii_circulares.py` (ya existe) + **extraer texto del PDF** (OCR si es escaneado)
    para que el contenido sea consultable, no solo el índice.
14. Oficios: documentar que el ACJ público no los expone (intranet-only). Opciones a evaluar:
    (a) fuente alternativa pública del SII (normativa_legislacion), (b) prescindir en v1 y centrarse
    en fallos TTA + circulares.

## Fase 6 — Avanzado (paridad con el plan Empresa del competidor)

15. **Doctrina CS/CA**: los fallos TTA citan textualmente criterios de Corte Suprema / C. de
    Apelaciones. Extraer el criterio citado + cadena de fallos que lo respaldan.
16. **Connect / MCP**: exponer la base vía MCP para Claude/ChatGPT (feature Enterprise). Evaluar
    después de tener la base indexada.

## Orden sugerido de implementación (próximas sesiones)

1. Fase 1 (corregir scraper + smoke test) — crítica, desbloquea todo.
2. Fase 2 (metadata del fallo) — entrega el valor diferencial.
3. Fase 3 (pipeline completo + reindexar).
4. Fase 4 (integrar citas en respuestas).
5. Fases 5-6 según prioridad.
