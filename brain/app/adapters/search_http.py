"""Embedder and Reranker ports over the network: the brain's side of the GPU search server (app/search_server.py).

SEARCH_PROVIDER=http: our search server on a GPU machine (SearchClient, bearer token).
SEARCH_PROVIDER=sagemaker: the same server image as a SageMaker endpoint (SageMakerSearchClient, AWS credentials).
Both run the very same model code as SEARCH_PROVIDER=local, so vectors and scores are identical and switching between
the three needs no re-indexing. Same interface as adapters/models.py, so nothing above the ports changes.

- Model check: the server's model names and vector size must equal EMBED_MODEL / RERANK_MODEL / EMBED_DIM.
  Stored vectors are tied to the embedder's name, so a different model would quietly break search. Checked at
  startup; if the server is not up yet, the brain still starts (bank and voice keep working) and checks again
  before the first search.
- Errors: one retry on a connection failure only (both calls are read-only, so a retry is safe). Then SearchError,
  which the conversation turns into the normal "system problem" reply with a hand-over - never a made-up answer.
- Never logs the texts it sends.
"""

import json
import logging
import threading

import httpx

logger = logging.getLogger(__name__)

INGEST_BATCH = 64  # chunks per /v1/embed request while a document is indexed


class SearchError(RuntimeError):
    """The search server did not answer (down, timeout, wrong token, wrong model)."""


class _Remote:
    """What both transports share: the model check, and the calls the embedder and reranker make."""

    def _setup(self, embed_model: str, dim: int, rerank_model: str, ingest_timeout_s: float) -> None:
        self.expected = {"embed_model": embed_model, "dim": dim, "rerank_model": rerank_model}
        self.ingest_timeout_s = ingest_timeout_s
        self.device = "remote"
        self._verified = False
        self._lock = threading.Lock()

    def _send(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        raise NotImplementedError

    def verify(self) -> None:
        """Check the server runs the expected models. Raises SearchError on a mismatch or when it can't be reached."""
        if self._verified:
            return
        with self._lock:
            if self._verified:
                return
            info = self._send("GET", "/v1/info")
            wrong = {k: (v, info.get(k)) for k, v in self.expected.items() if info.get(k) != v}
            if wrong:
                raise SearchError("search server runs different models: "
                                  + ", ".join(f"{k} expected {e!r}, got {g!r}" for k, (e, g) in wrong.items()))
            self.device = f"remote:{info.get('device', '-')}"
            self._verified = True

    def try_verify(self) -> bool:
        """At startup: verify if possible. A model mismatch stops the brain; an unreachable server does not."""
        try:
            self.verify()
            return True
        except SearchError as e:
            if "different models" in str(e):
                raise
            logger.warning("search server not reachable yet; will check again before the first search (%s)", e)
            return False

    def post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        self.verify()
        return self._send("POST", path, body, timeout)


class SearchClient(_Remote):
    def __init__(self, url: str, token: str, *, embed_model: str, dim: int, rerank_model: str,
                 timeout_s: float = 3.0, ingest_timeout_s: float = 60.0, transport: httpx.BaseTransport | None = None):
        if not url:
            raise ValueError("SEARCH_PROVIDER=http needs SEARCH_URL")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        # httpx.Client is thread-safe; the brain calls it from worker threads (asyncio.to_thread), so keep-alive
        # connections are reused across calls (no new TCP connection per search).
        # Connecting inside the VPC takes well under 1 ms, so a 1 s connect limit only cuts the wait when the server is gone.
        self.http = httpx.Client(base_url=url.rstrip("/"), headers=headers, timeout=httpx.Timeout(timeout_s, connect=1.0),
                                 transport=transport,
                                 limits=httpx.Limits(max_connections=32, max_keepalive_connections=16))
        self._setup(embed_model, dim, rerank_model, ingest_timeout_s)

    def ping(self) -> str:
        try:
            self.http.get("/health", timeout=1.0).raise_for_status()
            return "ok" if self._verified else "ok (models not checked yet)"
        except httpx.HTTPError as e:
            return f"error: {type(e).__name__}"

    def _send(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        kwargs = {"json": body} if body is not None else {}
        if timeout is not None:
            kwargs["timeout"] = httpx.Timeout(timeout, connect=1.0)
        for attempt in (1, 2):
            try:
                r = self.http.request(method, path, **kwargs)
                break
            except httpx.ConnectTimeout as e:  # no answer at all: a retry would only double the caller's wait
                raise SearchError("search server unreachable: ConnectTimeout") from e
            except (httpx.ConnectError, httpx.RemoteProtocolError) as e:  # never reached the model: safe to retry
                if attempt == 2:
                    raise SearchError(f"search server unreachable: {type(e).__name__}") from e
            except httpx.TimeoutException as e:
                raise SearchError(f"search server timed out on {path}") from e
        if r.status_code == 401:
            raise SearchError("search server refused the token (check SEARCH_TOKEN on both sides)")
        if r.status_code != 200:
            raise SearchError(f"search server error {r.status_code} on {path}")
        return r.json()

    def close(self) -> None:
        self.http.close()


class SageMakerSearchClient(_Remote):
    """The search server as a SageMaker real-time endpoint: InvokeEndpoint with the AWS credential chain (the EC2 or ECS
    role in AWS, your AWS login on a laptop). The caller needs `sagemaker:InvokeEndpoint` on that endpoint.
    Each call is {"op": ..., body} to /invocations. A read-only call, so botocore's own retry (2 attempts, throttling and
    5xx only) is safe."""

    _OPS = {"/v1/info": "info", "/v1/embed": "embed", "/v1/rerank": "rerank"}

    def __init__(self, endpoint: str, region: str, *, embed_model: str, dim: int, rerank_model: str,
                 timeout_s: float = 3.0, ingest_timeout_s: float = 60.0, runtime=None):
        if not endpoint:
            raise ValueError("SEARCH_PROVIDER=sagemaker needs SAGEMAKER_ENDPOINT")
        self.endpoint, self.timeout_s = endpoint, timeout_s
        if runtime is None:
            import boto3
            from botocore.config import Config

            def client(read_s: float):  # boto3 clients are thread-safe; one per timeout (a question vs indexing)
                return boto3.client("sagemaker-runtime", region_name=region, config=Config(
                    connect_timeout=2, read_timeout=read_s, retries={"max_attempts": 2, "mode": "standard"},
                    max_pool_connections=32))
            self._fast, self._slow = client(timeout_s), client(ingest_timeout_s)
        else:
            self._fast = self._slow = runtime
        self._setup(embed_model, dim, rerank_model, ingest_timeout_s)

    def ping(self) -> str:
        try:
            self._send("GET", "/v1/info")
            return "ok" if self._verified else "ok (models not checked yet)"
        except SearchError as e:
            return f"error: {e}"

    def _send(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, NoCredentialsError, ReadTimeoutError

        client = self._slow if timeout and timeout > self.timeout_s else self._fast
        payload = json.dumps({"op": self._OPS[path], **(body or {})}).encode()
        try:
            r = client.invoke_endpoint(EndpointName=self.endpoint, ContentType="application/json",
                                       Accept="application/json", Body=payload)
            return json.loads(r["Body"].read())
        except ReadTimeoutError as e:
            raise SearchError(f"SageMaker endpoint timed out on {path}") from e
        except ConnectTimeoutError as e:
            raise SearchError("SageMaker endpoint unreachable: ConnectTimeout") from e
        except NoCredentialsError as e:
            raise SearchError("no AWS credentials for SageMaker (role, profile or SSO login)") from e
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            raise SearchError(f"SageMaker endpoint error {code} on {path} (endpoint {self.endpoint!r})") from e
        except BotoCoreError as e:
            raise SearchError(f"SageMaker endpoint unreachable: {type(e).__name__}") from e

    def close(self) -> None:
        for c in {id(self._fast): self._fast, id(self._slow): self._slow}.values():
            getattr(c, "close", lambda: None)()


class HttpEmbedder:
    def __init__(self, client: _Remote):
        self.client = client
        self.name, self.dim = client.expected["embed_model"], client.expected["dim"]

    @property
    def device(self) -> str:
        return self.client.device

    def embed(self, texts: list[str]) -> list[list[float]]:
        if len(texts) == 1:  # a caller's question: the short timeout
            return self.client.post("/v1/embed", {"texts": texts})["vectors"]
        out: list[list[float]] = []
        for i in range(0, len(texts), INGEST_BATCH):  # indexing a document: batches, long timeout
            out += self.client.post("/v1/embed", {"texts": texts[i:i + INGEST_BATCH]},
                                    timeout=self.client.ingest_timeout_s)["vectors"]
        return out

    def embed_query(self, text: str) -> list[float]:
        return self.embed([text])[0]


class HttpReranker:
    def __init__(self, client: _Remote):
        self.client = client
        self.name = client.expected["rerank_model"]

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return self.client.post("/v1/rerank", {"query": query, "passages": passages})["scores"]
