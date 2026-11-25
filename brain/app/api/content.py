"""The journey catalogue editor (/journeys): read and edit the draft, translate, publish, canary, roll back.

Needs X-API-Key whenever API_KEY is set (in production: the bank's SSO with an editor role). Every save carries the
draft's `rev`; a stale one gets 409, so two editors never overwrite each other silently.
"""

import asyncio

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import container, require_key
from app.config import ROOT
from app.container import Container
from app.services.registry import GUARDRAILS
from app.services.releases import ReleaseError

router = APIRouter(prefix="/v1/content", tags=["journeys"], dependencies=[Depends(require_key)])


def _releases(c: Container):
    if c.releases is None:
        raise HTTPException(503, "content releases are not available")
    return c.releases


def _error(e: ReleaseError) -> JSONResponse:
    return JSONResponse(status_code=e.status, content={"detail": str(e), "problems": e.problems})


@router.get("")
async def editor(c: Container = Depends(container)):
    """The draft (created from what is live on first open) with everything the editor shows."""
    return await asyncio.to_thread(_releases(c).editor)


class Save(BaseModel):
    rev: int
    author: str = Field("", max_length=80)
    changes: dict = Field(default_factory=dict, description="{messages: {id: {en} | null}, prompts, settings, tree}")


@router.patch("/draft")
async def save(body: Save, c: Container = Depends(container)):
    r = _releases(c)
    try:
        await asyncio.to_thread(r.save, body.changes, body.rev, body.author)
    except ReleaseError as e:
        return _error(e)
    return await asyncio.to_thread(r.editor)


class Translate(BaseModel):
    author: str = Field("", max_length=80)
    ids: list[str] | None = Field(None, max_length=50, description="sentences to translate now; none = every changed one still missing a language")
    languages: list[str] | None = Field(None, max_length=12, description="language codes; none = every language but English")
    hint: str = Field("", max_length=300, description="what to change in the last translation (sent to the LLM)")
    fill_missing: bool = Field(False, description="every sentence with no text yet in `languages` (50 per call; call again while remaining > 0)")


@router.post("/draft/translate")
async def translate(body: Translate, c: Container = Depends(container)):
    """Write other languages of English sentences with the LLM into the draft (a few seconds per dozen sentences)."""
    r = _releases(c)
    try:
        result = await r.translate(body.author, body.ids, body.languages, body.hint, body.fill_missing)
    except ReleaseError as e:
        return _error(e)
    return result | {"editor": await asyncio.to_thread(r.editor)}


@router.delete("/draft")
async def discard(confirm: bool = False, c: Container = Depends(container)):
    """Throw away every unpublished change. Needs ?confirm=true."""
    if not confirm:
        raise HTTPException(400, "add ?confirm=true to discard the draft")
    r = _releases(c)
    await asyncio.to_thread(r.discard)
    return await asyncio.to_thread(r.editor)


class Publish(BaseModel):
    share: int = Field(5, description="percent of calls: 1, 5, 10, 25 or 50 for a canary, 100 for all calls")
    notes: str = Field("", max_length=1000)
    author: str = Field("", max_length=80)


@router.post("/publish")
async def publish(body: Publish, c: Container = Depends(container)):
    r = _releases(c)
    try:
        await asyncio.to_thread(r.publish, body.share, body.notes, body.author)
    except ReleaseError as e:
        return _error(e)
    return await asyncio.to_thread(r.editor)


class Rollout(BaseModel):
    action: str = Field(description="promote | share | stop | live")
    share: int | None = None
    release: int | None = Field(None, description="for live: a published release id; 0 = as shipped in git")
    author: str = Field("", max_length=80)


@router.post("/rollout")
async def rollout(body: Rollout, c: Container = Depends(container)):
    r = _releases(c)
    actions = {"promote": lambda: r.promote(body.author), "stop": lambda: r.stop_canary(body.author),
               "share": lambda: r.set_share(body.share or 0, body.author),
               "live": lambda: r.make_live(body.release or None, body.author)}
    if body.action not in actions:
        raise HTTPException(400, f"action must be one of {', '.join(actions)}")
    try:
        await asyncio.to_thread(actions[body.action])
    except ReleaseError as e:
        return _error(e)
    return await asyncio.to_thread(_status, r)


def _status(r) -> dict:
    return r.history() | {"rollout": r.rollout_view(), "stats": r.stats()}


@router.get("/releases")
async def releases(c: Container = Depends(container)):
    """Published releases, the rollout log, what is live, and the canary vs main comparison."""
    return await asyncio.to_thread(_status, _releases(c))


def _pack(name: str) -> list[dict]:
    path = ROOT / "evals" / f"{name}.yaml"
    if not path.is_file():
        return []
    items = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [{"id": i.get("id"), "group": i.get("cls", ""), "caller": i.get("caller"),
             "turns": [{k: t.get(k) for k in ("say", "outcome", "has", "has_any", "lacks", "max_words") if t.get(k) is not None}
                       for t in i.get("turns") or []]} for i in items if isinstance(i, dict)]


@router.get("/reference")
def reference():
    """Read-only: the guardrails code enforces, and the test conversations run before a change ships."""
    golden = ROOT / "evals" / "golden" / "v1.yaml"
    return {
        "guardrails": [{"name": n, "what": w, "where": f} for n, w, f in GUARDRAILS],
        "tests": {"smoke": _pack("smoke"), "regression": _pack("regression"),
                  "golden_count": len(yaml.safe_load(golden.read_text(encoding="utf-8")) or []) if golden.is_file() else 0},
    }
