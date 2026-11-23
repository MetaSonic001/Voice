"""Call audit: tamper-evident turns, grounding level, call timeline, alerts, and the readable call record.

Tamper evidence: every turn stores hash = SHA-256(previous turn's hash + this turn's content), and the session
keeps the latest hash. Editing a turn, deleting one, or deleting the last one all break the chain, which
`verify_chain` reports. Only fields fixed when the turn is saved are hashed; `data.voice` (playback timings added
after the reply was spoken) and `data.feedback` (a tester's verdict) are added later and are not part of it.

Grounding level: a rule-based label for how well an answer is backed, built from measured signals (how well the
best cited passage matched, the figure checks, how clearly the caller was heard). It is shown to reviewers and
counted by alerts; it never changes what the agent says.
"""

import hashlib
import json
import logging
import time
from collections import deque
from datetime import UTC, datetime

from app.db import Database
from app.logs import log

logger = logging.getLogger(__name__)
transcript_log = logging.getLogger("app.transcript")

MUTABLE_DATA_KEYS = ("voice", "feedback")  # added to a turn after it is saved: not hashed
HIGH, MEDIUM = 0.5, 0.15  # reranker score (0-1) of the best cited passage
UNCLEAR_SPEECH = 0.4  # STT no-speech probability above which the caller was not heard clearly


# ───────── tamper evidence ─────────

def turn_hash(prev_hash: str, trace: dict, created_at: datetime) -> str:
    data = {k: v for k, v in json.loads(json.dumps(trace["data"], default=str)).items() if k not in MUTABLE_DATA_KEYS}
    content = {
        "id": str(trace["id"]), "session_id": str(trace["session_id"]) if trace.get("session_id") else None,
        "created_at": created_at.astimezone(UTC).isoformat(), "question": trace["question"],
        "language": trace.get("language"), "intent": trace.get("intent"), "outcome": trace["outcome"],
        "answer": trace["answer"], "total_ms": trace["total_ms"], "data": data,
    }
    canonical = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


def verify_chain(session: dict, turns: list[dict]) -> dict:
    """'intact' | 'broken' (with the first bad turn) | 'not_sealed' (no hashed turns yet)."""
    prev, checked = "", 0
    for n, t in enumerate(turns, start=1):
        if t.get("hash") is None:  # saved before tamper evidence existed
            continue
        if (t.get("prev_hash") or "") != prev:
            return {"status": "broken", "turn": n, "problem": "a turn before this one was removed or reordered"}
        if turn_hash(prev, t, t["created_at"]) != t["hash"]:
            return {"status": "broken", "turn": n, "problem": "this turn was changed after it was saved"}
        prev, checked = t["hash"], checked + 1
    if checked == 0:
        return {"status": "not_sealed", "turns_checked": 0}
    if prev != (session.get("chain_hash") or "") or checked != session.get("chain_len"):
        return {"status": "broken", "turn": checked, "problem": "the latest turn(s) were removed"}
    return {"status": "intact", "turns_checked": checked, "head": prev}


# ───────── grounding ─────────

def grounding(outcome: str, intent: str, data: dict, citations: list[dict], timings: dict) -> dict:
    """{'level': high|medium|low|none|n/a, 'basis': [readable reasons]}"""
    heard = timings.get("stt_no_speech")
    unclear = heard is not None and heard >= UNCLEAR_SPEECH
    if outcome == "error":
        return {"level": "none", "basis": ["system error: no answer was given"]}
    if outcome == "answered" and data.get("personal"):
        basis = ["bank records, fixed template (no AI wording)", "caller verified"]
        return {"level": "low" if unclear else "high", "basis": basis + (["caller was not heard clearly"] if unclear else [])}
    if outcome == "answered" and citations:
        retrieval = data.get("retrieval") or {}
        if retrieval.get("reranker") == "off":  # RERANK=false: no relevance score, so never "high"
            best = retrieval.get("closest_dense") or 0.0
            level = "medium"
            basis = [f"no reranker: closest passage by meaning {best:.2f}", "all figures verified in the source"]
        else:
            scores = {h["chunk_id"]: h.get("rerank_score") or 0.0 for h in retrieval.get("reranked", [])}
            best = max((scores.get(c.get("chunk_id"), 0.0) for c in citations), default=0.0)
            level = "high" if best >= HIGH else "medium" if best >= MEDIUM else "low"
            basis = [f"source match {best:.2f}", "all figures verified in the source"]
        if unclear:
            level = {"high": "medium", "medium": "low"}.get(level, "low")
            basis.append("caller was not heard clearly")
        return {"level": level, "basis": basis, "score": round(best, 3)}
    if outcome == "refused":
        return {"level": "none", "basis": [f"not answered: {data.get('reason') or 'no reliable source'}"]}
    return {"level": "n/a", "basis": ["fixed reply (no source needed)"]}


# ───────── store: events, alerts, call record ─────────

class AuditStore:
    def __init__(self, db: Database):
        self.db = db
        self.records = None  # CallRecords (services/callrecords.py), set by the container: rebuilds calls whose rows were removed
        self._errors: deque[float] = deque(maxlen=20)  # recent system errors (for the health alert)
        self._last_health_alert = 0.0

    def event(self, session_id: str, event_type: str, /, **detail) -> None:
        with self.db.connection() as conn:
            conn.execute("INSERT INTO call_events (session_id, kind, detail) VALUES (%s, %s, %s)",
                         (session_id, event_type, json.dumps(detail, default=str)))

    def end_call(self, session_id: str, reason: str) -> None:
        with self.db.connection() as conn:
            conn.execute("UPDATE sessions SET ended_at = now(), end_reason = %s WHERE id = %s", (reason, session_id))
        self.event(session_id, "call_ended", reason=reason)

    # alerts ------------------------------------------------------------------------------------------------
    def _alert(self, session_id: str | None, kind: str, severity: str, message: str, once: bool = True, **detail) -> None:
        with self.db.connection() as conn:
            if once and session_id and conn.execute("SELECT 1 FROM alerts WHERE session_id = %s AND kind = %s",
                                                    (session_id, kind)).fetchone():
                return
            conn.execute("INSERT INTO alerts (session_id, kind, severity, message, detail) VALUES (%s,%s,%s,%s,%s)",
                         (session_id, kind, severity, message, json.dumps(detail, default=str)))
        logger.warning("alert", extra={"data": {"kind": kind, "session_id": session_id, "message": message}})

    def after_turn(self, session_id: str | None, trace: dict, level: str) -> None:
        """Alerts that follow from a saved turn (runs off the event loop, after the reply was produced)."""
        if trace["outcome"] == "error":
            self.system_error("turn", trace["data"].get("error", "error"), session_id)
        if not session_id:
            return
        if trace.get("intent") == "human":
            self._alert(session_id, "wants_human", "high", "Caller asked to speak to a person",
                        question=trace["question"][:200])
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT count(*) FILTER (WHERE outcome IN ('refused', 'error')) AS failed, "
                "count(*) FILTER (WHERE data->'grounding'->>'level' = 'low') AS low FROM traces WHERE session_id = %s",
                (session_id,)).fetchone()
        if row["failed"] >= 2 or row["low"] >= 3:
            self._alert(session_id, "struggling", "medium",
                        f"The agent is struggling: {row['failed']} unanswered and {row['low']} weakly grounded replies",
                        unanswered=row["failed"], low_grounding=row["low"])

    def system_error(self, source: str, error: str, session_id: str | None = None) -> None:
        """Raise one health alert when 2+ system errors (LLM, STT, TTS) happen within 5 minutes."""
        now = time.monotonic()
        self._errors.append(now)
        recent = sum(1 for t in self._errors if now - t < 300)
        if recent >= 2 and now - self._last_health_alert > 300:
            self._last_health_alert = now
            self._alert(session_id, "system_health", "critical",
                        f"System health: {recent} errors in the last 5 minutes (latest: {source})",
                        once=False, source=source, error=str(error)[:300])

    def alerts_after(self, after_id: int, limit: int = 20) -> list[dict]:
        with self.db.connection() as conn:
            if after_id < 0:  # just the newest id, so a freshly opened page shows only new alerts
                row = conn.execute("SELECT coalesce(max(id), 0) AS id FROM alerts").fetchone()
                return [{"id": row["id"], "kind": "cursor"}]
            return conn.execute("SELECT id, session_id, created_at, kind, severity, message FROM alerts WHERE id > %s "
                                "ORDER BY id LIMIT %s", (after_id, limit)).fetchall()

    # call record -------------------------------------------------------------------------------------------
    def calls(self, limit: int = 50, before: str | None = None, q: str | None = None, needs_review: bool = False) -> list[dict]:
        """Sessions that had at least one turn or recording, newest first."""
        where, args = ["(s.chain_len > 0 OR EXISTS (SELECT 1 FROM traces t WHERE t.session_id = s.id) "
                       "OR EXISTS (SELECT 1 FROM recordings r WHERE r.session_id = s.id))"], []
        if before:
            where.append("s.created_at < %s")
            args.append(before)
        if needs_review:
            where.append("rv.needs_review")
        if q:
            where.append("(s.id::text ILIKE %s OR s.caller_number ILIKE %s OR EXISTS (SELECT 1 FROM traces t "
                         "WHERE t.session_id = s.id AND (t.question ILIKE %s OR t.answer ILIKE %s)))")
            args += [f"{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT s.id, s.created_at, s.ended_at, s.end_reason, s.language, s.caller_number, s.verified_at, "
                "s.purged_at IS NOT NULL AS rows_removed, s.summary, "
                "s.customer_id, t.turns, t.first_at, t.last_at, t.answered, t.unanswered, t.last_question, "
                "(SELECT count(*) FROM alerts a WHERE a.session_id = s.id) AS alerts, "
                "(SELECT count(*) FROM recordings r WHERE r.session_id = s.id AND r.path IS NOT NULL) AS recordings, "
                "(SELECT kind FROM recordings r WHERE r.session_id = s.id AND r.kind = 'call' LIMIT 1) IS NOT NULL AS live, "
                "rv.score AS review_score, rv.critical AS review_critical, rv.needs_review AS review_needs_attention, "
                "rv.mode AS review_mode, rv.human IS NOT NULL AS review_labelled "
                "FROM sessions s LEFT JOIN call_reviews rv ON rv.session_id = s.id LEFT JOIN LATERAL (SELECT count(*) AS turns, min(created_at) AS first_at, "
                "max(created_at) AS last_at, count(*) FILTER (WHERE outcome = 'answered') AS answered, "
                "count(*) FILTER (WHERE outcome IN ('refused', 'error')) AS unanswered, "
                "(array_agg(question ORDER BY created_at DESC))[1] AS last_question "
                "FROM traces WHERE session_id = s.id) t ON true "
                "WHERE " + " AND ".join(where) + " ORDER BY s.created_at DESC LIMIT %s",
                (*args, limit)).fetchall()
        for r in rows:  # turns removed from Postgres (CALL_ROWS_DAYS): the counts saved at export stand in
            summary = r.pop("summary") or {}
            if r["rows_removed"]:
                r.update({k: summary.get(k) for k in ("turns", "first_at", "last_at", "answered", "unanswered")})
        return rows

    def call(self, session_id: str) -> dict | None:
        """The readable call record. After its rows were removed from Postgres (CALL_ROWS_DAYS), it is rebuilt from the
        exported record file (services/callrecords.py) and looks the same; recordings and alerts still come from here."""
        rows = self.call_rows(session_id)
        if rows is None:
            return None
        if rows.get("purged_at") and self.records is not None:
            stored = self.records.load(rows.get("record_key"))
            if stored is not None:
                stored["recordings"], stored["alerts"] = rows["recordings"], rows["alerts"]
                return call_view(stored)
        return call_view(rows)

    def call_rows(self, session_id: str) -> dict | None:
        """Everything recorded about one call, as stored (turns with their hashes): what the record file holds."""
        with self.db.connection() as conn:
            s = conn.execute("SELECT id, created_at, ended_at, end_reason, language, caller_number, customer_id, "
                             "verified_at, facts, chain_hash, chain_len, state->'agenda' AS agenda, record_key, "
                             "exported_at, purged_at FROM sessions WHERE id = %s", (session_id,)).fetchone()
            if not s:
                return None
            s["turns"] = conn.execute("SELECT * FROM traces WHERE session_id = %s ORDER BY created_at, id",
                                      (session_id,)).fetchall()
            s["auth_events"] = conn.execute("SELECT created_at, event, detail FROM auth_events WHERE session_id = %s "
                                            "ORDER BY created_at", (session_id,)).fetchall()
            s["events"] = conn.execute("SELECT created_at, kind, detail FROM call_events WHERE session_id = %s "
                                       "ORDER BY created_at", (session_id,)).fetchall()
            s["alerts"] = conn.execute("SELECT id, created_at, kind, severity, message FROM alerts WHERE session_id = %s "
                                       "ORDER BY created_at", (session_id,)).fetchall()
            s["recordings"] = conn.execute(
                "SELECT id, trace_id, kind, started_at, duration_ms, format, bytes, sha256, deleted_at, path IS NOT NULL AS available "
                "FROM recordings WHERE session_id = %s ORDER BY started_at", (session_id,)).fetchall()
        return s


def call_view(s: dict) -> dict:
    """The call record page from the stored rows (Postgres, or the exported record file)."""
    s = {k: v for k, v in s.items() if k not in ("record_key", "exported_at", "purged_at")}
    turns = s.pop("turns")
    # what the caller asked on this call and what became of each (services/agenda.py): labels and statuses only
    s["agenda"] = [{"id": i.get("id"), "label": i.get("label") or "", "kind": (i.get("req") or {}).get("kind", ""),
                    "status": i.get("status")} for i in (s.get("agenda") or []) if isinstance(i, dict)]
    s["integrity"] = verify_chain(s, turns)
    s["turns"] = [_turn_view(t) for t in turns]
    for t in s["turns"]:
        t["why"] = why(t)
    return s


def _turn_view(t: dict) -> dict:
    """One turn for the call record: what was said, and why the agent said it (no raw model payloads)."""
    d = t["data"] or {}
    u = d.get("understanding") or {}
    retrieval = d.get("retrieval") or {}
    used = set(d.get("context_chunk_ids") or [])
    timings = d.get("timings") or {}
    voice = d.get("voice") or {}
    return {
        "id": t["id"], "created_at": t["created_at"], "question": t["question"], "answer": t["answer"],
        "language": t["language"], "intent": t["intent"], "outcome": t["outcome"], "total_ms": t["total_ms"],
        "input": timings.get("input") or voice.get("input") or ("speech" if "stt_ms" in timings else "typed"),
        "route": d.get("route"), "reason": d.get("reason"), "bank_calls": d.get("bank_calls") or [],
        "understood": {"question": u.get("standalone_question"), "search": u.get("search_query_en"),
                       "personal": u.get("personal") or None, "facts": u.get("facts") or None,
                       # every request the turn was split into, as run (after the code corrections), not just the first
                       "requests": [{"question": r.get("question"), "kind": r.get("kind"),
                                     "topic": (r.get("personal") or {}).get("topic") or r.get("product") or "",
                                     "detail": (r.get("personal") or {}).get("detail") or ""}
                                    for r in u.get("requests") or []]},
        "sources": [{"chunk_id": h["chunk_id"], "document": h.get("document"), "section": h.get("section"),
                     "score": h.get("rerank_score"), "snippet": (h.get("text") or "")[:300]}
                    for h in retrieval.get("reranked", []) if h["chunk_id"] in used],
        "citations": d.get("citations") or [],
        "checks": d.get("checks") or [],
        "bank": d.get("personal"),
        "grounding": d.get("grounding"),
        "timings": {k: v for k, v in {**timings, **voice}.items() if isinstance(v, (int, float)) and k.endswith("_ms")},
        "stt_no_speech": timings.get("stt_no_speech"),
        "prompt_version": d.get("prompt_version"), "models": d.get("models"),
        "feedback": d.get("feedback"), "hash": t.get("hash"), "journey": d.get("journey"),
        "long_reply": d.get("long_reply"),
        "fallback": bool((d.get("answer_llm") or {}).get("fallback_for") or (d.get("understand_llm") or {}).get("fallback_for")),
    }


REASONS = {"low_relevance": "no relevant passage was found in the knowledge base",
           "not_in_sources": "the knowledge base does not contain the answer",
           "figure_check_failed": "a figure could not be verified against the source",
           "unsafe_output": "the reply asked for a secret or gave a link not in the sources (blocked)",
           "uncited": "the answer did not point to a source", "malformed_output": "the AI output was malformed",
           "internal_error": "a system error"}
AUTH_EVENTS = {"mobile_requested": "Asked for the registered mobile number", "mobile_not_registered": "Number is not registered",
               "otp_sent": "One-time code sent", "otp_reused": "Same code kept for the continued question", "otp_failed": "Wrong code entered", "otp_expired": "Code expired",
               "locked": "Verification locked after too many wrong codes", "otp_verified": "Caller verified",
               "too_many_codes": "Too many codes requested", "verification_ended": "Verification ended",
               "verification_expired": "Verification expired (idle)", "data_read": "Account data read"}
LANG = {"en": "English", "hi": "Hindi", "hinglish": "Hinglish"}


def why(turn: dict) -> list[str]:
    """Plain-language reasons for one reply, built from what was recorded at the time (evidence, not a summary)."""
    lines: list[str] = []
    u, outcome, intent = turn["understood"], turn["outcome"], turn["intent"] or "unknown"
    requests = u.get("requests") or []
    if len(requests) > 1:  # PM review: "Understood as: balance" hid the transactions request that was also answered
        parts = "; ".join(f"\"{r['question']}\" ({r['kind']}{': ' + r['topic'] if r['topic'] else ''}"
                          f"{', ' + r['detail'] if r['detail'] else ''})" for r in requests)
        lines.append(f"Understood as {len(requests)} requests ({LANG.get(turn['language'], turn['language'])}): {parts}")
    elif u.get("question"):
        detail = (u.get("personal") or {}).get("detail") or ""
        lines.append(f"Understood as: \"{u['question']}\" ({LANG.get(turn['language'], turn['language'])}, {intent}"
                     f"{', ' + detail if detail else ''})")
    bank = turn.get("bank") or {}
    journey = turn.get("journey") or {}
    action = journey.get("action") or {}
    card = f"{action.get('kind', '')} card ending {action.get('last4', '')}".strip()
    if outcome == "confirm":
        waiting = journey.get("waiting_for", "")
        lines.append({"action": "Asked the caller to confirm before changing anything (nothing changed yet).",
                      "handoff": "Asked the caller whether to transfer them to a person.",
                      "ticket": "Asked the caller whether to raise a ticket.",
                      "fraud_followup": "Card protected; asked whether to raise a dispute and transfer to the fraud team.",
                      "lead": "Offered a callback to help open an account."}.get(waiting, "Asked the caller to confirm."))
    elif outcome == "action_done":
        result = journey.get("result") or {}
        lines.append(f"The caller said yes, so the {action.get('action', '').replace('_', ' ')} of the {card} was done; "
                     f"the bank system's state was read back and matched (reference {result.get('reference')}).")
    elif outcome == "action_failed":
        lines.append(f"The {card} change did not complete in the bank system, so the caller was told nothing changed "
                     "and was offered the cards team.")
    elif outcome == "cancelled":
        lines.append("The caller said no, so nothing was done.")
    elif outcome == "ticket":
        ticket = journey.get("ticket") or {}
        lines.append(f"The caller agreed, so ticket {ticket.get('id')} was raised for the {ticket.get('department')} team "
                     f"(due by {ticket.get('due_by')}).")
    elif outcome in ("handoff", "callback") and journey:
        h = journey.get("handoff") or {}
        ticket = journey.get("ticket") or {}
        lines.append(f"The caller agreed to be transferred: waiting for the {h.get('department')} team (priority "
                     f"{h.get('priority')})." if h else f"The team was closed, so callback {ticket.get('id')} was booked.")
    elif outcome == "interrupted":
        lines.append("The caller interrupted before the reply was finished.")
    elif outcome == "error":
        lines.append("A system error occurred: the caller was told and offered a specialist.")
    elif bank and intent == "personal":
        fields = bank.get("fields") or []
        lines.append("Personal question, caller verified: answered from the bank's records"
                     + (f" ({', '.join(fields)})" if fields else "") + " using a fixed template.")
    elif turn.get("route") == "verify" or outcome in ("auth_required", "needs_verification"):
        lines.append("Personal question, caller not verified yet: verification started (no account data was given).")
    elif outcome == "answered":
        lines.append("General question: answered from the knowledge base.")
    elif outcome == "refused":
        lines.append(f"Not answered because {REASONS.get(turn.get('reason'), turn.get('reason') or 'no reliable source')}; "
                     "a specialist was offered.")
    elif outcome == "clarify":
        lines.append("The question was ambiguous: the agent asked the caller to clarify.")
    elif intent == "human":
        lines.append("The caller asked for a person: a transfer was offered.")
    elif intent in ("action", "issue"):
        lines.append("This needs a person (a request or a complaint): a transfer was offered.")
    elif intent == "manipulation":
        lines.append("Possible manipulation attempt: a fixed safe reply was given.")
    else:
        lines.append(f"Fixed reply ({intent.replace('_', ' ')}).")
    scores = {s["chunk_id"]: s.get("score") for s in turn.get("sources") or []}
    for c in turn.get("citations") or []:
        if c.get("chunk_id"):
            score = scores.get(c["chunk_id"])
            lines.append(f"Source [{c.get('marker')}]: {c.get('document')} \u203a {c.get('section')}"
                         + (f" (valid from {c['effective_from']})" if c.get("effective_from") else "")
                         + (f", match {score:.2f}" if isinstance(score, (int, float)) else ""))
    checks = turn.get("checks") or []
    if checks and outcome == "answered":
        bad = [n for c in checks for n in c.get("unsupported_numbers") or []]
        lines.append("Checks: every figure was found in the cited source." if not bad
                     else f"Checks: figures not found in the source: {', '.join(map(str, bad))}")
    g = turn.get("grounding") or {}
    if g.get("level") not in (None, "n/a"):
        lines.append(f"Grounding: {g['level']} - {'; '.join(g.get('basis') or [])}")
    if turn.get("stt_no_speech") is not None and turn["stt_no_speech"] >= UNCLEAR_SPEECH:
        lines.append("Speech recognition was unsure what the caller said.")
    return lines


EVENT_TEXT = {"call_started": "Live call started", "disclosure": "AGENT (opening disclosure)",
              "interrupted": "Caller interrupted the agent", "error": "SYSTEM ERROR",
              "call_ended": "Call ended", "recording_deleted": "Recording deleted (retention policy)",
              "card_action": "CARD ACTION", "ticket": "TICKET RAISED", "handoff_requested": "TRANSFER requested",
              "on_hold": "Caller on hold", "agent_joined": "A person joined the call", "handoff_accepted": "Transfer accepted",
              "handoff_completed": "Transfer completed"}


def render_transcript(call: dict) -> str:
    """The call record as plain text: who said what, when, and why the agent replied as it did."""
    def ts(d) -> str:
        return d.astimezone().strftime("%H:%M:%S")

    items = [(t["created_at"], "turn", t) for t in call["turns"]]
    items += [(e["created_at"], "event", e) for e in call["events"] if e["kind"] in EVENT_TEXT]
    items += [(a["created_at"], "auth", a) for a in call["auth_events"]]
    items.sort(key=lambda x: x[0])
    integrity = call["integrity"]
    out = [f"AU Small Finance Bank - call record {call['id']}",
           f"Started    {call['created_at'].astimezone():%d %b %Y %H:%M:%S}"
           + (f"   Ended {ts(call['ended_at'])} ({call.get('end_reason')})" if call.get("ended_at") else ""),
           f"Caller     {mask_number(call.get('caller_number')) or 'unknown'}"
           + (f"   Verified at {ts(call['verified_at'])}" if call.get("verified_at") else "   Not verified"),
           f"Integrity  {integrity['status']}"
           + (f" - {integrity.get('problem')} (turn {integrity.get('turn')})" if integrity["status"] == "broken" else ""),
           f"Recordings {sum(1 for r in call['recordings'] if r['available'])}", ""]
    for at, kind, item in items:
        if kind == "turn":
            mode = {"speech": "spoken", "voice_note": "voice note", "text": "typed"}.get(item["input"], item["input"])
            out.append(f"{ts(at)}  CALLER ({mode}): {item['question']}")
            out.append(f"{'':10}AGENT: {item['answer'] or '(no reply: interrupted)'}")
            out += [f"{'':12}why: {line}" for line in item["why"]]
            out.append(f"{'':12}reply time: {item['total_ms'] / 1000:.1f} s")
        elif kind == "auth":
            out.append(f"{ts(at)}  VERIFICATION: {AUTH_EVENTS.get(item['event'], item['event'])}")
        else:
            d = item["detail"] or {}
            extra = d.get("text") or d.get("reason") or d.get("while_saying") or d.get("error") or ""
            if item["kind"] == "card_action":
                extra = f"{d.get('action')} {d.get('card')} {'done, ref ' + str(d.get('reference')) if d.get('done') else 'FAILED'}"
            elif item["kind"] == "ticket":
                extra = f"{d.get('id')} ({d.get('kind')}, {d.get('department')})"
            elif item["kind"] in ("handoff_requested", "on_hold"):
                extra = f"{d.get('department')}" + (f": {d.get('reason')}" if d.get("reason") else "")
            elif item["kind"] in ("agent_joined", "handoff_accepted", "handoff_completed"):
                extra = " ".join(str(x) for x in (d.get("agent"), d.get("disposition")) if x)
            out.append(f"{ts(at)}  {EVENT_TEXT[item['kind']]}" + (f": {extra}" if extra else ""))
        out.append("")
    out += [f"ALERT {ts(a['created_at'])} [{a['severity']}] {a['message']}" for a in call["alerts"]]
    return "\n".join(out).rstrip() + "\n"


def log_turn(session_id: str | None, trace_id: str, question: str, answer: str, intent: str, outcome: str,
             personal_fields: list | None, turn_hash_: str | None) -> None:
    """Transcript lines in the log (already masked). Account figures never go to logs: the call record has them."""
    said = f"[account details given: {', '.join(personal_fields)}]" if personal_fields else answer
    log(transcript_log, "caller", call=session_id, turn=trace_id, text=question)
    log(transcript_log, "agent", call=session_id, turn=trace_id, text=said, intent=intent, outcome=outcome,
        hash=(turn_hash_ or "")[:16])


def mask_number(number: str | None) -> str | None:
    if not number:
        return None
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"{digits[:2]}{'•' * max(0, len(digits) - 5)}{digits[-3:]}" if len(digits) > 5 else "•••"


def now_utc() -> datetime:
    return datetime.now(UTC)
