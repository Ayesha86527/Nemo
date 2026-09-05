"""Job application workflow orchestration.

Enforces the mandatory gate: role-match analysis + deep research (the
preparation pipeline) must complete before any CV tailoring or cover letter
generation for a given job. Also powers the zero-entry flows: quick
tailor/cover-letter runs auto-create the tracked job, and the agent can drive
the same flow from chat.
"""

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlmodel import Session, desc, select

from app.coverletter.service import CoverLetterService
from app.cv.tailor import CVTailorService, TailorResult
from app.db.engine import engine
from app.db.models import CoverLetter, JobApplication
from app.deps import cv_context_text, get_profile_row, now_iso
from app.llm.providers import LLMResponse
from app.research.pipeline import PreparationPipeline
from app.routers.cv import resolve_cv_document

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

VALID_STATUSES = ("wishlist", "applied", "interview", "offer", "rejected")

PLACEHOLDER_COMPANY = "Unknown company"
PLACEHOLDER_ROLE = "Unknown role"

DEFAULT_EXPORT_DIR = Path.home() / "Downloads"


class JobWorkflowError(Exception):
    def __init__(self, message: str, hint: str = "", status_code: int = 409):
        super().__init__(message)
        self.hint = hint
        self.status_code = status_code


def get_job(job_id: int) -> JobApplication:
    with Session(engine) as session:
        job = session.get(JobApplication, job_id)
    if job is None:
        raise JobWorkflowError("Job application not found.", status_code=404)
    return job


def _assert_prepared(job: JobApplication) -> None:
    if not job.prepared_at:
        raise JobWorkflowError(
            "This job has not been prepared yet.",
            hint="Run Prepare first — Nemo requires role-match analysis and company research before tailoring or cover letters.",
        )


def _research_context(job: JobApplication) -> str:
    match_line = f"Match score: {job.match_score}%" if job.match_score is not None else ""
    return "\n".join(part for part in (match_line, job.match_summary, job.research) if part)


async def prepare_job(generate_fn: GenerateFn, job_id: int) -> JobApplication:
    job = get_job(job_id)
    if not job.job_description.strip():
        raise JobWorkflowError(
            "The job description is missing.",
            hint="Add the job description before preparing — the analysis needs it.",
            status_code=422,
        )
    pipeline = PreparationPipeline(generate_fn)
    result = await pipeline.run(
        company=job.company, role=job.role, job_description=job.job_description, cv_context=cv_context_text()
    )
    with Session(engine) as session:
        row = session.get(JobApplication, job_id)
        row.match_score = result.match_score
        row.match_summary = result.match_summary
        row.research = result.research
        row.prepared_at = now_iso()
        # Auto-created jobs may still carry placeholders — fill them from the JD.
        if result.company and row.company in ("", PLACEHOLDER_COMPANY):
            row.company = result.company
        if result.role and row.role in ("", PLACEHOLDER_ROLE):
            row.role = result.role
        session.commit()
        session.refresh(row)
        return row


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def find_existing_job(
    job_description: str = "", company: str = "", role: str = ""
) -> JobApplication | None:
    """Find an already-tracked job matching the given posting (newest first).

    Prevents duplicate entries when the same JD is processed again. A job
    matches on identical description text, or company (plus role when both
    are given).
    """
    jd = _norm(job_description)
    comp = _norm(company)
    rol = _norm(role)
    with Session(engine) as session:
        jobs = list(session.exec(select(JobApplication).order_by(desc(JobApplication.id))).all())
    if jd:
        for job in jobs:
            if _norm(job.job_description) == jd:
                return job
    if comp:
        for job in jobs:
            if _norm(job.company) != comp:
                continue
            if rol and _norm(job.role) not in (rol, _norm(PLACEHOLDER_ROLE), ""):
                continue
            return job
    return None


async def quick_prepare(
    generate_fn: GenerateFn, job_description: str, company: str = "", role: str = ""
) -> JobApplication:
    """Auto-track a job from a bare job description, then run the gate.

    Company/role are optional — preparation extracts them from the JD when
    possible, so the user never has to fill forms. An already-tracked job
    matching the posting is reused instead of duplicated.
    """
    jd = job_description.strip()
    if not jd:
        raise JobWorkflowError(
            "The job description is missing.",
            hint="Paste the job posting — Nemo creates and prepares the tracked job automatically.",
            status_code=422,
        )
    existing = find_existing_job(job_description=jd, company=company, role=role)
    if existing is not None:
        with Session(engine) as session:
            row = session.get(JobApplication, existing.id)
            if not row.job_description.strip():
                row.job_description = jd
            if company.strip() and row.company in ("", PLACEHOLDER_COMPANY):
                row.company = company.strip()
            if role.strip() and row.role in ("", PLACEHOLDER_ROLE):
                row.role = role.strip()
            session.commit()
            session.refresh(row)
        return await prepare_job(generate_fn, row.id)
    with Session(engine) as session:
        row = JobApplication(
            company=company.strip() or PLACEHOLDER_COMPANY,
            role=role.strip() or PLACEHOLDER_ROLE,
            job_description=jd,
            created_at=now_iso(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
    return await prepare_job(generate_fn, row.id)


async def tailor_job(generate_fn: GenerateFn, job_id: int) -> tuple[bytes, str, TailorResult]:
    """Returns (docx_bytes, source_filename, result). Raises on gate failure."""
    job = get_job(job_id)
    _assert_prepared(job)
    data, source_name = resolve_cv_document()
    service = CVTailorService(generate_fn)
    result = await service.tailor(data, job.job_description, research=_research_context(job))
    return result.output, source_name, result


async def create_cover_letter(generate_fn: GenerateFn, job_id: int) -> CoverLetter:
    job = get_job(job_id)
    _assert_prepared(job)
    profile = get_profile_row()
    service = CoverLetterService(generate_fn)
    text, provider = await service.generate(
        candidate_name=profile.name,
        company=job.company,
        role=job.role,
        job_description=job.job_description,
        cv_context=cv_context_text(),
        research=job.research,
        match_summary=job.match_summary,
    )
    with Session(engine) as session:
        row = CoverLetter(job_id=job_id, content=text, provider=provider, created_at=now_iso())
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def list_cover_letters(job_id: int) -> list[CoverLetter]:
    with Session(engine) as session:
        return list(
            session.exec(
                select(CoverLetter).where(CoverLetter.job_id == job_id).order_by(CoverLetter.created_at)
            ).all()
        )


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9 _-]", "", text).strip() or "job"


async def tailor_to_file(
    generate_fn: GenerateFn, job_id: int, export_dir: Path = DEFAULT_EXPORT_DIR
) -> Path:
    """Tailor and save the result to disk (used by the agent flow)."""
    output, _, _ = await tailor_job(generate_fn, job_id)
    job = get_job(job_id)
    path = Path(export_dir) / f"CV_{_safe_name(job.company)}_tailored.docx"
    path.write_bytes(output)
    return path


async def cover_letter_to_file(
    generate_fn: GenerateFn, job_id: int, export_dir: Path = DEFAULT_EXPORT_DIR
) -> tuple[CoverLetter, Path]:
    """Generate, persist, and export the cover letter (used by the agent flow)."""
    letter = await create_cover_letter(generate_fn, job_id)
    job = get_job(job_id)
    path = Path(export_dir) / f"CoverLetter_{_safe_name(job.company)}.txt"
    path.write_text(letter.content, encoding="utf-8")
    return letter, path
