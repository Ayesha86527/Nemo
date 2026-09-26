"""Preparation pipeline: role-match analysis + deep company research.

Every CV tailoring or cover letter run must pass through this pipeline first
(the gate is enforced by the jobs service). Two LLM calls: a structured
match analysis (JSON) and a free-text deep research brief.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]


class PreparationError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class PrepResult:
    match_score: int | None
    match_summary: str
    research: str
    company: str = ""  # extracted from the JD when the caller didn't know it
    role: str = ""


_MATCH_SYSTEM = """You are a recruiter. Score how well the candidate's CV matches the job description.
Base the score STRICTLY on evidence present in the candidate's CV and the stated job description — never assume an industry, skills, preferences, or experience the documents do not show.
Also identify the hiring company and the job title if they are identifiable in the job description.
Respond with STRICT JSON only, no markdown:
{"match_score": <integer 0-100>, "summary": "<at most 2 sentences: the single strongest match and the single biggest gap, with concrete evidence — no restating of the score, no filler>", "company": "<company name or empty string>", "role": "<job title or empty string>"}
The summary is plain text: no markdown (no **, no #, no bullet symbols)."""

_RESEARCH_SYSTEM = """You are a company and role researcher. Produce a terse brief for a job applicant.
Use exactly these short section headers, each followed by 1-3 sentences:
COMPANY & ROLE — what the company does and what this role owns, strictly as evidenced by the job description. No candidate commentary here.
GAPS & ANGLES — at most 3 bullets pairing one candidate strength and one real gap with the JD. Do NOT restate the match score or fit summary; the caller already has them.
NEXT ACTIONS — 3 short, concrete steps for this application.
Do not assume industry conventions or company facts; frame inferences as such. Plain text only — no markdown formatting (no **, no #, no ---, use the section headers and - bullets as shown). Max 150 words total. Every sentence must earn its place."""


def _parse_match(text: str) -> tuple[int | None, str, str, str]:
    """Returns (score, summary, company, role)."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    candidates = [cleaned]
    # Flat candidates first: models often append prose after the JSON object,
    # which a greedy "{.*}" match swallows into invalid JSON.
    candidates += re.findall(r"\{[^{}]*\}", cleaned, re.DOTALL)
    greedy = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if greedy:
        candidates.append(greedy.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and ("match_score" in data or "summary" in data):
            score = data.get("match_score")
            if isinstance(score, (int, float)) and not isinstance(score, bool):
                score = max(0, min(100, int(score)))
            else:
                score = None
            return (
                score,
                str(data.get("summary") or "").strip(),
                str(data.get("company") or "").strip(),
                str(data.get("role") or "").strip(),
            )
    return None, text.strip(), "", ""


class PreparationPipeline:
    def __init__(self, generate_fn: GenerateFn):
        self._generate = generate_fn

    async def run(
        self, company: str, role: str, job_description: str, cv_context: str
    ) -> PrepResult:
        match_prompt = (
            f"Company: {company}\nRole: {role}\n\n"
            f"JOB DESCRIPTION:\n{job_description}\n\n"
            f"CANDIDATE CV:\n{cv_context.strip() or '(no CV content yet)'}"
        )
        match_response = await self._generate(match_prompt, _MATCH_SYSTEM)
        if match_response.error:
            raise PreparationError(match_response.error, hint=match_response.hint or "")
        match_score, match_summary, found_company, found_role = _parse_match(match_response.text)

        research_prompt = (
            f"Company: {company}\nRole: {role}\n\n"
            f"JOB DESCRIPTION:\n{job_description}\n\n"
            f"Candidate background (for tailoring the talking points):\n"
            f"{cv_context.strip() or '(none)'}"
        )
        research_response = await self._generate(research_prompt, _RESEARCH_SYSTEM)
        if research_response.error:
            raise PreparationError(research_response.error, hint=research_response.hint or "")

        found_company = found_company if company.strip() in ("", "Unknown company") else company
        found_role = found_role if role.strip() in ("", "Unknown role") else role
        return PrepResult(
            match_score=match_score,
            match_summary=match_summary,
            research=research_response.text.strip(),
            company=found_company,
            role=found_role,
        )
