# AU Small Finance Bank: multilingual voice agent

An inbound AI voice agent for AU Small Finance Bank: answers from the bank's documents, verifies callers by keypad code, reads
their own bank data, does a few controlled card actions after an explicit yes, raises tickets, hands over to people with context.
English, Hindi and Hinglish. Security and compliance come before features.

## Where to look

| I want to... | Read |
|---|---|
| Understand everything: how it works, what was built in each phase, how it goes live, research, open questions | [reference/PROJECT-GUIDE.md](reference/PROJECT-GUIDE.md) (the one detailed document) |
| Know where the project is, what is live, the dated log and numbers | [context/progress-tracker.md](context/progress-tracker.md) |
| See what is left to do (local demo and AWS) | [context/pending-work.md](context/pending-work.md) |
| Know **why** something was decided | [adr/](adr/README.md) (24 decisions) |
| Run, configure or test the backend | [brain/README.md](brain/README.md) |
| Run or change the UI | [ui/README.md](ui/README.md) |
| The rules for working on this repo | [CLAUDE.md](CLAUDE.md) and the other files in [context/](context/) |

| Folder or file | What |
|---|---|
| `brain/` | Backend (Python, FastAPI): conversation, bank systems (mock), voice, audit, review, evals |
| `ui/` | Test console (Next.js + shadcn/ui): `/` console, `/kb`, `/calls`, `/desk`, `/fixes` |
| `adr/` | Architecture decision records |
| `context/` | Project memory: tracker, pending work, architecture, standards, rules |
| `docs/deployment-costs-and-options.xlsx` + `docs/deployment-options-and-costs.md` | Cost estimate (13 Nov) for 100 calls at once, and the AWS proposal behind it |
| `docs/test-call-scripts.md` | Test calls to run yourself, with what to expect |
| `docs/work-tracker.xlsx` | The owner's own work log: daily log, interactions, people |
| `docs/diagrams/diagrams.md` | All diagrams (Mermaid), each with a short explanation |
| `reference/PROJECT-GUIDE.md` | The complete project guide (see the table above) |
| `reference/aws-primer.md` | AWS study guide: every concept and service, explained from scratch |
| `reference/briefing-security-state-scale.md` | Long-form review answers: row-level security, auth tokens, guardrails, evaluations, state, Bedrock APIs and cost |
| `deploy/` | Docker images and ECS task definitions (written, not yet run) |
| `Sample Questions/` | The hackathon inputs (knowledge base, dummy data). Never modified |

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

Tests: `cd brain && uv run pytest` (offline, no Groq calls). Real-LLM checks are rare and replay-cached: see "Tests" in brain/README.md.

Open questions and production research still to act on: [reference/PROJECT-GUIDE.md](reference/PROJECT-GUIDE.md) sections 29 and 30.
