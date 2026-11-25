"""People behind the agent: departments, handoffs (a call or chat passed to a person) and tickets.

A "department" here is a team of people with a waiting list (a queue in contact-centre language, not a message
queue). The same AI agent answers every caller; when it can't or shouldn't help, it asks the caller, then puts
the call on that team's list with a brief, and the first free person picks it up at the agent desk (/desk).
In production the contact centre (Cisco) holds the waiting list and the bank's CRM holds tickets; both sit behind
small interfaces here (HandoffService, ServiceDesk), so swapping them doesn't touch the conversation.
"""

import json
import logging
import uuid
from datetime import date, datetime, time, timedelta

from app.db import Database
from app.services.content import Departments, Setting, current
from app.logs import log

logger = logging.getLogger(__name__)

# key: (name, priority 1 = critical .. 3 = normal, open 24x7?, what they handle)
DEPARTMENTS = Departments()  # brain/content/catalog.json: names "dept.*", the rest under settings.departments
# Turnaround promised on tickets (working days). Synthetic policy pack; payments follow RBI's harmonised TAT.
TAT_DAYS = Setting("tat_days")


def department_name(key: str, lang: str = "en") -> str:
    """The team's name as the agent says it: "and", never "&" (call 3eab4732 spoke "Payments & Disputes")."""
    if lang == "hi":
        return current().text(f"dept.{key if key in DEPARTMENTS else 'general'}")["hi"]
    return DEPARTMENTS.get(key, DEPARTMENTS["general"])[0].replace(" & ", " and ")


def add_working_days(start: date, days: int) -> date:
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d


class LocalServiceDesk:
    """Tickets in our own database (the CRM's place in production)."""

    PREFIX = {"complaint": "CMP", "dispute": "DSP", "service_request": "SRQ", "callback": "CBK", "lead": "LED"}

    def __init__(self, db: Database, today: date):
        self.db, self.today = db, today

    def create_ticket(self, *, session_id, cif, kind, category, department, summary, detail, tat_days) -> dict:
        with self.db.connection() as conn, conn.transaction():
            n = conn.execute("SELECT count(*) AS n FROM tickets").fetchone()["n"] + 1
            ticket_id = f"AU-{self.PREFIX.get(kind, 'SRQ')}-{self.today:%Y%m%d}-{n:04d}"
            due = add_working_days(self.today, tat_days)
            conn.execute("INSERT INTO tickets (id, session_id, cif, kind, category, department, summary, detail, due_by) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                         (ticket_id, session_id, cif, kind, category, department, summary, json.dumps(detail, default=str), due))
        log(logger, "ticket raised", ticket=ticket_id, kind=kind, category=category, department=department)
        return {"id": ticket_id, "due_by": due, "department": department}

    def open_requests(self, cif: str, days: int = 30) -> list[dict]:
        """The customer's open complaints, disputes and service requests from the last `days` days (never callbacks or leads)."""
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT id, kind, category FROM tickets WHERE cif = %s AND status = 'open' AND kind IN ('complaint', 'dispute', 'service_request') "
                "AND created_at > now() - make_interval(days => %s) ORDER BY created_at DESC", (cif, days)).fetchall()

    def tickets(self, session_id: str) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM tickets WHERE session_id = %s ORDER BY created_at", (session_id,)).fetchall()


class HandoffService:
    def __init__(self, db: Database, settings):
        self.db, self.s = db, settings

    def is_open(self, department: str, now: datetime | None = None) -> bool:
        """Fraud is 24x7; others follow DESK_HOURS (e.g. "08:00-20:00")."""
        if DEPARTMENTS.get(department, DEPARTMENTS["general"])[2]:
            return True
        start, end = (time.fromisoformat(x) for x in self.s.desk_hours.split("-"))
        now_t = (now or datetime.now()).time()
        return start <= now_t <= end

    def create(self, session_id: str, department: str, reason: str, note: dict, channel: str) -> dict:
        department = department if department in DEPARTMENTS else "general"
        handoff = {"id": str(uuid.uuid4()), "department": department, "priority": DEPARTMENTS[department][1]}
        with self.db.connection() as conn:
            # one open handoff per call: a second request updates the brief instead of queueing twice
            row = conn.execute("SELECT id FROM handoffs WHERE session_id = %s AND status IN ('waiting', 'active')",
                               (session_id,)).fetchone()
            if row:
                conn.execute("UPDATE handoffs SET department = %s, priority = %s, reason = %s, note = %s WHERE id = %s",
                             (department, handoff["priority"], reason, json.dumps(note, default=str), row["id"]))
                handoff["id"] = str(row["id"])
            else:
                conn.execute("INSERT INTO handoffs (id, session_id, department, priority, reason, note, channel) "
                             "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                             (handoff["id"], session_id, department, handoff["priority"], reason,
                              json.dumps(note, default=str), channel))
        log(logger, "handoff waiting", call=session_id, department=department, reason=reason)
        return handoff

    def get(self, handoff_id: str) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM handoffs WHERE id = %s", (handoff_id,)).fetchone()

    def add_note(self, handoff_id: str, extra: dict) -> None:
        """Add to the brief a person sees (the AI summary arrives a moment after the transfer starts)."""
        with self.db.connection() as conn:
            conn.execute("UPDATE handoffs SET note = coalesce(note, '{}'::jsonb) || %s::jsonb WHERE id = %s",
                         (json.dumps(extra, default=str), handoff_id))

    def for_session(self, session_id: str) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM handoffs WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
                                (session_id,)).fetchone()

    def board(self) -> list[dict]:
        """Waiting and active handoffs, most urgent first, then oldest; plus the last day's finished ones."""
        with self.db.connection() as conn:
            return conn.execute(
                "SELECT h.*, s.caller_number, extract(epoch FROM (coalesce(h.accepted_at, now()) - h.created_at))::int AS wait_s "
                "FROM handoffs h JOIN sessions s ON s.id = h.session_id "
                "WHERE h.status IN ('waiting', 'active') OR h.created_at > now() - interval '1 day' "
                "ORDER BY (h.status = 'waiting') DESC, (h.status = 'active') DESC, h.priority, h.created_at").fetchall()

    def set_status(self, handoff_id: str, status: str, **fields) -> dict | None:
        cols = {"status": status, **fields}
        if status == "active":
            cols["accepted_at"] = datetime.now().astimezone()
        if status in ("completed", "abandoned", "callback"):
            cols["ended_at"] = datetime.now().astimezone()
        sets = ", ".join(f"{k} = %s" for k in cols)
        with self.db.connection() as conn:
            return conn.execute(f"UPDATE handoffs SET {sets} WHERE id = %s RETURNING *", (*cols.values(), handoff_id)).fetchone()

    def position(self, handoff_id: str) -> int:
        """Place in the waiting list (1 = next)."""
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT count(*) AS n FROM handoffs w, handoffs me WHERE me.id = %s AND w.status = 'waiting' AND "
                "(w.priority < me.priority OR (w.priority = me.priority AND w.created_at <= me.created_at))",
                (handoff_id,)).fetchone()
        return max(1, row["n"])

    def add_message(self, handoff_id: str, sender: str, text: str) -> dict:
        with self.db.connection() as conn:
            return conn.execute("INSERT INTO handoff_messages (handoff_id, sender, text) VALUES (%s, %s, %s) RETURNING *",
                                (handoff_id, sender, text[:2000])).fetchone()

    def messages(self, handoff_id: str, after: int = 0) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute("SELECT * FROM handoff_messages WHERE handoff_id = %s AND id > %s ORDER BY id",
                                (handoff_id, after)).fetchall()
