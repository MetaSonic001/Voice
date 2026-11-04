"""Question answering, sessions and traces."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import container
from app.container import Container
from app.metrics import observe_turn

router = APIRouter(prefix="/v1", tags=["conversation"])


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000, examples=["FD ka interest rate kya hai senior citizen ke liye 2 saal?"])
    session_id: str | None = Field(None, description="From POST /v1/sessions; enables follow-up questions")


class SearchBody(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)


class SessionBody(BaseModel):
    language: str | None = None
    caller_number: str | None = Field(None, max_length=20, pattern=r"^\+?[\d\s-]{6,20}$",
                                      description="The number the call comes from (telephony caller ID; simulated in tests)")


@router.post("/sessions", status_code=201)
def create_session(body: SessionBody | None = None, c: Container = Depends(container)):
    body = body or SessionBody()
    return c.traces.create_session(body.language, body.caller_number)


@router.get("/sessions/{session_id}")
def get_session(session_id: str, c: Container = Depends(container)):
    s = c.traces.session(session_id)
    if not s:
        raise HTTPException(404, "session not found")
    return s


@router.post("/ask")
async def ask(body: AskBody, c: Container = Depends(container)):
    if body.session_id and not c.traces.session_exists(body.session_id):
        raise HTTPException(404, "session not found")
    reply = await c.conversation.ask(body.question, body.session_id)
    observe_turn(reply.outcome, reply.language, reply.timings)
    return asdict(reply)


@router.post("/search")
async def search(body: SearchBody, c: Container = Depends(container)):
    """Retrieval only (no answer): what the hybrid search and reranker find, with scores."""
    return await c.conversation.search_only(body.query)


@router.get("/traces")
def list_traces(limit: int = 50, outcome: str | None = None, c: Container = Depends(container)):
    return c.traces.recent(min(limit, 500), outcome)


class FeedbackBody(BaseModel):
    verdict: str = Field(..., pattern="^(correct|wrong)$")
    note: str = Field("", max_length=500)


@router.post("/traces/{trace_id}/feedback")
def feedback(trace_id: str, body: FeedbackBody, c: Container = Depends(container)):
    """Mark an answer correct or wrong while testing; wrong ones are easy to turn into golden-set questions."""
    if not c.traces.get(trace_id) or not c.traces.add_feedback(trace_id, body.verdict, body.note):
        raise HTTPException(404, "trace not found")
    return {"trace_id": trace_id, **body.model_dump()}


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, c: Container = Depends(container)):
    t = c.traces.get(trace_id)
    if not t:
        raise HTTPException(404, "trace not found")
    return t
