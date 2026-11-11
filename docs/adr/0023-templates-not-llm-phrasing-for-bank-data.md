# 0023. Bank-data replies stay templates: no LLM phrasing call

- **Status:** Accepted
- **Date:** 2026-11-06

## Context

PHASE-4-PLAN §4.2 proposed an LLM call (streamed, figure-checked) to phrase lists and explanations of bank data, to sound less robotic, at a cost of 300 to 500 ms to first audio on those turns. The concrete failures in the call review (a list of 1 + 5 from two accounts, "paid (failed)", replies that were too long) were caused by how the data was merged and labelled, not by the wording.

## Decision

Replies built from bank data stay fixed templates in English, Hindi and Hinglish. The failures are fixed in code: one merged newest-first list cut to exactly N with each line's account, read three at a time ("Want the other 2?"); one status table in which "paid" and "received" follow only a successful transaction; short lines for small talk. No phrasing call is added.

## Options considered

| Option | Why not |
|---|---|
| LLM phrasing of validated facts + a figure check (the plan) | +300 to 500 ms on the slowest turns, more Groq quota, and a risk of invented words, to fix problems that were not about wording |
| Templates with the fixes above (chosen) | No added latency, every number comes straight from bank data, easy to test |

## Consequences

- Latency and accuracy are unchanged for bank-data turns; wording is plainer than an LLM would produce.
- If the owner still finds replies robotic after hearing them, reopen this: phrase only lists and explanations, behind the existing figure check, and measure first-audio latency before and after.

## Links

`brain/app/services/personal.py`, PHASE-4-PLAN §4.2, ADR 0006, ADR 0008.
