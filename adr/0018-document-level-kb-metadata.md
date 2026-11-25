# ADR-0018: Document-level KB tags; conflicts settled in code; fix stale data at the source

- **Status:** Accepted (supersedes the Phase 3 choice of prompt rules only)
- **Date:** 2026-11-06
- **Deciders:** project owner

## Context

Retrieval usually finds a relevant passage, yet answers mixed product **variants**:
- a Swadesh figure was given for a generic savings question;
- one answer claimed "no minimum balance, 6.75%" across variants.

Two sources disagreed on FD rates. The 23 Apr 2026 rate card says up to **7.75%** for senior citizens. An undated overview says "up to **7.90%**". In Phase 3 we skipped structured metadata as over-engineering; three failures in real calls changed that.

## Decision

- Tag each **document** (not each figure) once at ingest: category, product, variant, document type, effective from/to, superseded-by, authority rank. A small LLM call plus title rules propose the tags; a person checks them on the KB page.
- **In code, before the answer step:**
  - **variant check:** a generic question with passages from several variants, or from only one, becomes CLARIFY;
  - drop expired documents;
  - prefer the most authoritative or newest source for that topic;
  - compare applicability (tenure, amount band, category, callable) before calling anything a conflict.

  Code picks the source; the LLM never picks between conflicting figures.
- **Fix stale data at the source:** the overview's figures were corrected to the rate card's (7.75% senior / 7.35% general) and uploaded as a new KB version. The bank's original file is untouched.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Document-level tags + code checks** | Small effort (~1 min review per document); deterministic | Needs tag review on upload |
| Prompt rules only (Phase 3) | No work | Didn't hold in real calls |
| Per-figure policy records | Exact | Every figure maintained twice |
| A conflict-resolution engine | General | Built around one example that turned out to be stale data |

## Consequences

- **Easier:** correct variant handling; honest conflicts; filtering by product (also needed at scale, [0022](0022-rag-at-large-scale.md)).
- **Harder:** the upload flow gets a tag-review step.
- **Revisit when:** AU provides an official document register with effective dates (then import it instead of extracting tags).

## Links

`PHASE-4-PLAN.md` §5.

## Implementation notes (6 Nov 2026)

- Built from the heading path ("CASA+TD KB › AU Swadesh Savings Account › ..." gives family and variant); document type and authority from title rules, correctable on the KB page (`PATCH /api/v1/documents/{id}` `tags`). The variant gate fires only when the family has at least two variants and the best passage is variant-specific or the top three mix variants; comparisons are left to the answer step. A rate question keeps the dated rate card. Expired, superseded and instruction-like passages are dropped.
- The CASA+TD copy with 7.75% / 7.35% is in `brain/seed/`; the source file is untouched. The Green FD's 7.90% could not be checked (no row in the rate card) and was left as it is.
- Not yet measured on the golden set with the real LLM.
