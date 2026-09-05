"""Tests for the preparation pipeline (role-match + company research)."""

import json

import pytest

from app.llm.providers import LLMResponse
from app.research.pipeline import PreparationError, PreparationPipeline, _parse_match

MATCH_JSON = json.dumps({"match_score": 68, "summary": "Strong Python, gaps in K8s."})


class TestParseMatch:
    def test_parses_valid_json(self):
        score, summary, company, role = _parse_match(MATCH_JSON)
        assert score == 68
        assert summary == "Strong Python, gaps in K8s."
        assert company == ""
        assert role == ""

    def test_strips_markdown_fences(self):
        score, *_ = _parse_match("```json\n" + MATCH_JSON + "\n```")
        assert score == 68

    def test_clamps_score_to_range(self):
        score, *_ = _parse_match(json.dumps({"match_score": 140, "summary": "x"}))
        assert score == 100

    def test_non_numeric_score_returns_none(self):
        score, summary, _, _ = _parse_match(json.dumps({"match_score": "high", "summary": "ok"}))
        assert score is None
        assert summary == "ok"

    def test_garbage_degrades_to_raw_text(self):
        score, summary, company, role = _parse_match("Sorry, cannot score.")
        assert score is None
        assert "cannot score" in summary
        assert company == ""
        assert role == ""

    def test_json_with_trailing_prose(self):
        score, summary, _, _ = _parse_match(MATCH_JSON + "\n\nHere is my reasoning for the score.")
        assert score == 68
        assert summary.startswith("Strong Python")

    def test_extracts_company_and_role(self):
        text = json.dumps(
            {"match_score": 55, "summary": "ok", "company": "Acme Corp", "role": "Backend Engineer"}
        )
        score, summary, company, role = _parse_match(text)
        assert score == 55
        assert company == "Acme Corp"
        assert role == "Backend Engineer"


def _pipeline(responses: list[str], error: str | None = None):
    async def generate(prompt: str, system: str | None = None):
        if error:
            return LLMResponse(text="", provider="stub", model="m", error=error, hint="check keys")
        return LLMResponse(text=responses.pop(0), provider="stub", model="m")

    return PreparationPipeline(generate)


class TestPreparationPipeline:
    async def test_runs_two_llm_calls_and_returns_result(self):
        pipeline = _pipeline([MATCH_JSON, "Company makes widgets. Talk about scale."])
        result = await pipeline.run("Acme", "Backend Engineer", "Need Python", "Skills: Python")
        assert result.match_score == 68
        assert result.match_summary.startswith("Strong Python")
        assert "widgets" in result.research

    async def test_first_call_error_raises_preparation_error(self):
        pipeline = _pipeline([], error="provider down")
        with pytest.raises(PreparationError) as exc_info:
            await pipeline.run("Acme", "Role", "JD", "CV")
        assert exc_info.value.hint

    async def test_second_call_error_also_raises(self):
        async def generate(prompt, system=None):
            if "researcher" in (system or ""):
                return LLMResponse(text="", provider="stub", model="m", error="timeout", hint="retry")
            return LLMResponse(text=MATCH_JSON, provider="stub", model="m")

        pipeline = PreparationPipeline(generate)
        with pytest.raises(PreparationError):
            await pipeline.run("Acme", "Role", "JD", "CV")

    async def test_cv_context_reaches_both_prompts(self):
        seen = []

        async def generate(prompt, system=None):
            seen.append(prompt)
            if "recruiter" in (system or ""):
                return LLMResponse(text=MATCH_JSON, provider="stub", model="m")
            return LLMResponse(text="research brief", provider="stub", model="m")

        pipeline = PreparationPipeline(generate)
        await pipeline.run("Acme", "Role", "JD", "Experience: built Nemo")
        assert len(seen) == 2
        assert all("built Nemo" in p for p in seen)

    async def test_company_role_extracted_into_result(self):
        match = json.dumps(
            {"match_score": 70, "summary": "good", "company": "Nova Labs", "role": "ML Engineer"}
        )
        pipeline = _pipeline([match, "research brief"])
        result = await pipeline.run("", "", "JD text", "CV")
        assert result.company == "Nova Labs"
        assert result.role == "ML Engineer"

    async def test_explicit_company_role_take_precedence(self):
        match = json.dumps(
            {"match_score": 70, "summary": "good", "company": "Nova Labs", "role": "ML Engineer"}
        )
        pipeline = _pipeline([match, "research brief"])
        result = await pipeline.run("Acme", "Backend Engineer", "JD", "CV")
        assert result.company == "Acme"
        assert result.role == "Backend Engineer"
