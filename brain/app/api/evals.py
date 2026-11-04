"""Evaluation endpoints: start a run (in the background), read results, compare runs."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import container, require_key
from app.container import Container

router = APIRouter(prefix="/v1/evals", tags=["evals"])
_running: set[asyncio.Task] = set()


class EvalBody(BaseModel):
    golden: str = Field("v1", description="File name in evals/golden without .yaml")
    only: list[str] | None = Field(None, description="Run only these item ids")


@router.post("/run", status_code=202, dependencies=[Depends(require_key)])
async def run_eval(body: EvalBody | None = None, c: Container = Depends(container)):
    body = body or EvalBody()
    try:
        run_id = c.evals.start(body.golden)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    task = asyncio.create_task(c.evals.run(run_id, body.golden, body.only))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return {"run_id": run_id, "status": "running", "poll": f"/v1/evals/{run_id}"}


@router.get("")
def list_runs(limit: int = 20, c: Container = Depends(container)):
    return c.evals.runs(limit)


@router.get("/compare")
def compare(a: str, b: str, c: Container = Depends(container)):
    ra, rb = c.evals.get(a), c.evals.get(b)
    if not ra or not rb or not ra["metrics"] or not rb["metrics"]:
        raise HTTPException(404, "both runs must exist and be finished")

    def flat(m):
        o = m["overall"]
        return {**{k: o[k] for k in ("pass_rate", "outcome_accuracy", "fact_accuracy", "hit@1", "hit@5", "mrr")},
                "answered_p50_ms": m["answered_total_ms"]["p50"], "answered_p95_ms": m["answered_total_ms"]["p95"],
                "cost_usd_per_question": m["cost_usd"]["per_question"]}

    fa, fb = flat(ra["metrics"]), flat(rb["metrics"])
    changed = {r["id"]: (r["passed"], s["passed"]) for r in ra["results"] for s in rb["results"]
               if r["id"] == s["id"] and r["passed"] != s["passed"]}
    return {"a": {"id": a, "config": ra["config"], "metrics": fa}, "b": {"id": b, "config": rb["config"], "metrics": fb},
            "fixed_in_b": [k for k, (x, y) in changed.items() if not x and y],
            "broken_in_b": [k for k, (x, y) in changed.items() if x and not y]}


@router.get("/{run_id}")
def get_run(run_id: str, c: Container = Depends(container)):
    run = c.evals.get(run_id)
    if not run:
        raise HTTPException(404, "eval run not found")
    return run


@router.get("/{run_id}/report", response_class=PlainTextResponse)
def get_report(run_id: str, c: Container = Depends(container)):
    run = c.evals.get(run_id)
    if not run or not run["report_path"] or not Path(run["report_path"]).exists():
        raise HTTPException(404, "report not ready")
    return Path(run["report_path"]).read_text(encoding="utf-8")
