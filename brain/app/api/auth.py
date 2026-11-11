"""Caller verification (MVP 2). The code is typed on a keypad and sent here - never spoken, never seen by the LLM."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import container
from app.container import Container
from app.metrics import observe_turn

router = APIRouter(prefix="/v1", tags=["auth"])


class CodeBody(BaseModel):
    session_id: str
    code: str = Field(..., min_length=4, max_length=8, pattern=r"^\d+$")


class SessionRef(BaseModel):
    session_id: str


def _auth(c: Container, session_id: str):
    if not c.auth:
        raise HTTPException(404, "personal questions are switched off (BANK_DATA_FILE not set)")
    if not c.traces.session_exists(session_id):
        raise HTTPException(404, "session not found")
    return c.auth


@router.post("/auth/verify")
async def verify(body: CodeBody, c: Container = Depends(container)):
    """Check the code. When it's right and a question was waiting, that question is answered straight away."""
    _auth(c, body.session_id)
    auth, message, language = await c.conversation.verify(body.session_id, body.code)
    reply = None
    pending = auth.pop("pending_question", None)
    if auth["status"] == "verified" and pending:
        reply = await c.conversation.ask(pending, body.session_id, {"input": "after_verification_chat"})  # a re-ask, not the caller (typed chat: not a call)
        observe_turn(reply.outcome, reply.language, reply.timings)
    return {"auth": auth, "message": message, "language": language, "reply": asdict(reply) if reply else None}


@router.post("/auth/resend")
async def resend(body: SessionRef, c: Container = Depends(container)):
    _auth(c, body.session_id)
    auth, message, language = await c.conversation.resend(body.session_id)
    return {"auth": auth, "message": message, "language": language}


@router.get("/auth/{session_id}")
def status(session_id: str, c: Container = Depends(container)):
    return _auth(c, session_id).status(session_id)


@router.post("/auth/end")
def end(body: SessionRef, c: Container = Depends(container)):
    """Forget the verification (caller hung up / 'log out')."""
    _auth(c, body.session_id).end(body.session_id)
    return {"verified": False}


@router.get("/demo/callers")
def demo_callers(c: Container = Depends(container)):
    """Test customers for the UI's 'calling from' picker (simulated caller ID). Off unless DEMO_CALLERS=true."""
    if not c.bank or not c.settings.demo_callers:
        raise HTTPException(404, "demo callers are off")
    return c.bank.demo_callers()
