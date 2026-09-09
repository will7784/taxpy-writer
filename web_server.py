"""
Servidor web FastAPI para panel de administración de Impuestia.

Incluye:
- Login simple con sesiones
- Dashboard con estado del bot y notebooks
- Gestión de cuadernos primario/secundario
- Upload de storage_state.json
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import shutil
import asyncio
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from article_index import article_index
from decision_tree_drafter import DRAFTS_DIR, to_mermaid
from obsidian_writer import (
    list_clientes, get_cliente_dir, list_cliente_files, init_cliente_structure,
    write_note, VAULT,
)
from settings_store import store as settings_store
from skill_manager import list_skills, load_skill, delete_skill, create_skill_from_template, get_skill_templates

TREES_DIR = config.BASE_DIR / "decision_trees" / "codigo_tributario"

logger = logging.getLogger(__name__)

# ── Configuración FastAPI ─────────────────────────────────

ADMIN_USERNAME = config.ADMIN_USERNAME
ADMIN_PASSWORD = config.ADMIN_PASSWORD
SESSION_SECRET = config.SESSION_SECRET

templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


# ── Helpers NotebookLM ────────────────────────────────────

def _nb_manager(name: str | None = None):
    """Factory segura de NotebookLMManager."""
    try:
        from notebooklm_manager import NotebookLMManager
        return NotebookLMManager(notebook_name=name or config.NOTEBOOKLM_NOTEBOOK_NAME)
    except Exception:
        return None


async def _list_notebooks_from_api() -> list[dict]:
    """Lista notebooks directo desde NotebookLM API."""
    mgr = _nb_manager()
    if not mgr:
        settings_store.set("notebooklm_last_error", "notebooklm-py no está disponible (fallo al importar)")
        return []
    try:
        notebooks = await mgr.list_notebooks()
        result = []
        for nb in notebooks:
            nb_id = nb.get("id", "")
            try:
                sources = await mgr.get_notebook_sources(nb_id)
                source_count = len(sources)
            except Exception:
                source_count = 0
            result.append({
                "id": nb_id,
                "name": nb.get("name", "Sin nombre"),
                "source_count": source_count,
            })
        settings_store.save_notebooks(result)
        settings_store.set("notebooklm_last_error", "")
        return result
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)}"
        settings_store.set("notebooklm_last_error", error_msg)
        logger.exception("NotebookLM connection failed: %s", e)
        return settings_store.get_notebooks()


# ── FastAPI App ───────────────────────────────────────────

app = FastAPI(title="Impuestia Admin")


@app.on_event("startup")
async def start_official_monitor() -> None:
    config.require_production_secrets()
    from official_scheduler import run_daily_monitor
    from research_runs import research_runs
    app.state.official_monitor = asyncio.create_task(run_daily_monitor())
    await research_runs.recover()


@app.on_event("shutdown")
async def stop_official_monitor() -> None:
    task = getattr(app.state, "official_monitor", None)
    if task:
        task.cancel()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled error: %s", exc)
    if request.url.path.startswith("/api/"):
        return JSONResponse(
            status_code=500,
            content={"detail": f"Internal error: {str(exc)[:200]}"},
        )
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": f"Error interno: {str(exc)[:200]}"},
        status_code=500,
    )
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=3600 * 24 * 7,  # 7 días
)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")


# ── Auth helpers ──────────────────────────────────────────

def _is_authenticated(request: Request) -> bool:
    return request.session.get("authenticated") is True


# ── Rutas públicas ────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    if _is_authenticated(request):
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "login.html", {"error": error})


@app.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
    if ADMIN_USERNAME and ADMIN_PASSWORD and SESSION_SECRET and username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
        request.session["authenticated"] = True
        request.session["username"] = username
        return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    return RedirectResponse(
        url="/?error=Credenciales+incorrectas", status_code=status.HTTP_302_FOUND
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "impuestia"}


# ── Rutas protegidas ──────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Bot siempre "online" mientras este servidor corre (son el mismo proceso).
    bot_online = True

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "bot_online": bot_online,
        "vault_path": str(config.OBSIDIAN_VAULT_PATH),
        "message": message,
        "error": error,
    })


@app.post("/api/notebook/select")
async def notebook_select(
    request: Request,
    notebook_id: str = Form(...),
    notebook_name: str = Form(...),
    role: str = Form(...),
):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    if role == "primary":
        settings_store.set("primary_notebook_id", notebook_id)
        settings_store.set("primary_notebook_name", notebook_name)
    elif role == "secondary":
        settings_store.set("secondary_notebook_id", notebook_id)
        settings_store.set("secondary_notebook_name", notebook_name)

    return RedirectResponse(
        url=f"/dashboard?message=Cuaderno+{role}+actualizado+a:+{notebook_name}",
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/api/auth/upload")
async def auth_upload(request: Request, auth_file: UploadFile):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    try:
        content = await auth_file.read()
        # Validar que sea JSON válido
        data = json.loads(content)
        if not isinstance(data, dict):
            raise ValueError("El archivo no contiene un objeto JSON válido")

        # Guardar como notebooklm_auth.json
        auth_path = config.BASE_DIR / "notebooklm_auth.json"
        auth_path.write_bytes(content)

        # Sincronizar a la ruta que notebooklm-py espera
        try:
            import os
            target_dir = Path("/root/.notebooklm/profiles/default")
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / "storage_state.json"
            target_path.write_bytes(content)
        except Exception as sync_err:
            logger.warning("No se pudo sincronizar a ~/.notebooklm: %s", sync_err)

        return RedirectResponse(
            url="/dashboard?message=Credenciales+actualizadas+y+aplicadas+correctamente.",
            status_code=status.HTTP_302_FOUND,
        )
    except json.JSONDecodeError:
        return RedirectResponse(
            url="/dashboard?error=El+archivo+no+es+un+JSON+válido",
            status_code=status.HTTP_302_FOUND,
        )
    except Exception as e:
        return RedirectResponse(
            url=f"/dashboard?error=Error+al+subir:+{str(e)[:100]}",
            status_code=status.HTTP_302_FOUND,
        )


# ── Revisión de borradores de árboles de decisión (Fase 4) ─
#
# El LLM propone (decision_tree_drafter.py escribe a decision_trees/_drafts/),
# un humano aprueba acá. "Aprobar" solo mueve el archivo a
# decision_trees/codigo_tributario/ si el JSON es válido — nunca se
# publica un borrador sin pasar por esta pantalla.

def _list_drafts() -> list[dict]:
    if not DRAFTS_DIR.exists():
        return []
    result = []
    for path in sorted(DRAFTS_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            result.append({
                "tree_id": data.get("tree_id", path.stem),
                "title": data.get("title", "(sin título)"),
                "article": data.get("article", ""),
                "node_count": len(data.get("nodes", {})) + 1,
                "filename": path.name,
            })
        except Exception as e:
            result.append({"tree_id": path.stem, "title": f"[JSON inválido: {e}]", "article": "", "node_count": 0, "filename": path.name})
    return result


@app.get("/review/drafts", response_class=HTMLResponse)
async def review_drafts(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "review_drafts.html", {
        "drafts": _list_drafts(),
        "message": message,
        "error": error,
    })


@app.get("/review/drafts/{tree_id}", response_class=HTMLResponse)
async def review_draft_detail(request: Request, tree_id: str, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    path = DRAFTS_DIR / f"{tree_id}.json"
    if not path.exists():
        return RedirectResponse(url="/review/drafts?error=Borrador+no+encontrado", status_code=status.HTTP_302_FOUND)

    raw = path.read_text(encoding="utf-8")
    mermaid = ""
    try:
        mermaid = to_mermaid(json.loads(raw))
    except Exception as e:
        error = error or f"No se pudo generar el diagrama: {e}"

    return templates.TemplateResponse(request, "review_detail.html", {
        "tree_id": tree_id,
        "raw_json": raw,
        "mermaid": mermaid,
        "error": error,
    })


@app.post("/review/drafts/{tree_id}/save")
async def review_draft_save(request: Request, tree_id: str, raw_json: str = Form(...)):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    path = DRAFTS_DIR / f"{tree_id}.json"
    try:
        parsed = json.loads(raw_json)  # valida que sea JSON bien formado antes de guardar
        path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    except json.JSONDecodeError as e:
        return RedirectResponse(url=f"/review/drafts/{tree_id}?error=JSON+inválido:+{str(e)[:100]}", status_code=status.HTTP_302_FOUND)

    return RedirectResponse(url=f"/review/drafts/{tree_id}?message=Guardado", status_code=status.HTTP_302_FOUND)


@app.post("/review/drafts/{tree_id}/approve")
async def review_draft_approve(request: Request, tree_id: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    draft_path = DRAFTS_DIR / f"{tree_id}.json"
    if not draft_path.exists():
        return RedirectResponse(url="/review/drafts?error=Borrador+no+encontrado", status_code=status.HTTP_302_FOUND)

    try:
        from decision_engine import DecisionEngine
        data = json.loads(draft_path.read_text(encoding="utf-8"))
        # Reusa el parser real de decision_engine.py para validar que el
        # árbol cargue correctamente antes de publicarlo (misma lógica
        # que usa el bot en producción, no una validación aparte).
        # _parse_tree() no toca self, así que __new__ evita cargar todo
        # decision_trees/ solo para validar un archivo.
        DecisionEngine._parse_tree(DecisionEngine.__new__(DecisionEngine), draft_path)
    except Exception as e:
        return RedirectResponse(url=f"/review/drafts/{tree_id}?error=Árbol+inválido,+no+se+puede+aprobar:+{str(e)[:150]}", status_code=status.HTTP_302_FOUND)

    TREES_DIR.mkdir(parents=True, exist_ok=True)
    final_path = TREES_DIR / draft_path.name
    final_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    draft_path.unlink()

    return RedirectResponse(
        url=f"/review/drafts?message=Aprobado+y+publicado+en+{final_path.name}+(reinicia+el+bot+para+que+lo+tome)",
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/review/drafts/{tree_id}/discard")
async def review_draft_discard(request: Request, tree_id: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    path = DRAFTS_DIR / f"{tree_id}.json"
    if path.exists():
        path.unlink()
    return RedirectResponse(url="/review/drafts?message=Borrador+descartado", status_code=status.HTTP_302_FOUND)


# ── Revisión de Notas Aprobadas (Fase 7) ─────────────────────────────
#
# Concepto: el usuario valida contenido (jurisprudencia, peticiones,
# estudios, criterios propios) y lo marca como NOTA APROBADA. A partir de
# ahí, el bot lo consulta con prioridad ANTES de recurrir a la búsqueda web.
# "Aprobar" solo edita el frontmatter de la nota en el vault (aprobada: true).
# El borrado/aprobación nunca toca el contenido del cuerpo.

def _vault_rel(doc_path: str) -> Optional[str]:
    """Ruta relativa al vault, o None si el doc esta fuera (no es aprobable)."""
    try:
        rel = Path(doc_path).resolve().relative_to(VAULT.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _list_pending_aprobadas() -> list[dict]:
    items = []
    for doc in article_index.pending_approval(max_docs=300):
        rel = _vault_rel(doc.path)
        if rel is None:
            continue  # fuera del vault (p.ej. documents/): no es aprobable
        refs_txt = ", ".join(f"{t}:{a}" for t, a in doc.refs[:4])
        items.append({
            "path": doc.path,
            "rel_path": rel,
            "title": doc.title,
            "tipo": doc.tipo or "nota",
            "cliente": doc.cliente or "",
            "refs": refs_txt,
            "mtime": datetime.fromtimestamp(doc.mtime).strftime("%d/%m/%Y %H:%M"),
            "size": Path(doc.path).stat().st_size if Path(doc.path).exists() else 0,
        })
    return items


@app.get("/review/notas", response_class=HTMLResponse)
async def review_notas(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    article_index.rebuild()
    return templates.TemplateResponse(request, "review_notas.html", {
        "pendientes": _list_pending_aprobadas(),
        "aprobadas": 0,
        "message": message,
        "error": error,
    })


@app.get("/review/notas/{rel_path:path}", response_class=HTMLResponse)
async def review_nota_detail(request: Request, rel_path: str, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    path = (VAULT / rel_path).resolve()
    if not path.exists() or str(VAULT.resolve()) not in str(path):
        return RedirectResponse(url="/review/notas?error=Nota+no+encontrada", status_code=status.HTTP_302_FOUND)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return RedirectResponse(url=f"/review/notas?error=No+se+pudo+leer:+{str(e)[:80]}", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "review_nota_detail.html", {
        "rel_path": rel_path,
        "path": str(path),
        "content": content,
        "error": error,
    })


@app.post("/review/notas/{rel_path:path}/approve")
async def review_nota_approve(request: Request, rel_path: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    path = (VAULT / rel_path).resolve()
    if not path.exists():
        return RedirectResponse(url="/review/notas?error=Nota+no+encontrada", status_code=status.HTTP_302_FOUND)
    ok = article_index.set_aprobada(str(path), True)
    return RedirectResponse(
        url=f"/review/notas?message={'Nota+aprobada' if ok else 'No+se+pudo+marcar+la+nota'}",
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/review/notas/{rel_path:path}/unapprove")
async def review_nota_unapprove(request: Request, rel_path: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    path = (VAULT / rel_path).resolve()
    if not path.exists():
        return RedirectResponse(url="/review/notas?error=Nota+no+encontrada", status_code=status.HTTP_302_FOUND)
    ok = article_index.set_aprobada(str(path), False)
    return RedirectResponse(
        url=f"/review/notas?message={'Nota+desaprobada' if ok else 'No+se+pudo+desmarcar'}",
        status_code=status.HTTP_302_FOUND,
    )


# ── Subida de material para revisar (Fase 7b) ──────────────────────────
#
# Acepta .txt/.md/.docx/.pdf (con texto o escaneado) e imagenes (png/jpg/...).
# Los PDF escaneados y las imagenes se procesan con OCR de vision (GPT-4o,
# ver ocr_processor.py). El resultado se guarda en el vault como nota pendiente
# de aprobacion (carpeta Entrada/), listo para revisar y aprobar aqui mismo.

_UPLOAD_TYPES = {"jurisprudencia", "peticion", "analisis", "estudio", "nota"}
_UPLOAD_EXTS = {"txt", "md", "docx", "pdf", "png", "jpg", "jpeg", "webp", "tiff", "tif", "bmp"}


async def _process_upload(
    filename: str,
    data: bytes,
    *,
    titulo: str = "",
    tipo: str = "nota",
    cliente: str = "",
) -> dict:
    """Procesa un archivo subido y lo guarda como nota pendiente en Entrada/.

    Returns:
        {"ok": True, "path": ..., "ocr": bool} o {"ok": False, "error": str}
    """
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in _UPLOAD_EXTS:
        return {"ok": False, "error": f"Formato no soportado: .{ext}"}
    if tipo not in _UPLOAD_TYPES:
        tipo = "nota"
    if not data:
        return {"ok": False, "error": "Archivo vacío"}

    import tempfile
    tmp = Path(tempfile.mkdtemp()) / f"upload.{ext}"
    tmp.write_bytes(data)
    try:
        from ocr_processor import extract_markdown
        markdown, meta = await extract_markdown(tmp)
    except Exception as e:
        logger.exception("Fallo procesamiento de material")
        return {"ok": False, "error": f"No se pudo procesar: {str(e)[:140]}"}
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmp.parent.rmdir()
        except OSError:
            pass

    if not markdown.strip():
        return {"ok": False, "error": "No se extrajo texto del archivo (¿escaneo muy pobre?)"}

    body = (
        f"## Material original\n\n"
        f"- **Archivo:** `{filename}` ({meta['tipo_src']}, "
        f"{len(data) // 1024} KB)\n"
        f"- **Procesado:** {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"- **Paginas:** {meta['pages']} | **OCR:** {'si' if meta['ocr'] else 'no (texto nativo)'}\n\n"
        f"## Contenido extraido\n\n{markdown}"
    )
    safe_title = titulo or Path(filename).stem
    path = write_note(
        folder="Entrada",
        filename=safe_title[:80],
        content=body,
        title=safe_title,
        tipo=tipo,
        cliente=cliente or None,
        tags=["material"],
        fuentes=[filename],
    )
    article_index.rebuild()
    return {"ok": True, "path": str(path), "ocr": bool(meta.get("ocr", False))}


@app.post("/review/notas/upload")
async def review_nota_upload(
    request: Request,
    material: UploadFile,
    titulo: str = Form(""),
    tipo: str = Form("nota"),
    cliente: str = Form(""),
):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    filename = material.filename or "documento"
    data = await material.read()
    result = await _process_upload(
        filename, data, titulo=titulo, tipo=tipo, cliente=cliente
    )
    if result["ok"]:
        return RedirectResponse(
            url="/review/notas?message=Material+subido:+rev%C3%ADsalo+y+aprueba",
            status_code=status.HTTP_302_FOUND,
        )
    return RedirectResponse(
        url=f"/review/notas?error={result['error']}",
        status_code=status.HTTP_302_FOUND,
    )


@app.post("/api/notas/upload")
async def api_notas_upload(
    request: Request,
    material: UploadFile,
    titulo: str = Form(""),
    tipo: str = Form("nota"),
    cliente: str = Form(""),
):
    """Endpoint JSON para el drag & drop: sube un archivo y responde resultado."""
    if not _is_authenticated(request):
        return JSONResponse({"ok": False, "error": "No autenticado"}, status_code=401)

    filename = material.filename or "documento"
    data = await material.read()
    result = await _process_upload(
        filename, data, titulo=titulo, tipo=tipo, cliente=cliente
    )
    if result["ok"]:
        result["filename"] = filename
        return JSONResponse(result)
    return JSONResponse(result, status_code=400)


# ── Gestión de Archivos (Fase 2) ─────────────────────────────

@app.get("/files", response_class=HTMLResponse)
async def files_home(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    clientes = list_clientes()
    return templates.TemplateResponse(request, "files.html", {
        "clientes": clientes,
        "current_cliente": None,
        "current_folder": "",
        "files": [],
        "subfolders": [],
    })


@app.get("/files/{cliente}/{subfolder:path}", response_class=HTMLResponse)
async def files_cliente(request: Request, cliente: str, subfolder: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    cli_dir = get_cliente_dir(cliente)
    if not cli_dir.exists():
        return RedirectResponse(url="/files?error=Cliente+no+encontrado", status_code=status.HTTP_302_FOUND)

    target = cli_dir / subfolder if subfolder else cli_dir
    subfolders = sorted([d.name for d in target.iterdir() if d.is_dir() and not d.name.startswith(".")])
    files = sorted([p for p in target.iterdir() if p.is_file() and not p.name.startswith(".")])
    file_info = [{"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%d/%m/%Y %H:%M")} for f in files]

    breadcrumb = [{"label": "Clientes", "url": "/files"}]
    path_parts = [cliente]
    if subfolder:
        path_parts.extend(subfolder.replace("\\", "/").split("/"))
    for i, part in enumerate(path_parts):
        url_path = "/files/" + "/".join(path_parts[:i+1])
        breadcrumb.append({"label": part, "url": url_path})

    return templates.TemplateResponse(request, "files.html", {
        "clientes": list_clientes(),
        "current_cliente": cliente,
        "current_folder": subfolder,
        "breadcrumb": breadcrumb,
        "files": file_info,
        "subfolders": subfolders,
    })


@app.get("/works", response_class=HTMLResponse)
async def works(request: Request, cliente: str = ""):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    clientes = list_clientes()
    selected = None
    if cliente:
        for c in clientes:
            if c.get("nombre", "") == cliente:
                selected = c
                break
    return templates.TemplateResponse(request, "works.html", {
        "clientes": clientes,
        "selected_cliente": selected,
    })


@app.post("/api/cliente/create")
async def api_create_cliente(
    request: Request,
    nombre: str = Form(...),
    rut: str = Form(""),
    rubro: str = Form(""),
    regimen: str = Form(""),
    contacto: str = Form(""),
    notas: str = Form(""),
):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        init_cliente_structure(nombre, rut, rubro, regimen, contacto, notas)
        return JSONResponse({"ok": True, "nombre": nombre})
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


@app.get("/api/cliente/{cliente}/files")
async def api_cliente_files(request: Request, cliente: str, subfolder: str = ""):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    files = list_cliente_files(cliente, subfolder)
    return JSONResponse({
        "cliente": cliente,
        "folder": subfolder,
        "files": [{"name": f.name, "size": f.stat().st_size, "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat()} for f in files],
    })


@app.get("/api/vault/status")
async def api_vault_status(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    clientes_count = len(list_clientes())
    return JSONResponse({
        "vault_path": str(VAULT),
        "vault_exists": VAULT.exists(),
        "clientes": clientes_count,
    })


# ── Sincronización vault prod → Obsidian local ─────────────────
#
# El vault de producción vive en el volumen de Railway y el Obsidian
# local en Dropbox. Estos endpoints exponen el vault para que un script
# local (sync_vault.py) lo baje de forma idempotente:
#   GET /api/vault/manifest  -> lista de archivos + sha256 (sync incremental)
#   GET /api/vault/export    -> zip del vault + _MANIFEST.json
# Se excluyen .obsidian/ y .trash/ (config y papeleras son por-máquina).

def _vault_entries(root: Path) -> list[Path]:
    """Lista archivos del vault excluyendo carpetas por-máquina."""
    if not root.exists():
        return []
    excluded_dirs = {".obsidian", ".trash", ".git"}
    entries: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        parts = rel.parts
        if any(part in excluded_dirs for part in parts):
            continue
        if p.name in (".DS_Store", "Thumbs.db"):
            continue
        entries.append(p)
    return entries


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _build_vault_manifest(root: Path) -> list[dict]:
    manifest = []
    for p in _vault_entries(root):
        rel = p.relative_to(root).as_posix()
        st = p.stat()
        manifest.append({
            "path": rel,
            "size": st.st_size,
            "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "sha256": _file_sha256(p),
        })
    return manifest


@app.get("/api/vault/manifest")
async def api_vault_manifest(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    return JSONResponse({
        "vault_path": str(VAULT),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "files": _build_vault_manifest(VAULT),
    })


@app.get("/api/vault/export")
async def api_vault_export(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)

    manifest = _build_vault_manifest(VAULT)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("_MANIFEST.json", json.dumps(
            {"vault_path": str(VAULT), "generated_at": datetime.now().isoformat(timespec="seconds"), "files": manifest},
            ensure_ascii=False, indent=2,
        ))
        for entry in manifest:
            zf.write(VAULT / entry["path"], arcname=entry["path"])

    data = buf.getvalue()
    filename = f"vault-export-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Gestión de Skills (Fase 3) ────────────────────────────────

@app.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    skills = list_skills()
    return templates.TemplateResponse(request, "skills.html", {
        "skills": skills,
        "message": message,
        "error": error,
    })


@app.get("/skills/{name}", response_class=HTMLResponse)
async def skill_detail(request: Request, name: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    skill = load_skill(name)
    if not skill:
        return RedirectResponse(url="/skills?error=Skill+no+encontrado", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(request, "skill_detail.html", {
        "skill": skill,
    })


@app.post("/api/skills/create")
async def api_create_skill(
    request: Request,
    name: str = Form(...),
    description: str = Form(...),
    template_file: Optional[UploadFile] = None,
):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        if template_file:
            import tempfile
            content = await template_file.read()
            with tempfile.NamedTemporaryFile(suffix=".docx" if template_file.filename and template_file.filename.endswith(".docx") else ".txt", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                create_skill_from_template(name, description, tmp_path)
            finally:
                Path(tmp_path).unlink(missing_ok=True)
        else:
            from skill_manager import create_skill
            create_skill(name, description, triggers=[name.replace("-", " ")])
        return JSONResponse({"ok": True, "name": name})
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


@app.post("/api/skills/{name}/delete")
async def api_delete_skill(request: Request, name: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    ok = delete_skill(name)
    return JSONResponse({"ok": ok})


# ── Co-Work por Cliente (Fase 4) ───────────────────────────

@app.post("/api/cliente/{cliente}/entrada/upload")
async def api_cowork_upload(request: Request, cliente: str, material: UploadFile):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    client_dir = get_cliente_dir(cliente)
    if not client_dir.is_dir():
        return JSONResponse({"error": "Cliente no encontrado"}, status_code=404)
    # El navegador envía rutas relativas: nunca se aceptan rutas del servidor.
    parts = (material.filename or "").replace("\\", "/").split("/")
    if any(not p or p in {".", ".."} or any(c in p for c in ':<>"|?*') for p in parts):
        return JSONResponse({"error": "Nombre de documento inválido"}, status_code=400)
    name = "__".join(parts)
    _cowork_exts = {".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp"}

    if Path(name).suffix.lower() not in _cowork_exts:
        return JSONResponse({"error": "Formato no admitido. Usa PDF, DOCX, TXT, MD o una imagen (PNG/JPG/...)."}, status_code=400)
    entrada = client_dir / "entrada"
    entrada.mkdir(exist_ok=True)
    target = entrada / name
    created = False
    try:
        with target.open("xb") as output:
            created = True
            size = 0
            while chunk := await material.read(1024 * 1024):
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise ValueError("El documento supera el límite de 25 MB.")
                output.write(chunk)
        return {"ok": True, "filename": name}
    except FileExistsError:
        return JSONResponse({"error": "Ya existe un documento con ese nombre; no se sobrescribió."}, status_code=409)
    except Exception as exc:
        if created:
            target.unlink(missing_ok=True)
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)
    finally:
        await material.close()

@app.get("/works/{cliente}", response_class=HTMLResponse)
async def works_cliente(request: Request, cliente: str):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    from cowork_manager import get_cliente_estado, escanear_entrada
    estado = get_cliente_estado(cliente)
    archivos = [{"name": p.name, "size": p.stat().st_size} for p in escanear_entrada(cliente)]
    return templates.TemplateResponse(request, "works_cliente.html", {
        "cliente": cliente,
        "estado": estado,
        "archivos": archivos,
    })


@app.post("/api/cliente/{cliente}/process")
async def api_process_cliente(request: Request, cliente: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from cowork_manager import procesar_entrada_cliente
        # El panel usa el mismo cliente y flujo asincrono que Telegram.
        from writer import WriterEngine
        trabajos = await procesar_entrada_cliente(cliente, llm_client=WriterEngine()._llm)
        errors = [t.error_msg for t in trabajos if t.estado == "error"]
        return JSONResponse({
            "ok": not errors,
            "error": "; ".join(errors) if errors else None,
            "trabajos_creados": len(trabajos),
            "resultados": [{"tipo": t.tipo, "estado": t.estado, "titulo": t.titulo} for t in trabajos],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


# ── Expedientes y evidencia (produccion) ──────────────────────

@app.get("/api/cliente/{cliente}/expedientes")
async def api_list_cases(request: Request, cliente: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from production_store import store
    return {"cases": store.list_cases(get_cliente_dir(cliente).name)}


@app.post("/api/cliente/{cliente}/expedientes")
async def api_create_case(request: Request, cliente: str, titulo: str = Form(...)):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from cowork_manager import init_expediente_structure
    try:
        case = init_expediente_structure(cliente, titulo)
        return {"ok": True, "case_id": case["id"], "root": str(case["root"])}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.post("/api/cliente/{cliente}/expedientes/{case_id}/upload")
async def api_case_upload(request: Request, cliente: str, case_id: str, material: UploadFile):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from cowork_manager import get_expediente_dir
    try:
        base = get_expediente_dir(cliente, case_id)
        name = Path(material.filename or "documento").name
        target = base / "Entrada" / name
        if target.exists():
            return JSONResponse({"error": "Ya existe un original con ese nombre"}, status_code=409)
        target.write_bytes(await material.read())
        return {"ok": True, "filename": name}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.post("/api/cliente/{cliente}/expedientes/{case_id}/process/{filename}")
async def api_case_process(request: Request, cliente: str, case_id: str, filename: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from cowork_manager import get_expediente_dir, process_case_document
    from writer import WriterEngine
    try:
        original = get_expediente_dir(cliente, case_id) / "Entrada" / Path(filename).name
        if not original.is_file(): raise ValueError("Documento no encontrado")
        result = await process_case_document(cliente, case_id, original, llm_client=WriterEngine()._llm)
        return {"ok": True, **result}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


# ── MCP (Model Context Protocol) — harness externo ──────────────────
# Monta el endpoint MCP sobre el panel para que agentes externos (p.ej.
# DeepSeek Harness / @deepseek-ai/dsh-mcp-client) usen las tools del backend.
# Si la librería `mcp` no está instalada, el panel sigue funcionando sin MCP.
def _mount_mcp() -> None:
    try:
        from mcp_server import build_mcp_app
        path, sub_app = build_mcp_app()
        app.mount(path, sub_app, name="mcp")
        logger.info("MCP montado en %s (SSE en %s/sse)", path, path)
    except Exception as exc:
        logger.warning("MCP no montado (el panel sigue operando): %s", exc)


_mount_mcp()



@app.post("/api/cliente/{cliente}/expedientes/{case_id}/approve/{filename}")
async def api_case_approve(request: Request, cliente: str, case_id: str, filename: str, publish: bool = Form(False)):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from cowork_manager import get_expediente_dir
    from production_store import store
    try:
        base = get_expediente_dir(cliente, case_id)
        draft = (base / "Borradores" / Path(filename).name).resolve()
        if not draft.is_file() or draft.parent != (base / "Borradores").resolve(): raise ValueError("Borrador no encontrado")
        destination = base / ("Entregables" if publish else "Revisados") / draft.name
        shutil.copy2(draft, destination)
        store.audit("case_document_approved", case_id, draft=str(draft), destination=str(destination), published=publish)
        return {"ok": True, "path": str(destination), "status": "entregable" if publish else "revisado"}
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.get("/api/sources/changes")
async def api_source_changes(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from production_store import store
    return {"changes": store.latest_changes()}


@app.post("/api/sources/sync")
async def api_source_sync(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from official_sources import sync_official_sources
    result = await sync_official_sources()
    return {"ok": not result["errors"], **result}


@app.get("/api/sources/coverage")
async def api_source_coverage(request: Request):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from production_store import store
    return {"coverage": store.library_coverage()}


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    from production_store import store
    return templates.TemplateResponse(request, "sources.html", {"request": request, "changes": store.latest_changes()})


# ── Research Agent (Fase 5) ───────────────────────────────────

@app.get("/research", response_class=HTMLResponse)
async def research_page(request: Request):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    clientes = list_clientes()
    return templates.TemplateResponse(request, "research.html", {
        "clientes": clientes,
    })


@app.post("/api/research")
async def api_research(
    request: Request,
    query: str = Form(...),
    cliente: str = Form(""),
    fecha_hechos: str = Form(""),
):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from research_agent import run_research
        result = await run_research(
            query=query,
            cliente=cliente if cliente else None,
            fecha_hechos=fecha_hechos or None,
            include_local_laws=True,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


@app.post("/api/research/runs")
async def api_start_research_run(
    request: Request,
    query: str = Form(...),
    cliente: str = Form(""),
    case_id: str = Form(""),
    fecha_hechos: str = Form(""),
    budget_usd: Optional[float] = Form(None),
):
    """Inicia el flujo persistente; el cliente consulta luego su estado."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from research_runs import research_runs
        run = await research_runs.start(query=query, client_id=cliente or None, case_id=case_id or None,
                                        facts_date=fecha_hechos or None, budget_usd=budget_usd)
        return JSONResponse(run, status_code=202)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.get("/api/research/runs/{run_id}")
async def api_research_run(request: Request, run_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from production_store import store
    run = store.research_run(run_id)
    if not run:
        return JSONResponse({"error": "Investigación no encontrada"}, status_code=404)
    if run.get("status") == "queued":
        try:
            from research_runs import research_runs
            await research_runs.resume(run_id)
        except Exception:
            pass
    return run

    return run


@app.post("/api/research/runs/{run_id}/resume")
async def api_resume_research_run(request: Request, run_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from research_runs import research_runs
        return await research_runs.resume(run_id)
    except KeyError:
        return JSONResponse({"error": "Investigación no encontrada"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.post("/api/research/runs/{run_id}/clarify")
async def api_clarify_research_run(request: Request, run_id: str, message: str = Form(...)):
    """Vincula un antecedente nuevo al expediente y reinicia su análisis."""
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from research_runs import research_runs
        return await research_runs.clarify(run_id, message)
    except KeyError:
        return JSONResponse({"error": "Investigación no encontrada"}, status_code=404)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:200]}, status_code=400)


@app.post("/api/research/runs/{run_id}/cancel")
async def api_cancel_research_run(request: Request, run_id: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    from research_runs import research_runs
    research_runs.cancel(run_id)
    return {"ok": True}


# ── Ebook Writer (Fase 6) ─────────────────────────────────────

@app.post("/api/ebook")
async def api_ebook(request: Request, tema: str = Form(...), audiencia: str = Form("contadores, abogados y empresarios")):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from ebook_writer import write_ebook
        result = await write_ebook(tema=tema, audiencia=audiencia)
        return JSONResponse({"titulo": result["titulo"], "capitulos": result["capitulos"], "archivos": result["archivos"]})
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


@app.get("/api/skills/{name}/templates")
async def api_skill_templates(request: Request, name: str):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    templates = get_skill_templates(name)
    return JSONResponse({"templates": {k: v[:500] for k, v in templates.items()}})


# ── Uvicorn runner ────────────────────────────────────────

def run_web_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
