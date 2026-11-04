# Test console (UI)

A plain, grayscale test console for the brain API. You can type, record a voice note (listen back, then send or discard), or hold a live two-way call. It also shows verification (keypad + simulated SMS).

For testing, every message shows:
- **Under your message:** how it arrived (typed / voice note / live call), and for speech the language it was heard as plus the speech-to-text time. The bubble text is exactly what speech-to-text produced.
- **Each answer's "details":**
  - what was sent to the AI (after masking);
  - how it was understood (language, intent, rewritten question, search keywords, personal topic and account);
  - where the time went, step by step;
  - the passages found, with relevance scores and which were given to the AI;
  - the per-sentence number check, and which bank data was read;
  - **✓ Correct / ✗ Wrong** buttons, saved on the trace and counted in `/v1/stats`.
- **A running summary for the call:** answers, median time, outcomes.
- **"Knowledge base · this call" box:** every searchable document plus bank data, with the ones this call's answers cited highlighted and counted. The full management page is linked from it.

**Knowledge base page** (`/kb`, linked from the console):
- every ingested document, with its status, version, passage count, and whether it's used for answers or stored only;
- open a document to see every passage exactly as stored and searched (with a filter), its version history and parser report;
- rename it, set its **valid-from date** (shown to the AI with the source), replace it with a new file (a new version, swapped in when indexed), roll back to an older version, re-index, disable/enable (takes effect at once), delete, or delete permanently;
- upload new documents (with sheet selection for Excel, a category, and a "valid from" date);
- a search box that shows exactly which passages a question would be answered from, with their scores.

```bash
bun install      # first time only
bun run dev      # http://localhost:3000 (the brain must be running: python app.py)
```

- **API address:** `NEXT_PUBLIC_API_URL`, default `http://127.0.0.1:8000`. See `.env.example`.
- **Production:** `bun run build` writes static files to `out/`. Host them anywhere (S3 + CloudFront, nginx); no Node server is needed.
- **Dependencies:** Next.js, React, Tailwind (plus TypeScript at build time). The four UI components in `components/ui.tsx` are written in the shadcn style without the shadcn/Radix packages.

| File | What it does |
|---|---|
| `app/kb/page.tsx` | Knowledge base: documents, passages, versions, upload/replace/disable/delete, search preview |
| `app/page.tsx` | The console: caller picker, chat, voice note, live call, keypad, simulated SMS, answer details |
| `lib/api.ts` | REST calls to the brain |
| `lib/live-call.ts` | Live call over the WebSocket: microphone in, agent audio out, barge-in clear |
| `lib/worklets.ts` | Two audio worklets (run on the browser's audio thread), loaded from in-memory Blob URLs: **capture** (microphone → 16 kHz PCM frames, with the "focus on my voice" gate that ignores quieter background voices) and **playback** (gapless agent audio, dropped instantly when the caller interrupts) |
| `components/ui.tsx` | Button, Input, Select, Badge, Panel |
| `components/answer-details.tsx` | The per-answer details panel (loads the answer's trace) |

Live calls need a microphone; use headphones so the agent doesn't hear itself. Chrome and Edge work best.
