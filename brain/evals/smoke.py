"""T2 smoke run (PHASE-4-PLAN §10): the 40-item set on the real LLM, every response cached (replay), every check deterministic.

  python -m evals.smoke [--only id ...] [--fresh]      (stop nothing: it shares the running brain's database)

Writes evals/reports/smoke-<time>.md/.json: pass rate per failure class, the misses, understanding and turn latency, and how many
real LLM requests were made (cache misses). A second run with nothing changed makes 0 requests.
"""

import asyncio
import json
import re
import sys
import time
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build
from app.main import create_app
from evals.benchmark import Pacer, Patient, _p, _pct

HERE = Path(__file__).parent


def check(turn: dict, reply: dict, trace: dict) -> dict[str, bool]:
    text = (reply.get("answer") or "")
    low = text.lower()
    requests = ((trace.get("data") or {}).get("understanding") or {}).get("requests") or []
    kinds = [r["kind"] for r in requests]
    out = {"outcome": reply.get("outcome") in turn["outcome"]}
    if "has" in turn:
        out["has"] = all(h.lower() in low for h in turn["has"])
    if "has_any" in turn:
        out["has_any"] = any(h.lower() in low for h in turn["has_any"])
    if "lacks" in turn:
        out["lacks"] = not any(x.lower() in low for x in turn["lacks"])
    if "max_words" in turn:
        out["max_words"] = len(text.split()) <= turn["max_words"]
    if "first_kind" in turn:
        out["first_kind"] = (kinds[:1] == [turn["first_kind"]]) if turn["first_kind"] != "none" else not requests
    if "kinds" in turn:
        out["kinds"] = kinds == turn["kinds"]
    return out


async def run(only: list[str] | None) -> dict:
    s = Settings(llm_replay_dir=HERE / "cache", judge_mode="off")
    c = build(s, start_worker=False)
    replay = c.llm  # the cache sits outermost: a hit never reaches the pacer or the network
    inner = c.llm.inner
    c.llm.inner = Patient(inner)  # a real 429 waits as long as Groq asks, and is never recorded as a wrong answer
    pacer = Pacer()
    items = [i for i in yaml.safe_load((HERE / "smoke.yaml").read_text(encoding="utf-8")) if not only or i["id"] in only]
    rows, sessions = [], []
    started = time.perf_counter()
    with TestClient(create_app(c)) as client:
        for item in items:
            sid = client.post("/v1/sessions", json={"caller_number": item["caller"]} if item["caller"] else {}).json()["id"]
            sessions.append(sid)
            steps, ok_all = [], True
            for turn in item["turns"]:
                misses = replay.misses
                r = client.post("/v1/ask", json={"question": turn["say"], "session_id": sid}).json()
                if r.get("outcome") == "auth_required" and (r.get("auth") or {}).get("simulated_sms"):
                    code = re.search(r"(\d{6})", r["auth"]["simulated_sms"]).group(1)
                    r = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"] or {}
                trace = client.get(f"/v1/traces/{r['trace_id']}").json() if r.get("trace_id") else {}
                checks = check(turn, r, trace)
                if replay.misses > misses:  # real requests were made: keep under Groq's tokens-per-minute limit
                    pacer.add(s.understand_model, {"prompt_tokens": 2600})
                    await pacer.wait(s.understand_model, 3300)
                ok = all(checks.values())
                ok_all &= ok
                t = r.get("timings") or {}
                steps.append({"say": turn["say"], "outcome": r.get("outcome"), "ok": ok, "checks": checks, "answer": r.get("answer"),
                              "total_ms": t.get("total_ms"), "understand_ms": t.get("understand_ms"),
                              "kinds": [x["kind"] for x in ((trace.get("data") or {}).get("understanding") or {}).get("requests") or []]})
                print(f"{item['id']:34} {'ok  ' if ok else 'MISS'} {r.get('outcome')} {[k for k, v in checks.items() if not v] or ''}", flush=True)
            rows.append({"id": item["id"], "cls": item["cls"], "pass": ok_all, "steps": steps})
    with c.db.connection() as conn:  # put the demo cards back, close handoffs made here
        conn.execute("UPDATE corebank.credit_cards SET status = 'active' WHERE cif LIKE 'CIF9001%%' AND status <> 'blocked'")
        conn.execute("UPDATE handoffs SET status = 'completed', disposition = 'smoke test' WHERE session_id = ANY(%s::uuid[]) "
                     "AND status IN ('waiting', 'active')", (sessions,))
    steps = [x for r in rows for x in r["steps"]]
    by_class: dict[str, list] = {}
    for r in rows:
        by_class.setdefault(r["cls"], []).append(r["pass"])
    out = {"when": time.strftime("%Y-%m-%d %H:%M"), "models": [s.understand_model, s.answer_model], "items": rows,
           "pass_rate": _pct([r["pass"] for r in rows]), "by_class": {k: f"{sum(v)}/{len(v)}" for k, v in by_class.items()},
           "llm_requests_made": replay.misses, "cache_hits": replay.hits, "minutes": round((time.perf_counter() - started) / 60, 1),
           "turn_ms_p50": _p([x["total_ms"] for x in steps], 0.5), "turn_ms_p95": _p([x["total_ms"] for x in steps], 0.95),
           "understand_ms_p50": _p([x["understand_ms"] for x in steps], 0.5), "understand_ms_p95": _p([x["understand_ms"] for x in steps], 0.95)}
    await c.close()
    return out


def report(out: dict) -> str:
    lines = [f"# Smoke run {out['when']}", "", f"Models: understand {out['models'][0]}, answer {out['models'][1]}. Items: {len(out['items'])}, "
             f"passed {out['pass_rate']}%. Real LLM requests: {out['llm_requests_made']} (cache hits {out['cache_hits']}). "
             f"Took {out['minutes']} min.", "", f"Brain time per turn p50 {out['turn_ms_p50']} ms, p95 {out['turn_ms_p95']} ms. "
             f"Understanding p50 {out['understand_ms_p50']} ms, p95 {out['understand_ms_p95']} ms.", "",
             "| Class | Passed |", "|---|---|"] + [f"| {k} | {v} |" for k, v in out["by_class"].items()] + ["", "## Misses", ""]
    for r in out["items"]:
        for st in r["steps"]:
            if not st["ok"]:
                lines.append(f"- `{r['id']}` ({r['cls']}) said \"{st['say']}\" -> {st['outcome']}, failed {[k for k, v in st['checks'].items() if not v]}, "
                             f"kinds {st['kinds']}: {(st['answer'] or '')[:240]}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1:] if "--only" in sys.argv else None
    result = asyncio.run(run(only))
    reports = HERE / "reports"
    reports.mkdir(exist_ok=True)
    stem = reports / f"smoke-{time.strftime('%Y%m%d-%H%M%S')}"
    stem.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(f"report: {stem}.md")
