"""MOCK core banking: the bank systems the agent talks to, simulated in their own database schema (`corebank`).

Built once, deterministically (fixed random seed), from:
  - the hackathon dummy-data workbook (read-only; never modified): its 100 customers, accounts, balances,
    FD / loan / card values and transactions, cleaned (one row per product, consistent dates);
  - synthetic extensions the sample questions need (branch type, salary flag, holds, average balance, charges,
    channel / merchant / status on transactions, several FDs and loans, EMI history, two credit cards, card controls);
  - hand-made demo customers whose data matches the sample questions (account ending 4521 vs 4512, FD ending 7824,
    card ending 6712, the unrecognised ₹8,499, the failed EMI, the ATM that didn't dispense cash...).

Everything returned is simulated and labelled so. Calls take a realistic 50-150 ms and can be made to fail
(MOCK_BANK_FAIL=card_update | read) to demonstrate "the bank system is down" handling.
"""

import json
import logging
import random
import secrets
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app.db import Database
from app.domain import (
    Account, CardTxn, Charge, CreditCard, Customer, DebitCard, Emi, FixedDeposit, Loan, RateChange, Transaction,
)
from app.logs import log
from app.ports import BankError

logger = logging.getLogger(__name__)
SEED = 20261105

BRANCHES = [("JPR-MNR", "Jaipur Malviya Nagar", "Jaipur", "urban"), ("MUM-AND", "Mumbai Andheri", "Mumbai", "urban"),
            ("DEL-CP", "Delhi Connaught Place", "Delhi", "urban"), ("AJM-01", "Ajmer", "Ajmer", "semi_urban"),
            ("UDR-01", "Udaipur", "Udaipur", "semi_urban"), ("SKR-01", "Sikar", "Sikar", "rural")]
BRANCH_TYPE = {b[0]: b[3] for b in BRANCHES}
DEBIT_VARIANT = {"BSBDA": "RuPay Classic", "Digital": "RuPay Platinum", "Vikaas": "RuPay Classic", "Value": "Visa Gold",
                 "Platinum": "Platinum Debit Card", "Royale": "Royale Debit Card"}
# Card terms from the CC KB: annual fee, the yearly spend that waives it, lounge visits per quarter.
CARD_TERMS = {"Zenith+": (4999, 800000, 4), "Ananta": (2000, 300000, 4), "Laksya": (1000, 200000, 2)}
RMS = ["Rohan Desai", "Priya Menon", "Aditya Rao"]
MERCHANTS = ["Swiggy", "Zomato", "Amazon", "Flipkart", "BigBasket", "Reliance Smart", "DMart", "Uber", "Ola",
             "BookMyShow", "Myntra", "Apollo Pharmacy", "Indian Oil", "Croma"]
PEOPLE = ["Rajesh Sharma", "Sunil Verma", "Anita Gupta", "Mohit Jain", "Kiran Patel", "Deepak Yadav"]
EMPLOYERS = ["Infosys Ltd", "Tata Consultancy Services", "Wipro Ltd", "HDFC Life", "Jaipur Rugs Pvt Ltd"]


def amb_required(variant: str, branch_type: str) -> float:
    """Average monthly balance from the CASA KB (Value depends on the branch type)."""
    if variant == "Value":
        return 10000 if branch_type == "urban" else 5000
    return {"Vikaas": 1000, "Platinum": 25000, "Royale": 100000}.get(variant, 0)


def amb_charge(variant: str, shortfall: float) -> float:
    """6% of the shortfall, capped (₹50 for Vikaas, ₹600 otherwise), from the CASA KB."""
    return round(min(shortfall * 0.06, 50 if variant == "Vikaas" else 600))


def ensure_seeded(db: Database, workbook: Path | None, today: date) -> None:
    """Seed the mock bank once (deterministic). Does nothing when it already has customers.
    Under the startup lock, so two copies starting together on an empty database don't both seed."""
    with db.startup_lock() as conn:
        # The data is written relative to "today" ("₹5,000 from Rajesh today", "UPI payments this week"). Seeded on an
        # earlier day it goes stale: on 16 Nov the live mock bank still held data seeded on 6 Nov, so "did Rajesh pay me
        # today?" truly found nothing (call d48a5afe). It is re-seeded for today's date (changes made in test calls,
        # such as blocked cards and tickets in the mock CRM, go with it: it is test data).
        conn.execute("CREATE TABLE IF NOT EXISTS corebank.seed_info (seeded_for date NOT NULL)")
        seeded = conn.execute("SELECT count(*) AS n FROM corebank.customers").fetchone()["n"]
        row = conn.execute("SELECT seeded_for FROM corebank.seed_info LIMIT 1").fetchone()
        if seeded and (row is None or row["seeded_for"] != today):
            tables = [r["table_name"] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'corebank' AND table_type = 'BASE TABLE' "
                "AND table_name <> 'seed_info'").fetchall()]
            conn.execute(f"TRUNCATE {', '.join(f'corebank.{t}' for t in tables)} RESTART IDENTITY CASCADE")
            log(logger, "mock core banking re-seeded for today", was=str(row["seeded_for"]) if row else "unknown",
                today=str(today))
            seeded = 0
        if not seeded:
            t = time.perf_counter()
            counts = _Seeder(db, workbook, today).run()
            conn.execute("DELETE FROM corebank.seed_info")
            conn.execute("INSERT INTO corebank.seed_info (seeded_for) VALUES (%s)", (today,))
            log(logger, "mock core banking seeded", **counts, ms=round((time.perf_counter() - t) * 1000))


class MockCoreBank:
    def __init__(self, db: Database, workbook: Path | None, today: date, fail: str = "", latency_ms: tuple = (50, 150)):
        self.db, self.today, self.fail, self.latency = db, today, fail, latency_ms
        ensure_seeded(db, workbook, today)

    # ── reads ──
    def _call(self, kind: str) -> None:
        if self.fail in (kind, "all"):
            raise BankError(f"core banking {kind} unavailable (simulated by MOCK_BANK_FAIL)")
        time.sleep(random.uniform(*self.latency) / 1000)

    def find_by_mobile(self, mobile: str) -> Customer | None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT cif FROM corebank.customers WHERE mobile = %s", (_mobile(mobile),)).fetchone()
        return self.customer(row["cif"]) if row else None

    def customer(self, cif: str) -> Customer | None:
        self._call("read")
        with self.db.connection() as conn:
            c = conn.execute("SELECT c.*, b.type AS branch_type FROM corebank.customers c JOIN corebank.branches b "
                             "ON b.id = c.branch_id WHERE cif = %s", (cif,)).fetchone()
            if not c:
                return None
            accts = conn.execute("SELECT a.*, b.type AS branch_type FROM corebank.accounts a JOIN corebank.branches b "
                                 "ON b.id = a.branch_id WHERE cif = %s ORDER BY number", (cif,)).fetchall()
            numbers = [a["number"] for a in accts]
            txns = conn.execute("SELECT * FROM corebank.account_txns WHERE account_number = ANY(%s) "
                                "ORDER BY posted_at DESC", (numbers,)).fetchall()
            charges = conn.execute("SELECT * FROM corebank.account_charges WHERE account_number = ANY(%s) "
                                   "ORDER BY charged_on DESC", (numbers,)).fetchall()
            dcs = conn.execute("SELECT * FROM corebank.debit_cards WHERE account_number = ANY(%s)", (numbers,)).fetchall()
            fds = conn.execute("SELECT * FROM corebank.fixed_deposits WHERE cif = %s ORDER BY maturity_date", (cif,)).fetchall()
            loans = conn.execute("SELECT * FROM corebank.loans WHERE cif = %s ORDER BY number", (cif,)).fetchall()
            loan_nos = [x["number"] for x in loans]
            emis = conn.execute("SELECT * FROM corebank.loan_emis WHERE loan_number = ANY(%s) ORDER BY due_date DESC",
                                (loan_nos,)).fetchall()
            rates = conn.execute("SELECT * FROM corebank.loan_rate_changes WHERE loan_number = ANY(%s) "
                                 "ORDER BY changed_on DESC", (loan_nos,)).fetchall()
            ccs = conn.execute("SELECT * FROM corebank.credit_cards WHERE cif = %s ORDER BY id", (cif,)).fetchall()
            cc_txns = conn.execute("SELECT * FROM corebank.card_txns WHERE card_id = ANY(%s) ORDER BY posted_at DESC",
                                   ([x["id"] for x in ccs],)).fetchall()

        accounts = []
        for a in accts:
            card = next((d for d in dcs if d["account_number"] == a["number"]), None)
            accounts.append(Account(
                number=a["number"], type=a["variant"], balance=float(a["ledger_balance"]),
                available=float(a["available_balance"]), hold_amount=float(a["hold_amount"]), hold_reason=a["hold_reason"],
                status=a["status"], is_salary=a["is_salary"], branch_type=a["branch_type"],
                amb_required=float(a["amb_required"]), amb_this_month=_f(a["amb_this_month"]),
                amb_last_month=_f(a["amb_last_month"]),
                transactions=[_txn(x) for x in txns if x["account_number"] == a["number"]],
                charges=[Charge(x["charged_on"], x["type"], float(x["amount"]), x["reversed"])
                         for x in charges if x["account_number"] == a["number"]],
                debit_card=DebitCard(card["id"], card["last4"], card["variant"], card["status"], card["atm"], card["pos"],
                                     card["ecom"], card["contactless"], card["international"], a["number"][-4:]) if card else None))
        return Customer(
            cif=c["cif"], name=c["name"], mobile=c["mobile"], accounts=accounts, segment=c["segment"],
            rm_name=c["rm_name"], language=c["language"],
            fds=[FixedDeposit(f["number"], float(f["principal"]), float(f["rate"]), f["opened_on"], f["tenure_months"],
                              f["maturity_date"], float(f["maturity_amount"]), f["payout"], f["auto_renew"], f["status"])
                 for f in fds],
            loans=[Loan(x["number"], x["type"], x["rate_type"], float(x["rate"]), float(x["sanctioned"]),
                        float(x["outstanding"]), float(x["emi"]), x["next_due"], x["emis_paid"], x["emis_left"],
                        x["status"], x["debit_account"],
                        emis=[Emi(e["due_date"], float(e["amount"]), e["status"], e["reason"], float(e["charge"]), e.get("paid_on"))
                              for e in emis if e["loan_number"] == x["number"]],
                        rate_changes=[RateChange(r["changed_on"], float(r["old_rate"]), float(r["new_rate"]),
                                                 float(r["old_emi"]), float(r["new_emi"]))
                                      for r in rates if r["loan_number"] == x["number"]])
                   for x in loans],
            credit_cards=[CreditCard(
                id=x["id"], last4=x["last4"], variant=x["variant"], status=x["status"], limit=float(x["credit_limit"]),
                outstanding=float(x["outstanding"]), total_due=float(x["total_due"]), minimum_due=float(x["minimum_due"]),
                statement_date=x["statement_date"], due_date=x["due_date"], last_payment_on=x["last_payment_on"],
                last_payment_amount=_f(x["last_payment_amount"]), last_payment_status=x["last_payment_status"],
                previous_bill_paid=x["previous_bill_paid"], annual_fee=_f(x["annual_fee"]),
                annual_fee_charged_on=x["annual_fee_charged_on"], annual_fee_waiver_spend=_f(x["annual_fee_waiver_spend"]),
                spend_last_year=_f(x["spend_last_year"]), lounge_per_quarter=x["lounge_per_quarter"],
                lounge_used_quarter=x["lounge_used_quarter"], international=x["international"],
                transactions=[CardTxn(t["posted_at"].date(), t["merchant"], float(t["amount"]), t["status"], t["international"])
                              for t in cc_txns if t["card_id"] == x["id"]]) for x in ccs])

    def demo_callers(self, limit: int = 14) -> list[dict]:
        """The hand-made demo customers first (each with the story their data tells), then a few others."""
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT c.cif, c.name, c.mobile, c.scenario, "
                "(SELECT count(*) FROM corebank.accounts a WHERE a.cif = c.cif) AS accounts, "
                "EXISTS (SELECT 1 FROM corebank.fixed_deposits f WHERE f.cif = c.cif AND f.status = 'active') AS fd, "
                "EXISTS (SELECT 1 FROM corebank.loans l WHERE l.cif = c.cif) AS loan, "
                "EXISTS (SELECT 1 FROM corebank.debit_cards d JOIN corebank.accounts a ON a.number = d.account_number "
                "        WHERE a.cif = c.cif) AS debit_card, "
                "EXISTS (SELECT 1 FROM corebank.credit_cards k WHERE k.cif = c.cif) AS credit_card "
                "FROM corebank.customers c ORDER BY (c.scenario IS NULL), c.cif LIMIT %s", (limit,)).fetchall()
        return [{k: r[k] for k in ("name", "mobile", "accounts", "fd", "loan", "debit_card", "credit_card", "scenario")}
                for r in rows]

    # ── the one kind of write the agent may make ──
    def update_card(self, cif: str, kind: str, card_id: str, changes: dict, action: str, key: str | None = None) -> dict:
        self._call("card_update")
        table = "corebank.debit_cards" if kind == "debit" else "corebank.credit_cards"
        allowed = {"status", "atm", "pos", "ecom", "contactless", "international"} if kind == "debit" else {"status", "international"}
        changes = {k: v for k, v in changes.items() if k in allowed}
        if not changes:
            raise BankError("nothing to change")
        owner = ("SELECT 1 FROM corebank.debit_cards d JOIN corebank.accounts a ON a.number = d.account_number "
                 "WHERE d.id = %s AND a.cif = %s") if kind == "debit" else "SELECT 1 FROM corebank.credit_cards WHERE id = %s AND cif = %s"
        cols = sorted(allowed)
        with self.db.connection() as conn, conn.transaction():
            if not conn.execute(owner, (card_id, cif)).fetchone():
                raise BankError("card not found for this customer")
            before = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id = %s FOR UPDATE", (card_id,)).fetchone()
            if before["status"] == "blocked":
                raise BankError("card is permanently blocked")
            sets = ", ".join(f"{k} = %s" for k in changes)
            conn.execute(f"UPDATE {table} SET {sets}, updated_at = now() WHERE id = %s", (*changes.values(), card_id))
            after = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE id = %s", (card_id,)).fetchone()
            reference = f"AU{self.today:%y%m%d}{secrets.randbelow(10**6):06d}"
            conn.execute("INSERT INTO corebank.card_actions (reference, cif, card_kind, card_id, action, before, after) "
                         "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                         (reference, cif, kind, card_id, action, json.dumps(dict(before)), json.dumps(dict(after))))
        return {"reference": reference, "before": dict(before), "after": dict(after)}


# ───────────────────────── seeding ─────────────────────────


class _Seeder:
    def __init__(self, db: Database, workbook: Path | None, today: date):
        self.db, self.workbook, self.today = db, workbook, today
        self.rng = random.Random(SEED)
        self.rows: dict[str, list[tuple]] = {}
        self.used_last4: set[str] = set()
        self.ref = 0

    # helpers
    def add(self, table: str, row: tuple) -> None:
        self.rows.setdefault(table, []).append(row)

    def last4(self, preferred: str | None = None) -> str:
        v = preferred if preferred and preferred not in self.used_last4 else None
        while v is None or v in self.used_last4:
            v = f"{self.rng.randint(0, 9999):04d}"
        self.used_last4.add(v)
        return v

    def reference(self, channel: str) -> str:
        self.ref += 1
        return f"{channel[:3].upper()}{self.today:%y%m}{self.ref:07d}"

    def at(self, d: date, hour: int | None = None, minute: int | None = None) -> datetime:
        return datetime(d.year, d.month, d.day, hour if hour is not None else self.rng.randint(8, 21),
                        minute if minute is not None else self.rng.randint(0, 59)).astimezone()

    def txn(self, account: str, when: datetime, desc: str, kind: str, amount: float, channel: str,
            counterparty: str | None = None, status: str = "success", note: str | None = None) -> None:
        self.add("account_txns", (account, when, desc, kind, round(amount, 2), channel, counterparty,
                                  self.reference(channel), status, note))

    def run(self) -> dict:
        for b in BRANCHES:
            self.add("branches", b)
        self.scenarios()  # first, so their card endings (6712, 7824...) are guaranteed
        if self.workbook and Path(self.workbook).is_file():
            self.from_workbook()
        with self.db.connection() as conn, conn.transaction():
            for table in ("branches", "customers", "accounts", "account_txns", "account_charges", "debit_cards",
                          "credit_cards", "card_txns", "fixed_deposits", "loans", "loan_emis", "loan_rate_changes"):
                rows = self.rows.get(table, [])
                if not rows:
                    continue
                cols = _COLUMNS[table]
                with conn.cursor() as cur:
                    cur.executemany(f"INSERT INTO corebank.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})", rows)
        return {t: len(r) for t, r in self.rows.items() if t in ("customers", "accounts", "account_txns", "fixed_deposits", "loans", "credit_cards")}

    # ── the workbook's customers, cleaned and extended ──
    def from_workbook(self) -> None:
        from openpyxl import load_workbook

        wb = load_workbook(self.workbook, read_only=True, data_only=True)
        try:
            master = _records(wb["Customer_Master"])
            txns = _records(wb["Last_5_Transactions"])
        finally:
            wb.close()
        by_account: dict[str, list[dict]] = {}
        for r in txns:
            by_account.setdefault(str(r["Account No"]), []).append(r)
        rng, today = self.rng, self.today

        for r in master:
            cif = str(r["CIF ID"])
            branch = rng.choice(BRANCHES)[0]
            variants = [r.get(f"Savings Acct {n} Type") for n in (1, 2, 3) if r.get(f"Savings Acct {n}")]
            segment = "priority" if "Royale" in variants else "regular"
            self.add("customers", (cif, str(r["Customer Name"]), _mobile(r["Mobile No"]), segment, branch, None,
                                   rng.choice(RMS) if segment == "priority" else None, None))
            first_account = None
            for n in (1, 2, 3):
                number = r.get(f"Savings Acct {n}")
                if not number:
                    continue
                number, variant, balance = str(number), str(r[f"Savings Acct {n} Type"]), float(r[f"Balance {n}"] or 0)
                first_account = first_account or number
                rows = sorted(by_account.get(number, []), key=lambda x: _date(x["Txn Date"]))
                is_salary = any(str(x["Txn Description"]).startswith("Salary") for x in rows)
                employer = rng.choice(EMPLOYERS)
                for x in rows:
                    d = min(_date(x["Txn Date"]), today)
                    desc, kind = str(x["Txn Description"]), str(x["Txn Type"])
                    channel, who = _channel(desc), None
                    if desc.startswith("Salary"):
                        who = employer
                    elif channel in ("UPI", "IMPS", "NEFT"):
                        who = rng.choice(PEOPLE + MERCHANTS[:4])
                    elif channel in ("POS", "ECOM") or desc in ("Bill Payment", "Mobile Recharge"):
                        who = rng.choice(MERCHANTS)
                    self.txn(number, self.at(d), desc, kind, float(x["Amount"]), channel, who)
                for _ in range(rng.randint(2, 4)):  # a little recent activity, so "this week" questions have answers
                    d = today - timedelta(days=rng.randint(0, 9))
                    m = rng.choice(MERCHANTS)
                    self.txn(number, self.at(d), "UPI Payment", "Debit", rng.choice([149, 249, 399, 520, 860, 1240, 2150]),
                             "UPI", m)
                req = amb_required(variant, BRANCH_TYPE[branch])
                last_amb = round(balance * rng.uniform(0.5, 1.1))
                hold = round(balance * rng.uniform(0.02, 0.08)) if rng.random() < 0.12 else 0
                self.add("accounts", (number, cif, variant, branch, "active", is_salary,
                                      today - timedelta(days=rng.randint(300, 2500)), balance, balance - hold, hold,
                                      rng.choice(["a cheque deposit that is still clearing", "a pending card payment"]) if hold else None,
                                      req, round(balance * rng.uniform(0.7, 1.05)), last_amb))
                if req and last_amb < req:
                    self.add("account_charges", (number, today.replace(day=5), "amb_shortfall",
                                                 amb_charge(variant, req - last_amb), False))
                preferred = str(r["Debit Card Last4"]).zfill(4) if n == 1 and r.get("Debit Card Last4") else None
                on = (lambda k: str(r.get(k) or "").lower() == "enabled") if n == 1 else (lambda k: True)
                self.add("debit_cards", (f"DC{number}", number, self.last4(preferred), DEBIT_VARIANT.get(variant, "RuPay Classic"),
                                         "active", on("ATM"), on("POS"), on("ECOM"), on("POS"), rng.random() < 0.2))

            if r.get("FD Balance"):
                self.fd(cif, float(r["FD Balance"]), float(str(r["FD Interest Rate"]).strip("%")), _date(r["FD Maturity Date"]))
                for _ in range(rng.choice([0, 0, 1, 2])):
                    self.fd(cif, rng.choice([50000, 100000, 200000, 500000]), rng.choice([7.25, 7.5, 7.6, 7.75]),
                            today + timedelta(days=rng.randint(20, 900)))
            if r.get("Loan Balance"):
                out = float(r["Loan Balance"])
                kind = "home" if out >= 1500000 else "vehicle" if out >= 400000 else "personal"
                self.loan(cif, kind, out, float(r["EMI Amount"] or 0), int(r["EMIs Paid"] or 0), int(r["EMIs Left"] or 0),
                          _date(r["Emi Due Date"]), first_account)
            if r.get("Credit Card Last4"):
                variant = r.get("Credit Card Type") or rng.choice(list(CARD_TERMS))
                self.credit_card(cif, variant, float(r["CC Limit"] or 100000), float(r["CC Outstanding"] or 0),
                                 str(r["Credit Card Last4"]).zfill(4))

    def fd(self, cif: str, principal: float, rate: float, maturity: date | None, number: str | None = None,
           tenure: int | None = None, auto_renew: bool | None = None, payout: str = "cumulative") -> None:
        rng = self.rng
        tenure = tenure or rng.choice([12, 13, 18, 24, 36])
        maturity = maturity or self.today + timedelta(days=200)
        opened = maturity - timedelta(days=round(tenure * 30.44))
        amount = round(principal * (1 + rate / 400) ** (tenure / 3)) if payout == "cumulative" else principal
        number = number or f"7{rng.randint(10**10, 10**11 - 1)}"
        self.add("fixed_deposits", (number, cif, principal, rate, opened, tenure, maturity, amount, payout,
                                    rng.random() < 0.5 if auto_renew is None else auto_renew,
                                    "matured" if maturity < self.today else "active"))

    def loan(self, cif: str, kind: str, outstanding: float, emi: float, paid: int, left: int, due: date | None,
             account: str | None, number: str | None = None, bounce: bool | None = None, rate_change: tuple | None = None,
             rate: float | None = None) -> str:
        rng, today = self.rng, self.today
        rate_type = "floating" if kind == "home" else "fixed"
        rate = rate or {"home": 9.25, "vehicle": 10.5, "personal": 13.5, "gold": 11.0, "business": 14.0}[kind]
        number = number or f"L{kind[:2].upper()}{rng.randint(10**7, 10**8 - 1)}"
        day = (due.day if due else 5)
        next_due = date(today.year + (today.month == 12), today.month % 12 + 1, min(day, 28))
        emi = emi or round(outstanding / max(left, 12))
        self.add("loans", (number, cif, kind, rate_type, rate, round(outstanding * rng.uniform(1.3, 2.2), -3), outstanding,
                           emi, next_due, paid, left, account, "active"))
        bounced = rng.random() < 0.1 if bounce is None else bounce
        for k in range(6, 0, -1):  # the last six EMIs
            month = (today.month - k) % 12 + 1
            year = today.year - (1 if today.month - k < 0 else 0)
            d = date(year, month, min(day, 28))
            failed = bounced and k == 1
            self.add("loan_emis", (number, d, emi, "bounced" if failed else "paid",
                                   "insufficient_balance" if failed else None,
                                   590 if failed else 0, None if failed else d))  # NACH debits on the due date
        if rate_change or (rate_type == "floating" and rng.random() < 0.5):
            old_rate, old_emi = rate_change or (round(rate - 0.5, 2), round(emi * 0.965))
            self.add("loan_rate_changes", (number, today - timedelta(days=75), old_rate, rate, old_emi, emi))
        return number

    def credit_card(self, cif: str, variant: str, limit: float, outstanding: float, preferred: str | None = None,
                    **overrides) -> str:
        rng, today = self.rng, self.today
        fee, waiver, lounge = CARD_TERMS.get(variant, (0, None, 0))
        card_id = f"CC{cif}{len([x for x in self.rows.get('credit_cards', []) if x[1] == cif]) + 1}"
        statement = today - timedelta(days=rng.randint(5, 20))
        total_due = round(outstanding * rng.uniform(0.6, 1.0))
        row = {"id": card_id, "cif": cif, "last4": self.last4(preferred), "variant": variant, "status": "active",
               "credit_limit": limit, "outstanding": outstanding, "total_due": total_due,
               "minimum_due": max(round(total_due * 0.05), 200) if total_due else 0, "statement_date": statement,
               "due_date": statement + timedelta(days=20), "last_payment_on": statement - timedelta(days=rng.randint(3, 15)),
               "last_payment_amount": round(total_due * rng.uniform(0.3, 1.0), -2), "last_payment_status": "posted",
               "previous_bill_paid": rng.random() < 0.8, "annual_fee": fee or None,
               "annual_fee_charged_on": today - timedelta(days=rng.randint(30, 300)) if fee else None,
               "annual_fee_waiver_spend": waiver, "spend_last_year": round(rng.uniform(0.3, 1.2) * (waiver or 300000), -3),
               "lounge_per_quarter": lounge, "lounge_used_quarter": rng.randint(0, lounge) if lounge else 0,
               "international": rng.random() < 0.3}
        row.update(overrides)
        self.add("credit_cards", tuple(row[c] for c in _COLUMNS["credit_cards"]))
        for _ in range(rng.randint(4, 8)):
            self.add("card_txns", (card_id, self.at(today - timedelta(days=rng.randint(0, 35))), rng.choice(MERCHANTS),
                                   rng.choice([349, 899, 1499, 2340, 4120, 650, 7800]), "success", False))
        return card_id

    # ── demo customers: their data is shaped by the sample questions ──
    def scenarios(self) -> None:
        today, d = self.today, lambda n: self.today - timedelta(days=n)

        def customer(cif, name, mobile, segment, branch, story, language=None, rm=None):
            self.add("customers", (cif, name, mobile, segment, branch, language, rm, story))

        def account(number, cif, variant, branch, balance, *, salary=False, hold=0, hold_reason=None, amb_this=None,
                    amb_last=None, status="active"):
            req = amb_required(variant, BRANCH_TYPE[branch])
            self.add("accounts", (number, cif, variant, branch, status, salary, d(900), balance, balance - hold, hold,
                                  hold_reason, req, amb_this if amb_this is not None else balance,
                                  amb_last if amb_last is not None else balance))

        def debit(number, last4, variant, status="active", atm=True, pos=True, ecom=True, contactless=True, intl=False):
            self.add("debit_cards", (f"DC{number}", number, self.last4(last4), variant, status, atm, pos, ecom, contactless, intl))

        # 1. Ravi: two accounts (4521 salary, 4512), home + personal loan, two credit cards (6712), payments in flight
        customer("CIF900101", "Ravi Kumar", "9000000101", "regular", "JPR-MNR",
                 "Two accounts ending 4521 (salary) and 4512; home and personal loan (one EMI bounced); two credit "
                 "cards incl. 6712 with a payment still processing; ATM cash not dispensed; ₹10,000 transfer pending")
        account("10020094521", "CIF900101", "Value", "JPR-MNR", 82350, salary=True, hold=2500,
                hold_reason="a pending card payment at a fuel station", amb_this=61200, amb_last=58400)
        account("10020094512", "CIF900101", "Digital", "JPR-MNR", 1240, status="inactive", amb_this=1240, amb_last=1240)
        debit("10020094521", "3390", "Visa Gold", intl=False)
        debit("10020094512", "7781", "RuPay Platinum")
        self.txn("10020094521", self.at(today.replace(day=1), 10, 5), "Salary Credit", "Credit", 78500, "NEFT", "Infosys Ltd")
        self.txn("10020094521", self.at(today.replace(day=5), 6, 0), "EMI auto-debit returned", "Debit", 7850, "NACH",
                 "AU Personal Loan", status="failed", note=json.dumps({"key": "emi_insufficient", "time": "6:00 am", "balance": 4120, "credit_time": "10:05 am"}))
        # Cash not dispensed: the account WAS debited and the money comes back by the due date (RBI TAT). Before 23 Nov this
        # was "failed", so the agent said "no money was deducted" and then "the ₹10,000 will come back" (call 3eab4732).
        self.txn("10020094521", self.at(d(2), 19, 30), "ATM Withdrawal", "Debit", 10000, "ATM", "AU ATM, MI Road Jaipur",
                 status="success", note=json.dumps({"key": "atm_not_dispensed", "due": str(_working_days(d(2), 5))}))
        self.txn("10020094521", self.at(d(1), 13, 10), "POS Purchase", "Debit", 1980, "POS", "Reliance Smart")
        self.txn("10020094521", self.at(d(1), 21, 40), "POS Purchase", "Debit", 1250, "POS", "Barbeque Nation")
        self.txn("10020094521", self.at(today, 9, 15), "UPI Transfer", "Debit", 10000, "UPI", "Suresh Patel",
                 status="pending", note=json.dumps({"key": "upi_pending"}))
        self.txn("10020094512", self.at(d(240), 11, 0), "Cash Deposit", "Credit", 1500, "CASH")
        self.add("account_charges", ("10020094512", today.replace(day=5), "sms_alerts", 15, False))
        self.loan("CIF900101", "home", 2840000, 25140, 41, 199, date(today.year, today.month, 10), "10020094521",
                  number="LHO900101", bounce=False, rate_change=(8.75, 24310), rate=9.25)
        self.loan("CIF900101", "personal", 186000, 7850, 14, 22, date(today.year, today.month, 5), "10020094521",
                  number="LPE900102", bounce=True)
        card = self.credit_card("CIF900101", "Zenith+", 300000, 48300, "6712", total_due=41250, minimum_due=2063,
                                last_payment_on=d(1), last_payment_amount=15000, last_payment_status="processing",
                                previous_bill_paid=True, lounge_used_quarter=1, international=True)
        self.add("card_txns", (card, self.at(d(3)), "MakeMyTrip", 18450, "success", False))
        self.credit_card("CIF900101", "Laksya", 80000, 6400, "2208", international=False)

        # 2. Meera: senior citizen, three FDs (7824 maturing next month)
        customer("CIF900102", "Meera Iyer", "9000000102", "senior", "MUM-AND",
                 "Senior citizen with three FDs: 7824 matures next month (auto-renews), one later, one already matured")
        account("10020095001", "CIF900102", "Platinum", "MUM-AND", 142600, amb_this=139000, amb_last=141500)
        debit("10020095001", "4471", "Platinum Debit Card")
        self.txn("10020095001", self.at(d(20), 11, 0), "FD maturity proceeds", "Credit", 214380, "INTEREST", "FD ending 1102")
        self.fd("CIF900102", 500000, 7.75, today + timedelta(days=21), number="70000057824", tenure=13, auto_renew=True)
        self.fd("CIF900102", 200000, 7.60, today + timedelta(days=170), number="70000053310", tenure=24, auto_renew=False,
                payout="quarterly")
        self.fd("CIF900102", 200000, 7.25, d(20), number="70000051102", tenure=12, auto_renew=False)

        # 3. Arjun: debit card temporarily blocked yesterday; shops declining; travelling abroad
        customer("CIF900103", "Arjun Mehta", "9000000103", "regular", "DEL-CP",
                 "Debit card 5521 temporarily blocked yesterday; shop and online payments off; international off")
        account("10020096001", "CIF900103", "Digital", "DEL-CP", 56300)
        debit("10020096001", "5521", "RuPay Platinum", status="temp_blocked", atm=True, pos=False, ecom=False,
              contactless=False, intl=False)
        for minute in (2, 4):
            self.txn("10020096001", self.at(d(1), 18, minute), "POS Purchase declined", "Debit", 3450, "POS", "Croma",
                     status="failed", note=json.dumps({"key": "declined_blocked"}))

        # 4. Kavita: Royale priority customer with an RM; annual fee charged
        customer("CIF900104", "Kavita Singh", "9000000104", "priority", "JPR-MNR",
                 "Royale priority customer with RM Rohan Desai; Zenith+ annual fee charged (spend below the waiver)",
                 rm="Rohan Desai")
        account("10020097001", "CIF900104", "Royale", "JPR-MNR", 340500, amb_this=318000, amb_last=325000)
        debit("10020097001", "8102", "Royale Debit Card", intl=True)
        self.fd("CIF900104", 2000000, 7.75, today + timedelta(days=300), number="70000067001", tenure=24)
        self.credit_card("CIF900104", "Zenith+", 800000, 92000, "9051", annual_fee_charged_on=d(12), spend_last_year=620000)

        # 5. Imran: average balance shortfall charge; unrecognised ₹8,499 card spend; ₹799 debit; double charge
        customer("CIF900105", "Imran Khan", "9000000105", "regular", "AJM-01",
                 "Value account with a minimum-balance charge; unrecognised ₹8,499 international card spend; "
                 "₹799 subscription debit; same purchase charged twice")
        account("10020098001", "CIF900105", "Value", "AJM-01", 6200, amb_this=5400, amb_last=3800)
        debit("10020098001", "6634", "Visa Gold")
        self.add("account_charges", ("10020098001", today.replace(day=5), "amb_shortfall", amb_charge("Value", 5000 - 3800), False))
        self.txn("10020098001", self.at(d(1), 23, 50), "Subscription", "Debit", 799, "ECOM", "Streamify Premium")
        for second in (0, 1):
            self.txn("10020098001", self.at(d(3), 20, 14 + second), "POS Purchase", "Debit", 1499, "ECOM", "Myntra")
        card = self.credit_card("CIF900105", "Ananta", 150000, 21400, "4410", international=True)
        self.add("card_txns", (card, self.at(d(1), 3, 12), "GLOBALTECH STORE SINGAPORE", 8499, "success", True))

        # 6. Pooja: salary, refund, money from Rajesh today, UPI spending this week
        customer("CIF900106", "Pooja Nair", "9000000106", "regular", "MUM-AND",
                 "Salary credited on the 1st; Amazon refund; ₹5,000 from Rajesh today; several UPI payments this week")
        account("10020099001", "CIF900106", "Digital", "MUM-AND", 48900, salary=True)
        debit("10020099001", "2290", "RuPay Platinum")
        self.txn("10020099001", self.at(today.replace(day=1), 9, 30), "Salary Credit", "Credit", 40000, "NEFT",
                 "Tata Consultancy Services")
        self.txn("10020099001", self.at(d(2), 12, 0), "Refund Credit", "Credit", 2349, "UPI", "Amazon")
        self.txn("10020099001", self.at(today, 8, 45), "UPI Transfer", "Credit", 5000, "UPI", "Rajesh Sharma")
        for back, amount, who in ((0, 320, "Swiggy"), (1, 1240, "BigBasket"), (2, 180, "Uber"), (3, 2200, "Croma")):
            self.txn("10020099001", self.at(d(back)), "UPI Payment", "Debit", amount, "UPI", who)

        # 7. Sunita: basic account, Hindi, gold loan
        customer("CIF900107", "Sunita Devi", "9000000107", "regular", "SKR-01",
                 "Basic (BSBDA) account at a rural branch, prefers Hindi; gold loan", language="hi")
        account("10020099501", "CIF900107", "BSBDA", "SKR-01", 8450)
        debit("10020099501", "1180", "RuPay Classic")
        # her account had no transactions at all: "last five transactions" could only ever say "none" (data audit, 17 Nov)
        self.txn("10020099501", self.at(d(1), 11, 20), "UPI Payment", "Debit", 450, "UPI", "Sharma Kirana Store")
        self.txn("10020099501", self.at(d(3), 10, 5), "Cash Deposit", "Credit", 3000, "CASH")
        self.txn("10020099501", self.at(d(6), 16, 40), "UPI Payment", "Debit", 1200, "UPI", "Jain Medical")
        self.txn("10020099501", self.at(d(12), 12, 30), "UPI Transfer", "Credit", 2000, "UPI", "Ramesh Devi")
        self.loan("CIF900107", "gold", 60000, 5400, 4, 8, date(today.year, today.month, 15), "10020099501",
                  number="LGO900107", bounce=False)


_COLUMNS = {
    "branches": ("id", "name", "city", "type"),
    "customers": ("cif", "name", "mobile", "segment", "branch_id", "language", "rm_name", "scenario"),
    "accounts": ("number", "cif", "variant", "branch_id", "status", "is_salary", "opened_on", "ledger_balance",
                 "available_balance", "hold_amount", "hold_reason", "amb_required", "amb_this_month", "amb_last_month"),
    "account_txns": ("account_number", "posted_at", "description", "kind", "amount", "channel", "counterparty",
                     "reference", "status", "note"),
    "account_charges": ("account_number", "charged_on", "type", "amount", "reversed"),
    "debit_cards": ("id", "account_number", "last4", "variant", "status", "atm", "pos", "ecom", "contactless", "international"),
    "credit_cards": ("id", "cif", "last4", "variant", "status", "credit_limit", "outstanding", "total_due", "minimum_due",
                     "statement_date", "due_date", "last_payment_on", "last_payment_amount", "last_payment_status",
                     "previous_bill_paid", "annual_fee", "annual_fee_charged_on", "annual_fee_waiver_spend",
                     "spend_last_year", "lounge_per_quarter", "lounge_used_quarter", "international"),
    "card_txns": ("card_id", "posted_at", "merchant", "amount", "status", "international"),
    "fixed_deposits": ("number", "cif", "principal", "rate", "opened_on", "tenure_months", "maturity_date",
                       "maturity_amount", "payout", "auto_renew", "status"),
    "loans": ("number", "cif", "type", "rate_type", "rate", "sanctioned", "outstanding", "emi", "next_due", "emis_paid",
              "emis_left", "debit_account", "status"),
    "loan_emis": ("loan_number", "due_date", "amount", "status", "reason", "charge", "paid_on"),
    "loan_rate_changes": ("loan_number", "changed_on", "old_rate", "new_rate", "old_emi", "new_emi"),
}


def _working_days(start: date, days: int) -> date:
    d = start
    while days:
        d += timedelta(days=1)
        days -= d.weekday() < 5
    return d


def _channel(desc: str) -> str:
    d = desc.lower()
    for key, channel in (("upi", "UPI"), ("imps", "IMPS"), ("neft", "NEFT"), ("salary", "NEFT"), ("atm", "ATM"),
                         ("pos", "POS"), ("emi", "NACH"), ("interest", "INTEREST"), ("dividend", "NEFT"),
                         ("cash", "CASH"), ("cheque", "CHEQUE"), ("refund", "UPI"), ("bill", "ECOM"), ("recharge", "ECOM")):
        if key in d:
            return channel
    return "OTHER"


def _txn(x: dict) -> Transaction:
    at = x["posted_at"].astimezone()
    return Transaction(at.date(), x["description"], x["kind"], float(x["amount"]), x["channel"], x["counterparty"] or "",
                       x["status"], x["reference"], x["note"] or "", at.strftime("%H:%M"))


def _f(v) -> float | None:
    return None if v is None else float(v)


def _records(ws) -> list[dict]:
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows)]
    return [dict(zip(header, r, strict=False)) for r in rows if any(v is not None for v in r)]


def _date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v).strip(), "%d-%b-%Y").date()


def _mobile(v) -> str:
    """Last 10 digits, so '+91 71074 20369', '07107420369' and 7107420369 all match."""
    digits = "".join(ch for ch in str(v) if ch.isdigit())
    return digits[-10:]
