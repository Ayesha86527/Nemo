"""CV endpoints: persistent file storage, read-only content view, rendering.

CV content is managed through the Nemo Agent (cv_update actions); there is no
manual content entry. Tailoring happens per job application (see
routers/jobs.py); this module exposes `resolve_cv_document` — the fallback
chain the pipeline uses: stored file → document rendered from content.
"""

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlmodel import Session

from app.cv.content import (
    is_empty,
    load_content,
    save_content,
)
from app.cv.extract import (
    SUPPORTED_EXTENSIONS,
    extract_cv_text,
    extract_docx_text,
    get_cv_text_cache,
)
from app.cv.ingest import ingest_cv_text
from app.cv.renderer import render_cv_docx
from app.cv.storage import get_cv_store
from app.db.engine import engine
from app.deps import get_profile_row, now_iso
from app.llm.router import generate

router = APIRouter(prefix="/api/cv", tags=["cv"])

MAX_UPLOAD_MB = 10
DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PDF_MEDIA = "application/pdf"
TXT_MEDIA = "text/plain"


def _media_type(filename: str) -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return PDF_MEDIA
    if name.endswith(".txt"):
        return TXT_MEDIA
    return DOCX_MEDIA


def _status() -> dict:
    store = get_cv_store()
    meta = store.meta()
    with Session(engine) as session:
        content, updated = load_content(session)
    return {
        "has_file": store.exists(),
        "filename": meta.get("filename"),
        "uploaded_at": meta.get("uploaded_at"),
        "has_content": not is_empty(content),
        "content_updated_at": updated or None,
    }


@router.get("/status")
async def status():
    return _status()


@router.post("/file")
async def upload_file(file: UploadFile = File(...)):
    filename = file.filename or ""
    if not filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(422, "Upload a .docx, .pdf, or .txt CV.")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_MB} MB limit.")
    try:
        text = extract_cv_text(data, filename)
    except Exception:
        raise HTTPException(422, "Could not read the file — it may be corrupt or password-protected.")
    if not text.strip():
        raise HTTPException(422, "No extractable text found — a scanned PDF needs a text-based version.")
    get_cv_store().save(data, filename or "cv.docx")
    get_cv_text_cache().invalidate()

    ingested = False
    ingest_error = None
    with Session(engine) as session:
        content, _ = load_content(session)
        content_empty = is_empty(content)
    if content_empty:
        try:
            parsed = await ingest_cv_text(generate, text)
        except Exception as exc:
            parsed = None
            ingest_error = str(exc)
        if parsed is not None:
            with Session(engine) as session:
                save_content(session, parsed, now_iso())
            ingested = True

    return {**_status(), "ingested": ingested, "ingest_error": ingest_error}


@router.get("/file")
async def download_file():
    store = get_cv_store()
    data = store.load()
    if data is None:
        raise HTTPException(404, "No CV uploaded yet.")
    filename = store.meta().get("filename") or "cv.docx"
    return Response(
        content=data,
        media_type=_media_type(filename),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/file")
async def delete_file():
    get_cv_store().delete()
    get_cv_text_cache().invalidate()
    return _status()


@router.get("/content")
async def get_content():
    with Session(engine) as session:
        content, updated = load_content(session)
    return {**content.model_dump(), "updated_at": updated or None}


def render_current_cv() -> bytes:
    """Render the CV from content + profile. Raises ValueError when empty."""
    with Session(engine) as session:
        content, _ = load_content(session)
    profile = get_profile_row()
    if is_empty(content) and not (profile.name or profile.education):
        raise ValueError("Nothing to render yet.")
    return render_cv_docx(content, name=profile.name, email=profile.email, education=profile.education)


@router.post("/render")
async def render(format: str = "docx"):
    """Render the CV from structured content — downloadable as Word or PDF."""
    try:
        data = render_current_cv()
    except ValueError:
        raise HTTPException(
            422,
            detail={
                "error": "Nothing to render yet.",
                "hint": "Ask the Nemo Agent to add skills, experience or achievements to your CV first.",
            },
        )
    fmt = (format or "docx").lower()
    if fmt == "pdf":
        from app.cv.pdf_renderer import render_cv_pdf

        return Response(
            content=render_cv_pdf(data),
            media_type=PDF_MEDIA,
            headers={"Content-Disposition": 'attachment; filename="cv_rendered.pdf"'},
        )
    return Response(
        content=data,
        media_type=DOCX_MEDIA,
        headers={"Content-Disposition": 'attachment; filename="cv_rendered.docx"'},
    )


@router.post("/replace")
async def replace_file():
    """Overwrite the stored CV file with a fresh render of the current content."""
    try:
        data = render_current_cv()
    except ValueError:
        raise HTTPException(
            422,
            detail={
                "error": "Nothing to render yet.",
                "hint": "Ask the Nemo Agent to add CV content first.",
            },
        )
    get_cv_store().save(data, "cv.docx")
    get_cv_text_cache().invalidate()
    return _status()


def resolve_cv_document() -> tuple[bytes, str]:
    """Fallback chain used by the job pipeline: stored file → rendered content."""
    store = get_cv_store()
    data = store.load()
    if data is not None:
        return data, store.meta().get("filename") or "cv.docx"
    try:
        return render_current_cv(), "cv_rendered.docx"
    except ValueError:
        raise HTTPException(
            422,
            detail={
                "error": "No CV available.",
                "hint": "Upload a .docx CV or add your CV content in CV Studio first.",
            },
        )
