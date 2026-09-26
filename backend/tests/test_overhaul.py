"""Tests for the production-overhaul features: greeting, reset, formats, DM."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.cv.content import CVContentData, save_content
from app.db.models import AgentMessage, AppState, JobApplication, UserProfile
from app.llm.providers import LLMResponse


@pytest.fixture
def api(monkeypatch):
    monkeypatch.delenv("NEMO_AUTH_TOKEN", raising=False)
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


class TestGreeting:
    def test_greeting_asks_for_name_when_unknown(self, api, isolated_engine):
        data = api.get("/api/agent/greeting").json()
        assert "Hi there" in data["greeting"]
        assert data["onboarded"] is False
        assert "name" in data["missing"]

    def test_greeting_progresses_through_onboarding(self, api, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(
                UserProfile(
                    name="Ayesha",
                    education="BSc CS",
                    short_term_goal="SWE job",
                    long_term_goal="Staff eng",
                )
            )
            session.commit()
        data = api.get("/api/agent/greeting").json()
        assert data["onboarded"] is True
        assert data["greeting"].startswith("Hi there!")

    def test_greeting_accepts_experience_from_cv_content(self, api, isolated_engine):
        with Session(isolated_engine) as session:
            save_content(session, CVContentData(skills=["Python"]), "now")
            session.commit()
        data = api.get("/api/agent/greeting").json()
        assert "name" in data["missing"]
        assert "experience" not in data["missing"]


class TestSystemReset:
    def test_reset_wipes_user_data(self, api, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(UserProfile(name="Ayesha"))
            session.add(JobApplication(company="Acme", role="Dev", job_description="d", created_at="t"))
            session.add(AgentMessage(role="user", content="hi", created_at="t"))
            session.commit()

        resp = api.post("/api/system/reset?include_settings=false")
        assert resp.status_code == 200
        assert "userprofile" in resp.json()["wiped"]

        with Session(isolated_engine) as session:
            assert session.exec(select(UserProfile)).first() is None
            assert session.exec(select(JobApplication)).first() is None
            assert session.exec(select(AgentMessage)).first() is None

    def test_version_marker_survives_reset(self, api, isolated_engine):
        with Session(isolated_engine) as session:
            session.add(AppState(key="data_version", value="x"))
            session.commit()
        api.post("/api/system/reset")
        with Session(isolated_engine) as session:
            row = session.get(AppState, "data_version")
            assert row is not None and row.value == "x"


class TestMultiFormatIngest:
    def test_txt_upload_ingests(self, api, isolated_engine, monkeypatch):
        async def fake_ingest(generate_fn, text):
            return CVContentData(skills=["Python", "SQL"])

        monkeypatch.setattr("app.routers.cv.ingest_cv_text", fake_ingest)
        resp = api.post(
            "/api/cv/file",
            files={"file": ("cv.txt", b"Python developer with SQL experience", "text/plain")},
        )
        assert resp.status_code == 200
        assert resp.json()["ingested"] is True

    def test_unsupported_format_rejected(self, api, isolated_engine):
        resp = api.post(
            "/api/cv/file",
            files={"file": ("cv.rtf", b"{\\rtf1}", "application/rtf")},
        )
        assert resp.status_code == 422

    def test_dispatcher_routes_pdf(self, monkeypatch):
        from app.cv import extract as extract_mod

        called = []

        def fake_pdf(data):
            called.append(True)
            return "pdf text"

        monkeypatch.setattr(extract_mod, "extract_pdf_text", fake_pdf)
        assert extract_mod.extract_cv_text(b"%PDF-fake", "cv.pdf") == "pdf text"
        assert called


class TestLinkedInDM:
    def test_dm_endpoint_generates_and_persists(self, api, isolated_engine, monkeypatch):
        with Session(isolated_engine) as session:
            job = JobApplication(
                company="Acme",
                role="Dev",
                job_description="Build APIs",
                match_score=80,
                match_summary="Good fit.",
                research="Acme ships fast.",
                prepared_at="now",
                created_at="t",
            )
            session.add(job)
            session.commit()
            job_id = job.id

        async def fake_generate(prompt, system=None):
            return LLMResponse(text="Hi — saw the Dev role at Acme.", provider="stub", model="m")

        import app.routers.jobs as jobs_router

        monkeypatch.setattr(jobs_router, "generate", fake_generate)

        resp = api.post(f"/api/jobs/{job_id}/linkedin-dm")
        assert resp.status_code == 200
        assert resp.json()["linkedin_dm"].startswith("Hi —")

    def test_dm_requires_prepared_job(self, api, isolated_engine):
        with Session(isolated_engine) as session:
            job = JobApplication(company="Acme", role="Dev", job_description="d", created_at="t")
            session.add(job)
            session.commit()
            job_id = job.id
        resp = api.post(f"/api/jobs/{job_id}/linkedin-dm")
        assert resp.status_code == 409


class TestQuickAnalyze:
    def test_quick_analyze_prepares_job(self, api, isolated_engine, monkeypatch):
        from app.research.pipeline import PreparationPipeline, PrepResult

        async def fake_run(self, company, role, job_description, cv_context):
            return PrepResult(
                company="Nova Robotics",
                role="Controls Engineer",
                match_score=64,
                match_summary="Partial fit.",
                research="Nova Robotics builds arms.",
            )

        monkeypatch.setattr(PreparationPipeline, "run", fake_run)
        resp = api.post("/api/jobs/quick-analyze", json={"job_description": "Controls role at Nova."})
        assert resp.status_code == 200
        data = resp.json()
        assert data["company"] == "Nova Robotics"
        assert data["aligned"] is False  # 64 < 70
        assert "linkedin_dm" in data
