# Mejoras de Precisión RAG — Resumen de Cambios

## Contexto
Tras revisar el análisis `analisis_lightrag_saas_impuestos_chile.pdf`, decidimos **evolucionar el stack actual (Supabase pgvector)** en lugar de migrar a LlamaIndex/Neo4j/Weaviate, aplicando las mejores prácticas del análisis de forma pragmática y económica.

## Cambios implementados

### 1. Temperatura del LLM reducida drásticamente
- **Chat (Telegram)**: `0.7` → `0.1`
- **Documentos (writer)**: `0.5` → `0.2`
- **Default LLM**: `0.7` → `0.1`
- **Impacto**: Mucho menos alucinación en citas legales. El modelo es más conservador y se apega a las fuentes.

### 2. Reglas de dominio expandidas (14 reglas)
El RAG ahora fuerza automáticamente chunks clave cuando detecta ciertos conceptos, evitando que la búsqueda semántica "pase de largo" el artículo correcto:

| Concepto usuario | Chunk forzado |
|---|---|
| PRO-PYME + intereses/deuda | Art. 14 D LIR + Art. 192 CT |
| PRO-PYME (solo) | Art. 14 D LIR |
| Gastos rechazados | Art. 21 LIR |
| Depreciación / activo fijo | Art. 31 LIR |
| Crédito fiscal / devolución | Art. 33 bis LIR |
| Renta presunta | Art. 20 LIR |
| Citación SII | Art. 63 CT |
| Liquidación / giro | Art. 64 CT |
| Prescripción | Art. 200-201 CT |
| Infracciones / multas | Art. 97 CT |
| IVA débito/crédito | Art. 11 + 12 DL-825 |
| Retención de IVA | Art. 74 DL-825 |
| Condonación intereses | Art. 56 CT |
| Régimen simplificado (14 E) | Art. 14 E LIR |
| Dividendos / retiros | Art. 17 LIR |

### 3. Notas de dominio enriquecidas
El contexto enviado al LLM ahora incluye notas explicativas automáticas para conectar conceptos coloquiales con términos legales formales:
- "Artículo 14 letra D = régimen PRO-PYME"
- "Art. 31 N°5 = depreciación"
- "Art. 200-201 = prescripción tributaria"
- etc.

### 4. Chunking jerárquico para artículos largos
**Nuevo**: Los artículos de ley > 3.500 caracteres se dividen automáticamente en sub-chunks:
- **Padre**: artículo completo (embedding del texto completo)
- **Sub-chunks**: por incisos (`a)`, `b)`, `1.`, etc.) o por ventanas deslizantes con overlap
- Cada sub-chunk incluye el header del artículo para contexto
- Al recuperar un sub-chunk, el sistema **también trae el padre** automáticamente

**Archivo modificado**: `ingest.py` (PDFLawParser)

### 5. Guardrail de citas post-respuesta
**Nuevo módulo**: `citation_guardrail.py`

Después de que el LLM genera una respuesta:
1. Extrae automáticamente todas las citas legales (`Art. X`, `Ley Y`, etc.)
2. Verifica que existan en el contexto enviado
3. Si encuentra citas no verificadas, agrega una nota de advertencia al final

Ejemplo:
```
Si, segun el Art. 192 del Codigo Tributario no se aplican intereses. 
Tambien el Art. 999 de la Ley de Renta lo dice.

[WARN] Nota de verificacion: No pude confirmar en las fuentes consultadas: 
'Art. 999 de la Ley de Renta lo dice' Por favor verifica estas citas 
directamente en la norma.
```

### 6. Relaciones críticas del grafo expandidas
**Archivo modificado**: `critical_relations.py`

Se agregaron 20+ relaciones legales "inmutables" al grafo de conocimiento:
- PRO-PYME ↔ Art. 192 CT
- Art. 21 LIR ↔ Art. 31 LIR
- IVA débito/crédito ↔ retención
- Citación → liquidación → prescripción
- etc.

### 7. Grafo más robusto
**Archivo modificado**: `graph_engine.py`

- Búsqueda por prefijo: si una relación apunta a `ley_lir_art_21` pero en la DB existe `ley_lir_art_21_sub_0`, ahora resuelve correctamente
- `get_chunk_by_uid` y `get_neighbors` ahora son resilientes a sub-chunks

## Pendiente — Pasos manuales que debes hacer

### Paso 1: Crear tabla `knowledge_graph` en Supabase
1. Ve a tu proyecto Supabase → SQL Editor
2. Ejecuta esta sección del archivo `sql/supabase_rag_schema.sql`:

```sql
-- Tabla: grafo de conocimiento (GraphRAG)
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
```

3. También agrega la columna `parent_chunk_uid` a `document_chunks` si no existe:

```sql
alter table public.document_chunks 
add column if not exists parent_chunk_uid text null;
```

### Paso 2: Re-indexar leyes principales (aprovechar chunking jerárquico)
El nuevo chunking jerárquico solo se aplica a documentos nuevos. Para que las leyes existentes se beneficien:

```bash
# Opción A: Re-indexar TODO (toma tiempo, consume tokens de embedding)
python -m ingest

# Opción B: Re-indexar solo los PDFs de leyes principales
python -c "
import asyncio
from pathlib import Path
from ingest import IngestionPipeline
import config

async def main():
    pipeline = IngestionPipeline()
    count = await pipeline.ingest_leyes_pdf(config.DOCUMENTS_DIR)
    print(f'Re-indexadas: {count} chunks')

asyncio.run(main())
"
```

**Nota**: Los chunks padres tendrán el mismo `chunk_uid` que antes, así que el upsert los actualizará sin duplicar. Los sub-chunks nuevos se agregarán automáticamente.

### Paso 3: Insertar relaciones críticas en el grafo
Una vez creada la tabla `knowledge_graph`, las relaciones se insertarán automáticamente la próxima vez que se ejecute la ingestión. O puedes forzarlo:

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

### Paso 4: Deploy en Railway
```bash
git add -A
git commit -m "mejoras RAG: chunking jerarquico, guardrails, reglas dominio, temp baja"
git push origin main
```

## Validación
Ejecuta el test de calidad para verificar que todo funciona:

```bash
python scripts/test_rag_quality.py
```

Resultado esperado (6/6 PASS):
- PRO-PYME + intereses → Art. 192 CT + Art. 14 D LIR
- Gastos rechazados → Art. 21 LIR
- Depreciación → Art. 31 LIR
- Citación SII → Art. 63 CT
- Prescripción → Art. 200-201 CT
- Crédito fiscal IVA → Art. 12 DL-825

## Costos
- **Sin cambios en infraestructura**: Seguimos en Supabase pgvector (gratis/hobby)
- **Tokens de embedding adicionales**: ~$0.50-2.00 si re-indexas todas las leyes (por los sub-chunks nuevos)
- **Sin costos de Neo4j, Weaviate ni LlamaIndex**

## Próximos pasos recomendados
1. **Monitorizar respuestas reales del bot** durante 1 semana
2. **Agregar más reglas de dominio** según las consultas que fallen
3. **Implementar RAG particionado por dominio** (Legislativo / Jurisprudencial / Administrativo) cuando el corpus crezca > 10.000 chunks
4. **Evaluar LlamaParse** para mejorar extracción de tablas en PDFs (costo ~$0.03/página)
