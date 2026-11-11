# Architecture Decision Records (ADRs)

An **ADR** is a short note that records **one** important technical decision: the situation, what we chose, what else we considered, and what follows from it. The idea comes from Michael Nygard (2011). We use a light version of **MADR** (Markdown Architectural Decision Records): Nygard's sections plus "Options considered".

## How we use them

- **One decision per file:** `NNNN-short-title.md`, numbered in order, never renumbered.
- **Status:**
  - **Proposed:** written, not yet agreed.
  - **Accepted:** agreed; the code follows it.
  - **Deprecated:** no longer relevant.
  - **Superseded by NNNN:** replaced by a newer ADR.
- **Don't rewrite an accepted ADR to change the decision.** Write a new ADR and mark the old one *Superseded*. Small clarifications and corrections are fine.
- **When to write one:** a choice that is expensive to reverse, affects several parts of the system, or that someone will later ask "why did we do it this way?" about. Not for small code choices.
- **Template:** [`template.md`](template.md).

ADRs 0001–0021 were **recorded on 6 Nov 2026 for decisions made earlier** (2–6 Nov), taken from the plans, `PROJECT-GUIDE.md` and the owner's instructions. The dates show when each decision was actually made. Where the Phase 4 build differs from an ADR, the ADR has an "Implementation notes" section; ADR 0023 was written during the build.

## Index

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
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
| [0012](0012-websocket-pcm-not-webrtc.md) | Browser test calls over WebSocket audio, not WebRTC | Accepted |
| [0013](0013-speaker-echo-not-fixed.md) | Browser speaker echo: not fixed; use earphones | Accepted |
| [0014](0014-tamper-evident-audit-encrypted-recordings.md) | Tamper-evident audit chain and encrypted recordings | Accepted |
| [0015](0015-pii-redaction-regex-checksums.md) | PII redaction with rules and checksums, not an ML model | Accepted |
| [0016](0016-card-actions-confirmation-access-levels.md) | Bank actions: access levels per task, explicit "yes", read-back | Accepted |
| [0017](0017-one-agent-departments-as-human-queues.md) | One agent for all departments; departments are queues for people | Accepted |
| [0018](0018-document-level-kb-metadata.md) | Document-level KB tags; conflicts settled in code; fix stale data at source | Accepted |
| [0019](0019-tiered-testing-replay-cache.md) | Tiered testing with a replay cache (to fit Groq's free limits) | Accepted |
| [0020](0020-post-call-review-selective-judge.md) | After-call review: code checks always, LLM judge for every call in development, selectively in production | Accepted |
| [0021](0021-no-redis-kafka-yet.md) | No Redis or Kafka for now; when to add them | Accepted |
| [0022](0022-rag-at-large-scale.md) | Keeping retrieval fast and accurate at ~50,000 pages | Proposed |
| [0023](0023-templates-not-llm-phrasing-for-bank-data.md) | Bank-data replies stay templates: no LLM phrasing call | Accepted |
| [0024](0024-dialogue-decisions-in-code.md) | Dialogue decisions in code: strategy, guided flows, scoped facts, progressive disclosure | Accepted |
| [0025](0025-selective-bank-reads.md) | Read only the bank data a request needs (build with the real AU adapter) | Accepted (design) |
| [0026](0026-stateless-llm-calls-india-only.md) | Stateless LLM calls; the conversation stays in our database; India-only inference | Accepted |
| [0027](0027-access-control-token-at-bank-rls-with-sso.md) | Customer data guarded at the bank's APIs; database row-level security comes with staff SSO | Accepted |

## Sources on the format

- [Real Python: ADR](https://realpython.com/ref/software-engineering-glossary/architecture-decision-record/)
- [Thunderbird ADR process](https://source-docs.thunderbird.net/en/latest/adr/README.html)
- [Nygard template (clan.lol)](https://clan.lol/docs/26.05/decisions/template)
- [ADR overview](https://quochuydev.github.io/tech-research/researching/architecture-decision-record-overview.html)
