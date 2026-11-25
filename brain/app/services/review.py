"""Automatic quality review after a call (PHASE-4-PLAN §11). Runs in the background when a call ends: adds no latency.

1. A call packet is built from the record (already redacted): the turns, outcomes, bank fields read, actions, handoff, latency.
2. Deterministic checks always run (free): disclosure, verification before account data, leftover personal data, errors,
   slow replies, the caller repeating themselves, repeated "I don't know", a request for a person, a degraded model.
3. The LLM judge (a different model from the agent, so its quota is separate) scores five dimensions 1-5 against an anchored
   rubric and must quote evidence. The code verifies every quote against the transcript and drops invented evidence.
   Which calls are judged: JUDGE_MODE=all (development) | selective (risk-weighted, below) | off.
4. People review every critical failure and everything flagged; their labels are the calibration data.
"""

import json
import logging
import random
import re
from dataclasses import dataclass

from app.db import Database
from app.services.content import current
from app.services.audit import AuditStore
from app.services.numbers import screen_marks
from app.services.privacy import redact

logger = logging.getLogger(__name__)
DIMENSIONS = ("task_completion", "correctness", "conversation", "compliance", "handoff")
# What went wrong (failure labels), who or what caused it, and where an engineer would fix it. One judge call returns
# all of it as a short list of findings; the call's labels are worked out in code from those findings.
CATEGORIES = ("wrong_fact", "missing_information", "unnecessary_information", "unresolved_request", "repeated_question",
              "lost_context", "wrong_intent", "missing_verification", "unnecessary_verification", "wrong_refusal",
              "missed_handoff", "unnecessary_handoff", "bad_handoff", "unsafe_action", "ignored_risk", "poor_voice_response",
              "too_long", "wrong_language", "slow", "other")
SOURCES = ("agent", "system_failure", "bank_system", "knowledge_gap", "unclear_caller", "insufficient_evidence")
FIX_AREAS = ("router", "retrieval", "answer_prompt", "bank_templates", "workflow", "code", "knowledge_base", "voice", "none")
MAX_FINDINGS = 5
SAMPLE_RATE = 0.10  # selective mode: this share of clean calls is judged anyway, to catch drift
SLOW_MS = 2000


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "dimension_scores": {"type": "object", "properties": {d: {"type": "integer", "minimum": 0, "maximum": 5} for d in DIMENSIONS},
                             "required": list(DIMENSIONS), "additionalProperties": False},
        "evidence": {"type": "array", "items": {"type": "object", "properties": {
            "dimension": {"type": "string", "enum": list(DIMENSIONS)}, "turn": {"type": "integer"}, "quote": {"type": "string"}},
            "required": ["dimension", "turn", "quote"], "additionalProperties": False}},
        "findings": {"type": "array", "maxItems": MAX_FINDINGS, "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": list(CATEGORIES)}, "source": {"type": "string", "enum": list(SOURCES)},
            "turn": {"type": "integer"}, "quote": {"type": "string"}, "what_happened": {"type": "string"},
            "why_it_matters": {"type": "string"}, "fix": {"type": "string"}, "fix_area": {"type": "string", "enum": list(FIX_AREAS)}},
            "required": ["category", "source", "turn", "quote", "what_happened", "why_it_matters", "fix", "fix_area"],
            "additionalProperties": False}},
        "critical_failure": {"type": "boolean"},
        "needs_human_review": {"type": "boolean"},
        "recommended_fix": {"type": "string"},
    },
    "required": ["dimension_scores", "evidence", "findings", "critical_failure", "needs_human_review", "recommended_fix"],
    "additionalProperties": False,
}


@dataclass
class Check:
    name: str
    ok: bool
    severity: str = "info"  # info | warn | fail
    detail: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "severity": self.severity, "detail": self.detail}


REASK = "system_reask"  # the agent picking the caller's question up again after verification (the caller said nothing)


def is_reask(turns: list[dict], i: int) -> bool:
    """Turn i (0-based) is the system re-asking the caller's question after the keypad code - not the caller speaking.
    Labelled since 7 Nov (input after_verification*); older calls are recognised by the same question straight after a
    turn that asked for verification."""
    t = turns[i]
    if str(t.get("input", "")).startswith("after_verification"):
        return True
    prev = turns[i - 1] if i else None
    return bool(prev) and prev["outcome"] == "auth_required" and _norm(prev["question"]) == _norm(t["question"])


def build_packet(call: dict) -> dict:
    """The call as the reviewer sees it. Text is already redacted when it was stored; it is redacted again to be sure."""
    turns = []
    for i, t in enumerate(call["turns"], start=1):
        source = REASK if is_reask(call["turns"], i - 1) else t["input"]
        turns.append({"n": i, "caller": redact(t["question"]), "agent": redact(t["answer"] or ""), "intent": t["intent"],
                      "outcome": t["outcome"], "ms": t["total_ms"], "input": source, "bank_fields": (t.get("bank") or {}).get("fields"),
                      "sources": [f"{s.get('document')} > {s.get('section')}" for s in t.get("sources") or []][:3],
                      # the cited passages' text (short), so "correctness" is judged against what the agent actually had
                      "evidence": [redact(c.get("snippet") or "")[:200] for c in t.get("citations") or [] if c.get("snippet")][:3],
                      "first_audio_ms": (t.get("timings") or {}).get("first_audio_ms"), "long_reply": (t.get("long_reply") or 0),
                      "fallback": bool(t.get("fallback"))})
    events = [{"kind": e["kind"], "detail": e["detail"]} for e in call["events"]]
    return {"turns": turns[:40], "turn_count": len(turns), "events": events, "alerts": [a["kind"] for a in call["alerts"]],
            "verified": bool(call.get("customer_id")), "language": call.get("language"), "ended": call.get("end_reason"),
            "auth_events": [e["event"] for e in call["auth_events"]]}


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def code_checks(call: dict, packet: dict) -> list[Check]:
    """The free, deterministic checks. A failed one always gets the judge's attention."""
    turns, kinds = packet["turns"], {e["kind"] for e in packet["events"]}
    out: list[Check] = []
    live = "call_started" in kinds or "disclosure" in kinds or any(t["input"] == "speech" for t in turns)
    out.append(Check("disclosure_said", ("disclosure" in kinds) or not live, "fail" if live else "info",
                     "the opening disclosure was played" if "disclosure" in kinds else "a live call without the recorded disclosure"))
    verified_at = next((e["created_at"] for e in call["auth_events"] if e["event"] == "otp_verified"), None)
    early = [t["n"] for t, raw in zip(turns, call["turns"], strict=False)
             if raw["outcome"] == "answered" and (raw.get("bank") or {}).get("fields") and (verified_at is None or raw["created_at"] < verified_at)]
    out.append(Check("auth_before_account_data", not early, "fail", f"account data answered before verification in turn {early}" if early else ""))
    leaked = [t["n"] for t, raw in zip(turns, call["turns"], strict=False) if redact(raw["answer"] or "") != (raw["answer"] or "")
              or redact(raw["question"]) != raw["question"]]
    out.append(Check("no_personal_data_in_text", not leaked, "fail", f"unmasked identifiers in turn {leaked}" if leaked else ""))
    errors = [t["n"] for t in turns if t["outcome"] == "error"]
    out.append(Check("no_system_errors", not errors, "warn", f"errors in turn {errors}" if errors else ""))
    slow = sorted((t.get("first_audio_ms") or t["ms"] or 0) for t in turns)
    p95 = slow[min(len(slow) - 1, int(0.95 * len(slow)))] if slow else 0
    out.append(Check("replies_fast_enough", p95 <= SLOW_MS, "warn", f"slowest 5% of replies took {p95} ms"))
    asked = [_norm(t["caller"]) for t in turns if len(_norm(t["caller"]).split()) >= 3 and t["input"] != REASK]
    repeated = len(asked) != len(set(asked))
    out.append(Check("caller_not_repeating_themselves", not repeated, "warn", "the caller asked the same thing twice: the agent missed it" if repeated else ""))
    unanswered = sum(t["outcome"] in ("refused", "error") for t in turns)
    out.append(Check("not_stuck_saying_dont_know", unanswered < 2, "warn", f"{unanswered} turns without an answer" if unanswered >= 2 else ""))
    wants_human = any(a in ("wants_human", "struggling") for a in packet["alerts"])
    out.append(Check("caller_not_asking_for_a_person", not wants_human, "warn", "the caller asked for a person or the agent was struggling"))
    long_replies = [t["n"] for t in turns if t["long_reply"]]
    out.append(Check("replies_short_enough", not long_replies, "info", f"long replies without a check-in in turn {long_replies}" if long_replies else ""))
    written = {t["n"]: screen_marks(raw["answer"] or "") for t, raw in zip(turns, call["turns"], strict=False)}
    written = {n: marks for n, marks in written.items() if marks}
    out.append(Check("replies_in_spoken_form", not written, "warn",
                     "; ".join(f"turn {n}: {', '.join(m)}" for n, m in written.items()) if written else ""))
    degraded = [t["n"] for t in turns if t["fallback"]]
    out.append(Check("main_model_used", not degraded, "warn", f"fallback model used in turn {degraded}" if degraded else ""))
    # The call agenda (services/agenda.py): something the caller asked that was neither answered nor let go by them.
    # "skipped" (the caller said that's all), "dropped" (offered twice, not taken up) and "handed_over" are not counted.
    left = [i.get("label") or i.get("kind") or "a request" for i in call.get("agenda") or [] if i.get("status") in ("pending", "auth")]
    out.append(Check("every_request_answered", not left, "warn",
                     f"asked but never answered or offered again: {'; '.join(left)}" if left else ""))
    return out


def judge_reason(call: dict, packet: dict, checks: list[Check], mode: str, rng: random.Random) -> str | None:
    """Why this call goes to the LLM judge (None = it does not). The risk-weighted selection from the plan."""
    if mode == "off":
        return None
    if mode == "all":
        return "development: every call is judged"
    failed = [c.name for c in checks if not c.ok and c.severity in ("warn", "fail")]
    if failed:
        return f"a code check failed: {', '.join(failed)}"
    kinds = {e["kind"] for e in packet["events"]}
    if kinds & {"card_action", "ticket", "handoff_requested"}:
        return "an action, ticket or transfer happened"
    intents = {t["intent"] for t in packet["turns"]}
    if intents & {"fraud", "action", "issue"}:
        return "fraud, an action or a complaint was in the call"
    if packet["turns"] and packet["turns"][-1]["outcome"] in ("clarify", "confirm", "auth_required"):
        return "the caller hung up mid-task"
    return "random sample of clean calls" if rng.random() < SAMPLE_RATE else None


def verify_evidence(judge: dict, packet: dict) -> tuple[list[dict], int]:
    """Keep only evidence whose quote really is in the transcript turn it names. The same quote given for several
    dimensions is kept once, with all its dimensions (a model often repeats one sentence for every score)."""
    by_turn = {t["n"]: _norm(f"{t['caller']} {t['agent']}") for t in packet["turns"]}
    real = [e for e in judge.get("evidence", []) if e["turn"] in by_turn and _norm(e["quote"]) and _norm(e["quote"]) in by_turn[e["turn"]]]
    merged: dict[tuple, dict] = {}
    for e in real:
        key = (e["turn"], _norm(e["quote"]))
        if key in merged:
            if e["dimension"] not in merged[key]["dimensions"]:
                merged[key]["dimensions"].append(e["dimension"])
        else:
            merged[key] = e | {"dimensions": [e["dimension"]]}
    return list(merged.values()), len(judge.get("evidence", [])) - len(real)


def verify_findings(judge: dict, packet: dict) -> tuple[list[dict], int]:
    """A finding whose quote is not in the turn it names keeps its text but loses the quote (marked unverified):
    the problem may still be real, but nothing in it is presented as a transcript quote unless it is one."""
    by_turn = {t["n"]: _norm(f"{t['caller']} {t['agent']}") for t in packet["turns"]}
    out, unverified = [], 0
    for f in [f for f in (judge.get("findings") or []) if isinstance(f, dict) and (f.get("what_happened") or f.get("fix"))][:MAX_FINDINGS]:
        # The model does not always follow the schema: an unknown or missing label becomes "other", missing text becomes "".
        f = f | {"category": f.get("category") if f.get("category") in CATEGORIES else "other",
                 "source": f.get("source") if f.get("source") in SOURCES else "agent",
                 "fix_area": f.get("fix_area") if f.get("fix_area") in FIX_AREAS else "none",
                 "what_happened": f.get("what_happened") or "", "why_it_matters": f.get("why_it_matters") or "", "fix": f.get("fix") or ""}
        ok = f.get("turn") in by_turn and _norm(f.get("quote", "")) and _norm(f["quote"]) in by_turn[f["turn"]]
        if not ok:
            unverified += 1
        out.append(f | {"quote": f.get("quote", "") if ok else "", "quote_verified": bool(ok)})
    return out, unverified


def categories_of(findings: list[dict]) -> list[str]:
    """The call's failure labels: one per category found, the agent's own first."""
    agent = [f["category"] for f in findings if f.get("source") == "agent"]
    other = [f["category"] for f in findings if f.get("source") != "agent"]
    return list(dict.fromkeys(agent + other))


class ReviewService:
    def __init__(self, db: Database, audit: AuditStore, llm, settings):
        self.db, self.audit, self.llm, self.s = db, audit, llm, settings
        self.rng = random.Random()

    async def review(self, session_id: str, *, force_judge: bool = False) -> dict | None:
        import asyncio

        call = await asyncio.to_thread(self.audit.call, session_id)
        if not call or not call["turns"]:
            return None
        packet = build_packet(call)
        checks = code_checks(call, packet)
        reason = "requested" if force_judge else judge_reason(call, packet, checks, self.s.judge_mode, self.rng)
        hard_fail = any(not c.ok and c.severity == "fail" for c in checks)  # e.g. account data before verification
        judge, model, score, critical, attention = None, None, None, hard_fail, hard_fail
        if reason:
            try:
                judge, model = await self._judge(packet, checks)
                scores = [v for v in judge["dimension_scores"].values() if v]
                score = round(sum(scores) / len(scores), 1) if scores else None
                critical = hard_fail or bool(judge["critical_failure"])  # either is enough; a good average never hides it
                attention = attention or critical or bool(judge["needs_human_review"])
            except Exception as e:  # a judge problem must never lose the free checks
                logger.exception("call judge failed")
                reason = f"{reason}; the judge failed ({type(e).__name__})"
        row = {"mode": "judged" if judge else "code_only", "reason": reason or "clean call, not sampled",
               "checks": [c.to_dict() for c in checks], "judge": judge, "model": model, "score": score, "critical": critical,
               "needs_review": attention}
        await asyncio.to_thread(self._save, session_id, row)
        return row | {"session_id": session_id}

    async def _judge(self, packet: dict, checks: list[Check]) -> tuple[dict, str]:
        transcript = "\n".join((f"[{t['n']}] SYSTEM (re-asked the caller's question after verification; the caller did not "
                                 f"repeat it): {t['caller']}" if t["input"] == REASK else f"[{t['n']}] CALLER ({t['input']}): {t['caller']}")
                               + f"\n    AGENT ({t['outcome']}, {t['intent']}): {t['agent']}"
                               + (f"\n    bank data used: {', '.join(t['bank_fields'])}" if t["bank_fields"] else "")
                               + (f"\n    sources: {'; '.join(t['sources'])}" if t["sources"] else "")
                               + "".join(f"\n    source text: {x}" for x in t.get("evidence") or []) for t in packet["turns"])
        notes = [c.detail for c in checks if not c.ok and c.detail]
        user = (f"Language: {packet['language']}. Caller verified: {packet['verified']}. Events: "
                f"{', '.join(sorted({e['kind'] for e in packet['events']})) or 'none'}.\n"
                + (f"Automatic checks flagged: {'; '.join(notes)}\n" if notes else "") + f"\nTranscript:\n{transcript}")[:9000]
        data, _ = await self.llm.json(self.s.judge_model, current().prompts["judge"], user, JUDGE_SCHEMA)
        kept, dropped = verify_evidence(data, packet)
        data["evidence"], data["dropped_evidence"] = kept, dropped
        data["findings"], data["unverified_findings"] = verify_findings(data, packet)
        data["failure_categories"] = categories_of(data["findings"])
        if data.get("critical_failure") and not kept:  # a serious claim with nothing checkable behind it: a person must look
            data["needs_human_review"] = True
            data["failure_categories"] = list(dict.fromkeys([*data.get("failure_categories", []), "other"]))
        return data, self.s.judge_model

    def _save(self, session_id: str, row: dict) -> None:
        with self.db.connection() as conn, conn.transaction():
            self._sync_fixes(conn, session_id, (row.get("judge") or {}).get("findings") or [])
            conn.execute(
                "INSERT INTO call_reviews (session_id, mode, reason, checks, judge, model, score, critical, needs_review) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (session_id) DO UPDATE SET created_at = now(), mode = EXCLUDED.mode, "
                "reason = EXCLUDED.reason, checks = EXCLUDED.checks, judge = EXCLUDED.judge, model = EXCLUDED.model, "
                "score = EXCLUDED.score, critical = EXCLUDED.critical, needs_review = EXCLUDED.needs_review",
                (session_id, row["mode"], row["reason"], json.dumps(row["checks"]), json.dumps(row["judge"]) if row["judge"] else None,
                 row["model"], row["score"], row["critical"], row["needs_review"]))

    @staticmethod
    def _sync_fixes(conn, session_id: str, findings: list[dict]) -> None:
        """Each finding becomes a to-do on the fix list. A new review of the same call replaces its open to-dos; ones a
        person already marked done or won't-fix are kept (and not added again for the same turn and label)."""
        conn.execute("DELETE FROM fix_items WHERE session_id = %s AND status = 'open'", (session_id,))
        closed = {(r["turn"], r["category"]) for r in conn.execute(
            "SELECT turn, category FROM fix_items WHERE session_id = %s", (session_id,)).fetchall()}
        for f in findings:
            if (f.get("turn"), f["category"]) in closed:
                continue
            conn.execute("INSERT INTO fix_items (session_id, turn, category, source, fix_area, what_happened, why_it_matters, "
                         "fix, quote) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (session_id, f.get("turn"), f["category"], f.get("source", "agent"), f.get("fix_area", "none"),
                          f["what_happened"], f.get("why_it_matters", ""), f["fix"], f.get("quote", "")))

    def fixes(self, status: str | None = "open", limit: int = 200) -> list[dict]:
        with self.db.connection() as conn:
            where, args = ("WHERE status = %s", (status,)) if status else ("", ())
            return conn.execute(f"SELECT * FROM fix_items {where} ORDER BY (source = 'agent') DESC, created_at DESC LIMIT %s",
                                (*args, limit)).fetchall()

    def set_fix(self, fix_id: int, status: str, note: str = "") -> dict | None:
        if status not in ("open", "done", "wont_fix"):
            raise ValueError("status must be open, done or wont_fix")
        with self.db.connection() as conn:
            return conn.execute("UPDATE fix_items SET status = %s, status_note = %s, status_at = now() WHERE id = %s RETURNING *",
                                (status, note[:500], fix_id)).fetchone()

    def get(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM call_reviews WHERE session_id = %s", (session_id,)).fetchone()

    def label(self, session_id: str, scores: dict, critical: bool, note: str) -> dict | None:
        """A person's scores for the same dimensions: the calibration data."""
        human = {"scores": {d: int(v) for d, v in scores.items() if d in DIMENSIONS}, "critical": critical, "note": note[:500]}
        with self.db.connection() as conn:
            row = conn.execute("UPDATE call_reviews SET human = %s, needs_review = false WHERE session_id = %s RETURNING *",
                               (json.dumps(human), session_id)).fetchone()
        return row

    def summary(self, days: int = 30) -> dict:
        """Quality across calls: how many were reviewed, average scores, critical rate, and which problems keep coming back
        (by label, by source and by where to fix them), with a recent example of each - the biggest recurring problem first."""
        with self.db.connection() as conn:
            rows = conn.execute("SELECT session_id, created_at, judge, score, critical, needs_review FROM call_reviews "
                                "WHERE created_at > now() - make_interval(days => %s) ORDER BY created_at DESC", (days,)).fetchall()
        judged = [r for r in rows if r["judge"]]
        scores = {d: [r["judge"]["dimension_scores"].get(d) for r in judged if r["judge"]["dimension_scores"].get(d)]
                  for d in DIMENSIONS}
        issues: dict[str, dict] = {}
        by_source: dict[str, int] = {}
        by_area: dict[str, int] = {}
        for r in judged:
            for f in r["judge"].get("findings") or []:
                key = f["category"]
                item = issues.setdefault(key, {"category": key, "calls": set(), "agent": 0, "example": None})
                item["calls"].add(str(r["session_id"]))
                item["agent"] += f.get("source") == "agent"
                item["example"] = item["example"] or {"session_id": str(r["session_id"]), "what_happened": f["what_happened"],
                                                      "fix": f["fix"], "fix_area": f["fix_area"]}
                by_source[f.get("source", "agent")] = by_source.get(f.get("source", "agent"), 0) + 1
                if f.get("source") == "agent":
                    by_area[f.get("fix_area", "none")] = by_area.get(f.get("fix_area", "none"), 0) + 1
        top = sorted(issues.values(), key=lambda i: (len(i["calls"]), i["agent"]), reverse=True)
        return {"days": days, "reviewed_calls": len(rows), "judged_calls": len(judged),
                "critical_calls": sum(bool(r["critical"]) for r in rows),
                "needs_review": sum(bool(r["needs_review"]) for r in rows),
                "average_score": round(sum(r["score"] for r in judged if r["score"]) / max(1, sum(1 for r in judged if r["score"])), 2)
                if judged else None,
                "average_by_dimension": {d: (round(sum(v) / len(v), 2) if v else None) for d, v in scores.items()},
                "recurring_issues": [{"category": i["category"], "calls": len(i["calls"]), "agent_caused": i["agent"],
                                      "example": i["example"]} for i in top],
                "issues_by_source": by_source, "agent_issues_by_fix_area": by_area}

    def calibration(self) -> dict:
        """How well the judge agrees with people: within one point per dimension, critical-failure recall, false positives."""
        with self.db.connection() as conn:
            rows = conn.execute("SELECT session_id, judge, critical, human FROM call_reviews "
                                "WHERE human IS NOT NULL AND judge IS NOT NULL").fetchall()
        diffs = {d: [] for d in DIMENSIONS}  # judge minus person, per call
        tp = fn = fp = 0
        missed = []
        for r in rows:
            for d in DIMENSIONS:
                mine, theirs = r["judge"]["dimension_scores"].get(d), r["human"]["scores"].get(d)
                if mine and theirs:
                    diffs[d].append(mine - theirs)
            tp += bool(r["critical"] and r["human"]["critical"])
            fp += bool(r["critical"] and not r["human"]["critical"])
            if not r["critical"] and r["human"]["critical"]:
                fn += 1
                missed.append(str(r["session_id"]))

        def pct(values, ok) -> int | None:
            return round(100 * sum(ok(v) for v in values) / len(values)) if values else None

        return {"labelled_calls": len(rows),
                "exact_agreement": {d: pct(v, lambda x: x == 0) for d, v in diffs.items()},
                "agreement_within_one_point": {d: pct(v, lambda x: abs(x) <= 1) for d, v in diffs.items()},
                # above 0 = the judge scores higher than people (too generous); below 0 = too harsh
                "judge_minus_human": {d: (round(sum(v) / len(v), 2) if v else None) for d, v in diffs.items()},
                "critical_failure_recall": round(100 * tp / (tp + fn)) if (tp + fn) else None,
                "missed_critical_calls": missed, "false_positives": fp,
                "target": "85% or more agreement within one point per dimension; critical-failure recall must be 100% "
                          "(no missed critical calls); |judge_minus_human| under 0.5. Start with about 50 labelled calls "
                          "of every kind (good, bad, Hindi / Hinglish, fraud, verification, handoffs) and re-check after "
                          "every prompt or model change."}
