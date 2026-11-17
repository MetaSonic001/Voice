# ADR-0024: How the agent replies: dialogue decisions in code; bank-data replies stay templates

- **Status:** Accepted
- **Date:** 2026-11-06
- **Deciders:** project owner
- **Merged on 14 Nov 2026:** this file now also holds the former ADR 0023 (bank-data replies stay templates, section 7 below). That number is not reused.

## Context

Live calls on 6 Nov showed the agent behaving like a question-answering bot, not a first-line bank representative:

- broad questions got brochure answers (Zenith+: about 100 words in one go, read out with markdown dashes and asterisks);
- generic questions got a menu of variant names ("BSBDA, Aarambh, Digital or Institution?") instead of a question about
  the caller's need;
- "Can I withdraw an FD before maturity?" either refused part-way ("I can't confirm the rest… specialist?") or went
  straight to an OTP;
- every fraud-like turn started "I'm sorry this happened; I'll help you right away"; "Please connect me to your fraud
  team" ran the fraud workflow; "OTP kisi ko bata diya" searched for a matching payment;
- the same "One moment, please" played on almost every turn.

Retrieval was fine (right passages at 0.97 to 0.98). The router already returned structured requests; account choice
("the first one") was already resolved in code and kept the count. What was missing was a dialogue layer between them.

## Decision

Keep the architecture (one understanding call, code routes, ports and adapters, Postgres state) and add the dialogue
decisions as plain code in `services/dialogue.py`:

1. **Response strategy chosen in code** for every knowledge-base request: `direct`, `product_overview` (broad question:
   40 to 65 words, then offer one part), `overview` ("all accounts": range plus up to three examples), `full` (explicit
   request for everything), `guided` (help me choose) and `recommend`. The answer model gets the strategy as one line.
   This is **progressive disclosure**: broad → short overview, specific → that detail, "everything" → the full breakdown.
2. **Guided flows** (savings account, FD, credit card): code asks the questions that change the recommendation, one per
   turn, and skips any whose answer is already known; then the KB answer recommends one or two products with reasons.
3. **"Shall I check yours?" offer** after a general answer that could differ for the caller's own product (router flag
   `own_check`). Only a yes starts verification (keypad code, never spoken).
4. **Scoped caller facts**: the router returns only facts stated in this turn; code merges them. Call facts (senior
   citizen, occupation) last the call; task facts (tenure, spend, travel...) are cleared when the product changes. Only
   the facts relevant to the product go to the answer model. Bank data never becomes a fact.
5. **Voice-safe text and honest length**: markdown removed in code before screen and speech (markers were already
   removed from speech); a long answer is flagged against its strategy's target, and a generous safety limit stops a
   runaway answer only at a sentence boundary (never for `full`). A failed figure check keeps what was checked and asks
   what else they want, instead of pushing a transfer.
6. **Fraud**: an opening line that fits the report, said once; a request to reach the fraud team is a transfer with no
   verification; OTP shared → what went out lately + fraud team; a vague "money was taken" → the latest debits and
   "is it one of these?" (asking, never guessing).
7. **Bank-data replies stay fixed templates** (English, Hindi, Hinglish), with **no LLM phrasing call**. The Phase 4
   plan proposed an LLM call (streamed, figure-checked) to phrase lists and explanations of bank data, at 300–500 ms more
   to first audio on those turns. But the failures in the call review (a list of 1 + 5 from two accounts, "paid (failed)",
   replies too long) came from how data was merged and labelled, not from wording. They are fixed in code: one merged
   newest-first list cut to exactly N with each line's account, read three at a time ("Want the other 2?"); one status
   table in which "paid" and "received" follow only a successful transaction; short lines for small talk. Every number
   comes straight from bank data, and nothing from the bank goes to the LLM.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Dialogue rules in code + one router call (chosen)** | No extra model call or latency; testable offline; the LLM still only understands and phrases | Flows and strategies are hand-written; new products need a few lines |
| A separate "planner" LLM call before the composer | Flexible | +0.5 to 1 s per turn and one more call against the 8k tokens/min free tier |
| LangGraph / LangChain agent graph | Checkpointing, graph tooling | We already keep state in Postgres; a large new dependency for the bank's review; does not fix prompts, data or playbooks |
| An LLM-written rolling summary of the call | Handles very long calls | One more model call per turn; calls are 5-15 turns; structured facts and state carry what must persist |
| A hard global word cap | Simple | Cuts conditions and full breakdowns the caller asked for |
| **Bank data: templates with the fixes in point 7 (chosen)** | No added latency; every number from bank data; easy to test | Wording is plainer than an LLM would write |
| Bank data: LLM phrasing of validated facts + a figure check | Less robotic | +300–500 ms on the slowest turns, more quota, a risk of invented words, to fix problems that were not about wording |

## Consequences

- **Easier:** reviewing behaviour (the strategy is recorded on every turn as `strategy`), testing it offline, adding a
  flow (a list of fact keys and questions).
- **Harder / costs:** the router output has three more fields per request (`goal`, `product`, `own_check`) and more fact
  keys; its output still needs 800 tokens (600 truncated the JSON on the real model).
- **Bank-data turns:** latency and accuracy unchanged; wording plainer than an LLM's.
- **Revisit when:** flows need branching beyond "ask the next unknown fact", or calls routinely exceed about 20 turns
  (then add a code-built summary of older turns). For bank data: if the owner still finds replies robotic after
  hearing them, phrase only lists and explanations, behind the existing figure check, and measure first-audio latency
  before and after.

## Implementation notes (6 Nov 2026, second pass)

- **Scope comes from the router, checked by code.** Each request now carries `scope` (specific / overview / full / compare /
  choose, an enum) and `named` (the product names said). It replaces `goal`. Code accepts `compare` only with two or more
  named products and `full` only when the caller said all / everything / poori / सभी; otherwise the code patterns decide.
  The answer model still gets the caller's question and the evidence: the scope only sets the answer style.
- **Which passages are sent** (`kbmeta.trim_context`): a passage must score at least 0.20 of the best and at least 0.05;
  a passage whose heading is about a different named product is dropped unless its text covers the named one (comparison
  tables); near-copies are dropped; the best passage always stays. Tuned by replaying three saved golden-set runs
  (`evals/context_replay.py`, no LLM calls): 0 items lost the evidence their answer needs, and the passage text sent fell by
  22-24%. A 0.30 share lost the 2-year FD rate row; a 0.10 floor lost the online-shopping cards (all their scores were low).
- **Comparisons**: every named product must have a supporting passage. If one has none, the model is told to leave it out
  and code says "I don't have the details for Altura to hand, so I can't compare that side properly."
- **Rejected router output is repaired in code** when Groq returns a complete rejected generation (the model writes
  `kind: "compare"` for English "difference between A and B"); unknown kinds are mapped by the request's own fields,
  never blindly to general; the lenient retry is only for a cut-off generation. Counted in `/v1/stats`.
- **Primary product vs mentions**: a passage about another product that mentions the named one is kept but labelled
  "[about Kids; use only what it says about Digital]" for the answer model.
- Not done: separate retrieval per named product (first see whether one search misses a side), router schema trimming
  (needs a routing-set run on the real model).

## Links

`brain/app/services/dialogue.py`, `conversation.py` (`_dispatch`, `_guided`, `_flow`, `_recommend`, `_answer`),
`understanding.py`, `journeys.py` (fraud), `numbers.clean_markup`, `personal.py` (bank-data templates),
`tests/test_dialogue.py`. Related: ADR 0006, ADR 0008, the progress tracker's 6 Nov "dialogue layer" entry.
