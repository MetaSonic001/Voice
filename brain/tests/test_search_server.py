"""SEARCH_PROVIDER=http: the GPU search server and the brain's adapter, with fake models on a real local socket."""

import socket
import threading
import time

import pytest
import uvicorn

from app.adapters.search_http import INGEST_BATCH, HttpEmbedder, HttpReranker, SearchClient, SearchError
from app.search_server import check_bind, create_app
from tests.fakes import FakeEmbedder, FakeReranker

TOKEN = "test-search-token"
KB = b"""# AU Test Savings Account
## Charges
- Minimum balance requirement is Rs 10,000 average monthly balance.
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_url():
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(FakeEmbedder(), FakeReranker(), TOKEN), host="127.0.0.1",
                                           port=port, log_config=None, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(5)


def _client(url: str, token: str = TOKEN, **expected) -> SearchClient:
    e = FakeEmbedder()
    kw = {"embed_model": e.name, "dim": e.dim, "rerank_model": FakeReranker.name} | expected
    return SearchClient(url, token, timeout_s=2, **kw)


def test_same_vectors_and_scores_as_in_process(server_url):
    client = _client(server_url)
    embedder, reranker = HttpEmbedder(client), HttpReranker(client)
    texts = ["minimum balance savings account", "senior citizen FD rate", "डेबिट कार्ड ब्लॉक"]
    assert embedder.embed(texts) == FakeEmbedder().embed(texts)  # exact, not approximately
    assert embedder.embed_query(texts[0]) == FakeEmbedder().embed_query(texts[0])
    assert reranker.score("minimum balance", texts) == FakeReranker().score("minimum balance", texts)
    assert reranker.score("anything", []) == []
    assert (embedder.name, embedder.dim, reranker.name) == ("fake-embedder", 64, "fake-reranker")
    assert client.device.startswith("remote:")
    assert client.ping() == "ok"


def test_indexing_is_sent_in_batches(server_url):
    texts = [f"chunk number {i}" for i in range(INGEST_BATCH * 2 + 5)]
    assert HttpEmbedder(_client(server_url)).embed(texts) == FakeEmbedder().embed(texts)


def test_wrong_token_is_refused(server_url):
    with pytest.raises(SearchError, match="token"):
        HttpEmbedder(_client(server_url, token="wrong")).embed_query("hello")
    with pytest.raises(SearchError, match="token"):
        HttpEmbedder(_client(server_url, token="")).embed_query("hello")


def test_different_models_stop_the_brain(server_url):
    client = _client(server_url, embed_model="BAAI/bge-m3", dim=1024)
    with pytest.raises(SearchError, match="different models"):
        client.try_verify()


def test_server_down_does_not_stop_startup_but_fails_the_search():
    client = _client(f"http://127.0.0.1:{_free_port()}")
    assert client.try_verify() is False  # the brain still starts: bank and voice keep working
    assert client.ping().startswith("error")
    with pytest.raises(SearchError, match="unreachable"):
        HttpEmbedder(client).embed_query("hello")


def test_open_bind_needs_a_token():
    check_bind("127.0.0.1", "")  # local only: fine without
    check_bind("0.0.0.0", "t")
    with pytest.raises(SystemExit):
        check_bind("0.0.0.0", "")


def test_ask_through_the_search_server(client, container, server_url):
    """The whole question path with search over HTTP gives the same answer as in-process; with search down, the
    caller gets the safe 'system problem' reply and a hand-over, never an invented answer."""
    r = client.post("/v1/documents", files={"file": ("search-kb.md", KB)}, data={"category": "deposits"})
    assert r.status_code == 201, r.text
    container.worker.run_pending()
    q = {"question": "What is the minimum balance requirement for the savings account?"}
    local = client.post("/v1/ask", json=q).json()
    retriever = container.retriever
    saved = retriever.embedder, retriever.reranker
    try:
        http = _client(server_url)
        retriever.embedder, retriever.reranker = HttpEmbedder(http), HttpReranker(http)
        remote = client.post("/v1/ask", json=q).json()
        assert (remote["outcome"], remote["answer"]) == (local["outcome"], local["answer"])
        assert remote["citations"] == local["citations"]

        down = _client(f"http://127.0.0.1:{_free_port()}")
        retriever.embedder, retriever.reranker = HttpEmbedder(down), HttpReranker(down)
        failed = client.post("/v1/ask", json=q).json()
        assert failed["outcome"] == "error" and failed["route"] == "handoff"
    finally:
        retriever.embedder, retriever.reranker = saved
    assert client.get("/health/ready").json()["checks"]["search"] == "local"
