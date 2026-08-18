"""
Sync vault producción → Obsidian local (Dropbox).

Baja el vault desde el panel de producción (GET /api/vault/export) y lo
mergea al vault local de forma idempotente:

  - Archivo nuevo localmente      -> se descarga
  - Igual hash                    -> se salta
  - Remoto cambió + local intacto -> se sobrescribe (prod es la fuente)
  - Remoto cambió + local editado -> CONFLICTO: se conserva el local y la
                                     copia remota queda en _Sync/Conflictos/
  - Borrado en remoto + local intacto -> se elimina localmente

El estado previo se guarda en config.SYNC_STATE_PATH para detectar si el
archivo local fue editado desde la última sync.

Uso:
    python sync_vault.py            # sync normal
    python sync_vault.py --dry-run  # solo muestra qué haría
    python sync_vault.py --full     # sobrescribe todo aunque haya conflictos
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import httpx

import config

CONFLICT_DIR_NAME = "_Sync/Conflictos"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_state() -> dict:
    if config.SYNC_STATE_PATH.exists():
        try:
            return json.loads(config.SYNC_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    config.SYNC_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _login(client: httpx.Client, url: str) -> bool:
    resp = client.post(
        f"{url}/login",
        data={"username": config.ADMIN_USERNAME, "password": config.ADMIN_PASSWORD},
        follow_redirects=True,
    )
    if resp.status_code >= 400:
        return False
    # FastAPI setea la sesión solo si las credenciales coinciden; si no,
    # redirige a /?error=... y no quedamos autenticados. Verificamos con
    # una llamada a un endpoint protegido.
    check = client.get(f"{url}/api/vault/status", follow_redirects=True)
    return check.status_code == 200 and check.json().get("vault_exists") is not None


def sync(dry_run: bool = False, full: bool = False) -> int:
    url = config.SYNC_PROD_URL.rstrip("/")
    vault = config.OBSIDIAN_VAULT_PATH
    vault.mkdir(parents=True, exist_ok=True)

    print(f"Conectando a {url} ...")
    with httpx.Client(timeout=120.0) as client:
        if not _login(client, url):
            print("ERROR: no se pudo autenticar contra el panel (revisa ADMIN_USERNAME/PASSWORD).")
            return 1

        print("Descargando export del vault ...")
        resp = client.get(f"{url}/api/vault/export", follow_redirects=True)
        if resp.status_code != 200:
            print(f"ERROR: /api/vault/export respondió {resp.status_code}: {resp.text[:200]}")
            return 1

        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        manifest = json.loads(zf.read("_MANIFEST.json"))["files"]
        remote = {m["path"]: m for m in manifest}

    state = _load_state()

    stats = {"download": 0, "overwrite": 0, "skip": 0, "conflict": 0, "delete": 0, "conflict_files": []}
    new_state: dict = {}
    vault_paths = {p.relative_to(vault).as_posix() for p in vault.rglob("*") if p.is_file()}
    excluded_dirs = {".obsidian", ".trash", ".git"}
    vault_paths = {p for p in vault_paths if not any(part in excluded_dirs for part in Path(p).parts)}

    # 1) Merge de archivos remotos
    for path, meta in remote.items():
        target = vault / path
        target.parent.mkdir(parents=True, exist_ok=True)
        prev = state.get(path, {})

        if not target.exists():
            action = "download"
        else:
            local_hash = _sha256_file(target)
            if local_hash == meta["sha256"]:
                action = "skip"
            elif not prev or prev.get("local_hash") == local_hash or full:
                action = "overwrite"
            else:
                action = "conflict"

        if action in ("download", "overwrite"):
            if dry_run:
                print(f"[{action}] {path}")
            else:
                target.write_bytes(zf.read(path))
                print(f"[{action}] {path}")
            stats[action] += 1
            new_state[path] = {
                "remote_hash": meta["sha256"],
                "local_hash": meta["sha256"],
                "mtime": meta["mtime"],
            }
        elif action == "skip":
            stats["skip"] += 1
            new_state[path] = prev
        else:  # conflict
            stats["conflict"] += 1
            stats["conflict_files"].append(path)
            print(f"[conflicto] {path} (local editado; se conserva el local)")
            if not dry_run:
                conflict_target = vault / CONFLICT_DIR_NAME / path
                conflict_target.parent.mkdir(parents=True, exist_ok=True)
                conflict_target.write_bytes(zf.read(path))
            new_state[path] = prev  # no se toca el estado local

    # 2) Borrados remotos (solo si el local está intacto desde la última sync)
    for path, prev in state.items():
        if path in remote:
            continue
        local = vault / path
        if not local.exists():
            new_state[path] = prev
            continue
        local_hash = _sha256_file(local)
        if prev.get("local_hash") == local_hash:
            if dry_run:
                print(f"[delete] {path}")
            else:
                local.unlink()
                print(f"[delete] {path}")
            stats["delete"] += 1
        else:
            print(f"[local] {path} (editado localmente y ya no existe en prod; se conserva)")
            new_state[path] = prev

    # 3) Archivos locales que nunca estuvieron en el manifest (creados en local)
    for p in vault_paths:
        if p not in remote and p not in state:
            print(f"[local] {p} (solo local; se conserva)")

    if not dry_run:
        _save_state(new_state)

    print("\n--- Resumen ---")
    print(f"Descargados: {stats['download']} | Sobrescritos: {stats['overwrite']} | Sin cambios: {stats['skip']}")
    print(f"Conflictos: {stats['conflict']} | Borrados locales: {stats['delete']}")
    if stats["conflict_files"]:
        print("\nArchivos en conflicto (copia remota en _Sync/Conflictos/):")
        for p in stats["conflict_files"]:
            print(f"  - {p}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Sincroniza el vault de producción hacia el Obsidian local.")
    parser.add_argument("--dry-run", action="store_true", help="solo muestra qué se haría, sin tocar archivos")
    parser.add_argument("--full", action="store_true", help="sobrescribe todo aunque haya ediciones locales")
    args = parser.parse_args()
    return sync(dry_run=args.dry_run, full=args.full)


if __name__ == "__main__":
    sys.exit(main())
