# Progress tracker

**Where we are now.** Read this first, then `pending-work.md`. After every meaningful change, add what was done, the
numbers, what needs a restart and the next step. Keep it short: move old entries to
[`progress-archive.md`](progress-archive.md), the full dated history from 4 to 23 Nov, which is not updated.

## Where we are (25 Nov 2026)

- **Built and working:**
  - the text and voice agent (English, Hindi, Hinglish);
  - RAG with a figure check;
  - keypad verification (the OTP never goes through speech-to-text, the LLM or logs);
  - mock-bank answers, card actions with a "yes", complaints, fraud first steps, several questions in one sentence;
  - hand-over to `/desk`, post-call review and judge, a tamper-evident call audit with encrypted recordings;
  - the call record file after each call;
  - the `/bench` page.
- **Now:** ready for **UAT on AWS EKS**, agreed with AU's infra team (ADR 0031, `deploy/README.md`):
  - one ECR repository with `brain-<v>` and `ui-<v>`;
  - one `m7i.xlarge` node, 1 brain pod, 1 UI pod;
  - an internal ALB: `/api/*` → brain, everything else → UI;
  - RDS PostgreSQL 16/17 with pgvector, 20 GB;
  - S3, Secrets Manager, CloudWatch Logs, Bedrock GPT-5.6 Luna;
  - about ₹37,800 a month with GST (Google Sheet, tab UAT).
- **Development moves to the company laptop:** Bedrock, Docker Postgres with pgvector, no GPU.

## Live state

- **Brain: restart needed** (25 Nov code). No new migration (the latest is 013).
- **API paths changed:** every route is now `/api/v1/...`. The UI, tests, evals, CLI and `requests.http` are updated. `/health`, `/health/ready` and `/metrics` stay at the root.
- **UI:** `bun run build` passes; the dev UI called `/api/v1/...` with every request 200 (checked 25 Nov).
- **Docker images:** Dockerfiles and Compose checked (`docker compose config`), **not built**: Docker Desktop wasn't running here. Build them on the company laptop.

## Numbers

| What | Result | Date and notes |
|---|---|---|
| Offline tests | **432 pass, 0 fail** | 25 Nov, owner's laptop (the 5 failures of 23 Nov fixed; see below) |
| Retrieval, no LLM (52 English golden questions with known sources) | RRF hit@5 92.3%, **hit@6 96.2%**; bge reranker hit@5 98.1%; MiniLM-L6 on CPU hit@5 90.4% at ~900 ms | 25 Nov. Why 6 passages without a reranker (ADR 0031) |
| Same, Hindi / Hinglish in the caller's own words (worst case; real calls use the English rewrite) | Hinglish RRF hit@6 100% (13); Hindi 85.7% (12 of 14; reranker 100%) | 25 Nov |
| Refusal gate `MIN_DENSE_SCORE` 0.35 (all 105 golden questions) | Answerable 0.48–0.78; unanswerable 0.48–0.67: 0.35 never refuses a good question; no threshold separates the two | 25 Nov: kept 0.35 |
| Latency per stage (recorded turns 13–21 Nov, Groq, p50 / p90) | End-of-speech wait 0.80 / 1.76 s; STT 0.37 / 1.24 s; understanding ~1.35 s (12 Nov) / 2.6 s; embedding (GPU) 0.08 s; search 0.02 s; reranker (GPU) 0.58 / 1.22 s; answer first token 0.64 / 0.97 s | Without a GPU the reranker step is gone; the embedding is ~0.2 s on a CPU |
| Smoke set (24) | 19 / 24 | 17 Nov, Groq, older prompts. **Never run on Bedrock Luna** |
| Golden set (105) | 72.4% pass, hit@3 91.1% | 3 Nov, older prompt |
| First audio of the real answer (voice) | 3.6 s typical, 5.8 s slow | 12 Nov, Groq + Edge. Re-measure on Bedrock (`/bench`) |
| Search on CPU | rerank 16 passages 13–37 s; one query embedding ~0.2 s | 20 Nov |

## Latest change: 25 Nov (2), uv removed (the office laptop blocks it)

| What | Change |
|---|---|
| Packages | `uv.lock` → `brain/requirements.txt` (exact pins for Python 3.12, from the old lock: same versions) and `requirements-dev.txt` (pytest; pgserver only below 3.14). `pyproject.toml`: the uv blocks removed, `requires-python = ">=3.12"`, pgserver moved to the `dev` extra |
| PyTorch | Installed first, CPU or CUDA build (the `requirements.txt` header). Docker: `TORCH=cpu` by default (EKS, office laptop; several GB smaller), `compose.gpu.yaml` passes `TORCH=cu128` |
| Dockerfile | Plain `python -m venv` + pip; code used in place (`PYTHONPATH=/app`). Not built here (Docker Desktop not running) |
| Tests | Never use `DATABASE_URL` any more. `TEST_DATABASE_URL` (a `..._test` database, dropped and recreated each run), else a throwaway pgserver. On Python 3.14 (office) set `TEST_DATABASE_URL`. **432 pass** with plain Python (pgserver path); the `TEST_DATABASE_URL` path is not run yet (needs Docker Postgres) |
| Docs | `uv run …` → `python …` in every doc; setup steps rewritten for pip |

## Latest change: 25 Nov, EKS UAT and the handoff (code + docs; brain restart)

| What | Change |
|---|---|
| `/api` prefix | `main.py` `API_PREFIX`. `ops.py` split into `router` (health, metrics at the root) and `api_router` (stats, bench). UI `lib/api.ts` adds `/api` to `NEXT_PUBLIC_API_URL`, and the WebSocket URLs too |
| UI health path | `/healthz` → `/ui-health` (`ui/nginx.conf`) |
| `RERANK=auto` (new default) | On only with a GPU, or `SEARCH_PROVIDER=http|sagemaker` (`config._gpu_search`). The owner's laptop keeps the reranker; the company laptop and EKS turn it off with no setting |
| 6 passages without the reranker | `CONTEXT_CHUNKS_RRF=6` (5 with the reranker). Measured above. The answer model already picks and cites its passages: no extra LLM call |
| Image | `SEARCH_MODELS=embed` (bge-m3 only, 2.2 GB less) for EKS; `download_models.py --embed-only` |
| Compose | The GPU is now optional: `compose.gpu.yaml`. The default runs on the CPU (company laptop) |
| EKS | `deploy/eks/`: `brain.yaml` (ServiceAccount, ConfigMap, Deployment `Recreate`, Service), `ui.yaml`, `ingress.yaml`, `secrets.yaml` (Secrets Store CSI). `deploy/ecs/` and `aws-pilot-guide.md` removed; `deploy/README.md` rewritten |
| Chroma removed | `VECTOR_STORE=pgvector` (default) or `numpy`. The `chroma` extra, its class and 510 lines of `uv.lock` are gone |
| Tests fixed | `test_benchmark` (fake settings lacked `rerank`), `test_personal` (expects the 23 Nov salary wording). `test_rerank_off` left a test product in the shared KB, so later savings questions asked "which one?" (that was the order effect in `test_voice_api` ×2 and `test_review`); it now deletes it |
| Instructions | `AGENTS.md` is the one instruction file (Codex / ChatGPT); `CLAUDE.md` imports it |
| ADR | 0031 (supersedes 0028 and 0030, which stay as the record of the ECS plan) |

**Research behind the reranker decision** (owner's ChatGPT notes; Microsoft, AWS and Anthropic guides; the
clouatre-labs benchmark):

| Idea | Verdict |
|---|---|
| RRF top-k straight to the answer model, which picks the evidence | Built: 6 passages, citations already per sentence |
| Small CPU reranker (FlashRank / MiniLM, "~31 ms") | Measured on our KB: ~900 ms with our long passages, and no better at top 5. Not used |
| A separate LLM reranking call | Not built: one more round trip on every KB turn |
| Parallel dense + keyword search; LLM → TTS streaming by sentence; a static opener while searching; short spoken answers (1–3 sentences); compact history; templates for bank data; "which one?" questions when a product is unclear | Already built |
| One LLM call instead of understanding + answer | Not done: understanding decides routing, permissions and bank reads (ADR 0008). Measure first on `/bench` |
| Speculative work on partial transcripts | Not now: Sarvam STT is not streamed yet (pending B2.6) |

## Issues from earlier sessions: where they stand

| Issue | Status |
|---|---|
| 5 failing tests (23 Nov) | **Solved** (432 pass) |
| Chroma: an extra store, too much | **Solved:** removed |
| ONNX runtime (ADR 0029) | Kept: a CPU option for query embedding (~3x faster) on the company laptop. Try `SEARCH_RUNTIME=onnx` with `download_models.py --onnx --embed-only` |
| Only one brain copy (keypad-code secret and rate limits in memory, B2.3) | Open: fine for UAT (1 pod, `Recreate`); needed before 2+ copies |
| `MIN_DENSE_SCORE` (0.35) not calibrated | **Checked** offline on the golden set (above); kept. Watch `/calls` on real Hindi calls |
| Reranker off: Hindi / Hinglish retrieval not measured | **Measured** in the worst case (above); the real path (English rewrite) still needs the Bedrock smoke set |
| Fix list (`/fixes`): 39 items, all still "open" (nobody ticked them) | 7 Nov (17): mostly Groq limit and system failures before Phase 4, handled since by the fallbacks and the bank-data templates. 16–18 Nov (10): last EMI paid, Rajesh's transfer, the FD-rate failure (system) are handled by templates and fallbacks; "total paid across loans" and "EMIs left" are not built (judge suggestions, owner to decide). 20–21 Nov (12): fixed 23 Nov from the owner's list, except "replacement card delivery time" (not in the KB) and "long transaction list" (reads 3, then offers the rest). Tick them on `/fixes` after a live check |
| Smoke set never run on Bedrock Luna; reasoning effort `low` vs `none` | Open: company laptop (`/bench`) |
| Brain image size (CUDA PyTorch + bge-m3, est. 7–8 GB, not measured) | Open, optional: a CPU-only PyTorch build would cut several GB (only for the EKS image) |
| Live calls on a deploy: does uvicorn let open WebSockets finish within the 300 s grace? | Not verified: check on the first UAT deploy |
| S3 Object Lock / retention, `CALL_ROWS_DAYS` | AU to decide |
| CloudWatch metric filter + alarm on `alert` lines | Infra setup, no code |
| Production cost sheet (100 calls, ECS) | Out of date for EKS; refresh when production is planned. The UAT tab is current |
| `docs/deployment-options-and-costs.md` (+ `.xlsx`) missing | Links now point to the Google Sheet; the guide's §26 text stays as the proposal of 12–15 Nov |

## Next (company laptop)

1. `git pull`. `pip install -e .` in the conda environment. Copy `brain/.env.example` → `brain/.env`: the Bedrock block, `MOCK_BANK=true`, `USE_SYSTEM_CERTS=true`, `DATABASE_URL` for Docker.
2. `docker compose up -d db` (repo root, `PG_PASSWORD` in the root `.env`). `python scripts/download_models.py --embed-only`. `python app.py`. Check that `/health/ready` shows `reranker: off`.
3. `python -m pytest`. Upload the KB on `/kb`. A few text and voice calls.
4. `/bench`: pin this setup. Then `python -m evals.smoke` (Bedrock) and compare with 19/24.
5. `docker compose up -d --build` once, to prove the images build. Then hand `deploy/README.md` to the infra team.

## Open questions (owner / PM / AU)

- **AU:**
  - peak calls at once and the hourly curve; is the line 24×7?
  - retention for recordings and transcripts; Object Lock;
  - who issues bank API tokens;
  - the Cisco hand-off method and its versions;
  - approved fraud wording;
  - FD early-closure rules (ours come from a synthetic policy document).
- **Product:**
  - "I'm interested in credit cards": ask first what they want, or go straight to spending questions?
  - May the agent suggest an overdraft against an FD?
  - Shared-OTP fraud: transfer straight away?
  - Green FD 7.90%: is it right?
- **Redis at the first multi-copy deployment** (ADR 0021 says yes; the AWS proposal says no). Decide with B2.3.
- **Judge data:** may redacted transcripts go to the judge model? On Bedrock it is the same model and provider.
- **Calls per container:** 9 or 8 (`MAX_LIVE_CALLS`). Decide after the UAT load check.
- Highest-rate questions from a table can pick the wrong row (the figure check passes any real figure). Better table chunking, or a deterministic lookup?
