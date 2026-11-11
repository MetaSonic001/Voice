-- Phase 3. Two separate things:
--  1. schema "corebank": a MOCK of the bank's own systems (accounts, cards, deposits, loans). The agent reaches it
--     only through the Bank port (adapters/mock_corebank.py), exactly as it would reach real bank APIs later.
--  2. app tables for what the agent itself manages: pending confirmations, tickets, handoffs to people.
-- Only additions: nothing existing changes.

CREATE SCHEMA corebank;

CREATE TABLE corebank.branches (
    id   text PRIMARY KEY,
    name text NOT NULL,
    city text NOT NULL,
    type text NOT NULL                          -- urban | semi_urban | rural (decides some balance rules)
);

CREATE TABLE corebank.customers (
    cif        text PRIMARY KEY,
    name       text NOT NULL,
    mobile     text NOT NULL UNIQUE,            -- registered mobile (last 10 digits)
    segment    text NOT NULL DEFAULT 'regular', -- regular | senior | priority
    branch_id  text NOT NULL REFERENCES corebank.branches (id),
    language   text,                            -- preferred language, if known
    rm_name    text,                            -- relationship manager (priority customers)
    scenario   text                             -- demo customers only: what their data is set up to show
);

CREATE TABLE corebank.accounts (
    number            text PRIMARY KEY,
    cif               text NOT NULL REFERENCES corebank.customers (cif),
    variant           text NOT NULL,            -- Digital | Value | Platinum | Royale | BSBDA | Vikaas | ...
    branch_id         text NOT NULL REFERENCES corebank.branches (id),
    status            text NOT NULL DEFAULT 'active',   -- active | inactive | dormant
    is_salary         boolean NOT NULL DEFAULT false,
    opened_on         date NOT NULL,
    ledger_balance    numeric(14,2) NOT NULL,
    available_balance numeric(14,2) NOT NULL,
    hold_amount       numeric(14,2) NOT NULL DEFAULT 0,
    hold_reason       text,
    amb_required      numeric(14,2) NOT NULL DEFAULT 0,
    amb_this_month    numeric(14,2),
    amb_last_month    numeric(14,2)
);
CREATE INDEX ON corebank.accounts (cif);

CREATE TABLE corebank.account_txns (
    id             bigserial PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    posted_at      timestamptz NOT NULL,
    description    text NOT NULL,
    kind           text NOT NULL,               -- Credit | Debit
    amount         numeric(14,2) NOT NULL,
    channel        text NOT NULL,               -- UPI | IMPS | NEFT | POS | ECOM | ATM | NACH | CASH | INTEREST | CHARGE
    counterparty   text,                        -- person or merchant
    reference      text NOT NULL,
    status         text NOT NULL DEFAULT 'success',  -- success | failed | pending | reversed
    note           text                         -- e.g. "cash not dispensed: auto-reversal due by 5 working days"
);
CREATE INDEX ON corebank.account_txns (account_number, posted_at DESC);

CREATE TABLE corebank.account_charges (
    id             bigserial PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    charged_on     date NOT NULL,
    type           text NOT NULL,               -- amb_shortfall | debit_card_annual_fee | sms_alerts | emi_bounce
    amount         numeric(14,2) NOT NULL,
    reversed       boolean NOT NULL DEFAULT false
);

CREATE TABLE corebank.debit_cards (
    id             text PRIMARY KEY,
    account_number text NOT NULL REFERENCES corebank.accounts (number),
    last4          text NOT NULL,
    variant        text NOT NULL,
    status         text NOT NULL DEFAULT 'active',  -- active | temp_blocked | blocked (permanent)
    atm            boolean NOT NULL,
    pos            boolean NOT NULL,
    ecom           boolean NOT NULL,
    contactless    boolean NOT NULL,
    international  boolean NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE corebank.credit_cards (
    id                   text PRIMARY KEY,
    cif                  text NOT NULL REFERENCES corebank.customers (cif),
    last4                text NOT NULL,
    variant              text NOT NULL,
    status               text NOT NULL DEFAULT 'active',  -- active | temp_blocked | blocked
    credit_limit         numeric(14,2) NOT NULL,
    outstanding          numeric(14,2) NOT NULL,     -- everything spent, billed or not
    total_due            numeric(14,2) NOT NULL,     -- last statement
    minimum_due          numeric(14,2) NOT NULL,
    statement_date       date,
    due_date             date,
    last_payment_on      date,
    last_payment_amount  numeric(14,2),
    last_payment_status  text,                       -- posted | processing
    previous_bill_paid   boolean,
    annual_fee           numeric(14,2),
    annual_fee_charged_on date,
    annual_fee_waiver_spend numeric(14,2),           -- spend that waives the fee (from the card's KB page)
    spend_last_year      numeric(14,2),
    lounge_per_quarter   int NOT NULL DEFAULT 0,
    lounge_used_quarter  int NOT NULL DEFAULT 0,
    international        boolean NOT NULL DEFAULT false,
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON corebank.credit_cards (cif);

CREATE TABLE corebank.card_txns (
    id            bigserial PRIMARY KEY,
    card_id       text NOT NULL REFERENCES corebank.credit_cards (id),
    posted_at     timestamptz NOT NULL,
    merchant      text NOT NULL,
    amount        numeric(14,2) NOT NULL,
    status        text NOT NULL DEFAULT 'success',
    international boolean NOT NULL DEFAULT false
);

CREATE TABLE corebank.fixed_deposits (
    number          text PRIMARY KEY,
    cif             text NOT NULL REFERENCES corebank.customers (cif),
    principal       numeric(14,2) NOT NULL,
    rate            numeric(5,2) NOT NULL,
    opened_on       date NOT NULL,
    tenure_months   int NOT NULL,
    maturity_date   date NOT NULL,
    maturity_amount numeric(14,2) NOT NULL,
    payout          text NOT NULL,              -- cumulative | quarterly
    auto_renew      boolean NOT NULL,
    status          text NOT NULL               -- active | matured | closed
);
CREATE INDEX ON corebank.fixed_deposits (cif);

CREATE TABLE corebank.loans (
    number          text PRIMARY KEY,
    cif             text NOT NULL REFERENCES corebank.customers (cif),
    type            text NOT NULL,              -- home | personal | vehicle | gold | business
    rate_type       text NOT NULL,              -- fixed | floating
    rate            numeric(5,2) NOT NULL,
    sanctioned      numeric(14,2) NOT NULL,
    outstanding     numeric(14,2) NOT NULL,
    emi             numeric(14,2) NOT NULL,
    next_due        date,
    emis_paid       int NOT NULL,
    emis_left       int NOT NULL,
    debit_account   text,
    status          text NOT NULL DEFAULT 'active'
);
CREATE INDEX ON corebank.loans (cif);

CREATE TABLE corebank.loan_emis (
    id          bigserial PRIMARY KEY,
    loan_number text NOT NULL REFERENCES corebank.loans (number),
    due_date    date NOT NULL,
    amount      numeric(14,2) NOT NULL,
    status      text NOT NULL,                  -- paid | bounced
    reason      text,                           -- bounced: why
    charge      numeric(14,2) NOT NULL DEFAULT 0
);

CREATE TABLE corebank.loan_rate_changes (
    id          bigserial PRIMARY KEY,
    loan_number text NOT NULL REFERENCES corebank.loans (number),
    changed_on  date NOT NULL,
    old_rate    numeric(5,2) NOT NULL,
    new_rate    numeric(5,2) NOT NULL,
    old_emi     numeric(14,2) NOT NULL,
    new_emi     numeric(14,2) NOT NULL
);

-- Every change made through the agent, as the bank's own system would log it.
CREATE TABLE corebank.card_actions (
    id         bigserial PRIMARY KEY,
    reference  text NOT NULL UNIQUE,
    cif        text NOT NULL,
    card_kind  text NOT NULL,                   -- debit | credit
    card_id    text NOT NULL,
    action     text NOT NULL,
    before     jsonb NOT NULL,
    after      jsonb NOT NULL,
    channel    text NOT NULL DEFAULT 'voice-agent',
    created_at timestamptz NOT NULL DEFAULT now()
);

-- ── the agent's own records ──

-- A question the agent asked and is waiting on: "Shall I block card ending 6712?", "Shall I connect you?".
ALTER TABLE sessions ADD COLUMN pending jsonb;

-- Complaints, disputes, service requests, callbacks and new-customer leads raised on a call.
-- (In production these go to the bank's CRM through the same port.)
CREATE TABLE tickets (
    id          text PRIMARY KEY,               -- AU-CMP-20261231-0001
    session_id  uuid REFERENCES sessions (id) ON DELETE SET NULL,
    cif         text,
    kind        text NOT NULL,                  -- complaint | dispute | service_request | callback | lead
    category    text NOT NULL,
    department  text NOT NULL,
    summary     text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    status      text NOT NULL DEFAULT 'open',
    due_by      date,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON tickets (session_id);

-- A call (or chat) handed to a person: waiting, then accepted by an agent at the desk.
CREATE TABLE handoffs (
    id           uuid PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    department   text NOT NULL,
    priority     int NOT NULL,                  -- 1 = critical (fraud) .. 3 = normal
    reason       text NOT NULL,
    note         jsonb NOT NULL,                -- the brief the person sees: who, verified?, what was asked/done, pending
    status       text NOT NULL DEFAULT 'waiting',  -- waiting | active | completed | abandoned | callback
    channel      text NOT NULL,                 -- call | chat
    created_at   timestamptz NOT NULL DEFAULT now(),
    accepted_at  timestamptz,
    ended_at     timestamptz,
    agent_name   text,
    disposition  text,
    agent_notes  text
);
CREATE INDEX ON handoffs (status, priority, created_at);

-- Messages between the person at the desk and a caller who was handed over on the chat channel.
CREATE TABLE handoff_messages (
    id         bigserial PRIMARY KEY,
    handoff_id uuid NOT NULL REFERENCES handoffs (id) ON DELETE CASCADE,
    sender     text NOT NULL,                   -- customer | agent
    text       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
