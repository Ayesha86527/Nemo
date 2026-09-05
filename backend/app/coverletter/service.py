"""Cover letter generation service.

Only reachable after the preparation pipeline (enforced by the jobs
service), so the letter can reference real match analysis and research.
"""

from collections.abc import Awaitable, Callable

from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]


class CoverLetterError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


_SYSTEM = """You are a professional cover letter writer. Write a concise, specific cover letter (3-4 short paragraphs, plain text, no placeholders like [Company]).
Ground every claim in the candidate's actual CV content — never fabricate experience.
Use the preparation research to show genuine understanding of the company and role.
Open with the role and a sharp hook, connect 2-3 concrete pieces of candidate evidence to the job's needs, and close with a confident, brief call to action."""


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
        prompt = (
            f"Candidate name: {candidate_name or 'The candidate'}\n"
            f"Company: {company}\nRole: {role}\n\n"
            f"JOB DESCRIPTION:\n{job_description}\n\n"
            f"CANDIDATE CV:\n{cv_context.strip() or '(none)'}\n\n"
            f"MATCH ANALYSIS:\n{match_summary or '(none)'}\n\n"
            f"PREPARATION RESEARCH:\n{research or '(none)'}"
        )
        response = await self._generate(prompt, _SYSTEM)
        if response.error:
            raise CoverLetterError(response.error, hint=response.hint or "")
        return response.text.strip(), response.provider
