"""Job tracker + gated preparation/tailoring/cover-letter workflow."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, delete, desc, select

from app.coverletter.service import CoverLetterError
from app.cv.tailor import TailoringError
from app.db.engine import engine
from app.db.models import CoverLetter, JobApplication
from app.deps import now_iso
from app.jobs.service import (
    PLACEHOLDER_COMPANY,
    PLACEHOLDER_ROLE,
    VALID_STATUSES,
    JobWorkflowError,
    create_cover_letter,
    list_cover_letters,
    prepare_job,
    quick_prepare,
    tailor_job,
)
from app.llm.router import generate
from app.research.pipeline import PreparationError

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class JobCreate(BaseModel):
    company: str = ""
    role: str = ""
    job_description: str = ""
    status: str = "wishlist"
    applied_at: str = ""
    follow_up_at: str = ""
    notes: str = ""


class JobUpdate(BaseModel):
    company: str | None = None
    role: str | None = None
    job_description: str | None = None
    status: str | None = None
    applied_at: str | None = None
    follow_up_at: str | None = None
    notes: str | None = None


class QuickRequest(BaseModel):
    job_description: str
    company: str = ""
    role: str = ""


def _validate_status(status: str) -> None:
    if status not in VALID_STATUSES:
        raise HTTPException(422, f"status must be one of {', '.join(VALID_STATUSES)}")


def _get_or_404(job_id: int) -> JobApplication:
    with Session(engine) as session:
        job = session.get(JobApplication, job_id)
    if job is None:
        raise HTTPException(404, "Job application not found.")
    return job


def _out(job: JobApplication) -> dict:
    return {
        "id": job.id,
        "company": job.company,
        "role": job.role,
        "job_description": job.job_description,
        "status": job.status,
        "applied_at": job.applied_at,
        "follow_up_at": job.follow_up_at,
        "notes": job.notes,
        "match_score": job.match_score,
        "match_summary": job.match_summary,
        "research": job.research,
        "prepared": bool(job.prepared_at),
        "prepared_at": job.prepared_at,
        "created_at": job.created_at,
    }


def _letter_out(letter: CoverLetter) -> dict:
    return {
        "id": letter.id,
        "job_id": letter.job_id,
        "content": letter.content,
        "provider": letter.provider,
        "created_at": letter.created_at,
    }


def _workflow_error_response(exc: JobWorkflowError):
    return HTTPException(exc.status_code, detail={"error": str(exc), "hint": exc.hint})


@router.get("")
async def list_jobs():
    with Session(engine) as session:
        rows = session.exec(select(JobApplication).order_by(desc(JobApplication.created_at))).all()
        return [_out(r) for r in rows]


@router.post("")
async def create_job(payload: JobCreate):
    if not payload.company.strip() and not payload.role.strip():
        raise HTTPException(422, "company or role is required")
    _validate_status(payload.status)
    with Session(engine) as session:
        row = JobApplication(
            company=payload.company.strip() or PLACEHOLDER_COMPANY,
            role=payload.role.strip() or PLACEHOLDER_ROLE,
            job_description=payload.job_description.strip(),
            status=payload.status,
            applied_at=payload.applied_at,
            follow_up_at=payload.follow_up_at,
            notes=payload.notes,
            created_at=now_iso(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return _out(row)


@router.put("/{job_id}")
async def update_job(job_id: int, payload: JobUpdate):
    job = _get_or_404(job_id)
    if payload.status is not None:
        _validate_status(payload.status)
    with Session(engine) as session:
        row = session.get(JobApplication, job_id)
        for field_name, value in payload.model_dump(exclude_none=True).items():
            setattr(row, field_name, value)
        # A changed job description invalidates the preparation pipeline output.
        if payload.job_description is not None and row.prepared_at:
            row.prepared_at = ""
            row.match_score = None
            row.match_summary = ""
            row.research = ""
        session.commit()
        session.refresh(row)
        return _out(row)


@router.delete("/{job_id}")
async def delete_job(job_id: int):
    _get_or_404(job_id)
    with Session(engine) as session:
        session.exec(delete(CoverLetter).where(CoverLetter.job_id == job_id))
        row = session.get(JobApplication, job_id)
        session.delete(row)
        session.commit()
    return {"deleted": job_id}


@router.post("/quick-tailor")
async def quick_tailor(payload: QuickRequest):
    """Zero-entry flow: paste a JD → the job is tracked, prepared, and tailored."""
    try:
        job = await quick_prepare(generate, payload.job_description, payload.company, payload.role)
        output, source_name, result = await tailor_job(generate, job.id)
    except JobWorkflowError as exc:
        raise _workflow_error_response(exc) from exc
    except PreparationError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    except TailoringError as exc:
        raise HTTPException(422, detail={"error": str(exc), "hint": exc.hint}) from exc

    safe_company = "".join(c for c in job.company if c.isalnum() or c in " -_").strip() or "company"
    return Response(
        content=output,
        media_type=DOCX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="CV_{safe_company}_tailored.docx"',
            "X-Job-Id": str(job.id),
            "X-Job-Company": job.company,
            "X-Edits-Applied": str(result.edits_applied),
            "X-Edits-Skipped": str(result.edits_skipped),
            "X-Tailor-Summary": result.summary[:500],
            "X-Source": source_name,
        },
    )


@router.post("/quick-cover-letter")
async def quick_cover_letter(payload: QuickRequest):
    """Zero-entry flow: paste a JD → the job is tracked, prepared, and gets a cover letter."""
    try:
        job = await quick_prepare(generate, payload.job_description, payload.company, payload.role)
        letter = await create_cover_letter(generate, job.id)
    except JobWorkflowError as exc:
        raise _workflow_error_response(exc) from exc
    except PreparationError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    except CoverLetterError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    return {"job": _out(job), "letter": _letter_out(letter)}


@router.post("/{job_id}/prepare")
async def prepare(job_id: int):
    _get_or_404(job_id)
    try:
        row = await prepare_job(generate, job_id)
    except JobWorkflowError as exc:
        raise _workflow_error_response(exc) from exc
    except PreparationError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    return _out(row)


@router.post("/{job_id}/tailor")
async def tailor(job_id: int):
    try:
        output, source_name, result = await tailor_job(generate, job_id)
    except JobWorkflowError as exc:
        raise _workflow_error_response(exc) from exc
    except TailoringError as exc:
        raise HTTPException(422, detail={"error": str(exc), "hint": exc.hint}) from exc

    job = _get_or_404(job_id)
    safe_company = "".join(c for c in job.company if c.isalnum() or c in " -_").strip() or "company"
    out_name = f"CV_{safe_company}_tailored.docx"
    return Response(
        content=output,
        media_type=DOCX_MEDIA,
        headers={
            "Content-Disposition": f'attachment; filename="{out_name}"',
            "X-Edits-Applied": str(result.edits_applied),
            "X-Edits-Skipped": str(result.edits_skipped),
            "X-Tailor-Summary": result.summary[:500],
            "X-Source": source_name,
        },
    )


@router.post("/{job_id}/cover-letter")
async def cover_letter(job_id: int):
    try:
        row = await create_cover_letter(generate, job_id)
    except JobWorkflowError as exc:
        raise _workflow_error_response(exc) from exc
    except CoverLetterError as exc:
        raise HTTPException(502, detail={"error": str(exc), "hint": exc.hint}) from exc
    return _letter_out(row)


@router.get("/{job_id}/cover-letters")
async def cover_letters(job_id: int):
    _get_or_404(job_id)
    return [_letter_out(r) for r in list_cover_letters(job_id)]
