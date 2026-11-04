# Voice Agent Playbook (how Unisona's voice is built, and what to reuse)

A portable summary of how Unisona's real-time voice agent works, so a new project (e.g. a bank's inbound
call agent) can be built on the same patterns. File paths are relative to `services/api/unisona/` unless noted.

---

## 1. Stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Voice framework | **Pipecat 1.12** (Python) | A frame pipeline: transport → processors → transport |
| Browser transport | **SmallWebRTC** (aiortc on the server, `@pipecat-ai/small-webrtc-transport` on the client) | Opus, DTLS-SRTP, ICE via STUN/TURN |
| Phone transport | Twilio / Plivo / Telnyx / Exotel **media streams over WebSocket** | μ-law 8 kHz; Pipecat serializers convert the audio |
| VAD | **Silero** (ONNX) | Speech start/stop |
| Turn detection | **Smart Turn v3** (audio end-of-turn model) + patience fallback | Deepgram Flux "eager end-of-turn" when that STT is used |
| Noise | **RNNoise** filter on input | Per-agent toggle |
| STT | Groq **Whisper large-v3-turbo** (default); Deepgram Nova-3/Flux, Sarvam, OpenAI, local faster-whisper | Language auto-detect (see pitfalls) |
| LLM | **Groq**: `qwen/qwen3-32b` (reasoning off) for voice, `openai/gpt-oss-120b` for chat | Streamed tokens, function calling |
| TTS | **Edge neural voices** (free, unofficial); Sarvam, Cartesia, ElevenLabs, Deepgram Aura, OpenAI, Groq, Kokoro (local) | Custom streaming Edge service with per-sentence language switching |
| Speech-to-speech | Gemini Live / OpenAI Realtime (`_s2s_service`) | Built, not tested live |
| Retrieval | fastembed multilingual MiniLM (384-d) + Chroma + BM25 (bm25s) + RRF + FlashRank reranker | Spreadsheets in DuckDB, queried by validated SQL |
| Backend | FastAPI, SQLAlchemy async, Postgres (Supabase, schema `app`), Postgres job queue (`SKIP LOCKED`) | No Redis, no Docker |

---

## 2. Call lifecycle (browser WebRTC)

1. **Signalling.** The client POSTs an SDP offer to `/voice/offer` (`api/chat.py`) and PATCHes trickle ICE candidates to the same path.
   ICE servers come from `/public/ice-servers` (`voice/ice.py`): STUN, plus TURN (Cloudflare or static credentials) when hosted.
2. **Prepare in parallel with the handshake.** `prepare_call()` (`voice/session.py`) starts *on the offer*, not after connect:
   - **Agent snapshot** (`_agent_snapshot`, cached 10 min, invalidated on agent, knowledge or key writes): config, prompt, tools, decrypted keys.
   - **Records:** contact, conversation and call IDs are generated client-side; `_create_records` runs in the background.
   - **Greeting:** pre-synthesised; `prewarm_live_agents()` warms every live agent at startup.
   - **Result:** the greeting plays **14–25 ms after connect** (it took 9.8 s before this work).
3. **Pipeline** (`run_voice_session`):
   ```
   transport.input → [RNNoise] → VAD → STT → SpeculativeRetriever → DTMFCollector → KnowledgeInjector
     → user aggregator → LLM (+tools) → TTS → transport.output → assistant aggregator
     (LatencyProbe / TurnProbe log ⏱ timings; AudioBufferProcessor records the call)
   ```
4. **Per turn**:
   - **`SpeculativeRetriever`** starts the knowledge lookup as soon as the transcript arrives, before the turn is committed. One embedding is shared by retrieval and golden-answer matching.
   - **`KnowledgeInjector`** rebuilds that turn's system context:
     - **Guards:** voicemail cues, opt-out detection ("call mat karo"), emotion → escalation.
     - **Flow step** instructions (`brain/flows.py`).
     - **Language rule:** sticky reply language, `brain/lang.py`: `sticky()`, `voice_reply_instruction()`.
     - **Retrieved chunks:** fenced as untrusted data.
     - **Noise filter:** noise-only transcripts ("।", "hmm") are dropped (`_MEANINGFUL` regex).
   - **LLM tools** (`_tool_schemas`/`_tool_handler`): booking, availability, `query_table`, `transfer_call`, `switch_agent` (squads), `press_keys` (IVR), custom HTTP and MCP tools.
     A pre-synthesised **filler** ("ek second…") plays while a tool runs.
   - **TTS** starts on the first complete sentence; Edge decodes MP3 *as it streams*; short phrases are cached.
5. **Barge-in.** User speech during bot speech (respecting `interruption_min_words`) cancels the LLM and TTS and flushes the audio.
6. **Finalize** (`_finalize`):
   - Duration, latencies, retrievals, emotions and per-turn breakdowns go into `Call.metrics`.
   - The recording is saved as WAV to Supabase Storage (`knowledge/files.py`).
   - A `Trace` row is written.
   - Background jobs run: analysis (summary, sentiment, CSAT, disposition), memory fact extraction, automations, webhooks and CRM sync.

**Phone calls** (`channels/telephony.py`):
- **Audio:** `handle_media_ws` feeds the same `run_voice_session`.
- **Call control:** `transfer_call` (warm/cold, with an AI brief), voicemail detection params, `call_limit`/`at_capacity` concurrency caps, and compliance checks before `dial`.

---

## 3. Latency: what actually moved the numbers

Measured from VAD end-of-speech to first bot audio: **3.4 s → 0.67–1.6 s**.

| Technique | Where | Effect |
| --- | --- | --- |
| Start call prep on the SDP offer, cache agent snapshots | `prepare_call`, `_agent_snapshot` | Greeting 9.8 s → <0.5 s after offer |
| Pre-synthesise the greeting and fillers; phrase cache | `edge_tts_service.prewarm`, `_CACHE` | No TTS wait on fixed phrases |
| Streaming MP3 decode for TTS | `edge_tts_service._decode_packets` | First audio without waiting for the full clip |
| Smart Turn fallback 3 s → `turn_patience_s` (1.2 s) | `_turn_strategies` | The biggest single saving; endpointing is the hidden latency |
| Speculative retrieval before turn commit | `SpeculativeRetriever` | ~150–300 ms |
| Keep DB writes off the hot path | ordered background transcript writer | Removes ~135 ms per round-trip to Tokyo |
| Read keyword-hit chunk text from Chroma, not Postgres | `knowledge/store.get_docs` | One fewer remote query per turn |
| `127.0.0.1`, not `localhost`, on Windows | `.env` | Avoids a ~2 s IPv6 fallback |
| Measure to *first audio*, not end of speech | `LatencyProbe` | Honest metrics |

Still slow: batch Whisper (use streaming STT: Deepgram, Sarvam), Edge first chunk 0.7–1 s (use Cartesia, Sarvam or ElevenLabs Flash), and the DB region (use Mumbai).

---

## 4. Multilingual: hard-won lessons

1. **Pipecat's Whisper services default to `language=EN`.** That makes Whisper *translate* Hindi/Tamil into English, and the agent replies in English.
   - **Fix:** `_auto_language_stt()` subclasses the service and omits `language` (auto-detect).
   - Pass a language hint only when the agent supports exactly one language (`_stt_language`).
2. **Per-turn reply-language instruction**, made *sticky*: short replies ("ok", "haan", "ORD-1001") keep the previous language.
3. **Short Latin-script text gets misdetected** (langdetect called "Order ORD-1001" German).
   - **Fix:** fewer than 4 real words defaults to English; trust langdetect only above probability 0.6; combine with `sticky()`.
4. **Write Hinglish in Devanagari** for TTS, or the Hindi words are mispronounced.
5. **Edge TTS switches voices per sentence** to the sentence's script/language (`voice_for_text`): a Tamil sentence gets a Tamil voice.
6. **WER for Indic scripts** needs Unicode-aware tokenizing; when STT and reference scripts differ, use an LLM-judge score (`evals/metrics.py`).
7. **Known weak spot:** short noisy Hindi with Groq Whisper. Sarvam STT is better for Hindi-first agents.

---

## 5. Production / ops patterns (`ops.py`, `app.py`)

- **Errors:** `ErrorMiddleware` (pure ASGI, *inside* CORS) adds a request ID, logs each unhandled error once with a traceback, and returns JSON 500.
  - DB-unreachable errors return **503** (`is_db_unavailable`); `db_health` logs one line per outage.
- **Background loops:** `supervise()` restarts the worker and scheduler with backoff; `install_loop_handler()` catches errors in fire-and-forget tasks.
- **Fatal states:** the `watchdog` exits the process if the event loop is blocked 90 s or memory runs out, so the host restarts it.
- **Health:** `/health` is liveness (no DB); `/health/ready` checks the DB.
- **Logs:** plain one-line production logs (`LOG_FORMAT=plain`); Rich console locally.
- **Supabase auto-restore** (`supabase_wake.py`): free projects pause after 7 idle days. With `SUPABASE_ACCESS_TOKEN` the API calls the Management API restore, waits until healthy, then finishes startup. Verified end to end: ~3 min.
- **Ephemeral disks:** `knowledge/heal.py` rebuilds Chroma/BM25 from Postgres chunks at startup; uploads and recordings are mirrored to Supabase Storage.
- **Edge TTS 403:** Microsoft rotates the token, so keep `edge-tts` upgraded (the Render build does `uv pip install --upgrade edge-tts`).
- **Windows dev:** uvicorn `--reload` sends CTRL_C_EVENT to the whole console.
  - `scripts/dev.mjs` runs the API in its own process group.
  - The API exits if the runner dies (`UNISONA_DEV_PARENT_PID`), so no orphan holds port 8000.
- **Deploy:** Vercel (web) + Render Standard (API, ~1 GB RAM). See `docs/deploy.md`.

---

## 6. Evals

- `evals/voice_sim.py`: a simulated caller (LLM persona + TTS audio, accents, noise, barge-in) calls the agent. It measures WER, latency and LLM-judge task success. `pnpm eval --kind voice`.
- `tests/smoke_suite.py` (32 end-to-end checks against a running API), `tests/e2e_voice.py <pk_...> out.wav` (a real WebRTC call), `pytest` (29 unit tests).

---

## 7. Reusing this for a bank inbound agent (or any regulated voice agent)

**Reuse as-is:**
- Voice pipeline: transports, VAD, Smart Turn, RNNoise, barge-in, fillers, latency probes, recording.
- DTMF collector, warm transfer with a brief, language handling (`lang.py` and the STT auto-detect fix).
- Ops safety net (`ops.py`), the `voice_sim` eval harness, and the job queue.

**Change:**
- **LLM role:** use it for NLU (intent, entities, language, as JSON) and phrasing only. Journeys run as **deterministic state machines** (extend `brain/flows.py`).
- **Policy gateway:** tools never take customer or account IDs from the LLM; the server injects them from the authenticated session. Each tool declares a required auth level.
- **Actions** need a confirmation token (specific action + target, short TTL), an idempotency key, and a **verified result** before saying "done".
- **Money and figures** come from templates filled with API data; the LLM never generates numbers.
- **Secrets:** OTP, PIN and card digits go through **DTMF straight to the auth service**, never through STT, the LLM or logs.
- **Audit:** an append-only, hash-chained event log per interaction (intent, auth result, tool request/response masked, confirmation with transcript snippet and timestamp, outcome, escalation).
- **Knowledge:** bank-approved answer cards instead of open retrieval.
- **Latency and hosting:** streaming STT (Sarvam/Deepgram), streaming TTS with an SLA (Sarvam Bulbul, Cartesia, ElevenLabs Flash, Azure), all hosted in an India region.
- **Prefetch:** after authentication, fetch the balance, recent transactions and cards in parallel.

**Latency budget to aim for:**
- Endpointing 200–300 ms, STT 100–250, LLM 150–350, TTS first audio 80–200, network 50–100.
- That gives **~0.7–1.0 s voice-to-voice**.
