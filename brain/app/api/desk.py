"""The agent desk: people from each department pick up calls and chats the AI handed over.

  GET  /v1/desk/handoffs                     the board: waiting (most urgent first), active, recent
  GET  /v1/desk/handoffs/{id}                one handoff with its brief and chat messages
  POST /v1/desk/handoffs/{id}/accept         a person takes it (chat); for a live call, open the stream below
  POST /v1/desk/handoffs/{id}/messages       the person types to a chat customer
  POST /v1/desk/handoffs/{id}/complete       done: disposition + notes (ends a live call)
  WS   /v1/desk/stream?handoff_id=...        a live call: the person's microphone in, the caller's audio out
Customer side of a handed-over chat (the test console):
  GET  /v1/handoffs/{session_id}             status, position in the waiting list, messages from the person
  POST /v1/handoffs/{session_id}/messages    the customer types to the person
The desk sees customer details, so these need X-API-Key when API_KEY is set (production: SSO + roles).
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.api.deps import container, require_key
from app.container import Container
from app.services.audit import mask_number
from app.services.desk import DEPARTMENTS
from app.services.store import _is_uuid

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["desk"])
AGENTS = ["Priya (Cards)", "Arjun (Payments)", "Neha (Loans)", "Rahul (Fraud & Risk)", "Sana (Customer Care)",
          "Vikram (Priority Banking)"]


def _handoff(c: Container, handoff_id: str) -> dict:
    h = c.handoffs.get(handoff_id) if _is_uuid(handoff_id) else None
    if not h:
        raise HTTPException(404, "handoff not found")
    return h


def _view(h: dict) -> dict:
    out = {k: v for k, v in h.items() if k != "caller_number"}
    out["caller"] = mask_number(h.get("caller_number")) or (h.get("note") or {}).get("caller")
    out["department_name"] = DEPARTMENTS.get(h["department"], DEPARTMENTS["general"])[0]
    return out


@router.get("/desk/handoffs", dependencies=[Depends(require_key)])
def board(c: Container = Depends(container)):
    from app.voice.session import LIVE_SESSIONS

    rows = [_view(h) for h in c.handoffs.board()]
    for r in rows:
        r["live"] = str(r["session_id"]) in LIVE_SESSIONS
    return {"handoffs": rows, "agents": AGENTS, "departments": {k: v[0] for k, v in DEPARTMENTS.items()}}


@router.get("/desk/handoffs/{handoff_id}", dependencies=[Depends(require_key)])
def get_handoff(handoff_id: str, c: Container = Depends(container)):
    h = _handoff(c, handoff_id)
    call = c.audit.call(str(h["session_id"]))
    transcript = [{"at": t["created_at"], "caller": t["question"], "agent": t["answer"], "outcome": t["outcome"], "language": t["language"]}
                  for t in (call or {}).get("turns", [])]  # read-only, already redacted
    verification = {"verified": bool((call or {}).get("customer_id")), "verified_at": (call or {}).get("verified_at")}
    return _view(h) | {"messages": c.handoffs.messages(handoff_id), "transcript": transcript, "verification": verification}


class Accept(BaseModel):
    agent_name: str = Field(..., min_length=1, max_length=60)


@router.post("/desk/handoffs/{handoff_id}/accept", dependencies=[Depends(require_key)])
def accept(handoff_id: str, body: Accept, c: Container = Depends(container)):
    h = _handoff(c, handoff_id)
    if h["status"] not in ("waiting", "active"):
        raise HTTPException(409, f"handoff is {h['status']}")
    updated = c.handoffs.set_status(handoff_id, "active", agent_name=body.agent_name)
    c.audit.event(str(h["session_id"]), "handoff_accepted", agent=body.agent_name, department=h["department"])
    return _view(updated)


class Message(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/desk/handoffs/{handoff_id}/messages", dependencies=[Depends(require_key)])
def agent_message(handoff_id: str, body: Message, c: Container = Depends(container)):
    h = _handoff(c, handoff_id)
    if h["status"] != "active":
        raise HTTPException(409, "accept the handoff first")
    return c.handoffs.add_message(handoff_id, "agent", body.text)


class Complete(BaseModel):
    disposition: str = Field(..., pattern="^(resolved|ticket_raised|callback|transferred|no_response)$")
    notes: str = Field("", max_length=2000)


@router.post("/desk/handoffs/{handoff_id}/complete", dependencies=[Depends(require_key)])
async def complete(handoff_id: str, body: Complete, c: Container = Depends(container)):
    from app.voice.session import LIVE_SESSIONS

    h = await asyncio.to_thread(_handoff, c, handoff_id)
    updated = await asyncio.to_thread(c.handoffs.set_status, handoff_id, "completed", disposition=body.disposition,
                                      agent_notes=body.notes)
    await asyncio.to_thread(c.audit.event, str(h["session_id"]), "handoff_completed", agent=h.get("agent_name"),
                            disposition=body.disposition, notes=body.notes[:300])
    if session := LIVE_SESSIONS.get(str(h["session_id"])):
        await session.close("call completed by the bank's team")
    return _view(updated)


# ── customer side (chat) ──

@router.get("/handoffs/{session_id}")
def customer_status(session_id: str, after: int = 0, c: Container = Depends(container)):
    h = c.handoffs.for_session(session_id) if _is_uuid(session_id) else None
    if not h:
        return {"status": "none"}
    position = c.handoffs.position(str(h["id"])) if h["status"] == "waiting" else 0
    messages = [m for m in c.handoffs.messages(str(h["id"]), after) if m["sender"] == "agent"]
    return {"status": h["status"], "department": DEPARTMENTS.get(h["department"], DEPARTMENTS["general"])[0],
            "agent": h["agent_name"], "position": position, "messages": messages}


@router.post("/handoffs/{session_id}/messages")
def customer_message(session_id: str, body: Message, c: Container = Depends(container)):
    h = c.handoffs.for_session(session_id) if _is_uuid(session_id) else None
    if not h or h["status"] not in ("waiting", "active"):
        raise HTTPException(409, "not handed over to a person")
    return c.handoffs.add_message(str(h["id"]), "customer", body.text)


# ── live call: the person's audio bridged to the caller ──

@router.websocket("/desk/stream")
async def desk_stream(ws: WebSocket):
    from app.voice.session import LIVE_SESSIONS

    c: Container = ws.app.state.container
    expected = c.settings.api_key
    if expected and (ws.headers.get("x-api-key") or ws.query_params.get("key")) != expected:
        await ws.close(code=4401)
        return
    handoff_id, agent = ws.query_params.get("handoff_id", ""), (ws.query_params.get("agent") or "Agent")[:60]
    h = await asyncio.to_thread(c.handoffs.get, handoff_id) if _is_uuid(handoff_id) else None
    session = LIVE_SESSIONS.get(str(h["session_id"])) if h else None
    if not h or not session or h["status"] not in ("waiting", "active"):
        await ws.close(code=4404)
        return
    await ws.accept()
    lock = asyncio.Lock()

    async def to_agent(pcm: bytes):
        async with lock:
            await ws.send_bytes(pcm)

    await asyncio.to_thread(c.handoffs.set_status, handoff_id, "active", agent_name=agent)
    await asyncio.to_thread(c.audit.event, str(h["session_id"]), "handoff_accepted", agent=agent, department=h["department"])
    await session.connect_agent(agent, to_agent)
    await ws.send_text(json.dumps({"type": "ready", "sample_rate": c.settings.audio_rate}))
    try:
        while not session.closed.is_set():
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break
            if message.get("bytes"):
                session.agent_audio(message["bytes"])
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("desk stream failed")
    finally:
        if not session.closed.is_set():
            await session.close("the bank's team ended the call")
        try:
            await ws.close()
        except Exception:
            pass
