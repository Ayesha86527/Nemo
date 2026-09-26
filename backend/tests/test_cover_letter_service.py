"""Tests for the cover letter service."""

import pytest

from app.coverletter.service import CoverLetterError, CoverLetterService
from app.llm.providers import LLMResponse


def _service(error: str | None = None, seen: list | None = None):
    async def generate(prompt: str, system: str | None = None):
        if seen is not None:
            seen.append(prompt)
        if error:
            return LLMResponse(text="", provider="stub", model="m", error=error, hint="check keys")
        return LLMResponse(text="Dear Hiring Manager...", provider="stub", model="m")

    return CoverLetterService(generate)


class TestCoverLetterService:
    async def test_returns_text_and_provider(self):
        text, provider = await _service().generate(
            candidate_name="Ayesha",
            company="Acme",
            role="Backend Engineer",
            job_description="Python",
            cv_context="Skills: Python",
            research="Acme values scale.",
            match_summary="Strong match.",
        )
        assert text == "Dear Hiring Manager..."
        assert provider == "stub"

    async def test_prompt_contains_preparation_context(self):
        seen: list = []
        await _service(seen=seen).generate(
            candidate_name="Ayesha",
            company="Acme",
            role="Backend Engineer",
            job_description="Python",
            cv_context="Skills: Python",
            research="Acme values scale.",
            match_summary="Strong match.",
        )
        # Two-step pipeline: analysis (JD + CV + match) then drafting (mapping
        # + research + name) — preparation context must reach the step that
        # can actually use it.
        step1, step2 = seen
        assert "Acme" in step1 and "Python" in step1 and "Strong match." in step1
        assert "Skills: Python" in step1
        assert "Acme values scale." in step2 and "Ayesha" in step2

    async def test_llm_error_raises_with_hint(self):
        service = _service(error="rate limited")
        with pytest.raises(CoverLetterError) as exc_info:
            await service.generate(
                candidate_name="",
                company="Acme",
                role="R",
                job_description="JD",
                cv_context="",
                research="",
                match_summary="",
            )
        assert exc_info.value.hint
