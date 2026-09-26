"""Cover letter generation service — two-step pipeline.

Step 1 (analyze): read the JD, CV, and fit analysis in ONE call and produce the
requirement-by-requirement evidence mapping. One call instead of the old
extract-then-map pair halves latency and avoids losing JD context between steps.
Step 2 (draft): write the letter from the mapping.

Only reachable after the preparation pipeline (enforced by the jobs service),
so the letter can reference real match analysis and research.
"""

from collections.abc import Awaitable, Callable

from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

_MAX_CV = 3000   # chars -- keeps each step prompt within model context limits
_MAX_JD = 2500
_MAX_RES = 1200


class CoverLetterError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


# -- Step 1: Requirements + evidence mapping (single call) --------------------

_ANALYZE_SYSTEM = """You align one candidate to one job. Read the job description and the candidate's CV, then output the 5-7 strongest requirements of the role, each mapped to the candidate's best evidence.
Plain text only, one block per requirement, exactly:
  REQUIREMENT: <the requirement, in the JD's own words>
  EVIDENCE: "<verbatim CV quote>" — <one sentence: how it satisfies the requirement>
or, when the CV has no supporting evidence:
  REQUIREMENT: <the requirement>
  GAP: <what is missing>
Never fabricate skills or experience. No preamble, no summary."""


# -- Step 2: Letter drafting --------------------------------------------------

_DRAFT_SYSTEM = """You are a professional cover letter writer. Write a focused 3-4 paragraph cover letter.
Rules:
- Use the evidence mapping to select the 2-3 strongest candidate-job alignment points.
- Reference actual CV text from the evidence mapping — never fabricate experience.
- Paragraph 1: role + company name + one sharp hook sentence (what makes this candidate stand out).
- Paragraphs 2-3: connect specific evidence to the role's top requirements (cite the candidate's actual work).
- Final paragraph: confident, brief call to action (2 sentences max).
- Plain text only. Use the real company name throughout — no placeholders like [Company].
- Tone: professional, direct, specific. Avoid generic phrases like "passionate about" or "team player"."""


def _clip(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated for context length...]"


class CoverLetterService:
    def __init__(self, generate_fn: GenerateFn):
        self._generate = generate_fn

    async def generate(
        self,
        *,
        candidate_name: str,
        company: str,
        role: str,
        job_description: str,
        cv_context: str,
        research: str,
        match_summary: str,
    ) -> tuple[str, str]:
        jd = _clip(job_description, _MAX_JD)
        cv = _clip(cv_context.strip() or "(none)", _MAX_CV)
        res = _clip(research or "", _MAX_RES)
        name = candidate_name or "The candidate"

        # Step 1 -- requirements + evidence mapping in a single call
        step1_prompt = (
            f"Company: {company}\nRole: {role}\n\n"
            f"JOB DESCRIPTION:\n{jd}\n\n"
            f"CANDIDATE CV:\n{cv}\n\n"
            f"MATCH SUMMARY:\n{match_summary or '(none)'}"
        )
        r1 = await self._generate(step1_prompt, _ANALYZE_SYSTEM)
        if r1.error:
            raise CoverLetterError(r1.error, hint=r1.hint or "")
        evidence_map = r1.text.strip()

        # Step 2 -- draft the letter from the mapping
        step2_prompt = (
            f"Candidate name: {name}\nCompany: {company}\nRole: {role}\n\n"
            f"EVIDENCE MAPPING (requirement -> candidate evidence):\n{evidence_map}\n\n"
            f"PREPARATION RESEARCH:\n{res or '(none)'}"
        )
        r2 = await self._generate(step2_prompt, _DRAFT_SYSTEM)
        if r2.error:
            raise CoverLetterError(r2.error, hint=r2.hint or "")

        return r2.text.strip(), r2.provider
