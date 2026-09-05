"""Roadmap endpoints: generate, view latest, and track step completion."""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from app.db.engine import engine
from app.db.models import Roadmap, UserProfile
from app.deps import cv_context_text, now_iso
from app.llm.router import generate
from app.market.engine import MarketIntelError
from app.roadmap.service import RoadmapError, RoadmapService, market_context_json

router = APIRouter(prefix="/api/roadmap", tags=["roadmap"])


class GenerateRequest(BaseModel):
    target_role: str = ""
    horizon_weeks: int | None = None
    focus: str = ""
    preferences: str = ""


class StepUpdate(BaseModel):
    milestone: int  # index into milestones list
    step: int  # index into milestone steps
    done: bool


def _progress(milestones: list[dict]) -> dict:
    total = sum(len(m.get("steps", [])) for m in milestones)
    done = sum(1 for m in milestones for s in m.get("steps", []) if s.get("done"))
    return {"done": done, "total": total, "percent": round(done / total * 100) if total else 0}


def _payload(row: Roadmap) -> dict:
    milestones = json.loads(row.milestones_json or "[]")
    return {
        "id": row.id,
        "target_role": row.target_role,
        "goal": row.goal,
        "horizon_weeks": row.horizon_weeks,
        "focus": row.focus,
        "preferences": row.preferences,
        "milestones": milestones,
        "progress": _progress(milestones),
        "provider": row.provider,
        "created_at": row.created_at,
    }


@router.post("/generate")
async def generate_roadmap(req: GenerateRequest):
    target_role = req.target_role.strip()
    horizon = req.horizon_weeks
    if horizon is not None and not 1 <= horizon <= 52:
        raise HTTPException(422, "horizon_weeks must be between 1 and 52.")
    with Session(engine) as session:
        profile = session.exec(select(UserProfile)).first()
        if not target_role and profile is not None:
            target_role = profile.target_roles.strip()

    if not target_role:
        raise HTTPException(422, detail={
            "error": "No target role available.",
            "hint": "Enter a target role in the request or set target roles in your Profile.",
        })

    cv_context = cv_context_text()
    try:
        market_json = await market_context_json(generate, target_role, cv_context)
    except MarketIntelError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc

    with Session(engine) as session:
        prev = session.exec(select(Roadmap).order_by(desc(Roadmap.id))).first()

    focus = req.focus.strip() or (prev.focus if prev else "")
    preferences = req.preferences.strip() or (prev.preferences if prev else "")

    previous_json, previous_horizon = "", None
    if horizon is not None and prev is not None and prev.horizon_weeks != horizon:
        previous_json, previous_horizon = prev.milestones_json, prev.horizon_weeks

    service = RoadmapService(generate)
    try:
        plan, provider = await service.generate(
            target_role,
            cv_context,
            market_report_json=market_json,
            horizon_weeks=horizon,
            previous_plan_json=previous_json,
            previous_horizon_weeks=previous_horizon,
            focus=focus,
            preferences=preferences,
        )
    except RoadmapError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc

    with Session(engine) as session:
        row = Roadmap(
            target_role=target_role,
            goal=plan.goal,
            horizon_weeks=plan.horizon_weeks,
            focus=focus,
            preferences=preferences,
            milestones_json=json.dumps(plan.milestones),
            provider=provider,
            created_at=now_iso(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _payload(row)


@router.get("/latest")
async def latest():
    with Session(engine) as session:
        row = session.exec(select(Roadmap).order_by(desc(Roadmap.id))).first()
        if row is None:
            raise HTTPException(404, "No roadmap yet — generate one from Market Intel or the Nemo agent.")
        return _payload(row)


@router.put("/step")
async def update_step(req: StepUpdate):
    with Session(engine) as session:
        row = session.exec(select(Roadmap).order_by(desc(Roadmap.id))).first()
        if row is None:
            raise HTTPException(404, "No roadmap yet — generate one first.")
        milestones = json.loads(row.milestones_json or "[]")
        try:
            milestones[req.milestone]["steps"][req.step]
        except (IndexError, KeyError, TypeError) as exc:
            raise HTTPException(422, "Unknown milestone or step index.") from exc
        milestones[req.milestone]["steps"][req.step]["done"] = req.done
        row.milestones_json = json.dumps(milestones)
        session.add(row)
        session.commit()
        session.refresh(row)
        return _payload(row)
