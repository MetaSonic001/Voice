# ADR-0007: Two LLMs on Groq behind a swappable interface

- **Status:** Accepted (development); production provider still to be decided
- **Date:** 2026-11-03
- **Deciders:** project owner

## Context

Each caller turn needs:
1. fast **understanding**: language, intent, scope, entities, as strict JSON;
2. a careful **answer**, streamed sentence by sentence.

The voice budget is about 1 s to the first word. In production the LLM must probably be India-hosted or bank-approved. For development we need something fast and free.

## Decision

- **Understanding:** `openai/gpt-oss-20b` on **Groq**, strict JSON schema (≈ 0.9 s p50). Fields that aren't needed are nullable, so the model writes less.
- **Answers:** `openai/gpt-oss-120b` on Groq, streamed.
- Both sit behind the **`LLM` port**. Providers are adapters chosen in config; a Bedrock adapter exists. Production (an India-hosted or bank-approved model, or self-hosted vLLM) is a config change plus evaluation, not a rewrite.
- The API key lives only in `brain/.env` as a secret setting and is never logged.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Groq, two sizes** | Very low latency; free tier for development; open-weight models (self-hostable later) | Free tier: 8k tokens/min and 1,000 requests/day per model; US-hosted |
| One large model for both | Simpler | Slower understanding; more quota |
| OpenAI / Anthropic APIs | Strong quality | Cost; data location; not open-weight |
| Self-hosted now | Data stays local | Needs GPUs we don't have (4 GB laptop GPU) |

## Consequences

- **Easier:** fast development; providers can be swapped.
- **Harder:** the rate limits shape testing ([0019](0019-tiered-testing-replay-cache.md)) and need a fallback (PHASE-4-PLAN §7.3). Groq is not a production answer for data residency.
- **Revisit when:** choosing the production LLM. Any candidate must pass the same eval suite before switching.

## Implementation notes (11 Nov 2026)

- The owner's `.env` now runs **both** the router and the answer step on `gpt-oss-120b`, so they share one quota. The `config.py` default for the router is still 20b. `gpt-oss-20b` is the post-call judge's model and the fallback (`LLM_FALLBACK`).
- Production: stateless calls on Bedrock with India-only model IDs, see [0026](0026-stateless-llm-calls-india-only.md).

## Links

`brain/app/adapters/llm.py`, `brain/app/adapters/bedrock.py`, `PROJECT-GUIDE.md` §18.
