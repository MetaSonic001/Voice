"""Health, readiness, metrics, a quick stats view and the speed / quality benchmark (/bench)."""

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.deps import container
from app.container import Container
from app.services.benchmark import Benchmarks

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
    # Reported, but not part of "ready": if search is down, bank questions and voice still work and KB questions
    # get the safe "system problem" reply. Failing readiness would take the whole brain out of the load balancer.
    checks["search"] = c.embedder.client.ping() if hasattr(c.embedder, "client") else "local"
    checks["llm"] = c.settings.llm_provider
    checks["voice"] = f"{c.voice.stt.name} + {c.voice.tts.name}" if c.voice else "disabled"
    checks["pending_jobs"] = c.worker.pending()
    # Reported, but not part of "ready": a full process must keep its live calls. On ECS a failing health check
    # gets the task replaced, which would cut those calls off. New calls over the limit are refused instead.
    from app.api.voice import live_calls

    limit = c.settings.max_live_calls
    checks["live_calls"] = f"{live_calls()}/{limit}" if limit else str(live_calls())
    ok = checks["database"] == "ok"
    response.status_code = 200 if ok else 503
    return {"status": "ready" if ok else "not_ready", "checks": checks}


@router.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@router.get("/v1/stats")
def stats(hours: int = 24, c: Container = Depends(container)):
    return c.traces.stats(hours)


class PinBody(BaseModel):
    setup_id: str | None = Field(None, max_length=20)  # default: the setup this brain runs with now
    label: str = Field("", max_length=80)


@router.get("/v1/bench")
def bench(c: Container = Depends(container)):
    """The running setup's speed and quality against the benchmark to beat (pinned, or the last other setup with
    enough real turns). Only real turns count: eval replay-cache hits are left out."""
    return Benchmarks(c.db).compare(c.conversation.setup_id)


@router.post("/v1/bench/baseline")
def pin_baseline(body: PinBody, c: Container = Depends(container)):
    """Keep a setup as the benchmark: its numbers are frozen now and stay until replaced."""
    pinned = Benchmarks(c.db).pin(body.setup_id or c.conversation.setup_id, body.label)
    if pinned is None:
        raise HTTPException(404, "no real turns recorded for that setup yet")
    return pinned


@router.delete("/v1/bench/baseline")
def unpin_baseline(c: Container = Depends(container)):
    """Back to the automatic benchmark (the last other setup with enough turns)."""
    Benchmarks(c.db).unpin()
    return {"baseline_from": "auto"}
