"""Tests for the Nemo agent service: action application, settings, persistence."""

import json
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.agent.service import AgentError, AgentService, parse_agent_response
from app.db.models import Roadmap, Settings, UserProfile
from app.llm.providers import LLMResponse
from app.roadmap.service import RoadmapPlan

CV_ACTION = {
    "reply": "Done!",
    "actions": [
        {
            "type": "cv_update",
            "skills_add": ["FastAPI", "Python"],
            "skills_remove": ["Java"],
            "experience_add": [
                {"role": "Backend Engineer", "company": "Acme", "start": "2023", "end": "now", "description": "APIs"}
            ],
            "projects_add": [{"name": "Nemo", "tech": "Python", "description": "Career copilot"}],
            "achievements_add": ["Shipped v1"],
        }
    ],
}


class FakeRoadmapService:
    def __init__(self):
        self.calls = []

    async def generate(self, target_role, cv_context, market_report_json="", horizon_weeks=None,
                       previous_plan_json="", previous_horizon_weeks=None, focus="", preferences=""):
        self.calls.append((target_role, market_report_json, horizon_weeks))
        self.last_focus = focus
        self.last_preferences = preferences
        plan = RoadmapPlan(
            goal="Get hired",
            horizon_weeks=8,
            milestones=[{"title": "M1", "focus": "f", "steps": [{"task": "t", "done": False}]}],
        )
        return plan, "stub"


def _agent(reply_payload: dict | str, error: str | None = None, roadmap_service=None):
    async def generate(prompt: str, system: str | None = None):
        if error:
            return LLMResponse(text="", provider="stub", model="m", error=error, hint="check keys")
        text = reply_payload if isinstance(reply_payload, str) else json.dumps(reply_payload)
        return LLMResponse(text=text, provider="stub", model="m")

    return AgentService(generate_fn=generate, roadmap_service=roadmap_service)


class TestParseAgentResponse:
    def test_parses_actions(self):
        data = parse_agent_response(json.dumps(CV_ACTION))
        assert data["reply"] == "Done!"
        assert data["actions"][0]["type"] == "cv_update"

    def test_malformed_degrades_to_plain_reply(self):
        data = parse_agent_response("Just chatting, no JSON here.")
        assert data["reply"] == "Just chatting, no JSON here."
        assert data["actions"] == []

    def test_strips_fences(self):
        data = parse_agent_response('```json\n{"reply": "Hi", "actions": []}\n```')
        assert data["reply"] == "Hi"

    def test_recovers_actions_from_prose_plus_separate_object(self):
        raw = (
            "I've reworked your roadmap.\n\n"
            '{"actions": [{"type": "roadmap_request", "focus": "e-commerce", '
            '"preferences": "exactly 2 projects"}]}'
        )
        data = parse_agent_response(raw)
        assert data["actions"][0]["type"] == "roadmap_request"
        assert data["actions"][0]["preferences"] == "exactly 2 projects"
        assert "{" not in data["reply"]
        assert data["reply"].startswith("I've reworked")

    def test_recovers_actions_embedded_in_reply_string(self):
        raw = '{"reply": "All set.\\n\\n{\\"actions\\": [{\\"type\\": \\"roadmap_request\\", \\"focus\\": \\"fintech\\"}]}", "actions": []}'
        data = parse_agent_response(raw)
        assert data["actions"][0]["focus"] == "fintech"
        assert "{" not in data["reply"]


class TestCvActions:
    async def test_cv_update_applies_and_dedupes(self, isolated_engine):
        turn = await _agent(CV_ACTION).chat("Add my skills")
        assert "Added skill: FastAPI" in turn.changes[0]

        # Second identical run must not duplicate skills.
        await _agent(CV_ACTION).chat("Add them again")
        with Session(isolated_engine) as session:
            from app.cv.content import load_content

            content, _ = load_content(session)
        assert content.skills.count("FastAPI") == 1
        assert content.skills.count("Python") == 1
        assert len(content.experience) == 2  # experience entries are not deduped
        assert len(content.projects) == 2

    async def test_skills_remove(self, isolated_engine):
        await _agent(CV_ACTION).chat("setup")
        turn = await _agent({"reply": "ok", "actions": [{"type": "cv_update", "skills_remove": ["fastapi"]}]}).chat(
            "remove fastapi"
        )
        assert "Removed skill: fastapi" in turn.changes[0]

    async def test_experience_without_role_is_skipped(self, isolated_engine):
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "cv_update", "experience_add": [{"company": "NoRole"}]}]}
        ).chat("bad experience")
        assert turn.changes == []

    async def test_cv_update_sets_cv_updated_flag(self, isolated_engine):
        turn = await _agent(CV_ACTION).chat("add my skills")
        assert turn.cv_updated is True

    async def test_flag_false_for_non_cv_actions(self, isolated_engine):
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "profile_update", "name": "Ayesha"}]}
        ).chat("set my name")
        assert turn.cv_updated is False

    async def test_flag_false_when_cv_update_applies_nothing(self, isolated_engine):
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "cv_update", "experience_add": [{"company": "NoRole"}]}]}
        ).chat("bad experience")
        assert turn.cv_updated is False


class TestSettingsActions:
    async def test_settings_update_refused_out_of_scope(self, isolated_engine):
        turn = await _agent(
            {
                "reply": "ok",
                "actions": [
                    {
                        "type": "settings_update",
                        "custom_base_url": "https://api.groq.com/openai/v1",
                        "custom_api_key": "sk-x",
                        "cloud_model": "llama-3.3-70b",
                    }
                ],
            }
        ).chat("switch to groq")
        assert "can't change app settings" in turn.changes[0]
        # Nothing may be persisted — settings are outside the agent's scope.
        with Session(isolated_engine) as session:
            settings = session.exec(select(Settings)).first()
        assert settings is None or (
            settings.custom_base_url == ""
            and settings.custom_api_key == ""
            and settings.cloud_model == ""
        )

    async def test_settings_update_never_crashes_on_unknown_fields(self, isolated_engine):
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "settings_update", "cloud_provider": "skynet"}]}
        ).chat("bad settings")
        assert "can't change app settings" in turn.changes[0]


class TestProfileActions:
    async def test_profile_update(self, isolated_engine):
        await _agent(
            {"reply": "ok", "actions": [{"type": "profile_update", "name": "Ayesha", "target_roles": "Backend Engineer"}]}
        ).chat("set my name")
        with Session(isolated_engine) as session:
            profile = session.exec(select(UserProfile)).first()
        assert profile.name == "Ayesha"
        assert profile.target_roles == "Backend Engineer"


class TestRoadmapActions:
    async def test_roadmap_request_creates_row(self, isolated_engine):
        fake = FakeRoadmapService()
        turn = await _agent(
            {"reply": "Planning...", "actions": [{"type": "roadmap_request", "target_role": "Backend Engineer"}]},
            roadmap_service=fake,
        ).chat("make me a roadmap")
        assert "roadmap" in turn.changes[0].lower()
        assert fake.calls[0][0] == "Backend Engineer"
        with Session(isolated_engine) as session:
            rows = session.exec(select(Roadmap)).all()
        assert len(rows) == 1
        assert rows[0].horizon_weeks == 8

    async def test_roadmap_without_role_asks_user(self, isolated_engine):
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "roadmap_request"}]}, roadmap_service=FakeRoadmapService()
        ).chat("roadmap please")
        assert "target role" in turn.changes[0].lower()

    async def test_roadmap_horizon_passed_through(self, isolated_engine):
        fake = FakeRoadmapService()
        await _agent(
            {"reply": "ok", "actions": [{"type": "roadmap_request", "target_role": "Dev", "horizon_weeks": 6}]},
            roadmap_service=fake,
        ).chat("give me a 6 week plan")
        assert fake.calls[0][2] == 6

    async def test_roadmap_out_of_range_horizon_ignored(self, isolated_engine):
        fake = FakeRoadmapService()
        await _agent(
            {"reply": "ok", "actions": [{"type": "roadmap_request", "target_role": "Dev", "horizon_weeks": 999}]},
            roadmap_service=fake,
        ).chat("huge plan")
        assert fake.calls[0][2] is None


class TestChatLifecycle:
    async def test_llm_error_raises_agent_error(self, isolated_engine):
        with pytest.raises(AgentError) as exc_info:
            await _agent({}, error="provider down").chat("hello")
        assert exc_info.value.hint

    async def test_history_persisted_both_sides(self, isolated_engine):
        await _agent({"reply": "Hi there", "actions": []}).chat("hello")
        from app.db.models import AgentMessage

        with Session(isolated_engine) as session:
            rows = session.exec(select(AgentMessage).order_by(AgentMessage.id)).all()
        assert [r.role for r in rows] == ["user", "agent"]
        assert rows[0].content == "hello"
        assert rows[1].content == "Hi there"

    async def test_changes_appended_to_reply(self, isolated_engine):
        turn = await _agent(CV_ACTION).chat("add things")
        assert "- Added skill: FastAPI" in turn.reply

    async def test_history_included_in_prompt(self, isolated_engine):
        seen = []
        await _agent({"reply": "First reply", "actions": []}).chat("first message")

        async def generate(prompt, system=None):
            seen.append(prompt)
            return LLMResponse(text=json.dumps({"reply": "Second", "actions": []}), provider="stub", model="m")

        await AgentService(generate_fn=generate).chat("second message")
        assert "first message" in seen[0]
        assert "First reply" in seen[0]

    async def test_screen_context_injected_into_prompt(self, isolated_engine):
        seen = []

        async def generate(prompt, system=None):
            seen.append(prompt)
            return LLMResponse(text=json.dumps({"reply": "ok", "actions": []}), provider="stub", model="m")

        await AgentService(generate_fn=generate).chat("help me here", context="Job Tracker — 3 applications")
        assert "Current screen: Job Tracker" in seen[0]


class TestJobActions:
    def _patch_job_fns(self, monkeypatch, seen, match_score=77):
        import app.agent.service as agent_service

        FakeJob = type(
            "FakeJob",
            (),
            {
                "id": 7,
                "company": "Nova Labs",
                "role": "ML Engineer",
                "match_score": match_score,
                "match_summary": "Strong overall match.",
                "research": "Nova Labs values scale.",
                "job_description": "Need Python.",
            },
        )

        async def fake_quick_prepare(generate_fn, jd, company="", role=""):
            seen["jd"] = jd
            seen["company"] = company
            return FakeJob()

        async def fake_tailor(generate_fn, job_id, export_dir=None):
            seen["tailor_job"] = job_id
            return Path("downloads/CV_Nova Labs_tailored.docx")

        async def fake_letter(generate_fn, job_id, export_dir=None):
            seen["letter_job"] = job_id
            return None, Path("downloads/CoverLetter_Nova Labs.txt")

        async def fake_dm(generate_fn, **kwargs):
            seen["dm"] = kwargs
            return "Hi — I build scalable pipelines and noticed your posting."

        def fake_save(job_id, dm):
            seen["dm_saved"] = (job_id, dm)
            return None

        monkeypatch.setattr(agent_service, "quick_prepare", fake_quick_prepare)
        monkeypatch.setattr(agent_service, "tailor_to_file", fake_tailor)
        monkeypatch.setattr(agent_service, "cover_letter_to_file", fake_letter)
        monkeypatch.setattr(agent_service, "linkedin_dm_text", fake_dm)
        monkeypatch.setattr(agent_service, "save_linkedin_dm", fake_save)

    async def test_job_action_full_tracks_exports_and_skips_tailor_when_aligned(
        self, isolated_engine, monkeypatch
    ):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen, match_score=77)
        turn = await _agent(
            {
                "reply": "ok",
                "actions": [
                    {"type": "job_action", "mode": "full", "job_description": "Need Python.", "company": "Nova Labs"}
                ],
            }
        ).chat("full workup for this JD")
        note = turn.changes[0]
        assert seen["jd"] == "Need Python."
        assert seen["company"] == "Nova Labs"
        assert seen["letter_job"] == 7
        assert "dm" in seen
        assert "Tracked job #7" in note
        assert "77%" in note
        assert "kept the original" in note  # 77% >= 70: aligned, no tailored CV
        assert "Tailored CV saved" not in note
        assert "Cover letter saved" in note

    async def test_job_action_tailors_when_misaligned(self, isolated_engine, monkeypatch):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen, match_score=55)
        turn = await _agent(
            {
                "reply": "ok",
                "actions": [
                    {"type": "job_action", "mode": "tailor", "job_description": "Need Python.", "company": "Nova Labs"}
                ],
            }
        ).chat("tailor my CV")
        note = turn.changes[0]
        assert seen["tailor_job"] == 7
        assert "misaligned (55% < 70%)" in note
        assert "Tailored CV saved" in note

    async def test_job_action_analyze_gives_verdict_without_exports(
        self, isolated_engine, monkeypatch
    ):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen)
        turn = await _agent(
            {
                "reply": "ok",
                "actions": [
                    {"type": "job_action", "mode": "analyze", "job_description": "Need Python.", "company": "Nova Labs"}
                ],
            }
        ).chat("analyze this JD")
        note = turn.changes[0]
        assert "Fit verdict" in note
        assert "Tailored CV saved" not in note
        assert "Cover letter saved" not in note
        assert "tailor_job" not in seen

    async def test_job_action_cover_letter_only(self, isolated_engine, monkeypatch):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen)
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "job_action", "mode": "cover_letter", "job_description": "JD"}]}
        ).chat("cover letter only")
        assert "tailor_job" not in seen
        assert seen["letter_job"] == 7
        assert "Cover letter saved" in turn.changes[0]
        assert "Tailored CV" not in turn.changes[0]

    async def test_job_action_missing_jd_reports(self, isolated_engine, monkeypatch):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen)
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "job_action", "mode": "tailor"}]}
        ).chat("tailor it")
        assert "job description" in turn.changes[0].lower()
        assert "jd" not in seen

    async def test_job_action_invalid_mode_noted(self, isolated_engine, monkeypatch):
        seen: dict = {}
        self._patch_job_fns(monkeypatch, seen)
        turn = await _agent(
            {"reply": "ok", "actions": [{"type": "job_action", "mode": "apply", "job_description": "JD"}]}
        ).chat("apply for me")
        assert "Ignored invalid job_action mode" in turn.changes[0]
        assert "jd" not in seen
