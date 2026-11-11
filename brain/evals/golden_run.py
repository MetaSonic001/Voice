"""Golden-set run on the real LLM, replay-cached and paced under Groq's tokens-per-minute limit (T3 in PHASE-4-PLAN §10).

  python -m evals.golden_run [v1]          takes about an hour on the free tier the first time; a re-run with nothing changed is free

Uses the in-process pipeline (not the running API), so it measures the code in this folder. Prints the summary and the report path.
"""

import asyncio
import json
import sys

from app.config import Settings
from app.container import build
from evals.benchmark import HERE, Pacer, Patient


class Paced:
    """Waits for room under the per-model token limit before every real request (a cache hit never gets here)."""

    def __init__(self, llm, pacer: Pacer):
        self.llm, self.pacer = llm, pacer

    async def json(self, model, system, user, schema, max_tokens=400):
        await self.pacer.wait(model, 3800)
        data, meta = await self.llm.json(model, system, user, schema, max_tokens)
        self.pacer.add(model, meta)
        return data, meta

    async def stream(self, model, messages, meta, max_tokens=400):
        await self.pacer.wait(model, 3400)
        async for delta in self.llm.stream(model, messages, meta, max_tokens):
            yield delta
        self.pacer.add(model, meta)

    async def close(self):
        await self.llm.close()


async def main(golden: str) -> None:
    s = Settings(llm_replay_dir=HERE / "cache", judge_mode="off")
    c = build(s, start_worker=False)
    c.llm.inner = Paced(Patient(c.llm.inner), Pacer())
    run_id = c.evals.start(golden)
    metrics = await c.evals.run(run_id, golden)
    print(json.dumps({"overall": metrics["overall"], "answered_latency_ms": metrics.get("answered_total_ms"),
                      "by_language": metrics.get("by_language"), "llm_requests": c.llm.misses, "cache_hits": c.llm.hits}, indent=1))
    print("report:", c.evals.get(run_id)["report_path"])
    await c.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "v1"))
