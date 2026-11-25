"""RERANK=false: questions are answered from the hybrid-search order without calling the reranker, and the refusal gate
uses the embedding similarity (MIN_DENSE_SCORE)."""

import pytest

from app.adapters.models import NoReranker
from app.domain import Hit
from app.services.retrieval import without_reranker

KB = b"""# AU Lab Savings Account
## Charges
- Minimum balance requirement for the AU Lab Savings Account is Rs 7,500 average monthly balance.
"""
QUESTION = {"question": "What is the minimum balance requirement for the AU Lab Savings Account?"}


class MustNotRun:
    name, device = "must-not-run", "-"

    def score(self, query, passages, batch_size=16):
        raise AssertionError("the reranker was called with RERANK=false")


@pytest.fixture
def rerank_off(client, container):
    r = client.post("/api/v1/documents", files={"file": ("lab-savings.md", KB)}, data={"category": "deposits"})
    assert r.status_code in (200, 201), r.text  # the second test gets the same file back (deduplicated)
    container.worker.run_pending()
    s, retriever = container.settings, container.retriever
    saved = s.rerank, s.min_dense_score, retriever.reranker
    s.rerank, retriever.reranker = False, MustNotRun()
    yield s
    s.rerank, s.min_dense_score, retriever.reranker = saved
    # Take the test product out again: in the shared KB a second "savings account" makes later savings questions ask
    # "which one?" (variant gate), which broke test_voice_api when the whole suite ran.
    doc_id = r.json().get("document_id") or r.json().get("id")
    if doc_id:
        client.delete(f"/api/v1/documents/{doc_id}")


def test_answers_without_calling_the_reranker(client, rerank_off):
    reply = client.post("/api/v1/ask", json=QUESTION).json()
    assert reply["outcome"] == "answered", reply
    trace = client.get(f"/api/v1/traces/{reply['trace_id']}").json()["data"]
    assert trace["retrieval"]["reranker"] == "off" and trace["retrieval"]["closest_dense"] > 0
    assert "rerank_ms" not in trace["timings"]
    scores = [h["rerank_score"] for h in trace["retrieval"]["reranked"]]
    assert scores == sorted(scores, reverse=True) and 0 < scores[0] <= 1  # the RRF order, scaled to 0..1
    assert trace["grounding"]["level"] == "medium"  # never "high" without a relevance score


def test_refuses_when_nothing_is_close_by_meaning(client, rerank_off):
    rerank_off.min_dense_score = 0.999
    reply = client.post("/api/v1/ask", json=QUESTION).json()
    assert reply["outcome"] == "refused", reply


def test_without_reranker_keeps_order_and_scales():
    hits = [Hit(chunk_id=str(i), text="t", section_path="s", document_id="d", document_title="t", version_id="v",
                version_no=1, rrf=r) for i, r in enumerate([2 / 61, 1 / 61, 1 / 90])]
    out = without_reranker(hits)
    assert [h.chunk_id for h in out] == ["0", "1", "2"]
    assert out[0].rerank_score == 1.0 and out[1].rerank_score == 0.5 and 0 < out[2].rerank_score < 0.5


def test_no_reranker_stub():
    assert NoReranker().score("q", ["a", "b"]) == [0.0, 0.0] and NoReranker.name == "off"
