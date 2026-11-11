# ADR-0002: Own small pipeline, few trusted libraries

- **Status:** Accepted
- **Date:** 2026-11-03
- **Deciders:** project owner (manager's rule: few, bank-approvable dependencies)

## Context

Every dependency must pass the bank's security review, and large frameworks bring hundreds of transitive packages. We also need fine control in places frameworks don't give it:
- per-sentence checks while the answer streams;
- interruption handling that pauses, transcribes the overlap, then resumes or stops.

## Decision

Write the RAG, conversation and voice pipelines ourselves, in a few hundred clear lines each. Use only well-known building blocks:
- FastAPI, psycopg + pgvector, pdfplumber;
- PyTorch + Hugging Face models (bge-m3, bge-reranker-v2-m3, Silero VAD);
- the Groq/HTTP clients;
- Next.js + shadcn for the UI.

Prefer the standard library: OOXML parsing, `hmac`, regex, `zipfile`. Each new dependency needs a written reason.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **Own pipeline + few libraries** | Small attack surface; full control; easy to read and test | We maintain the code |
| LangChain / LlamaIndex | Fast to start | Many layers and dependencies; hard to add per-sentence checks |
| Pipecat (voice) | Telephony connectors | Lacks our interruption behaviour; large dependency |
| Docling (parsing) | ML layout parsing | 103 packages; dropped ~5% of the rates PDF in testing |
| Ragas / Langfuse | Ready-made eval and tracing | Our runner is exact on numbers and stores results with the traces |

## Consequences

- **Easier:** security review; debugging; precise behaviour.
- **Harder:** telephony connectors, OCR and similar features must be added deliberately.
- **Revisit when:** a framework solves a real need we can't meet in reasonable code. For example, Pipecat's phone connectors when real lines arrive, or Textract for scanned PDFs.

## Links

`PROJECT-GUIDE.md` §16–17; memory "minimal trusted libraries".
