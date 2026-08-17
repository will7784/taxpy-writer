"""
Co-Work Manager — ImpuestIA (Fase 4)

Gestiona trabajos por cliente con carpetas vigiladas (entrada/ → procesados/ → output).
Integrado con Obsidian vault para persistencia y skill_manager para redaccion asistida.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

import config
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
    return datetime.now().strftime("%Y%m%d%H%M%S")


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


def procesar_documento(cliente: str, archivo: Path) -> tuple[str, str]:
    """Lee un documento de entrada y extrae texto para procesar."""
    ext = archivo.suffix.lower()
    if ext == ".txt":
        return archivo.read_text(encoding="utf-8"), archivo.name
    elif ext == ".md":
        return archivo.read_text(encoding="utf-8"), archivo.name
    elif ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(archivo))
            return "\n".join([p.text for p in doc.paragraphs if p.text.strip()]), archivo.name
        except Exception as e:
            return "", f"Error leyendo DOCX: {e}"
    elif ext == ".pdf":
        try:
            import pdfplumber
            with pdfplumber.open(str(archivo)) as pdf:
                pages = [page.extract_text() or "" for page in pdf.pages]
            return "\n".join(pages), archivo.name
        except Exception as e:
            return "", f"Error leyendo PDF: {e}"
    else:
        return "", f"Formato no soportado: {ext}"


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


def redactar_para_cliente(
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

    system = (
        "Eres un asistente legal chileno especializado en derecho tributario.\n"
        "Vas a redactar un documento para un cliente especifico.\n\n"
        f"=== DATOS DEL CLIENTE ===\n{cliente_context}\n\n"
        "=== TIPO DE DOCUMENTO ===\n"
        f"Tipo: {tipo}\n"
        f"Instrucciones: {instrucciones}\n\n"
        "Redacta en espanol chileno formal, dirigido al SII cuando corresponda.\n"
        "Incluye fundamentos de derecho con citas exactas.\n"
        "Usa la estructura y el tono del skill de referencia si esta disponible.\n"
    )

    if skills_context:
        system += f"\n{skills_context}\n"

    user = f"{instrucciones}"
    if contenido_extra:
        user += f"\n\n=== DOCUMENTOS DEL CLIENTE ===\n{contenido_extra[:5000]}"

    if llm_client:
        try:
            result = llm_client.chat_completion(
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


def procesar_entrada_cliente(
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
        contenido, _ = procesar_documento(cliente, archivo)
        if not contenido:
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
            resultado = redactar_para_cliente(
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
                        titulo=t.titulo,
                        tipo=tipo,
                        cliente=cliente,
                    )

                completar_trabajo(t, contenido=resultado[:500], archivo_salida=str(output_path))
            else:
                marcar_error(t, resultado or "Error desconocido")

        except Exception as e:
            marcar_error(t, str(e))

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
