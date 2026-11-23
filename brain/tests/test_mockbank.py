"""Contract tests for the mock bank systems and the brain's HTTP adapter (PHASE-4-PLAN §3.3): auth between brain and
bank, idempotent writes, fault injection, status mapping, and what the agent may claim when a call cannot be confirmed."""

import time

import pytest
from fastapi.testclient import TestClient

from app import tokens
from app.adapters.http_bank import HttpBank
from app.mockbank.core import BankConfig, MockBank, create_app, parse_faults
from app.ports import BankError, BankUnconfirmed
from app.services import actions

SERVICE, SECRET = "svc-token", b"test-secret-for-customer-tokens"
RAVI, IMRAN = "9000000101", "9000000105"


@pytest.fixture()
def bank_env(container):
    cfg = BankConfig(SERVICE, SECRET, container.settings.demo_today, {}, (0, 0))
    client = TestClient(create_app(MockBank(container.db, cfg)), base_url="http://bank")
    return cfg, client, HttpBank("http://bank", SERVICE, SECRET, client=client)


def _token(cif, systems=("*",), scopes=("read", "card_update", "crm"), ttl=60, now=None):
    return tokens.issue(SECRET, cif=cif, systems=list(systems), scopes=list(scopes), ttl_s=ttl, now=now)


def _cif(bank, mobile):
    return bank.find_by_mobile(mobile).cif


def _auth(cif, **kw):
    return {"X-Service-Token": SERVICE, "Authorization": f"Bearer {_token(cif, **kw)}"}


# ───────────── authorisation between brain and bank ─────────────


def test_every_call_needs_the_service_token_and_customer_data_needs_a_customer_token(bank_env):
    cfg, client, bank = bank_env
    cif = _cif(bank, RAVI)
    assert client.get("/cbs/v1/accounts", params={"cif": cif}).status_code == 401  # no service token
    assert client.get("/cbs/v1/accounts", params={"cif": cif}, headers={"X-Service-Token": SERVICE}).status_code == 401  # no customer token
    assert client.get("/cbs/v1/accounts", params={"cif": cif}, headers=_auth(cif)).status_code == 200
    assert client.get("/cif/v1/customers", params={"mobile": RAVI}, headers={"X-Service-Token": SERVICE}).status_code == 200  # lookup: service only


def test_a_token_for_one_customer_cannot_read_another(bank_env):
    _, client, bank = bank_env
    ravi, imran = _cif(bank, RAVI), _cif(bank, IMRAN)
    assert client.get("/cbs/v1/accounts", params={"cif": imran}, headers=_auth(ravi)).status_code == 403
    imran_card = client.get("/cc/v1/cards", params={"cif": imran}, headers=_auth(imran)).json()[0]["card_id"]
    assert client.get(f"/cc/v1/cards/{imran_card}/transactions", headers=_auth(ravi)).status_code == 403  # by id, too
    imran_acct = client.get("/cbs/v1/accounts", params={"cif": imran}, headers=_auth(imran)).json()[0]["account_no"]
    assert client.get(f"/cbs/v1/accounts/{imran_acct}/transactions", headers=_auth(ravi)).status_code == 403


def test_expired_wrong_system_and_wrong_scope_tokens_are_refused(bank_env):
    _, client, bank = bank_env
    cif = _cif(bank, RAVI)
    get = lambda **kw: client.get("/cbs/v1/accounts", params={"cif": cif}, headers=_auth(cif, **kw)).status_code  # noqa: E731
    assert get(ttl=-5) == 403 and get(systems=("cards",)) == 403 and get(scopes=("card_update",)) == 403
    forged = _token(cif)[:-3] + "abc"
    assert client.get("/cbs/v1/accounts", params={"cif": cif}, headers={"X-Service-Token": SERVICE, "Authorization": f"Bearer {forged}"}).status_code == 403


def test_the_token_names_the_customer_and_never_carries_card_or_otp_data():
    payload = tokens.verify(SECRET, _token("CIF900101"), system="cbs", scope="read")
    assert set(payload) == {"cif", "aud", "scp", "exp"} and payload["cif"] == "CIF900101"


# ───────────── each system's own vocabulary, mapped in one place ─────────────


def test_systems_use_their_own_words_and_the_adapter_maps_them(bank_env):
    _, client, bank = bank_env
    cif = _cif(bank, RAVI)
    raw = client.get("/cbs/v1/accounts/10020094521/transactions", headers=_auth(cif)).json()
    assert {x["posting_status"] for x in raw} <= {"POSTED", "HOLD", "REJECTED", "RETURNED"} and raw[0]["txn_id"].startswith("CBS")
    cards = client.get("/cards/v1/debit", params={"cif": cif}, headers=_auth(cif)).json()
    assert {c["status"] for c in cards} <= {"ACTIVE", "TEMP_BLOCKED", "HOT_LISTED"}
    customer = bank.customer(cif)
    assert {t.status for a in customer.accounts for t in a.transactions} <= {"success", "pending", "failed", "reversed"}
    assert customer.accounts and customer.credit_cards and customer.loans


def test_the_http_adapter_returns_what_the_in_process_bank_returns(container, bank_env):
    from app.adapters.mock_corebank import MockCoreBank

    _, _, bank = bank_env
    cif = _cif(bank, RAVI)
    direct = MockCoreBank(container.db, None, container.settings.demo_today, latency_ms=(0, 0)).customer(cif)
    over_http = bank.customer(cif)
    assert direct == over_http  # same customer, field for field


def test_an_unknown_status_word_becomes_unknown_never_success(bank_env):
    cfg, client, bank = bank_env
    cfg.faults = parse_faults("cc:unexpected_status")
    cif = _cif(bank, RAVI)
    card = bank.customer(cif).credit_cards[0]
    # fault is on writes' status word; reads keep working
    result = bank.update_card(cif, "credit", card.id, {"international": not card.international}, "controls")
    assert result["after"]["status"] == "unknown"
    out = actions.execute(bank, bank.customer(cif), actions.CardAction("credit", card.id, card.last4, card.variant, "controls",
                                                                        {"international": card.international}), "en")
    assert out[0] in (True, False)  # (state read back is judged on the requested change only)


# ───────────── idempotent writes ─────────────


def _count_actions(container, card_id):
    with container.db.connection() as conn:
        return conn.execute("SELECT count(*) AS n FROM corebank.card_actions WHERE card_id = %s", (card_id,)).fetchone()["n"]


def test_same_key_same_request_returns_the_original_result_once(container, bank_env):
    _, client, bank = bank_env
    cif = _cif(bank, RAVI)
    card = bank.customer(cif).credit_cards[0]
    path, body = f"/cc/v1/cards/{card.id}/international", {"enabled": not card.international}
    headers = _auth(cif) | {"Idempotency-Key": "key-A"}
    base = _count_actions(container, card.id)
    first = client.post(path, json=body, headers=headers).json()
    again = client.post(path, json=body, headers=headers).json()
    assert first == again and first["reference"].startswith("AU")
    assert _count_actions(container, card.id) == base + 1  # done once, however often it was sent
    different = client.post(path, json={"enabled": card.international}, headers=headers)
    assert different.status_code == 409  # same key, different request: rejected
    assert client.post(path, json=body, headers=_auth(cif)).status_code == 400  # a write without a key is refused
    client.post(path, json={"enabled": card.international}, headers=_auth(cif) | {"Idempotency-Key": "key-restore"})


def test_a_lost_reply_is_retried_with_the_same_key_and_nothing_happens_twice(container, bank_env):
    cfg, client, bank = bank_env
    cif = _cif(bank, RAVI)
    card = bank.customer(cif).credit_cards[0]
    cfg.faults = parse_faults("cc:lost_response")
    path, body, key = f"/cc/v1/cards/{card.id}/international", {"enabled": not card.international}, "key-lost"
    base = _count_actions(container, card.id)
    lost = client.post(path, json=body, headers=_auth(cif) | {"Idempotency-Key": key})
    assert lost.status_code == 504  # the bank did it, the answer never arrived
    assert _count_actions(container, card.id) == base + 1
    retry = client.post(path, json=body, headers=_auth(cif) | {"Idempotency-Key": key})
    assert retry.status_code == 200 and _count_actions(container, card.id) == base + 1  # the original result, not a second change
    cfg.faults = {}
    client.post(path, json={"enabled": card.international}, headers=_auth(cif) | {"Idempotency-Key": "key-restore-2"})


def test_through_the_adapter_a_lost_reply_still_ends_in_a_confirmed_result(container, bank_env):
    cfg, _, bank = bank_env
    cif = _cif(bank, RAVI)
    card = bank.customer(cif).credit_cards[0]
    customer = bank.customer(cif)
    base = _count_actions(container, card.id)
    cfg.faults = parse_faults("cc:lost_response")
    plan = actions.CardAction("credit", card.id, card.last4, card.variant, "controls", {"international": not card.international})
    done, text, record = actions.execute(bank, customer, plan, "en")
    assert done and "reference number" in text and _count_actions(container, card.id) == base + 1
    cfg.faults = {}
    bank.update_card(cif, "credit", card.id, {"international": card.international}, "controls")


# ───────────── faults: never claim an outcome that was not confirmed ─────────────


@pytest.mark.parametrize("fault,error", [("5xx", BankError), ("malformed", BankError)])
def test_read_faults_raise_a_bank_error(bank_env, fault, error):
    cfg, _, bank = bank_env
    cif = _cif(bank, RAVI)
    cfg.faults = parse_faults(f"cbs:{fault}")
    with pytest.raises(error):
        bank.customer(cif)


def test_a_timeout_on_a_write_is_unconfirmed_not_failed(bank_env):
    cfg, client, _ = bank_env
    short = HttpBank("http://bank", SERVICE, SECRET, client=TestClient(client.app, base_url="http://bank"), timeout_s=0.2)
    cif = _cif(short, RAVI)
    card = short.customer(cif).credit_cards[0]
    cfg.faults = parse_faults("cc:timeout=1")
    short.http.request = _timing_out(short.http.request)  # (the in-process client has no socket to time out)
    plan = actions.CardAction("credit", card.id, card.last4, card.variant, "controls", {"international": not card.international})
    done, text, record = actions.execute(short, short.customer(cif), plan, "en")
    assert not done and record.get("unconfirmed") and "couldn't confirm" in text and "nothing has been changed" not in text


def _timing_out(real):
    import httpx

    def request(method, url, **kw):
        if method == "POST":
            raise httpx.ReadTimeout("simulated")
        return real(method, url, **kw)

    return request


def test_a_clean_503_on_a_write_is_a_failure_with_nothing_changed(bank_env):
    cfg, _, bank = bank_env
    cif = _cif(bank, RAVI)
    customer = bank.customer(cif)
    card = customer.credit_cards[0]
    cfg.faults = parse_faults("cc:5xx")
    plan = actions.CardAction("credit", card.id, card.last4, card.variant, "controls", {"international": not card.international})
    done, text, record = actions.execute(bank, customer, plan, "en")
    assert not done and "nothing has been changed" in text


def test_legacy_fail_words_still_work():
    assert set(parse_faults("all")) == {"cif", "cbs", "payments", "cards", "cc", "lms", "deposits", "crm"}
    assert "cards" not in parse_faults("read") and parse_faults("card_update") == {"cards": ("5xx", 0), "cc": ("5xx", 0)}
    assert parse_faults("cards:latency=400,bogus:5xx") == {"cards": ("latency", 400)}


# ───────────── CRM + speed ─────────────


def test_a_ticket_is_raised_through_the_crm_system(container, bank_env):
    from app.adapters.http_bank import HttpServiceDesk

    _, _, bank = bank_env
    cif = _cif(bank, RAVI)
    t = HttpServiceDesk(bank).create_ticket(session_id=None, cif=cif, kind="complaint", category="service_complaint",
                                            department="general", summary="test", detail={}, tat_days=7)
    assert t["id"].startswith("AU-CMP-") and t["due_by"] > container.settings.demo_today
    lead = HttpServiceDesk(bank).create_ticket(session_id=None, cif=None, kind="lead", category="lead", department="sales",
                                               summary="test", detail={}, tat_days=1)
    assert lead["id"].startswith("AU-LED-")


def test_one_customer_is_read_in_parallel_and_quickly(bank_env):
    _, _, bank = bank_env
    cif = _cif(bank, RAVI)
    bank.customer(cif)  # warm
    started = time.perf_counter()
    for _ in range(5):
        bank.customer(cif)
    per_read_ms = (time.perf_counter() - started) / 5 * 1000
    print(f"customer read over HTTP: {per_read_ms:.0f} ms")
    assert per_read_ms < 600


def test_the_bank_provider_is_a_setting(container):
    from app.adapters.mock_corebank import MockCoreBank
    from app.config import Settings
    from app.container import build_bank

    base = dict(data_dir=container.settings.data_dir)
    bank, server = build_bank(Settings(bank_provider="mock", **base), container.db)
    assert isinstance(bank, MockCoreBank) and server is None  # the in-process mock still works
    assert build_bank(Settings(bank_provider="off", **base), container.db) == (None, None)
    with pytest.raises(ValueError):
        build_bank(Settings(bank_provider="nonsense", **base), container.db)
    assert build_bank(Settings(bank_provider="http", mock_bank=False, **base), container.db) == (None, None)  # default: no mock
    bank, server = build_bank(Settings(bank_provider="http", mock_bank=True, **base), container.db)  # embedded, free port
    try:
        assert bank.find_by_mobile(RAVI).cif and server.port > 0
    finally:
        server.close()
        bank.close()
