"""Nemo agent chat endpoints."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.agent.service import AgentError, AgentService, onboarding_greeting
from app.cv.content import is_empty, load_content
from app.db.engine import engine
from app.db.models import AgentMessage, UserProfile
from app.llm.router import generate

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatRequest(BaseModel):
    message: str
    context: str = ""


@router.get("/greeting")
async def greeting():
    """Deterministic launch greeting + onboarding state for the Nemo panel."""
    with Session(engine) as session:
        profile = session.exec(select(UserProfile)).first()
        content, _ = load_content(session)
    has_name = bool(profile and profile.name.strip())
    has_experience = not is_empty(content) or bool(profile and profile.education.strip())
    has_goals = bool(profile and (profile.short_term_goal.strip() or profile.long_term_goal.strip()))
    return {
        "greeting": onboarding_greeting(has_name=has_name, has_experience=has_experience, has_goals=has_goals),
        "onboarded": has_name and has_experience and has_goals,
        "missing": [
            label
            for label, ok in (
                ("name", has_name),
                ("experience", has_experience),
                ("goals", has_goals),
            )
            if not ok
        ],
    }


@router.post("/chat")
async def chat(req: ChatRequest):
    if not req.message.strip():
        raise HTTPException(422, "message must not be empty")
    service = AgentService(generate_fn=generate)
    try:
        turn = await service.chat(req.message.strip(), context=req.context)
    except AgentError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    return {"reply": turn.reply, "changes": turn.changes, "cv_updated": turn.cv_updated}


@router.post("/stream")
async def stream_chat(req: ChatRequest):
    """Streaming agent chat via Server-Sent Events.

    Clients read the event stream until a 'done' or 'error' event arrives.
    Each event is a JSON object: {"type": "chunk"|"done"|"error", ...}
    """
    if not req.message.strip():
        raise HTTPException(422, "message must not be empty")
    from app.llm.router import stream as stream_fn

    service = AgentService(generate_fn=generate, stream_fn=stream_fn)

    async def event_generator():
        async for event in service.stream_chat(req.message.strip(), context=req.context):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



@router.get("/history")
async def history(limit: int = 100):
    with Session(engine) as session:
        rows = session.exec(select(AgentMessage).order_by(AgentMessage.id.desc()).limit(limit)).all()
        return [
            {"role": r.role, "content": r.content, "created_at": r.created_at}
            for r in reversed(rows)
        ]


@router.delete("/history")
async def clear_history():
    with Session(engine) as session:
        session.exec(delete(AgentMessage))
        session.commit()
    return {"cleared": True}
