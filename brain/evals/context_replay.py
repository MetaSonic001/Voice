"""Replay saved golden-set runs to tune which passages reach the answer model. No LLM calls: it reads the reranked
passages each saved trace kept, applies the trimming rules from services/kbmeta.py and checks that the evidence the
golden answer needs (its `facts`, else its `sources`) is still in what would be sent.

    uv run python -m evals.context_replay                      # the saved fixtures in evals/fixtures/ (no brain needed)
    uv run python -m evals.context_replay --grid               # try several thresholds
    uv run python -m evals.context_replay --run <id> --save    # snapshot a golden run from the running brain into a fixture

Prints, per rule: items whose evidence was present before / after, items that LOST evidence (must be 0), and the
average characters sent. Fixtures live in the repo so a clean slate (which deletes call records) never loses them.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

from app.domain import Hit
from app.services import kbmeta
from app.services.evals import load_golden
from app.config import ROOT

API = "http://127.0.0.1:8000"
FIXTURES = Path(__file__).parent / "fixtures"
CONTEXT_CHUNKS = 5


def _norm(text: str) -> str:
    return re.sub(r"[,\s]+", " ", text.lower().replace("rs.", "₹").replace("rs ", "₹")).strip()


def _evidence(item: dict, hits: list[Hit]) -> bool | None:
    hay = _norm(" ".join(f"{h.section_path} {h.text}" for h in hits)).replace(" ", "")
    if item.get("facts"):
        return all(any(_norm(str(a)).replace(" ", "") in hay for a in group) for group in item["facts"])
    if item.get("sources"):
        return any(_norm(s).replace(" ", "") in hay for s in item["sources"])
    return None


def _hits(rows: list[dict]) -> list[Hit]:
    return [Hit(chunk_id=r.get("chunk_id", ""), text=r.get("text") or r.get("snippet", ""), section_path=r.get("section", ""),
                document_id="", document_title=r.get("document", ""), version_id="", version_no=r.get("version") or 0,
                rerank_score=r.get("rerank_score")) for r in rows]


def load(run_id: str | None):
    with httpx.Client(base_url=API, timeout=30) as h:
        if not run_id:
            runs = [r for r in h.get("/v1/evals").json() if r["status"] == "done"]
            run_id = runs[0]["id"]
        run = h.get(f"/v1/evals/{run_id}").json()
        golden = {g["id"]: g for g in load_golden(ROOT / "evals" / "golden" / run.get("golden_file", "v1.yaml"))}
        items = []
        for r in run.get("results") or []:
            if not r.get("trace_id") or r["id"] not in golden:
                continue
            trace = h.get(f"/v1/traces/{r['trace_id']}").json()
            data = trace.get("data") or {}
            reranked = (data.get("retrieval") or {}).get("reranked") or []
            if reranked:
                items.append((golden[r["id"]], _hits(reranked), r))
    return run_id, items


def save(run_id: str, items) -> Path:
    """Write a golden run's saved passages to evals/fixtures/context-<run>.json."""
    FIXTURES.mkdir(exist_ok=True)
    path = FIXTURES / f"context-{run_id[:8]}.json"
    rows = [{"id": item["id"], "reranked": [{"chunk_id": h.chunk_id, "document": h.document_title, "version": h.version_no,
                                             "section": h.section_path, "rerank_score": h.rerank_score, "text": h.text}
                                            for h in hits]} for item, hits, _ in items]
    path.write_text(json.dumps({"run_id": run_id, "golden_file": "v1.yaml", "items": rows}, ensure_ascii=False), encoding="utf-8")
    return path


def load_fixture(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    golden = {g["id"]: g for g in load_golden(ROOT / "evals" / "golden" / data.get("golden_file", "v1.yaml"))}
    items = [(golden[x["id"]], _hits(x["reranked"]), x) for x in data["items"] if x["id"] in golden]
    return data["run_id"], items


def evaluate(items, rel: float, floor: float, identity: bool):
    kbmeta.REL_TO_BEST, kbmeta.ABS_FLOOR = rel, floor
    before_ok = after_ok = lost = 0
    chars_before = chars_after = n = 0
    lost_ids = []
    for item, hits, _ in items:
        base = [h for h in hits[:CONTEXT_CHUNKS] if (h.rerank_score or 0) >= 0.015]  # today's rule (min_rerank_score * 0.5)
        if not base:
            continue
        names = kbmeta.named_products(item["q"], [], hits) if identity else []
        trimmed, _ = kbmeta.trim_context(base, names, "", item["q"])
        e0, e1 = _evidence(item, base), _evidence(item, trimmed)
        if e0 is None:
            continue
        n += 1
        before_ok += bool(e0)
        after_ok += bool(e1)
        if e0 and not e1:
            lost += 1
            lost_ids.append(item["id"])
        chars_before += sum(len(h.text) for h in base)
        chars_after += sum(len(h.text) for h in trimmed)
    return {"rel": rel, "floor": floor, "identity": identity, "items": n, "evidence_before": before_ok,
            "evidence_after": after_ok, "lost": lost, "lost_ids": lost_ids,
            "avg_chars_before": round(chars_before / max(n, 1)), "avg_chars_after": round(chars_after / max(n, 1))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="read this golden run from the running brain instead of the fixtures")
    ap.add_argument("--save", action="store_true", help="with --run: also save it as a fixture")
    ap.add_argument("--grid", action="store_true", help="try several thresholds")
    args = ap.parse_args()
    if args.run:
        run_id, items = load(args.run)
        sources = [(run_id, items)]
        if args.save:
            print(f"saved {save(run_id, items)}", file=sys.stderr)
    else:
        sources = [load_fixture(p) for p in sorted(FIXTURES.glob("context-*.json"))]
        if not sources:
            sys.exit("no fixtures in evals/fixtures; use --run <id> --save with the brain running")
    grid = [(r, f, i) for r in (0.0, 0.2, 0.3, 0.4) for f in (0.0, 0.05, 0.1) for i in (False, True)] if args.grid else [
        (kbmeta.REL_TO_BEST, kbmeta.ABS_FLOOR, True)]
    for run_id, items in sources:
        print(f"run {run_id}: {len(items)} items with saved passages", file=sys.stderr)
        for rel, floor, identity in grid:
            print(evaluate(items, rel, floor, identity))


if __name__ == "__main__":
    main()
