# Conectar un harness externo (DeepSeek Harness) por MCP

Impuestia ahora expone sus herramientas del backend como **servidor MCP**
(`mcp_server.py`, montado en `web_server.py`). Esto permite que un agente/harness
externo — p. ej. el **DeepSeek Harness** (`@deepseek-ai/dsh-mcp-client`) — use todo
el corpus curado (biblioteca, jurisprudencia, notas, Co-Work por cliente) sin tocar
el backend.

## 1. Qué se expone

| Tool MCP (nombre `mcp__impuestia__<tool>`) | Qué hace |
|---|---|
| `listar_clientes` | Lista clientes del Co-Work |
| `listar_casos(cliente)` | Lista trabajos/expedientes de un cliente |
| `procesar_caso(cliente)` | Procesa la carpeta `entrada/` (OCR + análisis) |
| `buscar_jurisprudencia(consulta)` | Busca jurisprudencia/notas por artículo/keywords |
| `buscar_notas(consulta)` | Busca solo notas aprobadas |
| `obtener_norma(clave)` | Entrada del catálogo legal (`lir`, `iva`, `ct`, …) |
| `investigar(consulta, cliente, fecha_hechos)` | Flujo completo de investigación con respaldo |
| `escribir_informe(cliente, titulo, contenido)` | Escribe el informe en el vault del cliente |

## 2. Autenticación

Bearer token simple (no OAuth 2.1). El agente debe enviar:

```
Authorization: Bearer <MCP_TOKEN>
```

- Si `MCP_TOKEN` está **vacío**, el endpoint queda abierto (solo **dev local**).
- En producción **siempre** setea `MCP_TOKEN` en Railway.

## 3. Configurar el harness de DeepSeek

Endpoint SSE (transporte `http`): `{MCP_MOUNT_PATH}/sse` → por defecto **`/mcp/sse`**.

En la Web UI de dsh:
`Configuración → MCP → + Agregar servidor MCP`
→ transporte **HTTP/SSE**, nombre `impuestia`, URL según tu entorno:

- **Local:** `http://localhost:8000/mcp/sse`
- **Railway:** `https://taxpy-writer-production.up.railway.app/mcp/sse`

O en el `profile`/YAML:

```yaml
plugins:
  - name: "@deepseek-ai/dsh-mcp-client"
    config:
      transport: "http"
      serverName: "impuestia"
      url: "http://localhost:8000/mcp/sse"   # o la URL pública de Railway
      # Si dsh-mcp-client soporta headers, envía el token:
      # headers:
      #   Authorization: "Bearer <MCP_TOKEN>"
```

Una vez conectado, las tools se registran como `mcp__impuestia__<tool>`.

## 4. Variables de entorno

| Variable | Descripción |
|---|---|
| `MCP_TOKEN` | Bearer token que exige el endpoint. Vacío = dev (abierto). |
| `MCP_MOUNT_PATH` | Ruta base del MCP (default `/mcp` → SSE en `/mcp/sse`). |

## 5. Notas / transporte

- Se usa el transporte **SSE** porque es el que documenta el harness.
- MCP también define **Streamable HTTP** (transporte moderno, un solo endpoint
  `/mcp`). Si tu cliente solo lo soporta, cambia `mcp.sse_app()` por
  `mcp.streamable_http_app()` en `build_mcp_app()` (y ajusta la URL sin `/sse`).
- Dependencias fijadas en `requirements.txt` para que `mcp` coexista con
  `fastapi==0.115.0`: `starlette<0.39` y `sse-starlette<2`. **No cambiar** esas
  cotas sin revisar la compatibilidad.
