"""The `Bank` port over HTTP: the brain's adapter for the bank's separate systems (PHASE-4-PLAN §3.3).

Each system speaks its own vocabulary (POSTED / HOLD / REJECTED, ACTIVE / TEMP_BLOCKED / HOT_LISTED, ...). This module is
the one place that maps them to the brain's internal words, so the conversation code never sees a system's own codes.
Swapping to AU's real APIs means a new adapter like this one, not a change to the conversation.

- Reads of one customer run in parallel (about 15 small calls in two rounds), each with a short timeout and one retry.
- A customer-scoped token is minted here, from the verified CIF the caller passes. `find_by_mobile` (before verification)
  uses the service token only and returns just enough to send the code.
- Writes carry an `Idempotency-Key`. If the reply is lost, the same key is retried: the bank returns the original result,
  so nothing happens twice. If it still cannot be confirmed, `BankUnconfirmed` is raised (never "failed").
- An unknown status word becomes "unknown", which is said honestly ("I can't confirm its status").
"""

import contextvars
import logging
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import httpx

from app import tokens
from app.domain import (
    Account, CardTxn, Charge, CreditCard, Customer, DebitCard, Emi, FixedDeposit, Loan, RateChange, Transaction,
)
from app.ports import BankError, BankUnconfirmed

logger = logging.getLogger(__name__)
SYSTEMS = ["cif", "cbs", "payments", "cards", "cc", "lms", "deposits", "crm"]

# One status table: every system's word -> the brain's word. Anything not listed is "unknown".
TXN_STATUS = {"POSTED": "success", "HOLD": "pending", "REJECTED": "failed", "RETURNED": "reversed",
              "SUCCESS": "success", "PENDING": "pending", "FAILURE": "failed", "REVERSED": "reversed"}
DEBIT_STATUS = {"ACTIVE": "active", "TEMP_BLOCKED": "temp_blocked", "HOT_LISTED": "blocked"}
CREDIT_STATUS = {"ACTIVE": "active", "TEMP_BLOCKED": "temp_blocked", "HOTLISTED": "blocked"}
ACCOUNT_STATUS = {"ACTIVE": "active", "INACTIVE": "inactive", "DORMANT": "dormant"}
EMI_STATUS = {"PAID": "paid", "BOUNCED": "bounced"}
STATUS_TO_DEBIT = {v: k for k, v in DEBIT_STATUS.items()}


class BankTimeout(BankError):
    """No answer in time (or a gateway timeout): for a write, the outcome is unknown."""


def _d(value) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _f(value) -> float | None:
    return None if value is None else float(value)


class HttpBank:
    def __init__(self, base_url: str, service_token: str, token_secret: bytes, *, timeout_s: float = 3.0,
                 token_ttl_s: int = 900, client: httpx.Client | None = None, workers: int = 8):
        self.http = client or httpx.Client(base_url=base_url, timeout=timeout_s)
        self._service_token, self._secret, self._ttl = service_token, token_secret, token_ttl_s
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="bank")

    # ── transport ──
    def _token(self, cif: str) -> str:
        return tokens.issue(self._secret, cif=cif, systems=SYSTEMS, scopes=["read", "card_update", "crm"], ttl_s=self._ttl)

    def _headers(self, cif: str | None, extra: dict | None = None) -> dict:
        headers = {"X-Service-Token": self._service_token} | (extra or {})
        if cif:
            headers["Authorization"] = f"Bearer {self._token(cif)}"
        return headers

    def _submit(self, fn, *args):
        """Run a read on the bank's worker threads with the caller's context, so the turn's call log sees it."""
        return self._pool.submit(contextvars.copy_context().run, fn, *args)

    def _request(self, method: str, path: str, cif: str | None, *, params=None, json=None, headers=None, retry: bool = True):
        last: Exception | None = None
        started = time.perf_counter()
        for attempt in range(2 if retry else 1):
            try:
                response = self.http.request(method, path, params=params, json=json, headers=self._headers(cif, headers))
            except httpx.TimeoutException as e:
                last = e
                continue
            except httpx.HTTPError as e:
                log_call(method, path, "unreachable", started, attempt + 1)
                raise BankError(f"bank system unreachable: {type(e).__name__}") from e
            if response.status_code >= 500:
                cls = BankTimeout if response.status_code == 504 else BankError
                last = cls(f"bank system error {response.status_code} on {path.split('/')[1]}")
                continue
            log_call(method, path, response.status_code, started, attempt + 1)
            return response
        log_call(method, path, "timeout" if isinstance(last, httpx.TimeoutException) else "error", started, 2 if retry else 1)
        if isinstance(last, httpx.TimeoutException):
            raise BankTimeout(f"bank system timed out on {path.split('/')[1]}") from last
        raise last or BankError("bank system unavailable")

    def _get(self, path: str, cif: str | None, params: dict | None = None):
        response = self._request("GET", path, cif, params=params)
        if response.status_code == 404:
            return None
        if response.status_code >= 400:
            raise BankError(f"bank refused {path.split('/')[1]}: {response.status_code}")
        try:
            return response.json()
        except ValueError as e:
            raise BankError(f"unexpected response from {path.split('/')[1]}") from e

    # ── the Bank port ──
    def find_by_mobile(self, mobile: str) -> Customer | None:
        found = self._get("/cif/v1/customers", None, {"mobile": mobile})
        if not found:
            return None
        return Customer(found["cif"], found["name"], found["mobile"], [], segment=found.get("segment") or "regular",
                        language=found.get("language"))

    def demo_callers(self, limit: int = 14) -> list[dict]:
        return self._get("/cif/v1/demo-callers", None, {"limit": limit}) or []

    def customer(self, cif: str) -> Customer | None:
        first = {k: self._submit(self._get, path, cif, params) for k, (path, params) in {
            "profile": (f"/cif/v1/customers/{cif}", None), "accounts": ("/cbs/v1/accounts", {"cif": cif}),
            "debit": ("/cards/v1/debit", {"cif": cif}), "credit": ("/cc/v1/cards", {"cif": cif}),
            "loans": ("/lms/v1/loans", {"cif": cif}), "fds": ("/deposits/v1/fds", {"cif": cif})}.items()}
        profile = first["profile"].result()
        if not profile:
            return None
        accounts, debit, credit, loans, fds = (first[k].result() or [] for k in ("accounts", "debit", "credit", "loans", "fds"))
        second = (
            [(self._submit(self._get, f"/cbs/v1/accounts/{a['account_no']}/transactions", cif, {"limit": 60}),
              self._submit(self._get, f"/cbs/v1/accounts/{a['account_no']}/charges", cif)) for a in accounts],
            [self._submit(self._get, f"/cc/v1/cards/{k['card_id']}/transactions", cif) for k in credit],
            [(self._submit(self._get, f"/lms/v1/loans/{x['loan_no']}/emis", cif),
              self._submit(self._get, f"/lms/v1/loans/{x['loan_no']}/rate-changes", cif)) for x in loans])
        acct_calls, cc_calls, loan_calls = second
        return Customer(
            cif=profile["cif"], name=profile["name"], mobile=profile["mobile"], segment=profile.get("segment") or "regular",
            rm_name=profile.get("rm_name"), language=profile.get("language"),
            accounts=[self._account(a, t.result() or [], c.result() or [], profile.get("branch_type", ""), debit)
                      for a, (t, c) in zip(accounts, acct_calls, strict=True)],
            fds=[self._fd(f) for f in fds],
            loans=[self._loan(x, e.result() or [], r.result() or []) for x, (e, r) in zip(loans, loan_calls, strict=True)],
            credit_cards=[self._credit(k, t.result() or []) for k, t in zip(credit, cc_calls, strict=True)])

    def update_card(self, cif: str, kind: str, card_id: str, changes: dict, action: str, key: str | None = None) -> dict:
        key = key or str(uuid.uuid4())
        if kind == "debit":
            if "status" in changes:
                path, body = f"/cards/v1/debit/{card_id}/status", {"status": STATUS_TO_DEBIT[changes["status"]]}
            else:
                path, body = f"/cards/v1/debit/{card_id}/controls", {k: v for k, v in changes.items()}
        elif "status" in changes:
            if changes["status"] == "active":
                path, body = f"/cc/v1/cards/{card_id}/unblock", {}
            else:
                path, body = f"/cc/v1/cards/{card_id}/block", {"type": "TEMPORARY" if changes["status"] == "temp_blocked" else "PERMANENT"}
        else:
            path, body = f"/cc/v1/cards/{card_id}/international", {"enabled": bool(changes.get("international"))}
        before = self._card_state(cif, kind, card_id)
        try:
            response = self._request("POST", path, cif, json=body, headers={"Idempotency-Key": key})
        except BankTimeout as e:  # no answer after the retry (same key): the write may or may not have happened
            raise BankUnconfirmed(str(e)) from e
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", "rejected")
            except ValueError:
                detail = "rejected"
            raise BankError(f"bank rejected the change: {detail}")
        try:
            out = response.json()
        except ValueError as e:
            raise BankUnconfirmed("unreadable reply to a card change") from e
        return {"reference": out["reference"], "before": before, "after": self._after(kind, out)}

    # ── reading one card back (the read-back before saying "done") ──
    def _card_state(self, cif: str, kind: str, card_id: str) -> dict:
        try:
            if kind == "debit":
                c = self._get(f"/cards/v1/debit/{card_id}", cif)
                return {"status": DEBIT_STATUS.get(c["status"], "unknown"), **c["controls"]} if c else {}
            cards = self._get("/cc/v1/cards", cif, {"cif": cif}) or []
            c = next((x for x in cards if x["card_id"] == card_id), None)
            return {"status": CREDIT_STATUS.get(c["status"], "unknown"), "international": c["international"]} if c else {}
        except (BankError, KeyError):
            return {}

    @staticmethod
    def _after(kind: str, out: dict) -> dict:
        if kind == "debit":
            card = out["card"]
            return {"status": DEBIT_STATUS.get(card["status"], "unknown"), **card["controls"]}
        return {"status": CREDIT_STATUS.get(out["status"], "unknown"), "international": out["international"]}

    # ── mapping to the brain's types ──
    @staticmethod
    def _txn(x: dict) -> Transaction:
        at = datetime.fromisoformat(x["posted_at"]).astimezone()
        return Transaction(at.date(), x["narration"], "Debit" if x["dr_cr"] == "DR" else "Credit", float(x["amount"]), x["mode"],
                           x.get("counterparty") or "", TXN_STATUS.get(x["posting_status"], "unknown"), x["utr"],
                           x.get("remarks") or "", at.strftime("%H:%M"))

    def _account(self, a: dict, txns: list, charges: list, branch_type: str, debit_cards: list) -> Account:
        avg, hold = a["average_balance"], a["hold"]
        card = next((d for d in debit_cards if d["account_no"] == a["account_no"]), None)
        return Account(
            number=a["account_no"], type=a["product"], balance=float(a["ledger_balance"]), available=_f(a["available_balance"]),
            hold_amount=float(hold["amount"] or 0), hold_reason=hold["reason"], status=ACCOUNT_STATUS.get(a["status"], "unknown"),
            is_salary=a["salary_account"], branch_type=a.get("branch_type") or branch_type, amb_required=float(avg["required"] or 0),
            amb_this_month=_f(avg["this_month"]), amb_last_month=_f(avg["last_month"]),
            transactions=[self._txn(x) for x in txns],
            charges=[Charge(_d(c["charged_on"]), c["charge_code"], float(c["amount"]), c["reversed"]) for c in charges],
            debit_card=DebitCard(card["card_id"], card["last4"], card["product"], DEBIT_STATUS.get(card["status"], "unknown"),
                                 *(card["controls"][k] for k in ("atm", "pos", "ecom", "contactless", "international")),
                                 a["account_no"][-4:]) if card else None)

    @staticmethod
    def _credit(k: dict, txns: list) -> CreditCard:
        s, p, fee, lounge = k["last_statement"], k["last_payment"], k["fee"], k["lounge"]
        return CreditCard(
            id=k["card_id"], last4=k["last4"], variant=k["product"], status=CREDIT_STATUS.get(k["status"], "unknown"),
            limit=float(k["credit_limit"]), outstanding=float(k["outstanding"]), total_due=float(s["total_due"]),
            minimum_due=float(s["minimum_due"]), statement_date=_d(s["statement_date"]), due_date=_d(s["due_date"]),
            last_payment_on=_d(p["on"]), last_payment_amount=_f(p["amount"]), last_payment_status=p["status"],
            previous_bill_paid=k["previous_bill_paid"], annual_fee=_f(fee["annual"]), annual_fee_charged_on=_d(fee["charged_on"]),
            annual_fee_waiver_spend=_f(fee["waiver_spend"]), spend_last_year=_f(fee["spend_last_year"]),
            lounge_per_quarter=lounge["per_quarter"], lounge_used_quarter=lounge["used_quarter"], international=k["international"],
            transactions=[CardTxn(datetime.fromisoformat(t["posted_at"]).astimezone().date(), t["merchant"], float(t["amount"]),
                                  TXN_STATUS.get(t["status"], "unknown"), t["international"]) for t in txns])

    @staticmethod
    def _fd(f: dict) -> FixedDeposit:
        return FixedDeposit(f["fd_no"], float(f["principal"]), float(f["interest_rate"]), _d(f["booked_on"]), f["tenure_months"],
                            _d(f["maturity_date"]), float(f["maturity_value"]), f["payout"].lower(), f["auto_renew"], f["status"].lower())

    @staticmethod
    def _loan(x: dict, emis: list, rates: list) -> Loan:
        return Loan(
            x["loan_no"], x["product"], x["rate_type"].lower(), float(x["interest_rate"]), float(x["sanctioned"]),
            float(x["outstanding"]), float(x["emi"]), _d(x["next_due"]), x["emis_paid"], x["emis_left"], x["status"].lower(),
            x["debit_account"],
            emis=[Emi(_d(e["due_date"]), float(e["amount"]), EMI_STATUS.get(e["status"], "unknown"), e["failure_reason"],
                      float(e["bounce_charge"]), _d(e["paid_on"]) if e.get("paid_on") else None) for e in emis],
            rate_changes=[RateChange(_d(r["changed_on"]), float(r["old_rate"]), float(r["new_rate"]), float(r["old_emi"]),
                                     float(r["new_emi"])) for r in rates])

    def close(self) -> None:
        self._pool.shutdown(wait=False)
        self.http.close()


class HttpServiceDesk:
    """Tickets raised through the CRM system's API (the same `ServiceDesk` port as the local desk)."""

    def __init__(self, bank: HttpBank):
        self.bank = bank

    def create_ticket(self, *, session_id, cif, kind, category, department, summary, detail, tat_days) -> dict:
        body = {"cif": cif, "session_ref": session_id, "kind": kind, "category": category, "department": department,
                "summary": summary, "detail": detail, "tat_days": tat_days}
        key = str(uuid.uuid4())
        try:
            response = self.bank._request("POST", "/crm/v1/complaints", cif, json=body, headers={"Idempotency-Key": key})
        except BankTimeout as e:
            raise BankUnconfirmed(str(e)) from e
        if response.status_code >= 400:
            raise BankError(f"CRM refused the ticket: {response.status_code}")
        out = response.json()
        return {"id": out["ticket_id"], "due_by": date.fromisoformat(out["due_by"]), "department": out["department"]}

    def open_requests(self, cif: str, days: int = 30) -> list[dict]:
        rows = self.bank._get("/crm/v1/complaints", cif, {"cif": cif, "open": "true", "days": days}) or []
        return [{"id": r["ticket_id"], "kind": r["kind"], "category": r["category"]} for r in rows
                if r["kind"] in ("complaint", "dispute", "service_request")]


# ───────── which bank systems a turn called (shown on /calls as evidence) ─────────
# Conversation.turn sets a fresh list per turn; every HTTP call to a bank system appends one line. Account, card and loan
# numbers in the path are masked to their last 4 digits (as everywhere else).
BANK_CALLS: contextvars.ContextVar[list | None] = contextvars.ContextVar("bank_calls", default=None)
SYSTEMS = {"cif": "Customer information (CIF)", "cbs": "Core banking: accounts", "payments": "Payments",
           "cards": "Debit cards", "cc": "Credit cards", "lms": "Loans (LMS)", "deposits": "Deposits (FDs)",
           "crm": "CRM: complaints and requests"}


def _mask(path: str) -> str:
    return re.sub(r"[A-Za-z]*\d[\w-]{4,}", lambda m: "…" + re.sub(r"\D", "", m.group(0))[-4:], path)


def log_call(method: str, path: str, status, started: float, attempts: int) -> None:
    calls = BANK_CALLS.get()
    if calls is None:
        return
    system = path.strip("/").split("/")[0]
    calls.append({"system": system, "name": SYSTEMS.get(system, system), "method": method, "path": _mask(path),
                  "status": status, "ms": round((time.perf_counter() - started) * 1000), "attempts": attempts})
