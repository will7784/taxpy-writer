# Pendientes

> Última actualización: 2026-08-18

## 1. Sincronización automática vault producción → Obsidian (Dropbox)

**Objetivo:** que todo lo que se genere en producción (estudios, jurisprudencia, notas, peticiones) aparezca automáticamente en el Obsidian local, con etiquetas (tags) y grafos comunes, sin intervención manual.

**Estado actual:**
- Vault de producción: volumen de Railway `impuestia-rag-volume` montado en `/data`, con `OBSIDIAN_VAULT_PATH=/data/vault` (ya configurado y funcionando).
- Obsidian local: `C:\Users\lyf-a\Dropbox\OBSIDIAN\Impuestia` (sincronizado por Dropbox).
- **Transporte implementado (endpoint de export en el panel):**
  - `GET /api/vault/manifest` — lista de archivos + sha256 + mtime (sync incremental).
  - `GET /api/vault/export` — zip del vault + `_MANIFEST.json`. Excluye `.obsidian/`, `.trash/`, `.git/`.
  - `sync_vault.py` (local) — descarga el zip, mergea idempotente: descarga/sobrescribe si el local está intacto, **no pisa ediciones locales** (copia remota va a `_Sync/Conflictos/`), y elimina archivos borrados en prod solo si el local está intacto.
  - Estado previo por archivo en `sync_state.json` (gitignored). Flags: `--dry-run`, `--full`.
  - Config: `SYNC_PROD_URL`, `SYNC_STATE_PATH` en `config.py`.

**Pendiente de resolver:**
1. **Automatizar la sync local** (solo falta el horario): programar `python sync_vault.py` con el Programador de tareas de Windows (o un `.bat`/`.vbs` a escondidas al inicio). Queda pendiente de decidir frecuencia (ej. cada 30 min).
2. **Tags y grafos comunes (mejora opcional):** `obsidian_writer.py` ya escribe frontmatter (tipo, cliente, fecha, fuentes, articulo) y tags por ley. Para que el Graph View sea más útil, falta que el contenido generado embeba `[[wikilinks]]` entre notas (estudio ↔ jurisprudencia ↔ ley) — es del lado de generación, no del sync.

**Dónde tocar (cuando se implemente):**
- `obsidian_writer.py` (generación de frontmatter, tags y wikilinks).
- `sync_vault.py` + `web_server.py` (ya creados).

---

## Contexto de la sesión (referencia rápida)

- Repo: `github.com/will7784/taxpy-writer` (rama `main`, con `feat/context-rag` mergeada).
- Producción: servicio Railway `ImpuestIA-RAG` (proyecto `beneficial-comfort`), URL `https://taxpy-writer-production.up.railway.app`.
- Deploy: auto-deploy por push a `main` (GitHub integration).
- LitM: mitigado con `litm.py` (reordenamiento en U) + pre-resumir (`PRE_SUMMARIZE`).
