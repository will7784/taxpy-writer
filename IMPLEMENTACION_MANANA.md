# Guia de Implementacion — Mejoras RAG (paso a paso desde Supabase)

> Fecha: manana (2026-06-04)
> Contexto: ya hiciste `git pull` y tienes el codigo con las mejoras en tu maquina.

---

## PASO 0: Asegurate de tener el codigo actualizado

```bash
cd c:/Users/lyf-a/Dropbox/AGENTES\ DE\ IA/PROYECTOS\ KIMI/_Proyectos_en_Produccion/Tax_en_produccion/Rag_tributario_telegram
git pull origin main
```

---

## PASO 1: Crear tabla `knowledge_graph` en Supabase

1. Abre tu proyecto Supabase: https://supabase.com/dashboard/project/ylquqglvcnjwatqgfabj
2. Ve al menu **SQL Editor** (izquierda)
3. Crea un **New query**
4. Pega ESTO EXACTO y ejecuta (boton verde **Run**):

```sql
-- ============================================
-- Tabla: grafo de conocimiento (GraphRAG)
-- ============================================
create table if not exists public.knowledge_graph (
  id bigserial primary key,
  source_chunk_uid text not null,
  target_chunk_uid text not null,
  relation_type text not null,
  confidence float not null default 1.0,
  extracted_by text not null default 'llm',
  created_at timestamptz not null default now()
);

create index if not exists idx_kg_source on knowledge_graph(source_chunk_uid);
create index if not exists idx_kg_target on knowledge_graph(target_chunk_uid);
create index if not exists idx_kg_type on knowledge_graph(relation_type);

-- ============================================
-- Columna parent_chunk_uid (chunking jerarquico)
-- ============================================
alter table public.document_chunks 
add column if not exists parent_chunk_uid text null;
```

5. Deberia decir "Success. No rows returned" (o similar).

---

## PASO 2: Re-indexar los PDFs de leyes (aprovechar chunking jerarquico)

El nuevo sistema divide articulos largos en sub-chunks por incisos. Esto solo se aplica a documentos nuevos, asi que debes re-procesar los PDFs de leyes.

```bash
# Ve a la carpeta del proyecto
cd c:/Users/lyf-a/Dropbox/AGENTES\ DE\ IA/PROYECTOS\ KIMI/_Proyectos_en_Produccion/Tax_en_produccion/Rag_tributario_telegram

# Activa tu entorno virtual (si usas uno)
# Si no usas venv, salta este paso
# source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate     # Windows

# Ejecuta la re-indexacion de leyes
python -c "
import asyncio
from pathlib import Path
import config
from ingest import IngestionPipeline

async def main():
    pipeline = IngestionPipeline()
    count = await pipeline.ingest_leyes_pdf(config.DOCUMENTS_DIR)
    print(f'Total chunks procesados: {count}')

asyncio.run(main())
"
```

**Que hace esto:**
- Re-lee los PDFs (DL-824, DL-825, DL-830)
- Los articulos cortos se mantienen igual
- Los articulos largos (>3.500 chars) se dividen en sub-chunks por incisos
- Los chunks padres se actualizan (mismo UID, no se duplican)
- Los sub-chunks nuevos se agregan a la base de datos
- **Costo**: ~$0.50-$2.00 en tokens de OpenAI embeddings (por los nuevos sub-chunks)

**Tiempo estimado**: 5-15 minutos

---

## PASO 3: Insertar relaciones criticas en el grafo

Una vez que la tabla `knowledge_graph` existe, inserta las relaciones legales hardcodeadas:

```bash
python -c "
import asyncio
from critical_relations import get_critical_relations
from graph_engine import graph

async def main():
    rels = get_critical_relations()
    inserted = await graph.insert_relations(rels)
    print(f'Relaciones insertadas: {inserted}')

asyncio.run(main())
"
```

**Resultado esperado**: `Relaciones insertadas: 24` (o el numero que tengamos)

---

## PASO 4: Probar que todo funciona

Ejecuta el test de calidad:

```bash
python scripts/test_rag_quality.py
```

**Resultado esperado** (6/6 PASS):
```
PASS: beneficio propyme intereses
PASS: gastos rechazados tributariamente
PASS: depreciacion activos fijos
PASS: sii cita fiscalizar
PASS: prescripcion deudas tributarias
PASS: credito fiscal iva

Total: 6 PASS, 0 FAIL
```

Si algun test falla, revisa que:
- La tabla `knowledge_graph` exista (PASO 1)
- Las leyes esten re-indexadas (PASO 2)
- Tengas conexion a Supabase (verifica `.env`)

---

## PASO 5: Deploy en Railway

```bash
git add -A
git commit -m "deploy: mejoras RAG en produccion"
git push origin main
```

Railway deberia hacer deploy automatico. Verifica en el dashboard de Railway que no haya errores en los logs.

---

## PASO 6: Probar en Telegram

Mandale un mensaje a tu bot con la pregunta critica:

> "¿Hay algun beneficio de rebaja de interes para el regimen PRO-PYME?"

**Respuesta esperada**:
> "Si. Segun el Art. 192 del Codigo Tributario, para los contribuyentes del regimen PRO-PYME —regulado por el Art. 14 letra D de la LIR— no se aplican intereses sobre las cuotas de convenios de hasta 18 meses."

Si la respuesta inventa un articulo, revisa que:
- El guardrail este funcionando (deberia agregar `[WARN] Nota de verificacion...`)
- La temperatura este en 0.1 (revisa logs de Railway)

---

## Checklist rapido (marcar al completar)

- [ ] PASO 1: Tabla `knowledge_graph` creada en Supabase SQL Editor
- [ ] PASO 1: Columna `parent_chunk_uid` agregada a `document_chunks`
- [ ] PASO 2: Leyes re-indexadas (sub-chunks creados)
- [ ] PASO 3: Relaciones criticas insertadas en el grafo
- [ ] PASO 4: Test `scripts/test_rag_quality.py` pasa 6/6
- [ ] PASO 5: Codigo pusheado a GitHub / Railway
- [ ] PASO 6: Bot de Telegram responde correctamente la pregunta PRO-PYME

---

## Si algo falla

**Error: "Tabla knowledge_graph no existe"**
→ No ejecutaste el PASO 1. Ve a Supabase SQL Editor y corre el SQL.

**Error: "GraphRAG desactivado"**
→ Normal si la tabla no existe. Desaparece despues del PASO 1.

**Error en embeddings (max_tokens_per_request)**
→ Disminuye `EMBEDDING_BATCH_SIZE` en `ingest.py` (linea ~397) de 15 a 10.

**Relaciones criticas no se insertan**
→ Verifica que la tabla exista primero (PASO 1), luego corre PASO 3 de nuevo.

---

## Resumen de los cambios que ya estan en el codigo

| Cambio | Archivo principal |
|---|---|
| Temperatura 0.1 en chat | `telegram_mvp_bot.py`, `llm_client.py`, `config.py` |
| 14 reglas de dominio | `rag_engine.py` |
| Notas de dominio enriquecidas | `rag_engine.py` |
| Chunking jerarquico | `ingest.py` |
| Guardrail de citas | `citation_guardrail.py` |
| Relaciones criticas expandidas | `critical_relations.py` |
| Grafo robusto (prefijos) | `graph_engine.py` |
| Test de calidad | `scripts/test_rag_quality.py` |

**Todo listo para manana. Suerte con la implementacion.**
