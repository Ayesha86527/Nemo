"""First-upload ingestion: turn the resume's text into structured CV content.

Runs once (when no content exists yet) right after a .docx upload, so CV
Studio immediately shows the parsed skills/experience/projects/achievements
instead of an empty state. Best-effort: any failure degrades to "no content".
"""

import json
import re
from collections.abc import Awaitable, Callable

from app.cv.content import CVContentData, ExperienceItem, ProjectItem
from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

_SYSTEM = """You extract structured content from a resume's plain text.
Return STRICT JSON only, no markdown, matching:
{"skills": ["<skill>", ...],
 "experience": [{"role": "", "company": "", "start": "", "end": "", "description": ""}],
 "projects": [{"name": "", "tech": "", "description": ""}],
 "achievements": ["<measurable achievement>", ...]}
Rules:
- Only extract what the text actually states; never invent details.
- skills: concrete tools, languages, frameworks, and methods (deduplicated).
- experience: every job/internship; keep dates exactly as written, use "" when absent.
- description fields: 1-2 concise sentences summarizing impact.
- achievements: quantified wins, awards, certifications.
- Omit sections you cannot find (use [] for empty lists)."""


def parse_ingest_response(text: str) -> CVContentData | None:
    """Lenient parse: fenced JSON and surrounding chatter are tolerated."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    skills = [str(s).strip() for s in data.get("skills", []) or [] if str(s).strip()]
    experience = [
        ExperienceItem(
            role=str(item.get("role") or "").strip(),
            company=str(item.get("company") or "").strip(),
            start=str(item.get("start") or "").strip(),
            end=str(item.get("end") or "").strip(),
            description=str(item.get("description") or "").strip(),
        )
        for item in data.get("experience", []) or []
        if isinstance(item, dict) and (str(item.get("role") or "").strip() or str(item.get("description") or "").strip())
    ]
    projects = [
        ProjectItem(
            name=str(item.get("name") or "").strip(),
            tech=str(item.get("tech") or "").strip(),
            description=str(item.get("description") or "").strip(),
        )
        for item in data.get("projects", []) or []
        if isinstance(item, dict) and (str(item.get("name") or "").strip() or str(item.get("description") or "").strip())
    ]
    achievements = [
        str(a).strip() for a in data.get("achievements", []) or [] if str(a).strip()
    ]

    content = CVContentData(skills=skills, experience=experience, projects=projects, achievements=achievements)
    return content if (skills or experience or projects or achievements) else None


async def ingest_cv_text(generate_fn: GenerateFn, resume_text: str) -> CVContentData | None:
    if not resume_text.strip():
        return None
    response = await generate_fn(f"Resume text:\n{resume_text}", _SYSTEM)
    if response.error:
        return None
    return parse_ingest_response(response.text)
