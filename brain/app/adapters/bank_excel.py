"""Mock core banking: the hackathon dummy-data workbook, loaded once, read-only, kept in memory.

Sheets used: Customer_Master (one row per customer), Last_5_Transactions (by account number) and
Acct Debit card Mapping (account type -> debit card variant). The file itself is never modified.
"""

import logging
from datetime import date, datetime
from pathlib import Path

from app.domain import Account, Customer, Transaction
from app.logs import log

logger = logging.getLogger(__name__)


class ExcelBank:
    def __init__(self, path: Path):
        from openpyxl import load_workbook

        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            master = _records(wb["Customer_Master"])
            txns = _records(wb["Last_5_Transactions"])
            variants = {r["Account Type"]: r["Debit Card"] for r in _records(wb["Acct Debit card Mapping"])}
        finally:
            wb.close()

        by_account: dict[str, list[Transaction]] = {}
        for r in txns:
            by_account.setdefault(str(r["Account No"]), []).append(
                Transaction(_date(r["Txn Date"]), str(r["Txn Description"]), str(r["Txn Type"]), float(r["Amount"])))
        for items in by_account.values():
            items.sort(key=lambda t: t.date, reverse=True)

        self._by_cif: dict[str, Customer] = {}
        self._by_mobile: dict[str, str] = {}
        for r in master:
            accounts = [Account(str(r[f"Savings Acct {n}"]), str(r[f"Savings Acct {n} Type"]), float(r[f"Balance {n}"] or 0),
                                by_account.get(str(r[f"Savings Acct {n}"]), []))
                        for n in (1, 2, 3) if r.get(f"Savings Acct {n}")]
            c = Customer(cif=str(r["CIF ID"]), name=str(r["Customer Name"]), mobile=_mobile(r["Mobile No"]), accounts=accounts)
            if r.get("FD Balance"):
                c.fd = {"balance": float(r["FD Balance"]), "rate": str(r["FD Interest Rate"]), "maturity": _date(r["FD Maturity Date"])}
            if r.get("Loan Balance"):
                c.loan = {"outstanding": float(r["Loan Balance"]), "emi": float(r["EMI Amount"] or 0),
                          "paid": int(r["EMIs Paid"] or 0), "left": int(r["EMIs Left"] or 0), "next_due": _date(r["Emi Due Date"])}
            if r.get("Debit Card Last4"):
                c.debit_card = {"last4": str(r["Debit Card Last4"]).zfill(4), "variant": variants.get(accounts[0].type),
                                "ecom": r["ECOM"], "pos": r["POS"], "atm": r["ATM"]}
            if r.get("Credit Card Last4"):
                c.credit_card = {"last4": str(r["Credit Card Last4"]).zfill(4), "type": r["Credit Card Type"],
                                 "outstanding": float(r["CC Outstanding"] or 0), "limit": float(r["CC Limit"] or 0),
                                 "due": _date(r["CC Due Date"]), "bill_date": _date(r["CC Bill Date"])}
            self._by_cif[c.cif] = c
            self._by_mobile[c.mobile] = c.cif
        log(logger, "bank data loaded", source=path.name, customers=len(self._by_cif), transactions=len(txns))

    def find_by_mobile(self, mobile: str) -> Customer | None:
        cif = self._by_mobile.get(_mobile(mobile))
        return self._by_cif.get(cif) if cif else None

    def customer(self, cif: str) -> Customer | None:
        return self._by_cif.get(cif)

    def demo_callers(self, limit: int = 12) -> list[dict]:
        """A few customers with different product mixes, for the test UI's 'calling from' picker."""
        picked, seen = [], set()
        for c in self._by_cif.values():
            mix = (len(c.accounts), bool(c.fd), bool(c.loan), bool(c.debit_card), bool(c.credit_card))
            if mix not in seen:
                seen.add(mix)
                picked.append({"name": c.name, "mobile": c.mobile, "accounts": len(c.accounts), "fd": bool(c.fd),
                               "loan": bool(c.loan), "debit_card": bool(c.debit_card), "credit_card": bool(c.credit_card)})
            if len(picked) >= limit:
                break
        return picked


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
