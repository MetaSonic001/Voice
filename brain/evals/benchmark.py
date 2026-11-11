"""Phase 3 benchmark with the real models, the real LLM and the mock core bank.

  1. routing    evals/routing.yaml: intent / topic / detail / action / issue / department accuracy and latency of the
                understanding step; the previous prompt (git HEAD) is timed on the same utterances for comparison.
  2. decisions  evals/decisions.yaml: answer / clarify / conflict / refuse with the previous and the new answer prompt,
                on identical retrieved passages (the prompt is the only difference), with the figure check applied.
  3. journeys   scripted multi-turn calls through the API (verification, actions, fraud, complaints, transfers).

Usage (stop the API first: this loads the models itself):  python -m evals.benchmark [routing] [decisions] [journeys]
Groq's free tier allows ~8k tokens a minute per model, so calls are paced; a full run takes ~30-40 minutes.
Writes evals/reports/benchmark-<time>.md and .json.
"""

import asyncio
import json
import re
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

import yaml

from app.config import ROOT, Settings
from app.container import build
from app.services import conversation as conv
from app.services.numbers import allowed_numbers, unsupported_numbers
from app.services.sentences import SentenceSplitter

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
        if "429" not in str(error) or attempt == 9:
            raise error
        m = re.search(r"try again in ([\d.]+)s", str(error))
        await asyncio.sleep(float(m.group(1)) + 1 if m else 15)

    async def json(self, *args, **kwargs):
        for attempt in range(10):
            try:
                return await self.llm.json(*args, **kwargs)
            except Exception as e:  # noqa: PERF203
                await self._wait(e, attempt)

    async def stream(self, model, messages, meta, max_tokens=400):
        for attempt in range(10):
            started = False
            try:
                async for delta in self.llm.stream(model, messages, meta, max_tokens=max_tokens):
                    started = True
                    yield delta
                return
            except Exception as e:
                if started:
                    raise
                await self._wait(e, attempt)

    async def close(self):
        await self.llm.close()


def _git(path: str) -> str:
    return subprocess.run(["git", "show", f"HEAD:{path}"], capture_output=True, text=True, encoding="utf-8",
                          cwd=ROOT.parent).stdout


def previous_prompts() -> dict:
    """The understanding prompt + schema and the answer prompt as they were before Phase 3 (git HEAD)."""
    src = _git("brain/app/services/conversation.py")
    old_topics = ("balance", "transactions", "fd", "loan", "credit_card", "debit_card", "summary", "other")
    old_periods = ("", "today", "yesterday", "last_7_days", "this_week", "this_month", "last_month", "last_30_days")
    ns = {"TOPICS": old_topics, "PERIODS": old_periods, "LANGUAGES": ("en", "hi", "hinglish", "other"),
          "INTENTS": ("general", "personal", "action", "issue", "human", "smalltalk", "out_of_scope", "manipulation")}
    exec(src[src.index("_PERSONAL_FIELDS = {"):src.index("UNDERSTAND_SYSTEM = ")], ns)
    for name in ("UNDERSTAND_SYSTEM", "ANSWER_SYSTEM"):
        start = src.index(f'{name} = """') + len(f'{name} = """')
        ns[name] = src[start:src.index('"""', start)]
    return ns


def _ok(expected, got) -> bool:
    if isinstance(expected, list):
        return got in expected
    return str(got) == str(expected)


def _pct(values) -> float | None:
    return round(100 * sum(values) / len(values), 1) if values else None


def _p(values, q):
    values = sorted(v for v in values if v is not None)
    return values[min(len(values) - 1, int(q * len(values)))] if values else None


async def routing(c, pacer: Pacer, old: dict) -> dict:
    items = yaml.safe_load((HERE / "routing.yaml").read_text(encoding="utf-8"))
    model = c.settings.understand_model
    rows, field_hits, latency_new, latency_old = [], defaultdict(list), [], []
    for n, item in enumerate(items):
        await pacer.wait(model, 3800)
        try:
            u, meta = await c.conversation.understand(conv.redact(item["q"]), [], {})
        except Exception as e:
            rows.append({"id": item["id"], "error": str(e)})
            continue
        pacer.add(model, meta)
        latency_new.append(meta.get("latency_ms"))
        checks = {"intent": _ok(item["intent"], u.intent)}
        p = u.personal or {}
        for key in ("topic", "detail"):
            if key in item:
                checks[key] = _ok(item[key], p.get(key))
        if "issue" in item:
            checks["issue"] = u.issue == item["issue"]
        if "department" in item:
            checks["department"] = u.department == item["department"]
        if "amount" in item:
            checks["amount"] = float(p.get("amount") or 0) == float(item["amount"])
        for key, want in (item.get("action") or {}).items():
            checks[f"action.{key}"] = str((u.action or {}).get(key, "")) == str(want)
        for k, v in checks.items():
            field_hits[k.split(".")[0] if k.startswith("action") else k].append(v)
        rows.append({"id": item["id"], "q": item["q"], "pass": all(checks.values()), "checks": checks, "intent": u.intent,
                     "personal": {k: p.get(k) for k in ("topic", "detail", "amount")}, "action": u.action, "issue": u.issue,
                     "department": u.department, "latency_ms": meta.get("latency_ms")})
        if n < 15:  # the previous prompt on the same utterance, right after, for a fair latency comparison
            await pacer.wait(model, 3200)
            user = f"Caller: {conv.redact(item['q'])}"
            try:
                _, meta_old = await c.llm.json(model, old["UNDERSTAND_SYSTEM"], user, old["UNDERSTAND_SCHEMA"], max_tokens=600)
                pacer.add(model, meta_old)
                latency_old.append(meta_old.get("latency_ms"))
            except Exception:
                pass
        print(f"routing {item['id']}: {'ok' if rows[-1].get('pass') else 'MISS'} {u.intent}", flush=True)
    paired = len(latency_old)
    return {"items": rows, "pass_rate": _pct([r.get("pass", False) for r in rows]),
            "fields": {k: _pct(v) for k, v in field_hits.items()},
            "latency_ms": {"new_p50": _p(latency_new, 0.5), "new_p95": _p(latency_new, 0.95),
                           "new_p50_paired": _p(latency_new[:paired], 0.5), "old_p50_paired": _p(latency_old, 0.5),
                           "paired_n": paired}}


async def _answer(c, system: str, lang: str, question: str, context, facts: dict) -> dict:
    sources = "\n\n".join(f"[{i}] {h.section_path}" + (f" (effective {h.effective_from})" if h.effective_from else "")
                          + f"\n{h.text}" for i, h in enumerate(context, start=1))
    messages = [{"role": "system", "content": system.format(language=conv.LANGUAGE_INSTRUCTIONS[lang])},
                {"role": "user", "content": f"Sources:\n{sources}\n\n" + (f"Caller details: {conv.describe_facts(facts)}\n" if facts else "")
                 + f"Question: {question}"}]
    meta, raw, t = {}, "", time.perf_counter()
    async for delta in c.llm.stream(c.settings.answer_model, messages, meta, max_tokens=700):
        raw += delta.translate(conv._BRACKETS)
    head = raw.strip()
    mode = conv._output_mode(head) or ("no_answer" if not head else "answer")
    decision = {"answer": "answer", "clarify": "clarify", "conflict": "conflict", "no_answer": "refuse"}[mode]
    source_text = [f"{h.section_path} {h.effective_from or ''} {h.text}" for h in context]
    unsafe = False
    if decision == "answer":
        splitter, checks = SentenceSplitter(), []
        sentences = list(splitter.feed(head)) + list(splitter.flush())
        cited: set = set()
        for s in sentences:
            if conv.Conversation._check_sentence(s, context, source_text, (question,), cited, checks):
                decision, unsafe = "refuse", True  # the figure check would have stopped it
                break
    elif decision == "clarify" and unsupported_numbers(head, allowed_numbers(question, *source_text)):
        decision, unsafe = "refuse", True
    return {"decision": decision, "text": head, "unsafe_blocked": unsafe, "ttft_ms": meta.get("ttft_ms"),
            "ms": round((time.perf_counter() - t) * 1000), "meta": meta}


async def decisions(c, pacer: Pacer, old: dict) -> dict:
    items = yaml.safe_load((HERE / "decisions.yaml").read_text(encoding="utf-8"))
    rows = []
    for item in items:
        await pacer.wait(c.settings.understand_model, 3800)
        u, meta = await c.conversation.understand(conv.redact(item["q"]), [], {})
        pacer.add(c.settings.understand_model, meta)
        lang = u.language if u.language in conv.LANGUAGE_INSTRUCTIONS else "en"
        dense = u.search_query_en if c.settings.dense_query == "english" and u.search_query_en else u.standalone_question
        reranked, _ = await c.retriever.search(dense, u.search_query_en or u.standalone_question, {})
        context = [h for h in reranked[: c.settings.context_chunks] if (h.rerank_score or 0) >= c.settings.min_rerank_score * 0.5]
        row = {"id": item["id"], "q": item["q"], "expect": item["expect"]}
        if not context or (reranked[0].rerank_score or 0) < c.settings.min_rerank_score:
            row["old"] = row["new"] = {"decision": "refuse", "text": "(low relevance: no LLM call)", "unsafe_blocked": False}
        else:
            for label, system in (("old", old["ANSWER_SYSTEM"]), ("new", conv.ANSWER_SYSTEM)):
                await pacer.wait(c.settings.answer_model, 4800)
                result = await _answer(c, system, lang, u.standalone_question, context, u.facts)
                pacer.add(c.settings.answer_model, result.pop("meta"))
                row[label] = result
        for label in ("old", "new"):
            r = row[label]
            expected = item["expect"] if isinstance(item["expect"], list) else [item["expect"]]
            r["decision_ok"] = r["decision"] in expected
            r["facts_ok"] = None
            if item.get("facts") and r["decision"] == "answer":
                text = re.sub(r"[,\s]", "", r["text"].lower())
                r["facts_ok"] = all(any(re.sub(r"[,\s]", "", str(a).lower()) in text for a in g) for g in item["facts"])
            r["pass"] = r["decision_ok"] and r["facts_ok"] is not False
        rows.append(row)
        print(f"decision {item['id']}: old={row['old']['decision']} new={row['new']['decision']}", flush=True)

    def summary(label):
        rs = [r[label] for r in rows]
        answerable = [r for r, it in zip(rs, items) if "answer" in (it["expect"] if isinstance(it["expect"], list) else [it["expect"]])
                      and it["expect"] != "refuse"]
        return {"pass_rate": _pct([r["pass"] for r in rs]), "decision_accuracy": _pct([r["decision_ok"] for r in rs]),
                "unnecessary_refusals": sum(1 for r, it in zip(rs, items) if r["decision"] == "refuse" and "refuse" not in
                                            (it["expect"] if isinstance(it["expect"], list) else [it["expect"]])),
                "wrong_figures_blocked": sum(r["unsafe_blocked"] for r in rs),
                "facts_wrong": sum(1 for r in rs if r["facts_ok"] is False),
                "answer_ttft_p50_ms": _p([r.get("ttft_ms") for r in answerable], 0.5)}

    return {"items": rows, "old": summary("old"), "new": summary("new")}


JOURNEYS = [
    ("Salary account, then block and unblock a credit card", "9000000101", [
        ("Which one is my salary account?", ["answered"], ["4521"]),
        ("Block only my credit card ending 6712", ["confirm"], ["6712"]),
        ("yes", ["action_done"], ["temporarily blocked"]),
        ("Please unblock that card again", ["confirm"], ["6712"]),
        ("haan kar do", ["action_done"], ["active again"])]),
    ("Fraud: unrecognised ₹8,499, block, dispute, fraud team", "9000000105", [
        ("I don't recognise a transaction of 8,499 on my card", ["confirm"], ["8,499", "permanently block"]),
        ("yes go ahead", ["action_done"], ["permanently blocked", "1930"]),
        ("yes", ["handoff"], ["AU-DSP-"])]),
    ("ATM cash not dispensed (Hinglish)", "9000000101", [
        ("ATM se cash nahi nikla lekin 10,000 kat gaye", ["confirm"], ["10,000"]),
        ("haan", ["ticket"], ["AU-CMP-"])]),
    ("Debit card status first, then switch online payments on", "9000000103", [
        ("Before you change anything, tell me which payments are enabled on my debit card", ["answered"], ["5521"]),
        ("Okay, enable online payments on it", ["confirm"], ["5521"]),
        ("yes", ["action_done"], ["5521"])]),
    ("Senior citizen FDs (Hindi)", "9000000102", [
        ("मेरी कौन सी FD सबसे पहले mature होने वाली है?", ["answered"], ["7824"]),
        ("उससे मुझे कितने पैसे मिलेंगे?", ["answered"], ["7824"]),
        ("क्या वह अपने आप renew हो जाएगी?", ["answered"], ["7824"])]),
    ("My minimum balance, then charges", "9000000105", [
        ("How much minimum balance do I need to keep in my account?", ["answered"], ["5,000"]),
        ("Was any charge deducted for it?", ["answered"], ["72"])]),
    ("EMI failed despite balance", "9000000101", [
        ("I had enough balance but my personal loan EMI still failed, what happened?", ["answered", "confirm"], ["7,850"])]),
    ("Asks for a person: transferred straight away (an explicit request is the consent)", "9000000001", [
        ("You're not understanding me, please connect me to someone", ["handoff"], ["stay on the line"])]),
    ("Generic minimum balance, then the account named", None, [
        ("What is the minimum balance for a savings account?", ["clarify", "answered"], []),
        ("The Digital one", ["answered", "clarify"], [])]),
]


async def journeys(c, pacer: Pacer) -> dict:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with c.db.connection() as conn:  # remember the demo customers' card states, to put them back afterwards
        saved = {"credit_cards": conn.execute("SELECT * FROM corebank.credit_cards WHERE cif LIKE 'CIF9001%%'").fetchall(),
                 "debit_cards": conn.execute("SELECT d.* FROM corebank.debit_cards d JOIN corebank.accounts a "
                                             "ON a.number = d.account_number WHERE a.cif LIKE 'CIF9001%%'").fetchall()}
    results, sessions = [], []
    with TestClient(create_app(c)) as client:
        for name, caller, turns in JOURNEYS:
            sid = client.post("/v1/sessions", json={"caller_number": caller} if caller else {}).json()["id"]
            sessions.append(sid)
            steps, ok_all = [], True
            for text, outcomes, must in turns:
                await pacer.wait(c.settings.understand_model, 3500)
                await pacer.wait(c.settings.answer_model, 1000)
                t = time.perf_counter()
                r = client.post("/v1/ask", json={"question": text, "session_id": sid}).json()
                if r.get("outcome") == "auth_required" and r.get("auth", {}).get("simulated_sms"):
                    code = re.search(r"\b(\d{6})\b", r["auth"]["simulated_sms"]).group(1)
                    r = client.post("/v1/auth/verify", json={"session_id": sid, "code": code}).json()["reply"] or {}
                ms = round((time.perf_counter() - t) * 1000)
                for model in (c.settings.understand_model, c.settings.answer_model):
                    pacer.add(model, {"prompt_tokens": 2500 if model == c.settings.understand_model else 0})
                ok = r.get("outcome") in outcomes and all(m.lower() in (r.get("answer") or "").lower() for m in must)
                ok_all &= ok
                steps.append({"say": text, "outcome": r.get("outcome"), "ok": ok, "answer": r.get("answer"), "ms": ms,
                              "brain_ms": (r.get("timings") or {}).get("total_ms")})
                print(f"journey {name[:30]} | {text[:40]} -> {r.get('outcome')} {'ok' if ok else 'MISS'}", flush=True)
            results.append({"journey": name, "pass": ok_all, "steps": steps})
    with c.db.connection() as conn:  # restore demo cards; close handoffs made by the benchmark
        for table, rows in saved.items():
            keys = [k for k in ("status", "atm", "pos", "ecom", "contactless", "international") if rows and k in rows[0]]
            for row in rows:
                conn.execute(f"UPDATE corebank.{table} SET {', '.join(f'{k} = %s' for k in keys)} WHERE id = %s",
                             (*[row[k] for k in keys], row["id"]))
        conn.execute("UPDATE handoffs SET status = 'completed', disposition = 'benchmark' WHERE session_id = ANY(%s::uuid[])",
                     (sessions,))
    turns = [s for j in results for s in j["steps"]]
    return {"journeys": results, "pass_rate": _pct([j["pass"] for j in results]), "turn_pass_rate": _pct([s["ok"] for s in turns]),
            "turn_ms_p50": _p([s["brain_ms"] for s in turns], 0.5), "turn_ms_p95": _p([s["brain_ms"] for s in turns], 0.95)}


def report(out: dict) -> str:
    lines = [f"# Benchmark {out['when']}", "", f"Models: understand {out['models'][0]}, answer {out['models'][1]}. "
             "Real embeddings, reranker, knowledge base and mock core bank.", ""]
    if r := out.get("routing"):
        lines += ["## Routing (understanding step)", "", f"Items: {len(r['items'])} · all fields right: {r['pass_rate']}%", "",
                  "| Field | Accuracy |", "|---|---|"] + [f"| {k} | {v}% |" for k, v in r["fields"].items()]
        lat = r["latency_ms"]
        lines += ["", f"Latency p50 {lat['new_p50']} ms, p95 {lat['new_p95']} ms. Same {lat['paired_n']} utterances, back to back: "
                  f"previous prompt p50 {lat['old_p50_paired']} ms, new prompt p50 {lat['new_p50_paired']} ms.", "",
                  "Misses:", ""] + [f"- `{i['id']}` {i.get('q', '')} -> intent {i.get('intent')} {i.get('error', '')[:80]}, "
                                    f"{ {k: v for k, v in i.get('checks', {}).items() if not v} }" for i in r["items"] if not i.get("pass")]
    if d := out.get("decisions"):
        lines += ["", "## Answer decisions (previous vs new answer prompt, identical passages)", "",
                  "| | Previous | New |", "|---|---|---|"]
        for key in ("pass_rate", "decision_accuracy", "unnecessary_refusals", "wrong_figures_blocked", "facts_wrong", "answer_ttft_p50_ms"):
            lines.append(f"| {key} | {d['old'][key]} | {d['new'][key]} |")
        lines += ["", "| Question | Expected | Previous | New |", "|---|---|---|---|"]
        for i in d["items"]:
            lines.append(f"| {i['q'][:70]} | {i['expect']} | {i['old']['decision']}{'' if i['old']['pass'] else ' ✗'} | "
                         f"{i['new']['decision']}{'' if i['new']['pass'] else ' ✗'} |")
        lines += ["", "New answers to the generic questions:", ""] + [
            f"- {i['q']}: {i['new']['text'][:300]}" for i in d["items"] if i["id"].endswith(("generic", "generic-hl", "generic-hi"))]
    if j := out.get("journeys"):
        lines += ["", "## Journeys (multi-turn, through the API)", "",
                  f"Journeys passed: {j['pass_rate']}% · turns passed: {j['turn_pass_rate']}% · brain time per turn p50 "
                  f"{j['turn_ms_p50']} ms, p95 {j['turn_ms_p95']} ms", ""]
        for x in j["journeys"]:
            lines.append(f"**{x['journey']}**: {'pass' if x['pass'] else 'FAIL'}")
            lines += [f"- \"{s['say']}\" -> {s['outcome']}{'' if s['ok'] else ' ✗'}: {(s['answer'] or '')[:220]}" for s in x["steps"]]
            lines.append("")
    return "\n".join(lines) + "\n"


async def main(parts: list[str]):
    s = Settings()
    c = build(s, start_worker=False)
    c.llm = c.conversation.llm = Patient(c.llm)
    pacer, old = Pacer(), previous_prompts()
    out = {"when": time.strftime("%Y-%m-%d %H:%M"), "models": [s.understand_model, s.answer_model]}
    try:
        for part, run in (("routing", lambda: routing(c, pacer, old)), ("decisions", lambda: decisions(c, pacer, old)),
                          ("journeys", lambda: journeys(c, pacer))):
            if part in parts:
                try:
                    out[part] = await run()
                except Exception as e:  # keep the other parts and still write the report
                    out[f"{part}_error"] = repr(e)
                    print(f"{part} failed: {e!r}", flush=True)
    finally:
        reports = HERE / "reports"
        reports.mkdir(exist_ok=True)
        stem = reports / f"benchmark-{time.strftime('%Y%m%d-%H%M%S')}"
        stem.with_suffix(".json").write_text(json.dumps(out, default=str, ensure_ascii=False, indent=1), encoding="utf-8")
        stem.with_suffix(".md").write_text(report(out), encoding="utf-8")
        print(f"report: {stem}.md", flush=True)
        await c.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or ["routing", "decisions", "journeys"]))
