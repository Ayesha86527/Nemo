"""Tests for the roadmap service and parser."""

import json

import pytest
from sqlmodel import Session

from app.db.models import MarketIntelReport
from app.llm.providers import LLMResponse
from app.roadmap.service import RoadmapError, RoadmapPlan, RoadmapService, latest_market_json, parse_roadmap

VALID = json.dumps(
    {
        "goal": "Land a backend role",
        "horizon_weeks": 10,
        "milestones": [
            {"title": "Foundations", "focus": "Core", "steps": ["Learn FastAPI", "Build API"]},
            {"title": "Depth", "focus": "Scale", "steps": ["Add caching"]},
        ],
    }
)


class TestParseRoadmap:
    def test_parses_valid_json(self):
        plan = parse_roadmap(VALID)
        assert plan.goal == "Land a backend role"
        assert plan.horizon_weeks == 10
        assert len(plan.milestones) == 2
        assert plan.milestones[0]["steps"][0] == {"task": "Learn FastAPI", "done": False}

    def test_strips_markdown_fences(self):
        plan = parse_roadmap("```json\n" + VALID + "\n```")
        assert plan.horizon_weeks == 10

    def test_no_json_raises(self):
        with pytest.raises(RoadmapError):
            parse_roadmap("I cannot produce a roadmap.")

    def test_empty_milestones_raise(self):
        with pytest.raises(RoadmapError):
            parse_roadmap(json.dumps({"goal": "x", "milestones": []}))

    def test_milestones_without_steps_are_dropped(self):
        data = json.loads(VALID)
        data["milestones"].append({"title": "Empty", "steps": []})
        plan = parse_roadmap(json.dumps(data))
        assert len(plan.milestones) == 2

    def test_bad_horizon_defaults_to_12(self):
        data = json.loads(VALID)
        data["horizon_weeks"] = "ten"
        assert parse_roadmap(json.dumps(data)).horizon_weeks == 12


class TestRoadmapService:
    def test_uses_market_report_for_the_requested_role(self, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(MarketIntelReport(target_role="Museum educator", gap_report='{"role":"museum"}'))
            session.add(MarketIntelReport(target_role="Community outreach coordinator", gap_report='{"role":"outreach"}'))
            session.commit()

        assert latest_market_json("Community outreach coordinator") == '{"role":"outreach"}'
        assert latest_market_json("Museum educator") == '{"role":"museum"}'
        assert latest_market_json("Unrelated role") == ""

    async def test_generate_returns_plan_and_provider(self):
        async def generate(prompt, system=None):
            return LLMResponse(text=VALID, provider="stub", model="m")

        plan, provider = await RoadmapService(generate).generate("Backend Engineer", "Skills: Python")
        assert isinstance(plan, RoadmapPlan)
        assert provider == "stub"

    async def test_market_report_injected_when_provided(self):
        seen = []

        async def generate(prompt, system=None):
            seen.append(prompt)
            return LLMResponse(text=VALID, provider="stub", model="m")

        await RoadmapService(generate).generate(
            "Backend Engineer", "Skills: Python", market_report_json='{"match_score": 40}'
        )
        assert '{"match_score": 40}' in seen[0]

    async def test_llm_error_raises_with_hint(self):
        async def generate(prompt, system=None):
            return LLMResponse(text="", provider="stub", model="m", error="down", hint="retry")

        with pytest.raises(RoadmapError) as exc_info:
            await RoadmapService(generate).generate("Backend Engineer", "")
        assert exc_info.value.hint

    async def test_horizon_overrides_model_output(self):
        async def generate(prompt, system=None):
            return LLMResponse(text=VALID, provider="stub", model="m")

        plan, _ = await RoadmapService(generate).generate("Backend Engineer", "CV", horizon_weeks=6)
        assert plan.horizon_weeks == 6

    async def test_prompt_requires_evidence_to_outcome_and_honors_preferences(self):
        seen = []

        async def generate(prompt, system=None):
            seen.append((prompt, system))
            return LLMResponse(text=VALID, provider="stub", model="m")

        await RoadmapService(generate).generate(
            "Community outreach coordinator", "Coordinated neighborhood events", horizon_weeks=6,
            preferences="Create exactly one volunteer-training guide.",
        )
        assert "6 weeks" in seen[0][0]
        assert "evidence-to-outcome map" in seen[0][1]
        assert "Community outreach coordinator" in seen[0][0]
        assert "exactly one volunteer-training guide" in seen[0][0]
        assert "Portfolio Projects" not in seen[0][1]
        assert "2-3 NEW projects" not in seen[0][1]
