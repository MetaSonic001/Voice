"""T2 smoke run (PHASE-4-PLAN §10): the smoke set (24 items) on the real LLM, every response cached (replay), every check deterministic.

  python -m evals.smoke [--only id ...] [--fresh]      (stop nothing: it shares the running brain's database)
  python -m evals.smoke --pack regression [--keep] [--only id ...]
      another pack (evals/<pack>.yaml); --keep leaves its calls on /calls for review (smoke runs delete theirs).
      An item with `llm_down: true` runs with the LLM failing (429), to check the code-only paths.
      Stops at Groq's daily limit (switch the key in brain/.env, then run again with --only the items not done).

Writes evals/reports/smoke-<time>.md/.json: pass rate per failure class, the misses, understanding and turn latency, and how many
real LLM requests were made (cache misses). A second run with nothing changed makes 0 requests.
"""

import asyncio
import json
import re
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from app.config import Settings
from app.container import build
from app.main import create_app

HERE = Path(__file__).parent

TPM = 7600  # stay under Groq's 8k tokens/minute (it counts the prompt plus the reserved max tokens)


class Pacer:
    """Keeps each model under TPM tokens in any 60 s window, from the token counts the API reports."""

    def __init__(self):
        self.used: dict[str, deque] = defaultdict(deque)

    async def wait(self, model: str, need: int = 3000):
        q = self.used[model]
        while True:
            now = time.monotonic()
            while q and now - q[0][0] > 60:
                q.popleft()
            if sum(t for _, t in q) + need <= TPM:
                return
            await asyncio.sleep(max(0.5, 60 - (now - q[0][0])))

    def add(self, model: str, meta: dict, reserved: int = 600):
        self.used[model].append((time.monotonic(), (meta.get("prompt_tokens") or 0) + reserved))


class Patient:
    """Wraps the LLM: on a rate-limit (429) waits as long as Groq asks and tries again, so a benchmark run never
    records a rate-limit as a wrong answer. Latency is measured per call, so the waiting doesn't distort it."""

    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    async def _wait(error: Exception, attempt: int) -> None:
        text = str(error)
        # A daily limit (TPD / RPD) is not waited out: the run stops and the owner switches the key
        if "429" not in text or attempt == 9 or "per day" in text:
            raise error
        m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", text)  # "11.8s" or "22m5.3s"
        await asyncio.sleep((int(m.group(1) or 0) * 60 + float(m.group(2)) + 1) if m else 15)

    async def json(self, *args, **kwargs):
        for attempt in range(10):
            try:
                return await self.llm.json(*args, **kwargs)
            except Exception as e:  # noqa: PERF203
                await self._wait(e, attempt)

    async def stream(self, model, messages, meta, max_tokens=400, effort=None):
        for attempt in range(10):
            started = False
            try:
                async for delta in self.llm.stream(model, messages, meta, max_tokens=max_tokens, effort=effort):
                    started = True
                    yield delta
                return
            except Exception as e:
                if started:
                    raise
                await self._wait(e, attempt)

    async def close(self):
        await self.llm.close()


def _pct(values) -> float | None:
    return round(100 * sum(values) / len(values), 1) if values else None


def _p(values, q):
    values = sorted(v for v in values if v is not None)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None



class Down:
    """The LLM failing as Groq does at its rate limit, for `llm_down` items."""

    async def json(self, *args, **kwargs):
        raise RuntimeError("LLM HTTP 429: rate limit (simulated by the eval)")

    async def stream(self, *args, **kwargs):
        raise RuntimeError("LLM HTTP 429: rate limit (simulated by the eval)")
        yield ""  # makes this an async generator

    async def close(self):
        pass


def _requests(trace: dict) -> list[str]:
    """Each understood request as "kind:topic/detail filters", for the report."""
    out = []
    for r in ((trace.get("data") or {}).get("understanding") or {}).get("requests") or []:
        p = r.get("personal") or {}
        filters = " ".join(f"{k}={p[k]}" for k in ("account", "period", "keyword", "direction", "count", "total", "item")
                           if p.get(k) and p.get(k) != "any")
        out.append(f"{r['kind']}:{p.get('topic', '')}/{p.get('detail', '')} {filters}".strip())
    return out


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



def _forget(client, sessions: list[str]) -> None:
    """A test run is not a call: its sessions (turns, handoffs, alerts) are deleted, so /calls lists real calls only.
    The results stay in the report."""
    for sid in sessions:
        client.delete(f"/api/v1/calls/{sid}")


def _ravi_cards_active(conn) -> None:
    conn.execute("UPDATE corebank.credit_cards SET status = 'active' WHERE cif = 'CIF900101'")
    conn.execute("UPDATE corebank.debit_cards SET status = 'active' WHERE account_number IN "
                 "(SELECT number FROM corebank.accounts WHERE cif = 'CIF900101')")


async def run(only: list[str] | None, pack: str = "smoke", keep: bool = False) -> dict:
    s = Settings(llm_replay_dir=HERE / "cache", judge_mode="off")
    c = build(s, start_worker=False)
    replay = c.llm  # the cache sits outermost: a hit never reaches the pacer or the network
    inner = c.llm.inner
    c.llm.inner = Patient(inner)  # a real 429 waits as long as Groq asks, and is never recorded as a wrong answer
    pacer = Pacer()
    items = [i for i in yaml.safe_load((HERE / f"{pack}.yaml").read_text(encoding="utf-8")) if not only or i["id"] in only]
    patient, stopped = c.llm.inner, ""
    rows, sessions = [], []
    started = time.perf_counter()
    with TestClient(create_app(c)) as client:
        for item in items:
            if stopped:
                break
            with c.db.connection() as conn:  # each item starts with the demo cards as seeded (a block in one doesn't leak)
                conn.execute("UPDATE corebank.credit_cards SET status = 'active' WHERE cif LIKE 'CIF9001%%' AND status = 'temp_blocked'")
                _ravi_cards_active(conn)  # Ravi's cards are all active when seeded; a test may block one for good
            c.llm.inner = Down() if item.get("llm_down") else patient
            sid = client.post("/api/v1/sessions", json={"caller_number": item["caller"]} if item["caller"] else {}).json()["id"]
            sessions.append(sid)
            steps, ok_all = [], True
            for turn in item["turns"]:
                misses = replay.misses
                r = client.post("/api/v1/ask", json={"question": turn["say"], "session_id": sid}).json()
                if r.get("outcome") == "auth_required" and (r.get("auth") or {}).get("simulated_sms"):
                    code = re.search(r"(\d{6})", r["auth"]["simulated_sms"]).group(1)
                    r = client.post("/api/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"] or {}
                trace = client.get(f"/api/v1/traces/{r['trace_id']}").json() if r.get("trace_id") else {}
                checks = check(turn, r, trace)
                error = str((trace.get("data") or {}).get("error") or "")
                if "per day" in error:  # Groq's daily limit: stop here, keep what ran
                    stopped = f"{item['id']}: {error[:200]}"
                    print(f"GROQ DAILY LIMIT at {item['id']} - switch the key and run again with --only the rest", flush=True)
                    break
                if replay.misses > misses:  # real requests were made: keep under Groq's tokens-per-minute limit
                    pacer.add(s.understand_model, {"prompt_tokens": 2600})
                    await pacer.wait(s.understand_model, 3300)
                ok = all(checks.values())
                ok_all &= ok
                t = r.get("timings") or {}
                steps.append({"say": turn["say"], "outcome": r.get("outcome"), "ok": ok, "checks": checks, "answer": r.get("answer"),
                              "total_ms": t.get("total_ms"), "understand_ms": t.get("understand_ms"),
                              "kinds": [x["kind"] for x in ((trace.get("data") or {}).get("understanding") or {}).get("requests") or []],
                              "requests": _requests(trace)})
                print(f"{item['id']:34} {'ok  ' if ok else 'MISS'} {r.get('outcome')} {[k for k, v in checks.items() if not v] or ''}", flush=True)
            if not stopped:
                rows.append({"id": item["id"], "cls": item["cls"], "pass": ok_all, "steps": steps, "session": sid})
        if not keep:
            _forget(client, sessions)
    with c.db.connection() as conn:  # put the demo cards back
        conn.execute("UPDATE corebank.credit_cards SET status = 'active' WHERE cif LIKE 'CIF9001%%' AND status <> 'blocked'")
        _ravi_cards_active(conn)
    steps = [x for r in rows for x in r["steps"]]
    by_class: dict[str, list] = {}
    for r in rows:
        by_class.setdefault(r["cls"], []).append(r["pass"])
    out = {"pack": pack, "stopped": stopped, "when": time.strftime("%Y-%m-%d %H:%M"), "models": [s.understand_model, s.answer_model], "items": rows,
           "pass_rate": _pct([r["pass"] for r in rows]), "by_class": {k: f"{sum(v)}/{len(v)}" for k, v in by_class.items()},
           "llm_requests_made": replay.misses, "cache_hits": replay.hits, "minutes": round((time.perf_counter() - started) / 60, 1),
           "turn_ms_p50": _p([x["total_ms"] for x in steps], 0.5), "turn_ms_p95": _p([x["total_ms"] for x in steps], 0.95),
           "understand_ms_p50": _p([x["understand_ms"] for x in steps], 0.5), "understand_ms_p95": _p([x["understand_ms"] for x in steps], 0.95)}
    if not only:  # a whole run (not --only a few items) is the setup's accuracy on the /bench page
        import uuid

        with c.db.connection() as conn:
            conn.execute("INSERT INTO eval_runs (id, golden_file, config, status, metrics) VALUES (%s, %s, %s, 'done', %s)",
                         (str(uuid.uuid4()), pack, json.dumps({"setup_id": c.conversation.setup_id, "setup": c.conversation.setup}),
                          json.dumps({"pass_rate": out["pass_rate"], "by_class": out["by_class"], "items": len(rows),
                                      "stopped": bool(stopped), "llm_requests_made": replay.misses, "cache_hits": replay.hits})))
    try:
        await c.close()
    except RuntimeError:  # "Event loop is closed": the test client's loop already closed Groq's connection; the report still counts
        pass
    return out


def report(out: dict) -> str:
    lines = [f"# {out.get('pack', 'smoke').title()} run {out['when']}", "", f"Models: understand {out['models'][0]}, answer {out['models'][1]}. Items: {len(out['items'])}, "
             f"passed {out['pass_rate']}%. Real LLM requests: {out['llm_requests_made']} (cache hits {out['cache_hits']}). "
             f"Took {out['minutes']} min.", "", f"Brain time per turn p50 {out['turn_ms_p50']} ms, p95 {out['turn_ms_p95']} ms. "
             f"Understanding p50 {out['understand_ms_p50']} ms, p95 {out['understand_ms_p95']} ms.", "",
             "| Class | Passed |", "|---|---|"] + [f"| {k} | {v} |" for k, v in out["by_class"].items()] + ["", "## Misses", ""]
    for r in out["items"]:
        for st in r["steps"]:
            if not st["ok"]:
                lines.append(f"- `{r['id']}` ({r['cls']}) said \"{st['say']}\" -> {st['outcome']}, failed {[k for k, v in st['checks'].items() if not v]}, "
                             f"kinds {st['kinds']}: {(st['answer'] or '')[:240]}")
    if out.get("stopped"):
        lines += ["", f"**Stopped at Groq's daily limit** ({out['stopped']}); items after it did not run.", ""]
    if out.get("pack", "smoke") != "smoke":  # the whole conversation, for reading each reply
        lines += ["", "## Transcripts", ""]
        for r in out["items"]:
            lines.append(f"### {r['id']} ({r['cls']}) - {'pass' if r['pass'] else 'MISS'} - call {r.get('session', '')[:8]}")
            for st in r["steps"]:
                failed = [k for k, v in st["checks"].items() if not v]
                lines.append(f"- **Caller:** {st['say']}")
                lines.append(f"  - understood: {'; '.join(st.get('requests') or []) or '-'} | outcome {st['outcome']}"
                             + (f" | FAILED {failed}" if failed else ""))
                lines.append(f"  - **Agent:** {st['answer']}")
            lines.append("")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    only = sys.argv[sys.argv.index("--only") + 1:] if "--only" in sys.argv else None
    pack = sys.argv[sys.argv.index("--pack") + 1] if "--pack" in sys.argv else "smoke"
    result = asyncio.run(run(only, pack, keep="--keep" in sys.argv))
    reports = HERE / "reports"
    reports.mkdir(exist_ok=True)
    stem = reports / f"{pack}-{time.strftime('%Y%m%d-%H%M%S')}"
    stem.with_suffix(".json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    stem.with_suffix(".md").write_text(report(result), encoding="utf-8")
    print(f"report: {stem}.md")
