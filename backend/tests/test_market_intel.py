"""Tests for the market intelligence engine (structured report output)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.llm.providers import LLMResponse
from app.market.engine import (
    GapReport,
    LLMInsightSource,
    MarketIntelError,
    MarketIntelligenceEngine,
    is_due,
    parse_report,
)

VALID_JSON = json.dumps(
    {
        "match_score": 72,
        "summary": "Solid backend foundation.",
        "skill_gaps": [
            {"skill": "Docker", "status": "missing", "note": "no evidence"},
            {"skill": "FastAPI", "status": "have", "note": "built services"},
            {"skill": "SQL", "status": "partial", "note": "some usage"},
        ],
        "market_signals": ["Docker", "Kubernetes"],
        "recommendations": ["Ship a containerized project"],
    }
)


def _stub_llm(text_by_marker: dict[str, str], error: str | None = None):
    async def generate(prompt: str, system: str | None = None):
        if error:
            return LLMResponse(text="", provider="stub_error", model="m", error=error, hint="hint")
        for marker, text in text_by_marker.items():
            if marker in prompt:
                return LLMResponse(text=text, provider="stub", model="m")
        return LLMResponse(text="generic answer", provider="stub", model="m")

    return generate


class TestCadence:
    def test_due_when_never_run(self):
        assert is_due(None) is True

    def test_not_due_within_week(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert is_due(recent) is False

    def test_due_after_week(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        assert is_due(old) is True


class TestLLMInsightSource:
    async def test_gathers_market_context(self):
        source = LLMInsightSource(_stub_llm({"Backend Engineer": "FastAPI and cloud skills are hot"}))
        context = await source.gather("Backend Engineer")
        assert "FastAPI" in context

    async def test_llm_failure_raises_market_intel_error(self):
        source = LLMInsightSource(_stub_llm({}, error="provider down"))
        with pytest.raises(MarketIntelError):
            await source.gather("Backend Engineer")


class TestParseReport:
    def test_parses_valid_json(self):
        report = parse_report(VALID_JSON)
        assert report["match_score"] == 72
        assert report["summary"] == "Solid backend foundation."
        assert len(report["skill_gaps"]) == 3
        assert report["skill_gaps"][0] == {"skill": "Docker", "status": "missing", "note": "no evidence"}
        assert report["market_signals"] == ["Docker", "Kubernetes"]

    def test_strips_markdown_fences(self):
        report = parse_report("```json\n" + VALID_JSON + "\n```")
        assert report["match_score"] == 72

    def test_clamps_match_score(self):
        report = parse_report(json.dumps({"match_score": 140, "summary": "x"}))
        assert report["match_score"] == 100

    def test_invalid_status_falls_back_to_missing(self):
        report = parse_report(json.dumps({"skill_gaps": [{"skill": "Go", "status": "kinda"}]}))
        assert report["skill_gaps"][0]["status"] == "missing"

    def test_raw_text_degrades_to_summary(self):
        report = parse_report("# Gap Analysis\nplain markdown, no json")
        assert report["match_score"] is None
        assert "Gap Analysis" in report["summary"]
        assert report["skill_gaps"] == []


class TestMarketIntelligenceEngine:
    CV_CONTEXT = "Skills: Python, FastAPI\nBackend Engineer at Acme (Jan 2022 - Present)"

    def _engine(self, error=None, llm_text=VALID_JSON):
        async def generate(prompt, system=None):
            if error:
                return LLMResponse(text="", provider="stub_error", model="m", error=error, hint="h")
            if "career strategist" in (system or "").lower():
                return LLMResponse(text=llm_text, provider="stub", model="m")
            if "job market" in prompt:
                return LLMResponse(text="Docker and k8s are in demand", provider="stub", model="m")
            return LLMResponse(text="generic answer", provider="stub", model="m")

        return MarketIntelligenceEngine(generate_fn=generate)

    async def test_run_gap_analysis_produces_structured_report(self):
        engine = self._engine()
        report = await engine.run_gap_analysis("Backend Engineer", cv_context=self.CV_CONTEXT)
        assert isinstance(report, GapReport)
        assert report.target_role == "Backend Engineer"
        assert report.report["match_score"] == 72
        assert report.report["skill_gaps"]

    async def test_cv_context_included_in_prompt(self):
        seen = []
        engine = self._engine()

        async def spy_generate(prompt, system=None):
            seen.append(prompt)
            return LLMResponse(text=VALID_JSON, provider="stub", model="m")

        engine._generate = spy_generate
        await engine.run_gap_analysis("Backend Engineer", cv_context=self.CV_CONTEXT)
        assert any("Backend Engineer at Acme" in p for p in seen)

    async def test_llm_error_raises_with_hint(self):
        engine = self._engine(error="Ollama is not reachable.")
        with pytest.raises(MarketIntelError) as exc_info:
            await engine.run_gap_analysis("Backend Engineer")
        assert exc_info.value.hint

    async def test_empty_cv_still_runs(self):
        engine = self._engine()
        report = await engine.run_gap_analysis("Backend Engineer", cv_context="")
        assert report.report["match_score"] == 72

    async def test_unparseable_llm_output_degrades_to_summary(self):
        engine = self._engine(llm_text="Sorry, I cannot comply.")
        report = await engine.run_gap_analysis("Backend Engineer")
        assert report.report["match_score"] is None
        assert "cannot comply" in report.report["summary"]
