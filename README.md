# AU Small Finance Bank: multilingual voice agent

An inbound AI voice agent for AU Small Finance Bank: answers from the bank's documents, verifies callers by keypad code, reads
their own bank data, does a few controlled card actions after an explicit yes, raises tickets, hands over to people with context.
English, Hindi and Hinglish. Security and compliance come before features.

## Where to look

| I want to... | Read |
|---|---|
| Know where the project is, what is live, what is next | [context/progress-tracker.md](context/progress-tracker.md) |
| Understand how the whole system works | [PROJECT-GUIDE.md](PROJECT-GUIDE.md) (§24 = Phase 4) |
| Know **why** something was decided | [docs/adr/](docs/adr/README.md) (22 decisions) |
| See the Phase 4 design and the PM questions | [PHASE-4-PLAN.md](PHASE-4-PLAN.md) (built; §13 scale and §19 questions are still useful) |
| Run, configure or test the backend | [brain/README.md](brain/README.md) |
| Run or change the UI | [ui/README.md](ui/README.md) |
| Production research (telephony, vendors, compliance) | [ROADMAP.md](ROADMAP.md), [reference/voice-agent-playbook.md](reference/voice-agent-playbook.md) |
| The rules for working on this repo | [CLAUDE.md](CLAUDE.md) and the other files in [context/](context/) |

| Folder | What |
|---|---|
| `brain/` | Backend (Python, FastAPI): conversation, bank systems (mock), voice, audit, review, evals |
| `docs/manual-test-scripts.md` | Five long calls to run yourself (voice or text), with what to expect |
| `ui/` | Test console (Next.js + shadcn/ui): `/` console, `/kb`, `/calls`, `/desk` |
| `Sample Questions/` | The hackathon inputs (knowledge base, dummy data). Never modified |
| `docs/adr/` | Architecture decision records |
| `flows/` | Diagrams (Mermaid) |
| `context/` | Project memory: tracker, architecture, standards, rules |

## Run it
Terminal 1 (the brain; first start loads models in about a minute):
```bash
cd brain
python app.py
```
API at http://127.0.0.1:8000 (docs at `/docs`). Terminal 2:
```bash
cd ui
bun run dev
```
Open http://localhost:3000. First-time setup: [brain/README.md](brain/README.md), [ui/README.md](ui/README.md).

Tests: `cd brain && uv run pytest -m "not live"` (offline, no Groq calls). Real-LLM checks are rare and replay-cached: see "Tests" in brain/README.md.

## Pointers for the future (carried over from the removed planning notes)

Still open, none of it needed for the demo:

- **Compliance documents:** a limitations and risk register; a disclosure of third-party models and services; recording consent and a
  retention period under DPDP; confirm data residency rules with AU's compliance team (RBI payment-data localisation, outsourcing rules).
  Transcripts sent to the judge and summary models contain amounts and counterparty names: use an India-hosted model in production.
- **Resilience:** a second STT / TTS provider if one is down; a minimal scripted mode if the LLM is down; offer keypad options
  ("press 1 for card block...") after repeated unclear speech; ~20 concurrent STT streams is the Sarvam pay-as-you-go limit.
- **Bonus items:** quality analytics (top unresolved needs, high-friction journeys) built on the review table; accessibility ("slower
  please" needs per-call TTS rate); an extra Indian language beyond Hindi.
- **Knowledge upkeep:** rates change, so each document needs an effective-from date and an owner (tags exist: `doc_type`, `effective_to`,
  `superseded_by`; a register from AU is still needed). The 7.90% figure for the Green FD could not be checked against the rate card.
- **Going live on AU's APIs:** write one adapter per AU system against AU's specs (see PHASE-4-PLAN §3.5); re-validate workflows, status
  mappings and tests; set the queue and hours table from AU's real teams. Scale design: PHASE-4-PLAN §13 and ADR 0021, 0022.
- **PM questions that change the build:** PHASE-4-PLAN §19 (top call reasons, API catalogue and sandbox, allowed actions, document owners,
  compliance wording, queues and hours, languages, benchmark targets).
