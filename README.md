# AU Small Finance Bank: multilingual voice agent (MVP 1 + MVP 2)

**Everything about how this project works (architecture, flows, libraries, decisions): [PROJECT-GUIDE.md](PROJECT-GUIDE.md).**
**What comes next (audit, recording, alerts, monitoring console, barge-in, large PDFs, Cisco): [ROADMAP.md](ROADMAP.md).**

| Folder | What |
|---|---|
| `brain/` | The backend (Python, FastAPI). General questions answered from the knowledge base (MVP 1). Verified personal questions answered from bank data (MVP 2). Text and voice. |
| `ui/` | Test console (Next.js): chat, voice note, live call, keypad verification |
| `Sample Questions/` | The hackathon inputs: knowledge base, dummy customer data, sample questions (never modified) |
| `*.md`, `flows/` | Research, plans and diagrams |

## Run it
Terminal 1:
```bash
cd brain
python app.py
```
The API is at http://127.0.0.1:8000, with docs at `/docs`. The first start loads models in about a minute.

Terminal 2:
```bash
cd ui
bun run dev
```
Open http://localhost:3000.

First-time setup is in [brain/README.md](brain/README.md) (Python environment, models, knowledge-base upload) and [ui/README.md](ui/README.md) (`bun install`).
