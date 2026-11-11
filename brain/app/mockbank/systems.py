"""The bank's separate systems as routers (PHASE-4-PLAN §3.3). Each has its own paths, ids and status words:

| System          | Prefix        | Status words                                                      |
|-----------------|---------------|-------------------------------------------------------------------|
| Customer / CIF  | /cif/v1       | -                                                                 |
| Core banking    | /cbs/v1       | posting: POSTED, HOLD, REJECTED, RETURNED                         |
| Payments switch| /payments/v1  | SUCCESS, PENDING, FAILURE, REVERSED                               |
| Debit cards     | /cards/v1     | ACTIVE, TEMP_BLOCKED, HOT_LISTED                                  |
| Credit cards    | /cc/v1        | ACTIVE, TEMP_BLOCKED, HOTLISTED                                   |
| Loans (LMS)     | /lms/v1       | EMI: PAID, BOUNCED                                                |
| Deposits        | /deposits/v1  | ACTIVE, MATURED, CLOSED                                           |
| CRM             | /crm/v1       | OPEN, ...                                                         |

The brain's adapter (adapters/http_bank.py) maps all of these to one internal vocabulary.
"""

import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.mockbank.core import MockBank, encode, request_hash

POSTING = {"success": "POSTED", "pending": "HOLD", "failed": "REJECTED", "reversed": "RETURNED"}
SWITCH = {"success": "SUCCESS", "pending": "PENDING", "failed": "FAILURE", "reversed": "REVERSED"}
DEBIT_STATUS = {"active": "ACTIVE", "temp_blocked": "TEMP_BLOCKED", "blocked": "HOT_LISTED"}
CREDIT_STATUS = {"active": "ACTIVE", "temp_blocked": "TEMP_BLOCKED", "blocked": "HOTLISTED"}
ACCOUNT_STATUS = {"active": "ACTIVE", "inactive": "INACTIVE", "dormant": "DORMANT"}


def routers(bank: MockBank) -> list[APIRouter]:
    return [_cif(bank), _cbs(bank), _payments(bank), _cards(bank), _cc(bank), _lms(bank), _deposits(bank), _crm(bank)]


def _rows(bank: MockBank, sql: str, *args) -> list[dict]:
    with bank.db.connection() as conn:
        return conn.execute(sql, args).fetchall()


def _one(bank: MockBank, sql: str, *args) -> dict | None:
    with bank.db.connection() as conn:
        return conn.execute(sql, args).fetchone()


# ───────────────────────── customer / CIF ─────────────────────────


def _cif(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/cif/v1", tags=["cif: customer information"])
    lookup = bank.caller("cif", None)  # before verification: the registered mobile finds the customer
    customer = bank.caller("cif", "read")

    @r.get("/customers")
    def find(mobile: str, _=Depends(lookup)):
        row = _one(bank, "SELECT cif, name, mobile, segment, language FROM corebank.customers WHERE mobile = %s", mobile[-10:])
        if not row:
            raise HTTPException(404, "no customer with that mobile")
        return encode(row)

    @r.get("/customers/{cif}")
    def profile(cif: str, c=Depends(customer)):
        bank.owns(c, cif)
        row = _one(bank, "SELECT c.cif, c.name, c.mobile, c.segment, c.language, c.rm_name, b.type AS branch_type "
                         "FROM corebank.customers c JOIN corebank.branches b ON b.id = c.branch_id WHERE cif = %s", cif)
        if not row:
            raise HTTPException(404, "unknown customer")
        return encode(row)

    @r.get("/demo-callers")
    def demo_callers(limit: int = 14, _=Depends(lookup)):  # simulation only: the test console's "calling from" list
        rows = _rows(bank, "SELECT c.name, c.mobile, c.scenario, "
                           "(SELECT count(*) FROM corebank.accounts a WHERE a.cif = c.cif) AS accounts, "
                           "EXISTS (SELECT 1 FROM corebank.fixed_deposits f WHERE f.cif = c.cif AND f.status = 'active') AS fd, "
                           "EXISTS (SELECT 1 FROM corebank.loans l WHERE l.cif = c.cif) AS loan, "
                           "EXISTS (SELECT 1 FROM corebank.debit_cards d JOIN corebank.accounts a ON a.number = d.account_number "
                           "        WHERE a.cif = c.cif) AS debit_card, "
                           "EXISTS (SELECT 1 FROM corebank.credit_cards k WHERE k.cif = c.cif) AS credit_card "
                           "FROM corebank.customers c ORDER BY (c.scenario IS NULL), c.cif LIMIT %s", limit)
        return encode(rows)

    return r


# ───────────────────────── core banking ─────────────────────────


def _cbs(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/cbs/v1", tags=["cbs: core banking"])
    read = bank.caller("cbs", "read")

    def own_account(number: str, c) -> dict:
        row = _one(bank, "SELECT a.*, b.type AS branch_type FROM corebank.accounts a JOIN corebank.branches b ON b.id = a.branch_id "
                         "WHERE number = %s", number)
        if not row:
            raise HTTPException(404, "unknown account")
        bank.owns(c, row["cif"])
        return row

    @r.get("/accounts")
    def accounts(cif: str, c=Depends(read)):
        bank.owns(c, cif)
        rows = _rows(bank, "SELECT a.*, b.type AS branch_type FROM corebank.accounts a JOIN corebank.branches b "
                           "ON b.id = a.branch_id WHERE cif = %s ORDER BY number", cif)
        return encode([{
            "account_no": a["number"], "product": a["variant"], "branch_type": a["branch_type"],
            "status": ACCOUNT_STATUS.get(a["status"], a["status"].upper()), "salary_account": a["is_salary"],
            "ledger_balance": a["ledger_balance"], "available_balance": a["available_balance"],
            "hold": {"amount": a["hold_amount"], "reason": a["hold_reason"]},
            "average_balance": {"required": a["amb_required"], "this_month": a["amb_this_month"], "last_month": a["amb_last_month"]},
        } for a in rows])

    @r.get("/accounts/{number}/transactions")
    def transactions(number: str, limit: int = 60, c=Depends(read)):
        own_account(number, c)
        rows = _rows(bank, "SELECT * FROM corebank.account_txns WHERE account_number = %s ORDER BY posted_at DESC LIMIT %s",
                     number, min(limit, 200))
        return encode([{
            "txn_id": f"CBS{x['id']:09d}", "posted_at": x["posted_at"], "narration": x["description"],
            "dr_cr": "DR" if x["kind"] == "Debit" else "CR", "amount": x["amount"], "mode": x["channel"],
            "counterparty": x["counterparty"], "utr": x["reference"],
            "posting_status": POSTING.get(x["status"], "POSTED"), "remarks": x["note"]} for x in rows])

    @r.get("/accounts/{number}/charges")
    def charges(number: str, c=Depends(read)):
        own_account(number, c)
        rows = _rows(bank, "SELECT * FROM corebank.account_charges WHERE account_number = %s ORDER BY charged_on DESC", number)
        return encode([{"charge_code": x["type"], "charged_on": x["charged_on"], "amount": x["amount"],
                        "reversed": x["reversed"]} for x in rows])

    return r


# ───────────────────────── payments switch ─────────────────────────


def _payments(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/payments/v1", tags=["payments: UPI / IMPS / NEFT switch"])
    read = bank.caller("payments", "read")

    @r.get("/txns/{utr}")
    def txn(utr: str, c=Depends(read)):
        row = _one(bank, "SELECT t.*, a.cif FROM corebank.account_txns t JOIN corebank.accounts a ON a.number = t.account_number "
                         "WHERE t.reference = %s", utr)
        if not row:
            raise HTTPException(404, "unknown UTR")
        bank.owns(c, row["cif"])
        return encode({"utr": row["reference"], "mode": row["channel"], "amount": row["amount"],
                       "status": SWITCH.get(row["status"], "UNKNOWN"), "initiated_at": row["posted_at"],
                       "counterparty": row["counterparty"], "account_no": row["account_number"]})

    return r


# ───────────────────────── debit cards ─────────────────────────


class DebitStatusBody(BaseModel):
    status: str  # ACTIVE | TEMP_BLOCKED | HOT_LISTED


class DebitControlsBody(BaseModel):
    atm: bool | None = None
    pos: bool | None = None
    ecom: bool | None = None
    contactless: bool | None = None
    international: bool | None = None


def _debit_view(row: dict) -> dict:
    return {"card_id": row["id"], "last4": row["last4"], "product": row["variant"], "account_no": row["account_number"],
            "status": DEBIT_STATUS.get(row["status"], "UNKNOWN"),
            "controls": {k: row[k] for k in ("atm", "pos", "ecom", "contactless", "international")}}


def _cards(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/cards/v1", tags=["cards: debit card management"])
    read = bank.caller("cards", "read")
    write = bank.caller("cards", "card_update")

    def own(card_id: str, c) -> dict:
        row = _one(bank, "SELECT d.*, a.cif FROM corebank.debit_cards d JOIN corebank.accounts a ON a.number = d.account_number "
                         "WHERE d.id = %s", card_id)
        if not row:
            raise HTTPException(404, "unknown card")
        bank.owns(c, row["cif"])
        return row

    @r.get("/debit")
    def listing(cif: str, c=Depends(read)):
        bank.owns(c, cif)
        return encode([_debit_view(x) for x in _rows(
            bank, "SELECT d.* FROM corebank.debit_cards d JOIN corebank.accounts a ON a.number = d.account_number WHERE a.cif = %s", cif)])

    @r.get("/debit/{card_id}")
    def one(card_id: str, c=Depends(read)):
        return encode(_debit_view(own(card_id, c)))

    def apply(card_id: str, c, changes: dict, action: str, key, body: dict, fault):
        def run():
            row = own(card_id, c)
            if row["status"] == "blocked":
                return 409, {"detail": "card is hot-listed (permanently blocked)"}
            sets = ", ".join(f"{k} = %s" for k in changes)
            with bank.db.connection() as conn, conn.transaction():
                before = conn.execute("SELECT status, atm, pos, ecom, contactless, international FROM corebank.debit_cards "
                                      "WHERE id = %s FOR UPDATE", (card_id,)).fetchone()
                conn.execute(f"UPDATE corebank.debit_cards SET {sets}, updated_at = now() WHERE id = %s", (*changes.values(), card_id))
                after = conn.execute("SELECT status, atm, pos, ecom, contactless, international FROM corebank.debit_cards "
                                     "WHERE id = %s", (card_id,)).fetchone()
                reference = f"AU{bank.cfg.today:%y%m%d}{secrets.randbelow(10**6):06d}"
                conn.execute("INSERT INTO corebank.card_actions (reference, cif, card_kind, card_id, action, before, after) "
                             "VALUES (%s,%s,'debit',%s,%s,%s,%s)",
                             (reference, c.cif, card_id, action, json.dumps(before), json.dumps(after)))
            shown = "UNKNOWN_CODE_77" if fault and fault[0] == "unexpected_status" else DEBIT_STATUS[after["status"]]
            return 200, {"reference": reference, "card": _debit_view({**row, **after, "status": after["status"]}) | {"status": shown}}

        status, out = bank.idempotent(key, request_hash("POST", f"/cards/v1/debit/{card_id}/{action}", body | {"cif": c.cif}), run, fault)
        if status >= 400:
            raise HTTPException(status, out.get("detail", "rejected"))
        return out

    @r.post("/debit/{card_id}/status")
    def status(card_id: str, body: DebitStatusBody, c=Depends(write), idempotency_key: str | None = Header(None)):
        inverse = {v: k for k, v in DEBIT_STATUS.items()}
        if body.status not in inverse:
            raise HTTPException(422, f"status must be one of {list(inverse)}")
        return apply(card_id, c, {"status": inverse[body.status]}, "status", idempotency_key, body.model_dump(), c.fault)

    @r.post("/debit/{card_id}/controls")
    def controls(card_id: str, body: DebitControlsBody, c=Depends(write), idempotency_key: str | None = Header(None)):
        changes = {k: v for k, v in body.model_dump().items() if v is not None}
        if not changes:
            raise HTTPException(422, "no control given")
        return apply(card_id, c, changes, "controls", idempotency_key, body.model_dump(), c.fault)

    return r


# ───────────────────────── credit cards ─────────────────────────


class CreditBlockBody(BaseModel):
    type: str  # TEMPORARY | PERMANENT


class CreditInternationalBody(BaseModel):
    enabled: bool


def _credit_view(x: dict) -> dict:
    return {"card_id": x["id"], "last4": x["last4"], "product": x["variant"], "status": CREDIT_STATUS.get(x["status"], "UNKNOWN"),
            "credit_limit": x["credit_limit"], "outstanding": x["outstanding"],
            "last_statement": {"total_due": x["total_due"], "minimum_due": x["minimum_due"], "statement_date": x["statement_date"],
                               "due_date": x["due_date"]},
            "last_payment": {"on": x["last_payment_on"], "amount": x["last_payment_amount"], "status": x["last_payment_status"]},
            "previous_bill_paid": x["previous_bill_paid"],
            "fee": {"annual": x["annual_fee"], "charged_on": x["annual_fee_charged_on"], "waiver_spend": x["annual_fee_waiver_spend"],
                    "spend_last_year": x["spend_last_year"]},
            "lounge": {"per_quarter": x["lounge_per_quarter"], "used_quarter": x["lounge_used_quarter"]},
            "international": x["international"]}


def _cc(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/cc/v1", tags=["cc: credit card platform"])
    read = bank.caller("cc", "read")
    write = bank.caller("cc", "card_update")

    def own(card_id: str, c) -> dict:
        row = _one(bank, "SELECT * FROM corebank.credit_cards WHERE id = %s", card_id)
        if not row:
            raise HTTPException(404, "unknown card")
        bank.owns(c, row["cif"])
        return row

    @r.get("/cards")
    def listing(cif: str, c=Depends(read)):
        bank.owns(c, cif)
        return encode([_credit_view(x) for x in _rows(bank, "SELECT * FROM corebank.credit_cards WHERE cif = %s ORDER BY id", cif)])

    @r.get("/cards/{card_id}/transactions")
    def transactions(card_id: str, c=Depends(read)):
        own(card_id, c)
        rows = _rows(bank, "SELECT * FROM corebank.card_txns WHERE card_id = %s ORDER BY posted_at DESC", card_id)
        return encode([{"posted_at": t["posted_at"], "merchant": t["merchant"], "amount": t["amount"],
                        "status": SWITCH.get(t["status"], "UNKNOWN"), "international": t["international"]} for t in rows])

    def apply(card_id: str, c, changes: dict, action: str, key, body: dict, fault):
        def run():
            row = own(card_id, c)
            if row["status"] == "blocked":
                return 409, {"detail": "card is hot-listed (permanently blocked)"}
            sets = ", ".join(f"{k} = %s" for k in changes)
            with bank.db.connection() as conn, conn.transaction():
                before = conn.execute("SELECT status, international FROM corebank.credit_cards WHERE id = %s FOR UPDATE", (card_id,)).fetchone()
                conn.execute(f"UPDATE corebank.credit_cards SET {sets}, updated_at = now() WHERE id = %s", (*changes.values(), card_id))
                after = conn.execute("SELECT status, international FROM corebank.credit_cards WHERE id = %s", (card_id,)).fetchone()
                reference = f"AU{bank.cfg.today:%y%m%d}{secrets.randbelow(10**6):06d}"
                conn.execute("INSERT INTO corebank.card_actions (reference, cif, card_kind, card_id, action, before, after) "
                             "VALUES (%s,%s,'credit',%s,%s,%s,%s)",
                             (reference, c.cif, card_id, action, json.dumps(before), json.dumps(after)))
            shown = "UNKNOWN_CODE_77" if fault and fault[0] == "unexpected_status" else CREDIT_STATUS[after["status"]]
            return 200, {"reference": reference, "card_id": card_id, "status": shown, "international": after["international"]}

        status, out = bank.idempotent(key, request_hash("POST", f"/cc/v1/cards/{card_id}/{action}", body | {"cif": c.cif}), run, fault)
        if status >= 400:
            raise HTTPException(status, out.get("detail", "rejected"))
        return out

    @r.post("/cards/{card_id}/block")
    def block(card_id: str, body: CreditBlockBody, c=Depends(write), idempotency_key: str | None = Header(None)):
        if body.type not in ("TEMPORARY", "PERMANENT"):
            raise HTTPException(422, "type must be TEMPORARY or PERMANENT")
        status = "temp_blocked" if body.type == "TEMPORARY" else "blocked"
        return apply(card_id, c, {"status": status}, "block", idempotency_key, body.model_dump(), c.fault)

    @r.post("/cards/{card_id}/unblock")
    def unblock(card_id: str, c=Depends(write), idempotency_key: str | None = Header(None)):
        return apply(card_id, c, {"status": "active"}, "unblock", idempotency_key, {}, c.fault)

    @r.post("/cards/{card_id}/international")
    def international(card_id: str, body: CreditInternationalBody, c=Depends(write), idempotency_key: str | None = Header(None)):
        return apply(card_id, c, {"international": body.enabled}, "international", idempotency_key, body.model_dump(), c.fault)

    return r


# ───────────────────────── loans ─────────────────────────


def _lms(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/lms/v1", tags=["lms: loan management"])
    read = bank.caller("lms", "read")

    @r.get("/loans")
    def listing(cif: str, c=Depends(read)):
        bank.owns(c, cif)
        return encode([{
            "loan_no": x["number"], "product": x["type"], "rate_type": x["rate_type"].upper(), "interest_rate": x["rate"],
            "sanctioned": x["sanctioned"], "outstanding": x["outstanding"], "emi": x["emi"], "next_due": x["next_due"],
            "emis_paid": x["emis_paid"], "emis_left": x["emis_left"], "status": x["status"].upper(),
            "debit_account": x["debit_account"]} for x in _rows(bank, "SELECT * FROM corebank.loans WHERE cif = %s ORDER BY number", cif)])

    def own(number: str, c) -> None:
        row = _one(bank, "SELECT cif FROM corebank.loans WHERE number = %s", number)
        if not row:
            raise HTTPException(404, "unknown loan")
        bank.owns(c, row["cif"])

    @r.get("/loans/{number}/emis")
    def emis(number: str, c=Depends(read)):
        own(number, c)
        rows = _rows(bank, "SELECT * FROM corebank.loan_emis WHERE loan_number = %s ORDER BY due_date DESC", number)
        return encode([{"due_date": e["due_date"], "amount": e["amount"], "status": e["status"].upper(),
                        "failure_reason": e["reason"], "bounce_charge": e["charge"]} for e in rows])

    @r.get("/loans/{number}/rate-changes")
    def rate_changes(number: str, c=Depends(read)):
        own(number, c)
        rows = _rows(bank, "SELECT * FROM corebank.loan_rate_changes WHERE loan_number = %s ORDER BY changed_on DESC", number)
        return encode([{"changed_on": x["changed_on"], "old_rate": x["old_rate"], "new_rate": x["new_rate"],
                        "old_emi": x["old_emi"], "new_emi": x["new_emi"]} for x in rows])

    return r


# ───────────────────────── deposits ─────────────────────────


def _deposits(bank: MockBank) -> APIRouter:
    r = APIRouter(prefix="/deposits/v1", tags=["deposits: FD servicing"])
    read = bank.caller("deposits", "read")

    @r.get("/fds")
    def listing(cif: str, c=Depends(read)):
        bank.owns(c, cif)
        return encode([{
            "fd_no": f["number"], "principal": f["principal"], "interest_rate": f["rate"], "booked_on": f["opened_on"],
            "tenure_months": f["tenure_months"], "maturity_date": f["maturity_date"], "maturity_value": f["maturity_amount"],
            "payout": f["payout"].upper(), "auto_renew": f["auto_renew"], "status": f["status"].upper()}
            for f in _rows(bank, "SELECT * FROM corebank.fixed_deposits WHERE cif = %s ORDER BY maturity_date", cif)])

    return r


# ───────────────────────── CRM ─────────────────────────


class ComplaintBody(BaseModel):
    cif: str | None = None
    session_ref: str | None = None
    kind: str
    category: str
    department: str
    summary: str
    detail: dict = {}
    tat_days: int = 7


def _crm(bank: MockBank) -> APIRouter:
    from app.services.desk import LocalServiceDesk

    r = APIRouter(prefix="/crm/v1", tags=["crm: complaints and disputes"])
    write = bank.caller("crm", "crm", optional=True)  # a ticket for an unverified caller (callback, lead) has no customer
    desk = LocalServiceDesk(bank.db, bank.cfg.today)

    @r.post("/complaints", status_code=201)
    def create(body: ComplaintBody, c=Depends(write), idempotency_key: str | None = Header(None)):
        if body.cif:
            bank.owns(c, body.cif)

        def run():
            t = desk.create_ticket(session_id=body.session_ref, cif=body.cif, kind=body.kind, category=body.category,
                                   department=body.department, summary=body.summary, detail=body.detail, tat_days=body.tat_days)
            return 201, {"ticket_id": t["id"], "due_by": t["due_by"], "department": t["department"], "status": "OPEN"}

        status, out = bank.idempotent(idempotency_key, request_hash("POST", "/crm/v1/complaints", body.model_dump()), run, c.fault)
        return out

    @r.get("/complaints")
    def listing(cif: str, open: bool = True, days: int = 30, c=Depends(write)):
        bank.owns(c, cif)
        rows = _rows(bank, "SELECT id, kind, category, department, summary, status, due_by, created_at FROM tickets WHERE cif = %s "
                           "AND created_at > now() - make_interval(days => %s) AND (NOT %s OR status = 'open') ORDER BY created_at DESC",
                     cif, days, open)
        return encode([{"ticket_id": t["id"], "kind": t["kind"], "category": t["category"], "department": t["department"],
                        "summary": t["summary"], "status": t["status"].upper(), "due_by": t["due_by"], "created_at": t["created_at"]}
                       for t in rows])

    return r
