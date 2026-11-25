"""FastAPI app: lifespan wiring, request ids, access logs, metrics and error mapping."""

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, calls, conversation, desk, documents, ops
from app.config import get_settings
from app.container import Container, build
from app.logs import log, request_id, setup_logging
from app.metrics import REQUEST_SECONDS, REQUESTS
from app.services.documents import DocumentError

logger = logging.getLogger("app.http")


def create_app(container: Container | None = None) -> FastAPI:
    settings = container.settings if container else get_settings()
    setup_logging(settings.log_level, settings.log_dir, settings.log_console)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = container or build(settings)
        warm = None
        if app.state.container.voice:  # synthesise the greeting and fixed lines in the background so they play instantly
            warm = asyncio.create_task(app.state.container.voice.phrases.warm())
        yield
        if warm and not warm.done():
            warm.cancel()
        if container is None:  # tests manage their own container
            await app.state.container.close()

    app = FastAPI(title="AU SFB Brain", version="0.1.0", lifespan=lifespan,
                  description="AU SFB voice agent: general questions (MVP 1) and verified personal questions (MVP 2), by text or voice.")

    limiter = RateLimiter(settings.rate_limit_per_min)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request_id.set(rid)
        if request.url.path.startswith(LIMITED_PATHS) and not limiter.allow(
                request.headers.get("x-api-key") or (request.client.host if request.client else "?")):
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:  # answered here, inside the CORS layer, so a browser sees the error rather than "Failed to fetch"
            logger.exception("unhandled error")
            response = JSONResponse(status_code=500, content={"detail": "internal error", "request_id": rid})
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")  # route template keeps metric labels bounded
        elapsed = time.perf_counter() - started
        REQUESTS.labels(request.method, route_path, response.status_code).inc()
        REQUEST_SECONDS.labels(route_path).observe(elapsed)
        response.headers["x-request-id"] = rid
        if route_path not in ("/metrics", "/health"):
            log(logger, "request", method=request.method,
                path=route_path if route else request.url.path, status=response.status_code, ms=round(elapsed * 1000))
        return response

    @app.exception_handler(DocumentError)
    async def document_error(_: Request, exc: DocumentError):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    if origins:  # the browser test UI runs on another port
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                           allow_headers=["content-type", "x-api-key", "x-request-id"])

    # Every API route is under /api (/api/v1/...), so one load balancer rule (/api/*) sends the API to the brain and
    # everything else to the UI. Health checks and metrics stay at the root: probes reach the pod directly.
    for module in (documents, conversation, auth, calls, desk):
        app.include_router(module.router, prefix=API_PREFIX)
    app.include_router(ops.api_router, prefix=API_PREFIX)
    app.include_router(ops.router)
    if settings.voice_enabled:
        from app.api import voice  # imports audio libraries only when voice is on

        app.include_router(voice.router, prefix=API_PREFIX)
    return app


API_PREFIX = "/api"
LIMITED_PATHS = tuple(API_PREFIX + p for p in ("/v1/ask", "/v1/search", "/v1/voice", "/v1/auth", "/v1/handoffs"))


class RateLimiter:
    """Sliding one-minute window per client (API key or IP). In production, also limit at the gateway."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque] = defaultdict(deque)

    def allow(self, client: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        hits = self._hits[client]
        while hits and now - hits[0] > 60:
            hits.popleft()
        if len(hits) >= self.per_minute:
            return False
        hits.append(now)
        return True


app = create_app()
