"""Call audit (read only): past calls, the full call record, recordings, transcript file, live alerts.

These hold customer conversations, so they need X-API-Key whenever API_KEY is set (in production: the bank's
SSO with roles, and every access logged).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from app.api.deps import container, require_key
from app.container import Container
from app.services.audit import mask_number, render_transcript
from app.services.store import _is_uuid

router = APIRouter(prefix="/v1", tags=["audit"], dependencies=[Depends(require_key)])


def _call(c: Container, session_id: str) -> dict:
    call = c.audit.call(session_id) if _is_uuid(session_id) else None
    if not call:
        raise HTTPException(404, "call not found")
    return call


@router.get("/calls")
def list_calls(limit: int = 50, before: str | None = None, q: str | None = None, needs_review: bool = False,
               c: Container = Depends(container)):
    """Newest first. `before` = created_at of the last row for the next page; `q` searches id, number and words;
    `needs_review` = only calls the review flagged."""
    rows = c.audit.calls(min(limit, 200), before, (q or "").strip()[:100] or None, needs_review)
    for r in rows:
        r["caller"] = mask_number(r.pop("caller_number"))
    return rows


@router.get("/calls/{session_id}")
async def get_call(session_id: str, c: Container = Depends(container)):
    call = await asyncio.to_thread(_call, c, session_id)
    call["caller"] = mask_number(call.pop("caller_number"))
    call["review"] = await asyncio.to_thread(c.reviews.get, session_id) if c.reviews else None
    return call


@router.post("/calls/{session_id}/review")
async def review_call(session_id: str, judge: bool = True, c: Container = Depends(container)):
    """(Re-)run the review of one call now; `judge=true` always includes the LLM judge."""
    await asyncio.to_thread(_call, c, session_id)
    row = await c.reviews.review(session_id, force_judge=judge)
    if row is None:
        raise HTTPException(409, "this call has no turns to review")
    return await asyncio.to_thread(c.reviews.get, session_id)


class HumanLabel(BaseModel):
    scores: dict[str, int] = Field(default_factory=dict, description="1-5 per dimension")
    critical: bool = False
    note: str = Field("", max_length=500)


@router.post("/calls/{session_id}/review/human")
async def label_review(session_id: str, body: HumanLabel, c: Container = Depends(container)):
    """A person's scores for the same dimensions: the calibration data, and it clears the call's attention flag."""
    row = await asyncio.to_thread(c.reviews.label, session_id, body.scores, body.critical, body.note)
    if row is None:
        raise HTTPException(404, "no review for this call yet")
    return row


class FixStatus(BaseModel):
    status: str = Field(..., pattern="^(open|done|wont_fix)$")
    note: str = Field("", max_length=500)


def _not_live(session_id: str) -> None:
    from app.api.voice import LIVE_SESSIONS  # a call still in progress cannot be deleted under its own feet
    if session_id in LIVE_SESSIONS:
        raise HTTPException(409, "the call is still live")


@router.delete("/calls/{session_id}")
async def delete_call(session_id: str, c: Container = Depends(container)):
    """Delete a call for good (turns, timeline, review, fix-list items, recordings and their files)."""
    if not _is_uuid(session_id):
        raise HTTPException(404, "call not found")
    _not_live(session_id)
    from app.services import maintenance

    removed = await asyncio.to_thread(maintenance.delete_call, c.db, c.recordings.files, session_id)
    if removed is None:
        raise HTTPException(404, "call not found")
    return {"deleted": session_id, "removed": removed}


@router.delete("/calls/{session_id}/recordings/{recording_id}")
async def delete_recording(session_id: str, recording_id: str, c: Container = Depends(container)):
    """Delete one recording of a call (the row and the encrypted file)."""
    if not (_is_uuid(session_id) and _is_uuid(recording_id)) or not c.recordings:
        raise HTTPException(404, "recording not found")
    _not_live(session_id)
    if not await asyncio.to_thread(c.recordings.delete, session_id, recording_id):
        raise HTTPException(404, "recording not found")
    return {"deleted": recording_id}


@router.get("/fixes")
def list_fixes(status: str = "open", c: Container = Depends(container)):
    """The fix list: every problem the judge found, as a to-do (status: open | done | wont_fix | all)."""
    return c.reviews.fixes(None if status == "all" else status)


@router.patch("/fixes/{fix_id}")
def update_fix(fix_id: int, body: FixStatus, c: Container = Depends(container)):
    row = c.reviews.set_fix(fix_id, body.status, body.note)
    if not row:
        raise HTTPException(404, "fix not found")
    return row


@router.get("/reviews/summary")
def review_summary(days: int = 30, c: Container = Depends(container)):
    """Quality across calls: averages, critical rate and the recurring problems (biggest first), from the post-call reviews."""
    return c.reviews.summary(max(1, min(days, 365)))


@router.get("/reviews/calibration")
def calibration(c: Container = Depends(container)):
    return c.reviews.calibration()


@router.get("/calls/{session_id}/transcript.txt")
async def transcript(session_id: str, c: Container = Depends(container)):
    call = await asyncio.to_thread(_call, c, session_id)
    return Response(render_transcript(call), media_type="text/plain; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="call-{session_id[:8]}.txt"'})


@router.get("/calls/{session_id}/recordings/{recording_id}")
async def recording(session_id: str, recording_id: str, c: Container = Depends(container)):
    if not (_is_uuid(session_id) and _is_uuid(recording_id)):
        raise HTTPException(404, "recording not found")
    try:
        found = await asyncio.to_thread(c.recordings.load, session_id, recording_id)
    except ValueError as e:  # the file changed since it was saved
        raise HTTPException(409, str(e)) from e
    if not found:
        raise HTTPException(404, "recording not found (or deleted by the retention policy)")
    audio, media_type = found
    return Response(audio, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.get("/alerts")
def alerts(after: int = -1, c: Container = Depends(container)):
    """Alerts with id > `after`. `after=-1` returns only the newest id (a cursor), so a page shows new ones only."""
    return c.audit.alerts_after(after)
