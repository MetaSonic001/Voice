"""Speed and quality per setup, compared with a benchmark to beat (the /bench page, 23 Nov 2026).

A "setup" is everything that changes speed or quality: the LLM provider, models and reasoning effort, speech
providers, search runtime and vector store, and the prompt version. Every turn records its setup (`data.setup_id`), so
turns are grouped by setup, never by time window. A change of setup starts a new group; nothing moves turn by turn.

The benchmark is either pinned by a person ("keep as benchmark": its numbers are frozen at that moment and stay until
replaced) or, when nothing is pinned, the most recent other setup with enough real turns. Turns answered from the eval
replay cache are left out: their timings are not real.

Quality here is what the record already holds, nothing new is invented: outcomes, grounding (level and the top
passage's reranker score, the closest thing to an answer's confidence), figure-check refusals, testers' thumbs, the
post-call judge's score, the share of prompt tokens served from the provider's cache, and the latest smoke / golden
run for that setup.
"""

import hashlib
import json

from psycopg.types.json import Jsonb

MIN_TURNS = 20  # fewer real turns than this: shown, but marked as too few to compare

# (key, where it is in traces.data, label, lower is better)
LATENCY = [
    ("voice_to_voice_ms", "voice", "Caller stops → first audio (voice)", True),
    ("first_reply_ms", "timings", "Turn start → first sentence ready", True),
    ("understand_ms", "timings", "Understanding (LLM)", True),
    ("llm_ttft_ms", "timings", "Answer LLM: first token", True),
    ("embed_ms", "timings", "Search: embedding", True),
    ("rerank_ms", "timings", "Search: reranking", True),
    ("bank_ms", "timings", "Bank data", True),
    ("stt_ms", "timings", "Speech to text", True),
    ("endpoint_ms", "timings", "End-of-speech wait", True),
    ("total_ms", "timings", "Brain time per turn", True),
]
# (key, label, lower is better; None = neither is better)
QUALITY = [
    ("smoke_pass_pct", "Smoke set passed (latest run)", False),
    ("golden_pass_pct", "Golden set passed (latest run)", False),
    ("regression_pass_pct", "Regression pack passed (latest run)", False),
    ("judge_score", "Post-call judge score (1-5)", False),
    ("grounded_high_pct", "Answers with high grounding", False),
    ("kb_confidence", "KB answer confidence (top passage score, 0-1)", False),
    ("figure_check_fail_pct", "KB answers refused by the figure check", True),
    ("error_pct", "Turns that failed (system error)", True),
    ("refused_pct", "Turns refused or handed over for lack of an answer", None),
    ("thumbs_up_pct", "Testers' thumbs up", False),
    ("cache_hit_pct", "Understanding prompt served from cache", False),
    ("understand_out_tokens", "Understanding output tokens (incl. reasoning)", True),
]

_REAL = ("coalesce(t.data->'understand_llm'->>'cached', 'false') <> 'true' "
         "AND coalesce(t.data->'answer_llm'->>'cached', 'false') <> 'true'")


def setup_of(settings, prompt_version: str) -> tuple[str, dict]:
    """The setup this brain runs with, and a short id for it. Effort is the level asked for; on Groq's gpt-oss "none"
    is sent as low (adapters/llm.py), so it is written as it is sent."""
    groq = settings.llm_provider != "bedrock"

    def effort(level: str) -> str:
        level = (level or "").strip().lower() or "model default"
        return "low" if groq and level not in ("low", "medium", "high") else level

    setup = {
        "llm_provider": settings.llm_provider,
        "understand_model": settings.understand_model, "understand_effort": effort(settings.understand_reasoning_effort),
        "answer_model": settings.answer_model, "answer_effort": effort(settings.answer_reasoning_effort),
        "stt": settings.stt_provider + (f" {settings.sarvam_stt_model}" if settings.stt_provider == "sarvam" else ""),
        "tts": settings.tts_provider + (f" {settings.sarvam_tts_model}" if settings.tts_provider == "sarvam" else ""),
        "search": f"{settings.search_provider} {settings.search_runtime} {settings.device}",
        "vector_store": settings.vector_store,
        "prompt_version": prompt_version,
    }
    if not settings.rerank:  # only when off, so every setup recorded before the switch keeps its id
        setup["rerank"] = "off"
    sid = hashlib.sha256(json.dumps(setup, sort_keys=True).encode()).hexdigest()[:10]
    return sid, setup


def label_of(setup: dict) -> str:
    """"in.openai.gpt-5.6-luna (low / low), sarvam"" - short enough for a card title."""
    same = setup.get("understand_model") == setup.get("answer_model")
    models = setup.get("answer_model", "?") if same else f"{setup.get('understand_model')} + {setup.get('answer_model')}"
    return f"{models} ({setup.get('understand_effort')} / {setup.get('answer_effort')}), {str(setup.get('tts', '')).split()[0]}"


class Benchmarks:
    def __init__(self, db):
        self.db = db

    # ───────── numbers for one setup ─────────

    def summary(self, setup_id: str) -> dict | None:
        with self.db.connection() as conn:
            head = conn.execute(
                f"SELECT count(*) AS n, min(created_at) AS first_at, max(created_at) AS last_at, "
                f"(array_agg(t.data->'setup' ORDER BY created_at DESC))[1] AS setup FROM traces t "
                f"WHERE t.data->>'setup_id' = %s AND {_REAL}", (setup_id,)).fetchone()
            if not head or not head["n"]:
                return None
            latency = {}
            for key, where, _, _ in LATENCY:
                r = conn.execute(
                    f"SELECT count(v) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY v) AS p50, "
                    f"percentile_cont(0.95) WITHIN GROUP (ORDER BY v) AS p95 FROM "
                    f"(SELECT (t.data->'{where}'->>'{key}')::numeric AS v FROM traces t "
                    f"WHERE t.data->>'setup_id' = %s AND {_REAL}) x WHERE v IS NOT NULL", (setup_id,)).fetchone()
                if r["n"]:
                    latency[key] = {"n": r["n"], "p50": round(float(r["p50"])), "p95": round(float(r["p95"]))}
            q = conn.execute(
                f"SELECT count(*) AS n, "
                f"100.0 * count(*) FILTER (WHERE outcome = 'error') / count(*) AS error_pct, "
                f"100.0 * count(*) FILTER (WHERE outcome = 'refused') / count(*) AS refused_pct, "
                f"100.0 * count(*) FILTER (WHERE t.data->'grounding'->>'level' = 'high') / "
                f"  nullif(count(*) FILTER (WHERE t.data->'grounding'->>'level' IN ('high', 'medium', 'low')), 0) AS grounded_high_pct, "
                f"avg((t.data->'grounding'->>'score')::numeric) AS kb_confidence, "
                f"100.0 * count(*) FILTER (WHERE t.data->>'reason' LIKE '%%figure_check%%') / "
                f"  nullif(count(*) FILTER (WHERE t.data ? 'answer_llm'), 0) AS figure_check_fail_pct, "
                f"100.0 * count(*) FILTER (WHERE t.data->'feedback'->>'verdict' = 'correct') / "
                f"  nullif(count(*) FILTER (WHERE t.data ? 'feedback'), 0) AS thumbs_up_pct, "
                f"100.0 * sum(coalesce((t.data->'understand_llm'->>'cache_read_tokens')::numeric, 0)) / "
                f"  nullif(sum((t.data->'understand_llm'->>'prompt_tokens')::numeric), 0) AS cache_hit_pct, "
                f"avg((t.data->'understand_llm'->>'completion_tokens')::numeric) AS understand_out_tokens, "
                f"array_agg(DISTINCT t.session_id) FILTER (WHERE t.session_id IS NOT NULL) AS sessions "
                f"FROM traces t WHERE t.data->>'setup_id' = %s AND {_REAL}", (setup_id,)).fetchone()
            judge = conn.execute("SELECT avg(score) AS s, count(score) AS n FROM call_reviews WHERE session_id = ANY(%s::uuid[])",
                                 ([str(s) for s in q["sessions"] or []],)).fetchone()
            evals = {r["kind"]: r for r in conn.execute(
                "SELECT DISTINCT ON (kind) kind, created_at, metrics FROM (SELECT CASE WHEN golden_file LIKE 'smoke%%' THEN 'smoke' "
                "WHEN golden_file LIKE 'regression%%' THEN 'regression' ELSE 'golden' END AS kind, created_at, metrics "
                "FROM eval_runs WHERE status = 'done' AND config->>'setup_id' = %s) e ORDER BY kind, created_at DESC",
                (setup_id,)).fetchall()}

        def num(v, digits=1):
            return None if v is None else round(float(v), digits)

        quality = {
            "smoke_pass_pct": num(_pass(evals.get("smoke"))),
            "golden_pass_pct": num(_pass(evals.get("golden"))),
            "regression_pass_pct": num(_pass(evals.get("regression"))),
            "judge_score": num(judge["s"], 2), "grounded_high_pct": num(q["grounded_high_pct"]),
            "kb_confidence": num(q["kb_confidence"], 3), "figure_check_fail_pct": num(q["figure_check_fail_pct"]),
            "error_pct": num(q["error_pct"]), "refused_pct": num(q["refused_pct"]), "thumbs_up_pct": num(q["thumbs_up_pct"]),
            "cache_hit_pct": num(q["cache_hit_pct"]), "understand_out_tokens": num(q["understand_out_tokens"], 0),
        }
        setup = head["setup"] or {}
        return {"setup_id": setup_id, "setup": setup, "label": label_of(setup), "turns": head["n"],
                "first_at": head["first_at"], "last_at": head["last_at"], "enough": head["n"] >= MIN_TURNS,
                "judged_calls": judge["n"], "latency": latency, "quality": quality,
                "evals": {k: {"at": v["created_at"], "pass_rate": _pass(v)} for k, v in evals.items()}}

    def setups(self, limit: int = 10) -> list[dict]:
        """The most recent setups that have real turns, newest first."""
        with self.db.connection() as conn:
            rows = conn.execute(
                f"SELECT t.data->>'setup_id' AS id, count(*) AS n, max(created_at) AS last_at FROM traces t "
                f"WHERE t.data ? 'setup_id' AND {_REAL} GROUP BY 1 ORDER BY last_at DESC LIMIT %s", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # ───────── the benchmark to beat ─────────

    def pinned(self) -> dict | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT setup_id, label, snapshot, pinned_at FROM benchmark_baseline WHERE id = 1").fetchone()
        return dict(row) if row else None

    def pin(self, setup_id: str, label: str = "") -> dict | None:
        """Freeze this setup's numbers as the benchmark (they no longer change as more turns come in)."""
        snapshot = self.summary(setup_id)
        if snapshot is None:
            return None
        label = (label or "").strip()[:80] or snapshot["label"]
        with self.db.connection() as conn:
            conn.execute(
                "INSERT INTO benchmark_baseline (id, setup_id, label, snapshot) VALUES (1, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET setup_id = excluded.setup_id, label = excluded.label, "
                "snapshot = excluded.snapshot, pinned_at = now()", (setup_id, label, Jsonb(json.loads(json.dumps(snapshot, default=str)))))
        return self.pinned()

    def unpin(self) -> None:
        with self.db.connection() as conn:
            conn.execute("DELETE FROM benchmark_baseline WHERE id = 1")

    def compare(self, current_id: str) -> dict:
        """The running setup against the benchmark, metric by metric."""
        current = self.summary(current_id)
        pin = self.pinned()
        if pin:
            baseline, how = pin["snapshot"] | {"label": pin["label"], "pinned_at": pin["pinned_at"]}, "pinned"
        else:
            baseline, how = None, "auto"
            for s in self.setups(limit=20):
                if s["id"] != current_id and s["n"] >= MIN_TURNS:
                    baseline = self.summary(s["id"])
                    break
        same = bool(baseline and baseline.get("setup_id") == current_id)
        rows = []
        for key, _, label, lower in LATENCY:
            rows.append(_row("latency", key, label, lower, (current or {}).get("latency", {}).get(key),
                             (baseline or {}).get("latency", {}).get(key)))
        for key, label, lower in QUALITY:
            rows.append(_row("quality", key, label, lower, (current or {}).get("quality", {}).get(key),
                             (baseline or {}).get("quality", {}).get(key)))
        return {"current_setup_id": current_id, "current": current, "baseline": baseline, "baseline_from": how,
                "baseline_is_current": same, "min_turns": MIN_TURNS, "rows": rows,
                "others": [s for s in self.setups() if s["id"] not in (current_id, (baseline or {}).get("setup_id"))]}


def _pass(run: dict | None):
    """A run's pass rate: smoke / regression keep it at the top, the golden runner under "overall"."""
    m = (run or {}).get("metrics") or {}
    return m.get("pass_rate") if m.get("pass_rate") is not None else (m.get("overall") or {}).get("pass_rate")


def _row(group: str, key: str, label: str, lower: bool | None, cur, base) -> dict:
    """One line of the comparison. Latency compares p50 (p95 shown too); `better` is True / False / None."""
    c = cur.get("p50") if isinstance(cur, dict) else cur
    b = base.get("p50") if isinstance(base, dict) else base
    delta = None if c is None or b is None else round(c - b, 3)
    pct = None if delta is None or not b else round(100 * delta / b, 1)
    better = None if delta is None or lower is None or delta == 0 else (delta < 0) == lower
    return {"group": group, "key": key, "label": label, "lower_is_better": lower, "current": cur, "baseline": base,
            "delta": delta, "delta_pct": pct, "better": better}
