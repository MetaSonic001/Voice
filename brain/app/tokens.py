"""Short-lived, signed tokens (stdlib `hmac` only) for calls from the brain to the bank's systems.

A customer-scoped token is minted only after the caller passed the keypad code. It names one customer (CIF), the
systems it may be used with, what it may do, and when it expires (about the length of a call). Each bank system checks
it on its own and rejects any other customer. The customer identity comes from the verified session, never from the
LLM, and the token is never shown to the LLM, the transcript or the desk.
"""

import base64
import hashlib
import hmac
import json
import time


class TokenError(ValueError):
    """The token is malformed, forged, expired or not meant for this use."""


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(secret: bytes, *, cif: str, systems: list[str], scopes: list[str], ttl_s: int, now: float | None = None) -> str:
    payload = {"cif": cif, "aud": systems, "scp": scopes, "exp": int((now or time.time()) + ttl_s)}
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())}"


def verify(secret: bytes, token: str, *, system: str, scope: str, now: float | None = None) -> dict:
    try:
        body, sig = token.split(".", 1)
        expected = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            raise TokenError("bad signature")
        payload = json.loads(_unb64(body))
    except (ValueError, KeyError) as e:
        raise TokenError("malformed token") from e
    if (now or time.time()) > payload.get("exp", 0):
        raise TokenError("token expired")
    if system not in payload.get("aud", []) and "*" not in payload.get("aud", []):
        raise TokenError(f"token is not valid for {system}")
    if scope not in payload.get("scp", []):
        raise TokenError(f"token does not allow {scope}")
    return payload
