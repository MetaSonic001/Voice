"""One explicit state record per call (PHASE-4-PLAN §3.2), kept in `sessions.state` (jsonb).

It holds what the chat history cannot reliably carry:
- open_requests: asked but not yet handled (a second request that waited behind a question). None is ever dropped.
- done / declined: which tasks were completed or refused, so cancelling one never cancels another.
- focus: the account / card the caller is talking about ("this account", "that card", "the other one").
- last_list: the items of a list being read out, so "go on" continues it and "the second one" resolves.
- greeted: the greeting is said once per call.
- flow: a guided "help me choose" flow in progress: {"name", "asked": [fact keys], "key", "question"}.
Verification, the yes/no question waiting and the caller's facts keep their own columns (auth, pending, facts).
There is no session restore after a dropped call: the next call verifies again.
"""

import json
from dataclasses import asdict, dataclass, field

from app.db import Database
from app.domain import Request

MAX_OPEN = 6


@dataclass
class CallState:
    open_requests: list[dict] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    declined: list[str] = field(default_factory=list)
    focus: dict = field(default_factory=dict)
    last_list: dict = field(default_factory=dict)  # {"kind": "transactions", "items": [...], "shown": 3}
    greeted: bool = False
    flow: dict = field(default_factory=dict)

    def queue(self, requests: list[Request]) -> None:
        self.open_requests = [r.to_dict() for r in requests][:MAX_OPEN]

    def take_open(self) -> list[Request]:
        out, self.open_requests = [Request.from_dict(d) for d in self.open_requests], []
        return out

    def mark(self, name: str, declined: bool = False) -> None:
        target = self.declined if declined else self.done
        if name not in target:
            target.append(name)

    def focus_text(self) -> str:
        return ", ".join(f"{k} {v}" for k, v in self.focus.items() if v)

    @classmethod
    def from_dict(cls, d: dict | None) -> "CallState":
        d = d or {}
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class StateStore:
    def __init__(self, db: Database):
        self.db = db

    def load(self, session_id: str | None) -> CallState:
        if not session_id:
            return CallState()
        with self.db.connection() as conn:
            row = conn.execute("SELECT state FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return CallState.from_dict(row["state"] if row else None)

    def save(self, session_id: str | None, state: CallState) -> None:
        if not session_id:
            return
        with self.db.connection() as conn:
            conn.execute("UPDATE sessions SET state = %s WHERE id = %s", (json.dumps(asdict(state), default=str), session_id))
