"""Nemo agent chat endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, delete, select

from app.agent.service import AgentError, AgentService
from app.db.engine import engine
from app.db.models import AgentMessage
from app.llm.router import generate

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatRequest(BaseModel):
    message: str
    context: str = ""


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
