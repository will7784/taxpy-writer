"""
Servidor web FastAPI para panel de administración de Impuestia.

Incluye:
- Login simple con sesiones
- Dashboard con estado del bot y notebooks
- Gestión de cuadernos primario/secundario
- Upload de storage_state.json
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from decision_tree_drafter import DRAFTS_DIR, to_mermaid
from obsidian_writer import list_clientes, get_cliente_dir, list_cliente_files, init_cliente_structure, VAULT
from settings_store import store as settings_store
from skill_manager import list_skills, load_skill, delete_skill, create_skill_from_template, get_skill_templates
from supabase_client import supabase

# Supabase free tier pausa el proyecto tras ~7 dias sin actividad -- eso
# dejo al RAG respondiendo "no encontro informacion" en produccion sin
# ningun aviso. Este ping evita llegar a ese limite mientras el bot este
# corriendo (Railway lo mantiene arriba 24/7). Bastante margen bajo 7 dias.
SUPABASE_KEEPALIVE_INTERVAL_SECONDS = 24 * 60 * 60  # 24 horas

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

async def _supabase_keepalive_loop() -> None:
    """Pinguea Supabase periodicamente para que el plan free no lo pause por inactividad."""
    while True:
        try:
            await asyncio.to_thread(
                lambda: supabase.table("document_chunks").select("chunk_uid").limit(1).execute()
            )
            logger.info("Supabase keepalive: OK")
        except Exception as e:
            logger.warning("Supabase keepalive fallo (se reintenta en el proximo ciclo): %s", e)
        await asyncio.sleep(SUPABASE_KEEPALIVE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Impuestia Admin starting up...")
    keepalive_task = asyncio.create_task(_supabase_keepalive_loop())
    yield
    keepalive_task.cancel()
    logger.info("Impuestia Admin shutting down...")


app = FastAPI(title="Impuestia Admin", lifespan=lifespan)


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
    if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
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
    # NotebookLM ya no se consulta acá -- es funcionalidad deprecada
    # (config.py: "se eliminará"), el consultor usa RAG/Supabase directo.
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


@app.get("/files/{cliente}", response_class=HTMLResponse)
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
        trabajos = procesar_entrada_cliente(cliente)
        return JSONResponse({
            "ok": True,
            "trabajos_creados": len(trabajos),
            "resultados": [{"tipo": t.tipo, "estado": t.estado, "titulo": t.titulo} for t in trabajos],
        })
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


# ── Research Agent (Fase 5) ───────────────────────────────────

@app.post("/api/research")
async def api_research(
    request: Request,
    query: str = Form(...),
    cliente: str = Form(""),
    max_results: int = Form(3),
):
    if not _is_authenticated(request):
        return JSONResponse({"error": "No autenticado"}, status_code=401)
    try:
        from research_agent import run_research
        result = await run_research(
            query=query,
            cliente=cliente if cliente else None,
            max_results=max_results,
        )
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)[:200]}, status_code=400)


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
