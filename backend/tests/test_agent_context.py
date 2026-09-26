"""The agent's data context: roadmap + market intel in, settings out."""

import json

from sqlmodel import Session

from app.agent.service import AgentService
from app.db.models import MarketIntelReport, Roadmap, Settings


class _Recorder:
    def __init__(self):
        self.prompts = []

    async def __call__(self, prompt, system=None):
        self.prompts.append(prompt)
        return type("R", (), {"text": json.dumps({"reply": "ok", "actions": []}),
                              "error": None, "hint": None, "provider": "t", "model": "m"})()


class TestDataContext:
    async def test_summary_includes_roadmap_and_market(self, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(Roadmap(
                target_role="Backend Engineer", goal="Land a backend job",
                horizon_weeks=8, milestones_json=json.dumps(
                    [{"title": "APIs", "focus": "fastapi", "steps": [{"task": "build", "done": True}]}]),
                provider="t", created_at="now",
            ))
            session.add(MarketIntelReport(
                created_at="now", target_role="Backend Engineer",
                gap_report=json.dumps({"match_score": 71, "summary": "Decent fit.",
                                       "skill_gaps": [{"skill": "Kubernetes", "status": "missing"}],
                                       "market_signals": ["k8s demand"]}),
                provider="t",
            ))
            session.commit()

        rec = _Recorder()
        await AgentService(generate_fn=rec).chat("what do you know about me?")
        prompt = rec.prompts[0]
        assert "Roadmap for Backend Engineer" in prompt
        assert "Land a backend job" in prompt
        assert "Market intel for Backend Engineer" in prompt
        assert "71%" in prompt and "Kubernetes (missing)" in prompt

    async def test_summary_never_exposes_settings(self, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(Settings(custom_base_url="https://secret.example/v1",
                                 custom_api_key="sk-secret", cloud_model="secret-model"))
            session.commit()

        rec = _Recorder()
        await AgentService(generate_fn=rec).chat("hello")
        prompt = rec.prompts[0]
        assert "secret.example" not in prompt
        assert "sk-secret" not in prompt
        assert "secret-model" not in prompt
        assert "LLM endpoint" not in prompt
