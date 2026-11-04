"""Request-level helpers shared by routers."""

from fastapi import Header, HTTPException, Request

from app.container import Container


def container(request: Request) -> Container:
    return request.app.state.container


def require_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    """Write endpoints need X-API-Key when API_KEY is configured (open in local dev when it's empty)."""
    expected = request.app.state.container.settings.api_key
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="missing or wrong X-API-Key")
