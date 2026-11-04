"""Logging: readable lines on the console, JSON lines in logs/app.jsonl, request/trace ids attached automatically.

Standard library `logging` only. Set LOG_CONSOLE=json to get JSON on stdout too (containers / log shippers).
"""

import json
import logging
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)

def mask(text: str) -> str:
    """Every log line goes through the same masking as caller text (app/services/privacy.py)."""
    from app.services.privacy import redact  # imported here: logging is set up before the services load

    return redact(text)


def _data(record: logging.LogRecord) -> dict | None:
    extra = getattr(record, "data", None)
    return json.loads(mask(json.dumps(extra, default=str))) if extra else None


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": mask(record.getMessage()),
        }
        if rid := request_id.get():
            entry["request_id"] = rid
        if tid := trace_id.get():
            entry["trace_id"] = tid
        if data := _data(record):
            entry["data"] = data
        if record.exc_info:
            entry["error"] = mask(self.formatException(record.exc_info))
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """`09:48:02 INFO  app.db  postgres ready  data_dir=...  [trace 1a2b3c4d]`, tracebacks below the line."""

    def format(self, record: logging.LogRecord) -> str:
        parts = [self.formatTime(record, "%H:%M:%S"), f"{record.levelname:<7}", f"{record.name:<24}", mask(record.getMessage())]
        if data := _data(record):
            parts.append("  ".join(f"{k}={v}" for k, v in data.items()))
        if tid := trace_id.get():
            parts.append(f"[trace {tid[:8]}]")
        line = " ".join(parts[:3]) + " " + "  ".join(parts[3:])
        if record.exc_info:
            line += "\n" + mask(self.formatException(record.exc_info))
        return line


def setup_logging(level: str, log_dir: Path, console_format: str = "text") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter() if console_format == "json" else TextFormatter())
    file = RotatingFileHandler(log_dir / "app.jsonl", maxBytes=10_000_000, backupCount=5, encoding="utf-8")
    file.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [console, file]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "urllib3", "pdfminer", "transformers", "huggingface_hub",
                  "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # pgserver dumps the whole Postgres log on a slow start; app.db reports the useful tail itself.
    logging.getLogger("pgserver").setLevel(logging.CRITICAL)
    # Every request is already logged once by the app ("request" line with status and ms).
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def log(logger: logging.Logger, msg: str, **data) -> None:
    logger.info(msg, extra={"data": data} if data else None)
