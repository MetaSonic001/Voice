# Code Standards

## General

- Plain, simple code. Only build what is needed; no over-engineering. Say what was skipped and why.
- Few, well-known libraries the bank's security team would approve. Prefer the standard library; justify every new dependency.
- Fix root causes, no layered workarounds. Keep modules small and single-purpose; no coupling across boundaries.
- Services depend on ports (`app/ports.py`), never on a concrete adapter. Wiring happens only in `container.py`.
- Nothing may get worse: measure accuracy and latency before and after, and report numbers honestly, including failures.

## Python (brain)

- Python 3.12, type hints on public functions, dataclasses for plain data (`domain.py`), no I/O in `domain.py`.
- Blocking work (Postgres, bank calls) runs through `asyncio.to_thread` from async code.
- Validate input at boundaries (FastAPI models, LLM JSON through the strict schema and then code checks).
- Logging goes through `app.logs.log`; every line is redacted. Never log secrets, codes, full numbers or the API key.
- Caller text is `redact()`ed before the LLM, traces, history and logs. Financial facts stay, identifiers are masked.

## Tests

- Offline tests (`uv run pytest -m "not live"`) use a fake LLM and a throwaway Postgres; they make zero Groq calls.
- Every bug from a real call becomes a regression test (code decides: unit test; LLM decides: smoke item).
- Evals are tiered: unit, replay cache, 40-item smoke, full benchmark (rarely). See `PHASE-4-PLAN.md` section 10.

## UI (Next.js)

- Server components by default; `use client` only for interaction. Components from shadcn (`ui/components/ui/`), added with the CLI.
- Tailwind tokens only (see `ui-context.md`); no hard-coded colours. Every page must fit at 390, 768, 1024 and 1440 px with no horizontal page scroll.
- `bun run build` must pass after UI changes.

## API

- Parse and validate input first; enforce auth and ownership before any mutation.
- Consistent response shapes. Write endpoints that destroy data need an explicit confirmation.

## File organisation

- `brain/app/services/` business logic; `adapters/` implementations of ports; `api/` thin routers; `voice/` call handling;
  `mockbank/` the mock bank systems; `migrations/` additive SQL only; `brain/tests/`, `brain/evals/`.
- `context/` project memory; `PHASE-*-PLAN.md` plans; `CLAUDE.md` rules from the owner.
