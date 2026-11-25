# ADR-0032: One journey catalogue in git, edited by the product manager as releases in Postgres, with canary rollout

- **Status:** Accepted
- **Date:** 2026-11-26
- **Deciders:** project owner

## Context

- About 300 fixed sentences the agent says (bank-data answers, fraud, cards, complaints, hand-over, sign-in, small talk,
  voice phrases) were spread over 9 Python files as `{key: {en, hi, hinglish}}` tables, with 5 duplicates.
- The prompts (understanding, answer, judge, hand-over note) and the business settings (teams, turnaround days, which
  team gets which problem) were also in code.
- The owner wants the product manager to own this content: see it organised by journey, edit it on a web page, move
  things between domains, and roll changes out safely (a small share of calls first), without a developer.
- Fixed sentences are said without any LLM call (instant, no bank data sent to an LLM, ADR 0024). That must stay.

## Decision

1. **One catalogue in git:** `brain/content/catalog.json` (sentences, tree, settings, short prompt parts) and
   `brain/content/prompts/*.md` (long prompts). Code reads it through `services/content.py`; the old table names are
   read-only views, so the logic did not change and every reply is byte-for-byte the same.
2. **Tree:** domain > journey > step > message id. Message ids never change; the tree is only grouping, so the product
   manager can add, rename, reorder, move and delete domains, journeys and steps (deleting moves the contents first).
3. **English is written by people.** Hindi and Hinglish are stored too, and written by the LLM **once, when a draft is
   prepared** (`content/prompts/translate.md`), never during a call. Edits are checked in code: same placeholders, no new
   figures, sayable by a voice, no request for an OTP or PIN, no unknown links, right script.
4. **Releases in Postgres** (migration 014): one draft at a time (optimistic `rev` lock); published releases never
   change; each stores only changes on top of git. Locked parts (understanding prompt, answer rules, grammar word lists,
   pronunciation) always come from git.
5. **Canary:** a release goes live for 1–50% of new calls (chosen from the call id) or for all. A call keeps its release
   for its whole length. Every trace records `{release, arm}`; `/journeys` compares main vs canary on the same time
   window (outcomes, time to first words, judge score). Promote, change the share, stop, or roll back to any release
   (or to git's catalogue). Each brain copy polls one row every 5 s.
6. `python -m app.cli content-export` writes the live release back into `brain/content/` so it can be committed.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Git seed + releases in Postgres, canary in our code** | No new service or library; RDS is already there; full history; per-call pinning; A/B readout from our own traces | We maintain ~500 lines (content, releases, API) |
| Keep templates in code, developer edits | Nothing to build | Every wording change needs the developer |
| Only English, LLM translates during the call | One language to store | Adds an LLM call (~0.5–1 s) to every template reply, sends bank figures to the LLM, can change amounts; breaks ADR 0024 |
| Headless CMS / feature-flag SaaS / AWS AppConfig | Ready-made UI or rollout | New dependency and IAM for the bank to approve; doesn't know our placeholders, figure rule or journeys. AppConfig is worth a look in production for rollout only |
| JSON files edited on the server (no database) | Simple | No history, no concurrent editing, lost on redeploy |

## Consequences

- **Easier:** wording, prompts and team settings change without a deploy; rollback is one click; the duplicates are gone;
  `test_spoken_form` now checks every sentence (307 instead of the tables it could find).
- **Harder / costs:** a developer adding a sentence adds it to `catalog.json` (code + JSON). Prompt edits can't be checked
  by code: they start on a canary. With UAT call volumes a canary needs time to reach ~100 calls before a comparison means much.
- **Not built yet:** maker-checker approval (needs staff SSO with roles), running the smoke set from the page (costs ~50
  LLM calls per run), editing eval cases on the page, per-team opening hours (still `DESK_HOURS`).
- **Revisit when:** staff SSO arrives (add an approver role), or several brain copies serve chat over HTTP (pinning is
  per copy; voice calls stay on one copy).

## Links

`brain/content/`, `brain/app/services/content.py`, `services/releases.py`, `api/content.py`, `ui/app/journeys/`,
`tests/test_content.py`, ADR 0006, 0008, 0024.
