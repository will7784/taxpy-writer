"""
Servidor web FastAPI para panel de administración de Taxpy Writer.

Incluye:
- Login simple con sesiones
- Dashboard con estado del bot y notebooks
- Gestión de cuadernos primario/secundario
- Upload de storage_state.json
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Form, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

import config
from settings_store import store as settings_store

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
        return result
    except Exception:
        return settings_store.get_notebooks()


# ── FastAPI App ───────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Taxpy Writer Admin starting up...")
    yield
    logger.info("Taxpy Writer Admin shutting down...")


app = FastAPI(title="Taxpy Writer Admin", lifespan=lifespan)


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
    return {"status": "ok", "service": "taxpy-writer"}


# ── Rutas protegidas ──────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    if not _is_authenticated(request):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # Estado
    primary_name = settings_store.get("primary_notebook_name", config.NOTEBOOKLM_NOTEBOOK_NAME)
    secondary_name = settings_store.get("secondary_notebook_name", "")
    primary_id = settings_store.get("primary_notebook_id", "")
    secondary_id = settings_store.get("secondary_notebook_id", "")

    # Intentar listar notebooks
    notebooks = await _list_notebooks_from_api()

    # Verificar conexión NotebookLM
    notebooklm_ok = bool(notebooks)

    # Bot siempre "online" mientras este servidor corre (son el mismo proceso)
    bot_online = True

    return templates.TemplateResponse(request, "dashboard.html", {
        "request": request,
        "bot_online": bot_online,
        "notebooklm_ok": notebooklm_ok,
        "primary_name": primary_name,
        "secondary_name": secondary_name,
        "primary_id": primary_id,
        "secondary_id": secondary_id,
        "notebooks": notebooks,
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

        return RedirectResponse(
            url="/dashboard?message=Credenciales+actualizadas+correctamente.+Reinicia+el+servicio+para+aplicar.",
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


# ── Uvicorn runner ────────────────────────────────────────

def run_web_server(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
