# ADR-0029: ONNX Runtime as an option for the search models on CPU-only laptops

- **Status:** Proposed (built 20 Nov 2026; the owner decides whether to keep it)
- **Date:** 2026-11-20
- **Deciders:** project owner

## Context

The office laptop has no GPU and blocks `.bin` downloads. The search models (bge-m3, bge-reranker-v2-m3) ran only through
PyTorch. On a CPU, reranking 16 passages takes 13-20 s per knowledge-base question (Ryzen 7 5800H); on the RTX 3050
it takes ~0.5 s. The `.bin` problem is already solved without ONNX: `download_models.py` fetches `.safetensors` only.

## Decision

Add `SEARCH_RUNTIME=onnx` (default stays `torch`): the same two models as ONNX files, run by Microsoft's ONNX Runtime on
the CPU (`OnnxEmbedder`, `OnnxReranker` in `app/adapters/models.py`). `download_models.py --onnx` fetches bge-m3 from
BAAI's own `onnx/` folder and the reranker from onnx-community (Hugging Face's converter, full precision), both pinned to
one commit and checked by SHA-256. AWS is unchanged (PyTorch on a GPU behind SageMaker).

## Measured (20 Nov 2026, CPU only, 12 KB questions in English, Hindi and Hinglish, the real search on the real KB)

| | PyTorch fp32 | ONNX fp32 | ONNX int8 (not wired in) | 8 candidates instead of 16 |
|---|---|---|---|---|
| Query embedding | 232 ms | **80 ms** (2.9x) | - | - |
| Rerank 16 passages | ~17-20 s | ~17-18 s (no gain) | ~11 s (1.8x) | ~10 s (2.1x) |
| Same top-5, same order | - | **12/12, scores equal to 5 decimals** | top-1 12/12, top-5 4.92/5, scores differ up to 0.16 | top-1 11/12, top-5 3.75/5 |

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **ONNX fp32 (built)** | Identical results; embedding 3x faster; no `.bin`; BAAI's own embedding file | A new library (`onnxruntime`); reranking is no faster, so KB answers stay slow on CPU |
| ONNX int8 reranker | 1.8x faster reranking | Scores change: needs `MIN_RERANK_SCORE` re-tuned and the smoke + golden sets re-run; still ~11 s |
| Fewer rerank candidates (setting only) | 2x faster, no code | Worse top-5 (3.75/5 the same) |
| GGUF (llama.cpp) | Quantised, small | A native library, third-party files, scores change |
| A GPU (laptop or AWS) | ~0.5 s | Hardware |

## Consequences

- **Easier:** the office laptop can run search with no `.bin` and no PyTorch model files; KB ingestion embeds ~3x faster.
- **Harder / costs:** one more dependency (`onnxruntime` + `flatbuffers`, `protobuf`) for the security team; two more model folders (4.5 GB).
- **Not solved:** a KB answer on a CPU still waits ~10-20 s for the reranker. Only a GPU makes it voice-fast.
- **Revisit when:** the owner decides whether CPU-only is a real target (then test int8 with the evals) or not (then remove this option).

## Links

`brain/app/adapters/models.py`, `brain/app/container.py` (`build_search`), `brain/scripts/download_models.py`, ADR 0028 (AWS layout).
