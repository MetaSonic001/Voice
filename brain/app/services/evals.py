"""Evaluation runner: golden questions -> real pipeline -> scores, latency, cost and a Markdown report.

Golden file (YAML list), one item per question:
  id: fd-senior-1y
  q: "I'm a senior citizen. Best FD rate for one year?"
  lang: en                      # en | hi | hinglish (for per-language breakdowns)
  expect: answer                # answer | clarify | refuse | verify | handoff | smalltalk | out_of_scope (or a list)
  facts: [["7.60", "7.6"]]      # answer must contain one alternative from EVERY group (numbers compared without commas)
  sources: ["Senior Citizen"]   # a top-ranked chunk should contain one of these (retrieval check)
  history: ["earlier question"] # optional: asked first in the same session (follow-up tests)
"""

import json
import logging
import re
import statistics
import time
import uuid
from pathlib import Path

import yaml

from app.db import Database
from app.logs import log
from app.services.conversation import Conversation
from app.services.store import TraceStore

logger = logging.getLogger(__name__)

EXPECT_TO_OUTCOME = {"answer": "answered", "clarify": "clarify", "refuse": "refused", "verify": "needs_verification",
                     "handoff": "handoff", "smalltalk": "smalltalk", "out_of_scope": "out_of_scope"}
# USD per 1M tokens (input, output) - Groq list prices; unknown models are reported without cost.
PRICES = {"openai/gpt-oss-20b": (0.075, 0.30), "openai/gpt-oss-120b": (0.15, 0.60), "llama-3.1-8b-instant": (0.05, 0.08),
          "llama-3.3-70b-versatile": (0.59, 0.79)}
_DEV = str.maketrans("०१२३४५६७८९", "0123456789")


def _norm(text: str) -> str:
    text = re.sub(r"[\u00a0\u2007\u2009\u202f]", " ", text.translate(_DEV)).lower().replace(",", "").replace("₹", "rs ")
    text = text.replace("‑", "-").replace("लाख", "lakh").replace("करोड़", "crore")
    return re.sub(r"(\d)\s+%", r"\1%", text)


def load_golden(path: Path) -> list[dict]:
    items = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = [i["id"] for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate ids in golden file")
    return items


def _cost(meta: dict | None) -> float | None:
    if not meta or meta.get("model") not in PRICES:
        return None
    pin, pout = PRICES[meta["model"]]
    return (meta.get("prompt_tokens", 0) * pin + meta.get("completion_tokens", 0) * pout) / 1e6


def score_item(item: dict, reply, trace_data: dict) -> dict:
    expected = item["expect"] if isinstance(item["expect"], list) else [item["expect"]]
    allowed = {EXPECT_TO_OUTCOME[e] for e in expected}
    if "handoff" in allowed:  # in a call the agent first asks "shall I connect you?"
        allowed.add("confirm")
    outcome_ok = reply.outcome in allowed
    answer = _norm(reply.answer)
    facts_ok = None
    if item.get("facts") and reply.outcome == "answered":
        facts_ok = all(any(_norm(str(alt)) in answer for alt in group) for group in item["facts"])
    rank = fused_rank = None
    if item.get("sources"):
        wanted = [s.lower() for s in item["sources"]]
        retrieval = trace_data.get("retrieval") or {}

        def first_rank(rows):
            for i, row in enumerate(rows, start=1):
                hay = f"{row.get('section', '')} {row.get('text') or row.get('snippet', '')}".lower()
                if any(w in hay for w in wanted):
                    return i
            return None

        rank = first_rank(retrieval.get("reranked") or [])
        fused_rank = first_rank(retrieval.get("fused_top") or [])
    passed = outcome_ok and (facts_ok is not False) and not (reply.outcome == "answered" and item.get("facts") and facts_ok is None)
    cost = sum(c for c in (_cost(trace_data.get("understand_llm")), _cost(trace_data.get("answer_llm"))) if c)
    return {
        "id": item["id"], "lang": item.get("lang", "en"), "question": item["q"], "expect": expected,
        "outcome": reply.outcome, "reason": reply.reason, "answer": reply.answer, "trace_id": reply.trace_id,
        "outcome_ok": outcome_ok, "facts_ok": facts_ok, "passed": passed,
        "rank": rank, "fused_rank": fused_rank, "timings": reply.timings, "cost_usd": cost,
    }


def summarize(results: list[dict]) -> dict:
    def pct(xs):
        return round(100 * sum(xs) / len(xs), 1) if xs else None

    def p(values, q):
        values = sorted(v for v in values if v is not None)
        if not values:
            return None
        return round(values[min(len(values) - 1, int(q * len(values)))])

    def block(rows):
        with_src = [r for r in rows if r.get("_has_sources")]
        return {
            "n": len(rows),
            "pass_rate": pct([r["passed"] for r in rows]),
            "outcome_accuracy": pct([r["outcome_ok"] for r in rows]),
            "fact_accuracy": pct([r["facts_ok"] for r in rows if r["facts_ok"] is not None]),
            "hit@1": pct([r["rank"] == 1 for r in with_src]),
            "hit@3": pct([r["rank"] is not None and r["rank"] <= 3 for r in with_src]),
            "hit@5": pct([r["rank"] is not None and r["rank"] <= 5 for r in with_src]),
            "mrr": round(statistics.mean(1 / r["rank"] if r["rank"] else 0 for r in with_src), 3) if with_src else None,
        }

    overall = block(results)
    by_lang = {lang: block([r for r in results if r["lang"] == lang]) for lang in sorted({r["lang"] for r in results})}
    by_expect = {exp: block([r for r in results if exp in r["expect"]])
                 for exp in sorted({e for r in results for e in r["expect"]})}
    stages = ["understand_ms", "embed_ms", "dense_ms", "keyword_ms", "rerank_ms", "llm_ttft_ms", "llm_ms", "total_ms"]
    latency = {s: {"p50": p([r["timings"].get(s) for r in results], 0.5), "p95": p([r["timings"].get(s) for r in results], 0.95)}
               for s in stages}
    answered_latency = [r["timings"].get("total_ms") for r in results if r["outcome"] == "answered"]
    costs = [r["cost_usd"] for r in results if r["cost_usd"]]
    return {
        "overall": overall, "by_language": by_lang, "by_expectation": by_expect, "latency_ms": latency,
        "answered_total_ms": {"p50": p(answered_latency, 0.5), "p95": p(answered_latency, 0.95)},
        "figure_check_blocks": sum(r["reason"] == "figure_check_failed" for r in results),
        "errors": sum(r["outcome"] == "error" for r in results),
        "cost_usd": {"total": round(sum(costs), 5), "per_question": round(statistics.mean(costs), 6) if costs else None},
    }


def render_report(run_id: str, golden: str, config: dict, metrics: dict, results: list[dict]) -> str:
    o = metrics["overall"]
    lines = [f"# Evaluation run {run_id}", "", f"- Golden set: `{golden}` ({o['n']} questions)",
             f"- Models: understand `{config['understand_model']}`, answer `{config['answer_model']}`, "
             f"embedder `{config['embedder']}`, reranker `{config['reranker']}`",
             f"- Settings: {', '.join(f'{k}={v}' for k, v in config['retrieval'].items())}", "",
             "## Summary", "", "| Metric | Value |", "|---|---|",
             f"| Pass rate | {o['pass_rate']}% |", f"| Correct outcome (answer/refuse/clarify/route) | {o['outcome_accuracy']}% |",
             f"| Correct facts (when answered) | {o['fact_accuracy']}% |",
             f"| Retrieval hit@1 / @3 / @5 | {o['hit@1']}% / {o['hit@3']}% / {o['hit@5']}% |", f"| Retrieval MRR | {o['mrr']} |",
             f"| Answers blocked by figure check | {metrics['figure_check_blocks']} |", f"| Errors | {metrics['errors']} |",
             f"| Latency, answered questions p50 / p95 | {metrics['answered_total_ms']['p50']} / {metrics['answered_total_ms']['p95']} ms |",
             f"| Cost | ${metrics['cost_usd']['total']} total, ${metrics['cost_usd']['per_question']} per question |", "",
             "## By language", "", "| Language | n | Pass | Outcome | Facts | hit@5 | MRR |", "|---|---|---|---|---|---|---|"]
    for lang, b in metrics["by_language"].items():
        lines.append(f"| {lang} | {b['n']} | {b['pass_rate']}% | {b['outcome_accuracy']}% | {b['fact_accuracy']}% | {b['hit@5']}% | {b['mrr']} |")
    lines += ["", "## By expected behaviour", "", "| Expected | n | Pass | Outcome |", "|---|---|---|---|"]
    for exp, b in metrics["by_expectation"].items():
        lines.append(f"| {exp} | {b['n']} | {b['pass_rate']}% | {b['outcome_accuracy']}% |")
    lines += ["", "## Latency per step (ms)", "", "| Step | p50 | p95 |", "|---|---|---|"]
    for stage, v in metrics["latency_ms"].items():
        lines.append(f"| {stage.removesuffix('_ms')} | {v['p50']} | {v['p95']} |")
    failures = [r for r in results if not r["passed"]]
    lines += ["", f"## Failures ({len(failures)})", ""]
    for r in failures:
        lines += [f"### {r['id']} ({r['lang']})", f"- Question: {r['question']}",
                  f"- Expected: {', '.join(r['expect'])} · Got: **{r['outcome']}**" + (f" ({r['reason']})" if r["reason"] else ""),
                  f"- Facts ok: {r['facts_ok']} · Retrieval rank: {r['rank']} (before rerank: {r['fused_rank']})",
                  f"- Answer: {r['answer']}", f"- Trace: `GET /v1/traces/{r['trace_id']}`", ""]
    return "\n".join(lines)


class EvalRunner:
    def __init__(self, db: Database, conversation: Conversation, traces: TraceStore, settings, golden_dir: Path,
                 reports_dir: Path):
        self.db, self.conversation, self.traces, self.s = db, conversation, traces, settings
        self.golden_dir, self.reports_dir = golden_dir, reports_dir

    def config_snapshot(self) -> dict:
        s = self.s
        return {"understand_model": s.understand_model, "answer_model": s.answer_model,
                "embedder": self.conversation.retriever.embedder.name, "reranker": self.conversation.retriever.reranker.name,
                "retrieval": {"dense_query": s.dense_query, "dense_k": s.dense_k, "keyword_k": s.keyword_k,
                              "rerank_candidates": s.rerank_candidates, "context_chunks": s.context_chunks,
                              "min_rerank_score": s.min_rerank_score}}

    def start(self, golden: str) -> str:
        path = self.golden_dir / f"{golden}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"golden set '{golden}' not found in {self.golden_dir}")
        run_id = str(uuid.uuid4())
        with self.db.connection() as conn:
            conn.execute("INSERT INTO eval_runs (id, golden_file, config) VALUES (%s, %s, %s)",
                         (run_id, path.name, json.dumps(self.config_snapshot())))
        return run_id

    async def run(self, run_id: str, golden: str, only: list[str] | None = None) -> dict:
        path = self.golden_dir / f"{golden}.yaml"
        items = load_golden(path)
        if only:
            items = [i for i in items if i["id"] in only]
        results, started = [], time.perf_counter()
        try:
            for item in items:
                session_id = None
                if item.get("history"):
                    session_id = str(self.traces.create_session()["id"])
                    for prior in item["history"]:
                        await self.conversation.ask(prior, session_id)
                reply = await self.conversation.ask(item["q"], session_id)
                trace = self.traces.get(reply.trace_id)
                row = score_item(item, reply, trace["data"] if trace else {})
                row["_has_sources"] = bool(item.get("sources"))
                results.append(row)
                log(logger, "eval item", run_id=run_id, id=item["id"], passed=row["passed"], outcome=reply.outcome)
            metrics = summarize(results)
            metrics["duration_s"] = round(time.perf_counter() - started, 1)
            for r in results:
                r.pop("_has_sources", None)
            config = self.config_snapshot()
            self.reports_dir.mkdir(parents=True, exist_ok=True)
            report_path = self.reports_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_{golden}_{run_id[:8]}.md"
            report_path.write_text(render_report(run_id, path.name, config, metrics, results), encoding="utf-8")
            with self.db.connection() as conn:
                conn.execute("UPDATE eval_runs SET status = 'done', metrics = %s, results = %s, report_path = %s WHERE id = %s",
                             (json.dumps(metrics), json.dumps(results, default=str), str(report_path), run_id))
            log(logger, "eval finished", run_id=run_id, pass_rate=metrics["overall"]["pass_rate"], report=str(report_path))
            return metrics
        except Exception as e:
            logger.exception("eval failed")
            with self.db.connection() as conn:
                conn.execute("UPDATE eval_runs SET status = 'failed', error = %s WHERE id = %s", (str(e), run_id))
            raise

    def get(self, run_id: str) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM eval_runs WHERE id::text = %s", (run_id,)).fetchone()
