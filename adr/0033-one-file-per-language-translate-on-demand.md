# ADR-0033: One file per language, and translation on demand with a note

- **Status:** Accepted (adds to [0032](0032-journey-catalogue-and-releases.md); 0032 still holds)
- **Date:** 2026-11-26
- **Deciders:** project owner

## Context

- ADR 0032 kept every language of a sentence inside `catalog.json`. The product manager writes English only and wants
  the AI to write the other languages on request, pick which ones, and send a note when a translation is wrong.
- More languages are expected (the bank serves several states). With all of them in one file, `catalog.json` grows by
  ~100 KB per language and every review mixes languages.

## Decision

1. **Git:** `catalog.json` holds English (the source), the tree, settings and a `languages` list (code, name, writing
   style, script, spoken or not). Each other language is its own file, `languages/<code>.json` (message id -> text).
   The loader joins them into the same structure as before, so the code reading sentences didn't change.
2. **Releases:** a release stores changes per sentence and language as before; it can also add a language (a name and
   a writing style) or change a language's writing style. English can't be changed and git's languages can't be removed.
3. **Translate now:** `POST /api/v1/content/draft/translate` with `ids`, `languages` and an optional `hint` (the
   product manager's note, max 300 characters). The LLM writes them once, into the draft, marked AI-written; code checks
   blanks, figures, script and sayability. "Prepare translations" (every changed sentence) takes a language list too.
4. **Fill a language:** `fill_missing: true` with `languages` writes every sentence that has no text yet in them, 50
   per request; the page repeats until `remaining` is 0 or a round makes no progress. Locked word lists are left to code.
5. A language added on the page is **kept ready, not spoken**: callers can't use it until a developer adds speech,
   understanding and the answer-language rule for it. The page says so.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **One file per language in git; all in one record per release in Postgres** | Small, reviewable diffs per language; adding a language is one new file; nothing changes for the code | Two layouts to know (files in git, one record in the database) |
| Everything in `catalog.json` | One file | Grows with every language; mixed review |
| One database row per sentence and language | Fine-grained | Many rows to version per release; no gain at ~300 sentences |

## Consequences

- **Easier:** adding a language for templates; native speakers can review one file; the PM fixes a bad translation by
  saying what to change instead of writing it.
- **Harder:** a developer adding a sentence also adds it to each language file (missing ones fall back to English).
- **Revisit when:** a language becomes spoken: it then also needs STT/TTS voices, the understanding enum and an answer
  language rule (code, not the page).

## Links

`brain/content/`, `services/content.py` (`read_seed`, `languages_of`, `write_seed`), `services/releases.py` (`translate`),
`ui/components/journeys/`.
