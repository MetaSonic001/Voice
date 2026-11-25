# The journey catalogue

Everything the agent says that the AI doesn't write on the spot, plus the prompts and business settings. ADR 0032.

| File | What |
|---|---|
| `catalog.json` | `languages` (code, name, how it is written, spoken or not), `tree` (domain > journey > step > message ids), `messages` (id -> English `text`, `note`, `locked`), `aliases` (one sentence used under two ids), `settings` (teams, ticket turnaround, problem -> team), `prompts` (strategy lines, language rules), `pronunciation` |
| `languages/<code>.json` | One file per other language: message id -> text. A sentence a file doesn't have is said in English (ADR 0033) |
| `prompts/*.md` | Long prompts: `answer_style` (editable), `answer_rules` (locked), `understand` (locked), `judge`, `summary` (editable), `translate` (locked) |

## How it is used

- Code reads it through `app/services/content.py`. The old table names (`personal.T`, `journeys.J`, `conversation.TEMPLATES`,
  `desk.DEPARTMENTS`...) are read-only views of it, so the logic around them is unchanged.
- This folder is the base. A product manager's edits on `/journeys` are **releases** in Postgres, stored as changes on
  top of it (`services/releases.py`). Locked parts always come from here.
- English is written by people. Other languages are written by the LLM on request (Translate now, with an optional note on
  what to change) or for every changed sentence (Prepare translations), never during a call.

## As a developer

- **New sentence:** add it to `messages` (English) and to each `languages/<code>.json`, and put its id in a step of `tree`
  (if you forget, it shows under "Not sorted yet"). Use it through the module's table (`t("key", lang)`, `j(...)`) or
  `content.phrase("id", lang)`.
- **Removed sentence:** delete it from `messages` and `tree`. Releases that changed it simply ignore it.
- **Placeholders** (`{ref}`, `{dept}`) are a contract with the code: the editor refuses edits that change them.
- **Bring the product manager's live wording into git:** `python -m app.cli content-export`, review `git diff`, commit.
- `tests/test_spoken_form.py` checks every sentence is sayable; `tests/test_content.py` covers the catalogue and releases.
