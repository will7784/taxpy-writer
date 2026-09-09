"""Estado transaccional para fuentes oficiales y expedientes.

El vault de Obsidian sigue siendo una exportacion humana; esta base guarda el
estado operativo, hashes y auditoria necesarios para reproducir una respuesta.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import config

LEGAL_STATUSES = {
    "vigente", "modificada", "derogada", "proyecto_en_tramitacion",
    "aprobada_no_publicada", "publicada_pendiente_vigencia",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


class ProductionStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.PRODUCTION_DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS official_sources (
              url TEXT PRIMARY KEY, source TEXT NOT NULL, document_type TEXT NOT NULL,
              title TEXT NOT NULL, legal_status TEXT NOT NULL, body TEXT NOT NULL,
              content_hash TEXT NOT NULL, retrieved_at TEXT NOT NULL, effective_date TEXT,
              metadata_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS source_versions (
              id TEXT PRIMARY KEY, url TEXT NOT NULL, version INTEGER NOT NULL,
              content_hash TEXT NOT NULL, legal_status TEXT NOT NULL, body TEXT NOT NULL,
              effective_date TEXT, retrieved_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}',
              UNIQUE(url, version)
            );
            CREATE TABLE IF NOT EXISTS library_catalog (
              catalog_key TEXT PRIMARY KEY, source TEXT NOT NULL, document_type TEXT NOT NULL,
              title TEXT NOT NULL, url TEXT NOT NULL, collection TEXT NOT NULL,
              discovered_at TEXT NOT NULL, downloaded_at TEXT, indexed_at TEXT,
              state TEXT NOT NULL DEFAULT 'discovered', last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS source_changes (
              id TEXT PRIMARY KEY, url TEXT NOT NULL, change_type TEXT NOT NULL,
              old_hash TEXT, new_hash TEXT, detected_at TEXT NOT NULL, summary TEXT NOT NULL,
              FOREIGN KEY(url) REFERENCES official_sources(url)
            );
            CREATE TABLE IF NOT EXISTS source_relations (
              id TEXT PRIMARY KEY, source_url TEXT NOT NULL, relation_type TEXT NOT NULL,
              target TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(source_url, relation_type, target),
              FOREIGN KEY(source_url) REFERENCES official_sources(url)
            );
            CREATE TABLE IF NOT EXISTS sync_runs (
              id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
              status TEXT NOT NULL, sources_checked INTEGER NOT NULL DEFAULT 0,
              changes_found INTEGER NOT NULL DEFAULT 0, error TEXT
            );
            CREATE TABLE IF NOT EXISTS cases (
              id TEXT PRIMARY KEY, client_id TEXT NOT NULL, title TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'activo', created_at TEXT NOT NULL, archived_at TEXT
            );
            CREATE TABLE IF NOT EXISTS case_documents (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, original_path TEXT NOT NULL,
              extracted_path TEXT, sha256 TEXT NOT NULL, pages INTEGER, ocr INTEGER NOT NULL DEFAULT 0,
              extraction_status TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(case_id, sha256), FOREIGN KEY(case_id) REFERENCES cases(id)
            );
            CREATE TABLE IF NOT EXISTS case_jobs (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, document_id TEXT, status TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0, input_hash TEXT NOT NULL, output_path TEXT,
              evidence_json TEXT NOT NULL DEFAULT '[]', model TEXT, error TEXT,
              created_at TEXT NOT NULL, finished_at TEXT,
              UNIQUE(case_id, input_hash), FOREIGN KEY(case_id) REFERENCES cases(id)
            );
            CREATE TABLE IF NOT EXISTS case_memory (
              id TEXT PRIMARY KEY, case_id TEXT NOT NULL, fact TEXT NOT NULL, source_document_id TEXT,
              reviewed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
              FOREIGN KEY(case_id) REFERENCES cases(id)
            );
            CREATE TABLE IF NOT EXISTS audit_events (
              id TEXT PRIMARY KEY, event_type TEXT NOT NULL, entity_id TEXT, details_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """)
            # FTS5 es una mejora de consulta, no una condición para conservar
            # evidencia. Algunas distribuciones mínimas de SQLite lo omiten.
            try:
                c.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS official_sources_fts
                    USING fts5(url UNINDEXED, title, body, tokenize='unicode61 remove_diacritics 2')""")
            except sqlite3.OperationalError:
                pass

    def audit(self, event_type: str, entity_id: str | None = None, **details: Any) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?, ?)",
                      (str(uuid.uuid4()), event_type, entity_id, json.dumps(details, ensure_ascii=False), _now()))

    def begin_sync(self) -> str:
        run_id = str(uuid.uuid4())
        with self._conn() as c:
            # Un reinicio o una caída de red no debe dejar una sincronización
            # eternamente "running" ni ocultar que requiere una nueva pasada.
            c.execute("UPDATE sync_runs SET status='interrupted',finished_at=?,error=COALESCE(error,'proceso interrumpido') WHERE status='running'",
                      (_now(),))
            c.execute("INSERT INTO sync_runs (id,started_at,status) VALUES (?,?,'running')", (run_id, _now()))
        return run_id

    def finish_sync(self, run_id: str, *, checked: int, changes: int, error: str = "") -> None:
        with self._conn() as c:
            c.execute("UPDATE sync_runs SET finished_at=?,status=?,sources_checked=?,changes_found=?,error=? WHERE id=?",
                      (_now(), "failed" if error else "completed", checked, changes, error or None, run_id))

    def upsert_source(self, *, url: str, source: str, document_type: str, title: str,
                      legal_status: str, body: str, effective_date: str | None = None,
                      metadata: dict[str, Any] | None = None) -> str | None:
        if legal_status not in LEGAL_STATUSES:
            raise ValueError(f"Estado juridico invalido: {legal_status}")
        body_hash = _hash(body)
        with self._conn() as c:
            old = c.execute("SELECT * FROM official_sources WHERE url=?", (url,)).fetchone()
            unchanged = old and all(old[key] == value for key, value in {
                "content_hash": body_hash, "source": source, "document_type": document_type,
                "title": title, "legal_status": legal_status, "effective_date": effective_date,
            }.items())
            if unchanged:
                c.execute("UPDATE official_sources SET retrieved_at=?,metadata_json=? WHERE url=?",
                          (_now(), json.dumps(metadata or {}, ensure_ascii=False), url))
                self._refresh_fts(c, url, title, body)
                self._ensure_current_version(c, url)
                return None
            change_type = "alta" if not old else "modificacion"
            c.execute("""INSERT INTO official_sources
                (url,source,document_type,title,legal_status,body,content_hash,retrieved_at,effective_date,metadata_json)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(url) DO UPDATE SET source=excluded.source,document_type=excluded.document_type,
                title=excluded.title,legal_status=excluded.legal_status,body=excluded.body,content_hash=excluded.content_hash,
                retrieved_at=excluded.retrieved_at,effective_date=excluded.effective_date,metadata_json=excluded.metadata_json""",
                (url, source, document_type, title, legal_status, body, body_hash, _now(), effective_date,
                 json.dumps(metadata or {}, ensure_ascii=False)))
            change_id = str(uuid.uuid4())
            c.execute("INSERT INTO source_changes VALUES (?,?,?,?,?,?,?)",
                      (change_id, url, change_type, old["content_hash"] if old else None, body_hash, _now(), title[:500]))
            self._append_version(c, url, body_hash, legal_status, body, effective_date, metadata or {})
            self._refresh_fts(c, url, title, body)
        self.audit("official_source_changed", url, change_type=change_type, legal_status=legal_status)
        return change_id

    @staticmethod
    def _refresh_fts(conn: sqlite3.Connection, url: str, title: str, body: str) -> None:
        """Actualiza el índice sin duplicar resultados si FTS5 está disponible."""
        try:
            conn.execute("DELETE FROM official_sources_fts WHERE url=?", (url,))
            conn.execute("INSERT INTO official_sources_fts (url,title,body) VALUES (?,?,?)", (url, title, body))
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _append_version(conn: sqlite3.Connection, url: str, content_hash: str, legal_status: str,
                        body: str, effective_date: str | None, metadata: dict[str, Any]) -> None:
        exists = conn.execute("SELECT 1 FROM source_versions WHERE url=? AND content_hash=?", (url, content_hash)).fetchone()
        if exists:
            return
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM source_versions WHERE url=?", (url,)).fetchone()[0]
        conn.execute("""INSERT INTO source_versions
            (id,url,version,content_hash,legal_status,body,effective_date,retrieved_at,metadata_json)
            VALUES (?,?,?,?,?,?,?,?,?)""", (str(uuid.uuid4()), url, version, content_hash,
            legal_status, body, effective_date, _now(), json.dumps(metadata, ensure_ascii=False)))

    @staticmethod
    def _ensure_current_version(conn: sqlite3.Connection, url: str) -> None:
        current = conn.execute("SELECT * FROM official_sources WHERE url=?", (url,)).fetchone()
        if current:
            ProductionStore._append_version(conn, url, current["content_hash"], current["legal_status"],
                current["body"], current["effective_date"], json.loads(current["metadata_json"] or "{}"))

    def latest_changes(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("""SELECT c.*,s.title,s.source,s.document_type,s.legal_status FROM source_changes c
                JOIN official_sources s ON s.url=c.url ORDER BY c.detected_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def search_official(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Retiene solo los mejores resultados y fragmentos, nunca el corpus completo."""
        if limit <= 0:
            return []
        limit = min(limit, 50)
        words = [w for w in re.findall(r"[\wáéíóúñ]{4,}", query.lower())][:12]
        if not words:
            return []
        scored = []
        query_lower = query.lower()
        requests_history = any(term in query_lower for term in ("precedente", "históric", "historico", "remisi", "laguna", "evoluci"))
        with self._conn() as c:
            rows = c.execute("""SELECT url,source,document_type,title,legal_status,content_hash,
                retrieved_at,effective_date,metadata_json,substr(body,1,30000) AS search_text
                FROM official_sources WHERE legal_status != 'derogada' ORDER BY url""")
            for index, row in enumerate(rows):
                haystack = (row["title"] + " " + row["search_text"]).lower()
                score = sum(haystack.count(w) for w in words)
                if not score:
                    continue
                d = dict(row)
                metadata = json.loads(d.pop("metadata_json") or "{}")
                historical = bool(metadata.get("archive_historical"))
                # El archivo anterior a 2024 se activa por una remisión o
                # precedente expreso; si faltan documentos recientes, queda
                # como respaldo de última instancia y se marca al citarlo.
                priority = 2 if not historical else (1 if requests_history else 0)
                body = d.pop("search_text")
                positions = [body.lower().find(w) for w in words]
                first_match = min((p for p in positions if p >= 0), default=0)
                start = max(0, first_match - 250)
                d["score"] = score
                d["extract"] = body[start:start + 1800]
                d["historical_archive"] = historical
                item = (priority, score, -index, d)
                if len(scored) < limit:
                    heapq.heappush(scored, item)
                else:
                    heapq.heapreplace(scored, item)
        return [item[3] for item in sorted(scored, reverse=True)]

    def full_official_documents(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        """Recupera los originales de los resultados locales para análisis.

        ``search_official`` mantiene ventanas pequeñas para paneles y listados;
        esta operación se usa sólo después de esa clasificación y conserva el
        texto completo requerido para verificar citas.
        """
        matches = self.search_official(query, limit=limit)
        if not matches:
            return []
        urls = [item["url"] for item in matches]
        placeholders = ",".join("?" for _ in urls)
        with self._conn() as c:
            rows = c.execute(f"""SELECT url,source,document_type,title,legal_status,body,content_hash,
                retrieved_at,effective_date,metadata_json FROM official_sources
                WHERE url IN ({placeholders})""", urls).fetchall()
        by_url = {row["url"]: dict(row) for row in rows}
        for url, document in by_url.items():
            document["relations"] = self.source_relations(url)
        return [by_url[url] for url in urls if url in by_url]

    def add_source_relation(self, source_url: str, *, relation_type: str, target: str) -> None:
        self.add_source_relations([(source_url, relation_type, target)])

    def add_source_relations(self, relations: list[tuple[str, str, str]]) -> None:
        """Registra relaciones en lote; el importador no abre miles de conexiones."""
        if not relations:
            return
        with self._conn() as c:
            c.executemany("""INSERT OR IGNORE INTO source_relations
                (id,source_url,relation_type,target,created_at) VALUES (?,?,?,?,?)""",
                [(str(uuid.uuid4()), source_url, relation_type, target, _now())
                 for source_url, relation_type, target in relations])

    def source_relations(self, source_url: str) -> list[dict[str, str]]:
        with self._conn() as c:
            rows = c.execute("""SELECT relation_type,target FROM source_relations
                WHERE source_url=? ORDER BY relation_type,target""", (source_url,)).fetchall()
        return [dict(row) for row in rows]

    # ── Catálogo y cobertura de biblioteca ──────────────────────

    def register_library_catalog(self, entries: list[dict[str, Any]]) -> None:
        """Registra puntos de entrada oficiales sin afirmar que ya se indexaron."""
        with self._conn() as c:
            for entry in entries:
                values = {
                    "catalog_key": entry["key"], "source": entry["source"],
                    "document_type": entry["document_type"], "title": entry["title"],
                    "url": entry["url"], "collection": entry["collection"], "discovered_at": _now(),
                }
                c.execute("""INSERT INTO library_catalog
                    (catalog_key,source,document_type,title,url,collection,discovered_at)
                    VALUES (:catalog_key,:source,:document_type,:title,:url,:collection,:discovered_at)
                    ON CONFLICT(catalog_key) DO UPDATE SET source=excluded.source,
                    document_type=excluded.document_type,title=excluded.title,url=excluded.url,collection=excluded.collection""", values)

    def record_library_attempt(self, catalog_key: str, *, downloaded: bool, indexed: bool,
                               error: str = "") -> None:
        state = "indexed" if indexed else "downloaded" if downloaded else "pending"
        with self._conn() as c:
            # Una caída posterior del portal no borra la evidencia que ya se
            # descargó e indexó. Se conserva como advertencia operacional,
            # sin convertir artificialmente la cobertura previa en pendiente.
            c.execute("""UPDATE library_catalog SET
                state=CASE WHEN ? THEN ? WHEN state IN ('downloaded','indexed') THEN state ELSE 'pending' END,
                downloaded_at=CASE WHEN ? THEN ? ELSE downloaded_at END,
                indexed_at=CASE WHEN ? THEN ? ELSE indexed_at END,
                last_error=? WHERE catalog_key=?""",
                (int(downloaded or indexed), state, int(downloaded), _now(), int(indexed), _now(), error or None, catalog_key))

    def library_coverage(self) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("""SELECT collection, source, document_type,
                COUNT(*) AS discovered,
                SUM(CASE WHEN downloaded_at IS NOT NULL THEN 1 ELSE 0 END) AS downloaded,
                SUM(CASE WHEN indexed_at IS NOT NULL THEN 1 ELSE 0 END) AS indexed,
                SUM(CASE WHEN state='pending' THEN 1 ELSE 0 END) AS pending
                FROM library_catalog GROUP BY collection,source,document_type
                ORDER BY collection,source,document_type""").fetchall()
        return [dict(row) | {"coverage": "no cuantificable"} for row in rows]

    # ── Investigaciones persistentes y presupuesto ───────────────

    def create_research_run(self, *, query: str, client_id: str | None = None,
                            case_id: str | None = None, facts_date: str | None = None,
                            budget_usd: float = 15.0) -> dict[str, Any]:
        run = {"id": str(uuid.uuid4()), "query": query, "client_id": client_id, "case_id": case_id,
               "facts_date": facts_date, "status": "queued", "budget_usd": budget_usd,
               "reserved_final_usd": round(budget_usd * min(1.0, max(0.0, config.RESEARCH_RESERVED_FINAL_SHARE)), 4), "created_at": _now()}
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS research_runs (
              id TEXT PRIMARY KEY, query TEXT NOT NULL, client_id TEXT, case_id TEXT, facts_date TEXT,
              status TEXT NOT NULL, budget_usd REAL NOT NULL, reserved_final_usd REAL NOT NULL,
              result_json TEXT, error TEXT, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)
            """)
            c.execute("""CREATE TABLE IF NOT EXISTS research_usage (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, purpose TEXT NOT NULL, provider TEXT,
              estimated_usd REAL NOT NULL, actual_usd REAL, status TEXT NOT NULL, created_at TEXT NOT NULL,
              FOREIGN KEY(run_id) REFERENCES research_runs(id)
            )""")
            c.execute("""CREATE TABLE IF NOT EXISTS research_messages (
              id TEXT PRIMARY KEY, run_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
              created_at TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES research_runs(id)
            )""")
            c.execute("""INSERT INTO research_runs
                (id,query,client_id,case_id,facts_date,status,budget_usd,reserved_final_usd,created_at)
                VALUES (:id,:query,:client_id,:case_id,:facts_date,:status,:budget_usd,:reserved_final_usd,:created_at)""", run)
        self.audit("research_run_created", run["id"], client_id=client_id, case_id=case_id, budget_usd=budget_usd)
        return run

    def research_run(self, run_id: str) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result.pop("result_json") or "null")
        result["usage"] = self.research_usage(run_id)
        result["messages"] = self.research_messages(run_id)
        return result

    def restartable_research_runs(self) -> list[dict[str, Any]]:
        """Trabajos que un reinicio dejó sin una tarea de ejecución en memoria."""
        try:
            with self._conn() as c:
                rows = c.execute("SELECT * FROM research_runs WHERE status IN ('queued','running') ORDER BY created_at").fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def add_research_message(self, run_id: str, *, role: str, content: str) -> dict[str, str]:
        if role not in {"user", "system"}:
            raise ValueError("Rol de mensaje inválido")
        message = {"id": str(uuid.uuid4()), "run_id": run_id, "role": role,
                   "content": content, "created_at": _now()}
        with self._conn() as c:
            if not c.execute("SELECT 1 FROM research_runs WHERE id=?", (run_id,)).fetchone():
                raise KeyError("Investigación no encontrada")
            c.execute("INSERT INTO research_messages VALUES (:id,:run_id,:role,:content,:created_at)", message)
        self.audit("research_message_added", run_id, role=role)
        return message

    def research_messages(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with self._conn() as c:
                rows = c.execute("SELECT * FROM research_messages WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def append_research_clarification(self, run_id: str, clarification: str) -> None:
        """Conserva una aclaración segura y la incorpora al contexto al reanudar."""
        self.add_research_message(run_id, role="user", content=clarification)
        with self._conn() as c:
            c.execute("""UPDATE research_runs
                SET query=query || ?, status='queued', result_json=NULL, error=NULL, finished_at=NULL
                WHERE id=?""", ("\n\nAclaración posterior del expediente: " + clarification, run_id))
        self.audit("research_clarification_appended", run_id)

    def set_research_run(self, run_id: str, *, status: str, result: dict[str, Any] | None = None,
                         error: str | None = None) -> None:
        if status not in {"queued", "running", "completed", "partial", "failed", "cancelled"}:
            raise ValueError("Estado de investigación inválido")
        started = _now() if status == "running" else None
        finished = _now() if status in {"completed", "partial", "failed", "cancelled"} else None
        with self._conn() as c:
            c.execute("""UPDATE research_runs SET status=?,result_json=COALESCE(?,result_json),
                error=COALESCE(?,error),started_at=COALESCE(?,started_at),finished_at=COALESCE(?,finished_at)
                WHERE id=?""", (status, json.dumps(result, ensure_ascii=False) if result is not None else None,
                error, started, finished, run_id))
        self.audit("research_run_" + status, run_id, error=error)

    def research_usage(self, run_id: str) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM research_usage WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def reserve_research_usage(self, run_id: str, *, purpose: str, provider: str,
                               estimate_usd: float, final_stage: bool = False,
                               monthly_limit_usd: float = 150.0) -> str:
        """Reserva una llamada antes de emitirla; impide superar topes conocidos."""
        if estimate_usd <= 0:
            raise ValueError("La estimación debe ser positiva")
        with self._conn() as c:
            run = c.execute("SELECT * FROM research_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise KeyError("Investigación no encontrada")
            used = c.execute("SELECT COALESCE(SUM(COALESCE(actual_usd,estimated_usd)),0) FROM research_usage WHERE run_id=? AND status != 'released'", (run_id,)).fetchone()[0]
            reserve = 0 if final_stage else run["reserved_final_usd"]
            if used + estimate_usd + reserve > run["budget_usd"] + 1e-9:
                raise RuntimeError("Se alcanzó el presupuesto de esta investigación; puedes reanudarla con un límite mayor.")
            month = run["created_at"][:7]
            monthly = c.execute("""SELECT COALESCE(SUM(COALESCE(actual_usd,estimated_usd)),0)
                FROM research_usage u JOIN research_runs r ON r.id=u.run_id
                WHERE substr(r.created_at,1,7)=? AND u.status != 'released'""", (month,)).fetchone()[0]
            if monthly + estimate_usd > monthly_limit_usd + 1e-9:
                raise RuntimeError("Se alcanzó el presupuesto mensual de investigación.")
            usage_id = str(uuid.uuid4())
            c.execute("INSERT INTO research_usage VALUES (?,?,?,?,?,?,?,?)", (usage_id, run_id, purpose, provider,
                estimate_usd, None, "reserved", _now()))
        return usage_id

    def commit_research_usage(self, usage_id: str, actual_usd: float | None = None) -> None:
        with self._conn() as c:
            c.execute("UPDATE research_usage SET status='committed',actual_usd=COALESCE(?,estimated_usd) WHERE id=?",
                      (actual_usd, usage_id))

    def release_research_usage(self, usage_id: str) -> None:
        """Libera una reserva si el proveedor no llegó a completar la llamada."""
        with self._conn() as c:
            c.execute("UPDATE research_usage SET status='released' WHERE id=? AND status='reserved'", (usage_id,))

    def backup(self, destination_dir: Path | None = None) -> dict[str, Any]:
        """Crea una copia consistente de SQLite y comprueba que puede restaurarse."""
        destination_dir = destination_dir or config.BACKUPS_DIR
        destination_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = destination_dir / f"impuestia_production_{stamp}.sqlite3"
        check_target = destination_dir / f".restore_check_{stamp}.sqlite3"
        source = sqlite3.connect(str(self.path))
        backup = sqlite3.connect(str(target))
        try:
            source.backup(backup)
        finally:
            backup.close()
            source.close()
        backup = sqlite3.connect(str(target))
        restored = sqlite3.connect(str(check_target))
        try:
            backup.backup(restored)
        finally:
            restored.close()
            backup.close()
        try:
            backup = sqlite3.connect(str(target))
            restored = sqlite3.connect(str(check_target))
            try:
                integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
                source_count = backup.execute("SELECT COUNT(*) FROM official_sources").fetchone()[0]
                restored_count = restored.execute("SELECT COUNT(*) FROM official_sources").fetchone()[0]
            finally:
                restored.close()
                backup.close()
            if integrity.lower() != "ok" or source_count != restored_count:
                raise RuntimeError("La copia no superó la comprobación de restauración")
        finally:
            check_target.unlink(missing_ok=True)
        result = {"path": str(target), "integrity": integrity, "official_sources": source_count,
                  "restoration_verified": True}
        self.audit("production_backup_verified", str(target), **result)
        return result

    def create_case(self, client_id: str, title: str) -> dict[str, str]:
        case = {"id": str(uuid.uuid4()), "client_id": client_id, "title": title, "status": "activo", "created_at": _now()}
        with self._conn() as c:
            c.execute("INSERT INTO cases (id,client_id,title,status,created_at) VALUES (:id,:client_id,:title,:status,:created_at)", case)
        self.audit("case_created", case["id"], client_id=client_id, title=title)
        return case

    def list_cases(self, client_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM cases WHERE (? IS NULL OR client_id=?) ORDER BY created_at DESC", (client_id, client_id)).fetchall()
        return [dict(r) for r in rows]

    def case_for(self, case_id: str, client_id: str | None = None) -> dict[str, Any] | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM cases WHERE id=? AND (? IS NULL OR client_id=?)", (case_id, client_id, client_id)).fetchone()
        return dict(row) if row else None

    def add_case_document(self, case_id: str, original_path: str, sha256: str, *, pages: int | None = None,
                          ocr: bool = False, extracted_path: str | None = None, status: str = "extraido") -> dict[str, Any]:
        doc = {"id": str(uuid.uuid4()), "case_id": case_id, "original_path": original_path,
               "extracted_path": extracted_path, "sha256": sha256, "pages": pages, "ocr": int(ocr),
               "extraction_status": status, "created_at": _now()}
        with self._conn() as c:
            found = c.execute("SELECT * FROM case_documents WHERE case_id=? AND sha256=?", (case_id, sha256)).fetchone()
            if found: return dict(found)
            c.execute("""INSERT INTO case_documents
                (id,case_id,original_path,extracted_path,sha256,pages,ocr,extraction_status,created_at)
                VALUES (:id,:case_id,:original_path,:extracted_path,:sha256,:pages,:ocr,:extraction_status,:created_at)""", doc)
        self.audit("case_document_added", doc["id"], case_id=case_id, sha256=sha256)
        return doc

    def create_job(self, case_id: str, input_hash: str, document_id: str | None = None, model: str | None = None) -> dict[str, Any]:
        job = {"id": str(uuid.uuid4()), "case_id": case_id, "document_id": document_id, "status": "queued",
               "attempts": 0, "input_hash": input_hash, "model": model, "created_at": _now()}
        with self._conn() as c:
            found = c.execute("SELECT * FROM case_jobs WHERE case_id=? AND input_hash=?", (case_id, input_hash)).fetchone()
            if found: return dict(found)
            c.execute("""INSERT INTO case_jobs (id,case_id,document_id,status,attempts,input_hash,model,created_at)
                VALUES (:id,:case_id,:document_id,:status,:attempts,:input_hash,:model,:created_at)""", job)
        return job

    def complete_case_document(self, document_id: str, *, extracted_path: str, pages: int | None, ocr: bool) -> None:
        with self._conn() as c:
            c.execute("UPDATE case_documents SET extracted_path=?,pages=?,ocr=?,extraction_status='extraido' WHERE id=?",
                      (extracted_path, pages, int(ocr), document_id))
        self.audit("case_document_extracted", document_id, pages=pages, ocr=ocr)

    def finish_job(self, job_id: str, *, status: str, output_path: str | None = None,
                   evidence: list[dict[str, Any]] | None = None, error: str | None = None) -> None:
        if status not in {"completed", "failed", "review"}: raise ValueError("Estado de trabajo invalido")
        with self._conn() as c:
            c.execute("UPDATE case_jobs SET status=?,attempts=attempts+1,output_path=?,evidence_json=?,error=?,finished_at=? WHERE id=?",
                      (status, output_path, json.dumps(evidence or [], ensure_ascii=False), error, _now(), job_id))
        self.audit("case_job_" + status, job_id, output_path=output_path, error=error)


store = ProductionStore()
