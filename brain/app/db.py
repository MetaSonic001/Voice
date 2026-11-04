"""Postgres access: a private server under data/pg (pgserver, no Docker) or an external DATABASE_URL.

Synchronous psycopg with a small pool. Async code calls it through `asyncio.to_thread`.
"""

import json
import logging
import time
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.logs import log

logger = logging.getLogger(__name__)
MIGRATIONS = Path(__file__).parent / "migrations"


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


def _log_tail(path: Path, lines: int = 8) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError as e:
        return f"(log not readable: {e})"


class Database:
    def __init__(self, database_url: str, data_dir: Path):
        self._server = None
        if not database_url:
            self._server, database_url = _start_private_server(data_dir / "pg")
        self.url = database_url
        self._migrate()
        self.pool = ConnectionPool(
            self.url,
            min_size=2,
            max_size=10,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=register_vector,
            open=True,
        )

    def _migrate(self) -> None:
        """Create the vector extension (needed before register_vector) and apply pending migrations."""
        with psycopg.connect(self.url, autocommit=True) as conn:
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
        with self.pool.connection() as conn:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS emb_hnsw_{safe}_{dim} ON chunk_embeddings "
                f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE model = '{model}'"
            )

    def connection(self):
        return self.pool.connection()

    def close(self) -> None:
        self.pool.close()
        if self._server is not None:
            self._server.cleanup()
