# Pending work (25 Nov 2026)

What is left, in three lists. Tick items here and log them in `progress-tracker.md`. The detail is in
`reference/PROJECT-GUIDE.md` §26 (going live) and the ADRs.

## A. Company laptop (development and checks)

| # | Item | Why |
|---|---|---|
| A1 | Run `pytest`, then the smoke set on Bedrock Luna (`python -m evals.smoke`), then the regression pack | No real-LLM numbers on Bedrock yet; nothing may get worse (last: 19/24 on Groq, 17 Nov) |
| A2 | Measure first-audio latency on `/bench` (pin the setup first); try `ANSWER_REASONING_EFFORT=none` | Only one measurement exists (12 Nov, Groq) |
| A3 | Watch `MIN_DENSE_SCORE` on real Hindi / Hinglish calls (`/calls`: "closest passage by meaning") | Checked offline 25 Nov (0.35 never refuses a golden question); confirm on live calls |
| A4 | Optional: `SEARCH_RUNTIME=onnx` for a faster query embedding on the CPU | ~3x faster embedding (ADR 0029); check it on `/bench` |
| A5 | Build the images once (`docker compose up -d --build`) | Proves the Dockerfiles before ECR |
| A6 | Live voice checks: a pause mid-sentence, a stop word while the agent speaks, the opener | Fixed in code, not heard live |
| A7 | Known bug: an English turn answered in Hinglish after a Hindi turn | Fixed in code 23 Nov (`reply_language`); confirm live |
| A8 | Open product questions | Tracker, "Open questions" |

## B. UAT on AWS EKS (with AU's infra team)

| # | Item | Notes |
|---|---|---|
| B1 | Infra creates: ECR `au-voice`, EKS + 1 `m7i.xlarge` node and the add-ons, RDS, S3, secret `au/brain`, the IAM role, domain + certificate | `deploy/README.md` §1 |
| B2 | Run the two database SQL files; put `DATABASE_URL` in the secret | `deploy/README.md` §2 |
| B3 | Build and push `brain-<v>` (`SEARCH_MODELS=embed`) and `ui-<v>`; fill in the placeholders in `deploy/eks/*.yaml`; apply | §3–4 |
| B4 | First deploy checks: start-up time, memory used (requests 6 Gi), that a live call survives a deploy within the 300 s grace, logs in CloudWatch | Not verified anywhere yet |
| B5 | CloudWatch metric filter + alarm on `alert` log lines | AWS setup only |

## C. Before production

| # | Item | Why |
|---|---|---|
| C1 | One keypad-code secret and rate limits shared by all brain copies (today in memory) | Needed for 2+ copies; Secrets Manager or Postgres. Decide Redis with it (ADR 0021) |
| C2 | Cisco telephony adapter (audio in and out, keypad digits, transfer back with call variables) | Needs AU's Cisco details |
| C3 | Sarvam STT as a live stream (and WebSocket TTS) | Today each utterance is uploaded after it ends (adds delay) |
| C4 | Tune voice detection and turn-taking on 8 kHz phone audio | Browser tests use 16 kHz |
| C5 | Real AU bank adapter with selective reads (ADR 0025) | Replaces the mock bank |
| C6 | Hand-over to AU's agents through Cisco; staff SSO with roles, then row-level security (ADR 0027) | |
| C7 | After-call work in the `jobs` table; bigger thread and Bedrock pools; each copy's share of vendor limits | PROJECT-GUIDE §26.6 |
| C8 | A GPU for the reranker (GPU node or `SEARCH_PROVIDER=http`), if UAT answers need it | `RERANK=auto` turns it on; no code change |
| C9 | Guardrails and security: red-team set, kill switches, WAF, VAPT, CERT-In runbook, AU's approved wording | PROJECT-GUIDE §26 |
| C10 | Multi-AZ RDS, two nodes in two zones, private endpoints; load test; cost sheet for production volumes | AU's volumes first |

## Not planned (on purpose)

- Microservices, LangChain, Pipecat, Kafka, a separate vector database (ADR 0002, 0003, 0021).
- An LLM reranking call, or a small CPU reranker: measured, slower and no better (ADR 0031).
- Server-side WebRTC: phone calls don't use it (ADR 0012).
