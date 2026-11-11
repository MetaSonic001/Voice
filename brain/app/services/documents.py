"""Document lifecycle: upload, versions, replace, update, disable, delete/purge, roll back, re-index.

Processing (parse -> chunk -> embed) runs in the background worker; the final switch to a new version
happens in one transaction, so search never sees a half-indexed document.
"""

import hashlib
import json
import logging
import time
import uuid
from datetime import date
from pathlib import Path

from app.adapters.parsers import SUPPORTED_TYPES, ParserRouter
from app.db import Database
from app.logs import log
from app.ports import Embedder, FileStore
from app.services.chunking import chunk_blocks, content_hash
from app.services.guard import instruction_like

logger = logging.getLogger(__name__)


class DocumentError(ValueError):
    """A client-side problem (bad file, unknown id, invalid state). Mapped to HTTP 4xx."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def detect_type(filename: str, data: bytes) -> str:
    """The extension picks the parser; the first bytes must agree with it (a renamed binary is rejected)."""
    ext = Path(filename).suffix.lower().lstrip(".")
    ext = {"markdown": "md", "text": "txt"}.get(ext, ext)
    if ext not in SUPPORTED_TYPES:
        raise DocumentError(f"unsupported file type '.{ext}'; supported: {', '.join(sorted(SUPPORTED_TYPES))}", 415)
    head = data[:4096]
    if ext == "pdf":
        ok = head.startswith(b"%PDF-")
    elif ext in ("docx", "xlsx"):
        ok = head.startswith(b"PK\x03\x04")  # Office files are ZIP archives
    else:  # text: must decode as UTF-8 and contain no NUL bytes
        try:
            head.decode("utf-8")
            ok = b"\x00" not in head
        except UnicodeDecodeError:
            ok = len(head) == 4096 and b"\x00" not in head  # a multi-byte character may be cut at the boundary
    if not ok:
        raise DocumentError(f"file content does not look like .{ext}", 415)
    return ext


class DocumentService:
    def __init__(self, db: Database, store: FileStore, parser: ParserRouter, embedder: Embedder, settings):
        self.db, self.store, self.parser, self.embedder, self.settings = db, store, parser, embedder, settings

    # ---------- upload ----------

    def upload(self, filename: str, data: bytes, *, title: str | None = None, category: str | None = None,
               purpose: str = "knowledge", effective_from: date | None = None, options: dict | None = None) -> dict:
        if purpose not in ("knowledge", "data"):
            raise DocumentError("purpose must be 'knowledge' or 'data'")
        self._check_size(data)
        file_type = detect_type(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        with self.db.connection() as conn:
            dup = conn.execute(
                "SELECT d.id FROM document_versions v JOIN documents d ON d.id = v.document_id "
                "WHERE v.sha256 = %s AND d.deleted_at IS NULL AND d.purpose = %s LIMIT 1", (sha, purpose)).fetchone()
            if dup:
                return {**self.get(str(dup["id"])), "deduplicated": True}
            doc_id = str(uuid.uuid4())
            with conn.transaction():
                conn.execute("INSERT INTO documents (id, title, category, purpose) VALUES (%s, %s, %s, %s)",
                             (doc_id, title or Path(filename).stem, category, purpose))
                self._add_version(conn, doc_id, 1, filename, file_type, data, sha, effective_from, options or {})
        log(logger, "document uploaded", document_id=doc_id, file=filename, type=file_type, purpose=purpose)
        return {**self.get(doc_id), "deduplicated": False}

    def add_version(self, doc_id: str, filename: str, data: bytes, effective_from: date | None = None,
                    options: dict | None = None) -> dict:
        doc = self._doc(doc_id)
        self._check_size(data)
        file_type = detect_type(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        with self.db.connection() as conn:
            same = conn.execute("SELECT version_no FROM document_versions WHERE document_id = %s AND sha256 = %s "
                                "AND status IN ('queued','processing','indexed')", (doc_id, sha)).fetchone()
            if same:
                return {**self.get(doc_id), "deduplicated": True}
            with conn.transaction():
                n = conn.execute("SELECT coalesce(max(version_no), 0) + 1 AS n FROM document_versions WHERE document_id = %s",
                                 (doc_id,)).fetchone()["n"]
                prev_options = {}
                if doc["current_version_id"]:
                    prev_options = conn.execute("SELECT options FROM document_versions WHERE id = %s",
                                                (doc["current_version_id"],)).fetchone()["options"]
                self._add_version(conn, doc_id, n, filename, file_type, data, sha, effective_from,
                                  options if options is not None else prev_options)
        log(logger, "document version added", document_id=doc_id, version=n)
        return {**self.get(doc_id), "deduplicated": False}

    def _add_version(self, conn, doc_id, n, filename, file_type, data, sha, effective_from, options) -> str:
        version_id = str(uuid.uuid4())
        key = f"{doc_id}/{version_id}/{Path(filename).name}"
        self.store.put(key, data)
        conn.execute(
            "INSERT INTO document_versions (id, document_id, version_no, filename, file_type, size_bytes, sha256, "
            "storage_key, status, effective_from, options) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'queued',%s,%s)",
            (version_id, doc_id, n, filename, file_type, len(data), sha, key, effective_from, json.dumps(options)))
        conn.execute("INSERT INTO jobs (id, kind, version_id) VALUES (%s, 'ingest', %s)", (str(uuid.uuid4()), version_id))
        return version_id

    def _check_size(self, data: bytes) -> None:
        if not data:
            raise DocumentError("empty file")
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise DocumentError(f"file larger than {self.settings.max_upload_mb} MB", 413)

    # ---------- processing (called by the worker) ----------

    def process_version(self, version_id: str) -> dict:
        """Parse, chunk and embed one version, then make it current in one transaction."""
        with self.db.connection() as conn:
            v = conn.execute(
                "SELECT v.*, d.title, d.purpose FROM document_versions v JOIN documents d ON d.id = v.document_id "
                "WHERE v.id = %s", (version_id,)).fetchone()
            conn.execute("UPDATE document_versions SET status = 'processing', error = NULL WHERE id = %s", (version_id,))
        started = time.perf_counter()
        report: dict = {"file_type": v["file_type"]}
        drafts, vectors = [], []
        if v["purpose"] == "knowledge":
            options = {"product_prefixes": [p for p in self.settings.product_title_prefixes.split("|") if p],
                       **(v["options"] or {})}
            with self.store.local_path(v["storage_key"]) as path:
                parsed = self.parser.parse(path, v["file_type"], options)
            report.update(parsed.report)
            report["parse_ms"] = round((time.perf_counter() - started) * 1000)
            drafts = chunk_blocks(parsed.blocks, v["title"], self.settings.chunk_target_words, self.settings.chunk_max_words)
            if not drafts:
                raise DocumentError("no text could be extracted from this file")
            flagged = [d.section_path for d in drafts if instruction_like(d.text)]
            report["instruction_like_passages"] = len(flagged)
            if flagged:  # kept out of answers by services/kbmeta.select_sources; shown on the KB page for a person to review
                logger.warning("document text looks like instructions to the assistant", extra={"data": {"count": len(flagged)}})
            t = time.perf_counter()
            vectors = self.embedder.embed([d.embed_text for d in drafts])
            report["embed_ms"] = round((time.perf_counter() - t) * 1000)
            report.update(chunks=len(drafts), table_chunks=sum(d.kind == "table" for d in drafts),
                          avg_words=round(sum(d.word_count for d in drafts) / len(drafts)))
        else:
            report["stored_only"] = True
        report["total_ms"] = round((time.perf_counter() - started) * 1000)

        with self.db.connection() as conn, conn.transaction():
            conn.execute("DELETE FROM chunks WHERE version_id = %s", (version_id,))  # re-index replaces old chunks
            for i, (draft, vec) in enumerate(zip(drafts, vectors)):
                chunk_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO chunks (id, version_id, ordinal, kind, section_path, text, content_hash, word_count) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (chunk_id, version_id, i, draft.kind, draft.section_path, draft.text, content_hash(draft), draft.word_count))
                conn.execute("INSERT INTO chunk_embeddings (chunk_id, model, embedding) VALUES (%s, %s, %s::vector)",
                             (chunk_id, self.embedder.name, str(vec)))
            doc = conn.execute("SELECT current_version_id FROM documents WHERE id = %s FOR UPDATE",
                               (v["document_id"],)).fetchone()
            if doc["current_version_id"] and str(doc["current_version_id"]) != version_id:
                conn.execute("UPDATE document_versions SET status = 'superseded' WHERE id = %s", (doc["current_version_id"],))
            conn.execute("UPDATE document_versions SET status = 'indexed', report = %s, indexed_at = now() WHERE id = %s",
                         (json.dumps(report), version_id))
            conn.execute("UPDATE documents SET current_version_id = %s, updated_at = now(), "
                         "tags = CASE WHEN %s THEN tags || '{\"instruction_flag\": true}'::jsonb ELSE tags - 'instruction_flag' END "
                         "WHERE id = %s", (version_id, report.get("instruction_like_passages", 0) > 0, v["document_id"]))
            self._refresh_active(conn, str(v["document_id"]))
        log(logger, "version indexed", version_id=version_id, **{k: report[k] for k in ("chunks", "total_ms") if k in report})
        return report

    def mark_failed(self, version_id: str, error: str) -> None:
        # A failed re-index of the current version keeps it 'indexed' (its old chunks are untouched).
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE document_versions v SET error = %s, status = CASE WHEN d.current_version_id = v.id "
                "THEN 'indexed' ELSE 'failed' END FROM documents d WHERE d.id = v.document_id AND v.id = %s",
                (error[:2000], version_id))

    @staticmethod
    def _refresh_active(conn, doc_id: str) -> None:
        conn.execute(
            "UPDATE chunks c SET active = (c.version_id = d.current_version_id AND d.enabled "
            "AND d.deleted_at IS NULL AND d.purpose = 'knowledge') "
            "FROM document_versions v, documents d WHERE c.version_id = v.id AND v.document_id = d.id AND d.id = %s",
            (doc_id,))

    # ---------- reads ----------

    def _doc(self, doc_id: str) -> dict:
        try:
            uuid.UUID(doc_id)
        except ValueError:
            raise DocumentError("document not found", 404) from None
        with self.db.connection() as conn:
            doc = conn.execute("SELECT * FROM documents WHERE id = %s AND deleted_at IS NULL", (doc_id,)).fetchone()
        if not doc:
            raise DocumentError("document not found", 404)
        return doc

    def get(self, doc_id: str) -> dict:
        doc = self._doc(doc_id)
        with self.db.connection() as conn:
            versions = conn.execute(
                "SELECT id, version_no, filename, file_type, size_bytes, sha256, status, error, effective_from, options, "
                "report, created_at, indexed_at, (SELECT count(*) FROM chunks c WHERE c.version_id = v.id) AS chunks "
                "FROM document_versions v WHERE document_id = %s ORDER BY version_no DESC", (doc_id,)).fetchall()
        current = next((v for v in versions if v["id"] == doc["current_version_id"]), None)
        return {**doc, "searchable": bool(current) and doc["enabled"] and doc["purpose"] == "knowledge",
                "current_version": current["version_no"] if current else None, "versions": versions}

    def list_documents(self, include_deleted: bool = False) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT d.id, d.title, d.category, d.purpose, d.enabled, d.deleted_at, d.created_at, d.updated_at, d.tags, "
                "v.version_no AS current_version, v.file_type, v.filename, v.effective_from, v.options, "
                "(SELECT count(*) FROM chunks c WHERE c.version_id = d.current_version_id) AS chunks, "
                "(SELECT status FROM document_versions lv WHERE lv.document_id = d.id ORDER BY version_no DESC LIMIT 1) AS latest_status "
                "FROM documents d LEFT JOIN document_versions v ON v.id = d.current_version_id "
                + ("" if include_deleted else "WHERE d.deleted_at IS NULL ") + "ORDER BY d.created_at").fetchall()

    def chunks(self, doc_id: str, version_no: int | None = None) -> dict:
        doc = self._doc(doc_id)
        with self.db.connection() as conn:
            if version_no is None:
                vid = doc["current_version_id"]
            else:
                row = conn.execute("SELECT id FROM document_versions WHERE document_id = %s AND version_no = %s",
                                   (doc_id, version_no)).fetchone()
                vid = row["id"] if row else None
            if not vid:
                raise DocumentError("version not found or not indexed yet", 404)
            rows = conn.execute("SELECT id, ordinal, kind, section_path, text, word_count, active FROM chunks "
                                "WHERE version_id = %s ORDER BY ordinal", (vid,)).fetchall()
        return {"document_id": doc_id, "version_id": vid, "count": len(rows), "chunks": rows}

    # ---------- changes ----------

    def update(self, doc_id: str, *, title=None, category=None, enabled=None, effective_from=None, tags=None) -> dict:
        doc = self._doc(doc_id)
        with self.db.connection() as conn, conn.transaction():
            if tags is not None:  # a person's correction of the document's type / authority / dates (merged, "" clears a key)
                merged = {**(doc["tags"] or {}), **tags}
                conn.execute("UPDATE documents SET tags = %s WHERE id = %s",
                             (json.dumps({k: v for k, v in merged.items() if v not in ("", None)}), doc_id))
            conn.execute("UPDATE documents SET title = coalesce(%s, title), category = coalesce(%s, category), "
                         "enabled = coalesce(%s, enabled), updated_at = now() WHERE id = %s", (title, category, enabled, doc_id))
            if effective_from is not None and doc["current_version_id"]:
                conn.execute("UPDATE document_versions SET effective_from = %s WHERE id = %s",
                             (effective_from, doc["current_version_id"]))
            if title is not None:  # titles are part of chunk paths; refresh the path prefix in place
                conn.execute("UPDATE chunks c SET section_path = %s || substr(c.section_path, length(%s) + 1) "
                             "FROM document_versions v WHERE c.version_id = v.id AND v.document_id = %s "
                             "AND left(c.section_path, length(%s)) = %s", (title, doc["title"], doc_id, doc["title"], doc["title"]))
            self._refresh_active(conn, doc_id)
        log(logger, "document updated", document_id=doc_id, title=title, category=category, enabled=enabled)
        return self.get(doc_id)

    def delete(self, doc_id: str, purge: bool = False) -> dict:
        if purge:
            with self.db.connection() as conn:
                row = conn.execute("SELECT id FROM documents WHERE id = %s", (doc_id,)).fetchone()
                if not row:
                    raise DocumentError("document not found", 404)
                conn.execute("DELETE FROM documents WHERE id = %s", (doc_id,))
            self.store.delete_prefix(doc_id)
            log(logger, "document purged", document_id=doc_id)
            return {"id": doc_id, "purged": True}
        self._doc(doc_id)
        with self.db.connection() as conn, conn.transaction():
            conn.execute("UPDATE documents SET deleted_at = now(), updated_at = now() WHERE id = %s", (doc_id,))
            self._refresh_active(conn, doc_id)
        log(logger, "document deleted", document_id=doc_id)
        return {"id": doc_id, "deleted": True, "purged": False}

    def activate(self, doc_id: str, version_no: int) -> dict:
        doc = self._doc(doc_id)
        with self.db.connection() as conn, conn.transaction():
            v = conn.execute("SELECT id, status FROM document_versions WHERE document_id = %s AND version_no = %s",
                             (doc_id, version_no)).fetchone()
            if not v:
                raise DocumentError("version not found", 404)
            if v["status"] not in ("indexed", "superseded"):
                raise DocumentError(f"version {version_no} is '{v['status']}' and cannot be activated", 409)
            if doc["current_version_id"] and doc["current_version_id"] != v["id"]:
                conn.execute("UPDATE document_versions SET status = 'superseded' WHERE id = %s", (doc["current_version_id"],))
            conn.execute("UPDATE document_versions SET status = 'indexed' WHERE id = %s", (v["id"],))
            conn.execute("UPDATE documents SET current_version_id = %s, updated_at = now() WHERE id = %s", (v["id"], doc_id))
            self._refresh_active(conn, doc_id)
        log(logger, "version activated", document_id=doc_id, version=version_no)
        return self.get(doc_id)

    def reindex(self, doc_id: str | None = None) -> dict:
        """Queue re-processing of current versions (one document, or all) - e.g. after changing the chunker or model."""
        with self.db.connection() as conn:
            if doc_id:
                self._doc(doc_id)
                rows = conn.execute("SELECT current_version_id AS vid FROM documents WHERE id = %s AND current_version_id IS NOT NULL",
                                    (doc_id,)).fetchall()
            else:
                rows = conn.execute("SELECT current_version_id AS vid FROM documents WHERE deleted_at IS NULL "
                                    "AND current_version_id IS NOT NULL").fetchall()
            for r in rows:
                conn.execute("INSERT INTO jobs (id, kind, version_id) VALUES (%s, 'ingest', %s)", (str(uuid.uuid4()), r["vid"]))
        return {"queued": len(rows)}

    def jobs(self, status: str | None = None, limit: int = 50) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT j.*, v.filename, v.version_no FROM jobs j LEFT JOIN document_versions v ON v.id = j.version_id "
                + ("WHERE j.status = %s " if status else "") + "ORDER BY j.created_at DESC LIMIT %s",
                ((status, limit) if status else (limit,))).fetchall()
