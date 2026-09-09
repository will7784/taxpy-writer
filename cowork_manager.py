"""
Co-Work Manager — ImpuestIA (Fase 4)

Gestiona trabajos por cliente con carpetas vigiladas (entrada/ → procesados/ → output).
Integrado con Obsidian vault para persistencia y skill_manager para redaccion asistida.
"""

from __future__ import annotations

import hashlib
import asyncio
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

import config
from production_store import store
from obsidian_writer import (
    get_cliente_dir,
    init_cliente_structure,
    list_clientes,
    write_analisis,
    write_note,
    write_peticion,
)
from skill_manager import load_skills_context

COWORK = config.COWORK_PATH


def init_expediente_structure(cliente: str, titulo: str) -> dict[str, Path | str]:
    """Crea un espacio aislado por expediente dentro del cliente."""
    client_dir = get_cliente_dir(cliente)
    if not client_dir.exists():
        init_cliente_structure(cliente)
    case = store.create_case(client_dir.name, titulo)
    base = client_dir / "Expedientes" / case["id"]
    dirs: dict[str, Path | str] = {"id": case["id"], "root": _ensure_case_dir(base)}
    for name in ("Entrada", "Extraccion", "Evidencia", "Borradores", "Revisados", "Entregables"):
        dirs[name.lower()] = _ensure_case_dir(base / name)
    (base / "manifest.yaml").write_text(yaml.dump({"case": case, "version": 1}, allow_unicode=True), encoding="utf-8")
    return dirs


def _ensure_case_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_expediente_dir(cliente: str, expediente_id: str) -> Path:
    client_dir = get_cliente_dir(cliente)
    if not store.case_for(expediente_id, client_dir.name):
        raise ValueError("Expediente no encontrado para este cliente")
    return client_dir / "Expedientes" / expediente_id


async def process_case_document(cliente: str, expediente_id: str, archivo: Path, *, llm_client) -> dict:
    """Extrae y genera un borrador; el original no se mueve ni se modifica."""
    from ocr_processor import extract_markdown
    base = get_expediente_dir(cliente, expediente_id)
    sha = hashlib.sha256(archivo.read_bytes()).hexdigest()
    doc = store.add_case_document(expediente_id, str(archivo), sha, status="extrayendo")
    job = store.create_job(expediente_id, sha, doc["id"], getattr(llm_client, "model", ""))
    try:
        text, meta = await extract_markdown(archivo)
        if not text.strip():
            raise ValueError("No se pudo extraer texto del documento")
        extracted = base / "Extraccion" / f"{archivo.stem}.md"
        extracted.write_text(text, encoding="utf-8")
        store.complete_case_document(doc["id"], extracted_path=str(extracted), pages=meta.get("pages"), ocr=bool(meta.get("ocr")))
        case_query = f"Analiza el documento y prepara un borrador verificable. Hechos extraídos: {text[:1500]}"
        result = await redactar_para_cliente(cliente, "analisis", case_query, contenido_extra=text, llm_client=llm_client)
        if not result or result.startswith("[ERROR"):
            raise ValueError(result or "El modelo no genero un borrador")
        draft = base / "Borradores" / f"{archivo.stem}_borrador.md"
        draft.write_text(result, encoding="utf-8")
        evidence = [{"document_id": doc["id"], "sha256": sha, "pages": meta.get("pages"), "ocr": meta.get("ocr", False)}]
        try:
            from official_sources import official_evidence
            _text, legal_docs = official_evidence(text[:5000])
            evidence.extend({"official_url": d["url"], "legal_status": d["legal_status"], "source_hash": d["content_hash"]} for d in legal_docs)
        except Exception:
            pass
        store.finish_job(job["id"], status="review", output_path=str(draft), evidence=evidence)
        return {"job": job["id"], "status": "review", "draft": str(draft), "evidence": evidence}
    except Exception as exc:
        store.finish_job(job["id"], status="failed", error=str(exc))
        raise


@dataclass
class Trabajo:
    id: str
    cliente: str
    tipo: str  # "peticion", "analisis", "investigacion", "redaccion"
    titulo: str
    estado: str = "pendiente"  # pendiente, en_proceso, completado, error
    contenido: str = ""
    archivo_entrada: str = ""
    archivo_salida: str = ""
    creado: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    completado: str = ""
    error_msg: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cliente": self.cliente,
            "tipo": self.tipo,
            "titulo": self.titulo,
            "estado": self.estado,
            "contenido": self.contenido[:200],
            "archivo_entrada": self.archivo_entrada,
            "archivo_salida": self.archivo_salida,
            "creado": self.creado,
            "completado": self.completado,
            "error_msg": self.error_msg,
        }


def _trabajo_id() -> str:
    return str(uuid.uuid4())


def _save_trabajo(t: Trabajo) -> Path:
    cli_dir = get_cliente_dir(t.cliente)
    trabajos_dir = cli_dir / ".trabajos"
    trabajos_dir.mkdir(exist_ok=True)
    job_path = trabajos_dir / f"{t.id}_{t.tipo}.yaml"
    job_path.write_text(
        yaml.dump(t.to_dict(), allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    return job_path


def list_trabajos(cliente: str, estado: str = "") -> list[Trabajo]:
    cli_dir = get_cliente_dir(cliente)
    trabajos_dir = cli_dir / ".trabajos"
    if not trabajos_dir.exists():
        return []
    result = []
    for f in sorted(trabajos_dir.glob("*.yaml"), reverse=True):
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if estado and data.get("estado") != estado:
            continue
        result.append(Trabajo(**{k: v for k, v in data.items() if k in Trabajo.__dataclass_fields__}))
    return result


def escanear_entrada(cliente: str) -> list[Path]:
    """Escanea la carpeta entrada/ de un cliente y retorna archivos pendientes."""
    cli_dir = get_cliente_dir(cliente)
    entrada_dir = cli_dir / "entrada"
    if not entrada_dir.exists():
        return []
    return sorted([p for p in entrada_dir.iterdir() if p.is_file() and not p.name.startswith(".")])


async def procesar_documento(archivo: Path) -> tuple[str, str]:
    """Lee un documento de entrada y extrae texto mediante ocr_processor.

    Soporta txt/md/docx/pdf y también imágenes (png/jpg/...). Los PDFs
    escaneados y las imágenes se procesan con OCR de visión (GPT-4o),
    de modo que la carpeta Co-Work puede ingerir fotos y escaneos.
    """
    from ocr_processor import extract_markdown
    try:
        text, meta = await extract_markdown(archivo)
        if not text.strip():
            return "", f"No se extrajo texto de {archivo.name}. El documento puede requerir OCR."
        return text, meta.get("tipo_src", archivo.name)
    except Exception as e:
        return "", f"Error leyendo {archivo.name}: {e}"


def mover_a_procesados(cliente: str, archivo: Path) -> Path:
    cli_dir = get_cliente_dir(cliente)
    procesados_dir = cli_dir / "procesados"
    procesados_dir.mkdir(exist_ok=True)
    dest = procesados_dir / archivo.name
    shutil.move(str(archivo), str(dest))
    return dest


def crear_trabajo(cliente: str, tipo: str, titulo: str, contenido: str = "", archivo_entrada: str = "") -> Trabajo:
    t = Trabajo(
        id=_trabajo_id(),
        cliente=cliente,
        tipo=tipo,
        titulo=titulo,
        contenido=contenido,
        archivo_entrada=archivo_entrada,
    )
    _save_trabajo(t)
    return t


def completar_trabajo(t: Trabajo, contenido: str = "", archivo_salida: str = ""):
    t.estado = "completado"
    t.completado = datetime.now().strftime("%Y-%m-%d %H:%M")
    if contenido:
        t.contenido = contenido
    if archivo_salida:
        t.archivo_salida = archivo_salida
    _save_trabajo(t)


def marcar_error(t: Trabajo, error_msg: str):
    t.estado = "error"
    t.error_msg = error_msg
    _save_trabajo(t)


async def redactar_para_cliente(
    cliente: str,
    tipo: str,
    instrucciones: str,
    *,
    contenido_extra: str = "",
    llm_client=None,
) -> str:
    """
    Redacta un documento para un cliente usando el LLM con el contexto del cliente
    y los skills activos detectados automaticamente.

    Args:
        cliente: nombre del cliente
        tipo: tipo de documento (peticion, analisis, respuesta, etc.)
        instrucciones: instrucciones especificas del usuario
        contenido_extra: texto adicional de documentos del cliente
        llm_client: cliente LLM (si no, usa el default)

    Returns:
        contenido redactado
    """
    from obsidian_writer import COWORK
    cli_dir = COWORK / cliente
    yaml_path = cli_dir / ".cliente.yaml"
    cliente_context = ""
    if yaml_path.exists():
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        cliente_context = "\n".join(f"{k}: {v}" for k, v in data.items())

    skills_context = load_skills_context(f"{tipo} {instrucciones}")

    evidence = ""
    try:
        from official_sources import official_evidence
        evidence, _ = official_evidence(instrucciones)
    except Exception:
        pass
    system = (
        "Eres un asistente legal chileno especializado en derecho tributario.\n"
        "Vas a redactar un documento para un cliente especifico.\n\n"
        f"=== DATOS DEL CLIENTE ===\n{cliente_context}\n\n"
        "=== TIPO DE DOCUMENTO ===\n"
        f"Tipo: {tipo}\n"
        f"Instrucciones: {instrucciones}\n\n"
        "Redacta en espanol chileno formal, dirigido al SII cuando corresponda.\n"
        "Incluye fundamentos de derecho con citas exactas.\n"
        "No presentes proyectos de ley como derecho vigente. Cada afirmacion juridica "
        "debe usar una fuente oficial suministrada o marcarse como pendiente de verificacion.\n"
        "Usa la estructura y el tono del skill de referencia si esta disponible.\n"
    )

    if skills_context:
        system += f"\n{skills_context}\n"

    user = f"{instrucciones}"
    if evidence:
        user += f"\n\n{evidence}"
    if contenido_extra:
        user += f"\n\n=== DOCUMENTOS DEL CLIENTE ===\n{contenido_extra[:5000]}"

    if llm_client:
        try:
            result = await llm_client.chat_completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
                max_tokens=4000,
            )
            return result.strip() if result else ""
        except Exception as e:
            return f"[ERROR LLM: {e}]"

    return system + "\n\n---\n" + user


async def procesar_entrada_cliente(
    cliente: str,
    *,
    llm_client=None,
) -> list[Trabajo]:
    """
    Procesa todos los documentos en la carpeta entrada/ de un cliente.
    Detecta automaticamente el tipo de trabajo necesario.

    Returns:
        lista de trabajos creados
    """
    archivos = escanear_entrada(cliente)
    if not archivos:
        return []

    trabajos = []
    for archivo in archivos:
        try:
            contenido, extraction_note = await procesar_documento(archivo)
        except Exception as exc:
            contenido, extraction_note = "", f"No se pudo leer el documento: {type(exc).__name__}"
        if not contenido:
            t = crear_trabajo(cliente, "analisis", f"Error de lectura: {archivo.name}", archivo_entrada=archivo.name)
            marcar_error(t, extraction_note if extraction_note != archivo.name else "No se extrajo texto. El documento puede requerir OCR.")
            trabajos.append(t)
            continue

        tipo = detectar_tipo_trabajo(archivo.name, contenido)
        t = crear_trabajo(
            cliente=cliente,
            tipo=tipo,
            titulo=f"Procesado: {archivo.name}",
            contenido=contenido[:500],
            archivo_entrada=archivo.name,
        )

        try:
            resultado = await redactar_para_cliente(
                cliente=cliente,
                tipo=tipo,
                instrucciones=f"Procesa el siguiente documento y genera la respuesta o analisis correspondiente.",
                contenido_extra=contenido,
                llm_client=llm_client,
            )

            if resultado and not resultado.startswith("[ERROR"):
                if tipo == "peticion":
                    output_path = write_peticion(cliente, resultado, titulo=f"Respuesta_{archivo.stem}")
                elif tipo == "analisis":
                    output_path = write_analisis(cliente, resultado, titulo=f"Analisis_{archivo.stem}")
                else:
                    output_path = write_note(
                        folder=f"Clientes/{cliente}/Notas",
                        filename=f"trabajo_{t.id}",
                        content=resultado,
                        title=t.titulo,
                        tipo=tipo,
                        cliente=cliente,
                    )

                completar_trabajo(t, contenido=resultado[:500], archivo_salida=str(output_path))
            else:
                marcar_error(t, resultado or "Error desconocido")

        except Exception as e:
            marcar_error(t, str(e))

        # Un original solo sale de entrada cuando hay resultado persistido.
        if t.estado == "completado":
            mover_a_procesados(cliente, archivo)
        trabajos.append(t)

    return trabajos


def detectar_tipo_trabajo(filename: str, content: str) -> str:
    """Detecta automaticamente el tipo de trabajo basado en el contenido."""
    text = (filename + " " + content[:1000]).lower()

    if any(w in text for w in ["observacion", "citacion", "g113", "g22", "fiscalizacion", "notificacion", "liquidacion", "giro"]):
        return "peticion"
    if any(w in text for w in ["sentencia", "fallo", "tdt", "tribunal tributario"]):
        return "analisis"
    if any(w in text for w in ["contrato", "escritura", "compraventa", "sociedad", "constitucion"]):
        return "analisis"
    if any(w in text for w in ["declaracion", "f22", "f29", "renta", "iva"]):
        return "peticion"
    return "analisis"


def get_cliente_estado(cliente: str) -> dict:
    """Retorna un resumen del estado del co-work para un cliente."""
    t = list_trabajos(cliente)
    pendientes = escanear_entrada(cliente)

    return {
        "cliente": cliente,
        "trabajos_total": len(t),
        "trabajos_pendientes": len([x for x in t if x.estado == "pendiente"]),
        "trabajos_completados": len([x for x in t if x.estado == "completado"]),
        "trabajos_error": len([x for x in t if x.estado == "error"]),
        "archivos_pendientes_entrada": len(pendientes),
        "ultimos_trabajos": [x.to_dict() for x in t[:5]],
    }
