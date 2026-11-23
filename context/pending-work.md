# Pending work: local demo and production on AWS (13 Nov 2026)

What is left, in two lists. Each item says why it matters and where the detail is. Tick items here and log them in
`progress-tracker.md`. Decisions behind the AWS plan are in `docs/deployment-options-and-costs.md` (proposals, not yet
ADRs: the owner and AU confirm them first).

## Where things stand

- **Built and running locally:** text and voice agent, RAG with figure check, keypad verification, bank-data answers,
  card actions with confirmation, complaints, fraud first steps, hand-over to the `/desk` page, post-call review and judge,
  call audit with encrypted recordings, test console (`/`, `/kb`, `/calls`, `/desk`, `/fixes`).
- **Code last changed:** brain 11 Nov 2026; UI 12–13 Nov (echo loopback, ADR 0012; tunnel hostnames in `next.config.ts`).
- **Offline tests:** 395 pass (18 Nov). The real-LLM smoke set and regression pack were last run 17 Nov.
- **Production target (proposed):** callers → AU's Cisco → our app on AWS Mumbai (one VPC, ALB + WAF, ECS). The app is
  one container image (a modular monolith). There is also a GPU search server, a staff screens app, Aurora PostgreSQL
  and S3. Sarvam does the speech; gpt-oss-120b on Bedrock Mumbai (Converse API) is the LLM.

## A. Local demo (laptop)

| # | Item | Why | Detail |
|---|---|---|---|
| A1 | Groq paid Dev tier, or test slowly | The free tier allows 8,000 tokens a minute: about one full turn. A product question (2 LLM calls), or a sentence split by a pause, hits the limit | Tracker, 13 Nov |
| A2 | Re-run the smoke set, then the regression pack | No real-LLM numbers since 5–6 Nov; nothing may get worse | `uv run python -m evals.smoke` (replay-cached), CLAUDE.md |
| A3 | Measure first-audio latency on the current code | Only one measurement exists (12 Nov, 22 turns: real answer 3.6 s typical, 5.8 s slow; "one moment" at 2.3 s) | `docs/deployment-options-and-costs.md` |
| A4 | Run the echo self-test on the owner's laptop | The loopback fix is unmeasured on real speakers | Pass mark: at least 15 dB of the agent's voice removed. The self-test doc `docs/echo-self-test.md` no longer exists (missing since at least 14 Nov); rewrite it or test by hand |
| A5 | Re-check the old issue table A–P on the real LLM | Fixed in code during Phase 4; most acceptance tests not re-run live | `progress-archive.md`, "Issue tracker" |
| A6 | Known bug: an English turn answered in Hinglish after a Hindi turn | Seen in call e0f24b03 (11 Nov) | `progress-archive.md`, 11 Nov |
| A7 | A pause mid-sentence splits one question into two turns | Fixed in code 18 Nov (opener rule, merge within 1.5 s of the reply, code reused); 20 Nov: a stop word first ("ruko, ...") while the agent speaks is a new turn, not merged; needs a live voice test | `voice/turntaking.py`, `voice/session.py`, `services/auth.py` |
| A8 | Share the demo: Cloudflare quick tunnel for the brain, ngrok for the UI | Done in docs and config | `ui/README.md` "Share the demo" |
| A9 | Commit the work | Much of the work since the second commit is uncommitted (owner commits) | `git status` |
| A10 | Tidy `docs/`: keep one cost workbook | `deployment-costs-and-options.xlsx` is current (100 calls at once); `-NEW`, `old_`, `new_` are older versions | Owner's call |
| A11 | Open product questions | FD early-closure rules, approved fraud wording, overdraft-against-FD, Green FD 7.90% | Tracker "Open questions" |
| A12 | Optional: a laptop load-test harness (fake STT/LLM/TTS with realistic delays + a call simulator with silent, talking and interrupting callers) | Replaces the sizing assumptions (× 1.5 AWS slow-down, 2% "everything else") with measured CPU per call, without Groq or Sarvam quota. About 1–2 days, no new libraries | PROJECT-GUIDE §26.6 |

## B. Production on AWS (Mumbai)

### B1. Inputs we need from AU and vendors first

| # | Item | Why |
|---|---|---|
| B1.1 | AU: calls a day, the busiest hour's share, average call length, peak calls at once | Every cost and server number depends on them. Today's figures are placeholders (12,500 calls a day; SBI's ratio suggests ~16,400 for AU's 1.2 crore customers) |
| B1.2 | AU: how Cisco hands calls to us (streams audio to our app, or a SIP call that needs a media gateway); licences for 100+ AI sessions | Decides whether we need a media gateway and an NLB |
| B1.3 | AU: bank API catalogue, sandbox, who issues customer tokens | Real bank adapter (ADR 0009, 0025, 0027) |
| B1.4 | AU: SMS gateway and DLT template for the OTP | Real keypad code delivery |
| B1.5 | AU: retention for recordings, transcripts, logs; DPDP and RBI owner; staff SSO | Storage, deletion jobs, access control |
| B1.6 | AU: AWS account rules, landing zone, ECS or EKS standard, private link to AU | Where and how we deploy |
| B1.7 | Sarvam: Enterprise contract (self-serve plans stop at 100 live streams **and** 4,000 STT / 1,000 TTS requests a minute on Growth), volume price, India-only processing terms, how fast `vad_signals` reports end of speech | 100 calls at once is exactly the self-serve stream limit; TTS requests reach Growth's ceiling at about 165 calls (PROJECT-GUIDE §26.4) |
| B1.8 | AWS: Bedrock quota for gpt-oss-120b in Mumbai (≈1.2M input tokens a minute at 100 calls at once) | The first limit we would hit |

### B2. Code changes before the app can run on AWS

| # | Item | Why | Notes |
|---|---|---|---|
| B2.1 | ~~GPU search server~~ **Built 16 Nov**: `python -m app search` + `adapters/search_http.py`, switched by `SEARCH_PROVIDER` (local default / http) | Lets brain copies run without a GPU | Left: run it on AWS (`deploy/README.md`, "Moving search to its own GPU server"); measure latency there |
| B2.2 | ~~Recordings on S3~~ **Built 16 Nov**: recordings go through the file store (`data/recordings` locally, `<S3_PREFIX>recordings/` with `FILE_STORE=s3`); `FILE_STORE=s3` refuses to start without `RECORDING_KEY`. Also built: a Postgres startup lock (migrations, vector index and mock-bank seeding run one copy at a time) | A container loses its local disk on every restart | Left: create the bucket and the `RECORDING_KEY` secret on AWS; Object Lock is a question for AU (PROJECT-GUIDE §30). Details: PROJECT-GUIDE §26.12 |
| B2.3 | One OTP secret and limits shared by all copies | Today per process (ADR 0021 note). Voice OTP works anyway (a call stays on one copy); text chat and restarts don't | Secrets Manager (about 1 hour); rate limits at the WAF. `LIVE_SESSIONS` and the desk live flag stay per copy (decided 16 Nov, PROJECT-GUIDE §26.12) |
| B2.4 | Dockerfiles: **app and UI written 13 Nov, not yet built** (`brain/Dockerfile`, `ui/Dockerfile`, ECS task definitions and steps in `deploy/README.md`). Still to do: let calls finish on deploy: the ECS stop timeout is at most 120 s and calls last ~4 min, so use the ALB deregistration delay (about 600 s): ECS waits for it before stopping the old task. Config, to check on the first deploy (the search server is the same image with another command, B2.1) | Needed to deploy | `/health` and `/health/ready` exist; today the brain runs as 1 task on 1 GPU server |
| B2.5 | Telephony adapter for Cisco (audio in and out, keypad digits as events, transfer back to Cisco with call variables) | Production calls | Voice engine takes plain audio frames (ADR 0012). Path depends on B1.2 |
| B2.6 | Sarvam speech-to-text as a live stream (and WebSocket TTS) | Today `SarvamSTT` uploads each utterance after it ends (adds delay). Text-to-speech streams over HTTP, one request per sentence, which counts against the per-minute limit. Do it first in rollout phase 0, measured before and after (PROJECT-GUIDE §26.4) | `voice/providers.py` |
| B2.7 | Tune voice detection and turn-taking on 8 kHz phone audio | Browser tests use 16 kHz | ADR 0012 |
| B2.8 | Real AU bank adapter with selective reads | Replace the mock bank | ADR 0025; `adapters/http_bank.py` |
| B2.9 | Hand-over to AU's human agents through Cisco, not our `/desk` | Real staff use AU's desktop | Go-live doc B2.2–B2.3. `/desk` across containers only if AU keeps it |
| B2.10 | Staff SSO with roles, then row-level security | Staff screens show customer data | ADR 0027 |
| B2.11 | ~~A cap on calls per container~~ **Built 16 Nov**: `MAX_LIVE_CALLS=9`, refused calls get `busy` + close 1013, `brain_live_calls` gauge, `live_calls` in `/health/ready` (which stays ready, so a full task keeps its calls) | Left: the Cisco gateway's retry or hand-over on 1013; ECS scaling on `brain_live_calls` (e.g. target 6 of 9) | PROJECT-GUIDE §26.11 |
| B2.12 | Larger helper-thread and Bedrock connection pools; a separate pool for LLM calls; set the pool size explicitly | Python's default pool is min(32, CPUs + 4) threads shared by every database, Bedrock and search call, and in an ECS container Python 3.12 counts the **server's** 8 vCPUs (12 threads), not the container's 2; boto3 keeps 10 connections per process | `adapters/bedrock.py`, PROJECT-GUIDE §26.6 |
| B2.13 | After-call work (review/judge, summary) in the `jobs` table; reclaim only jobs running too long | Today the review is an in-process task (`api/voice.py`): lost if the container stops right after a call. On start-up the worker requeues **every** running job, so a new copy would take back a job another copy is still running | `services/worker.py`, PROJECT-GUIDE §26.6 |
| B2.14 | Each copy's share of provider limits (Sarvam requests a minute, Bedrock calls) | Several copies must not together go over a vendor limit; limit ÷ copies, a semaphore per process; no Redis while copies are few | PROJECT-GUIDE §26.4, §26.6 |

### B3. AWS set-up (proposed, small on purpose)

- One VPC in Mumbai, two zones; ALB + WAF in front; NAT gateway to Sarvam; private endpoints to Bedrock, S3, KMS,
  Secrets Manager and logs. No NLB unless Cisco sends SIP.
- ECS (no fee) running the app copies on c7i.2xlarge, two g6.xlarge GPU servers (1 + spare), small servers for the
  staff UI. Aurora PostgreSQL (main + standby; RDS is fine for a pilot), S3 (recordings locked from early deletion).
- No Redis, SQS or RDS Proxy at launch. **Conflicts with ADR 0021** (accepted), which says add Redis at the first multi-server deployment; needs the owner's decision and a new ADR that supersedes 0021 (tracker "Open questions"). No Redis/SQS "watchman" for barge-in either: the voice detector stays inside each call (PROJECT-GUIDE §26.6). Backups copied to Hyderabad; a warm standby only if AU's
  recovery-time target needs one.
- Infrastructure as code and CI/CD with gates: offline tests, smoke set, image scan, small canary.

### B4. Guardrails, evals, security, latency and cost before go-live

| Area | Pending |
|---|---|
| Evals | Re-run smoke, routing and golden sets on **Bedrock gpt-oss-120b** (the production model) and compare with the Groq numbers. Add a phone-audio (8 kHz) voice set. Judge model and data approval (tracker open question) |
| Guardrails | Already in code: guard, redaction, figure check, access levels, confirmations, OTP never to STT/LLM/logs. Pending: red-team set, kill switches, per-mobile SMS limit, abusive-caller rule, AU's approved compliance wording |
| Security | KMS keys, Secrets Manager, IAM role per container, CloudTrail, GuardDuty, WAF rules, VAPT, CERT-In incident runbook (6 h). Bedrock India-only inference (ADR 0026) |
| Latency | Target: real answer under 2.5 s after the caller stops. Levers: GPU server for search, streaming STT, measured on AWS |
| Load | One-day AWS load test: calls per app container (with silent, talking and interrupting calls, CPU per process), time per search on the GPU, then ramp to the peak with faked speech and LLM. The measured calls per container goes into the cost sheet's Servers sheet (PROJECT-GUIDE §26.6) |
| Cost | Confirm with AU's volumes. Estimate today: about ₹8 a call, 75–80% of it Sarvam (`docs/deployment-costs-and-options.xlsx`) |
| Rollout | Lab with Cisco → shadow → information only (1–5% of calls) → account reads → actions → scale (go-live doc B9) |

## Not planned (on purpose)

- Microservices: one app is enough until ~1,000 calls at once or separate teams (docs §5).
- Server-side WebRTC: the browser loopback fixes echo; phone calls don't use WebRTC (ADR 0012).
- LangChain, Pipecat, Redis, Kafka, a vector database: ADR 0002, 0003, 0021.
