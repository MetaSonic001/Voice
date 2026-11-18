"""End to end through the HTTP API with fake models: upload -> index -> search -> ask -> lifecycle, plus the golden-set runner."""

from pathlib import Path

import pytest

KB_V1 = b"""# AU Test Savings Account
## Charges
- Minimum balance requirement is Rs 10,000 average monthly balance.
- Non-maintenance charge is 6% of the shortfall.
## Benefits
- Free debit card with lounge access twice a quarter.

# AU Test Fixed Deposit
## Rates
- Fixed deposit interest rate for senior citizens is 7.60% for 2 years.
"""

KB_V2 = KB_V1.replace(b"Rs 10,000", b"Rs 25,000")


def _index(container):
    container.worker.run_pending()


@pytest.fixture(scope="module")
def doc(client, container):
    r = client.post("/v1/documents", files={"file": ("test-kb.md", KB_V1)}, data={"category": "deposits"})
    assert r.status_code == 201, r.text
    _index(container)
    return r.json()


def test_upload_indexes_and_exposes_chunks(client, doc):
    d = client.get(f"/v1/documents/{doc['id']}").json()
    assert d["searchable"] and d["current_version"] == 1
    assert d["versions"][0]["status"] == "indexed"
    chunks = client.get(f"/v1/documents/{doc['id']}/chunks").json()
    assert chunks["count"] >= 2
    assert any("AU Test Fixed Deposit" in c["section_path"] for c in chunks["chunks"])


def test_same_file_is_not_stored_twice(client, doc):
    again = client.post("/v1/documents", files={"file": ("copy.md", KB_V1)}).json()
    assert again["deduplicated"] and again["id"] == doc["id"]


def test_ask_answers_with_citation_and_trace(client, doc):
    reply = client.post("/v1/ask", json={"question": "What is the minimum balance requirement for the savings account?"}).json()
    assert reply["outcome"] == "answered", reply
    assert "10,000" in reply["answer"] and reply["citations"]
    assert "ten thousand rupees" in reply["speech_text"]
    trace = client.get(f"/v1/traces/{reply['trace_id']}").json()
    assert trace["data"]["retrieval"]["reranked"]
    assert trace["data"]["checks"] and all(not c["unsupported_numbers"] for c in trace["data"]["checks"])
    assert "total_ms" in trace["data"]["timings"]


def test_personal_and_human_requests_are_routed(client, doc):
    personal = client.post("/v1/ask", json={"question": "What is my account balance?"}).json()
    assert personal["outcome"] == "needs_verification" and personal["route"] == "auth_required"
    human = client.post("/v1/ask", json={"question": "Connect me to a human agent"}).json()
    assert human["outcome"] == "handoff"


def test_unrelated_question_is_refused(client, doc):
    reply = client.post("/v1/ask", json={"question": "Who won the cricket world cup?"}).json()
    assert reply["outcome"] == "refused" and reply["reason"] == "low_relevance"


def test_sessions_keep_history(client, doc):
    session = client.post("/v1/sessions", json={}).json()["id"]
    client.post("/v1/ask", json={"question": "minimum balance requirement savings account", "session_id": session})
    s = client.get(f"/v1/sessions/{session}").json()
    assert len(s["turns"]) == 1


def test_replace_disable_rollback_delete(client, container, doc):
    q = {"question": "What is the minimum balance requirement for the savings account?"}
    r = client.post(f"/v1/documents/{doc['id']}/versions", files={"file": ("test-kb-v2.md", KB_V2)})
    assert r.status_code == 201
    _index(container)
    assert "25,000" in client.post("/v1/ask", json=q).json()["answer"]

    client.post(f"/v1/documents/{doc['id']}/versions/1/activate")
    assert "10,000" in client.post("/v1/ask", json=q).json()["answer"]

    client.patch(f"/v1/documents/{doc['id']}", json={"enabled": False})
    assert client.post("/v1/ask", json=q).json()["outcome"] == "refused"
    client.patch(f"/v1/documents/{doc['id']}", json={"enabled": True})
    assert client.post("/v1/ask", json=q).json()["outcome"] == "answered"

    versions = client.get(f"/v1/documents/{doc['id']}").json()["versions"]
    assert {v["version_no"]: v["status"] for v in versions} == {1: "indexed", 2: "superseded"}


def test_data_purpose_is_never_searchable(client, container):
    r = client.post("/v1/documents", files={"file": ("customers.csv", b"Name,Balance\nAsha,999999\n")},
                    data={"purpose": "data"}).json()
    _index(container)
    assert client.get(f"/v1/documents/{r['id']}").json()["searchable"] is False
    hits = client.post("/v1/search", json={"query": "Asha balance 999999"}).json()["reranked"]
    assert all("Asha" not in h["text"] for h in hits)


def test_bad_uploads_are_rejected(client):
    assert client.post("/v1/documents", files={"file": ("x.exe", b"MZ")}).status_code == 415
    assert client.post("/v1/documents", files={"file": ("empty.md", b"")}).status_code == 400
    assert client.get("/v1/documents/not-a-uuid").status_code == 404


async def test_golden_runner_scores_and_writes_a_report(container, doc):
    """The runner behind evals/golden_run.py: scores each item and writes the Markdown report."""
    golden = container.evals.golden_dir / "mini.yaml"
    golden.write_text(
        "- id: balance\n  q: What is the minimum balance requirement for the savings account?\n  lang: en\n"
        "  expect: answer\n  facts: [['10000']]\n  sources: ['Minimum balance']\n"
        "- id: personal\n  q: What is my account balance?\n  lang: en\n  expect: verify\n", encoding="utf-8")
    run_id = container.evals.start("mini")
    metrics = await container.evals.run(run_id, "mini")
    assert metrics["overall"]["pass_rate"] == 100.0
    run = container.evals.get(run_id)
    assert run["status"] == "done"
    assert "Evaluation run" in Path(run["report_path"]).read_text(encoding="utf-8")
