"""Caller verification for personal questions: registered mobile number (RMN) + one-time code (OTP).

How Indian bank IVRs do it (AU's included): the call must come from the RMN, or the caller gives the RMN;
a 6-digit code is sent there by SMS; the caller types it on the keypad. Rules here:
- the code is random (`secrets`), stored only as an HMAC, compared in constant time, valid for a few minutes;
- a few wrong tries lock verification for the session (then: transfer to a person);
- the code arrives through the keypad / an API call only - never through speech-to-text, the LLM, traces or logs;
- verification is server-side session state. The LLM never decides identity and cannot change it;
- every step is written to auth_events (masked numbers only).
SMS delivery is simulated in development: the code is handed back to the test UI and clearly labelled.
"""

import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC

from app.db import Database
from app.domain import Customer
from app.logs import log
from app.ports import Bank

logger = logging.getLogger(__name__)
_SECRET = secrets.token_bytes(32)  # per process: codes never outlive a restart


@dataclass
class AuthResult:
    status: str  # verified | already_verified | otp_sent | need_mobile | not_registered | invalid | expired | locked
    #               | too_many_codes | no_code
    customer: Customer | None = None
    masked_mobile: str = ""
    attempts_left: int = 0
    simulated_sms: str | None = None  # dev only: the "SMS" the caller would receive
    pending_question: str | None = None


def mask_mobile(mobile: str) -> str:
    return "******" + mobile[-4:]


class AuthService:
    def __init__(self, db: Database, bank: Bank, settings):
        self.db, self.bank, self.s = db, bank, settings

    # ───────── state ─────────

    def verified_customer(self, session_id: str | None) -> Customer | None:
        """The verified caller of this session, or None. Verification expires after a quiet period."""
        if not session_id:
            return None
        with self.db.connection() as conn:
            row = conn.execute("SELECT customer_id, verified_at FROM sessions WHERE id = %s", (session_id,)).fetchone()
            if not row or not row["customer_id"]:
                return None
            if _now() - row["verified_at"] > timedelta(seconds=self.s.auth_idle_s):
                conn.execute("UPDATE sessions SET customer_id = NULL, verified_at = NULL WHERE id = %s", (session_id,))
                self._event(conn, session_id, "verification_expired", row["customer_id"])
                return None
            conn.execute("UPDATE sessions SET verified_at = now() WHERE id = %s", (session_id,))  # sliding window
        return self.bank.customer(row["customer_id"])

    def status(self, session_id: str) -> dict:
        customer = self.verified_customer(session_id)
        auth = self._auth(session_id)
        return {"verified": customer is not None, "name": customer.name if customer else None,
                "stage": auth.get("stage"), "masked_mobile": auth.get("masked_mobile")}

    def awaiting_mobile(self, session_id: str | None) -> bool:
        return bool(session_id) and self._auth(session_id).get("stage") == "need_mobile"

    # ───────── steps ─────────

    def start(self, session_id: str, pending_question: str) -> AuthResult:
        """A personal question arrived from an unverified caller: send a code to the RMN, or ask for the RMN."""
        with self.db.connection() as conn:
            row = conn.execute("SELECT caller_number, auth FROM sessions WHERE id = %s", (session_id,)).fetchone()
        auth = dict(row["auth"] or {})
        if auth.get("stage") == "locked":
            return AuthResult("locked")
        auth["pending_question"] = pending_question
        customer = self.bank.find_by_mobile(row["caller_number"]) if row["caller_number"] else None
        if customer is None:
            auth["stage"] = "need_mobile"
            self._save(session_id, auth, "mobile_requested", None, {"caller": _mask(row["caller_number"])})
            return AuthResult("need_mobile")
        return self._send(session_id, auth, customer)

    def submit_mobile(self, session_id: str, mobile: str) -> AuthResult:
        auth = self._auth(session_id)
        customer = self.bank.find_by_mobile(mobile)
        if customer is None:
            self._save(session_id, auth, "mobile_not_registered", None, {"mobile": _mask(mobile)})
            return AuthResult("not_registered")
        return self._send(session_id, auth, customer)

    def resend(self, session_id: str) -> AuthResult:
        auth = self._auth(session_id)
        customer = self.bank.customer(auth.get("cif", "")) if auth.get("cif") else None
        if customer is None:
            return AuthResult("no_code")
        return self._send(session_id, auth, customer)

    def verify(self, session_id: str, code: str) -> AuthResult:
        auth = self._auth(session_id)
        stage = auth.get("stage")
        if stage == "locked":
            return AuthResult("locked")
        if stage == "verified":
            return AuthResult("already_verified", masked_mobile=auth.get("masked_mobile", ""))
        if stage != "otp_sent":
            return AuthResult("no_code")
        if _now() > datetime.fromisoformat(auth["expires"]):
            auth["stage"] = "expired"
            self._save(session_id, auth, "otp_expired", auth.get("cif"))
            return AuthResult("expired")
        code = "".join(ch for ch in str(code) if ch.isdigit())
        if not hmac.compare_digest(_digest(session_id, code), auth["otp_hash"]):
            auth["attempts"] = auth.get("attempts", 0) + 1
            left = self.s.otp_max_attempts - auth["attempts"]
            if left <= 0:
                auth["stage"] = "locked"
                self._save(session_id, auth, "locked", auth.get("cif"), {"attempts": auth["attempts"]})
                return AuthResult("locked")
            self._save(session_id, auth, "otp_failed", auth.get("cif"), {"attempts_left": left})
            return AuthResult("invalid", attempts_left=left)
        customer = self.bank.customer(auth["cif"])
        pending = auth.get("pending_question")
        with self.db.connection() as conn, conn.transaction():
            conn.execute("UPDATE sessions SET customer_id = %s, verified_at = now(), auth = %s WHERE id = %s",
                         (customer.cif, json.dumps({"stage": "verified", "masked_mobile": auth.get("masked_mobile")}),
                          session_id))
            self._event(conn, session_id, "otp_verified", customer.cif)
        log(logger, "caller verified", session_id=session_id, customer=customer.cif)
        return AuthResult("verified", customer, auth.get("masked_mobile", ""), pending_question=pending)

    def end(self, session_id: str) -> None:
        with self.db.connection() as conn:
            conn.execute("UPDATE sessions SET customer_id = NULL, verified_at = NULL, auth = '{}' WHERE id = %s",
                         (session_id,))
            self._event(conn, session_id, "verification_ended", None)

    def pending_query(self, session_id: str) -> dict | None:
        """The personal question waiting for "which account?" to be answered, if any."""
        return self._auth(session_id).get("pending_query")

    def set_pending_query(self, session_id: str, query: dict | None) -> None:
        with self.db.connection() as conn:
            if query is None:
                conn.execute("UPDATE sessions SET auth = auth - 'pending_query' WHERE id = %s", (session_id,))
            else:
                conn.execute("UPDATE sessions SET auth = jsonb_set(auth, '{pending_query}', %s::jsonb) WHERE id = %s",
                             (json.dumps(query), session_id))

    def record_read(self, session_id: str, customer: Customer, topic: str, fields: list[str]) -> None:
        """Audit: which data of which customer was disclosed, in which session."""
        with self.db.connection() as conn:
            self._event(conn, session_id, "data_read", customer.cif, {"topic": topic, "fields": fields})

    # ───────── internals ─────────

    def _send(self, session_id: str, auth: dict, customer: Customer) -> AuthResult:
        sends = auth.get("sends", 0)
        if sends >= self.s.otp_max_sends:
            self._save(session_id, auth, "too_many_codes", customer.cif)
            return AuthResult("too_many_codes")
        code = f"{secrets.randbelow(1_000_000):06d}"
        auth.update(stage="otp_sent", cif=customer.cif, otp_hash=_digest(session_id, code), attempts=0, sends=sends + 1,
                    expires=(_now() + timedelta(seconds=self.s.otp_ttl_s)).isoformat(),
                    masked_mobile=mask_mobile(customer.mobile))
        self._save(session_id, auth, "otp_sent", customer.cif, {"to": mask_mobile(customer.mobile), "delivery": self.s.otp_delivery})
        sms = None
        if self.s.otp_delivery == "simulated":
            sms = f"{code} is your AU Bank verification code. Valid for {self.s.otp_ttl_s // 60} minutes. Do not share it."
        # A real SMS gateway would be called here; the code is never logged.
        return AuthResult("otp_sent", customer, mask_mobile(customer.mobile), self.s.otp_max_attempts, sms)

    def _auth(self, session_id: str) -> dict:
        with self.db.connection() as conn:
            row = conn.execute("SELECT auth FROM sessions WHERE id = %s", (session_id,)).fetchone()
        return dict(row["auth"] or {}) if row else {}

    def _save(self, session_id: str, auth: dict, event: str, cif: str | None, detail: dict | None = None) -> None:
        with self.db.connection() as conn, conn.transaction():
            conn.execute("UPDATE sessions SET auth = %s WHERE id = %s", (json.dumps(auth), session_id))
            self._event(conn, session_id, event, cif, detail)
        log(logger, f"auth {event}", session_id=session_id, **(detail or {}))

    @staticmethod
    def _event(conn, session_id: str, event: str, cif: str | None, detail: dict | None = None) -> None:
        conn.execute("INSERT INTO auth_events (session_id, event, customer_id, detail) VALUES (%s, %s, %s, %s)",
                     (session_id, event, cif, json.dumps(detail or {})))


def _digest(session_id: str, code: str) -> str:
    return hmac.new(_SECRET, f"{session_id}:{code}".encode(), hashlib.sha256).hexdigest()


def _mask(number: str | None) -> str | None:
    digits = "".join(ch for ch in str(number or "") if ch.isdigit())
    return mask_mobile(digits) if digits else None


def _now() -> datetime:
    return datetime.now(UTC)
