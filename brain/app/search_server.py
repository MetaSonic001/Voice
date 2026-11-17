"""The GPU search server: the embedder (bge-m3) and the reranker (bge-reranker-v2-m3) behind a small HTTP API.

    python -m app search [--host 0.0.0.0] [--port 8200]

The brain uses it when SEARCH_PROVIDER=http (adapters/search_http.py). With SEARCH_PROVIDER=local (the default)
the brain loads the same two models itself and this server is not needed.

It runs the very same model classes as the in-process mode (adapters/models.py), so vectors and scores are identical.
It never logs the texts it receives (questions and KB passages). Every /v1 route needs `Authorization: Bearer
<SEARCH_TOKEN>`; /health is open for the load balancer. It refuses to start without a token unless it only listens
on this machine (127.0.0.1).

Endpoints:
  GET  /health      {"status": "ok"}
  GET  /v1/info     {"embed_model", "dim", "rerank_model", "device"}
  POST /v1/embed    {"texts": [...]}              -> {"vectors": [[...], ...]}
  POST /v1/rerank   {"query": "...", "passages": [...]} -> {"scores": [...]}
"""

import hmac
import logging
import time

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import ROOT, Settings, get_settings
from app.logs import log, setup_logging
from app.ports import Embedder, Reranker

logger = logging.getLogger("app.search")

MAX_TEXTS = 256  # per /v1/embed request (the brain sends 64 per batch while indexing)
MAX_PASSAGES = 64  # per /v1/rerank request (the brain sends RERANK_CANDIDATES, 16 by default)


class EmbedIn(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=MAX_TEXTS)


class RerankIn(BaseModel):
    query: str
    passages: list[str] = Field(max_length=MAX_PASSAGES)


def create_app(embedder: Embedder, reranker: Reranker, token: str) -> FastAPI:
    app = FastAPI(title="AU SFB search server", version="0.1.0", docs_url=None, redoc_url=None)

    def check(authorization: str = Header(default="")) -> None:
        if token and not hmac.compare_digest(authorization.encode(), f"Bearer {token}".encode()):
            raise HTTPException(401, "missing or wrong search token")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # Plain `def` routes: FastAPI runs them in its thread pool, so several calls are served at once. Each model
    # has its own lock (per batch of 16), exactly as in-process: a live question waits for at most one batch of an
    # indexing job, not for the whole document.
    @app.get("/v1/info", dependencies=[Depends(check)])
    def info():
        return {"embed_model": embedder.name, "dim": embedder.dim, "rerank_model": reranker.name,
                "device": getattr(embedder, "device", "-")}

    @app.post("/v1/embed", dependencies=[Depends(check)])
    def embed(body: EmbedIn):
        t = time.perf_counter()
        vectors = embedder.embed(body.texts)
        log(logger, "embed", n=len(body.texts), ms=round((time.perf_counter() - t) * 1000))
        return {"vectors": vectors}

    @app.post("/v1/rerank", dependencies=[Depends(check)])
    def rerank(body: RerankIn):
        t = time.perf_counter()
        scores = reranker.score(body.query, body.passages) if body.passages else []
        log(logger, "rerank", n=len(body.passages), ms=round((time.perf_counter() - t) * 1000))
        return {"scores": scores}

    return app


def from_settings(s: Settings | None = None) -> FastAPI:
    """Load the two models (as the brain does in local mode) and build the app. Used by `python -m app search`."""
    s = s or get_settings()
    setup_logging(s.log_level, s.log_dir, s.log_console)
    from app.adapters.models import CrossEncoderReranker, TransformerEmbedder

    t = time.perf_counter()
    models_dir = ROOT / "models"
    embedder = TransformerEmbedder(s.embed_model, s.embed_dim, s.device, models_dir=models_dir)
    reranker = CrossEncoderReranker(s.rerank_model, s.device, s.rerank_max_tokens, models_dir=models_dir)
    embedder.embed_query("warm up")  # first call is slow; pay it now, not on the first question
    reranker.score("warm up", ["warm up"])
    log(logger, "search server ready", device=embedder.device, startup_ms=round((time.perf_counter() - t) * 1000))
    return create_app(embedder, reranker, s.search_token.get_secret_value())


def check_bind(host: str, token: str) -> None:
    if not token and host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit("SEARCH_TOKEN is required when the search server listens beyond 127.0.0.1")
