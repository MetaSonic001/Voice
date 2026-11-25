"""The AI summary a person sees when a call is handed over (PHASE-4-PLAN §12).

Generated in the background right after the transfer starts (the caller is never made to wait), from the redacted
transcript and the facts of the call. Three to five lines: the goal, what was answered, what was done, what is still open
and the caller's mood. **Every figure in it must exist in the record**; if one does not, the summary is discarded and the
desk shows the deterministic brief alone. It is always labelled "AI summary"; the brief stays the trusted part.
"""

import asyncio
import json
import logging

from app.services.content import current
from app.services.numbers import allowed_numbers, unsupported_numbers

logger = logging.getLogger(__name__)
SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "mood": {"type": "string", "enum": ["calm", "worried", "frustrated", "angry"]}},
    "required": ["summary", "mood"], "additionalProperties": False,
}


class Summariser:
    def __init__(self, llm, model: str, audit, handoffs):
        self.llm, self.model, self.audit, self.handoffs = llm, model, audit, handoffs

    async def summarise(self, session_id: str, handoff_id: str) -> dict | None:
        call = await asyncio.to_thread(self.audit.call, session_id)
        if not call or not call["turns"]:
            return None
        transcript = "\n".join(f"Caller: {t['question']}\nAgent: {t['answer']}" for t in call["turns"][-12:])
        record = json.dumps({"verified": bool(call.get("customer_id")), "facts": call.get("facts") or {},
                             "events": [{"kind": e["kind"], "detail": e["detail"]} for e in call["events"]
                                        if e["kind"] in ("card_action", "ticket", "handoff_requested")]}, default=str)
        try:
            data, _ = await self.llm.json(self.model, current().prompts["summary"], f"Record: {record}\n\nTranscript:\n{transcript}"[:8000], SCHEMA)
        except Exception:
            logger.exception("handover summary failed")
            return None
        allowed = allowed_numbers(transcript, record)
        bad = unsupported_numbers(data["summary"], allowed)
        if bad:  # a figure the record does not contain: show the trusted brief only
            logger.warning("handover summary discarded: figures not in the record", extra={"data": {"figures": bad}})
            return None
        summary = {"text": data["summary"].strip(), "mood": data["mood"], "model": self.model, "label": "AI summary"}
        await asyncio.to_thread(self.handoffs.add_note, handoff_id, {"ai_summary": summary})
        return summary
