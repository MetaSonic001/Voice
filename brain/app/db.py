"""Postgres access: a private server under data/pg (pgserver, no Docker) or an external DATABASE_URL.

Synchronous psycopg with a small pool. Async code calls it through `asyncio.to_thread`.
"""

import json
import logging
import time
from contextlib import contextmanager
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.logs import log

logger = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).parent / "migrations"
# Postgres advisory lock for one-time startup work (migrations, vector index, mock-bank seeding). When several copies
# of the brain start at once against one database, they take turns: the first does the work, the others then see it
# done. Released when its connection closes, so a crashed copy can't leave it held.
STARTUP_LOCK = 7_241_001


def _start_private_server(pg_dir: Path, wait_s: float = 120):
    """Start (or reuse) the private Postgres; it is stopped again when the app shuts down (Database.close).

    After an unclean shutdown (killed process, reboot) Postgres first runs crash recovery, which can outlast
    pgserver's fixed 10 s start timeout. pgserver caches a failed handle per folder, so drop it and retry until
    the server accepts connections.
    """
    import pgserver  # only needed for the private server
    from pgserver.postgres_server import PostgresServer

    pg_dir = pg_dir.resolve()
    pg_dir.mkdir(parents=True, exist_ok=True)
    _prune_dead_handles(pg_dir / ".handle_pids.json")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            server = pgserver.get_server(pg_dir, cleanup_mode="stop")
            url = server.get_uri()
            with psycopg.connect(url, connect_timeout=5):
                pass
            log(logger, "postgres ready", data_dir=str(pg_dir))
            return server, url
        except Exception as e:
            PostgresServer._instances.pop(pg_dir, None)  # a failed handle is never refreshed by pgserver
            if time.monotonic() > deadline:
                raise RuntimeError(f"private Postgres did not start within {wait_s:.0f}s ({type(e).__name__}). "
                                   f"Last lines of {pg_dir / 'log'}:\n{_log_tail(pg_dir / 'log')}") from e
            logger.warning("postgres still starting (crash recovery after an unclean shutdown?) - retrying in 3 s")
            time.sleep(3)


def _prune_dead_handles(path: Path) -> None:
    """pgserver stops Postgres only when the last process using it exits, tracked in this file. Processes that
    crashed or were killed stay listed forever, so Postgres would never be stopped - drop them."""
    import psutil

    try:
        pids = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    alive = [pid for pid in pids if psutil.pid_exists(pid)]
    if alive != pids:
        path.write_text(json.dumps(alive))


def _embedding_type(conn) -> str:
    """'vector' (pgvector) or 'real[]' (plain array) for chunk_embeddings.embedding."""
    return conn.execute(
        "SELECT format_type(atttypid, atttypmod) AS t FROM pg_attribute "
        "WHERE attrelid = 'chunk_embeddings'::regclass AND attname = 'embedding'").fetchone()["t"]


def _log_tail(path: Path, lines: int = 8) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError as e:
        return f"(log not readable: {e})"


class Database:
    def __init__(self, database_url: str, data_dir: Path, vector_store: str = "pgvector"):
        """vector_store "numpy" or "chroma": the database needs no pgvector extension (embeddings are a plain real[])."""
        self._server = None
        self.pgvector = vector_store not in ("numpy", "chroma")
        if not database_url:
            self._server, database_url = _start_private_server(data_dir / "pg")
        self.url = database_url
        self._migrate()
        self.pool = ConnectionPool(
            self.url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=register_vector if self.pgvector else None,
            open=True,
        )

    def _migrate(self) -> None:
        """Create the vector extension (pgvector mode; needed before register_vector) and apply pending migrations.
        One copy at a time (STARTUP_LOCK): otherwise two copies on an empty database both create the tables."""
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (STARTUP_LOCK,))  # released when this connection closes
            if self.pgvector:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY, applied_at timestamptz DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations").fetchall()}
            for path in sorted(MIGRATIONS.glob("*.sql")):
                if path.name in done:
                    continue
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
                log(logger, "migration applied", name=path.name)

    def ensure_vector_index(self, model: str, dim: int) -> None:
        """Partial HNSW index for one embedding model. Search uses the same cast and WHERE clause."""
        safe = "".join(c if c.isalnum() else "_" for c in model.lower())[:40]
        with self.startup_lock() as conn:  # two copies creating the same index at once: one would fail
            if _embedding_type(conn) != "vector":  # created by 001_init.sql, or used before with VECTOR_STORE=chroma
                conn.execute("ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE vector USING embedding::vector")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS emb_hnsw_{safe}_{dim} ON chunk_embeddings "
                f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE model = '{model}'"
            )

    def ensure_plain_embeddings(self) -> None:
        """VECTOR_STORE=chroma: embeddings as a plain real[] column. A database used before with pgvector is converted
        (its HNSW indexes dropped first); that needs the extension, which such a database has."""
        with self.startup_lock() as conn:
            if _embedding_type(conn) == "vector":
                for r in conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'chunk_embeddings' "
                                      "AND indexname LIKE 'emb_hnsw_%'").fetchall():
                    conn.execute(f'DROP INDEX IF EXISTS "{r["indexname"]}"')
                conn.execute("ALTER TABLE chunk_embeddings ALTER COLUMN embedding TYPE real[] USING embedding::real[]")
                log(logger, "embeddings column converted to real[]")

    @contextmanager
    def startup_lock(self):
        """Hold STARTUP_LOCK on a pooled connection while one-time startup work runs (yields that connection)."""
        with self.pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (STARTUP_LOCK,))
            try:
                yield conn
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (STARTUP_LOCK,))

    def connection(self):
        return self.pool.connection()

    def close(self) -> None:
        self.pool.close()
        if self._server is not None:
            self._server.cleanup()
