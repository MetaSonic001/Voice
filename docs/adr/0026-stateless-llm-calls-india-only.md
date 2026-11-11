# ADR-0026: Stateless LLM calls, conversation kept in our database, India-only inference

- **Status:** Accepted
- **Date:** 2026-11-11
- **Deciders:** project owner

## Context

The production LLM will be on AWS Bedrock. Bedrock offers two ways to hold a conversation:

- **Stateless calls** (Converse / ConverseStream, or the Responses API with `store=false`): we send the context every turn.
- **Provider-held state:**
  - the Responses API with `store=true` and `previous_response_id` (kept 30 days by AWS);
  - Bedrock Agents sessions;
  - the session management APIs (preview);
  - AgentCore Memory.

Facts that shape the choice:

- **Tokens.** Each turn sends about 3,100 router tokens: fixed instructions and schema, plus 10–140 tokens of trimmed history and call facts.
- **Billing of stored chains.** A stored chain is billed for the whole conversation again as input on every turn. This is OpenAI's documented behaviour; Bedrock's is assumed to match and still to be confirmed with AWS. On a 20-turn call that's about 144k router input tokens vs about 63k for our approach (an estimate). The gap grows with call length.
- **Two prompts per turn.** Each turn uses two different system prompts (router and answer), and the model is switched mid-call on rate limits (`adapters/resilient.py`).
- **We control what the model sees.** Account data and amounts are hidden from history (`store.history`). OTPs never reach the LLM.
- **Our own record is needed anyway.** The tamper-evident transcript, the delete-call feature and the retention policy all live in our Postgres.
- **Data residency.** Bedrock's `apac.` and global inference profiles can process requests outside India. An India geo profile (Mumbai + Hyderabad) exists for Claude Haiku 4.5, Sonnet 5 and Opus 5 (since 29 Sep 2026) and for OpenAI GPT-5.6. gpt-oss in India is not confirmed.

## Decision

- **Every LLM call is stateless.** On Bedrock, use **Converse / ConverseStream** (the existing `adapters/bedrock.py`), or the Responses API only with `store=false`. Never `store=true`, Bedrock Agents sessions, the session APIs or AgentCore Memory.
- **Postgres stays the only home of the conversation:**
  - `traces` holds the turns and the audit chain;
  - `sessions` holds facts, state, pending question and verification.
  - The model sees only what code chooses: 6 trimmed turns, call facts, focus.
- **Cost is cut with prompt caching** on the fixed router prompt, not with provider-held state.
- **Customer data goes only to India-only model IDs** (an India geo profile; allow ap-south-1 and ap-south-2). Never `apac.` or global profiles.
- **Bedrock invocation logging:** on, but without message text (metadata and token counts only). The text stays in our own traces. Confirm the setting in the console.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Stateless + our Postgres + prompt caching** | Flat cost per turn; we choose what the model sees; model fallback works; one record for audit, retention and erasure; works with every model | We build the prompt each turn (already done) |
| Responses API, `store=true` | Less to send per request | History re-billed every turn (about 2.3× on 20 turns); 30-day copy at AWS outside our retention and delete; can't continue on a fallback model; OpenAI models only |
| Bedrock Agents | Managed sessions and tools | The LLM picks tools, against [0008](0008-llm-understands-code-decides.md); a second store |
| Session APIs / AgentCore Memory | Managed storage | Duplicate `sessions` / `traces`; the session APIs are a preview; extra cost |

## Consequences

- **Easier:**
  - one source of truth for compliance (DPDP storage limits and erasure, RBI outsourcing inventory);
  - provider and model swaps need no data migration;
  - any brain copy can serve the next turn.
- **Harder / costs:** none new. The prompt building already exists.
- **Revisit when:** a provider offers held state that is billed only for new tokens, kept under our retention and erasure control, and in India. Even then, our own record stays.

## Links

`brain/app/adapters/bedrock.py`, `brain/app/services/store.py`, `brain/app/adapters/resilient.py`, `docs/briefing-security-state-scale.md` §6–7, [0007](0007-two-groq-models-behind-a-port.md), [0021](0021-no-redis-kafka-yet.md).
