# Salida a Notion (Fase 4)

Publica los informes generados por el agente en una **base de datos de Notion**,
para que el resultado quede en una herramienta que el profesional ya usa.

## Requisitos previos en Notion

1. Crea una **integración interna** en https://www.notion.so/my-integrations
   → copia el token (`ntn_...` o `secret_...`).
2. Crea una **base de datos** (o usa una existente). Comparte esa página/DB con la
   integración (invitarla con el email de la integración). Copia el **id de la DB**
   (de la URL: `...<32-char-id>?v=...`).
3. La DB debe tener una **propiedad de tipo Título** (por defecto `Name`).

## Variables de entorno

| Variable | Descripción |
|---|---|
| `NOTION_API_KEY` | Token de integración interna. |
| `NOTION_DATABASE_ID` | Id de la base de datos destino. |
| `NOTION_TITLE_PROPERTY` | Propiedad de título (default `Name`). |

## Formas de publicar

**1. Vía MCP (harness DeepSeek)** — tool `publicar_notion(titulo, contenido)`:
el agente publica el informe y devuelve la URL.

**2. Vía REST** — `POST /api/notion/publish` (auth bearer/X-API-Key con `INGEST_TOKEN`):

```bash
curl -X POST http://localhost:8000/api/notion/publish \
  -H "X-API-Key: <INGEST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"titulo": "Informe: aporte de inmueble a una SpA", "contenido": "# Conclusión ..."}'
```

Respuesta:
```json
{ "ok": true, "url": "https://www.notion.so/..." }
```

## Soporte de markdown
La herramienta convierte markdown ligero a bloques de Notion: `#`/`##`/`###` →
encabezados, `-`/`*` → viñetas, `1.` → lista numerada, y el resto → párrafos.
Los bloques se envían en lotes de 100 (el límite de la API).

## Notas
- La herramienta **no borra ni edita** páginas existentes; siempre crea una nueva.
- En `auto` (ingesta) el informe se genera con el LLM; después lo publicas a Notion.
- El token de Notion debe tener permiso de **escritura** en la base de datos destino.
