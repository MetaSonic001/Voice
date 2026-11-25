# Architecture Decision Records (ADRs)

An **ADR** is a short note that records **one** important technical decision: the situation, what we chose, what else we considered, and what follows from it. The idea comes from Michael Nygard (2011). We use a light version of **MADR** (Markdown Architectural Decision Records): Nygard's sections plus "Options considered".

## How we use them

- **One decision per file:** `NNNN-short-title.md`, numbered in order, never renumbered.
- **Status:**
  - **Proposed:** written, not yet agreed.
  - **Accepted:** agreed; the code follows it.
  - **Superseded by NNNN:** replaced by a newer ADR (kept for the reasoning).
  - **Deprecated:** no longer relevant.
- **Don't rewrite an accepted ADR to change the decision.** Write a new ADR and mark the old one *Superseded*. Small clarifications and corrections are fine. Superseded ADRs stay: they record why the earlier option was dropped, and the repository may move without its git history.
- **Keep the set small.** One file per decision that really stands on its own. When two ADRs record the same decision, or one only replaced another, merge them into the lower number and keep the history in its text. Removed numbers are never reused.
- **When to write one:** a choice that is expensive to reverse, affects several parts of the system, or that someone will later ask "why did we do it this way?" about. Not for small code choices.
- **Template:** [`template.md`](template.md).

**Why ADRs exist here** (formerly ADR 0001): decisions were spread across chats, planning documents and the guide. Work continues across separate Claude Code chats, and AU's architecture and security teams will review the POC; both need the "why" in one place, versioned with the code. `reference/PROJECT-GUIDE.md` explains *how* things work; ADRs record the options and trade-offs.

ADRs 0002–0021 were **recorded on 6 Nov 2026 for decisions made earlier** (2–6 Nov). The dates show when each decision was actually made. Where the Phase 4 build differs from an ADR, the ADR has an "Implementation notes" section.

**Merged on 14 Nov 2026** (the owner asked for no unnecessary ADRs): 0001 (record ADRs) → this README; 0013 (speaker echo not fixed) and the first 0028 (echo fixed with a loopback) → 0012 (the number 0028 was then reused on 19 Nov for the ECS decision); 0023 (bank-data templates) → 0024. Older documents that cite those numbers mean the ADR they were merged into.

## Index

| # | Decision | Status |
|---|---|---|
| [0002](0002-own-pipeline-minimal-libraries.md) | Own small pipeline, few trusted libraries (no LangChain, Pipecat, Docling) | Accepted |
| [0003](0003-postgres-pgvector-single-store.md) | One Postgres with pgvector for documents, vectors, calls and audit | Accepted |
| [0004](0004-hybrid-retrieval-rrf-rerank.md) | Hybrid retrieval: vectors + keywords, merged by RRF, then a reranker | Accepted |
| [0005](0005-structure-aware-chunking-native-parsers.md) | Structure-aware chunking with native parsers; no OCR | Accepted |
| [0006](0006-grounded-answers-decisions-figure-check.md) | Grounded answers: four answer decisions plus a figure check in code | Accepted |
| [0007](0007-two-groq-models-behind-a-port.md) | Two LLMs on Groq (small for understanding, large for answers) behind a swappable interface | Accepted |
| [0008](0008-llm-understands-code-decides.md) | The LLM understands; code decides and acts (no LLM tool-calling loop) | Accepted |
| [0009](0009-ports-and-adapters-mock-bank.md) | Ports and adapters; a realistic mock bank now, AU's APIs later | Accepted |
| [0010](0010-verification-keypad-otp.md) | Caller verification: registered mobile + keypad code; codes never reach STT, LLM or logs | Accepted |
| [0011](0011-own-voice-engine-cascaded.md) | Own voice engine on a cascaded pipeline (speech-to-text → LLM → text-to-speech) | Accepted |
| [0012](0012-websocket-pcm-not-webrtc.md) | Browser test calls: WebSocket audio to the brain, WebRTC loopback for echo | Accepted |
| [0014](0014-tamper-evident-audit-encrypted-recordings.md) | Tamper-evident audit chain and encrypted recordings | Accepted |
| [0015](0015-pii-redaction-regex-checksums.md) | PII redaction with rules and checksums, not an ML model | Accepted |
| [0016](0016-card-actions-confirmation-access-levels.md) | Bank actions: access levels per task, explicit "yes", read-back | Accepted |
| [0017](0017-one-agent-departments-as-human-queues.md) | One agent for all departments; departments are queues for people | Accepted |
| [0018](0018-document-level-kb-metadata.md) | Document-level KB tags; conflicts settled in code; fix stale data at source | Accepted |
| [0019](0019-tiered-testing-replay-cache.md) | Tiered testing with a replay cache (to fit Groq's free limits) | Accepted |
| [0020](0020-post-call-review-selective-judge.md) | After-call review: code checks always, LLM judge for every call in development, selectively in production | Accepted |
| [0021](0021-no-redis-kafka-yet.md) | No Redis or Kafka for now; when to add them | Accepted |
| [0022](0022-rag-at-large-scale.md) | Keeping retrieval fast and accurate at ~50,000 pages | Proposed |
| [0024](0024-dialogue-decisions-in-code.md) | How the agent replies: dialogue decisions in code; bank-data replies stay templates | Accepted |
| [0025](0025-selective-bank-reads.md) | Read only the bank data a request needs (build with the real AU adapter) | Accepted (design) |
| [0026](0026-stateless-llm-calls-india-only.md) | Stateless LLM calls; the conversation stays in our database; India-only inference | Accepted |
| [0027](0027-access-control-token-at-bank-rls-with-sso.md) | Customer data guarded at the bank's APIs; database row-level security comes with staff SSO | Accepted |
| [0028](0028-ecs-on-ec2-for-the-brain.md) | Brain on ECS with EC2 capacity (Managed Instances), UI on Fargate, search on SageMaker | Superseded by 0031 |
| [0029](0029-onnx-runtime-for-cpu-search.md) | ONNX Runtime as an option for the search models on CPU-only machines | Accepted |
| [0030](0030-ui-on-ecs-ec2.md) | UI on ECS with EC2 capacity (own `t3.small` servers), not Fargate | Superseded by 0031 |
| [0031](0031-eks-uat-cpu-no-reranker.md) | UAT on EKS (one CPU node), no GPU: reranker off by default (`RERANK=auto`), 6 RRF passages; API under `/api` | Accepted |

## Sources on the format

- [Real Python: ADR](https://realpython.com/ref/software-engineering-glossary/architecture-decision-record/)
- [Thunderbird ADR process](https://source-docs.thunderbird.net/en/latest/adr/README.html)
- [Nygard template (clan.lol)](https://clan.lol/docs/26.05/decisions/template)
- [ADR overview](https://quochuydev.github.io/tech-research/researching/architecture-decision-record-overview.html)
