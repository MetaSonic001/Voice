"""The mock bank as a service (PHASE-4-PLAN §3.3): one process, one router per bank system, each with its own paths,
ids and status words, so the brain's adapter has to do what it will do against AU's real APIs.

These are OUR proposed contracts (FastAPI generates the OpenAPI for each router at /docs), a starting point for a
conversation with AU's integration team. They are not AU's specs.

Realism built in:
- every call needs the service token (brain -> bank); customer data also needs a customer-scoped token (see app/tokens.py)
  and each system checks on its own that the data belongs to that customer;
- writes need an `Idempotency-Key`: a retry with the same key and the same request returns the original result (even
  if the first response was lost), the same key with a different request is rejected (409), keys last 24 h;
- fault injection per system (MOCKBANK_FAULTS="cards:lost_response,cbs:latency=400" or an `X-Mock-Fault` header):
  latency, timeout, 5xx, malformed JSON, an unexpected status word, and "succeeded but the response was lost".
The schemas are not separate: all systems read the same Postgres tables (separate schemas would add realism, not security).
"""

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app import tokens
from app.db import Database

logger = logging.getLogger("app.mockbank")
SYSTEMS = ("cif", "cbs", "payments", "cards", "cc", "lms", "deposits", "crm")
IDEMPOTENCY_TTL_H = 24
FAULT_KINDS = ("latency", "timeout", "5xx", "malformed", "lost_response", "unexpected_status")


@dataclass
class BankConfig:
    service_token: str
    token_secret: bytes
    today: date
    faults: dict = field(default_factory=dict)  # system -> (kind, argument)
    latency_ms: tuple = (5, 25)


def parse_faults(spec: str) -> dict:
    """"cards:lost_response,cbs:latency=400". The legacy MOCK_BANK_FAIL words still work: read, card_update, all."""
    legacy = {"read": {s: ("5xx", 0) for s in ("cif", "cbs", "payments", "cc", "lms", "deposits")} | {"cards": ("5xx", 0)},
              "card_update": {"cards": ("5xx", 0), "cc": ("5xx", 0)},
              "all": {s: ("5xx", 0) for s in SYSTEMS}}
    if spec in legacy:
        out = dict(legacy[spec])
        if spec == "read":  # reading fails, the card systems stay up
            out.pop("cards", None)
        return out
    out = {}
    for part in filter(None, (p.strip() for p in spec.split(","))):
        system, _, rest = part.partition(":")
        kind, _, arg = rest.partition("=")
        if system in SYSTEMS and kind in FAULT_KINDS:
            out[system] = (kind, int(arg) if arg.isdigit() else 0)
    return out


def encode(value):
    """JSON-safe: Decimal -> number, date / datetime -> ISO text."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


@dataclass
class Caller:
    """Who is calling a route: always the brain (service token); `cif` is set when a customer token was checked."""

    cif: str | None = None
    fault: tuple | None = None


class MockBank:
    def __init__(self, db: Database, config: BankConfig):
        self.db, self.cfg = db, config

    # ── authorisation: each system checks on its own ──
    def caller(self, system: str, scope: str | None, optional: bool = False):
        """A FastAPI dependency for one system. `scope` None = service token only (a lookup before verification);
        `optional` = a customer token is checked when sent but not required (a ticket for an unverified caller)."""

        def dependency(request: Request, x_service_token: str | None = Header(None), authorization: str | None = Header(None),
                       x_mock_fault: str | None = Header(None)) -> Caller:
            if not x_service_token or not hmac.compare_digest(x_service_token, self.cfg.service_token):
                raise HTTPException(401, "service token required")
            cif = None
            if scope and not (optional and not authorization):
                if not authorization or not authorization.startswith("Bearer "):
                    raise HTTPException(401, "customer token required")
                try:
                    cif = tokens.verify(self.cfg.token_secret, authorization[7:], system=system, scope=scope)["cif"]
                except tokens.TokenError as e:
                    raise HTTPException(403, str(e)) from e
            fault = self.cfg.faults.get(system)
            if x_mock_fault:
                kind, _, arg = x_mock_fault.partition("=")
                fault = (kind, int(arg) if arg.isdigit() else 0) if kind in FAULT_KINDS else fault
            request.state.fault = fault
            return Caller(cif, fault)

        return dependency

    @staticmethod
    def owns(caller: Caller, cif: str) -> None:
        if caller.cif != cif:
            raise HTTPException(403, "this token is for a different customer")

    # ── idempotent writes ──
    def idempotent(self, key: str | None, request_hash: str, run, fault: tuple | None) -> tuple[int, dict]:
        """Run a write once per key. Returns (status, body). `fault` lost_response: do the work, then fail the reply."""
        if not key:
            raise HTTPException(400, "Idempotency-Key header required")
        with self.db.connection() as conn:
            conn.execute("DELETE FROM corebank.idempotency WHERE created_at < now() - make_interval(hours => %s)", (IDEMPOTENCY_TTL_H,))
            row = conn.execute("SELECT request_hash, status, response FROM corebank.idempotency WHERE key = %s", (key,)).fetchone()
        if row:
            if row["request_hash"] != request_hash:
                raise HTTPException(409, "Idempotency-Key was used with a different request")
            return row["status"], row["response"]
        status, body = run()
        body = encode(body)
        with self.db.connection() as conn:
            conn.execute("INSERT INTO corebank.idempotency (key, request_hash, status, response) VALUES (%s,%s,%s,%s) "
                         "ON CONFLICT DO NOTHING", (key, request_hash, status, json.dumps(body)))
        if fault and fault[0] == "lost_response":  # the work is done and recorded; the caller never hears back
            raise HTTPException(504, "gateway timeout (response lost)")
        return status, body


def request_hash(method: str, path: str, body: dict) -> str:
    return hashlib.sha256(json.dumps([method, path, body], sort_keys=True, default=str).encode()).hexdigest()


def create_app(bank: MockBank) -> FastAPI:
    from app.mockbank import systems

    app = FastAPI(title="AU SFB mock bank systems (simulated; our proposed contracts, not AU's specs)", version="0.1.0")

    @app.middleware("http")
    async def faults(request: Request, call_next):
        """Latency, timeouts, 5xx and malformed bodies, decided per system from the path (/cbs/v1/... -> cbs)."""
        system = request.url.path.strip("/").split("/", 1)[0]
        header = request.headers.get("x-mock-fault")
        fault = bank.cfg.faults.get(system)
        if header:
            kind, _, arg = header.partition("=")
            fault = (kind, int(arg) if arg.isdigit() else 0) if kind in FAULT_KINDS else fault
        lo, hi = bank.cfg.latency_ms
        await asyncio.sleep(random.uniform(lo, hi) / 1000)
        if fault and fault[0] == "latency":
            await asyncio.sleep(fault[1] / 1000)
        if fault and fault[0] == "timeout":
            await asyncio.sleep(fault[1] / 1000 if fault[1] else 30)
        if fault and fault[0] == "5xx":
            return JSONResponse(status_code=503, content={"detail": f"{system} is unavailable (simulated)"})
        started = time.perf_counter()
        response = await call_next(request)
        if fault and fault[0] == "malformed" and response.status_code < 300:
            return Response(content=b'{"unexpected": ', media_type="application/json", status_code=200)
        response.headers["x-mockbank-ms"] = str(round((time.perf_counter() - started) * 1000))
        return response

    for router in systems.routers(bank):
        app.include_router(router)
    return app
