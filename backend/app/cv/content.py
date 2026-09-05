"""Manually-entered CV content: schemas, persistence, and text projection.

Structured content (skills / experience / projects / achievements) is the
single source of truth for rendering, the agent, and every LLM feature.
"""

import json

from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.models import CVContent


class ExperienceItem(BaseModel):
    role: str = ""
    company: str = ""
    start: str = ""  # free-form: "Jan 2022", "2022"
    end: str = ""  # free-form: "Present", "2024"
    description: str = ""


class ProjectItem(BaseModel):
    name: str = ""
    tech: str = ""  # e.g. "Python, FastAPI"
    description: str = ""


class CVContentData(BaseModel):
    skills: list[str] = []
    experience: list[ExperienceItem] = []
    projects: list[ProjectItem] = []
    achievements: list[str] = []


def is_empty(content: CVContentData) -> bool:
    return not (content.skills or content.experience or content.projects or content.achievements)


def load_content(session: Session) -> tuple[CVContentData, str]:
    """Returns (content, updated_at). Empty content when nothing was saved."""
    row = session.exec(select(CVContent)).first()
    if row is None:
        return CVContentData(), ""
    return (
        CVContentData(
            skills=json.loads(row.skills_json or "[]"),
            experience=[ExperienceItem(**e) for e in json.loads(row.experience_json or "[]")],
            projects=[ProjectItem(**p) for p in json.loads(row.projects_json or "[]")],
            achievements=json.loads(row.achievements_json or "[]"),
        ),
        row.updated_at,
    )


def save_content(session: Session, data: CVContentData, updated_at: str) -> CVContent:
    row = session.exec(select(CVContent)).first()
    if row is None:
        row = CVContent()
        session.add(row)
    row.skills_json = json.dumps(data.skills)
    row.experience_json = json.dumps([e.model_dump() for e in data.experience])
    row.projects_json = json.dumps([p.model_dump() for p in data.projects])
    row.achievements_json = json.dumps(data.achievements)
    row.updated_at = updated_at
    session.commit()
    session.refresh(row)
    return row


def to_text(content: CVContentData, profile_name: str = "", education: str = "") -> str:
    """Plain-text projection used as LLM evidence."""
    parts: list[str] = []
    if profile_name:
        parts.append(profile_name)
    if content.skills:
        parts.append("Skills: " + ", ".join(content.skills))
    for item in content.experience:
        line = item.role
        if item.company:
            line += f" at {item.company}"
        if item.start or item.end:
            line += f" ({item.start} - {item.end})"
        parts.append(line)
        if item.description:
            parts.append(item.description)
    for project in content.projects:
        line = f"Project: {project.name}"
        if project.tech:
            line += f" ({project.tech})"
        parts.append(line)
        if project.description:
            parts.append(project.description)
    if content.achievements:
        parts.append("Achievements:\n" + "\n".join(f"- {a}" for a in content.achievements))
    if education:
        parts.append(f"Education: {education}")
    return "\n\n".join(parts)
