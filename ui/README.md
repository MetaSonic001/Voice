# Console (UI)

Four pages, sharing one top bar. New alerts (caller wants a person, agent struggling, system health) pop up as toasts
on every page, with a link to the call.

**Test console** (`/`): type, record a voice note (listen back, then send or discard), or hold a live two-way call, as
one of the test customers. It also shows verification (keypad + simulated SMS).
- **Under your message:** how it arrived (typed / voice note / live call), and for speech the language heard plus the
  speech-to-text time. The bubble text is exactly what speech-to-text produced.
- **Each answer's "Details":** what was sent to the AI (masked); how it was understood; the grounding level; where
  the time went; the passages with scores; the per-sentence number check; the bank data read; **Correct / Wrong**
  (saved on the trace and counted in `/v1/stats`).
- **Knowledge used in this call:** documents cited by this call's answers are highlighted.
- Every conversation is saved: the header links to it in the call audit.

**Knowledge base** (`/kb`): drag and drop (or choose) a file to upload; every document with status, version and
passages; open one to rename it, set its valid-from date, drop in a new version, roll back, re-index, disable or delete
(with confirmation); a search preview shows which passages a question would use.

**Agent desk** (`/desk`): for the bank's people. Calls and chats the AI handed over wait here, most urgent first
(fraud on top). Each comes with a brief built from the record (verified or not, what was asked, what was already
done, why it was transferred). Accept a chat to type to the customer; accept a live call to talk to the caller through
your microphone (bridged and recorded). Wrap up with a disposition and notes; they go into the call record.
In the test console, a "Yes / No" pair appears when the agent asks for confirmation, and a banner shows while a chat
waits for a person.

**Call audit** (`/calls`): every past call, searchable. For each call: the summary (caller, verification, turns,
integrity check), the **recording** player, the **conversation** with *why the agent said each reply* (and the
evidence: passages, checks, timings, prompt version, turn hash; "Play from here" jumps the recording to that question),
the **audit trail** (every event, in order) and the **transcript** (download as .txt, or "Save as PDF").

```bash
bun install      # first time only
bun run dev      # http://localhost:3000 (the brain must be running: python app.py)
```

- **API address:** `NEXT_PUBLIC_API_URL`, default `http://127.0.0.1:8000`. See `.env.example`.
- **Production:** `bun run build` writes static files to `out/`. Host them anywhere (S3 + CloudFront, nginx); no Node server is needed.
- **Components:** [shadcn/ui](https://ui.shadcn.com) (new-york style), copied from the official registry into
  `components/ui/` so the code is in the repo and reviewable. They build on Radix UI. Dependencies, pinned exactly:
  next, react, radix-ui, class-variance-authority, clsx, tailwind-merge, lucide-react (icons), sonner (toasts),
  tw-animate-css. Add more with `bunx shadcn add <name>` (official registry only).
- **Theme:** neutral greys; colour only for meaning (green verified / good, amber attention, red problem). Light and
  dark follow the operating system.

| File | What it does |
|---|---|
| `app/layout.tsx`, `components/app-shell.tsx` | Top bar, navigation, live alert toasts |
| `app/page.tsx` | Test console: caller picker, chat, voice note, live call, keypad, simulated SMS, answer details |
| `app/kb/page.tsx` | Knowledge base |
| `app/calls/page.tsx`, `components/call-record.tsx` | Call audit: list and full call record |
| `app/desk/page.tsx`, `lib/desk-call.ts` | Agent desk: waiting board, brief, accept, chat or live audio bridge, wrap-up |
| `components/answer-details.tsx`, `components/labels.tsx` | Per-answer details, latency bars, outcome and grounding badges |
| `lib/api.ts` | REST calls to the brain |
| `lib/live-call.ts` | Live call over the WebSocket: microphone in, agent audio out, barge-in clear |
| `lib/worklets.ts` | Two audio worklets, loaded from in-memory Blob URLs: **capture** (microphone → 16 kHz PCM frames, with the "focus on my voice" gate) and **playback** (gapless agent audio, dropped instantly when the caller interrupts) |

Live calls need a microphone; use headphones so the agent doesn't hear itself. Chrome and Edge work best.


## Phase 4 additions (6 Nov 2026)

- `/fixes`: the fix list - every problem the post-call judge found, with the call and turn, why it matters, the fix and where to make it; mark done / won't fix / reopen.
- `/calls`: a score chip on every call (average of the judge's scores, "Critical", or "Check"), an "Only calls that need review"
  filter, and a **Quality review** panel on each call: the free checks, the judge's five scores, verified evidence quotes, a
  suggested fix, and a form for your own scores (these are how the judge is checked).
- `/desk`: a one-line headline, the deterministic brief (labelled "not written by the AI"), an **AI summary** with the caller's
  mood (labelled, figure-checked), and the full read-only conversation so far.
- Responsive: no horizontal page scroll at 390, 768, 1024 and 1440 px (the `/calls` list grid uses `minmax(0, 1fr)`; wide tables
  scroll inside their own container).
