# The AU voice agent, from zero to expert

A teaching guide to this repository. It starts from the basics (what sound is inside a computer, what a thread is, what a database index is) and ends with every folder, every important class, the full path of a live call, how many calls run at once and why, and a long list of questions you may be asked, with answers.

**How this guide relates to the others**
- `reference/PROJECT-GUIDE.md` is the reference: what the system does, why, and what was measured. This guide is the **course**: it teaches you the ideas underneath and walks through the code line by line.
- Every claim here points to a file and, where useful, a line. When you read a section, open the file next to it.
- Written 17 Nov 2026 from the code as it is in the working tree (including changes not yet committed). If code changes later, the code wins.

**How to read it**
1. Part A (foundations) if any of these words are new: PCM, sample rate, WebSocket, event loop, GIL, ASGI, transaction, index, embedding, HMAC.
2. Part B (the map) to know where everything lives.
3. Parts C to F are the heart: startup, a live call, one turn, documents.
4. Parts G to K: database, caches, concurrency, security, monitoring, tests, deployment.
5. Part O (where everything is stored, logging, every table, data lifecycle, failures) for architecture questions.
6. Part P (deployment, scaling, evaluation, measured numbers, limits, hard questions) right before a technical review.
7. Part L (patterns) and Part M (FAQ) before any review or interview.

---

## Contents

- Part A. Foundations
  - A1. Sound inside a computer: samples, 16 kHz, 16-bit, PCM
  - A2. Speech words: VAD, STT, TTS, endpointing, barge-in, echo
  - A3. Networks: sockets, HTTP, WebSocket, streaming, CORS
  - A4. How a Python program does many things at once: processes, threads, the GIL, asyncio
  - A5. The web server stack: ASGI, uvicorn, Starlette, FastAPI, Pydantic
  - A6. Databases: Postgres, tables, indexes, transactions, locks, migrations
  - A7. The AI pieces: LLM, tokens, JSON schema, embeddings, reranking, RAG
  - A8. Security building blocks: hashes, HMAC, AES-GCM, tokens, masking
- Part B. The repository, folder by folder and file by file
- Part C. Startup: what happens when you type `python app.py`
- Part D. The objects: classes, inheritance, protocols, who creates what
- Part E. A live call, end to end, at the level of bytes and tasks
- Part F. Inside one turn: `Conversation.turn()`
- Part G. Documents: upload, the job queue, chunks, vectors
- Part H. The database in depth: migrations and every table
- Part I. Every cache in the system
- Part J. Concurrency: the complete picture
- Part K. Security, privacy, audit, monitoring, tests, deployment
- Part L. Software engineering patterns used, and where
- Part O. Technical architecture: where everything lives (storage map, logging, every table, lifecycle, flows per door, config, failures)
- Part P. Deep-dive prep: deployment, scaling, evaluation, measured numbers, alternatives, languages, cost, known limits, hard questions
- Part M. Frequently asked questions (with answers)
- Part N. Quick glossary

---

# Part A. Foundations

## A1. Sound inside a computer: samples, 16 kHz, 16-bit, PCM

### What sound is
Sound is air pressure going up and down very fast. A microphone turns that pressure into a voltage that goes up and down the same way. That voltage is a smooth curve (an **analog** signal).

### Sampling: turning a curve into numbers
A computer can only store numbers. So the sound card **measures** the voltage many times a second. Each measurement is one **sample**: one number saying "how high was the curve at this instant".

- **Sample rate** = how many measurements per second. Unit: hertz (Hz), "per second". **16 kHz = 16,000 samples every second.** So one sample every 1/16,000 s = 0.0625 ms.
- A note on the user's question "16 hertz": it is **16 kilohertz** (16,000 Hz), not 16 Hz. 16 Hz would be 16 measurements a second, far too few to capture a voice.

**Why 16 kHz for speech?** The **Nyquist rule**: to capture a frequency `f`, you must sample at least `2 × f` times a second. 16 kHz captures sounds up to 8 kHz. Human speech carries almost all of its meaning below 8 kHz (consonants like "s" and "f" sit around 4 to 8 kHz). So:

| Sample rate | Captures up to | Used for |
|---|---|---|
| 8 kHz | 4 kHz | Classic phone lines ("narrowband"). Muffled "s"/"f" |
| **16 kHz** | 8 kHz | **"Wideband" speech. What we use. What Whisper, Silero VAD and Sarvam expect** |
| 44.1 / 48 kHz | 22 / 24 kHz | Music, CDs, laptop microphones by default |

The setting is `audio_rate: int = 16000` in `brain/app/config.py`. Silero VAD refuses anything else (`SileroVAD.__init__` in `brain/app/voice/providers.py` raises "SileroVAD expects 16 kHz audio").

### Bit depth: how precise each number is
Each sample is stored as an integer. **16-bit** means each sample is a whole number that fits in 16 bits: from **-32,768 to +32,767**. 0 is silence (the resting position of the speaker cone); large positive or negative numbers are loud.

- 16 bits = **2 bytes per sample**. This is the constant `BYTES_PER_SAMPLE = 2` in `brain/app/voice/audio.py`.
- More bits = finer steps = less hiss. 16-bit gives about 96 dB of range, plenty for speech.

### PCM
**PCM (Pulse-Code Modulation)** is the simplest way to store audio: just the list of samples, one after another, with nothing else. No compression, no header. When this project says "**PCM16 mono at 16 kHz**", it means:
- **PCM**: raw samples;
- **16**: each sample is a 16-bit signed integer (`s16`, "signed 16-bit");
- **little-endian** ("le"): of the 2 bytes in each sample, the low byte comes first. This is how Intel/AMD CPUs store numbers, so no conversion is needed. The voice API reports this as `"format": "pcm_s16le"` (`brain/app/api/voice.py`, `providers()`);
- **mono**: one channel (one microphone). **Stereo** = two channels interleaved (left sample, right sample, left, right…). The call recording is stereo: caller on the left, agent on the right;
- **16 kHz**: 16,000 samples per second.

### The arithmetic you will be asked about
| Quantity | Calculation | Result |
|---|---|---|
| Bytes per second | 16,000 samples × 2 bytes | **32,000 bytes/s ≈ 32 KB/s** |
| One minute | 32,000 × 60 | 1.92 MB (≈ 3.8 MB for stereo, both sides) |
| A 20 ms frame (what the browser sends, what the player sends) | 16,000 × 0.020 = 320 samples × 2 | **640 bytes** |
| A 32 ms frame (what the VAD reads) | 512 samples × 2 | **1,024 bytes** |
| Duration of `n` bytes | n / 2 / 16,000 s | `duration_ms()` in `audio.py` |

A **frame** is just a small slice of consecutive samples processed together. Different parts of the system use different frame sizes: the browser sends 20 ms frames, Silero VAD needs exactly 512 samples (32 ms), the player sends 20 ms frames, Opus encodes 20 ms frames. Code glues these together with byte buffers (`bytearray`) that collect bytes until a full frame is there.

### Integers versus floats
Machine-learning models want **floating-point** numbers between -1.0 and +1.0. Conversion: divide by 32,768. You see it in `SileroVAD.prob`:
```python
samples = np.frombuffer(frame, dtype=np.int16).astype(np.float32) / 32768.0
```
- `np.frombuffer(frame, dtype=np.int16)` reads the raw bytes as 16-bit integers (no copy);
- `.astype(np.float32) / 32768.0` scales to -1..1.

The browser goes the other way in the capture worklet (`ui/lib/worklets.ts`): `pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff` (0x8000 = 32,768, 0x7fff = 32,767), after clamping `s` to -1..1.

### Loudness: RMS and decibels
- **RMS** (root mean square) = the "average loudness" of a frame: square every sample, take the mean, take the square root. `rms()` in `audio.py`. Used to compare the caller's voice with background sound.
- **Decibels (dB)** compare two loudnesses on a log scale: `20 × log10(ratio)`. Ratio 0.25 ≈ -12 dB; 0.3 ≈ -10.5 dB; 0.5 ≈ -6 dB.
- **Gain** = multiply every sample by a number. `Player.duck(0.3)` multiplies agent audio by 0.3 (about -10 dB quieter) while the agent checks whether the caller really interrupted.

### Containers and codecs: WAV, MP3, Opus, Ogg
Raw PCM is big (32 KB/s). Files and network streams often use a **codec** (coder/decoder) to compress it, and a **container** (file format) to wrap it.

| Name | What it is | Where in this project |
|---|---|---|
| **WAV** | A 44-byte header (rate, channels, bits) followed by plain PCM | `to_wav()` wraps PCM before sending it to Whisper/Sarvam; the `/v1/voice/tts` endpoint returns WAV |
| **MP3** | Lossy compressed audio | Edge TTS sends MP3; `StreamingDecoder("mp3", 16000)` turns it back into PCM16 as it arrives |
| **Opus** | Modern lossy codec built for speech (used by WebRTC, WhatsApp) | Call recordings are encoded as Opus at 32 kbit/s stereo (`encode_ogg()` in `services/recordings.py`) |
| **Ogg** | A container that holds Opus packets | Recordings are `.ogg` files (then encrypted, `.ogg.enc`) |

**PyAV** (`av` package) is the Python wrapper around **FFmpeg**, the standard audio/video library. It decodes any format (`decode()`), **resamples** (changes sample rate, e.g. 24 kHz MP3 → 16 kHz) and encodes Opus.

### Why everything is PCM16 16 kHz inside
One internal format means no conversions between stages: the browser sends it, VAD reads it, STT receives it (wrapped as WAV), TTS output is decoded to it, the player sends it, the recorder stores it. Conversion happens only at the edges (MP3 from Edge, any file format for voice notes, Opus for recordings).

---

## A2. Speech words: VAD, STT, TTS, endpointing, barge-in, echo

| Word | Meaning | In this project |
|---|---|---|
| **VAD** (voice activity detection) | "Is someone speaking in this tiny slice of audio?" Returns a probability 0..1 | **Silero VAD**, a small neural network run locally on the CPU, ~1 ms per 32 ms frame. `SileroVAD` in `voice/providers.py` |
| **STT** (speech-to-text, also ASR) | Audio → words | Groq **Whisper large-v3-turbo** in development; **Sarvam saaras** in production. `GroqSTT`, `SarvamSTT` |
| **TTS** (text-to-speech) | Words → audio | **Edge** voices (free, unofficial, dev only) or **Sarvam bulbul**. `EdgeTTS`, `SarvamTTS` |
| **Utterance** | One stretch of a caller's speech | A `Capture` object in `voice/session.py` |
| **Endpointing / end of turn** | Deciding the caller has finished speaking | 700 ms of silence if the words sound complete, up to 2.5 s if they end in "and…", "aur…" |
| **Speculative STT** | Start transcribing before you are sure the turn ended | After 300 ms of silence (`SPECULATIVE_MS`) |
| **Barge-in** | The caller speaks while the agent is speaking | Agent's volume dips, the overlap is transcribed and classified |
| **Backchannel** | "hmm", "haan", "ok": listening noises, not interruptions | `_BACKCHANNEL` in `voice/turntaking.py` |
| **Echo** | The agent's own voice leaking from the speaker back into the microphone | Browser echo cancellation + a WebRTC loopback trick + server checks |
| **AEC** | Acoustic echo cancellation: subtracts known played audio from the mic | Built into the browser; works only on audio it knows was played |
| **Filler** | "One moment" when the answer is slow | After 1.2 s (`filler_after_ms`) |
| **Cascaded pipeline** | STT → LLM → TTS as separate steps (versus a single speech-to-speech model) | Our design (ADR 0011) |
| **DTMF** | The tones a phone keypad sends | How the OTP will arrive on a real phone; in the browser it is a JSON message |
| **Hysteresis** | Different thresholds to start and to stop, so a borderline signal doesn't flicker | VAD: 0.5 to start speech, 0.35 to stay in speech |

**Latency words**: **TTFT** (time to first token) = how long until the LLM's first word arrives. **Voice-to-voice** = from the moment the caller stops speaking to the first audio of the answer (`voice_to_voice_ms` in `VoiceSession._reply`).

---

## A3. Networks: sockets, HTTP, WebSocket, streaming, CORS

### IP, TCP, ports, sockets
- Every machine on a network has an **IP address** (like a building's street address). `127.0.0.1` means "this machine" (localhost).
- A **port** is a numbered door on that machine. The brain listens on **8000**, the UI dev server on **3000**, the optional search server on **8200**, the embedded mock bank on a random free port (or 8100 if set).
- **TCP** gives a reliable, ordered byte stream between two programs.
- A **socket** is the operating system's handle for one such connection. The server has one **listening socket** per port; each accepted client gets its own **connected socket**. This is how the OS knows which bytes belong to which caller: they arrive on different sockets.

### HTTP
**HTTP** is the request/response language on top of TCP. The browser sends a **request** (method + path + headers + optional body), the server returns a **response** (status code + headers + body), then the exchange is over.

- Methods: `GET` (read), `POST` (create/do), `PATCH` (change part), `DELETE`.
- Status codes: 200 OK, 201 Created, 400 bad input, 401 not authorised, 404 not found, 409 conflict, 415 unsupported file type, 429 too many requests, 500 server error, 502/503/504 gateway/unavailable/timeout.
- **Headers** used here: `X-API-Key` (our simple key), `X-Request-Id` (to follow one request in logs), `Authorization: Bearer …` (tokens to Groq, Sarvam, the bank systems, the search server), `Idempotency-Key` (bank writes).
- **REST** is the style of using these methods on "resources" (`/v1/documents/{id}`).
- **Keep-alive and TLS**: opening an HTTPS connection costs a TCP handshake plus a TLS (encryption) handshake, 100–250 ms to a far server. So clients keep connections open and reuse them. Every HTTP client here sets `keepalive_expiry=300` (5 minutes) and calls `warm()` at the start of a call.

### WebSocket
HTTP is one question, one answer. A phone call needs **both sides sending at any time, continuously**. A **WebSocket** gives that:
1. The browser sends a normal HTTP request with the header `Upgrade: websocket`.
2. The server answers `101 Switching Protocols` (`await ws.accept()` in `api/voice.py`).
3. From then on the same TCP connection carries **messages** in both directions, each either **text** (our JSON events) or **binary** (our PCM audio).
4. Either side ends it with a **close code**. Codes used here: **1013** "try again later" (all lines busy), **4401** wrong API key, **4404** voice disabled / handoff not found, **4400** bad first message.

Why WebSocket and not WebRTC: WebRTC is the browser's real-time media system, but it needs extra servers (STUN/TURN, signalling) and its own media handling. A WebSocket with raw PCM is simpler, and the production path is the bank's phone system (Cisco, SIP/RTP) anyway (ADR 0012).

### Streaming HTTP responses
Two places read a response while it is still arriving:
- **LLM streaming**: Groq sends **Server-Sent Events**: lines like `data: {...json...}`, ending with `data: [DONE]`. `OpenAICompatibleLLM.stream()` reads `response.aiter_lines()` and yields each text fragment.
- **TTS streaming**: Sarvam's `/text-to-speech/stream` sends audio bytes as they are made; `pcm_stream()` turns them into PCM pieces.

### CORS
Browsers block a page from one origin (`http://localhost:3000`) calling another (`http://127.0.0.1:8000`) unless the server says it is allowed. That permission is **CORS** (Cross-Origin Resource Sharing). `main.py` adds `CORSMiddleware` with `CORS_ORIGINS` from settings.

---

## A4. How a Python program does many things at once

This is the most important foundation for understanding concurrency here. Use one picture throughout: **a restaurant**.

### Process
A **process** is one running program with its own private memory. When you run `python app.py`, the operating system creates **one process**. Two processes share nothing unless they talk over a network or a database. (Postgres, started by pgserver, runs as **separate processes**.)

*Restaurant*: a process is one restaurant building.

### Thread
A **thread** is one line of execution inside a process. Threads of the same process **share memory** (the same Python objects). The OS switches between threads and can run them on different CPU cores at the same time.

*Restaurant*: threads are the staff inside one building.

### The GIL (global interpreter lock)
CPython (the normal Python, version 3.12 here) has a lock called the **GIL**. **Only one thread at a time may run Python bytecode** in a process. Threads take turns. But:
- a thread **releases** the GIL while it **waits** (network, disk, database, `time.sleep`);
- a thread releases it while running **C code that lets go of it** (PyTorch maths, NumPy, encryption, FFmpeg encoding).

So threads help with **waiting** and with **heavy C work**, not with running more Python at once. To run more Python at the same moment, you need **more processes**.

*Restaurant*: only one staff member may use the single order pad (Python) at a time, but others can be waiting at the oven (I/O) or the dishwasher (C code) meanwhile.

### Blocking versus non-blocking
A **blocking** call stops its thread until the answer comes (`requests.get`, `psycopg`'s `conn.execute`, `boto3`). If that thread is the one serving everyone, everyone waits.

### asyncio: one thread, many tasks
**asyncio** is Python's built-in system to handle thousands of waiting jobs on **one thread**:
- The **event loop** is a loop that keeps a list of things ready to run. It runs one, until that one says "I'm waiting" (`await`), then runs the next ready one.
- A **coroutine** is a function defined with `async def`. Calling it gives a coroutine object; it runs only when awaited or scheduled.
- `await x` means "pause me here until `x` is done, and let the loop run other things meanwhile".
- A **task** (`asyncio.create_task(coro)`) is a coroutine scheduled to run on its own, in the background, on the same loop. Costs a few kilobytes.
- `asyncio.gather(a, b)` runs several awaitables concurrently and waits for all.
- `asyncio.wait({a, b}, return_when=FIRST_COMPLETED)` waits for whichever finishes first.
- `asyncio.Queue` passes items between tasks; `asyncio.Event` is a flag tasks can wait on; `asyncio.Lock` makes tasks take turns.
- **Cancellation**: `task.cancel()` raises `CancelledError` inside the task at its current `await`. That is how an interrupted answer stops.
- **Async generator**: an `async def` with `yield`. The caller does `async for event in gen:` and gets items as they are produced. `Conversation.turn()` is one: it yields each checked sentence the moment it is ready.

*Restaurant*: the event loop is **one very fast waiter** serving many tables. He takes an order, hands it to the kitchen, and instead of standing there waiting, goes to the next table. When the kitchen rings (the I/O is done), he comes back. One waiter can serve many tables because tables spend most of their time waiting.

**The golden rule of asyncio**: never do slow blocking work on the event loop thread. If you must call a blocking library, send it to another thread:
- `await asyncio.to_thread(fn, args)` runs `fn` in the loop's **default thread pool** and lets the loop keep serving others while it runs. This project does this for **every database call** (psycopg is used in its blocking form).
- `loop.run_in_executor(None, fn)` is the older spelling of the same thing (used for audit events in `api/voice.py`).
- `loop.call_soon_threadsafe(...)` is how a background thread safely hands something back to the loop (used by `BedrockLLM.stream`).

The default thread pool size in Python 3.12 is `min(32, number_of_CPUs + 4)`: 20 on the owner's 16-CPU laptop.

### contextvars
A **ContextVar** is a variable whose value is private to the current task (like a thread-local, but for asyncio tasks). `logs.py` keeps `request_id` and `trace_id` in ContextVars so every log line of a request or turn carries its id without passing it around. `http_bank.py` copies the context into its worker threads (`contextvars.copy_context().run`) so bank calls made in threads are still recorded against the right turn (`BANK_CALLS`).

---

## A5. The web server stack: ASGI, uvicorn, Starlette, FastAPI, Pydantic

| Layer | What it is | Role here |
|---|---|---|
| **ASGI** | A Python standard: a server calls `app(scope, receive, send)` for each connection. `scope["type"]` is `"http"`, `"websocket"` or `"lifespan"` | The contract between uvicorn and FastAPI |
| **uvicorn** | An ASGI **server**: opens the socket, parses HTTP and WebSocket bytes, runs the event loop | Started by `cli.serve()` with `uvicorn.run("app.main:app", host, port)`. **One process, no `--workers`** |
| **Starlette** | A small ASGI toolkit: routing, requests, responses, WebSockets, middleware, background tasks | FastAPI is built on it |
| **FastAPI** | Adds: path functions with type hints, request validation via Pydantic, dependency injection (`Depends`), automatic OpenAPI docs at `/docs` | `create_app()` in `main.py` |
| **Pydantic** | Validates data against Python type hints | Request bodies (`AskBody`, `TTSBody`…) and `Settings` (via `pydantic-settings`, reading `.env`) |

**How a request travels**: TCP bytes arrive → uvicorn parses an HTTP request → calls the FastAPI app → **middleware** (`observe` in `main.py`: request id, rate limit, timing, metrics, error capture; then CORS) → the **router** finds the path function → FastAPI resolves `Depends(...)` (e.g. `container`, `require_key`) and validates the body → runs the function → turns the return value into JSON.

**`async def` versus `def` path functions** (important for concurrency):
- `async def` functions run **on the event loop**. They must not block. Example: `ask()`, `turn()`, `stream()`.
- plain `def` functions are run by FastAPI **in a worker thread** (AnyIO's thread pool, 40 threads by default), so they may block. Example: `list_traces()`, `board()`, `create_session()`. This is why many simple database endpoints are plain `def`.

**Lifespan**: code that runs once at startup and once at shutdown. In `main.py`, `lifespan()` builds the `Container` before the first request and closes it at the end (`yield` separates the two halves).

**Dependency injection with `Depends`**: `def providers(c: Container = Depends(container))` means "before calling me, call `container(request)` and pass the result as `c`". `container()` just returns `request.app.state.container`. Tests build their own container with fakes and pass it to `create_app(container)`; the routes do not change.

**WebSocket routes and middleware**: the `@app.middleware("http")` function only sees HTTP requests. WebSocket connections skip it, so `stream()` does its own API-key check and has its own live-call limit.

---

## A6. Databases: Postgres, tables, indexes, transactions, locks, migrations

### The basics
- **PostgreSQL** ("Postgres") is a database **server**: a separate program that stores data on disk and answers SQL queries over a socket.
- A **table** has **columns** (typed fields) and **rows** (records). A **schema** is a namespace of tables (`public` by default; the mock bank uses `corebank`).
- **Primary key**: the column(s) that uniquely identify a row (`id uuid PRIMARY KEY`).
- **Foreign key**: a column that must match a row in another table (`REFERENCES documents (id)`). `ON DELETE CASCADE` deletes child rows with the parent; `ON DELETE SET NULL` keeps them and blanks the link.
- **UUID**: a random 128-bit id, safe to create anywhere without asking the database.
- **JSONB**: a column that stores JSON in a binary, queryable form. Used for flexible data: `traces.data`, `sessions.auth`, `sessions.facts`, `sessions.state`.
- **Generated column**: computed by Postgres from other columns. `chunks.tsv` is generated from `section_path || ' ' || text`.

### Indexes
An **index** is a side structure that makes finding rows fast, like a book's index. Without one, Postgres reads every row.

| Index type | What it speeds up | Here |
|---|---|---|
| **B-tree** (default) | Equality and ranges | Most indexes, e.g. `traces_session (session_id, created_at)` |
| **GIN** | "Contains this word" in a `tsvector` | `chunks_tsv` for full-text search |
| **HNSW** (from pgvector) | "Nearest vectors" | `emb_hnsw_…` on `chunk_embeddings`, created at startup by `ensure_vector_index()` |
| **Partial index** | Only rows matching a `WHERE` | `chunks_active … WHERE active`, `jobs_queued … WHERE status = 'queued'`, the HNSW index `WHERE model = '<model>'` |

### Transactions and ACID
A **transaction** groups statements so they all happen or none do (`BEGIN … COMMIT`, or `ROLLBACK` on error). **ACID**: Atomic (all or nothing), Consistent, Isolated (others don't see half-done work), Durable (survives a crash once committed).
- The pool here opens connections in **autocommit** mode (`kwargs={"autocommit": True}` in `db.py`): each statement is its own transaction unless the code opens `with conn.transaction():`.
- That block is used where several changes must be atomic: switching a document to its new version (`process_version`), saving a turn with its hash (`TraceStore.save`), taking a job (`IngestWorker._take_one`), each migration file.

### Locks
| Lock | What it does | Where |
|---|---|---|
| `SELECT … FOR UPDATE` | Locks the selected rows until the transaction ends; others wanting them wait | `TraceStore.save` locks the session row so two turns of one call can't both read the same previous hash (no "fork" in the chain). `process_version` locks the document row |
| `FOR UPDATE SKIP LOCKED` | Like above, but **skip** rows someone else already locked instead of waiting | The job queue: several workers each grab a different job |
| **Advisory lock** `pg_advisory_lock(n)` | A lock on an arbitrary number, not a row. Released by unlock or when the connection closes | `STARTUP_LOCK = 7_241_001` in `db.py`: copies of the brain starting together take turns running migrations, creating the vector index and seeding the mock bank |

### Connection pool
Opening a database connection is slow (tens of ms). A **pool** keeps a few open and lends them out. `ConnectionPool(min_size=2, max_size=10)` in `db.py`: 2 always open, up to 10. `with db.connection() as conn:` borrows one and returns it at the end of the block. If all 10 are in use, the next borrower waits (up to the pool's timeout, 30 s by default).

### Migrations
A **migration** is a numbered SQL file that changes the database structure one step. Why: the code and the database must agree on the tables. When you add a column in code, every database (your laptop, a test database, production) must get it too, exactly once, in order.

How it works here (`Database._migrate` in `db.py`):
1. take the startup advisory lock;
2. `CREATE EXTENSION IF NOT EXISTS vector` (pgvector);
3. create `schema_migrations (name, applied_at)` if missing;
4. read which file names are already in it;
5. for each `app/migrations/*.sql` **in sorted name order** (`001_…`, `002_…`), if not done: run it **inside a transaction**, then insert its name. If it fails, the transaction rolls back and the name is not recorded.

Rules followed: files are never edited after being applied (a change is a new file); most are "only additions". Part H walks through all eleven.

### pgserver
`pgserver` is a pip package that **contains real PostgreSQL 16 binaries**. If `DATABASE_URL` is empty, `_start_private_server()` starts a private Postgres with its data in `brain/data/pg` (no Docker, no install). With `DATABASE_URL` set (Aurora/RDS on AWS), the same code talks to that server instead.

---

## A7. The AI pieces: LLM, tokens, JSON schema, embeddings, reranking, RAG

### LLM and tokens
- An **LLM** (large language model) predicts text, one **token** at a time. A token is a word piece (roughly ¾ of an English word).
- The input is a **prompt**: a **system** message (rules, persona) plus a **user** message (the question, sources, history).
- **Temperature** controls randomness. This project always uses **0** (most likely token each time) for repeatability.
- **Stateless**: the model remembers nothing between calls. Everything it needs is sent each time (ADR 0026). Memory lives in our Postgres.
- Two models: **`openai/gpt-oss-20b`** for understanding (fast, cheap), **`openai/gpt-oss-120b`** for writing answers (better). Both on Groq in development; on AWS Bedrock (Mumbai) in production.

### Structured output (JSON schema)
The understanding call must return data code can trust. It sends a **JSON schema** (a description of allowed fields, types and values) with `"strict": True` (`OpenAICompatibleLLM.json`). The provider forces the output to match. If the model still produces something the schema rejects, Groq returns the rejected text; `understanding.repair()` tries to fix it in code, else one retry with a looser schema.

### Embeddings and vectors
An **embedding model** turns text into a **vector**: a list of numbers (here **1,024** numbers) such that texts with similar meaning get vectors pointing in similar directions. "byaaj dar" (Hindi for interest rate) and "interest rate" land close together.
- **Cosine similarity** measures the angle between two vectors (1 = same direction). pgvector's `<=>` operator returns **cosine distance** = 1 - similarity; the SQL computes `1 - (a <=> b)` as the score.
- Model: **BAAI/bge-m3** (multilingual). It takes the vector of the first token (`[CLS]`) and normalises it to length 1 (`TransformerEmbedder.embed`).
- **fp16** ("half precision"): numbers stored in 16 bits instead of 32 on the GPU. About twice as fast, half the memory, same results for this use.

### Reranking (cross-encoder)
An embedding model reads the question and the passage **separately** (a "bi-encoder"), which is fast but rough. A **cross-encoder** reads them **together** and outputs one relevance score: slower but much more accurate. So: find ~16 candidates cheaply, then rerank them precisely. Model: **BAAI/bge-reranker-v2-m3**; its raw output (a "logit") is squashed to 0..1 with a **sigmoid** (`CrossEncoderReranker.score`).

### Full-text (keyword) search
Postgres turns text into a **tsvector**: a list of normalised words ("lexemes": lower-cased, stemmed, stop-words like "the" removed). A query becomes a **tsquery**. `@@` means "matches". `ts_rank_cd` scores how many query words appear and how close together. Good for exact names ("Zenith+", "BSBDA") that vectors may blur.

### RRF (Reciprocal Rank Fusion)
Two ranked lists (vector and keyword) need merging, but their scores are on different scales. RRF ignores scores and uses **ranks**: each list gives a passage `1 / (60 + rank)`. A passage ranked 1st in both gets `2/61`; one found by only one list gets less. `rrf_fuse()` in `services/retrieval.py`. `k = 60` is the standard constant from the original paper; it stops the top rank from dominating.

### HNSW
**Hierarchical Navigable Small World**: a graph index for vectors. Each vector links to some neighbours, in layers from coarse to fine. A search hops through the graph toward the query instead of comparing with every row. It is **approximate** (can miss a true nearest neighbour, rarely) but very fast.

### RAG
**Retrieval-Augmented Generation**: before answering, search your own documents, hand the best passages to the LLM, and tell it to answer **only** from them, citing `[1]`, `[2]`. The model writes; the facts come from the bank's documents. Here code also checks every number in the answer against the cited passages (the **figure check**).

---

## A8. Security building blocks

| Tool | What it is | Here |
|---|---|---|
| **SHA-256** | A one-way fingerprint of bytes (64 hex characters). Same input → same output; any change → a totally different output | File fingerprints (no duplicate uploads), recording file integrity, the hash chain of turns |
| **HMAC-SHA256** | A fingerprint that also needs a **secret key**: only someone with the key can make or check it | OTP storage (only the HMAC of the code is stored), bank tokens (`tokens.py`) |
| **Constant-time compare** | `hmac.compare_digest(a, b)` takes the same time whether the first or the last character differs, so an attacker can't guess byte by byte from timing | Token and OTP checks, search server token |
| **`secrets` module** | Cryptographically secure random numbers | The 6-digit OTP, per-process secrets |
| **AES-256-GCM** | Symmetric encryption (256-bit key) with built-in tamper detection ("authenticated encryption"). Needs a fresh 12-byte **nonce** per message. **AAD** (associated data) is extra text that isn't encrypted but must match on decryption | Call recordings: `MAGIC + nonce + encrypt(nonce, ogg, recording_id)`; the recording id is the AAD, so a file renamed to another id fails to decrypt |
| **Signed token** | `payload.signature`; anyone can read the payload, only the key holder can produce a valid signature | Customer-scoped bank tokens: `{cif, aud (systems), scp (scopes), exp}` signed with HMAC (`tokens.issue` / `tokens.verify`) |
| **Idempotency key** | A unique id sent with a write; if the same key comes again, the server returns the first result instead of doing it twice | Card blocks and tickets to the mock bank (`corebank.idempotency`) |
| **Luhn / Verhoeff checksums** | Check-digit algorithms for card numbers / Aadhaar | `privacy.redact` masks a 13–19 digit run only if it passes Luhn, 12 digits only if Verhoeff passes, so random numbers aren't over-masked |
| **Hash chain** | Each record stores `hash(previous_hash + content)`. Changing or deleting any record breaks all later hashes | Turns in `traces` (`services/audit.py`) |


---

# Part B. The repository, folder by folder and file by file

## B1. The top level

```
Voice/
├─ brain/               the backend: Python 3.12, FastAPI. Everything that thinks, listens and speaks
├─ ui/                  the test console: Next.js + React + shadcn/ui. Only for testing and demos
├─ adr/                 Architecture Decision Records: WHY each big choice was made (0002–0027)
├─ context/             project memory: progress tracker (where we are), pending work, standards, rules
├─ reference/           PROJECT-GUIDE.md (the reference), this guide, the AWS primer, the review briefing
├─ docs/                deployment options and costs (+ .xlsx), test-call scripts
├─ deploy/              ECS task definitions (JSON) and how to run the images on AWS
├─ Sample Questions/    the hackathon inputs: problem statement, questions, dummy data, KB. NEVER modified
├─ sarvam-check/        output of `brain sarvam-check` (voice samples comparing Sarvam models)
├─ .claude/launch.json  how the desktop app starts the brain and the UI for previews
├─ CLAUDE.md            rules for anyone (human or AI) working in the repo
└─ README.md            the short front page
```

**Why the split brain / ui?** The brain is the product (it will sit behind AU's phone system). The UI is a test bench: the bank's callers will never see it. In production the brain gets audio from Cisco, not from a browser.

## B2. `brain/` (outside `app/`)

| Path | What it is |
|---|---|
| `app.py` | Three lines: `from app.cli import main; main()`. So `python app.py` = `brain serve` |
| `pyproject.toml` | The Python project file: name, Python version (`>=3.12,<3.13`), the 22 runtime libraries, the `brain` command (`[project.scripts] brain = "app.cli:main"`), pytest settings (`asyncio_mode = "auto"`, one event loop for the whole test session), and the CUDA 12.8 build of torch (`[tool.uv.sources]`) |
| `uv.lock` | Exact versions of every library and sub-library, so every machine installs the same thing. Made by **uv** (a fast Python package manager) |
| `.env` / `.env.example` | Settings as environment variables. `.env` holds secrets (Groq key) and is git-ignored; `.env.example` documents every setting |
| `Dockerfile` | Builds the container image (Part K) |
| `README.md` | How to run, configure and test the brain |
| `requests.http` | Every API call, ready to send from VS Code |
| `scripts/download_models.py` | Downloads bge-m3 and the reranker (~4.4 GB) once, checks each file's SHA-256 |
| `seed/service-policies.md` | A synthetic, clearly labelled policy document added to the KB at first start (`_load_policy_pack` in `container.py`) |
| `evals/` | Real-LLM evaluation packs (Part K) |
| `tests/` | Offline tests with fakes (Part K) |
| `data/` (run-time, git-ignored) | `pg/` the Postgres data, `files/` uploaded originals, `recordings/` encrypted calls, `tts-cache/` saved phrase audio, `recording.key` (dev key), `audio/` |
| `logs/` (run-time) | `app.jsonl`, rotating JSON log (10 MB × 5 files) |
| `models/` (run-time) | The downloaded model weights |

## B3. `brain/app/` (the frame)

| File | Purpose | Key things inside |
|---|---|---|
| `__init__.py` | Marks `app` as a package; sets two Hugging Face env vars so downloads don't stall on Windows | `os.environ.setdefault("HF_HUB_DISABLE_XET", "1")` |
| `__main__.py` | Makes `python -m app …` work | calls `cli.main()` |
| `cli.py` | The command line. `serve` starts uvicorn; `search` starts the GPU search server; the rest (`ask`, `chat`, `upload`, `status`, `say`, `voice`, `sarvam-check`, `clean-slate`) are small HTTP clients of a running brain | `serve()` = `uvicorn.run("app.main:app", host, port, log_config=None)` |
| `config.py` | Every setting in one class, read from env vars and `.env` | `class Settings(BaseSettings)`; `get_settings()` wrapped in `@lru_cache` so the file is read once |
| `container.py` | The **composition root**: the one place that decides which real class implements each interface, and builds every service | `Container` and `VoiceRuntime` dataclasses; `build()`; `build_llm`, `build_search`, `build_voice`, `build_bank`, `build_stt`, `build_tts` |
| `ports.py` | The interfaces (contracts) every outside thing must satisfy | `Protocol` classes: `Parser`, `Embedder`, `Reranker`, `LLM`, `FileStore`, `STT`, `TTS`, `VAD`, `Bank`, `ServiceDesk`; errors `BankError`, `BankUnconfirmed` |
| `domain.py` | Plain data types shared by all layers. No I/O | dataclasses: `Block`, `ParsedDoc`, `ChunkDraft`, `Hit`, `Request`, `Understanding`, `Transcript`, `Customer`, `Account`, `Transaction`, `DebitCard`, `CreditCard`, `Loan`, `Emi`, `FixedDeposit`… |
| `db.py` | Database access | `Database` (starts pgserver or uses `DATABASE_URL`, runs migrations, holds the pool), `STARTUP_LOCK`, `ensure_vector_index()` |
| `main.py` | The FastAPI app | `create_app()`, `lifespan`, the `observe` middleware, `RateLimiter`, the module-level `app = create_app()` that uvicorn imports |
| `logs.py` | Logging that masks personal data | `JsonFormatter`, `TextFormatter`, ContextVars `request_id` / `trace_id`, `log()` helper |
| `metrics.py` | Prometheus counters, gauges, histograms | `REQUESTS`, `REQUEST_SECONDS`, `LIVE_CALLS`, `CALLS_REFUSED`, `TURNS`, `STAGE_SECONDS`, `observe_turn()` |
| `tokens.py` | Signed, short-lived tokens for bank calls (stdlib only) | `issue()`, `verify()`, `TokenError` |
| `search_server.py` | The embedder + reranker behind HTTP, for a separate GPU machine | `create_app(embedder, reranker, token)`, `/v1/embed`, `/v1/rerank`, `check_bind()` |
| `migrations/*.sql` | Database structure, one step per file (Part H) | 001–011 |

## B4. `brain/app/api/` (the doors)

A router file only validates input, calls one service, returns JSON. **No business rules live here**, so the same rules apply whichever door a request uses.

| File | Routes | Notes |
|---|---|---|
| `deps.py` | – | `container(request)` returns the app's `Container`; `require_key()` enforces `X-API-Key` when `API_KEY` is set |
| `conversation.py` | `POST /v1/sessions`, `GET /v1/sessions/{id}`, `POST /v1/ask`, `POST /v1/search`, `GET /v1/traces`, `GET /v1/traces/{id}`, `POST /v1/traces/{id}/feedback` | `/v1/ask` = one typed turn: `await c.conversation.ask(question, session_id)` |
| `auth.py` | `POST /v1/auth/verify`, `/resend`, `/end`, `GET /v1/auth/{session_id}`, `GET /v1/demo/callers` | Keypad codes arrive here (text channel); never through the LLM |
| `voice.py` | `GET /v1/voice/providers`, `POST /v1/voice/stt`, `/tts`, `/turn`, **WebSocket `/v1/voice/stream`** | The live call and the live-call limit (Part E) |
| `desk.py` | `/v1/desk/handoffs…` (board, accept, messages, complete), `/v1/handoffs/{session_id}` (customer side), **WebSocket `/v1/desk/stream`** | The human agent's desk; the desk WebSocket bridges the person's audio into a live call |
| `calls.py` | `/v1/calls`, `/v1/calls/{id}`, transcript, recordings, review, human labels, `/v1/fixes`, `/v1/reviews/…`, `/v1/alerts`, delete | The call audit pages |
| `documents.py` | `/v1/documents…` upload, list, get, chunks, patch, versions, activate, reindex, delete, jobs | The KB page |
| `ops.py` | `/health`, `/health/ready`, `/metrics`, `/v1/stats` | `/health/ready` reports `live_calls` like `"6/9"` but never fails because of it |

## B5. `brain/app/adapters/` (the outside world)

An **adapter** is a class that implements a **port** by talking to one real thing.

| File | Implements | Talks to | Key detail |
|---|---|---|---|
| `llm.py` | `LLM` | Groq (any OpenAI-compatible API) | `httpx.AsyncClient` with keep-alive; `json()` with strict schema; `stream()` reading SSE lines; retries only on 429/5xx and only within a 4 s total budget |
| `bedrock.py` | `LLM` | AWS Bedrock (Mumbai) | boto3 is blocking: `json()` runs in `asyncio.to_thread`; `stream()` runs boto3 in a background thread and passes pieces to the loop through an `asyncio.Queue` with `call_soon_threadsafe` |
| `resilient.py` | `LLM` (wrapper) | another `LLM` | On failure, retry the same request on a fallback model; a **circuit breaker**: 3 failures in 60 s → next 60 s go straight to the fallback. Never switches model mid-answer |
| `replay.py` | `LLM` (wrapper) | another `LLM` + a folder | Evals only: saves each real response under `sha256(exact request)`; unchanged requests are served from disk |
| `models.py` | `Embedder`, `Reranker` | PyTorch + transformers, on GPU (fp16) or CPU | Each model has a `threading.Lock`; the embedder locks **per batch of 16**, so a live query waits at most one batch behind a big document being indexed |
| `search_http.py` | `Embedder`, `Reranker` | the GPU search server over HTTP | `SearchClient`, `HttpEmbedder`, `HttpReranker` |
| `parsers.py` | `Parser` | files | `DocxParser` (own Word XML reader), `PdfParser` (pdfplumber), `TableFileParser` (openpyxl/csv), `TextParser`, and `ParserRouter` that picks one by type |
| `storage.py` | `FileStore` | local disk or S3 | `LocalFileStore`, `S3FileStore` (server-side encryption on every object) |
| `http_bank.py` | `Bank`, `ServiceDesk` | the bank systems over HTTP | `HttpBank`: 8-thread pool for parallel reads, customer-scoped tokens, status-word mapping, idempotency keys on writes, `BankUnconfirmed` when a write's outcome is unknown |
| `mock_corebank.py` | `Bank` (in-process variant) + the **seeder** | Postgres `corebank.*` | Builds the synthetic bank from the dummy workbook (read-only) plus the demo callers' stories; `ensure_seeded()` |

## B6. `brain/app/mockbank/` (a fake bank that behaves like a real one)

| File | What |
|---|---|
| `core.py` | `MockBank` (checks the service token and the customer token on every call, checks the data belongs to that customer, runs writes through an **idempotency table**), `BankConfig`, `parse_faults()` (inject latency, timeouts, 5xx, malformed JSON, lost responses), `create_app()` |
| `systems.py` | Eight FastAPI routers, one per bank system, each with its own paths and status words: `cif` (customer lookup), `cbs` (core banking: accounts, transactions, charges), `payments` (UPI/IMPS by UTR), `cards` (debit cards: status, controls), `cc` (credit cards), `lms` (loans, EMIs, rate changes), `deposits` (FDs), `crm` (complaints/tickets) |
| `server.py` | `EmbeddedServer`: binds a socket on 127.0.0.1, then runs **a second uvicorn server in its own thread with its own event loop** (`threading.Thread(target=lambda: asyncio.run(server.serve(...)))`). `start_embedded()` seeds data, makes random tokens for this process, starts the server and returns an `HttpBank` pointed at it |

Why a real HTTP server inside the same process? So the brain's bank adapter goes through real sockets, real timeouts, real status codes and real JSON, exactly as it will against AU's APIs. Only the URL changes in production (`BANK_BASE_URL`).

## B7. `brain/app/services/` (the logic)

Grouped by job. Sizes are lines.

**The conversation core**
| File | Job | Key objects |
|---|---|---|
| `conversation.py` (1,439) | Runs one turn, the same for every channel | `Conversation` (`turn`, `ask`, `understand`, `verify`, `_understood`, `_process`, `_dispatch`, `_answer`, `_personal`, `_journey`, `_check_sentence`), `Reply`, `TurnEvent`, `ANSWER_SYSTEM` prompt, `TEMPLATES` |
| `understanding.py` (592) | The router prompt + JSON schema; parsing; repair; **code corrections** after the LLM | `UNDERSTAND_SYSTEM`, `UNDERSTAND_SCHEMA`, `parse()`, `repair()`, `correct()`, `card_failing()`, `own_data()`, `with_risk()`, `asked_only()`, `broad_charges()` |
| `registry.py` (54) | Request kind → handler + access level | `Task` (frozen dataclass), `task_for()` |
| `state.py` (69) | The per-call state record (open requests, focus, last list, flow) | `CallState`, `StateStore` |
| `dialogue.py` (253) | How to answer a product question (overview, direct, full, compare, guided); caller facts and their scope | `pick_strategy()`, `merge_facts()`, `describe_facts()`, `next_question()` |
| `smalltalk.py`, `confirm.py` | Replies needing no LLM; yes/no/question/unclear in three languages | `is_courtesy`, `is_fragment`, `is_repeat`, `read_reply()`, `block_preference()` |

**Bank data and actions**
| File | Job | Key objects |
|---|---|---|
| `personal.py` (1,424) | Answers about the caller's own money from **fixed templates**, never LLM wording | `answer()`, `PersonalQuery`, `money()`, `_txn_line()`, the `T` template table |
| `actions.py` (200) | Card actions: plan, read back, execute once, read the state back | `CardAction`, `plan()`, `execute()` |
| `journeys.py` (740) | Multi-turn journeys: card actions, fraud, complaints, transfers, leads | `Journeys`, `Step`, `resolve()`, `offer_transfer()`, `start_handoff()` |
| `workflows.py` (109) | Finds candidate transactions only from what the caller said | `find_candidates()` |
| `auth.py` (237) | Verification state machine: RMN + OTP | `AuthService`, `AuthResult`, per-process `_SECRET` |
| `desk.py` (169) | Departments, waiting lists, handoffs, tickets | `DEPARTMENTS`, `HandoffService`, `LocalServiceDesk` |
| `summary.py` (51) | AI summary for the person who takes the call | `Summariser` |

**Knowledge base**
| File | Job |
|---|---|
| `retrieval.py` (129) | Dense + keyword search in parallel, RRF, rerank (`Retriever`, `rrf_fuse`) |
| `kbmeta.py` (229) | Product/variant tags; source selection, trimming, the variant gate, comparisons |
| `chunking.py` (266) | Blocks → passages of ~180 words with a section path; tables row by row |
| `documents.py` (320) | Upload, versions, replace, roll back, re-index, delete (`DocumentService`, `detect_type`) |
| `worker.py` (85) | The background ingestion thread (`IngestWorker`) |

**Checking and speaking**
| File | Job |
|---|---|
| `numbers.py` (206) | The figure check; numbers → Indian words for speech; markup cleaning |
| `guard.py` (112) | Injection screen, risk screens, unsafe-output check, instruction-like documents |
| `privacy.py` (106) | `redact()`: masks cards, Aadhaar, PAN, phones, accounts, emails, UPI ids |
| `pronunciation.py` (34) | "AU" said as "A.U." / "एयू" |
| `sentences.py` (37) | `SentenceSplitter` for streamed text |

**Recording and reviewing**
| File | Job |
|---|---|
| `store.py` (185) | `TraceStore`: sessions, one trace per turn (hash-chained), history, facts, pending question |
| `audit.py` (414) | `turn_hash`, `verify_chain`, `grounding()`, `AuditStore` (events, alerts, call records), `mask_number` |
| `recordings.py` (179) | `CallRecorder` (stereo in memory), `encode_ogg`, `RecordingStore` (AES-GCM, retention) |
| `review.py` (450) | Post-call code checks + LLM judge + fix list |
| `evals.py`, `maintenance.py` | Golden-set scoring and reports (run from `evals/golden_run.py`; there is no API route for it any more); clean slate and delete a call |

## B8. `brain/app/voice/` (ears, mouth, manners)

| File | Job | Key objects |
|---|---|---|
| `session.py` (573) | One live call | `VoiceSession`, `Capture`, `LIVE_SESSIONS`, constants (`SPECULATIVE_MS=300`, `BARGE_VAD_START=0.65`, `DUCK_GAIN=0.3`…) |
| `player.py` (100) | Real-time paced output | `Player` (`enqueue`, `duck`, `clear`, `_run`) |
| `providers.py` (325) | Speech vendors | `GroqSTT`, `SarvamSTT`, `EdgeTTS`, `SarvamTTS`, `SileroVAD`, `pcm_stream()`, `SpeechError` family |
| `fallback.py` (83) | Paid provider with a free backup | `_Fallback` → `FallbackSTT`, `FallbackTTS` |
| `turntaking.py` (91) | Pure rules: unfinished? noise? echo? backchannel? | `incomplete()`, `classify_barge()`, `is_noise()`, `unclear_speech()`, `is_echo()` |
| `phrases.py` (102) | Fixed phrases synthesised once and cached | `PHRASES`, `PhraseCache` |
| `audio.py` (90) | PCM helpers | `to_wav`, `decode`, `StreamingDecoder`, `rms`, `duration_ms`, `tone`, `silence` |
| `hold.py` (26) | Hold music generated with NumPy (no file, no licence) | `hold_music()` (`@cache`) |
| `vocab.py` (18) | Speech-recognition hints | `BASE`, `EXTENDED`, `hint_for`, `terms_for` |

## B9. `ui/` (the test console)

| Path | What |
|---|---|
| `app/layout.tsx`, `app/globals.css` | Page frame and styles (Tailwind CSS v4) |
| `app/page.tsx` | The console: pick a demo caller, type, record a voice note, or start a live call; shows each answer's details, the simulated SMS and keypad |
| `app/kb/page.tsx` | Documents: upload, versions, tags, chunks |
| `app/calls/page.tsx` + `components/call-record.tsx` | Call list and one call: turns, "why", sources, grounding, review, recordings, transcript |
| `app/desk/page.tsx` | The human agent desk: waiting board, accept, chat, join live call audio |
| `app/fixes/page.tsx` | The fix list from post-call reviews |
| `components/…` | `answer-details`, `review-panel`, `quality-summary`, `app-shell`, `labels`; `components/ui/` are shadcn/ui building blocks (button, card, table, tabs…) |
| `lib/api.ts` | Every call to the brain; `NEXT_PUBLIC_API_URL` |
| `lib/live-call.ts` | `LiveCall` class: mic → worklet → WebSocket; WebSocket → worklet → speakers |
| `lib/worklets.ts` | Two AudioWorklet processors as source strings: `capture` and `playback` |
| `lib/echo-safe-output.ts` | Plays agent audio through a WebRTC loopback so Chrome's echo canceller can remove it |
| `lib/mic.ts` | Opens the mic with echo cancellation, noise suppression, auto gain; reports what was applied |
| `lib/desk-call.ts` | The desk side of a live call's audio |
| `next.config.ts` | `output: "export"`: `bun run build` makes plain static files (no Node server in production) |
| `Dockerfile`, `nginx.conf` | Serves the static files with nginx |
| `package.json`, `bun.lock` | 9 runtime libraries; **bun** is the package manager and runner |

## B10. The other folders

| Folder | What, and when you need it |
|---|---|
| `adr/` | One file per big decision: context, decision, alternatives, consequences. Read the one that covers a thing before changing it. Accepted ADRs are never rewritten; a new one supersedes |
| `context/progress-tracker.md` | The diary of the project: what was built, test numbers, what's next, open questions. Read first |
| `context/pending-work.md` | What is left, for the demo and for production (B2.x items) |
| `context/architecture.md`, `code-standards.md`, `ai-workflow-rules.md`, `project-overview.md`, `ui-context.md` | Short context notes |
| `docs/deployment-options-and-costs.md` + `.xlsx` | The AWS design proposals and cost model |
| `docs/test-call-scripts.md` | Scripts for manual test calls |
| `deploy/README.md`, `deploy/ecs/*.json` | How to run the images on ECS; task definitions for brain, search server, UI |
| `reference/aws-primer.md` | AWS from scratch |
| `reference/briefing-security-state-scale.md` | Long answers prepared for a review meeting |

---

# Part C. Startup: what happens when you type `python app.py`

Follow the code in this exact order. Times are rough, on the owner's laptop.

### C1. From the command to uvicorn
1. `brain/app.py` runs `main()` from `app/cli.py`. `__init__.py` has already set the two Hugging Face env vars.
2. `cli.main()` parses arguments; no sub-command means `serve` with host `127.0.0.1`, port `8000`.
3. `serve()` calls `uvicorn.run("app.main:app", host=..., port=..., log_config=None)`. `log_config=None` tells uvicorn not to install its own logging (ours is set up in `create_app`).
4. uvicorn imports the module `app.main`. **Importing runs its top-level code**, which ends with `app = create_app()`.

### C2. `create_app()` (in `main.py`), still before any socket is open
1. `get_settings()` builds `Settings()`: pydantic-settings reads environment variables and `brain/.env`, converts types (`"9"` → `9`), wraps secrets in `SecretStr` (printing one shows `**********`). `@lru_cache` makes later calls return the same object.
2. `setup_logging()`: a console handler (readable text) and a rotating file handler (`logs/app.jsonl`), both masking personal data; quietens noisy libraries.
3. Defines `lifespan` (not run yet).
4. Creates the `FastAPI` object, the `RateLimiter`, the `observe` middleware, the `DocumentError` handler, `CORSMiddleware`, and includes the routers. The voice router is imported **only if** `VOICE_ENABLED` (so a text-only brain never imports audio libraries).

### C3. uvicorn starts serving
1. Creates the **event loop** on the **main thread** (on Linux with `uvicorn[standard]` it uses `uvloop`, a faster loop; on Windows the standard asyncio loop).
2. Runs the **lifespan startup**: our `lifespan()` up to its `yield`.
3. Only after startup finishes does it bind port 8000 and accept connections. This is why the first minute after start returns "connection refused": models are loading.

### C4. `lifespan()` → `build(settings)` (in `container.py`): the big one
This runs **synchronously** inside the lifespan (it blocks the loop, which is fine: no calls exist yet).

| Step | Code | What happens | Threads/processes created |
|---|---|---|---|
| 1 | `Database(settings.database_url, settings.data_dir)` | If no `DATABASE_URL`: `_start_private_server()` prunes dead pids from pgserver's handle file, starts **Postgres 16** with data in `data/pg`, retries every 3 s for up to 120 s (crash recovery after an unclean stop can be slow). Then `_migrate()` (advisory lock, pgvector extension, apply pending SQL files). Then the **connection pool** (2 open, up to 10) | Postgres **processes** (a postmaster plus its helpers); the pool's background worker threads |
| 2 | `build_search()` | `SEARCH_PROVIDER=local`: load **bge-m3** and **bge-reranker-v2-m3** onto the GPU in fp16 (from `models/` if present), then run one tiny embed and one rerank to "warm" them (the first run is slow). `http`: create a `SearchClient` and ping the search server | torch/CUDA/tokenizer native threads (many, mostly idle) |
| 3 | `build_llm()` | `OpenAICompatibleLLM` (Groq) or `BedrockLLM`; wrapped in `ResilientLLM` if `LLM_FALLBACK` is set; wrapped in `ReplayLLM` if `LLM_REPLAY_DIR` is set | none (connections open lazily) |
| 4 | `build_store()` | `LocalFileStore(data/files)` or `S3FileStore` | – |
| 5 | `build_voice()` | Builds STT and TTS (wrapped in `FallbackSTT`/`FallbackTTS` when the primary is Sarvam); **loads Silero once** (`SileroVAD(s.audio_rate)`) so the first call doesn't pay; returns `VoiceRuntime(stt, tts, vad_factory, PhraseCache)` where `vad_factory = lambda: SileroVAD(rate)` | importing `silero_vad` sets torch to 1 thread for the whole process |
| 6 | `build_bank()` | `BANK_PROVIDER=http` and no `BANK_BASE_URL`: `start_embedded()` seeds `corebank.*` once (from the dummy workbook, read-only), makes random tokens, starts the **mock bank server** on a free port, returns `HttpBank` | one **thread named `mockbank`** running its **own event loop** and its own uvicorn; `HttpBank`'s 8-thread pool (threads start on first use) |
| 7 | `db.ensure_vector_index(name, dim)` | Under the advisory lock: `CREATE INDEX IF NOT EXISTS emb_hnsw_<model>_<dim> … USING hnsw ((embedding::vector(1024)) vector_cosine_ops) WHERE model = '<model>'` | – |
| 8 | Services | `DocumentService`, `IngestWorker`, `TraceStore`, `Retriever`, `AuthService`, `AuditStore`, `RecordingStore`, `LocalServiceDesk`, `HandoffService`, `HttpServiceDesk` (tickets through the CRM API), `Journeys`, `Conversation`, `ReviewService`, `Summariser`, `EvalRunner` | – |
| 9 | `_load_policy_pack()` | First start only: uploads `seed/service-policies.md` | queues a job |
| 10 | `worker.start()` | Resets jobs left `running` by a crash to `queued`, starts the ingestion thread | one **thread named `ingest-worker`** (daemon) |
| 11 | `log("container ready", startup_ms=…)` | – | – |

Back in `lifespan`: `app.state.container = …`; if voice is on, `asyncio.create_task(voice.phrases.warm())` synthesises every fixed phrase (greeting, fillers, reprompts…) in the background. Then `yield`: the app is live.

### C5. What is running after startup
```
OS
├─ Process: python (the brain)
│   ├─ Main thread ── the asyncio event loop (uvicorn, every HTTP request handler marked async,
│   │                 every WebSocket / live call, the player, timers, the phrase warm-up task)
│   ├─ Thread "mockbank" ── its own event loop + uvicorn: the 8 mock bank systems on 127.0.0.1:<random>
│   ├─ Thread "ingest-worker" ── polls the jobs table every second
│   ├─ Default thread pool (created on first use, up to min(32, CPUs+4)) ── asyncio.to_thread work: DB calls, search, audit
│   ├─ AnyIO thread pool (FastAPI's, up to 40) ── plain `def` route functions
│   ├─ "bank" thread pool (8) ── parallel HttpBank reads
│   ├─ psycopg pool worker threads
│   └─ native threads of torch / CUDA / tokenizers (idle most of the time)
└─ Processes: postgres (private server from pgserver, several processes)
```
Measured on the laptop: about 257 threads just after start (model warm-up), 75–122 while serving calls (PROJECT-GUIDE §26.11).

### C6. Shutdown (Ctrl+C)
uvicorn stops accepting, then runs the rest of `lifespan` after `yield`: cancels the phrase warm-up if still running, then `Container.close()`: stop the worker thread (`_stop.set()`, `join(10)`), close the LLM client, close speech clients, stop the mock bank server (`should_exit = True`, `join(5)`), close the bank's HTTP client, close the search client, close the database pool and **stop the private Postgres** (`server.cleanup()`).

---

# Part D. The objects: classes, inheritance, protocols, who creates what

## D1. How classes relate here: mostly composition, very little inheritance

**Inheritance** means "class B is a kind of class A and reuses its code" (`class B(A)`). **Composition** means "class B holds an A and uses it" (`self.a = a`). This project uses **composition almost everywhere** and keeps inheritance for three narrow purposes.

### Where inheritance IS used (the complete list)
| Base class | Subclasses | Why inheritance here |
|---|---|---|
| `pydantic_settings.BaseSettings` | `Settings` | Gets env/.env reading and validation for free |
| `pydantic.BaseModel` | Request bodies: `AskBody`, `SearchBody`, `SessionBody`, `FeedbackBody`, `TTSBody`, `CodeBody`, `SessionRef`, `Accept`, `Message`, `Complete`, `DocumentPatch`, `EvalBody`, `HumanLabel`, `FixStatus`, `EmbedIn`, `RerankIn`, the mock bank bodies | FastAPI validates JSON bodies against them and documents them in OpenAPI |
| `RuntimeError` | `LLMError`, `BankError`, `SearchError`, `SpeechError` | Custom error types so callers can catch exactly the failure they handle |
| `BankError` | `BankUnconfirmed` (in `ports.py`), `BankTimeout` (in `http_bank.py`) | An **error hierarchy**: `except BankError` catches all bank failures; `except BankUnconfirmed` catches "a write may or may not have happened", which must never be reported as success **or** failure |
| `SpeechError` | `STTError`, `TTSError` | Same idea; the base class carries `status`, `code` and the properties `out_of_credit`, `retryable` |
| `ValueError` | `DocumentError` (carries an HTTP status), `TokenError` | Client-side problems |
| `_Fallback` | `FallbackSTT`, `FallbackTTS` | Shared logic (switching to the backup, logging, `warm`, `close`); each subclass adds its own method (`transcribe` / `stream`) |
| `logging.Formatter` | `JsonFormatter`, `TextFormatter` | The standard way to customise log output |
| `typing.Protocol` | the 10 ports + `Brain` in `session.py` | Not real inheritance: see D2 |
| (browser) `AudioWorkletProcessor` | `Capture`, `Playback` in `worklets.ts` | The Web Audio API requires it |

### Composition, everywhere else
`Conversation` holds an `llm`, a `retriever`, `traces`, `auth`, `audit`, `journeys`. `Retriever` holds a `db`, an `embedder`, a `reranker`. `VoiceSession` holds `brain`, `stt`, `tts`, `vad`, `phrases`, a `Player`, a `CallRecorder`. Each is passed in through `__init__` (constructor injection). The wrappers are composition too: `ResilientLLM(inner, fallbacks)` **holds** another LLM and has the same methods (the **decorator** pattern).

## D2. Protocols: interfaces without inheritance

`ports.py` defines interfaces with `typing.Protocol`:
```python
class STT(Protocol):
    name: str
    async def transcribe(self, pcm16: bytes, rate: int) -> Transcript: ...
```
A Protocol is **structural typing** ("duck typing" checked by type checkers): **any class that has these attributes and methods counts as an `STT`**, without inheriting from it. `GroqSTT`, `SarvamSTT`, `FallbackSTT` and the test's `FakeSTT` all satisfy `STT` without mentioning it.

Why Protocols rather than abstract base classes (ABC)?
- adapters (and test fakes) don't need to import the port module or inherit anything;
- a third-party class with the right methods would fit as-is;
- nothing is checked at run time; the contract is for readers and type checkers. The real enforcement is the tests.

## D3. Dataclasses
`@dataclass` writes `__init__`, `__repr__` and `__eq__` for you from the field list. Used for plain records: everything in `domain.py`, `Reply`, `TurnEvent`, `Capture`, `CallState`, `AuthResult`, `Step`, `Container`, `VoiceRuntime`, `BankConfig`. `@dataclass(frozen=True)` (`Task` in `registry.py`) makes instances read-only: a routing decision can't be changed after it's made. `field(default_factory=list)` gives each instance its own new list (a plain `= []` default would be shared by all instances, a classic Python bug).

## D4. Who creates what, and how long it lives

| Lifetime | Objects | Created by |
|---|---|---|
| **Once per process** (effectively singletons) | `Settings`, `Container`, `Database` + pool, embedder, reranker, LLM client(s), STT/TTS clients, `PhraseCache`, `HttpBank`, the mock bank server, every service (`Conversation`, `Retriever`, `AuthService`, `AuditStore`…), `IngestWorker`, `RateLimiter`, `_SECRET` in `auth.py`, the `_live_calls` counter, `LIVE_SESSIONS` dict | `build()` / module import |
| **Once per live call** | the WebSocket, an `asyncio.Lock` for sending, `VoiceSession`, its own **`SileroVAD`** (via `vad_factory()`, because the model keeps memory between frames), its `Player` (+ its player task), its `CallRecorder`, a `sessions` row | `stream()` in `api/voice.py` |
| **Once per utterance** | a `Capture` (audio buffer, timers, speculative STT task) | `VoiceSession._process_frame` |
| **Once per turn** | a trace id (uuid4), `timings`/`data` dicts, a `CallState` loaded from the DB, an `Understanding`, `Request`s, `Hit`s, a `SentenceSplitter`, `TurnEvent`s, a `Reply`, a `traces` row; in voice, the reply task with its `asyncio.Queue` of sentences and the filler task | `Conversation.turn()`, `VoiceSession._reply()` |
| **Per request (HTTP)** | the request, the validated body, a request id | uvicorn/FastAPI |

**Why one Silero model per call but one Whisper client for all?** Silero is **stateful**: it remembers the previous frames to judge the current one. Sharing it between calls would mix callers' audio history. The Whisper/Sarvam clients are **stateless** HTTP clients: each request carries all its audio, so one shared client (with a pool of connections) is correct and cheaper.

## D5. The main classes, one paragraph each

- **`Container`** (`container.py`): a dataclass holding every long-lived object. Routes get it with `Depends(container)`; WebSocket routes read `ws.app.state.container`. `close()` shuts everything down in order.
- **`Database`** (`db.py`): starts or connects to Postgres, migrates, owns the pool. `connection()` lends a pooled connection. `startup_lock()` is a context manager holding the advisory lock.
- **`Conversation`** (`services/conversation.py`): the brain of one turn. `turn()` is an async generator yielding `TurnEvent`s. `ask()` consumes `turn()` and returns the final `Reply` (used by `/v1/ask`).
- **`TurnEvent`**: `kind` is `understood` (language/intent known), `sentence` (one checked sentence: `text` for the screen, `speech` for TTS), `auth` (show the keypad / simulated SMS), `handoff` (stop the AI, hold music), `done` (the final `Reply`).
- **`Reply`**: the result of a turn: outcome (`answered`, `clarify`, `refused`, `needs_verification`, `handoff`, `smalltalk`, `error`…), intent, language, answer, speech text, citations, timings, auth, handoff.
- **`Retriever`** (`services/retrieval.py`): `search()` runs dense and keyword searches in parallel threads, fuses with RRF, reranks.
- **`AuthService`** (`services/auth.py`): the verification state machine, stored in `sessions.auth`; caches the verified customer for 8 s per session (`PREFETCH_TTL_S`).
- **`TraceStore`** (`services/store.py`): sessions and turns; `save()` writes the hash-chained turn under a row lock.
- **`AuditStore`** (`services/audit.py`): call events, alerts, call records for `/calls`.
- **`VoiceSession`** (`voice/session.py`): one live call's state machine: `feed()` per audio chunk, `_process_frame()` per VAD frame, `_decide_turn()`, `_decide_barge()`, `_reply()`, hold/desk bridge, keypad code.
- **`Player`** (`voice/player.py`): a background task that sends agent audio in 20 ms frames at real-time speed, at most 200 ms ahead.
- **`CallRecorder`** (`services/recordings.py`): two growing byte buffers (caller, agent), aligned on the caller's clock; `stereo()` makes an (n, 2) NumPy array.
- **`IngestWorker`** (`services/worker.py`): a thread that takes one queued job at a time with `FOR UPDATE SKIP LOCKED`.
- **`HttpBank`** (`adapters/http_bank.py`): the only door to bank data; mints customer tokens; parallel reads; idempotent writes.
- **`MockBank`** (`mockbank/core.py`): the server side of the fake bank: checks tokens and ownership, idempotency, fault injection.


---

# Part E. A live call, end to end, at the level of bytes and tasks

The scenario: Ravi (demo caller `9000000101`) clicks "Start call" in the console, hears the greeting, asks "What's my balance?", types the code, hears his balance, interrupts once, then hangs up.

## E1. In the browser: from microphone to WebSocket (`ui/lib/live-call.ts`, `worklets.ts`, `mic.ts`)

1. **Open the mic** (`openMic()`): `navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true}})`. The browser asks permission, then gives a `MediaStream`. It reports what it actually applied.
2. **Audio graph**: `new AudioContext({sampleRate: 16000})`. Asking for 16 kHz makes the **browser resample** the mic (usually 48 kHz) to 16 kHz for us. Then two **AudioWorklets** are loaded from source strings (via Blob URLs, so no network fetch).
   - An **AudioWorklet** is code that runs on the browser's dedicated **audio thread**, called every **128 samples** (the "render quantum", 8 ms at 16 kHz). It must never block.
3. **Capture worklet** (`Capture.process`): collects samples into a 320-sample buffer (**20 ms**). When full, `send()`:
   - computes the frame's RMS;
   - the **"focus on my voice" gate**: it learns the room's noise floor and the caller's speech level; frames much quieter than the caller (below `max(3 × floor, 0.25 × voice)`, about 12 dB under) become **silence**, with a 200 ms "hangover" so word endings aren't cut. People talking in the background never reach the server;
   - converts float (-1..1) to Int16 and posts the 640-byte buffer to the main page thread (`port.postMessage(pcm.buffer, [pcm.buffer])`, the second argument **transfers** the memory instead of copying).
4. **WebSocket**: `new WebSocket(ws://127.0.0.1:8000/v1/voice/stream)`, `binaryType = "arraybuffer"`. On open, it sends **one text message**: `{"type":"start","session_id":…,"language":"en","greet":true}`. From then on each 640-byte frame is sent as a **binary message**: 50 messages a second, 32 KB/s.
5. **Receiving**: binary messages (agent audio) go to the **playback worklet**, which queues them and plays them gaplessly (it converts Int16 back to float). A text message `{"type":"clear"}` empties that queue at once (interruption). Other text messages (`ready`, `agent_text`, `user`, `auth`, `reply_done`, `turn_metrics`, `busy`, `end`…) update the screen.
6. **Echo-safe output** (`echo-safe-output.ts`): the playback node's output is sent through **two `RTCPeerConnection`s inside the same page** (a loopback) and played by an `<audio>` element. Why: Chrome's echo canceller only removes sound it received over a peer connection. Without this, on laptop speakers the agent heard its own greeting through the mic and treated it as the caller speaking. If the loopback fails, it falls back to direct output and warns.

## E2. On the server: accepting the call (`api/voice.py`, `stream()`)

uvicorn receives the HTTP upgrade request on the listening socket, accepts the TCP connection (the OS gave it a new socket), sees `Upgrade: websocket`, and calls the FastAPI app with `scope["type"] == "websocket"`. FastAPI routes it to `stream(ws)` and **runs it as a new asyncio task**. Every call is one such task. Now line by line:

```python
c: Container = ws.app.state.container
if expected and (ws.headers.get("x-api-key") or ws.query_params.get("key")) != expected:
    await ws.close(code=4401); return          # API key (when configured)
if not c.voice:
    await ws.close(code=4404); return          # voice switched off
await ws.accept()                              # HTTP 101: now a WebSocket
```

**Admission control** (the live-call limit):
```python
if limit and _live_calls >= limit:
    CALLS_REFUSED.inc()
    await ws.send_text(json.dumps({"type": "busy", ...}))
    await ws.close(code=BUSY_CLOSE)            # 1013 "try again later"
    return
_live_calls += 1
```
`_live_calls` is a plain module-level integer. **No lock is needed** because (1) only the event loop thread touches it and (2) there is **no `await` between the check and the increment**, so no other task can run in between. In asyncio, code between two `await`s is never interrupted by another task. This is a key concurrency idea: in asyncio, "atomic" means "no await in the middle".

Then, inside `try … finally` (the `finally` always gives the line back, even if the task is cancelled):

1. `lock = asyncio.Lock()` and `send_json` / `send_audio` helpers that take the lock. Several tasks of this one call send on the same socket (the player sends audio, the reply task sends JSON events). The lock stops two sends from interleaving their bytes.
2. Wait up to **10 s** for the first message (`asyncio.wait_for(ws.receive_text(), timeout=10)`); it must be `{"type":"start"}` or the socket closes with 4400.
3. The caller number is cleaned (digits, `+`, spaces; max 20 characters).
4. `c.traces.create_session(language, caller)` inserts a `sessions` row; its UUID is the **session id** that every later record of this call carries.
5. `CallRecorder(16000)` if recording is on.
6. `on_event()` is defined: it writes call-timeline events to the database **in a worker thread** (`loop.run_in_executor(None, …)`) so the audio path never waits for the database.
7. `asyncio.create_task(c.voice.warm())` opens the STT/TTS HTTPS connections now, in the background, so the first turn doesn't pay the TLS handshake.
8. Builds the **`VoiceSession`** with this call's own VAD (`c.voice.vad_factory()`), shared STT/TTS clients, the phrase cache, the send helpers, the recorder.
9. `LIVE_SESSIONS[session_id] = session` (so the desk can find and join this call).
10. `await session.start(greet=True)`: sends `{"type":"ready","sample_rate":16000,"frame_ms":32}` and spawns the greeting task.

## E3. The receive loop

```python
while not session.closed.is_set():
    receive = asyncio.ensure_future(ws.receive())
    closed  = asyncio.ensure_future(session.closed.wait())
    done, _ = await asyncio.wait({receive, closed}, return_when=asyncio.FIRST_COMPLETED)
    if closed in done: receive.cancel(); break
    closed.cancel()
    message = receive.result()
    if message["type"] == "websocket.disconnect": break
    if message.get("bytes"):   session.feed(message["bytes"])        # audio
    elif message.get("text"):  # JSON control: stop | flush | text | otp
```
The loop waits for **either** the next message **or** the session closing itself (silence timeout, desk ended the call). While it waits it uses **no CPU**: the task is parked; the OS will wake the loop when bytes arrive on this call's socket.

Control messages:
- `stop` → leave the loop (hang up);
- `flush` → the client says the caller finished (push-to-talk or end of a test file) → decide the turn now;
- `text` → a typed turn inside a live call (testing);
- `otp` → **the keypad code**: `session.submit_code(code)`. It goes straight to verification. It never touches STT, the LLM, the transcript or the recording (the recording is audio only; the code was typed, not spoken).

## E4. `feed()` and `_process_frame()`: listening, 32 ms at a time (`voice/session.py`)

`feed(pcm)` is **synchronous** (no `await`). It:
1. appends the bytes to the recorder's caller buffer;
2. if a person has taken the call (`mode == "human"`), forwards the audio to them and returns;
3. adds the bytes to `_inbuf`, and while it holds at least **1,024 bytes** (512 samples = 32 ms, what Silero needs), cuts one frame and calls `_process_frame(frame)`.

Note the mismatch handled here: the browser sends 640-byte frames (20 ms), the VAD needs 1,024 (32 ms). The buffer bridges them. Over time, 8 browser frames (160 ms) give exactly 5 VAD frames.

`_process_frame(frame)`:
```python
self._clock_ms += self.frame_ms          # the AUDIO clock: +32 ms per frame, independent of wall time
p = self.vad.prob(frame)                 # Silero: probability of speech, ~0.9 ms of CPU, on the event loop
agent = cap is None and self.agent_active
start = BARGE_VAD_START if agent else 0.5          # 0.65 while the agent talks, else 0.5
self._in_speech = p >= (0.35 if self._in_speech else start)   # hysteresis
```
**Why an audio clock, not wall time?** Timing decisions ("700 ms of silence") are counted in frames of audio received. They don't depend on when the network delivered the bytes, so behaviour is the same in tests (which feed audio instantly) and live. It's **deterministic**.

**When no utterance is being captured** (`cap is None`):
- keep the last 300 ms of frames in `_preroll` (a `deque(maxlen=…)`), so when speech is confirmed the first syllables aren't lost;
- if the agent just started talking (< 0.3 s), ignore (likely its own echo);
- count consecutive speech time in `_speech_run_ms`; speech must last **200 ms** (`vad_start_ms`) to count as the caller starting a turn, or **350 ms** (`bargein_min_ms`) while the agent is talking;
- while the agent talks, also drop it if it is much quieter than this caller's own voice (`_quieter_than_caller()`: the median loudness of recent frames below 25% of the caller's median speech loudness);
- otherwise create a **`Capture`** (kind `"turn"`, or `"barge"` if the agent is talking) seeded with the preroll audio; cancel the no-input timer; if barge, **duck** the agent to 30% volume; emit `{"type":"speech_started"}`.

**While capturing** (`cap` exists): append the frame; if speech, reset `silence_ms` (and if speech resumed after a pause of 250 ms or more, bump `cap.version`, which invalidates any decision in progress, and remember the pause length to adapt the end-of-turn wait); if silence, add 32 ms to `silence_ms`. Then `_check_capture(cap)`:
- **speculative STT**: at **300 ms** of silence, if no transcription has been started for this version of the audio, spawn `_transcribe(audio)`;
- **end of turn**: at `_end_silence_ms()` of silence, normally **700 ms**, stretched to 1.3 × the caller's longest mid-sentence pause, capped at **1,500 ms**; or at **30 s** of speech (forced). Then spawn `_decide_turn(cap)`.

Everything in `feed`/`_process_frame` is synchronous on the event loop. That is deliberate: it is cheap (~1 ms per 32 ms) and keeps the frame order exact. Anything slow (STT, the brain, TTS) is spawned as a separate task.

## E5. `_decide_turn()`: was it a real, finished question?

```python
task = cap.stt[1] if cap.stt and cap.stt[0] == version else self._spawn(self._transcribe(...))
result = await task                       # usually already done: speculative STT started 400 ms earlier
if self._capture is not cap: return       # something else took over meanwhile
if cap.version != version: cap.deciding = False; return   # caller spoke again while we waited
```
Then, in order:
1. **Noise?** `is_noise(text, no_speech)`: empty, or Whisper's `no_speech_prob ≥ 0.6`, or a known hallucination ("Thank you.", "thanks for watching", "धन्यवाद"…) → ignore, keep listening.
2. **Unclear?** `unclear_speech()` needs **two of three** signals (low confidence `avg_logprob < -1`, repetitive text / high compression ratio, `no_speech ≥ 0.35`) → "Sorry, I didn't catch that"; twice in a row → offer the keypad or a person.
3. **Unfinished?** `incomplete(text)`: ends with "and", "aur", "कि", "matlab", a comma, "…", or is only filler → wait until 2.5 s of silence in total (`incomplete_wait_ms`). If the caller speaks again meanwhile, the version changes and this decision is dropped.
4. Otherwise `_start_reply(result, …)` with timings (`stt_ms`, `endpoint_ms`, `utterance_ms`).

## E6. `_reply()`: thinking and speaking at the same time

`_start_reply` cancels any reply still running (a correction made while the agent was thinking replaces the old answer) and spawns `_reply(question, …)`. Inside `_reply` two coroutines run **concurrently**, joined by an `asyncio.Queue`:

```python
sentences: asyncio.Queue = asyncio.Queue()

async def produce():                         # the brain
    async for event in self.brain.turn(question, self.session_id, extra_timings=timings):
        if event.kind == "sentence": await sentences.put(event)
        elif event.kind == "auth":   await self.send_json({"type": "auth", **event.auth})   # show the keypad
        ...
    finally: await sentences.put(None)       # "no more sentences"

async def speak():                           # the mouth
    while (event := await sentences.get()) is not None:
        filler.cancel()
        await self.send_json({"type": "agent_text", "text": event.text})
        async for pcm in self.tts.stream(event.speech, event.language):
            self.player.enqueue(pcm, on_start=mark_first_audio if first_chunk else None)

await asyncio.gather(produce(), speak())
await self.player.wait_done()
```
This is the **producer/consumer** pattern. While TTS speaks sentence 1, the LLM is still writing sentence 2. The caller hears the first sentence as soon as it exists, checked.

**The filler**: a separate task `_filler_after(1.2 s)`; if no audio is playing by then, it enqueues "One moment" (a different one of four lines each time). The first real sentence cancels it.

**TTS**: `EdgeTTS.stream()` gets MP3 chunks from Microsoft, feeds them to a `StreamingDecoder("mp3", 16000)` (FFmpeg via PyAV) and yields PCM16 as soon as it decodes. `SarvamTTS.stream()` asks for raw PCM (`linear16`) at 16 kHz and `pcm_stream()` copes with raw, WAV-headed or MP3 replies, keeping samples whole (a 16-bit sample is 2 bytes; a network chunk may end in the middle of one, so the odd byte is carried over).

At the end: `turn_metrics` (e.g. `voice_to_voice_ms` = first answer audio − end of the caller's speech) is sent and saved in the trace (`add_voice_timings`, in a thread); then either hold music (if a handoff started) or the **no-input timer** is armed.

## E7. `Player`: why audio is sent "just in time" (`voice/player.py`)

The player is one background task per call:
```python
while True:
    if not self._buf: wait for _wake event
    now = time.monotonic()
    if self._play_until < now: self._play_until = self.speaking_since = now    # new stretch of speech
    ahead = self._play_until - now
    if ahead > self.lead_s:                  # already 200 ms ahead of the listener?
        await asyncio.sleep(ahead - self.lead_s); continue
    frame = 640 bytes from _buf (20 ms)
    fire on_start callbacks whose byte position is reached ("first audio" timing)
    if self._gain != 1.0: frame = frame × gain   (NumPy)
    self._play_until += 0.020
    self._on_frame(frame)                    # recorder.agent(): placed on the caller's timeline
    await self._send(frame)                  # ws.send_bytes under the call's lock
```
TTS produces audio **faster than real time** (a 3-second sentence may arrive in 0.5 s). If we sent it all at once, the browser would hold 3 s of audio, and an interruption would have to wait for it or rely on the client to drop it. Sending only **200 ms ahead** (`playback_lead_ms`) means on interruption we stop sending and tell the client to drop at most 200 ms. The agent goes quiet within about 0.2 s.

`duck(0.3)` lowers the volume of the frames still to be sent; `clear()` drops everything not sent.

## E8. Interruptions (barge-in)

While the agent talks, a caller sound passes the three filters (0.65 score, not much quieter than the caller, not in the first 0.3 s) and lasts 350 ms → `Capture(kind="barge")`, `player.duck(0.3)`. The agent **keeps talking, quieter**. When the caller pauses 300 ms (or after 1.5 s of speech), `_decide_barge()` transcribes the overlap and `classify_barge()` says:
- `noise` (empty / no speech / hallucination), `echo` (≥ 70% of the words are words the agent is saying), `backchannel` (≤ 3 words, all like "haan", "ok", "hmm") → `duck(1.0)`, back to normal. **No gap** was ever heard;
- `interrupt` (any stop word: "stop", "ruko", "wait", "bas"…, or real words) → `_interrupt()`: cancel the reply task, `player.clear()`, send `{"type":"clear"}` (the browser drops its buffered audio), audit `interrupted`. If the caller only said "stop", stay quiet and listen; otherwise the capture becomes a normal turn and continues (if the agent hadn't started speaking yet, the new words are merged with the previous question: a correction).

Why the volume dip instead of pausing? Pausing to check left 1–3 s of silence mid-sentence for every cough. The dip never leaves a gap; a false alarm is barely noticeable (5 Nov finding).

**Cancellation flows through everything**: cancelling the reply task raises `CancelledError` at whatever it was awaiting: the LLM stream (`stream.aclose()` closes the HTTP stream), the TTS stream, the queue. `Conversation.turn()` catches `CancelledError`, saves an `interrupted` trace (so the audit shows what was asked and how far it got), and re-raises.

## E9. Verification during the call

Ravi asks "What's my balance?". The brain sees a personal request from an unverified session. `AuthService.start()` finds his number is a registered mobile, generates a 6-digit code with `secrets`, stores **only its HMAC** in `sessions.auth`, and (dev only) hands the code to the UI as a "simulated SMS". The turn yields an `auth` event: the session sends `{"type":"auth","status":"otp_sent",…}`, the UI shows a keypad. The agent says "I've sent a code…", and `_awaiting_code` makes the silence timer wait 30 s instead of 8 and remind about the code.

Ravi types the code. The browser sends `{"type":"otp","code":"123456"}`. `submit_code()`:
1. interrupts the agent if it is talking;
2. `await self.brain.verify(session_id, code)` → `AuthService` compares HMACs in constant time; right → `sessions.customer_id` and `verified_at` set; wrong → attempts left; 3 wrong → locked → transfer;
3. sends the `auth` result;
4. `_say_then_resume()` speaks "Thank you, you're verified" and **immediately queues the waiting question** ("What's my balance?") as a new reply, so the answer follows without a gap.

The balance answer comes from `personal.py` templates filled with bank data read through `HttpBank` with a customer-scoped token. The LLM never sees the balance (history replaces such answers with "(account details were given)").

## E10. Silence, hold, the desk

- **No input**: after the agent finishes, `_no_input()` sleeps 8 s (30 s while a code is awaited). Still nothing → reprompt ("Are you still there?"). Again → goodbye and `close("no_input")`.
- **Handoff**: if a turn ends with a transfer, `_start_hold()` switches `mode = "hold"` and `_hold()` loops every 0.25 s: keep at least 1 s of hold music queued (generated by `hold_music()`), say "Thank you for waiting" every 30 s; after `handoff_wait_s` (180 s) with nobody, book a callback, say so and end the call.
- **A person accepts** at `/desk` and opens `WebSocket /v1/desk/stream?handoff_id=…`. `desk_stream()` finds the call in `LIVE_SESSIONS` and calls `session.connect_agent(name, to_agent)`: `mode = "human"`, music stops, the client buffer is cleared. Now `feed()` forwards the caller's audio to the person's socket, and the person's audio arrives through `session.agent_audio()` into the same `Player`, so it is paced and recorded too. This only works when the person's desk connects to the **same process** that holds the call (PROJECT-GUIDE §26.12).

## E11. Hang-up and clean-up

The browser sends `stop` and closes, or the network drops (`websocket.disconnect`). The loop breaks; the `finally` blocks run in order:
1. `LIVE_SESSIONS.pop(session_id)`; `await session.close("client_closed")`: sets `closed`, cancels every task this call spawned (`_tasks`), stops the player task, tries to send `{"type":"end"}`;
2. a handoff still waiting becomes `abandoned`; an active one `completed`;
3. `open_ = False` (no more sends), `ws.close()`;
4. `audit.end_call()` in a thread (sets `sessions.ended_at`, `end_reason`, adds a `call_ended` event);
5. `asyncio.create_task(c.reviews.review(session_id))`: the post-call review starts **in the background**; nothing waits for it;
6. `recordings.save(...)` **in a thread**: `stereo()` → `encode_ogg()` (Opus via FFmpeg, C code that releases the GIL, so it can use another core) → AES-256-GCM encrypt → `files.put()` (disk or S3) → a `recordings` row;
7. outer `finally`: `_live_calls -= 1`, `LIVE_CALLS` gauge updated.

## E12. A timeline of one turn (typical development numbers)

| Time | What happens | Where |
|---|---|---|
| 0 ms | Caller's last word ends | browser → server, 20 ms frames continue (silence) |
| +300 ms | 300 ms of silence counted on the audio clock → speculative STT task starts (WAV upload to Groq) | event loop; Groq |
| +700 ms | End of turn decided; `_decide_turn` awaits the STT task (often already done) | event loop |
| +~800 ms | Brain starts: DB reads in threads (history, facts, pending, state) | thread pool, Postgres |
| +~800–1,700 ms | Router LLM call (~0.6–1 s) **in parallel with** GPU warm-up and the verified caller's bank prefetch | Groq; threads |
| +~1,750 ms | Search: embed (~60–90 ms GPU) ‖ keyword search; RRF; rerank 16 candidates | threads, GPU, Postgres |
| +~1,900 ms | Filler would play at 1.2 s after the turn started if nothing was ready (here it plays if the first sentence is late) | event loop |
| +~2,200 ms | First sentence streamed, split, checked (figure check, unsafe-output check) | Groq; event loop |
| +~3,200 ms | Edge TTS first audio (~1 s; Sarvam is faster) → player sends 20 ms frames | Microsoft; event loop |
| … | Sentence 2 checked and synthesised while sentence 1 plays | concurrently |
| end | Trace saved with hash, in a thread; no-input timer armed | thread pool |

Measured numbers are in PROJECT-GUIDE §19; the point here is which steps overlap: STT starts before the turn ends; the LLM, GPU warm-up and bank prefetch overlap; generation, checking, TTS and playback form a pipeline.

---

# Part F. Inside one turn: `Conversation.turn()` (`services/conversation.py`)

The same function serves typed chat (`/v1/ask`), voice notes (`/v1/voice/turn`) and live calls (`VoiceSession._reply`). One brain, many doors: a fix here fixes every channel.

## F1. Setup
```python
tid = str(uuid.uuid4()); trace_ctx.set(tid)                   # trace id, also attached to every log line
data = {"prompt_version": PROMPT_VERSION, "models": {...}}    # everything that will explain this turn later
bank_calls = []; calls_token = BANK_CALLS.set(bank_calls)     # every bank call of this turn is appended here
```

## F2. The order of decisions (each one can end the turn, and most need no LLM)

| # | Check | How | LLM? |
|---|---|---|---|
| 1 | **Keypad code**: a code step is active and the text is exactly a code | `is_code_entry(raw)` and `auth.awaiting_code(session)` → `_code_turn()`. Stored as `"[code]"`, never the digits. A code that came through **speech** is refused ("please use the keypad") | No |
| 2 | **Mask** personal data | `question = redact(raw)`: cards, Aadhaar, PAN, phones, long numbers, emails, UPI ids → `[card]`, `[phone]`…. The masked text is what the LLM, traces and history see; `raw` is kept only for the in-code checks below | No |
| 3 | Load memory | **One thread hop** for four reads: last 6 turns (`history`), call `facts`, the `pending` question, the `CallState` | No |
| 4 | **Pending yes/no** ("Shall I block card ending 6712?") | `journeys.resolve()` reads the reply with `confirm.read_reply()` → yes / no / question / unclear. Only a clear yes acts | No |
| 5 | **Registered mobile** said aloud when it was asked for | regex `_MOBILE` on the raw words (digits spoken as words are converted first) → `auth.submit_mobile()`. Read in code, never sent to the LLM | No |
| 6 | Courtesy ("thanks", "bye"), fragments ("so, you know"), "repeat that", bare "yes" after a clarify, "is my complaint registered?" | `smalltalk.py`, regexes | No |
| 7 | **Injection** ("ignore your instructions", "I'm the manager") | `guard.injection(raw)` → fixed reply | No |
| 8 | **Understand** | `self.understand()` → the router LLM, strict JSON. **In parallel**: `retriever.warm()` (wakes an idle GPU) and the verified customer's prefetch | **Yes (20b)** |
| 9 | **Code corrections** on the caller's own words | `asked_only(with_risk(own_data(card_failing(correct(u, raw)))))` | No |
| 10 | Save new facts | `traces.save_facts()` | No |
| 11 | Ask-back for "charges on my account?" (taken from it, or the general charges?) | `broad_charges()` | No |
| 12 | Guided flow in progress | `_flow()` | Depends |
| 13 | **Dispatch** each request (max 3) | `_process()` → `task_for(req)` (registry) → `_dispatch()` | Depends on handler |
| 14 | Save state, trace, alerts, log | `states.save`, `_record()` in a thread | No |
| 15 | Yield `done` | | |

Steps 1–7 exist because the LLM is **slow, costs requests, and can be talked into things**. Code handles what code can decide reliably.

## F3. The router (understanding) call
- Model `UNDERSTAND_MODEL` (gpt-oss-20b), `max_tokens=800`, temperature 0, `reasoning_effort: low`.
- Input built by `build_input()`: the last turns (answers trimmed to 300 characters; account data replaced), known facts, the pending question, the focus ("card 6712").
- Output (strict schema): `language`; `requests` (1–3) each with `kind` (general, personal, action, issue, human, new_customer, smalltalk, out_of_scope), a self-contained rewrite, `search_query_en`, personal fields (topic, detail, account, period, count, min_amount…), action fields (block/unblock/controls, card, last4…), `scope`, `named` products, `own_check`; `risk` flags; `reply_to_pending`; `facts`.
- Cost: about 3,000 prompt tokens per call, mostly the fixed instructions and the schema; history is small.
- **Fraud is never an LLM kind**: code makes a fraud request from a risk flag and puts it first (`with_risk`).

## F4. Dispatch: the registry decides, not the LLM (`registry.py`)
```python
def task_for(r: Request) -> Task:
    if kind == "general":      return Task("kb.answer", "kb", "open")
    if kind == "personal":     return Task(f"bank.{topic}", "bank_read", "verified")
    if kind == "action":
        if type in ("block","unblock","controls"): return Task(f"card.{type}", "action", "confirm")
        return Task("people.change", "handoff", "people")     # anything else that changes something → a person
    if kind == "issue":        return Task(f"issue.{issue}", "workflow", "verified")
    if kind == "fraud":        return Task("workflow.fraud", "workflow", "verified")
    if kind == "human":        return Task("handoff.person", "handoff", "open")
    ...
```
| Access | Meaning |
|---|---|
| `open` | Product information, anyone |
| `verified` | The caller's own data; RMN + keypad code first |
| `confirm` | Verified **and** an explicit "yes" to a one-line read-back before anything changes |
| `people` | The agent never does it (address/mobile change, refunds, limits, closing an FD, payments) |

`_dispatch()` then runs the handler: `kb` → `_answer()` (or `_guided()`), `bank_read` → `_personal()`, `action`/`workflow`/people → `_journey()`, `handoff` → `journeys.start_handoff()` (an explicit request for a person transfers straight away), else `_fixed()`.

`_process()` runs up to three requests **one after another**. A request that ends with a question to the caller ("which card?") **holds the rest**: they are saved in `CallState.open_requests` and carried on after the answer. Nothing is dropped.

## F5. The knowledge-base answer path (`_answer()`)

1. **Search** (`Retriever.search`): dense and keyword run **in parallel** (`asyncio.gather` of two tasks, each doing blocking work in `asyncio.to_thread`):
   - dense: embed `search_query_en` (bge-m3) → SQL:
     ```sql
     SELECT …, 1 - (e.embedding::vector(1024) <=> %s::vector(1024)) AS score
     FROM chunks c JOIN document_versions v … JOIN documents d … JOIN chunk_embeddings e ON e.chunk_id = c.id AND e.model = %s
     WHERE c.active ORDER BY e.embedding::vector(1024) <=> %s::vector(1024) LIMIT 30
     ```
     The cast and `model = …` match the partial HNSW index, so Postgres uses it.
   - keyword: the query's words OR-ed into a `tsquery`, matched against `chunks.tsv` (GIN index), ranked by `ts_rank_cd`, top 30.
   - `rrf_fuse()` → top 16 → `rerank()` (cross-encoder, in a thread).
2. **Refusal gate**: best rerank score below `MIN_RERANK_SCORE` (0.03) → "no approved information", no LLM call.
3. **Code decides which passages may be used** (`kbmeta.py`): authority and dates (rate card > schedule of charges > terms > FAQ > brochure; expired/superseded dropped), relevance trim (≥ 20% of the best and ≥ 0.05), product identity, foreign-currency tables only if asked, near-copies dropped, comparisons need every product. The **variant gate**: a generic question whose passages differ by variant → ask "which one do you have?", no LLM.
4. **Prompt**: `ANSWER_SYSTEM` (persona, short spoken sentences, only from sources, copy figures in digits, cite `[n]`, decisions ANSWER / CLARIFY / CONFLICT / NO_ANSWER) + numbered sources with their section path and date + relevant caller facts + the last exchange + the strategy line (overview/direct/full/compare/recommend) + the question.
5. **Stream and check, sentence by sentence**: `llm.stream()` yields fragments → the first characters decide the mode (an answer, or a `CLARIFY:` / `NO_ANSWER` / `CONFLICT` line) → `SentenceSplitter.feed()` releases complete sentences (keeps "7.10%", "p.a.", "[2]" with their sentence) → `clean_markup()` → **`_check_sentence()`**:
   - `unsafe_output()`: never ask for an OTP/PIN/CVV/password/card number; never give a web link not in the sources;
   - citation markers `[n]` collected;
   - **figure check**: every number in the sentence must be in the cited sources or the question (`allowed_numbers` / `unsupported_numbers`, treating 1,00,000 = 1 lakh = 100000, months ↔ years);
   - `spelled_figures()`: no figures written as words (they can't be checked).
   - Pass → `yield TurnEvent("sentence", …)` immediately (voice starts speaking it). Fail → stop.
6. **Endings**: no citation at all, or a failure before anything was said → a refusal ("I don't have approved information… I can connect you to a specialist", and a transfer offer is recorded as the pending question). A failure after some sentences → keep what was said and add "I'd rather not guess the rest". A safety cap on length.
7. `finish("answered", …, citations=[…])`.

## F6. The bank-data path (`_personal()`, `personal.py`)
- Not verified → start verification (F2/E9) and remember the question.
- Verified → `auth.verified_customer()` (sliding 10-minute idle window; cached 8 s) → `HttpBank.customer(cif)`: about 15 small HTTP calls to the mock bank systems, **in parallel on 8 threads, in two rounds**, each with a customer-scoped token, 3 s timeout, one retry.
- `personal.answer(customer, query)` picks a function per topic and fills **fixed templates** in en/hi/hinglish. Only last-4 digits are ever said. No LLM writes these sentences: the numbers cannot be invented.

## F7. Actions (`journeys.py`, `actions.py`)
"Block my debit card" → `plan()` picks the card (or asks which), checks it's allowed, returns a one-line read-back ("Just to confirm, I'll temporarily block the debit card ending 6712. Shall I go ahead?") and sets the **pending** question. The next turn: `read_reply()` → yes → `execute()`: one bank write with an **`Idempotency-Key`**; on a lost reply it retries with the **same key** (the bank returns the original result, so nothing happens twice); read the state back; only then "Done, reference …". If it still can't be confirmed → `BankUnconfirmed` → "I can't confirm whether it went through", never "done" and never "failed".

## F8. Recording the turn (`_record()`, in a thread, after the last sentence)
- `TraceStore.save(trace)`: in a transaction, `SELECT chain_hash FROM sessions WHERE id = %s FOR UPDATE` (row lock), compute `turn_hash(prev, trace, created)` = SHA-256 of the previous hash + a canonical JSON of the turn, insert the `traces` row with `prev_hash` and `hash`, update `sessions.chain_hash` and `chain_len`.
- `log_turn()` writes a masked transcript log line (field names, never amounts).
- `audit.after_turn()`: alerts (a request for a person → `wants_human`; 2 refusals/errors or 3 weakly grounded replies → `struggling`; 2 system errors in 5 minutes → `system_health`).
- `grounding()` labels the turn high/medium/low/none/n/a for reviewers.

## F9. Errors
Any exception → a plain "I'm having trouble right now… I can connect you to our team" (`unavailable` template), outcome `error`, and a transfer offer recorded as the pending question so "yes" transfers in code (no LLM needed, which matters when the LLM is the thing that failed). LLM retries are capped at 4 s in total so the caller never waits long in silence.

---

# Part G. Documents: upload, the job queue, chunks, vectors

## G1. Upload (`POST /v1/documents` → `DocumentService.upload`), instant
1. `_check_size` (≤ 100 MB).
2. `detect_type`: the extension picks the parser, and the **first bytes must agree** (`%PDF-` for PDF; `PK\x03\x04` for DOCX/XLSX because Office files are ZIP archives; valid UTF-8 without NUL bytes for text). A renamed binary is rejected (415).
3. `sha256(data)`: the same file already present → returns the existing document (`deduplicated: true`).
4. In one transaction: insert `documents` and `document_versions` (status `queued`), store the original in the file store, insert a `jobs` row.

## G2. The worker (`IngestWorker`, a thread)
```python
with self.db.connection() as conn, conn.transaction():
    job = conn.execute("SELECT id, version_id, attempts FROM jobs WHERE status = 'queued' "
                       "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
    if not job: return False
    conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, started_at = now() WHERE id = %s", ...)
self.documents.process_version(version_id)
```
- **Postgres as a job queue**: oldest first (FIFO). `SKIP LOCKED` lets several workers (in several brain copies) each take a different job, never the same one, with no extra queue software.
- Failure: retried once (`MAX_ATTEMPTS = 2`) unless it's a `ValueError` (bad file: retrying won't help); else `failed`.
- At start, jobs left `running` by a crash go back to `queued`.
- Polls every `worker_poll_s` (1 s) when idle; `_stop.wait(poll_s)` sleeps but wakes immediately on shutdown.

## G3. `process_version()`
1. Mark the version `processing`.
2. Parse (`ParserRouter` → DOCX/PDF/XLSX/text) into **blocks** (heading, para, list, table).
3. Chunk (`chunk_blocks`): sections become passages of ~180 words (max 260) with a **section path** ("CASA+TD KB › AU Royale Savings Account › Charges"); tables row by row with their headers; bank rate sheets with stacked headers read top-down; duplicates dropped.
4. Flag instruction-like text (`guard.instruction_like`).
5. Embed every passage (`embed_text = section_path + "\n" + text`) in batches of 16.
6. **The swap, in one transaction**: delete old chunks of this version (re-index), insert chunks and their vectors, lock the document row, mark the previous current version `superseded`, mark this one `indexed`, set `current_version_id`, refresh every chunk's `active` flag. Search only reads `WHERE c.active`, so it never sees a half-indexed document: before the commit it sees the old version, after it the new one.

---


# Part H. The database in depth: migrations and every table

## H1. Why persist anything at all?
| Reason | Example |
|---|---|
| **Survive restarts and crashes** | A brain restart must not lose the KB, call records, tickets or the audit trail |
| **Share between copies** | With several brain processes (production), any copy must see the same documents, calls and handoffs |
| **Audit and compliance** | A bank must show later what was said, why, from which source, and that the record wasn't edited |
| **Keep the LLM stateless** | The model remembers nothing; the call's memory (history, facts, state, pending question) is read from Postgres every turn |
| **Search** | Vectors and full-text indexes live next to the passages, in one store, updated in one transaction |

What is deliberately **not** in the database: the live audio stream and its VAD state (memory, per call), `LIVE_SESSIONS`, the live-call counter, the rate limiter's counters, the OTP secret (memory, per process), recordings' audio (files, encrypted; only metadata in the DB), model weights (files).

## H2. The migrations, one by one

| File | Adds | Why |
|---|---|---|
| `001_init.sql` | `vector` extension; `documents`, `document_versions`, `chunks` (with generated `tsv` + GIN index, `active` flag), `chunk_embeddings` (vector per chunk **per model**), `jobs`, `sessions`, `traces`, `eval_runs` | The knowledge base, the ingest queue, sessions and one trace per turn |
| `002_auth.sql` | `sessions.caller_number`, `customer_id`, `verified_at`, `auth` (jsonb); `auth_events` | Verification state on the server; every verification step audited (masked numbers only) |
| `003_call_facts.sql` | `sessions.facts` (jsonb) | Facts that must last the whole call ("I'm a senior citizen") |
| `004_audit.sql` | `sessions.ended_at`, `end_reason`, `chain_hash`, `chain_len`; `traces.prev_hash`, `hash`; `call_events`, `recordings`, `alerts` | Tamper evidence, call timeline, recordings metadata, alerts |
| `005_corebank.sql` | Schema `corebank`: `branches`, `customers`, `accounts`, `account_txns`, `account_charges`, `debit_cards`, `credit_cards`, `card_txns`, `fixed_deposits`, `loans` (+ EMIs, rate changes); app tables `tickets`, `handoffs`, `handoff_messages` | The mock bank's data; complaints and transfers to people |
| `006_call_state.sql` | `sessions.state` (jsonb) | The per-call `CallState` |
| `007_mockbank.sql` | `corebank.idempotency` | Write retries return the original result |
| `008_doc_tags.sql` | `documents.tags` (jsonb) | Document-level tags (type, authority, dates) a person can correct |
| `009_call_reviews.sql` | `call_reviews` | Post-call review results |
| `010_fix_items.sql` | `fix_items` | The fix list from reviews; kept on a clean slate (link cleared), deleted with its call when one call is deleted |
| `011_emi_paid_on.sql` | `corebank.loan_emis.paid_on` | The real debit date of an EMI, never confused with the due date |

## H3. The important design choices in the schema
- **Document vs version vs chunk**: a document ("CASA+TD KB") has versions (each upload); each version has chunks. Rolling back = make an older version current; nothing is re-uploaded. Old versions stay on record.
- **`chunks.active`**: one boolean the search filters on. It is recomputed in the same transaction as a version switch, enable/disable or delete. That's why searches never see half-done states.
- **Embeddings per model** (`PRIMARY KEY (chunk_id, model)`, column type `vector` without a fixed size): a new embedding model can be filled in next to the old one, and the HNSW index is **partial per model** with an explicit cast to `vector(1024)`.
- **`jobs`** is a queue table with a partial index on queued rows.
- **`traces.data` (jsonb)** holds everything that explains an answer: understanding, passages and scores, bank calls, checks, timings, models, prompt version, grounding. Fixed columns hold what is filtered on (session, outcome, created_at).
- **`sessions`** is the call: language, caller number, verification (`customer_id`, `verified_at`, `auth`), `facts`, `state`, `pending`, chain head, end.
- Deletion rules: chunks and embeddings cascade with their version; the foreign keys from `traces` and `fix_items` are `SET NULL`, but `delete_call` deletes a call's turns and fix items explicitly; a clean slate keeps the fix items (Part O5).

## H4. How the code talks to the database
- Synchronous **psycopg 3** with `dict_row` (rows come back as dicts) and **parameters always passed separately** (`conn.execute("… WHERE id = %s", (session_id,))`). The driver sends values apart from the SQL text, so a caller's words can never become SQL (**no SQL injection**). The only f-strings in SQL insert fixed values from code (column lists, the vector dimension, the model name from settings).
- `register_vector` (from the `pgvector` package) teaches psycopg the `vector` type.
- From async code: `await asyncio.to_thread(...)`; from threads (worker, plain `def` routes): directly.

---

# Part I. Every cache in the system

A **cache** keeps the result of slow work so the next time is fast. Each one here, what it saves, and how it stays correct:

| Cache | Where | What it saves | How it stays correct / expires |
|---|---|---|---|
| **Phrase cache (memory)** | `PhraseCache._cache` in `voice/phrases.py` | Synthesised audio of fixed lines (greeting, fillers, reprompts, goodbye, hold, unclear…) per language and variant | Text never changes at run time; `clear()` after a pronunciation change. An `asyncio.Lock` with a double check stops two calls synthesising the same phrase at once |
| **Phrase cache (disk)** | `data/tts-cache/*.pcm` | The same audio for **paid** voices (Sarvam), so a restart doesn't pay again (~Rs 5–6 per full set) | File name = SHA-256 of voice tag + language + exact text, so any change of voice, pace or text is a new file; not written if the fallback voice produced it |
| **Phrase warm-up** | `lifespan` → `phrases.warm()` | Pre-fills the phrase cache at startup | The greeting plays instantly on the first call |
| **HTTP connection pools (keep-alive)** | every `httpx` client (LLM, STT, TTS, search, bank) | Open TLS connections: saves 100–250 ms per request to Groq/Sarvam | `keepalive_expiry=300`; `warm()` at call start opens them |
| **Model warm-up** | `build_search()`; `Retriever.warm()` | First run of a model is slow; an idle GPU clocks down (~1 s to wake) | Done at startup; `warm()` runs while the LLM is understanding the turn, only if idle > 8 s |
| **Verified-customer prefetch** | `AuthService._customers` | One bank read of the customer, shared by the calls of one turn | **8 s TTL**; `invalidate()` after a card change so the read-back sees the new state |
| **LLM replay cache (evals only)** | `ReplayLLM`, `evals/cache/` | Real LLM responses keyed by SHA-256 of the exact request | Any change in model, prompt, input, schema or token limit = a different key = a real call. Never used in the live agent |
| **`get_settings()`** | `@lru_cache` in `config.py` | The parsed `Settings` | Read once per process; restart to change |
| **Hold music** | `@cache` on `hold_music()` | The generated PCM | Pure function of its arguments |
| **Postgres's own caches** | shared buffers, OS page cache | Hot table and index pages in memory | Managed by Postgres |
| **Docker build layers / uv cache** | `Dockerfile` | Dependencies and models aren't rebuilt when only code changes | Layers ordered from least to most often changed |

**What is NOT cached, on purpose**: answers. Every question is searched and answered fresh, because documents and the caller's context change, and a cached answer would skip the checks. Bank data is not cached beyond one turn (8 s), because balances change.

**Redis?** Not used (ADR 0021). One process holds a call from start to end, so nothing per call needs sharing. Redis would only matter for state shared between copies (e.g. a rate limiter), which is planned at the load balancer instead.

---

# Part J. Concurrency: the complete picture

## J1. The one-paragraph answer
One brain process runs **one asyncio event loop on its main thread**. **Every live call is a few asyncio tasks** on that loop (its WebSocket loop, its player, its reply, its timers), not a thread or a process. The loop switches between tasks whenever one waits (network, timer, a database call in a thread). Blocking work (database, local models, Bedrock, encoding) runs in **thread pools** so the loop stays free. A process takes at most **9** live calls (`MAX_LIVE_CALLS`); the 10th is refused at once with close code 1013. To serve more calls you run **more processes** (containers), and a load balancer spreads new calls across them.

## J2. "If several calls come in, how does it choose which one to handle? First come first served? Round robin?"
There is **no scheduler that picks calls**. Here is exactly what happens, layer by layer:

| Layer | What decides the order | Policy |
|---|---|---|
| **Load balancer** (production, AWS ALB) | Which container gets a **new** call | Round robin by default (or least outstanding requests if configured). After that, the call stays on that container for its whole life (one WebSocket = one TCP connection) |
| **Operating system** | New TCP connections wait in the listening socket's **accept queue** (uvicorn's backlog: 2048) | First in, first out |
| **uvicorn** | Accepts connections from the queue and starts a task per connection | In arrival order |
| **Our admission control** (`stream()`) | Takes the call if fewer than 9 are live, else refuses (1013) | **First come, first served** up to the limit; no waiting queue (a caller can't wait in silence) |
| **asyncio event loop** | Which **ready** task runs next | Tasks that become ready (bytes arrived, a timer fired, a thread finished) are put on the loop's ready queue and run **in the order they became ready** (FIFO). Each runs until its next `await`. This is **cooperative multitasking**: no task is forcibly paused; each one yields at `await` |
| **Thread pools** | Which queued blocking job runs next | FIFO queue in `ThreadPoolExecutor`; up to `min(32, CPUs+4)` at once |
| **Database pool** | Which thread gets a connection | Waiters are served in order; up to 10 connections |
| **Job queue** (documents) | Which document is indexed next | Oldest first (`ORDER BY created_at … SKIP LOCKED`) |

So: **first-come-first-served admission, then fair interleaving**. In each 32 ms of audio, the loop processes call 1's frame, call 2's frame, and so on, in whatever order their bytes arrived. With 9 calls that's about 9 × 0.9 ms ≈ 8 ms of VAD work per 32 ms on the laptop. Nobody waits long because every step is short or is an `await`.

## J3. Why no call ever hears another call's audio
Every piece of call data is owned by that call's objects:
- the bytes arrive on that call's **socket**; the OS wakes the task that is awaiting **that** socket;
- `feed()` is a method of **that call's** `VoiceSession`, with its own buffer, its own VAD model, its own `Capture`;
- outgoing audio goes through **that call's** `Player` to **that call's** `ws`;
- each request to Groq/Sarvam is `await`ed by the task that sent it, so the reply resumes that task;
- every database row carries the `session_id`;
- shared things (HTTP connection pools, the DB pool, the models) hold **no call data between uses**.

## J4. Every lock, and what it protects

| Lock | Type | Protects |
|---|---|---|
| `lock` in `stream()` / `desk_stream()` | `asyncio.Lock` per WebSocket | Two tasks sending on one socket at the same time (an audio frame and a JSON event) |
| `PhraseCache._lock` | `asyncio.Lock` | Two calls synthesising the same phrase twice |
| `TransformerEmbedder._lock`, `CrossEncoderReranker._lock` | `threading.Lock` | One model used by several threads at once (GPU memory, model state). Embedder locks per batch so a live query isn't stuck behind a whole document |
| `SELECT … FOR UPDATE` on `sessions` in `TraceStore.save` | Postgres row lock | Two turns of one session computing their hash from the same previous hash |
| `FOR UPDATE` on `documents` in `process_version` | Row lock | Two versions of one document being made current at once |
| `FOR UPDATE SKIP LOCKED` on `jobs` | Row lock | Two workers taking the same job |
| `pg_advisory_lock(STARTUP_LOCK)` | Advisory lock | Two copies migrating / creating the index / seeding at once |
| `corebank.idempotency` | Primary key on the key | A retried bank write happening twice |
| (none) `_live_calls` | – | Safe without a lock: single thread, no `await` between check and increment |

## J5. Where each kind of work runs

| Work | Runs on | Why there |
|---|---|---|
| Receiving/sending WebSocket frames, `feed()`, VAD per frame, turn-taking rules, the player loop, timers | **Event loop** (main thread) | Small, frequent, order-sensitive |
| HTTP calls to Groq (LLM, Whisper), Sarvam, Edge | **Event loop**, `await`ed (httpx async) | Pure waiting: no thread needed |
| Every Postgres query | **Default thread pool** via `asyncio.to_thread` | psycopg is used in blocking mode |
| Embedding and reranking (local mode) | Thread pool, GPU | Blocking C/CUDA work, releases the GIL |
| Bank reads (`HttpBank`) | Its own 8-thread pool | Blocking `httpx.Client`; ~15 calls in parallel |
| Bedrock (production LLM) | `to_thread` for JSON; one thread per streamed answer | boto3 is blocking |
| Audit events during a call | Thread pool (`run_in_executor`) | Never delay audio for a DB write |
| Recording encode + encrypt | Thread pool | Seconds of C work; uses another core |
| Post-call review | A task on the loop (its LLM calls awaited, its DB calls in threads) | Runs after the call, adds no latency |
| Document ingestion | `ingest-worker` thread | Long work, independent of calls |
| Mock bank systems | `mockbank` thread with its **own** event loop | A separate server, like a real bank, inside the process |
| FastAPI plain `def` routes | AnyIO thread pool | Blocking DB reads without blocking the loop |

## J6. The GIL and the "one core" limit
- One process's **Python** code runs on about one core at a time (the GIL), and our call work is Python on one event loop. Measured: 40 silent calls held the process at 99% of one core while the laptop had 16 logical CPUs. Right after, encoding 40 recordings in C code used 156% (more than one core), because C code releases the GIL.
- Hence the rule: about 60% of one core per process for calls → **9 real calls per process** on an AWS vCPU (cost sheet), with headroom for turn bursts.
- More calls = **more processes** (containers), not more threads.
- Torch is kept at **1 thread** (the `silero_vad` import sets it): measured 0.88 ms of CPU per VAD frame with 1 thread vs 3.7 ms with 4, for the same result.

## J7. Backpressure and limits (what stops overload)
| Limit | Value | Where |
|---|---|---|
| Live calls per process | 9 (`MAX_LIVE_CALLS`), refused with 1013 | `api/voice.py` |
| Requests per client per minute (`/v1/ask`, `/v1/voice*`, `/v1/auth`…) | 120, sliding window in memory | `RateLimiter` in `main.py` |
| DB connections | 10 | `db.py` |
| Bank calls at once | 8 threads | `http_bank.py` |
| Groq keep-alive connections | 10 | `adapters/llm.py` |
| LLM retry waiting | 4 s total | `llm_retry_budget_s` |
| Bank call timeout | 3 s, one retry | `bank_timeout_s` |
| Utterance length | 30 s, then forced | `max_utterance_s` |
| Recording kept in memory | 1 hour | `MAX_CALL_S` |
| Upload size | 100 MB (documents), 10 MB (voice files) | `documents.py`, `api/voice.py` |
| Agent audio ahead of playback | 200 ms | `playback_lead_ms` |

## J8. Async generators and cancellation, the subtle part
`Conversation.turn()` is an async generator consumed by `produce()` in the voice reply. When the caller interrupts, `_interrupt()` cancels the reply task. Python raises `CancelledError` inside whatever `await` the task is at: maybe `queue.get()` in `speak()`, maybe inside `turn()` waiting for the LLM stream. `asyncio.gather(produce(), speak())` cancels both children. In `turn()`, the `except (asyncio.CancelledError, GeneratorExit)` block saves an `interrupted` trace and re-raises. In `_answer()`, `finally: await stream.aclose()` closes the HTTP stream to the LLM so Groq stops generating. `ContextVar.reset(token)` can fail when a generator is closed from another context; the code catches `ValueError` for that.

## J9. Several copies of the brain (production)
- A call never moves between copies (one WebSocket). So per-call memory is safe with any number of copies.
- Shared through Postgres: KB, calls, turns, audit, tickets, handoffs, reviews, jobs. Shared through S3: uploads, recordings.
- Still per copy (to fix before a second copy, `context/pending-work.md` B2.3): the OTP HMAC secret (a typed code in **text chat** could land on another copy), the rate limiter (move to the WAF/load balancer), after-call work (move to the `jobs` table).

---

# Part K. Security, privacy, audit, monitoring, tests, deployment

## K1. Security and privacy, as mechanisms in code
| Rule | Mechanism | File |
|---|---|---|
| OTP/PIN never through STT, the LLM or logs | Keypad-only path (`{"type":"otp"}` / `/v1/auth/verify`); a code heard by STT is refused; the turn is stored as `[code]`; only the HMAC is stored | `voice/session.py`, `conversation._code_turn`, `services/auth.py` |
| Identity decided in code | Server-side state machine in `sessions.auth`; customer id comes from the verified session, never from the LLM | `auth.py` |
| Bank access per customer | Customer-scoped HMAC tokens (cif, systems, scopes, 15 min); every bank system checks them and that the data belongs to that cif | `tokens.py`, `mockbank/core.py` |
| Permissions in code | Registry access levels; confirm-before-act; people-only tasks | `registry.py`, `journeys.py` |
| No invented figures | Figure check per sentence; bank answers from templates | `numbers.py`, `personal.py` |
| PII masked | `redact()` before the LLM, traces, history and every log line | `privacy.py`, `logs.py` |
| Prompt injection | Regex screen before the LLM; "treat sources as data" in the prompt; instruction-like documents flagged at ingest and dropped at answer time | `guard.py`, `kbmeta.py` |
| Never ask for secrets | `unsafe_output()` blocks a sentence that asks for an OTP/PIN/CVV/password/card number or gives an unknown link | `guard.py` |
| Secrets never printed | `SecretStr` settings; logs mask | `config.py` |
| Records can't be silently edited | Hash chain on turns; `verify_chain()` | `audit.py`, `store.py` |
| Recordings protected | AES-256-GCM with the recording id as associated data; SHA-256 check before decrypt; retention policy | `recordings.py` |
| Uploads checked | Type from the first bytes; size limit; dedupe by SHA-256 | `documents.py` |
| Abuse limits | API key option, rate limit, live-call limit | `deps.py`, `main.py`, `api/voice.py` |

## K2. Monitoring
- **Logs**: one readable line per event on the console, JSON lines in `logs/app.jsonl`, all masked, with `request_id` / `trace_id` attached from ContextVars.
- **Metrics** (`/metrics`, Prometheus format): requests and latency per route, live calls, refused calls, turns by outcome/language, latency per pipeline stage.
- **Traces**: one row per turn with everything that explains it (`/v1/traces/{id}`).
- **Call audit** (`/calls`): timeline events, alerts, recordings, transcript, grounding, "why" lines, the review.
- **Post-call review** (`review.py`): free code checks on every call; an LLM judge (a different model, its own quota) scoring five dimensions with quoted evidence (quotes not found in the transcript are dropped); findings become the fix list (`/fixes`).

## K3. Tests and evaluations (tiered, to protect the 1,000-requests-a-day Groq budget)
| Tier | What | Command |
|---|---|---|
| Offline tests | 395 tests pass (tracker, 17 Nov; about 260 test functions, some run with several inputs): the full app with **fakes** (`FakeLLM` keyword rules, `FakeEmbedder` hashed bag-of-words, `FakeReranker` word overlap, `FakeSTT` scripted, `FakeTTS`, `FakeVAD`) and a throwaway Postgres. No network | `uv run pytest` (about 1.5 min) |
| Smoke set | 24 single questions, real LLM, **replay-cached** (a re-run with nothing changed makes 0 requests), paced under 8k tokens/min | `uv run python -m evals.smoke` |
| Regression pack | 33 whole conversations | `uv run python -m evals.smoke --pack regression --keep` |
| Golden set | 105 product questions with expected facts and sources | `uv run python -m evals.golden_run` (rarely: about an hour on a cold cache) |

How the fakes plug in: `tests/conftest.py` calls `build(settings, overrides={"embedder": FakeEmbedder(), "llm": FakeLLM(), "voice": VoiceRuntime(FakeSTT(...), ...)})` and `create_app(container)`. Because services depend on ports, nothing else changes. `pytest-asyncio` with `asyncio_mode = "auto"` runs `async def` tests.

## K4. Deployment
- **Brain image** (`brain/Dockerfile`): multi-stage. Stage 1 (`build`) installs exact packages from `uv.lock`, downloads the models (optional, `SEARCH_MODELS=0` to skip), copies code. Stage 2 (`runtime`) copies the result, creates a non-root user (uid 10001), makes code and models read-only, sets `HF_HUB_OFFLINE=1`, `LOG_CONSOLE=json`, and runs `python -m app serve --host 0.0.0.0 --port 8000`. One image, three uses: brain with search inside (GPU), the search server (`python -m app search`), brain without models (`SEARCH_PROVIDER=http`).
- **UI image**: static files from `next build` (`output: "export"`) served by nginx.
- **AWS plan** (proposed): callers → AU's Cisco → (media gateway) → ALB → ECS containers in Mumbai, each one brain process with ≤ 9 calls; Aurora/RDS Postgres with pgvector; S3 for files and recordings; a GPU search server; Sarvam for speech; gpt-oss-120b on Bedrock (India-only profile). Health: `/health` (process up), `/health/ready` (database reachable). Details: `docs/deployment-options-and-costs.md`, `deploy/README.md`, PROJECT-GUIDE §26.
- **Switches** that change the deployment without code changes: `LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`, `SEARCH_PROVIDER`, `BANK_PROVIDER`/`BANK_BASE_URL`, `DATABASE_URL`, `FILE_STORE`.

---

# Part L. Software engineering patterns used, and where

| Pattern / practice | What it means | Where |
|---|---|---|
| **Ports and adapters** (hexagonal architecture) | Core logic depends on interfaces; outside systems plug in through adapters | `ports.py` + `adapters/` + `voice/providers.py` (ADR 0009) |
| **Composition root** | Objects are wired in one place only | `container.build()` |
| **Dependency injection** | Objects receive their collaborators instead of creating them | Every service `__init__`; FastAPI `Depends`; test `overrides` |
| **Decorator (wrapper)** | An object wraps another with the same interface and adds behaviour | `ResilientLLM`, `ReplayLLM`, `FallbackSTT`/`FallbackTTS` |
| **Circuit breaker** | After repeated failures, stop calling the failing thing for a while | `ResilientLLM` (3 in 60 s → 60 s on the fallback) |
| **Retry with backoff + budget** | Retry transient errors, waiting longer each time, within a time cap | `OpenAICompatibleLLM._post`, speech providers, `HttpBank._request` |
| **Idempotency keys** | Safe retries of writes | `HttpBank.update_card`, `MockBank.idempotent` |
| **Strategy** | Choose an algorithm at run time | `pick_strategy()` (overview/direct/full/compare/guided), provider selection |
| **Table-driven / registry** | Behaviour from a table, not if-chains spread around | `registry.task_for`, `DEPARTMENTS`, `TEMPLATES`, `PHRASES`, status maps in `http_bank.py` |
| **State machine** | Explicit states and transitions | Verification (`auth.py`), the live call's `mode` (ai/hold/human), job statuses, document version statuses |
| **Producer/consumer** | Two workers joined by a queue | `_reply()`'s `produce`/`speak` with `asyncio.Queue`; the `jobs` table with the worker |
| **Pipeline / streaming** | Each stage starts on partial results of the previous | LLM tokens → sentences → checks → TTS → player |
| **Speculative execution** | Start work that will probably be needed | STT at 300 ms of silence; GPU warm-up and bank prefetch during the router call |
| **Admission control / load shedding** | Refuse new work when full rather than slow everyone | `MAX_LIVE_CALLS` |
| **Atomic swap** | Prepare the new state on the side, make it visible in one transaction | `process_version()` |
| **Optimistic invalidation with versions** | A counter that makes stale async results ignorable | `Capture.version` |
| **Defence in depth** | Several independent layers for one risk | PII (regex + checksums + log masking), injection (regex + prompt + output filter), figures (prompt + check) |
| **Fail safe** | On doubt, do the safe thing | Unknown status word → "can't confirm"; unconfirmed write → never "done"; low relevance → refuse; LLM down → offer a person |
| **"LLM understands, code decides"** | The model fills a form; code applies rules | `understanding.py` + `registry.py` + checks (ADR 0008) |
| **Stateless model calls** | The LLM keeps nothing; state is ours | ADR 0026 |
| **Pure functions for rules** | No I/O, easy to test | `turntaking.py`, `confirm.py`, `numbers.py`, `registry.py`, `dialogue.py` |
| **Test doubles (fakes)** | Deterministic stand-ins | `tests/fakes.py` |
| **Record/replay** | Save real responses for repeatable runs | `ReplayLLM` |
| **Configuration from the environment** (12-factor) | Same image everywhere; settings from env | `Settings` |
| **Schema migrations** | Versioned, ordered, once-only DB changes | `db._migrate` + `migrations/` |
| **ADRs** | Write down why | `adr/` |
| **Observability by design** | Every turn explainable afterwards | traces, audit, metrics, logs |
| **Minimal dependencies** | Few, well-known libraries; stdlib first | own DOCX reader, own tokens, own breaker, own rate limiter, no Redis/Kafka/LangChain |

---


# Part O. Technical architecture: where everything lives

This part answers "where is X saved, in what form, by which code, for how long". Read it with Parts H (database) and I (caches).

## O1. The architecture in two pictures

**Development (the owner's laptop): one brain process plus Postgres, plus the UI**
```
 Browser (localhost:3000, Next.js dev server via `bun run dev`)
   │  HTTP  /v1/...            (typed turns, voice notes, KB, calls, desk)
   │  WS    /v1/voice/stream   (live call: JSON + PCM16 both ways)
   │  WS    /v1/desk/stream    (a person joining a live call)
   ▼
 ┌──────────────────────── brain process: python app.py (uvicorn, 127.0.0.1:8000) ────────────────────────┐
 │ main thread: event loop ── FastAPI routes ── VoiceSession per call ── Conversation.turn() per turn        │
 │ threads: default pool (DB, search, audit, recordings) · AnyIO pool (plain def routes) · bank pool (8)     │
 │          · ingest-worker · mockbank (own loop + uvicorn on 127.0.0.1:<random>) · torch/CUDA natives      │
 │ in memory: models (bge-m3, reranker on GPU; Silero per call), phrase cache, LIVE_SESSIONS, counters      │
 └───────┬───────────────────────┬─────────────────────────────┬───────────────────────────────┬──────────┘
         │ psycopg (pool 2–10)   │ HTTPS (httpx, keep-alive)   │ HTTP (httpx, 127.0.0.1)        │ files
         ▼                       ▼                             ▼                               ▼
   Postgres 16 + pgvector   Groq: gpt-oss-20b/120b,      mock bank systems             brain/data/files, recordings,
   (pgserver, brain/data/pg) Whisper · Microsoft Edge TTS (in the same process)        tts-cache · brain/logs
```

**Production (proposed, PROJECT-GUIDE §26): many brain containers, shared state outside them**
```
 Caller ─► AU's Cisco ─► (media gateway: SIP/RTP → one WebSocket per call) ─► ALB (picks a container per new call)
                                                                              │
              ┌───────────────────────────────┬───────────────────────────────┤
              ▼                               ▼                               ▼
        brain container 1               brain container 2  …            brain container N   (1 process each, ≤ 9 calls)
              │  shared by all copies:
              ├─► Aurora / RDS Postgres + pgvector (KB, calls, audit, jobs)    ├─► S3 (uploads, encrypted recordings)
              ├─► GPU search server (embed + rerank, SEARCH_PROVIDER=http)      ├─► Sarvam (STT, TTS)
              ├─► Bedrock Mumbai (gpt-oss, India-only profile)                 └─► AU's bank APIs (BANK_BASE_URL)
 UI: static files (next build) on nginx / S3 + CloudFront, for testing and the desk
```

## O2. The storage map: every place data is kept

| What | Where (development) | Where (production plan) | Format | Written by | Kept for |
|---|---|---|---|---|---|
| Knowledge base: documents, versions, passages, vectors | Postgres in `brain/data/pg` | Aurora/RDS | Tables (Part H) | `DocumentService`, `IngestWorker` | Until deleted; old versions kept |
| Uploaded originals | `brain/data/files/<doc_id>/<version_id>/<filename>` | S3 `<S3_PREFIX>` + same key (default prefix `brain/`), server-side encrypted | The original bytes | `DocumentService._add_version` → `FileStore.put` | Until the document is purged |
| Calls, turns, memory, verification state | Postgres (`sessions`, `traces`, …) | Aurora/RDS | Rows; JSONB for flexible parts | `TraceStore`, `AuthService`, `StateStore` | Kept (no automatic deletion); delete a call or clean slate removes them |
| Call timeline, alerts, verification events | Postgres (`call_events`, `alerts`, `auth_events`) | Aurora/RDS | Rows | `AuditStore`, `AuthService` | As above |
| Tickets, handoffs, desk chat | Postgres (`tickets`, `handoffs`, `handoff_messages`) | Bank CRM / Cisco in production (same ports) | Rows | `HandoffService`, `LocalServiceDesk` / `HttpServiceDesk` | As above |
| Reviews and the fix list | Postgres (`call_reviews`, `fix_items`) | Aurora/RDS | Rows | `ReviewService` | Fix items survive a clean slate (link cleared); deleting one call deletes its fix items |
| Mock bank data | Postgres schema `corebank` | Not used (AU's real systems) | Tables | `mock_corebank.ensure_seeded` | Re-seeded when the day changes and on clean slate |
| Call recordings | `brain/data/recordings/<recording_id>.ogg.enc` | S3 `<S3_PREFIX>recordings/<id>.ogg.enc` | `AUREC1` + 12-byte nonce + AES-256-GCM(Ogg Opus) | `RecordingStore.save` | `RECORDING_RETENTION_DAYS` (0 = keep); the row stays with `deleted_at` |
| Recording key (dev only) | `brain/data/recording.key` (base64 of 32 bytes) | **Never a file**: `RECORDING_KEY` from the key vault; the brain refuses to start with S3 and no key | base64 | `RecordingStore._cipher` | Lose it and every recording is unreadable |
| Phrase audio for paid voices | `brain/data/tts-cache/<sha256[:32]>.pcm` | Container disk (rebuilt after a restart; cheap) | Raw PCM16 16 kHz | `PhraseCache._synthesise` | Until deleted; name changes if voice/text changes |
| Test audio | `brain/data/audio/*.wav` | – | WAV | You, with `brain say … -o data/audio/q.wav` | Yours to delete |
| Application logs | Console + `brain/logs/app.jsonl` (+ `.1` … `.5`) | Container stdout as JSON (`LOG_CONSOLE=json`) for the log service | Text / JSON lines | `logs.setup_logging` | 6 files × 10 MB, then the oldest is overwritten |
| Postgres's own log | `brain/data/pg/log` | Managed by RDS | Text | Postgres | Postgres's rules |
| pgserver bookkeeping | `brain/data/pg/.handle_pids.json` | – | JSON list of pids | pgserver (`_prune_dead_handles` cleans dead ones) | – |
| Metrics | **Process memory** only | Scraped from `/metrics` by Prometheus/CloudWatch agent | Counters, gauges, histograms | `metrics.py` | Reset on restart (the scraper keeps history) |
| Eval replay cache | `brain/evals/cache/<sha256>.json` | – | `{kind, model, request_preview (masked), response, meta}` | `ReplayLLM._store` (write to `.tmp`, then rename: never half-written) | Until you delete the folder |
| Eval reports | `brain/evals/reports/smoke-…`, `regression-…`, `checks-…`, golden reports (`.md` + `.json`) | – | Markdown + JSON | `evals/smoke.py`, `golden_run.py` | Until deleted |
| Eval run rows | Postgres `eval_runs` | – | Row (config, metrics, results) | `EvalRunner` | Kept |
| Model weights | `brain/models/<name>/` | Baked into the search server image | safetensors + tokenizer files | `scripts/download_models.py` (checksum-checked) | – |
| Settings and secrets | `brain/.env` (git-ignored) + environment | Task definition env vars + Secrets Manager | `KEY=value` | You | – |
| Browser | `localStorage["desk-agent"]` (the desk person's name) | Same | String | `ui/app/desk/page.tsx` | Per browser |

**What lives only in process memory (and is lost on a restart, by design)**
| Thing | Where | Why that's fine |
|---|---|---|
| Live calls (`LIVE_SESSIONS`), their audio buffers, VAD state, captures, player queue, recorder buffers | `voice/session.py`, `services/recordings.py` | A restart ends the calls anyway; the recording of a call cut by a crash is lost (known limit) |
| The live-call counter `_live_calls` | `api/voice.py` | Per process by design |
| OTP HMAC secret `_SECRET` | `services/auth.py` | A restart invalidates codes being typed (to fix before several copies: one secret from the vault) |
| Customer read cache (8 s) | `AuthService._customers` | Re-read on the next turn |
| Rate-limiter windows | `RateLimiter._hits` | Per process; production limit moves to the WAF |
| Breaker state, fallback "switched" flags | `ResilientLLM`, `_Fallback` | Starts fresh after a restart |
| Phrase cache (memory part) | `PhraseCache._cache` | Rebuilt by `warm()` at startup |
| Recent system errors for the health alert | `AuditStore._errors` | Last 20 timestamps |
| Mock bank tokens (random when not set) | `mockbank/server.py` | The embedded bank restarts with them |

## O3. Logging in depth (`brain/app/logs.py`)

**Setup** (`setup_logging`, called first thing in `create_app`):
- the root logger gets exactly **two handlers**:
  1. **console** (`StreamHandler`): `TextFormatter` by default, `JsonFormatter` when `LOG_CONSOLE=json` (containers);
  2. **file**: `RotatingFileHandler("logs/app.jsonl", maxBytes=10_000_000, backupCount=5)`, always `JsonFormatter`. When the file reaches 10 MB it is renamed `app.jsonl.1` (older ones shift to `.2` … `.5`; the sixth is dropped) and a new one starts;
- level from `LOG_LEVEL` (default INFO);
- noisy libraries (`httpx`, `httpcore`, `urllib3`, `pdfminer`, `transformers`, `huggingface_hub`, `multipart`) set to WARNING; `pgserver` to CRITICAL (it dumps whole Postgres logs); `uvicorn.access` to WARNING because our middleware already logs each request once.

**What a line looks like**
- Console: `09:48:02 INFO    app.db                   postgres ready  data_dir=…  [trace 1a2b3c4d]`
- File (one JSON object per line):
  ```json
  {"ts": "2026-11-17T09:48:02", "level": "INFO", "logger": "app.http", "msg": "request",
   "request_id": "3f9c0a1b2c4d", "data": {"method": "POST", "path": "/v1/ask", "status": 200, "ms": 1840}}
  ```
- `request_id` and `trace_id` are added automatically from **ContextVars**: the middleware sets `request_id` for each HTTP request; `Conversation.turn()` sets `trace_id` for each turn. So every line written while handling that request or turn carries them, without passing ids around.
- Structured fields go in `data` through the helper `log(logger, "msg", key=value…)` (= `logger.info(msg, extra={"data": {...}})`).

**Masking**: every message, every `data` field and every traceback goes through `mask()` = `privacy.redact()` before it is written. So even a careless `logger.info(f"… {text}")` can't leak a card number, Aadhaar, phone or account number.

**The main loggers and what they write**
| Logger | Lines |
|---|---|
| `app.http` | One `request` line per HTTP request (method, route template, status, ms); `unhandled error` with traceback |
| `app.transcript` | Two lines per turn: `caller` (masked question) and `agent` (the answer, or `[account details given: fields]` for bank data, never the amounts), with call id, turn id, intent, outcome, first 16 characters of the turn hash (`audit.log_turn`) |
| `app.services.conversation` | `turn` summary (outcome, intent, language, reason, grounding, total_ms); `router output repaired`; `turn failed` |
| `app.services.audit` | `alert` warnings |
| `app.db` | `postgres ready`, `migration applied`, retries during crash recovery |
| `app.container` | `container ready` with startup time, device, providers |
| `app.services.worker` | `job finished`, `ingest failed` (with a short traceback) |
| `app.services.recordings` | `recording saved` / deleted / purged |
| `app.adapters.llm` | `llm retry` (status, wait) |
| `app.adapters.resilient` | `llm breaker open` |
| `app.voice.fallback` | Sarvam failed / out of credit → backup |
| `app.mockbank` | The mock bank's own events |
| `app.voice.session` | `voice task failed`, `stt failed`, `reply failed` |

**Never in any log**: OTP/PIN codes (keypad path; the turn is stored as `[code]`), API keys (`SecretStr`), bank figures in transcript lines, raw audio. Texts sent to the search server are not logged by it either (`search_server.py`).

**Logs vs traces vs audit** (people mix these up):
| | Logs | Traces (`traces` table) | Audit (`call_events`, `auth_events`, `alerts`, hash chain) |
|---|---|---|---|
| For | Engineers debugging the running system | Explaining one answer | Proving what happened on a call |
| Form | Text/JSON lines in files or stdout | One DB row per turn with everything | DB rows, tamper-evident turns |
| Kept | Rotated away (60 MB) | Kept | Kept |
| Seen in | The terminal, `logs/app.jsonl` | `/v1/traces/{id}`, the console's answer details | `/calls`, transcript file, review |

## O4. Every table, column by column (the important columns)

**Calls**
| Table | Columns (type) and meaning |
|---|---|
| `sessions` | `id` uuid · `language` · `created_at` · `caller_number` (caller ID) · `customer_id` (CIF, set only after verification) · `verified_at` (sliding idle window) · `auth` jsonb (stage, code HMAC, expiry, attempts, sends, masked mobile, waiting question) · `facts` jsonb (senior citizen, tenure…) · `pending` jsonb (the yes/no or "which one?" question waiting, with its kind and time) · `state` jsonb (`CallState`) · `ended_at`, `end_reason` · `chain_hash`, `chain_len` (head of the hash chain) |
| `traces` | `id` uuid · `session_id` · `created_at` · `question` (masked; `[code]` for codes) · `language` · `intent` · `outcome` · `answer` · `total_ms` · `data` jsonb (understanding, retrieval with scores, context chunk ids, checks, bank calls, timings, models, prompt version, grounding, later `voice` timings and `feedback`) · `prev_hash`, `hash` |
| `call_events` | `session_id`, `created_at`, `kind` (call_started, disclosure, phrase, interrupted, unclear_speech, on_hold, agent_joined, handoff_accepted, ticket, call_ended, recording_deleted, error…), `detail` jsonb |
| `auth_events` | `session_id`, `event` (otp_sent, otp_verified, otp_failed, locked, mobile_requested, mobile_not_registered, verification_expired, data_read…), `customer_id`, `detail` (masked numbers only) |
| `alerts` | `session_id`, `kind` (wants_human, struggling, system_health), `severity`, `message`, `detail` |
| `recordings` | `id`, `session_id`, `trace_id` (voice notes), `kind` (call / voice_note), `started_at`, `duration_ms`, `format` (audio/ogg), `bytes`, `sha256` (of the encrypted file), `path` (file name; NULL once deleted by retention), `deleted_at` |
| `handoffs` | `id`, `session_id`, `department`, `priority` (1 fraud … 3 normal), `reason`, `note` jsonb (the brief), `status` (waiting/active/completed/abandoned/callback), `channel` (call/chat), `accepted_at`, `ended_at`, `agent_name`, `disposition`, `agent_notes` |
| `handoff_messages` | `handoff_id`, `sender` (customer/agent), `text` |
| `tickets` | `id` text (`AU-CMP-20261231-0001` style), `session_id`, `cif`, `kind` (complaint/dispute/service_request/callback/lead), `category`, `department`, `summary`, `detail`, `status`, `due_by` |
| `call_reviews` | `session_id` (PK), `mode` (judged/code_only), `reason`, `checks` jsonb, `judge` jsonb, `model`, `score`, `critical`, `needs_review`, `human` (a person's labels) |
| `fix_items` | `session_id` (SET NULL when the session row goes, so a clean slate keeps them), `turn`, `category`, `source`, `fix_area`, `what_happened`, `why_it_matters`, `fix`, `quote`, `status` (open/done/wont_fix), `status_note`, `status_at` |

**Knowledge base**
| Table | Columns |
|---|---|
| `documents` | `id`, `title`, `category`, `purpose` (knowledge / data: data is stored, never searchable), `enabled`, `deleted_at` (soft delete), `current_version_id`, `tags` jsonb (doc_type, authority, effective_to, superseded_by, instruction_flag), timestamps |
| `document_versions` | `id`, `document_id`, `version_no`, `filename`, `file_type`, `size_bytes`, `sha256`, `storage_key` (path in the file store), `status` (queued/processing/indexed/failed/superseded), `error`, `effective_from`, `options` (e.g. which sheets), `report` (parser report, timings, chunk counts), `indexed_at` |
| `chunks` | `id`, `version_id`, `ordinal`, `kind` (text/table), `section_path`, `text`, `content_hash`, `word_count`, `active`, `tsv` (generated) |
| `chunk_embeddings` | `chunk_id`, `model`, `embedding` (vector) |
| `jobs` | `id`, `kind` (ingest), `version_id`, `status` (queued/running/done/failed), `attempts`, `error`, `created_at`, `started_at`, `finished_at` |

**Quality and housekeeping**: `eval_runs` (`golden_file`, `config`, `metrics`, `results`, `report_path`, `status`, `error`); `schema_migrations` (`name`, `applied_at`).

**Mock bank (`corebank`)**: `branches`, `customers` (cif, name, mobile unique, segment, branch, language, rm_name, scenario), `accounts` (balances, holds, average-balance rules), `account_txns`, `account_charges`, `debit_cards` (status + channel switches), `credit_cards` (limits, dues, fees, lounge), `card_txns`, `fixed_deposits`, `loans`, `loan_emis` (with `paid_on`), `loan_rate_changes`, `card_actions` (every change made through the agent, with before/after), `idempotency` (key, request hash, status, response).

## O5. Data lifecycle: what is created when, and how it goes away

**During one live call**
| Moment | Created |
|---|---|
| Connect | `sessions` row; `call_events: call_started`; in memory: `VoiceSession`, VAD, player, recorder |
| Greeting | `call_events: disclosure` |
| Each turn | one `traces` row (hash-chained), updates to `sessions` (`facts`, `state`, `pending`, `chain_hash`); `auth_events` for verification steps and data reads; maybe `alerts`, `tickets`, `handoffs`, `corebank.card_actions`; two `app.transcript` log lines |
| Interruptions, unclear speech, errors | `call_events` |
| Hang-up | `sessions.ended_at`; `call_events: call_ended`; `recordings` row + encrypted file; later `call_reviews` and `fix_items` |

**How data is removed**
| Action | Removes | Keeps | Code |
|---|---|---|---|
| Delete one call (`DELETE /v1/calls/{id}`, refused while live) | That call's session, turns, events, alerts, auth events, recordings (rows and files), handoffs and their messages, review, fix items | Its tickets (the bank's records; link cleared) | `maintenance.delete_call` |
| Clean slate (`brain clean-slate`, asks for DELETE) | Every call and everything attached (`CALL_TABLES`), every recording file; re-seeds the mock bank | The KB, eval runs, fix items | `maintenance.clean_slate` |
| Recording retention (`RECORDING_RETENTION_DAYS`) | Old recording files | The row, marked `deleted_at`, plus a `recording_deleted` event | `RecordingStore.purge_expired` (runs after each save) |
| Delete a document | Soft: `deleted_at` set, chunks made inactive (kept for audit) · Purge (`purge=true`): the document row (versions, chunks, vectors cascade) and its files | Soft delete keeps everything on record |  `DocumentService.delete` |
| Disable a document | Nothing; its chunks stop being searchable at once | Everything | `update(enabled=False)` |
| Log rotation | The oldest log file past 6 × 10 MB | – | `RotatingFileHandler` |

**What survives a restart**: everything in Postgres and in the file store. **What doesn't**: everything in O2's memory table. A call in progress is cut; the next call verifies again (no session restore, by design).

## O6. The flow of each door, with what it touches

| Door | Flow | Touches |
|---|---|---|
| **Typed chat** `POST /v1/sessions` then `POST /v1/ask` | middleware (request id, rate limit) → `ask()` → `Conversation.ask()` → `turn()` (Part F) → JSON `Reply` | `sessions`, `traces`, maybe bank, LLM ×0–2, search |
| **Keypad code (chat)** `POST /v1/auth/verify` | → `Conversation.verify()` → `AuthService` HMAC compare → if verified, the waiting question is answered | `sessions.auth`, `auth_events` |
| **Voice note** `POST /v1/voice/turn` (a file) | read ≤ 10 MB → `decode()` to PCM16 16 kHz (in a thread) → STT → noise check → `turn()` → TTS per sentence → WAV (base64) + timings; recording saved **after** the response (`BackgroundTasks`) | as chat + `recordings` |
| **Live call** `WS /v1/voice/stream` | Part E | as chat + `call_events`, `recordings`, `call_reviews` |
| **Document upload** `POST /v1/documents` | type check → SHA-256 dedupe → store original → `jobs` row → worker: parse → chunk → embed → atomic swap | file store, `documents`, `document_versions`, `jobs`, `chunks`, `chunk_embeddings` |
| **Desk** `/v1/desk/handoffs…` + `WS /v1/desk/stream` | board (waiting lists by priority) → accept → chat messages or join the live call's audio → complete (ends the call) | `handoffs`, `handoff_messages`, `call_events`, `LIVE_SESSIONS` |
| **Call audit** `/v1/calls…` | read the call record (turns, `why`, chain check, events, recordings decrypted on request after a SHA-256 check) | read-only, except review re-run, human labels, delete |
| **Post-call review** (automatic) | code checks → LLM judge (if selected) → quotes verified against the transcript → findings → fix items | `call_reviews`, `fix_items` |
| **Ops** `/health`, `/health/ready`, `/metrics`, `/v1/stats` | liveness; DB check + component report; Prometheus text; outcome/latency/feedback/doc counts over N hours | read-only |

## O7. Configuration: where settings come from
- One class, `Settings` in `config.py`. Each field's name is the env var's name, case-insensitive (`max_live_calls` ← `MAX_LIVE_CALLS`).
- Order of precedence (pydantic-settings): values passed in code (tests do this) > **environment variables** > **`brain/.env`** > the default in the class.
- Secrets are `SecretStr` (`GROQ_API_KEY`, `SARVAM_API_KEY`, `SEARCH_TOKEN`, `RECORDING_KEY`, bank tokens): printing shows `**********`; code calls `.get_secret_value()` only where the value is sent.
- Read **once** per process (`@lru_cache`), so a change needs a restart.
- The settings that change the architecture without code changes: `LLM_PROVIDER`, `STT_PROVIDER`, `TTS_PROVIDER`, `SEARCH_PROVIDER`/`SEARCH_URL`, `BANK_PROVIDER`/`BANK_BASE_URL`, `DATABASE_URL`, `FILE_STORE`/`S3_*`, `VOICE_ENABLED`, `LLM_FALLBACK`, `MAX_LIVE_CALLS`, `JUDGE_MODE`.

## O8. When something fails: what the caller hears, and what is recorded

| Failure | What the code does | Caller hears | Recorded |
|---|---|---|---|
| Groq 429 / 5xx | Retries within 4 s; `ResilientLLM` tries the fallback model if configured; breaker after 3 failures | Normal answer, or "I'm having trouble right now… I can connect you to our team" | `llm retry` logs; trace `outcome=error`; `system_health` alert after 2 errors in 5 min |
| Router returns bad JSON | `repair()` in code, else one lenient retry | Normal answer | `router_repaired_in_code` / `router_retried` in stats |
| STT fails | `_transcribe` returns an empty transcript (treated as noise); Sarvam → Whisper fallback | Silence, then the reprompt if it persists | `call_events: error`, log |
| TTS fails | Sarvam → Edge fallback (only before any audio of that sentence) | Same words, maybe another voice | Log |
| Bank system down / slow | 3 s timeout, one retry for reads | A plain "can't check that right now" style reply and an offer of a person | `bank_calls` in the trace |
| Bank write outcome unknown | Same idempotency key retried; then `BankUnconfirmed` | "I can't confirm whether it went through" (never "done" or "failed") | `corebank.idempotency`, trace |
| Search server down | KB question can't be searched | The safe "system problem" reply; bank questions and voice still work | `/health/ready` reports it (doesn't fail readiness) |
| Database down | Turns fail | The "having trouble" reply | `/health/ready` → 503 |
| A sentence fails the figure check | Stop the answer there | What was already said stays + "I'd rather not guess the rest", or a refusal | `checks` in the trace, reason `figure_check_failed` |
| Process crashes mid-call | Call is cut; on restart, jobs left `running` go back to `queued`; Postgres recovers | The line drops | The turns saved so far remain; that call's recording is lost |
| Process full (9 calls) | Refuse the 10th | Busy (production: another container or a person) | `brain_calls_refused_total` |

---


# Part P. Deep-dive prep: deployment, scaling, evaluation, numbers, limits

This part is for a technical review with a manager. Each section gives the facts, the real numbers with their status, and short "if asked, say" answers. Labels used: **Built** (in the code), **Measured** (a real number from this project), **Estimate** (a calculation, not yet load-tested), **Plan** (proposed, not built), **Confirm** (depends on AU or a vendor).

Sources: `deploy/README.md`, `deploy/ecs/*.json`, PROJECT-GUIDE §17–21, §26–27, §30, `context/progress-tracker.md`, `brain/app/services/review.py`. Read 18 Nov 2026.

## P1. Deployment

### P1.1 What exists today
| Piece | Status |
|---|---|
| Demo | Runs on the owner's laptop with no Docker: `python app.py` (brain, :8000) + `bun run dev` (UI, :3000). Private Postgres via pgserver, models on the laptop GPU (RTX 3050), Groq + Edge in the US |
| `brain/Dockerfile` | **Written, never built or run** (as of 13 Nov; nothing newer in the tracker). Multi-stage, non-root, ~12 GB image with models |
| `ui/Dockerfile` + `nginx.conf` | Written: static pages on nginx, non-root, port 8080 |
| ECS task definitions | Written (`deploy/ecs/`), never registered |
| AWS account, network, database | **Not created** |
| Cisco integration, AU bank APIs, SMS gateway | **Not built** (depend on AU) |

**If asked "is it deployed?"**: No. It's a working demo on one laptop. The container images and ECS task definitions are written but haven't been built or run. The AWS design is a proposal that AU still has to confirm.

### P1.2 The images
| Image | Contents | Runs on | Task size (task definition) |
|---|---|---|---|
| `au-brain` (default build) | The whole app plus the two search models | ECS on **EC2 with a GPU** (g6.xlarge) | 4 vCPU, 14 GB, 1 GPU, `stopTimeout` 120 s, container health check `GET /health` |
| `au-brain` with `--build-arg SEARCH_MODELS=0` | The app without search models (~4.5 GB smaller); still has PyTorch for Silero VAD | CPU servers | Plan: 2 vCPU, ~4 GB per container (the sizing sheet) |
| `au-search` | Same image, command `python -m app search --host 0.0.0.0 --port 8200` | GPU g6.xlarge, 1 + a spare | 4 vCPU, 14 GB, 1 GPU |
| `au-ui` | Static pages + nginx | **Fargate** | 0.25 vCPU, 0.5 GB |

The difference between the two brain sizes: the **first deployment** (pilot) is one brain task with search inside it on one GPU server. The **scaled design** moves search to its own GPU servers and runs many small CPU brain containers.

### P1.3 The AWS pieces (`deploy/README.md` §1, Mumbai ap-south-1)
| Piece | Choice | Why |
|---|---|---|
| Region | Mumbai (ap-south-1); Hyderabad (ap-south-2) for disaster recovery (plan) | Data residency in India |
| Network | One VPC, 2 availability zones, private subnets; a **NAT gateway** only for Sarvam (the one internet service); **VPC endpoints** for Bedrock, S3, Secrets Manager, CloudWatch Logs, ECR | AWS traffic never crosses the internet |
| Front door | **ALB + WAF**, HTTPS with an ACM certificate (the browser mic needs HTTPS); idle timeout 300 s (WebSocket calls); security group limited to office/VPN IPs (no staff login yet) | One address for UI and API: no CORS |
| Routing | `/v1/*`, `/health`, `/health/ready`, `/docs`, `/openapi.json` → `au-brain-tg` (port 8000, health check `/health/ready`); everything else → `au-ui-tg` (port 8080, `/healthz`) | |
| Compute | **ECS**, not EKS (EKS adds about ₹6,862 a month; use it only if it's AU's standard) | Simpler to run |
| Database | RDS PostgreSQL 16/17 for a pilot (`db.t4g.medium`), Aurora later; pgvector built in; the brain creates the extension and tables itself | Same code as the laptop, only `DATABASE_URL` changes |
| Files | S3 bucket, public access blocked, SSE-KMS; recordings under `brain/recordings/` | Survive restarts; shared by copies |
| Secrets | Secrets Manager secret `au/brain`: `DATABASE_URL`, `SARVAM_API_KEY`, `RECORDING_KEY` (later `SEARCH_TOKEN`) | Injected as env vars by ECS; never in the image |
| IAM | `au-ecs-execution` (pull images, read the secret); `au-brain-task` (S3 on the bucket; `bedrock:InvokeModel` + `InvokeModelWithResponseStream` on the India-only model) | Least privilege per role |
| Logs | CloudWatch log groups `/ecs/au-brain`, `/ecs/au-ui` (the image logs JSON to stdout) | |
| Images | ECR repositories `au-brain`, `au-ui` | |
| Search server | ECS Service Connect alias `au-search:8200`, no load balancer, port 8200 open only to the brain's security group, bearer token | Private |

### P1.4 How a deploy works, and what's not solved
- Build on a machine in Mumbai (the build downloads ~4.5 GB of models and ~6 GB of packages), push to ECR, register task definitions, create the ECS services.
- **Brain service settings for the pilot**: desired count **1**, health-check grace period **300 s** (models take 1–2 minutes to load before the port opens), minimum healthy 0% / maximum 100% (one GPU server, so the old task stops before the new one starts: a short outage).
- **Live calls during a deploy (not solved yet, B2.4)**: ECS's stop timeout is at most 120 s, but calls last about 4 minutes. The plan is the ALB **deregistration delay** (about 600 s): the old task stops getting new calls and keeps its live calls until they end. To check on the first deploy.
- **Rollback**: re-deploy the previous image tag (standard ECS). Canary releases (1–5% of calls) are planned for production (PROJECT-GUIDE §26.7).
- **Settings in the task definition**: `LLM_PROVIDER=bedrock`, `STT_PROVIDER=sarvam`, `TTS_PROVIDER=sarvam`, `DEVICE=cuda` (no silent CPU fall-back), `FILE_STORE=s3`, `LOG_CONSOLE=json`. Demo-only defaults to turn off for real use: `OTP_DELIVERY=simulated`, `DEMO_CALLERS=true`.

### P1.5 Why these choices (if asked)
| Question | Answer |
|---|---|
| Why containers and not Lambda? | Calls hold a connection for minutes and the app loads about 2 GB of models. Neither fits serverless. Lambda only for small jobs (retention purge, nightly reports) |
| Why a modular monolith, not microservices? | One image is simpler to deploy, secure and debug. The parts are already separated by ports, so any of them can be split out later without rewriting logic. Microservices would add about ₹24,500 a month (one more server) and network hops |
| Why is search on its own GPU servers? | Brain copies then need no GPU (cheaper CPU servers, many copies). Bedrock rerank isn't offered in Mumbai and costs more; our thresholds are tuned on these exact models |
| Why ECS over EKS? | Less to run; EKS costs more. EKS only if AU's platform team requires it |
| Why no Docker Compose? | ECS doesn't read Compose files (AWS retired that tool in 2023). The ECS task definitions are the deployment files |

## P2. Scaling

### P2.1 The unit of scale
**Calls at once**, not requests a second. Each live call holds a WebSocket and a media stream for about 4 minutes. A call is pinned to one process for its whole life.

### P2.2 How many calls one process carries (PROJECT-GUIDE §26.6, §26.11)
| Part of a call | CPU (share of one core) | Status |
|---|---|---|
| Voice detector on every 32 ms frame (this is also the barge-in listening) | 2.9% on the laptop × 1.5 for a slower AWS vCPU = **4.3%** | Measured on the laptop; × 1.5 assumed |
| Everything else (audio in/out, recording, turn bursts, barge-in checks) | **2.0%** (about 1%, doubled to be safe) | Estimate |
| **One call** | **6.3%** | Estimate |

Rule: let calls use at most **60% of one core** (queueing theory: at 80% busy, bursts wait about 4 service times; at 95%, 19). 60% ÷ 6.3% = 9.5 → **9 calls per process** (`MAX_LIVE_CALLS=9`, Built). One container = one process = 2 vCPUs (the second vCPU serves helper threads, the network stack and the OS).

Laptop measurement (silent calls, Measured): 10 calls 40% of a core, 20 calls 89%, 40 calls 99% (one core full, although the laptop had 16 logical CPUs). An open question in the tracker: real calls may need 6.5–7.5%, which would mean 8 per container.

### P2.3 From 100 calls to servers (Estimate)
| Calls at once | Containers (÷ 9) | c7i.2xlarge servers (4 containers each) + 1 spare | GPU search servers | Database |
|---|---|---|---|---|
| 100 | 11.1 → 16 containers on 4 servers (6.3 calls each at the peak; 108 still fit with one server down) | **4** | **2** g6.xlarge (0.3 GPUs busy ÷ 60% = 0.5, + 1 spare) | **2** (main + standby, about 30 s failover); ~90 queries/s, ~160 connections |
| 400 | 44.4 | **13** | **3** | still fine |

### P2.4 What really limits capacity, in order (PROJECT-GUIDE §26.6)
1. **Bedrock token quota**: default quotas are far below what 100+ calls need (about 2 M input tokens a minute at 100 calls). Ask AWS early. Above 1,000–2,000 calls at once, compare provisioned throughput with self-hosted vLLM.
2. **Sarvam limits** (pricing page, 15 Nov): pay-as-you-go 60 STT and 60 TTS requests a minute, 20 STT streams; Pro 100 / 200 / 100; Growth 4,000 / 1,000 / 100. Today we make one STT request per utterance and one TTS request per sentence: at 100 calls about 300 STT and 600 TTS a minute, so the sheet uses **Growth**; its TTS ceiling is reached at about 165 calls. The Enterprise contract must state both numbers.
3. **Cisco ports and licences** for AI-agent calls, SIP trunk capacity (Confirm).
4. **GPU** for search: grows with searches, not KB size (documents are embedded once at upload).
5. **The database is not the limit** at these sizes (RDS Proxy and read replicas at very large scale).

### P2.5 How scaling would work (Plan)
- The ALB spreads **new** calls across containers; a full container refuses with WebSocket close 1013; the Cisco gateway tries another container or sends the caller to a person (gateway retry: not built).
- **ECS auto scaling** on the average `brain_live_calls` per container (e.g. target 6 of 9): not configured yet. Scale-in only after a container's calls end (the deregistration delay).
- `/health/ready` reports `live_calls` but never fails because of it (a failing health check would get the task replaced and cut its 9 calls).
- Beyond ~10,000 calls at once: **cells** (independent copies of the whole stack, each with its own quota and database).

### P2.6 What blocks running more than one brain copy today (B2.3 and §26.12)
| Blocker | Status |
|---|---|
| OTP HMAC secret is random per process (a typed chat code could land on another copy) | **To do**: one secret from Secrets Manager (about an hour) |
| Rate limiter is per process | **To do**: limit at the WAF / ALB |
| After-call review runs as an in-process task (lost if the copy stops right after a call) | **To do**: move into the `jobs` table |
| On start-up the worker resets **every** `running` job (would steal another copy's job) | **To do**: reclaim only jobs running longer than a time limit |
| Thread pool (min(32, CPUs+4)) and boto3's 10 connections shared by DB, Bedrock and search | **To do**: raise them, give the LLM its own pool |
| Each copy's share of Sarvam/Bedrock limits | **To do**: a semaphore per process |
| Recordings on S3, startup advisory lock, live-call cap and gauge, GPU search server | **Built** (16 Nov) |

**If asked "can it handle 100 calls?"**: The design says yes, with 16 containers on 4 servers plus 2 GPU servers. But that's an estimate. Measured: the code handles 9 calls per process, and the laptop test showed the CPU curve. Not measured: real calls on AWS. A load test at 2× the expected peak is the gate. Today's demo handles 1–2 calls, and the limit there is Groq's free tier, not the code.

## P3. Evaluation in depth

### P3.1 Four tiers (ADR 0019), so most checks cost no LLM quota
| Tier | What | LLM calls | Command |
|---|---|---|---|
| T0 offline | **395 tests pass** (tracker, 17 Nov): journeys, state, redaction, status mapping, variant gate, mock bank contracts, voice turn-taking, recordings, Sarvam adapters (faked with `httpx.MockTransport`), every template read as spoken | 0 | `uv run pytest` |
| T1 replay | Every real LLM response saved under SHA-256 of the exact request; any change is a miss | Only what changed | (inside T2/T3) |
| T2 smoke | 24 single questions, each failure class, en/hi/Hinglish | A few dozen | `python -m evals.smoke` |
| T2b regression | 33 whole conversations from the PM's test plans (cards, fraud, loans, transactions, language, LLM down) | Only what changed | `python -m evals.smoke --pack regression --keep` |
| T3 golden | 105 product questions (71 en, 17 hi, 17 Hinglish) with expected facts and behaviour | Hundreds, paced | `python -m evals.golden_run` (rarely; about an hour cold) |

### P3.2 How they are scored
- **Deterministic checks, not an LLM, for most things**: outcome, task, scope, transaction counts, status words, figures present in the source, language.
- **Multi-turn scripts check state, not prose** ("after turn 3, `declined` contains complaint and exactly 5 transactions were read, newest first").
- **Reported per class**: fraud recall and false-fraud rate, multi-intent recall, general vs personal accuracy, correction handling, **false action execution (must be 0)**, **missed verification (must be 0)**.
- Golden set: pass rate, behaviour, fact accuracy, retrieval hit@1/3/5 and MRR (mean reciprocal rank: 1 if the right passage is first, 0.5 if second…), latency p50/p95 per step, cost.
- **Pacing**: `evals/smoke.py` keeps each model under 7,600 tokens a minute (Groq's limit is 8k) and stops cleanly at the daily limit.
- **Every failure found in a call becomes a regression test**: T0 where code decides, T2 where the LLM does.

### P3.3 The post-call review: an LLM judge (`services/review.py`)
1. A **call packet** is built from the (already masked) record: turns, outcomes, bank fields read, actions, handoff, latency.
2. **Code checks always run, free**: disclosure played, verification before account data, leftover personal data, errors, slow replies (> 2 s), the caller repeating, repeated "I don't know", a request for a person, a degraded model, written-only marks in replies.
3. **Which calls the judge sees** (`judge_reason`): `JUDGE_MODE=all` in development. `selective` (production): any failed code check; any card action, ticket or transfer; any fraud, action or complaint intent; a call that ended mid-task; plus a **10% random sample** of clean calls to catch drift.
4. **The judge** is a different model from the agent (its own quota) and scores **five dimensions, 1–5, against an anchored rubric**: task completion, correctness, conversation (judged as heard once on a phone), compliance (no account data before verification, no action without a clear yes, fraud protected first), handoff. It lists findings with a category (wrong_fact, missing_information, lost_context, wrong_intent, missing_verification, unsafe_action, poor_voice_response, too_long, wrong_language, slow…), a source (agent, bank_system, knowledge_gap, unclear_caller…) and a fix area (router, retrieval, answer_prompt, bank_templates, workflow, code, knowledge_base, voice).
5. **Code verifies the judge**: every quoted piece of evidence must actually be in the transcript turn it names, or it is dropped (`verify_evidence`, `verify_findings`). Problems caused by a bank failure or an unclear caller don't lower the agent's score when it handled them honestly.
6. Findings become the **fix list** (`/fixes`), which a person marks done or won't fix.
7. **Calibration**: people label calls (`/v1/calls/{id}/review/human`) and `/v1/reviews/calibration` compares the judge with them. **Status: 0 labelled calls, so the judge is uncalibrated.** Owner's rule: never fix what the judge flags without the owner's OK.

**If asked "how do you know it works?"**: Most behaviour is checked by code, deterministically, on every change (395 offline tests). The LLM-dependent parts are checked on cached real-LLM packs (24 single questions, 33 conversations). The judge reviews calls afterwards, and its evidence is verified against the transcript. The honest gap: the golden set and routing set haven't been re-run on the current prompt, and the judge hasn't been calibrated against human labels.

## P4. Measured numbers (with their status)

**Speed** (laptop RTX 3050, Groq free tier, Edge TTS; PROJECT-GUIDE §19)
| Step | Typical |
|---|---|
| End-of-speech wait | ~1.1 s (700 ms silence + speculative STT) |
| Speech-to-text (Groq Whisper) | 0.3–1.2 s |
| Understanding (router) | 1.2–1.9 s |
| Embed + vector + keyword search (parallel) | ~0.1 s (85 ms warm) |
| Rerank 16 passages | 0.2–0.8 s |
| Answer first token | 0.5–0.75 s |
| Bank reads for a verified caller (6–15 calls in parallel, mock) | ~110 ms |
| **First audio of the real answer** | **3.6 s typical, 5.8 s slow** ("one moment" at 2.3 s); 22 recorded turns, 12 Nov |
| Bank-data question after understanding | ~1 s (no answer-writing LLM step) |
| VAD per frame | 0.88–0.94 ms CPU (1 torch thread; 3.7 ms with 4 threads) |
| HMAC signing | 6 µs (vs 56–132 µs ES256, 1.1 ms RSA) |

**Target**: 2.5 s first audio on AWS (Sarvam streaming STT, Sarvam voice, Bedrock in Mumbai). Research guidance: under 800 ms feels instant, over 1.2 s feels slow. We're not there.

**Accuracy**
| What | Result | Status |
|---|---|---|
| Documents indexed | Every word of every KB file | Word-by-word comparison |
| Golden set, best clean run | pass 72.4%, hit@3 91.1%, **hit@5 93.7%**, MRR 0.81, fact accuracy 78.1% | 3 Nov, **older prompt, not re-run** |
| Routing on the real LLM | 32/38 (84%) | 5 Nov, old prompt, stopped at the daily limit |
| Smoke set | **19/24 (79.2%)** in the latest runs (16–17 Nov) | Cached real 120b |
| Regression pack, first run | 61/69 turns passed the checks; reading the transcripts found 9 groups of problems, fixed in code | 17 Nov; not fully re-run after the fixes |
| Passage trimming | 0 items lost evidence, 22–24% less text | Replay on saved golden runs |
| STT vocabulary hint | word errors 15.7% → 12.0% | Synthetic clean audio, not phone audio |
| Offline tests | 395 pass | 17 Nov |

**Honest summary**: speed and accuracy numbers are from a laptop, the free tier and older prompts. Before go-live: re-run the golden, routing and smoke sets on the current prompt and again on Bedrock; a red-team set; human labels for the judge; speech tests on real phone audio; the load test.

## P5. Alternatives considered (why not X)
| Not used | Why not |
|---|---|
| Pipecat / LiveKit (voice frameworks) | Neither does our key behaviour: dip the volume → transcribe the overlap → resume or stop. LiveKit's interruption model runs in its cloud (audio would leave the bank). Our engine is about 400 tested lines. LiveKit SIP stays an option for the media gateway |
| LangChain / LlamaIndex / LangGraph | Many dependencies for a pipeline written in a few hundred clear lines; we need per-sentence checks while streaming |
| LLM function calling / agent loops | Each tool hop adds 0.6–1 s, and the model can pick the wrong tool, argument or customer. Instead: LLM understands → registry decides → code calls the bank in parallel → code validates |
| A separate vector database (Pinecone, Qdrant…) | A second store to secure and keep in step; pgvector is fast enough at our size; Pinecone is a US SaaS |
| Redis / Kafka / SQS for calls | A live call can't wait in a queue; all shared state is in Postgres; background work uses the `jobs` table |
| Docling (document parsing) | 103 packages, dropped about 5% of the rates PDF; pdfplumber reads it exactly |
| Presidio / ML PII models | A model per call adds latency; regex + checksums are more precise for Indian IDs |
| Voice biometrics | Cloned voices; verification stays RMN + keypad code |
| Responses API with stored history | Re-bills the whole call every turn (about 1.7× LLM cost), breaks fallback, keeps a copy of conversations at AWS. We use stateless Converse |
| Ragas / Langfuse | Our runner is exact for numbers and stores results with traces |
| tenacity / breaker libraries | Our retry (backoff + 4 s budget) and breaker are a few lines |
| WebRTC for audio | Needs STUN/TURN/signalling; production audio comes from Cisco anyway |

## P6. Languages
- **Built**: English, Hindi, Hinglish. The reply language follows the caller's **latest** turn, decided in code from their words (`reply_language`), not from the STT or LLM label alone. Greetings ("ok", "हाँ") never switch the language.
- **Other Indian languages**: detected as "other" and answered in English today.
- **Plan** (§27): Sarvam STT understands all 22 scheduled languages; Sarvam TTS speaks 10 + English. Tier 2 (Bengali, Marathi, Telugu, Tamil, Gujarati, Kannada, Odia, Malayalam, Punjabi) → full voice, one at a time; Tier 3 (Urdu, Assamese, Maithili…) → reply in the closest language the caller understands, or a human via a Cisco language skill group. **No per-language KB**: the router always writes an English search query. Each new language needs its bank-data templates translated (about 270 entries) and its own eval and red-team sets.

## P7. Cost (Estimate, cost workbook v2, 15 Nov, 100 calls at once)
- **₹28,59,913 a month, ₹7.63 a call**: Sarvam 69.3%, Bedrock 15.3%, other AWS 15.4%; 4 app servers.
- One call ≈ 4 minutes of audio, 1,350 characters spoken, 51,000 LLM tokens read, 4,600 written, 4.8 searches.
- LLM per call about $0.02 on gpt-oss-120b (about $0.14 on Claude Haiku 4.5). **Speech costs more than the LLM.**
- Call volume is a placeholder (about 10,500 or 16,400 a day: the owner picks; AU's data replaces both).
- Note: `docs/deployment-options-and-costs.md` and the local cost workbooks are **missing from the folder** (an open question in the tracker); the numbers above are quoted in PROJECT-GUIDE §26.6 from the Google Sheet.

## P8. Known limits and what is pending (say these before you're asked)
| Area | Limit |
|---|---|
| Accuracy | The figure check can't catch a real figure taken from the **wrong row** of a rate table. Golden and routing sets not re-run on the current prompt |
| Latency | First audio ~3.6 s vs a 2.5 s target; STT is per utterance, not streaming |
| Telephony | No Cisco integration; browser only. 8 kHz phone audio not tested; VAD and turn timings must be re-tuned on it |
| Scale | No load test. One brain copy only until B2.3 is done |
| Security | No staff login (SSO and row-level security are planned with it); OTP secret per process; kill switches not built; red-team set and VAPT not done |
| Judge | Uncalibrated (0 labelled calls) |
| Data | Mock bank only; some dummy fields missing (loan type, FD number, merchant names…); FD early-closure rules are from our **synthetic** policy document |
| Documents | No OCR for scanned PDFs, no PPTX, no page numbers on passages |
| Voice | "Slower please" not supported; a two-sentence opening was once split into two turns; Hinglish sometimes transcribed in Urdu script |
| Residency | Groq and Edge are US/global: fine for a hackathon, not for production customer data |
| Process | Much recent work is not committed (the owner commits) |

## P9. Hard questions, with short answers
| Question | Answer |
|---|---|
| What stops the LLM leaking another customer's data? | It never chooses the customer. The customer id comes from the verified session, and every bank call carries a signed token naming that customer. Each bank system rejects anyone else. The LLM only names a topic, and it never sees the figures (bank answers are templates) |
| What if the LLM says a wrong rate? | Every number in every sentence must be in the cited passage, or the sentence is blocked. The known gap is a real figure from the wrong table row |
| What if Groq/Bedrock is down? | Retries within 4 s, then a tested fallback model (breaker after 3 failures in 60 s), then an honest "I'm having trouble" with a transfer offer that "yes" accepts in code (no LLM needed) |
| Why does the voice detector run on the event loop? | It's ~0.9 ms per 32 ms frame and needs exact frame order. Threads wouldn't help Python code (GIL), and torch with more threads wasted CPU (measured). Moving it to threads is a later density option |
| Why not more threads per process? | The GIL lets one thread run Python at a time. More processes give more calls; threads only help with waiting and C code |
| Why 9 calls per container? | 6.3% of a core per call (estimate from measurement) and a 60% ceiling for bursts: 9.5 → 9. The load test replaces it |
| How do you avoid losing calls on deploy? | ALB deregistration delay (~600 s) so the old task drains. Planned, to be checked on the first deploy |
| How do you prove a transcript wasn't edited? | Each turn stores SHA-256(previous hash + content); the session keeps the head. Editing, deleting or reordering any turn breaks the chain, and `verify_chain` reports where |
| Where is the OTP stored? | Only as an HMAC in `sessions.auth`, with expiry and attempts. Never in logs, traces, the LLM or recordings (it's typed, not spoken) |
| What happens with 1,000 calls? | About 112 containers on ~29 servers by the same rule, plus GPU and quota (Estimate). The real limits first are the Bedrock quota and Sarvam's stream and request limits, not our code |
| Why Postgres as the job queue? | `FOR UPDATE SKIP LOCKED` gives safe concurrent workers with no extra system. It only carries background work, never live audio |
| How would you add a new task (e.g. cheque book request)? | A row in the registry (`task_for`), a line in the router prompt and schema, a handler (or a hand-off), templates in three languages, tests (T0) and a regression item (T2) |
| How would you plug in AU's real bank? | A new adapter implementing the `Bank` and `ServiceDesk` ports (like `http_bank.py`), mapping AU's ids and status words. The conversation code stays. Workflows and status mappings must be re-validated against the real APIs |
| Is any customer data sent outside India? | In the demo, yes: Groq and Edge are US/global (dev only, mock data). In production everything is in Mumbai/Hyderabad: Bedrock with an India-only profile, Sarvam India processing (to be put in the contract) |

## P10. Inconsistencies found while checking (worth fixing, or knowing)
| Where | What | Effect |
|---|---|---|
| `deploy/ecs/brain-task-definition.json` | Sets `STT_MODEL=saaras:v3`. In `config.py`, `STT_MODEL` is the **Groq Whisper** model name; Sarvam's is `SARVAM_STT_MODEL` (default `saaras:v4`) | Sarvam would still use v4; the Groq fallback would be given a Sarvam model name. Not yet fixed (not asked) |
| CLAUDE.md, README, PROJECT-GUIDE | Link to `docs/deployment-options-and-costs.md` and a cost `.xlsx`, which are not in `docs/` | Open question in the tracker |
| PROJECT-GUIDE §20 | Still says "316 tests" and mentions `test_live_llm.py` / `-m live`; the tracker says 395 pass and the live test was converted | Doc out of date |
| ADR 0021 vs the AWS proposal | ADR 0021 (accepted) says add Redis at the first multi-server deployment; the 12 Nov proposal says no Redis at launch | Needs a new ADR once the owner decides |

---


# Part M. Frequently asked questions (with answers)

Short answers first; the part to read for depth is in brackets.

## M1. The project in general
**Q: In one sentence, what is it?**
An inbound AI voice agent for AU Small Finance Bank that answers product questions from the bank's documents, verifies callers with a keypad code, reads their own bank data, does a few card actions after an explicit yes, raises tickets and hands over to people, in English, Hindi and Hinglish.

**Q: What are the main parts?**
The brain (Python/FastAPI: voice engine, conversation core, RAG, bank adapter, audit), Postgres with pgvector, local GPU models (embedder, reranker) and Silero VAD, cloud services (Groq LLM and Whisper in development; Sarvam and Bedrock in production), a mock bank, and a Next.js test console. [B, C]

**Q: Is it STT → LLM → TTS?**
Yes, a **cascaded** pipeline, but with a lot around it: VAD and turn-taking before STT; code checks before and after the LLM; **two** LLM calls per turn (understand, then answer) and often **zero** (yes/no, small talk, bank answers from templates, refusals); sentence-by-sentence checks and streaming into TTS; paced playback with barge-in. [E, F]

**Q: Why not one speech-to-speech model?**
We need code between hearing and speaking: verification, permissions, the figure check, PII masking, audit. A single audio model gives no place to enforce those, and its outputs can't be checked sentence by sentence (ADR 0011).

**Q: Why two LLMs?**
Understanding must be fast and structured (20b, strict JSON); answers need better language (120b, streamed). Separate models also have separate rate-limit quotas.

**Q: What does "the LLM understands, code decides" mean in practice?**
The LLM fills a fixed JSON form (what was asked, which topic, risk flags). Code decides the handler, the access level, whether verification is needed, which bank calls to make, what bank-data sentences say, whether an action may run, and whether each generated sentence may be spoken. [F2–F7]

## M2. Audio and voice
**Q: What is PCM16 16 kHz mono?**
Raw audio: 16,000 samples per second, each a signed 16-bit integer (2 bytes, little-endian), one channel. 32,000 bytes per second. [A1]

**Q: Why 16 kHz and not 8 or 48?**
16 kHz captures speech up to 8 kHz (Nyquist), enough for clear consonants; it's what Whisper, Silero and Sarvam expect. 8 kHz is old telephone quality; 48 kHz wastes 3× the bandwidth on frequencies speech doesn't need. Real phone audio from Cisco may arrive at 8 kHz and will need resampling (production item).

**Q: How big are the frames, and why do they differ?**
Browser → server: 20 ms (320 samples, 640 bytes). VAD: 32 ms (512 samples, 1,024 bytes), because Silero requires exactly 512 samples at 16 kHz. Server → browser: 20 ms. `feed()` buffers bytes until a full VAD frame exists. [E4]

**Q: How does the agent know the caller stopped talking?**
VAD per 32 ms frame; count silence on the audio clock; 700 ms of silence ends a complete-sounding turn (stretched up to 1.5 s for callers who pause a lot); up to 2.5 s when the words sound unfinished ("and…", "aur…"). STT starts speculatively at 300 ms. [E4, E5]

**Q: What happens if the caller talks while the agent talks?**
Three filters drop background sound; 350 ms of real speech lowers the agent's volume; the overlap is transcribed and classified: noise/echo/"hmm" → full volume again; real words or "stop" → the agent stops, the client drops buffered audio, and the caller's words become the next turn. [E8]

**Q: How is the agent stopped within 0.2 s?**
The player sends audio only 200 ms ahead of real time, so at most 200 ms is buffered in the browser; `{"type":"clear"}` drops it. [E7]

**Q: How do you avoid the agent hearing itself?**
Browser echo cancellation; playing agent audio through a WebRTC loopback so Chrome's canceller sees it; on the server: a 0.3 s grace period at the start of agent speech, a stricter speech threshold (0.65) while the agent talks, the loudness check, and `is_echo()` (≥ 70% of heard words are the agent's own). [E1, E8]

**Q: What does the "focus on my voice" option do?**
In the capture worklet, frames much quieter than the caller's learned speech level are sent as silence, so distant voices and TV never reach the server. [E1]

**Q: Why a filler, and why only sometimes?**
Silence on a phone feels like a dropped call; a filler on every turn sounds like stalling. So only if nothing is ready after 1.2 s, and never the same line twice in a row.

**Q: How are recordings made and stored?**
Caller (left) and agent (right) collected in memory during the call, aligned on the caller's timeline; at hang-up, encoded to Ogg Opus in a thread, encrypted with AES-256-GCM, written to disk or S3, metadata row in `recordings`. Keypad codes are typed, so they are never in the audio. [E11, A8]

**Q: Why WebSocket rather than WebRTC?**
Simpler (no STUN/TURN/signalling), raw PCM is easy to process, and production audio comes from Cisco (SIP/RTP) through a gateway anyway (ADR 0012).

## M3. FastAPI, servers, processes
**Q: What servers run?**
On the laptop: the brain (uvicorn, port 8000), the embedded mock bank (a second uvicorn in a thread, random port), Postgres (separate processes, from pgserver), the UI dev server (bun/Next.js, port 3000). Optional: the GPU search server (port 8200). In production: brain containers behind an ALB, managed Postgres, a GPU search server, external Sarvam/Bedrock; the UI is static files. [C5, K4]

**Q: How many uvicorn workers?**
One process, no `--workers`. More capacity = more containers, each one process (one event loop per process).

**Q: What is ASGI and why does it matter?**
The interface between the server (uvicorn) and the app (FastAPI). It supports HTTP, WebSockets and lifespan events with async code, which a live call needs. [A5]

**Q: What's the difference between `async def` and `def` routes here?**
`async def` runs on the event loop (must not block; used for the turn, voice, anything that awaits). Plain `def` is run by FastAPI in a worker thread (used for simple blocking DB reads). [A5]

**Q: What is `lifespan`?**
Startup/shutdown code: builds the `Container` (DB, models, clients, services, worker) before serving, closes it after. [C]

**Q: How does a route get the services?**
`Depends(container)` returns `request.app.state.container`. WebSocket routes read `ws.app.state.container` directly.

**Q: What does the middleware do?**
Assigns a request id, applies the rate limit to selected paths, times the request, records Prometheus metrics, catches unhandled errors (answering 500 inside the CORS layer so the browser sees a real error), logs one line. It does not run for WebSockets.

## M4. Concurrency
**Q: Is each call a thread or a process?**
Neither: a WebSocket plus a few asyncio tasks in one process's event loop. [J1]

**Q: FCFS or round robin?**
Admission is first come first served up to 9 calls per process, no waiting queue; inside, the event loop runs ready tasks in the order they became ready (cooperative); across containers, the load balancer spreads new calls (round robin by default). [J2]

**Q: What happens to the 10th call?**
Accepted at the WebSocket level, told `{"type":"busy"}`, closed with 1013. The 9 calls in progress are not slowed. In production the gateway retries another container or routes to a person.

**Q: Why no lock on the call counter?**
One thread, and no `await` between the check and the increment, so no other task can interleave. [E2]

**Q: Doesn't the GIL make this single-threaded?**
The GIL lets one thread run Python at a time; the process still has many threads. Waiting (network, DB) and C code (torch, FFmpeg, crypto) release it. The call logic is one event loop, so it uses about one core; that's why calls per process are capped and capacity grows by processes. [A4, J6]

**Q: What if a slow thing runs on the event loop?**
Every call in the process stalls (audio frames queue, interruptions are noticed late). That's why DB calls, model inference, encoding and Bedrock run in threads, and why VAD is kept at ~1 ms per frame with torch on 1 thread.

**Q: How do two concurrent turns of the same call not corrupt the record?**
A new reply cancels the previous one; and `TraceStore.save` locks the session row (`FOR UPDATE`) so hashes chain in order.

**Q: How do multiple workers not process the same document?**
`SELECT … FOR UPDATE SKIP LOCKED LIMIT 1` inside a transaction. [G2]

**Q: How do multiple brain copies start safely on one database?**
The advisory lock `STARTUP_LOCK`: one copy migrates/creates the index/seeds at a time; the others then find the work done. [A6]

**Q: Why no Kafka/SQS/Redis for calls?**
A call needs a reply within about a second and a continuous stream every 32 ms; a queue adds delay and no capacity, and a caller can't wait in silence. Background work uses the Postgres `jobs` table (ADR 0021).

## M5. Database
**Q: Why Postgres for everything (including vectors)?**
One store for relational data, JSON, full-text search and vectors, with transactions: a document and its passages change in one transaction. Banks already run and approve Postgres. pgvector is fast enough at this size (ADR 0003, 0022).

**Q: What is the migrations folder?**
Numbered SQL files applied once each, in order, at startup, each in a transaction, recorded in `schema_migrations`. A change is a new file; applied files are never edited. [A6, H2]

**Q: What's in a trace?**
One row per turn: masked question, language, intent, outcome, answer, total time, and `data` (understanding, passages and scores, bank calls, checks, timings, models, prompt version, grounding), plus `prev_hash` and `hash`.

**Q: What is the hash chain for?**
Tamper evidence: editing, deleting or reordering any turn breaks the chain; `verify_chain()` reports where. [A8, F8]

**Q: Where does call memory live?**
`sessions` (facts, state, pending question, verification) and `traces` (history). The LLM gets the last 6 turns plus facts each time; it keeps nothing. [H1]

**Q: What is pgserver?**
A pip package that ships PostgreSQL binaries; the brain starts a private server in `data/pg` when `DATABASE_URL` is empty. [A6]

## M6. RAG and answers
**Q: How is a product question answered?**
Router rewrites the question and makes English search keywords → vector search (bge-m3, HNSW, top 30) and keyword search (tsvector, top 30) in parallel → RRF → rerank top 16 (cross-encoder) → refusal gate → code picks/trims passages and may ask which variant → answer LLM streams → each sentence checked (citation, figures, unsafe output) → spoken. [F5]

**Q: Why hybrid search?**
Vectors catch meaning across languages ("byaaj dar"); keywords catch exact names ("Zenith+", "BSBDA"). RRF merges them without tuning.

**Q: Why rerank?**
The cross-encoder reads question and passage together and is much more accurate than vector similarity alone; running it on 16 candidates keeps it fast.

**Q: How do you stop hallucinated numbers?**
Every number in a sentence must appear in the cited passages or the question (with lakh/crore/comma normalisation); numbers in words are rejected; bank-data answers use templates; on failure the agent refuses or stops. Known gap: a real figure from the wrong row of a table can pass.

**Q: What if retrieval finds nothing good?**
Best rerank score below 0.03 → "no approved information" and a transfer offer, without calling the answer LLM.

**Q: How are documents chunked?**
By section (~180 words, max 260), each passage with its section path; tables row by row with headers; rate sheets with stacked headers read top-down; duplicates dropped. [G3]

**Q: How is a document replaced without breaking live answers?**
The new version is parsed and embedded in the background, then swapped in one transaction by flipping `active` flags. [G3]

## M7. Security
**Q: How is the caller verified?**
Caller ID is the registered mobile (or the caller says it) → a 6-digit code (from `secrets`) sent by SMS (simulated in dev) → typed on the keypad → compared as HMAC in constant time; 5-minute expiry, 3 tries, 3 codes per session; verification lapses after 10 idle minutes. [E9]

**Q: Can the LLM be tricked into reading someone else's account?**
No. The customer id comes from the verified session; bank calls carry a token naming that customer; each bank system rejects other customers. The LLM only names a topic.

**Q: What is masked, and where?**
Cards (Luhn), Aadhaar (Verhoeff), PAN, phones, long numbers, emails, UPI ids, secrets after "OTP/PIN/CVV": before the LLM, in traces/history, in every log line.

**Q: How is prompt injection handled?**
A regex screen answers attempts with a fixed reply (no LLM); prompts tell the model sources and questions are data; documents that look like instructions are flagged and their passages dropped; the output filter blocks requests for secrets.

**Q: What if a bank write times out?**
Retry with the same idempotency key; if still unknown → `BankUnconfirmed` → the agent says it can't confirm, never "done" and never "failed".

## M8. Engineering
**Q: Which classes use inheritance?**
Only settings, request bodies, error hierarchies, the speech fallback base class and log formatters. Everything else is composition. Ports are Protocols (structural typing). [D1, D2]

**Q: How would you swap Groq for Bedrock, Edge for Sarvam, the mock bank for AU's APIs?**
Settings: `LLM_PROVIDER=bedrock`, `TTS_PROVIDER=sarvam`, `BANK_BASE_URL=…` (or a new adapter class implementing `Bank`). The conversation code doesn't change. [B5, L]

**Q: How do you test without the LLM?**
Fakes injected through `build(overrides=…)`; a throwaway Postgres; 395 offline tests in about 1.5 minutes. Real-LLM packs are replay-cached. [K3]

**Q: How do you know a change didn't make things worse?**
Offline tests, then the smoke set and regression pack (cached), before/after numbers recorded in the progress tracker. Rule: accuracy and latency must improve or stay the same.

**Q: What would you improve first for production?**
Streaming STT (Sarvam WebSocket), telephony integration through Cisco, one shared OTP secret, rate limiting at the gateway, after-call work in the `jobs` table, a load test to confirm 9 calls per container (`context/pending-work.md` B2).

---

# Part N. Quick glossary

| Term | Meaning |
|---|---|
| ACID | Atomic, Consistent, Isolated, Durable: transaction guarantees |
| ADR | Architecture Decision Record |
| AEC | Acoustic echo cancellation |
| ALB | AWS Application Load Balancer |
| ASGI | Python async server/app interface |
| AudioWorklet | Browser code on the audio thread, 128 samples per call |
| Barge-in | Caller speaks over the agent |
| Bi-/cross-encoder | Encodes texts separately / together |
| Circuit breaker | Stops calling a failing service for a while |
| CIF | Customer Information File number: the bank's customer id |
| Coroutine | An `async def` function's running instance |
| CORS | Browser permission for cross-origin requests |
| DTMF | Phone keypad tones |
| Embedding | A vector representing text meaning |
| Event loop | The asyncio scheduler on one thread |
| fp16 | 16-bit floating point on the GPU |
| GIL | Lock letting one thread run Python at a time |
| GIN | Index type for full-text search |
| HMAC | Keyed hash for integrity and authenticity |
| HNSW | Graph index for approximate nearest vectors |
| Idempotency key | Makes a retried write happen once |
| JSONB | Binary JSON column in Postgres |
| Migration | A versioned schema change |
| Nonce | A number used once (encryption) |
| Nyquist | Sample at ≥ 2× the highest frequency |
| Opus / Ogg | Speech codec / its container |
| PCM | Raw audio samples |
| Port / adapter | Interface / its implementation |
| Protocol | Python structural interface |
| RAG | Retrieval-augmented generation |
| RMN | Registered mobile number |
| RMS | Average loudness of a frame |
| RRF | Reciprocal Rank Fusion |
| Sample rate | Samples per second (16 kHz) |
| SecretStr | Pydantic type that hides its value when printed |
| SKIP LOCKED | Skip rows other transactions locked |
| SSE | Server-sent events (`data: …` lines) |
| STT / TTS | Speech-to-text / text-to-speech |
| Task | A scheduled coroutine in asyncio |
| TLS | Encryption for HTTPS |
| tsvector / tsquery | Postgres full-text document / query |
| TTFT | Time to first token |
| uvicorn | The ASGI server |
| VAD | Voice activity detection |
| WebSocket | Two-way message channel over one TCP connection |
