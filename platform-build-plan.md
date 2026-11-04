# Platform Build Plan: Phase A (Knowledge) + Voice Layer, API-Only

Status: plan only, no code yet. This extends [phase-a-plan.md](phase-a-plan.md), which still holds the detailed design for ingestion, retrieval, answering and evaluation. This document adds:
- the changes Phase A needs so voice fits cleanly;
- the voice layer;
- how everything is tested **purely through API requests**;
- observability, latency and scalability.

**End state of this build:** one backend you operate entirely through HTTP and WebSocket requests, with no UI:
1. Upload and manage documents → ask questions in text → get cited answers + traces.
2. Send audio → it's transcribed → answered by the same knowledge service → audio comes back, with a per-step latency breakdown.
3. Switch STT/TTS providers (Sarvam, AWS, local) by config or per request.
4. Run text and voice evaluations through the API and read the reports.

---

## 1. Big picture

```
           REST (text)                 REST (audio file)              WebSocket (live audio)
               │                              │                                │
               ▼                              ▼                                ▼
        ┌─────────────┐              ┌──────────────────┐            ┌──────────────────────┐
        │  Text API   │              │ Voice "turn" API │            │ Voice streaming      │
        │ /v1/ask     │              │ STT → core → TTS │            │ (Pipecat pipeline)   │
        └──────┬──────┘              └────────┬─────────┘            │ VAD · turn · barge-in│
               │                              │                      └──────────┬───────────┘
               └──────────────┬───────────────┴─────────────────────────────────┘
                              ▼
                  Conversation core (channel-agnostic)
                  session · language · turn handling
                              │
                              ▼
                  Knowledge service (Phase A)
                  hybrid search → rerank → grounded answer → figure check
                              │
          ┌───────────────────┼──────────────────────────────┐
          ▼                   ▼                              ▼
     Postgres + pgvector   File storage (local → S3)    Traces / evals / metrics
```

**Key rule:** there is **one** brain, the conversation core plus the knowledge service. Text and voice are just different doors into it. Every answer, whether typed or spoken, goes through the same retrieval, the same figure check and the same trace.

---

## 2. Changes to Phase A so voice fits (built into Phase A from the start)

| Change | Why |
|---|---|
| **Sessions from day one:** `POST /v1/sessions`; `/v1/ask` and voice both accept a `session_id` | Follow-ups ("aur senior citizen ke liye?") need the previous turn. Voice calls are sessions anyway. |
| **A thin conversation core** between the API and the knowledge service: `handle_turn(session, text) → Reply` | Phases B–D add intent routing, verification and actions here, so the voice layer never changes again. |
| **The reply has two forms:** `display_text` (for the API) and `speech_text` (for TTS) | `speech_text` is shorter, numbers are written in Indian words ("सात दशमलव एक प्रतिशत" / "seven point one percent"), and Hindi words are in Devanagari. |
| **The answer is produced sentence by sentence, each checked before release** | Voice can start speaking the first sentence while the second is being written. The figure check runs **per sentence before TTS**, so streaming never lets an unchecked number through. |
| **The trace already has slots for voice steps** | One trace format for text and voice, so the latency breakdown covers everything. |
| **Query understanding returns JSON shaped for later intent detection** (`language`, `english_query`, `in_scope`, later `intent`) | Phase B adds intent to the same call; no redesign. |

---

## 3. The voice layer

### 3.1 What it has to do
1. **Take audio in**: a file (REST) or live 20 ms frames (WebSocket), in different formats and sample rates, converted to 16 kHz mono PCM internally.
2. **Clean it**: noise suppression (optional per provider).
3. **Detect speech** (VAD, Silero, local) and **the end of the caller's turn** (Smart Turn, local; Hindi quality still to be confirmed, with a silence-timer fallback).
4. **Transcribe** with the chosen STT provider, streaming where the provider supports it (partial transcripts while the caller speaks).
5. **Track language**: per-turn detection that stays "sticky" (short replies like "haan", "ok", "1234" don't switch it), and pick the TTS voice for that language.
6. **Call the conversation core** with the final transcript. Retrieval can start early on a stable partial transcript (speculative), but **nothing is spoken until the final transcript is answered**.
7. **Speak**: send each checked sentence to the chosen TTS provider and stream audio back as it's produced.
8. **Barge-in**: if the caller starts talking, stop the audio within ~100 ms and drop queued sentences. Short backchannels ("haan", "hmm") are ignored **only while the agent is speaking**, never when the agent is waiting for an answer.
9. **Fillers and cached phrases**: if an answer takes over ~400 ms, play "bas ek second…". The greeting, fillers and fixed lines are pre-synthesised per language and voice, then cached.
10. **Measure every step**: end of speech → STT final → understanding → retrieval → first LLM sentence → first TTS audio → first audio sent. All of this goes into the trace.

### 3.2 Swapping providers: how it's designed

**Two small interfaces, our own:**
- **STT port:** `transcribe(audio) → text` (batch) and `stream() → partial/final events` (streaming). Each adapter reports what it supports: streaming yes/no, languages, sample rates.
- **TTS port:** `synthesize(text, language, voice) → audio` (batch) and `stream(text, …) → audio chunks`.

**Adapters (one file each):**

| Role | Adapter | Mode | Use |
|---|---|---|---|
| STT | **Sarvam** (Saaras realtime, WebSocket) | streaming, partials | Production primary (strongest for Hindi/Hinglish) |
| STT | **AWS Transcribe** streaming (hi-IN, en-IN; 8 or 16 kHz) | streaming | Production backup / comparison |
| STT | **faster-whisper** (local) | batch per utterance (after VAD) | **Local testing**, offline, free |
| STT | sherpa-onnx streaming Hindi/Hinglish model (local, optional) | streaming partials | Local streaming experiments; community model, accuracy unproven |
| TTS | **Sarvam Bulbul** (v3 / v4 Flash, HTTP-stream or WebSocket) | streaming | Production primary |
| TTS | **AWS Polly** (neural, Hindi / Indian English voices) | streaming | Production backup / comparison |
| TTS | **Edge TTS** (hi-IN Swara/Madhur, en-IN Neerja) | streaming chunks | **Local testing only**: free, good quality, needs internet, unofficial (never production) |
| TTS | **Piper** (hi_IN "rohan") or **Kokoro** (Hindi voices, beta) | batch, fast | **Fully offline** local testing |

**Configuration (no code changes to switch):**
- **Profiles** pick a full set in one value: `VOICE_PROFILE=local` (faster-whisper + Edge), `sarvam` (Sarvam + Sarvam), `aws` (Transcribe + Polly), `mixed`.
- **A voice map file** (YAML) sets language → voice per provider (for example hi → Swara / Bulbul voice X / Polly voice Y).
- **Per-request override** for A/B testing: `?stt=aws&tts=edge` on REST, or in the WebSocket `start` message.
- **Fallback chain** per role (for example Sarvam → AWS) with a simple circuit breaker: after N failures, skip the provider for M seconds. The trace records which provider was actually used.
- `GET /v1/voice/providers` shows what's configured, healthy, and supported per language.

**Where Pipecat fits:**
- Pipecat runs **only the live streaming session**: WebSocket transport, VAD, turn detection, barge-in, frame flow.
- Our STT and TTS adapters plug into it through **one generic wrapper each**, so Pipecat never decides the provider; our config does.
- The REST voice endpoints use the same adapters directly, without Pipecat.

The result: one provider setup serves both paths, and if Pipecat ever had to be replaced, only the streaming session code changes.

### 3.3 Local testing setup (laptop)

| Piece | Local choice | Notes |
|---|---|---|
| STT | faster-whisper (`small` for speed, `large-v3-turbo` for accuracy, int8 on CPU) | Not streaming: transcribes each utterance after VAD. Hindi is fine; Hinglish weaker; slower per turn (expect ~1–3 s on CPU). Good for testing *logic*, not latency. |
| TTS | Edge TTS (online) or Piper/Kokoro (offline) | Edge sounds closest to production. Piper is the fastest offline option. |
| Embeddings + reranker | bge-m3 + bge-reranker-v2-m3, locally | Same as production |
| LLM | One hosted provider (cheap/fast), or Ollama for fully offline | Offline LLM quality and latency will be lower |
| Database | Postgres + pgvector (Docker or local install) | Only service needing setup |
| Files | Local disk | S3 adapter later |

**Latency numbers that count are measured on the cloud profile** (Sarvam/AWS + an India-hosted server). Local is for building and correctness.

### 3.4 Audio over the API (no UI)
- **REST:** accepts `wav`, `mp3`, `ogg`, `webm`, `m4a` (decoded and resampled by ffmpeg/PyAV). Returns JSON plus audio, either base64 in JSON or a URL to fetch the WAV/MP3 file.
- **WebSocket protocol** (`/v1/voice/stream`):
  - **Client → server:**
    - a `start` JSON message: session_id, language hint, providers, sample rate;
    - then **binary 20 ms PCM16 frames**;
    - `stop` to end.
  - **Server → client JSON events:**
    - `ready`, `speech_started`, `speech_ended`;
    - `stt.partial`, `stt.final`;
    - `agent.text` (with citations), `tts.started`, then **binary audio frames**, `tts.ended`;
    - `barge_in`;
    - `turn.metrics` (latency waterfall), `error`.
- **Phone later:** a μ-law 8 kHz serializer for Exotel/Plivo/Twilio media streams plugs into the same session, with no change to the core.
- **Test tools (not UI), kept in the repo:**
  - an **HTTP request collection** (`.http` files, or Bruno/Postman) for every endpoint;
  - a **command-line voice client** (`voice_client.py`) that streams a WAV file at real-time speed (or the microphone), saves the returned audio and prints the event timeline with latencies;
  - a few **sample audio files** (English, Hindi, Hinglish).

---

## 4. Complete API (what you'll call)

**Documents (Phase A)**

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/documents` | Upload (file, title, category, purpose = knowledge \| data, effective_from) |
| GET | `/v1/documents` | List with status, version, category, type |
| GET | `/v1/documents/{id}` | Details + ingestion report + versions |
| GET | `/v1/documents/{id}/chunks` | See exactly how it was parsed and chunked |
| PATCH | `/v1/documents/{id}` | Update details; enable or disable |
| DELETE | `/v1/documents/{id}` | Soft delete (`?purge=true` = permanent, admin) |
| POST | `/v1/documents/{id}/versions` | Replace content (new version, atomic swap) |
| POST | `/v1/documents/{id}/versions/{v}/activate` | Roll back |
| POST | `/v1/documents/{id}/reindex` · `/v1/reindex` | Re-chunk / re-embed |

**Conversation (text)**

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/sessions` | Start a session (language hint); returns greeting text + greeting audio URL |
| POST | `/v1/ask` | Question (+ session_id) → answer, citations, needs_clarification, trace_id, timings |
| POST | `/v1/search` | Retrieval only (debugging) |

**Voice**

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/voice/providers` | Configured providers, health, languages, voices |
| POST | `/v1/voice/stt` | Audio → transcript (test STT alone; `?stt=` override) |
| POST | `/v1/voice/tts` | Text → audio (test TTS alone; `?tts=&voice=`) |
| POST | `/v1/voice/turn` | **Audio in → transcript → answer → audio out**, + citations + latency breakdown |
| WS | `/v1/voice/stream` | Live, two-way streaming session (VAD, turn detection, barge-in, fillers) |

**Observability and evaluation**

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/traces/{id}` | Full trace: search hits, scores, citations, providers used, per-step timings |
| GET | `/v1/traces/{id}/audio` | Caller and agent audio for that turn (only if recording is on) |
| GET | `/v1/sessions/{id}` | Session transcript with trace links |
| POST | `/v1/evals/run` | Run a suite: `text` or `voice`, with optional provider/config overrides |
| GET | `/v1/evals/{run_id}` | Report: metrics by language and step, failures linked to traces |
| GET | `/v1/evals/compare?runs=a,b` | Side by side (for example Sarvam vs AWS STT) |
| GET | `/metrics` | Prometheus metrics |
| GET | `/health` · `/health/ready` | Alive · dependencies reachable (database, providers) |

All admin and write endpoints need an **API key**. Uploads have size and type limits. Requests are rate-limited per key.

---

## 5. How you'll test it (by API, step by step)

**Setup:** start Postgres, set `.env` (API key, LLM key, `VOICE_PROFILE=local`), start the service, and open the HTTP collection.

| # | What | Requests | What you check |
|---|---|---|---|
| 1 | Health | `GET /health/ready`, `GET /v1/voice/providers` | Database up, providers listed and healthy |
| 2 | Upload the knowledge base | 5 × `POST /v1/documents` (4 KB DOCX + rates PDF), poll `GET /v1/documents/{id}` | All reach `indexed`; ingestion report has no blocking warnings |
| 3 | Inspect parsing | `GET /v1/documents/{id}/chunks` for each | Sections intact; rate tables have headers on every row; duplicate Royale section dropped |
| 4 | Upload customer data as data | 1 × `POST /v1/documents` (xlsx, `purpose=data`) | Stored, **not** indexed; never appears in `/search` |
| 5 | Retrieval | ~15 × `POST /v1/search` (5 questions × English/Hindi/Hinglish) | Right passage in the top 3; look at dense vs keyword ranks |
| 6 | Answers | same ~15 × `POST /v1/ask`, then `GET /v1/traces/{id}` | Correct, short, cited, right language; refusals for out-of-KB; clarification for "FD rate?" |
| 7 | Follow-ups | `POST /v1/sessions` + 3 × `/v1/ask` with session_id | "Aur senior citizen ke liye?" uses the earlier context |
| 8 | Lifecycle | replace (versions), disable (PATCH), delete, activate (roll back), reindex; ~6 requests + re-ask | Answers change immediately and correctly; old traces still resolve |
| 9 | Text evaluation | `POST /v1/evals/run {suite: text}`, `GET /v1/evals/{id}` | Hit@5, correctness, refusal accuracy, zero ungrounded figures, p50/p95 |
| 10 | STT alone | ~6 × `POST /v1/voice/stt` (sample clips), then with `?stt=` overrides | Transcript quality per provider |
| 11 | TTS alone | ~6 × `POST /v1/voice/tts` (English/Hindi/Hinglish sentences with numbers) | Pronunciation of Hindi words and Indian numbers |
| 12 | Voice turn | ~10 × `POST /v1/voice/turn` (audio questions) | Transcript → answer → audio; latency breakdown in the response |
| 13 | Live streaming | `voice_client.py --file q_hi.wav` (several clips, plus one barge-in test that talks over the agent) | Partials arrive during speech; first audio fast; barge-in stops audio |
| 14 | Provider switch | repeat 12–13 with `VOICE_PROFILE=sarvam`, then `aws` (or per-request overrides) | Same answers; compare latency and transcripts |
| 15 | Voice evaluation | `POST /v1/evals/run {suite: voice, stt: …, tts: …}` per provider, then `/v1/evals/compare` | WER/CER per language, end-to-end correctness, latency p50/p95, cost |
| 16 | Failure behaviour | stop a provider, or set a fake key → repeat 12 | Fallback provider used (shown in trace); never silent or wrong |
| 17 | Load (later) | load-test script: N concurrent `/v1/ask` and M concurrent WebSocket voice clients | p95 holds; "busy" response when over capacity |

**Rough count:** about 80–100 manual requests for a full walkthrough. The two evaluation suites run another ~100 text and ~60–150 audio cases automatically.

---

## 6. Evaluation (accuracy you can prove)

| Suite | Contents | Metrics |
|---|---|---|
| **Text** (Phase A) | ~80–120 questions × English/Hindi/Hinglish; ~20% must-refuse; clarification cases; expected facts + must-contain source text | Hit@5, Recall@5, MRR; key-fact correctness; **ungrounded figures = 0**; refusal / clarification accuracy; p50/p95 per step; ₹ per question |
| **Voice** | 20–50 of the text questions as audio: recorded by people where possible, plus synthesised by a **different** TTS provider than the one under test (avoids bias); variants with noise (several noise levels), 8 kHz phone-quality audio, fast and slow speech, Hinglish | STT WER/CER per language (Unicode-aware for Devanagari); end-to-end answer correctness from audio; latency waterfall p50/p95 (end of speech → first audio); barge-in stop time; TTS check by "round trip" (synthesise → transcribe → compare) |
| **Provider comparison** | Same voice suite run per STT/TTS provider | One table: provider × language × WER × latency × ₹/min. This is how Sarvam vs AWS vs local gets decided |

Golden sets are YAML files in the repo with a version number. Every run stores the set version, the config, the provider names and the model/prompt versions, so runs stay comparable.

**Proposed bars:**

| Metric | Target |
|---|---|
| Hit@5 | ≥ 0.90 per language |
| Answer correctness | ≥ 0.90 |
| Ungrounded figures | 0 |
| Wrong answers that should have been refusals | ≤ 2% |
| STT WER on clean audio (Sarvam) | Hindi ≤ ~15%, Hinglish ≤ ~20% (set after the first baseline) |
| Voice-to-voice latency on the cloud profile | p50 ≤ 800 ms, p95 ≤ 1.3 s |
| Greeting | ≤ 1 s |

---

## 7. Observability (how you'll see what's happening)

- **Traces (in Postgres, via API):**
  - one record per turn, holding search hits and scores, citations, figure-check result, the providers actually used, the fallback if any, model/prompt/config versions, cost;
  - the **latency waterfall**: VAD end → STT final → understand → embed → search → rerank → first sentence → first TTS audio → first audio sent.

  This is the main debugging tool.
- **Structured JSON logs** with `request_id`, `session_id` and `trace_id` on every line. Personal data and secrets are masked.
- **Prometheus `/metrics`:**
  - request rate, errors, latency histograms per endpoint and per step;
  - active voice sessions;
  - provider errors and fallbacks;
  - ingestion queue length;
  - cache hit rates.
- **OpenTelemetry** instrumentation (FastAPI, database, HTTP clients), exported to the console locally and to any OTLP backend later (Grafana/Jaeger/X-Ray). It's optional and switched on by config.
- **Langfuse** (optional) for LLM call inspection. It's off by default, because traces already cover the essentials.
- **Recordings:** a per-session flag. When it's on, audio is stored encrypted with a retention period, and replayable through `/v1/traces/{id}/audio`.

---

## 8. Low latency: what's built in

| Technique | Where |
|---|---|
| Streaming STT with partial transcripts | Sarvam / AWS adapters |
| Semantic end-of-turn (Smart Turn) instead of silence timers | Voice session |
| Speculative retrieval on stable partial transcripts (read-only; nothing spoken until final) | Voice session → knowledge service |
| One small "understand" call; search methods run in parallel; reranking only the top ~20 | Knowledge service |
| Answers produced and checked sentence by sentence; TTS starts on the first checked sentence | Conversation core → TTS |
| Pre-synthesised greeting, fillers and fixed lines; TTS cache for repeated answers | Voice layer cache |
| Warm, persistent provider connections and connection pools | Adapters |
| Caches: question embeddings; answers to frequent questions (cleared when a document version changes) | Knowledge service |
| Local embedding and reranker models (no network hop) | Knowledge service |
| Everything hosted in Mumbai on the cloud profile; latency measured to first audio, not end of speech | Deployment / metrics |

---

## 9. Scalability

- **One codebase, three process roles**, started by config:
  - `api` (REST);
  - `voice` (WebSocket sessions, VAD and turn detection: CPU-light, I/O-heavy);
  - `worker` (ingestion and evaluation jobs).

  Locally they all run as one process. In production they scale separately.
- **Stateless processes:** session and trace state live in Postgres (Redis only if session reads ever become a bottleneck). Load balancers keep each live voice WebSocket on the same instance for the whole call (AWS ALB supports WebSockets).
- **Capacity limits:** each voice instance accepts N concurrent sessions; over the limit, it returns a clear "busy" response. This maps to the "all lines busy, callback" scenario.
- **Models:** the embedding model and reranker can move into a shared model server (one GPU serving many instances), or be swapped for API providers through their ports.
- **Provider quotas:** Sarvam pay-as-you-go allows ~20 concurrent STT streams, Pro/Growth ~100. Higher volume needs an enterprise plan or load spread with AWS through the fallback/routing chain. The plan treats provider concurrency as a capacity input.
- **Database:** HNSW vector index, connection pooling (PgBouncer / RDS Proxy), read replica later.
- **Autoscaling** (ECS) on active voice sessions and request rate.
- **Load testing:** a k6 or Locust script for REST, and a concurrent WebSocket client script for voice, run before claiming any capacity number.

---

## 10. Things that were missing, now added

1. **Sessions and follow-up questions** (needed for both text and voice).
2. **Two reply forms:** `speech_text` vs `display_text`, with Indian number words and Devanagari for TTS.
3. **Per-sentence figure check before TTS**, so streaming stays safe.
4. **Greeting with AI + recording notice**, pre-synthesised per language (`POST /v1/sessions`).
5. **Provider health, fallback and per-request override**, recorded in the trace.
6. **Audio format handling** (decode and resample); a phone μ-law path for later.
7. **Barge-in and backchannel rules** (backchannels ignored only while the agent speaks).
8. **Fillers and TTS caching.**
9. **Voice evaluation with noise / 8 kHz variants and a provider comparison report.**
10. **A command-line voice client and an HTTP request collection** (testing without a UI).
11. **API keys, rate limiting, upload limits, masking in logs.**
12. **Recording flag, encryption and retention.**
13. **Cost per turn** (STT seconds, LLM tokens, TTS characters → ₹) in the trace and the reports.
14. **Load-test scripts.**
15. **Phase B data gap (flagged):** the new flow verifies customers by **KYC (date of birth + address)**, but the dummy data has **no DOB or address**. With the data kept as-is, Phase B needs either a separate test-only KYC fixture file (not the dummy data) or the factor changed to last 4 digits of account/card + OTP. Decide before Phase B.

---

## 11. Build order (when we start)

| Step | Build | Usable result |
|---|---|---|
| **A0** | Skeleton: config, logging, database, migrations, health, ports, container, API key, HTTP collection | Service runs |
| **A1** | Documents: upload, types, hash dedupe, purpose, Docling parsing, chunking, versions, replace, disable, delete, roll back, jobs | Upload and inspect the KB |
| **A2** | Indexing: bge-m3 vectors + full-text, per-model embeddings, reindex | Searchable KB |
| **A3** | Retrieval + traces: understand, parallel search, merge, rerank, threshold, `/search` | Right passages found |
| **A4** | Sessions + conversation core + answering: grounded sentences with citations, per-sentence figure check, refusal, clarification, `display_text`/`speech_text` | Text Q&A works end to end |
| **A5** | Text evaluation + metrics + tuning; lock embedder/reranker/LLM by numbers | Phase A proven |
| **V1** | Voice ports + local adapters (faster-whisper, Edge, Piper); audio decoding; `/voice/stt`, `/voice/tts`, `/voice/providers` | Hear and speak locally |
| **V2** | `/voice/turn`: audio → core → audio, with latency breakdown; greeting and filler cache | Voice Q&A by file upload |
| **V3** | WebSocket streaming session (Pipecat): VAD, Smart Turn, partials, sentence streaming, barge-in, fillers; `voice_client.py` | Live, natural turn-taking |
| **V4** | Cloud adapters: Sarvam (STT + TTS), AWS (Transcribe + Polly); profiles, overrides, fallback + circuit breaker | Provider switching |
| **V5** | Voice evaluation suite + provider comparison + latency reports | Choices made on evidence |
| **V6** | Hardening: failure tests, recording/retention, load tests, Docker image, AWS deployment notes | Ready to extend into Phases B–D |

V1 can start once A4 is done. Phases B–D (verification, bank adapter, actions, transfer) then add to the conversation core and policy gateway, as described in phase-a-plan.md §8, with no changes to the voice layer.

---

## 12. Decisions needed before starting
1. Where Postgres runs for development (Docker / local install / managed).
2. The first LLM provider (any one; evaluation decides the final one).
3. Local machine: CPU only, or a GPU available (affects the reranker and local STT speed).
4. Provider accounts to set up now: Sarvam (₹100 free credit) and AWS (Transcribe/Polly in Mumbai), or local-only at first.
5. Which source wins when the KB and the rates sheet disagree.
6. Phase B KYC factors (DOB + address aren't in the dummy data; see §10.15).

---

## Sources (voice research)
- Pipecat services and AWS STT notes: [supported services](https://docs.pipecat.ai/api-reference/server/services/supported-services.md) · [AWS STT source](https://reference-server.pipecat.ai/en/latest/_modules/pipecat/services/aws/stt.html) · [Kokoro TTS in Pipecat](https://docs.pipecat.ai/api-reference/server/services/tts/kokoro.md)
- Sarvam streaming: [Realtime streaming STT](https://docs.sarvam.ai/api/api-guides-tutorials/speech-to-text/realtime-streaming) · [Streaming STT API reference](https://docs.sarvam.ai/api-reference-docs/speech-to-text/apis/streaming) · [TTS HTTP streaming](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/streaming-api/http-stream.md) · [pipecat-sarvam](https://pypi.org/project/pipecat-sarvam/)
- Local / offline speech: [Hindi/Hinglish streaming CTC (sherpa-onnx)](https://huggingface.co/mobilebytesensei/betterflow-hindi-streaming-ctc) · [Whisper Hindi on faster-whisper (discussion)](https://huggingface.co/vasista22/whisper-hindi-large-v2/discussions/6) · [Piper Hindi voice](https://huggingface.co/pronoobie/piper-voices-hindi) · [Kokoro voices](https://tts.ai/voices/kokoro/) · [Edge TTS voice usage example](https://huggingface.co/spaces/Vedika35/TTS/blob/main/app.py)
