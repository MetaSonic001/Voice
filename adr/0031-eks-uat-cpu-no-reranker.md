# ADR-0031: UAT on EKS (one CPU node), no GPU, no reranker, API under /api

- **Status:** Accepted. Supersedes [0028](0028-ecs-on-ec2-for-the-brain.md) and [0030](0030-ui-on-ecs-ec2.md) (the ECS plan of 19–23 Nov; changed because AU's platform is EKS)
- **Date:** 2026-11-25
- **Deciders:** project owner, with AU's infrastructure team

## Context

- AU's infrastructure team runs **EKS**, not ECS. They asked for a small UAT first: one server, no GPU, no SageMaker.
- UAT setup:
  - one ECR repository with two images (`brain-<version>`, `ui-<version>`);
  - EKS with one `m7i.xlarge` node (4 vCPU, 16 GB);
  - RDS PostgreSQL with pgvector;
  - S3, Secrets Manager, CloudWatch Logs;
  - Bedrock with one model, GPT-5.6 Luna (`in.openai.gpt-5.6-luna`, India profile).
- Cost estimate: about ₹37,800 a month with GST (Google Sheet, tab "UAT").
- Without a GPU, the cross-encoder reranker (bge-reranker-v2-m3, 568M parameters) takes 13–37 s for 16 passages. It can't run on the call path.
- The load balancer needs one rule that tells the API from the UI.

## Decision

1. **EKS for UAT.**
   - One brain pod (`replicas: 1`, `strategy: Recreate`), one UI pod, one internal ALB through the AWS Load Balancer Controller.
   - Secrets come from Secrets Manager through the Secrets Store CSI Driver.
   - One IAM role, given to the brain through EKS Pod Identity.
   - Logs go from stdout to CloudWatch Logs through the CloudWatch Observability add-on.
   - Files: `deploy/eks/` (brain, ui, ingress, secrets). `deploy/ecs/` is removed.
2. **Every API route is under `/api`** (`/api/v1/...`). The ALB rule is `/api/*` → brain (port 8000), everything else → UI (port 8080).
   - `/health`, `/health/ready` and `/metrics` stay at the root, because probes reach the pod directly.
   - The UI's health path is `/ui-health`.
3. **`RERANK=auto` (new default).** The reranker is on only where it is fast: a GPU in the process, or `SEARCH_PROVIDER=http|sagemaker`. On a CPU it is off:
   - RRF order;
   - the refusal gate `MIN_DENSE_SCORE`;
   - **6 passages** to the answer model (`CONTEXT_CHUNKS_RRF`), instead of 5.

   The answer model already picks the passages it uses and cites each sentence. No extra LLM call is added.
4. The UAT brain image holds **bge-m3 only** (`SEARCH_MODELS=embed`). That's 2.2 GB less to pull.

## Measured (25 Nov, owner's laptop, offline, no LLM)

The 52 English golden questions that have known sources. 16 RRF candidates each. "hit@k" means a passage with the expected source text is in the top k.

| Order | hit@1 | hit@3 | hit@5 | hit@6 | CPU time per question |
|---|---|---|---|---|---|
| RRF only | 57.7% | 80.8% | 92.3% | **96.2%** | 0 |
| bge-reranker-v2-m3 (on the GPU) | 78.8% | 88.5% | 98.1% | | 13–37 s on a CPU |
| ms-marco-MiniLM-L6-v2 (CPU) | 71.2% | 88.5% | 90.4% | | ~900 ms (p95 978) |

- RRF hit@7 and hit@8 are also 96.2%, and hit@10 is 98.1%. Six passages close most of the gap to the reranker's top 5.
- Hindi and Hinglish, searched with the caller's own words (the worst case; real calls search with the LLM's English
  rewrite, which behaves like the English rows): Hinglish RRF hit@6 100% (13 questions; reranker hit@5 92.3%), Hindi
  RRF hit@6 85.7% (12 of 14; reranker 100%).
- Refusal gate (`MIN_DENSE_SCORE` 0.35), all 105 golden questions: answerable ones score 0.48–0.78 (closest passage in
  the top 6), unanswerable ones 0.48–0.67. 0.35 never refuses a good question; no threshold separates the two groups,
  so "the KB doesn't say" stays with the answer model (NO_ANSWER) and the figure check.
- Answer accuracy with a real LLM is still to run: the smoke set on Bedrock, on the company laptop.

## Options considered

| Option | Pros | Cons |
|---|---|---|
| **RRF → 6 passages → the answer model picks and cites (chosen)** | No extra model or call. Measured 96.2% hit@6 | Less precise first passage (hit@1 57.7%), so an answer can lean on a near miss. The figure check still blocks invented numbers |
| A small CPU cross-encoder (MiniLM-L6) | Better hit@1 | ~900 ms on our long passages (the 31 ms in public benchmarks was for shorter passages on an M-series Mac). Worse hit@5 than RRF. English only |
| An LLM reranking call before the answer | Good selection | One more LLM round trip (~0.5–1.5 s) on every KB turn |
| Hand-written ranking rules | Fast | Fragile. Product and variant rules already exist (`select_sources`, `trim_context`, variant gate) |
| ECS on EC2 servers (ADR 0028 / 0030) | Written and costed; images cached on the servers | Not AU's platform |

## Consequences

- **Easier:**
  - UAT runs on one CPU node with no GPU costs;
  - the same code runs on the owner's GPU laptop (reranker on), the company laptop and EKS (off) with no setting change;
  - one ALB rule.
- **Harder / costs:**
  - KB answers are a little less precise without the reranker;
  - one brain copy, so a deploy is a 1–2 minute break and a node failure 5–10 minutes (UAT only);
  - API clients use `/api/v1/...`.
- **Revisit when:**
  - a GPU is available (the reranker comes back with `SEARCH_PROVIDER=http` or a GPU node: no code change);
  - B2.3 is fixed (shared keypad-code state), so the brain can run more than one copy;
  - the Bedrock smoke set shows KB answers got worse.

## Links

`deploy/eks/`, `deploy/README.md`, `brain/app/config.py` (`rerank`, `context_chunks_rrf`), `brain/app/main.py` (`API_PREFIX`), `compose.yaml` + `compose.gpu.yaml`.
