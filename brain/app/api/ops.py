"""Health, readiness, metrics and a quick stats view."""

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import container
from app.container import Container

router = APIRouter(tags=["ops"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(response: Response, c: Container = Depends(container)):
    checks = {}
    try:
        with c.db.connection() as conn:
            conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:  # report, don't raise: this endpoint must always answer
        checks["database"] = f"error: {e}"
    checks["embedder"] = c.embedder.name
    checks["reranker"] = c.reranker.name
    checks["llm"] = c.settings.llm_provider
    checks["voice"] = f"{c.voice.stt.name} + {c.voice.tts.name}" if c.voice else "disabled"
    checks["pending_jobs"] = c.worker.pending()
    ok = checks["database"] == "ok"
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


@router.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/stats")
def stats(hours: int = 24, c: Container = Depends(container)):
    return c.traces.stats(hours)
