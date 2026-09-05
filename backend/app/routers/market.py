import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from app.db.engine import engine
from app.db.models import MarketIntelReport
from app.deps import cv_context_text
from app.llm.router import generate
from app.market.engine import MarketIntelError, MarketIntelligenceEngine, is_due, parse_report

router = APIRouter(prefix="/api/market", tags=["market"])


class RunRequest(BaseModel):
    target_role: str


def _latest(session: Session) -> MarketIntelReport | None:
    return session.exec(
        select(MarketIntelReport).order_by(desc(MarketIntelReport.created_at))
    ).first()


def _report_payload(row: MarketIntelReport) -> dict:
    """Old rows may hold markdown; parse_report degrades that into a summary."""
    return {
        "id": row.id,
        "created_at": row.created_at,
        "target_role": row.target_role,
        "report": parse_report(row.gap_report) if row.gap_report else None,
        "provider": row.provider,
    }


@router.get("/status")
async def status():
    with Session(engine) as session:
        latest = _latest(session)
        return {
            "last_run_at": latest.created_at if latest else None,
            "due": is_due(latest.created_at if latest else None),
        }


@router.post("/run")
async def run(req: RunRequest):
    if not req.target_role.strip():
        raise HTTPException(422, "target_role must not be empty")
    engine_ = MarketIntelligenceEngine(generate_fn=generate)
    try:
        report = await engine_.run_gap_analysis(req.target_role.strip(), cv_context=cv_context_text())
    except MarketIntelError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc

    with Session(engine) as session:
        row = MarketIntelReport(
            created_at=report.created_at,
            target_role=report.target_role,
            gap_report=json.dumps(report.report),
            provider=report.provider,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return _report_payload(row)


@router.get("/reports")
async def reports(limit: int = 10):
    with Session(engine) as session:
        rows = session.exec(
            select(MarketIntelReport).order_by(desc(MarketIntelReport.created_at)).limit(limit)
        ).all()
        return [_report_payload(r) for r in rows]
