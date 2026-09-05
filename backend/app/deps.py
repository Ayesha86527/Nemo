"""Composition root: shared accessors for the single source of truth.

The knowledge base was consolidated into CV Studio — everything Nemo knows
about the user lives in SQLite (profile + CV content). Services receive this
context directly instead of querying a vector store.
"""

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.cv.content import CVContentData, load_content, to_text
from app.cv.extract import get_cv_text
from app.db.engine import engine
from app.db.models import UserProfile


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_profile_row() -> UserProfile:
    with Session(engine) as session:
        profile = session.exec(select(UserProfile)).first()
        if profile is None:
            profile = UserProfile()
            session.add(profile)
            session.commit()
            session.refresh(profile)
        return profile


def load_cv_content() -> tuple[CVContentData, str]:
    with Session(engine) as session:
        return load_content(session)


def cv_context_text() -> str:
    """Plain-text picture of the user — the evidence base for every LLM feature.

    Combines the structured CV Studio content with the text of the uploaded
    .docx so analyses are grounded in the actual resume even before the user
    enters anything manually.
    """
    content, _ = load_cv_content()
    profile = get_profile_row()
    parts: list[str] = []
    body = to_text(content, profile.name, profile.education)
    if body.strip():
        parts.append(body)
    cv_text = get_cv_text().strip()
    if cv_text:
        parts.append(f"UPLOADED CV DOCUMENT:\n{cv_text}")
    if profile.target_roles:
        parts.append(f"Target roles: {profile.target_roles}")
    return "\n\n".join(parts)
