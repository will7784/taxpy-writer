# Ingesta automática de documentos (webhook tipo n8n / Drive)

Un flujo externo (n8n, Dropbox, Google Drive, Zapier...) puede subir documentos
de clientes a Impuestia automáticamente vía el endpoint de ingesta. El documento
se deja en la carpeta `entrada/` del cliente del Co-Work y, opcionalmente, se
dispara el pipeline (OCR + análisis) para que quede consultable con el agente.

## Endpoint

```
POST /api/ingest/{cliente}
```

Autenticación (cabeceras):
```
X-API-Key: <INGEST_TOKEN>
# o
Authorization: Bearer <INGEST_TOKEN>
```

Si `INGEST_TOKEN` está vacío el endpoint queda **abierto** (solo dev).

## Cómo enviar el documento

**Opción A — archivo (multipart).** Ideal para n8n Webhook / HTTP Request:

```bash
curl -X POST http://localhost:8000/api/ingest/Nano_Calderon \
  -H "X-API-Key: <INGEST_TOKEN>" \
  -F "material=@caso_contrato.pdf" \
  -F "auto=1"
```

**Opción B — URL a descargar (JSON).** Si el flujo solo tiene el enlace del archivo:

```bash
curl -X POST http://localhost:8000/api/ingest/Nano_Calderon \
  -H "X-API-Key: <INGEST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://drive.google.com/uc?export=download&id=___", "filename":"escritura.pdf", "auto":1}'
```

## Parámetros

| Param | Descripción |
|---|---|
| `material` | Archivo (multipart). Extensiones: PDF, DOCX, TXT, MD, PNG, JPG, WEBP, TIFF, BMP. Máx 25 MB. |
| `url` | URL a descargar (alternativa a `material`). |
| `auto` | `1` (default) dispara el pipeline; `0` solo deja el archivo en `entrada/`. |
| `filename` | (solo JSON) nombre con el que guardar el archivo descargado. |

## Respuesta

```json
{
  "ok": true,
  "cliente": "Nano_Calderon",
  "filename": "caso__contrato.pdf",
  "procesados": [{"tipo": "analisis", "estado": "completado", "titulo": "..."}],
  "errores": []
}
```

## Variables

| Variable | Descripción |
|---|---|
| `INGEST_TOKEN` | Token que exige el endpoint. Vacío = abierto (dev). |
| `INGEST_AUTOPROCESS` | `1`/`true`/`yes` para procesar automáticamente al recibir. |

## Ejemplo n8n

1. Trigger: **Google Drive / Dropbox → archivo nuevo** en una carpeta por cliente.
2. **HTTP Request**: `POST https://<railway-url>/api/ingest/<cliente>` con `X-API-Key` header
   y el archivo binario en `material` (o `url` en el body JSON).
3. Listo: el documento queda en `entrada/` y, si `auto=1`, ya procesado y consultable.

> Nota: la carpeta del cliente debe existir (creada desde el panel Co-Work o
> con `list_clientes`/Co-Work). Si `auto=1`, el pipeline usa el LLM configurado
> (por defecto DeepSeek V4 Flash) para OCR/redacción.
