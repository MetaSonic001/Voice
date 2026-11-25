# Evaluation report: AU Small Finance Bank voice agent

**As of 18 Nov 2026.** One place for every measured number, with its date and what it covers. Nothing here was re-run for
this report: every figure comes from a saved run (the eval database, `brain/evals/reports/`, the call reviews) or from
the tracker. Where a number is old or the code has changed since, it says so. How to refresh each part is at the end.

## 1. Summary

| What | Result | When | How current |
|---|---|---|---|
| Offline tests (fake LLM, throwaway database) | **395 pass, 0 fail** | 17 Nov, latest code minus the 18 Nov fixes | Current; re-run after the 18 Nov changes |
| Product questions: 105-question golden set | **72.4% pass, 78.1% facts right, 85.7% right behaviour** | 3 Nov | Old: many answer, retrieval and dialogue changes since |
| Finding the right passage (same run) | hit@1 72.2%, hit@3 91.1%, hit@5 93.7%, MRR 0.81 | 3 Nov | Old |
| Understanding (routing), partial run | **84%** | 5 Nov | Old prompt; the router prompt has changed since |
| Smoke set (24 single questions, real LLM) | **19 of 24 (79.2%)** | 17 Nov 11:41 | The 5 misses have code fixes (18 Nov), not yet re-run |
| Regression pack (whole conversations, real LLM) | **22 of 22 conversations** (then 11 targeted conversations, all right after fixes) | 17 Nov | Now one pack of 33 conversations; not re-run as a whole since the merge |
| Language conversations (Hindi / Hinglish / English) | **5 of 5 conversations, every reply in the right language** | 17 Nov 23:02 | Current |
| Post-call LLM judge (23 judged calls) | average **4.43 / 5**; 1 critical; 6 marked for a person | 7–17 Nov | Judge rubric extended on 17 Nov (it now checks 8 known failure patterns) |
| Brain time per turn (smoke run) | p50 117 ms, p95 729 ms (understanding p50 84 ms on cached replies) | 17 Nov | Cached replies: LLM time not included |
| Voice: caller stops speaking → agent's first audio | 3.6–4.1 s typical (Edge voices); first audio 1.9 s after the end of speech | 12 Nov, 17 Nov (call 264e51d7) | Edge TTS (dev voice), not Sarvam |

## 2. Accuracy per language

**Product questions (golden set, 3 Nov).** The only run that covers all three languages on the same questions.

| Language | Questions | Pass | Facts right | Right behaviour | Right passage in top 3 |
|---|---|---|---|---|---|
| English | 71 | 78.9% | 86.0% | 87.3% | 90.4% |
| Hindi | 17 | 52.9% | 75.0% | 64.7% | 92.9% |
| Hinglish | 17 | 64.7% | 53.8% | 100% | 92.3% |
| **All** | **105** | **72.4%** | **78.1%** | **85.7%** | **91.1%** |

What it shows: search finds the right passage equally well in all three languages (top-3 hit 90–93%), so the gap is in
the answer step, not retrieval. Hindi misses were mostly the wrong behaviour (answering when it should have asked which
variant); Hinglish misses were facts (a figure missing from the answer). Both were worked on after 3 Nov (the variant
check, everyday Hindi wording, answer length rules), so this table is likely pessimistic, but it is the last measurement.

**Conversations (turns, latest saved runs).** Bank-data and journey turns; the language is the caller's sentence.

| Run | English | Hindi | Hinglish |
|---|---|---|---|
| Smoke, 17 Nov 11:41 | 19 / 24 turns | 2 / 2 | - |
| Language conversations, 17 Nov 23:02 | 2 / 2 | 4 / 4 | 5 / 5 |
| Reply in the right language (same run) | 2 / 2 | 4 / 4 | 5 / 5 |

## 3. Behaviour (golden set, 3 Nov)

| Expected | Questions | Right behaviour |
|---|---|---|
| Answer | 83 | 81.9% (65.1% pass with facts) |
| Ask which one (clarify) | 21 | 61.9% |
| Refuse (no source) | 11 | 100% |
| Verify first (own data) | 11 | 100% |
| Transfer to a person | 2 | 100% |
| Small talk / out of scope | 6 | 100% |

Some questions accept more than one behaviour, so the rows add up to more than 105. Safety-relevant behaviour (refuse, verify, transfer) was right every time. The figure check blocked 2 answers that had a
number not in the sources.

## 4. Smoke set misses (17 Nov) and what was done

| Item | What happened | Fix (18 Nov, code; not re-run) |
|---|---|---|
| A1 "Money got deducted, check my account balance" | Only the balance was handled | A plainly said second request (my balance; money deducted) is added in code when the router returns one |
| A3 "Block my card … and then tell me my balance" | Only the block was handled | Same rule: the balance follows the confirmation |
| R5 "How do I block my card if I lose it?" | Asked to verify, as if it were a block | A how-to question with nothing reported is a product question: answered, no verification |
| G1 "Minimum balance for a savings account?" | Asked back naming only BSBDA and Aarambh | Says how many accounts there are and names four, from the knowledge base |
| D1 "yes please" to "shall I check your own FD?" | Answered the rule again instead of starting verification | Not looked at in this round (open) |

## 5. Post-call judge (23 judged calls, 7–17 Nov)

| Dimension | Average (1–5) |
|---|---|
| Compliance | 5.0 |
| Handoff (when there was one) | 5.0 |
| Correctness | 4.65 |
| Conversation | 4.09 |
| Task completion | 3.96 |

Findings by type: unresolved request 18, poor voice response 2, too long 2, missing information 2, wrong fact 1, missing
verification 1. The lowest score is task completion: the calls that only half-answered a question (the loan and
multi-part cases fixed on 17–18 Nov). The judge is uncalibrated until about 50 calls carry a person's label.

## 6. What is not measured (and why)

- **Routing on the current prompt:** the 84% is from 5 Nov on an older prompt. Needs a fresh routing run (Groq quota).
- **The golden set since 3 Nov:** about an hour of paced requests on the free tier (`evals.golden_run`).
- **Sarvam speech:** no measured accuracy or latency on Sarvam yet beyond test calls; the numbers above are Edge (dev).
- **Real phone audio (8 kHz, Cisco):** not available until the telephony is connected.

## 7. How to refresh this report

| Part | Command (in `brain/`) | Groq requests |
|---|---|---|
| Offline tests | `python -m pytest` | none |
| Smoke set | `python -m evals.smoke` | only items whose understanding changed (replay cache) |
| Regression pack (33) | `python -m evals.smoke --pack regression --keep` | only changed turns |
| Golden set (per language) | `python -m evals.golden_run` | about 200, paced; about an hour on a cold cache |
| Judge averages | from `/calls` ("Quality across calls") or `GET /v1/reviews/summary?days=30` | none |

After a run, update the matching rows of sections 1–5 with the new date.
