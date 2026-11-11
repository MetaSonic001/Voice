"""Run the mock bank on its own port inside the brain process (a real HTTP server on a real socket, so timeouts,
status codes and serialisation behave as they will against AU's systems). For a separate process instead, serve
`app.mockbank.core.create_app(...)` with uvicorn and point BANK_BASE_URL at it.
"""

import asyncio
import logging
import secrets
import socket
import threading
import time

import uvicorn

from app.adapters.http_bank import HttpBank
from app.adapters.mock_corebank import ensure_seeded
from app.config import Settings
from app.db import Database
from app.mockbank.core import BankConfig, MockBank, create_app, parse_faults

logger = logging.getLogger(__name__)


class EmbeddedServer:
    def __init__(self, app, port: int = 0):
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", port))
        self.port = self._sock.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
        self.thread = threading.Thread(target=lambda: asyncio.run(self.server.serve(sockets=[self._sock])), daemon=True,
                                       name="mockbank")
        self.thread.start()
        deadline = time.monotonic() + 15
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the mock bank server did not start")
            time.sleep(0.02)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def close(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)


def start_embedded(db: Database, s: Settings) -> tuple[EmbeddedServer, HttpBank]:
    """Seed the data once, start the bank systems, and return the brain's client for them. The two tokens are random
    for this process unless configured, so nothing else on the machine can call the bank systems."""
    ensure_seeded(db, s.bank_data_file if str(s.bank_data_file) and s.bank_data_file.is_file() else None, s.demo_today)
    service = s.bank_service_token.get_secret_value() or secrets.token_urlsafe(24)
    secret = (s.bank_token_secret.get_secret_value() or secrets.token_urlsafe(32)).encode()
    lo, _, hi = s.mockbank_latency_ms.partition("-")
    config = BankConfig(service, secret, s.demo_today, parse_faults(s.mock_bank_fail), (float(lo), float(hi or lo)))
    server = EmbeddedServer(create_app(MockBank(db, config)), s.mockbank_port)
    logger.info("mock bank systems up", extra={"data": {"url": server.url}})
    return server, HttpBank(server.url, service, secret, timeout_s=s.bank_timeout_s, token_ttl_s=s.bank_token_ttl_s)
