"""One explicit state record per call (PHASE-4-PLAN §3.2), kept in `sessions.state` (jsonb).

It holds what the chat history cannot reliably carry:
- agenda: every separate thing the caller asked, with its status (services/agenda.py). Nothing asked is ever dropped
  silently: an item is answered, declined or skipped by the caller, handed to a person, or offered and let go.
- done / declined: which tasks were completed or refused, so cancelling one never cancels another.
- focus: the account / card the caller is talking about ("this account", "that card", "the other one").
- last_list: the items of a list being read out, so "go on" continues it and "the second one" resolves.
- greeted: the greeting is said once per call.
- flow: a guided "help me choose" flow in progress: {"name", "asked": [fact keys], "key", "question"}.
- v: the version of this record. A save only succeeds if nobody saved in between (two turns of one call can overlap
  after a barge-in, and later on any copy of the brain); otherwise the newer record is read and the two are merged.
Verification, the yes/no question waiting and the caller's facts keep their own columns (auth, pending, facts).
There is no session restore after a dropped call: the next call verifies again.
"""

import json
import logging
from dataclasses import asdict, dataclass, field

from app.db import Database

logger = logging.getLogger(__name__)

# An item that reached one of these is settled; a save never moves it back to an open status.
FINAL = ("done", "declined", "skipped", "dropped", "handed_over", "failed")


@dataclass
class CallState:
    agenda: list[dict] = field(default_factory=list)  # agenda items (services/agenda.py), oldest first
    agenda_meta: dict = field(default_factory=dict)  # {"batch": n, "language", "department", "bye_reminded"}
    open_requests: list[dict] = field(default_factory=list)  # before 19 Nov 2026: read once into the agenda, then empty
    done: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    focus: dict = field(default_factory=dict)
    last_list: dict = field(default_factory=dict)  # {"kind": "transactions", "items": [...], "shown": 3}
    greeted: bool = False
    flow: dict = field(default_factory=dict)
    # the bank-data requests answered in the last turn (their data fields only, no figures): the next turn drops a request
    # the LLM copied from them (conversation.copied; calls a6545287, 5884ab26, d42aa7ed)
    answered: list[dict] = field(default_factory=list)
    v: int = 0

    def mark(self, name: str, declined: bool = False) -> None:
        target = self.declined if declined else self.done
        if name not in target:
            target.append(name)

    def focus_text(self) -> str:
        return ", ".join(f"{k} {v}" for k, v in self.focus.items() if v)

    @classmethod
    def from_dict(cls, d: dict | None) -> "CallState":
        d = d or {}
        state = cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})
        if state.open_requests and not state.agenda:  # a call that started before the agenda existed
            from app.services.agenda import from_open_requests
            from_open_requests(state)
        state.open_requests = []
        return state


def merge(ours: CallState, theirs: CallState) -> CallState:
    """Our record could not be saved because another turn of the same call saved first. Keep both turns' work: an agenda
    item settled by either turn stays settled, items only one turn added are kept, done / declined lists are joined,
    and everything else is ours (the latest turn's view). The version becomes theirs, so the next save can succeed."""
    mine = {i["id"]: i for i in ours.agenda}
    for item in theirs.agenda:
        own = mine.get(item["id"])
        if own is None:
            ours.agenda.append(item)
        elif item.get("status") in FINAL and own.get("status") not in FINAL:
            own["status"] = item["status"]
        if own is not None:
            own["offered"] = max(int(own.get("offered") or 0), int(item.get("offered") or 0))
    ours.agenda.sort(key=lambda i: i["id"])
    ours.agenda_meta["batch"] = max(int(ours.agenda_meta.get("batch") or 0), int(theirs.agenda_meta.get("batch") or 0))
    ours.done = list(dict.fromkeys(theirs.done + ours.done))
    ours.declined = list(dict.fromkeys(theirs.declined + ours.declined))
    ours.v = theirs.v
    return ours


class StateStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self, session_id: str | None) -> CallState:
        if not session_id:
            return CallState()
        with self.db.connection() as conn:
            row = conn.execute("SELECT state FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return CallState.from_dict(row["state"] if row else None)

    def save(self, session_id: str | None, state: CallState, tries: int = 3) -> bool:
        """Save only if the stored version is the one this turn read (optimistic locking, inside the existing jsonb
        column: no extra column or migration). On a clash: read the newer record, merge, try again. On success
        `state.v` is the new version, so the same turn can save again later."""
        if not session_id:
            return False
        for _ in range(tries):
            expected = state.v
            state.v = expected + 1
            body = json.dumps(asdict(state), default=str)
            with self.db.connection() as conn:
                saved = conn.execute("UPDATE sessions SET state = %s WHERE id = %s "
                                     "AND COALESCE((state->>'v')::int, 0) = %s", (body, session_id, expected)).rowcount
                row = None if saved else conn.execute("SELECT state FROM sessions WHERE id = %s", (session_id,)).fetchone()
            if saved:
                return True
            state.v = expected
            if row is None:  # the call was deleted: nothing to save into
                return False
            merge(state, CallState.from_dict(row["state"]))
            logger.info("call state changed by another turn: merged", extra={"data": {"session_id": session_id}})
        logger.warning("call state not saved after %d tries", tries, extra={"data": {"session_id": session_id}})
        return False
