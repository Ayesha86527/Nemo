"""Tests for the job tracker workflow and the mandatory preparation gate."""

import json
from dataclasses import dataclass, field

import pytest
from fastapi.testclient import TestClient

from app.llm.providers import LLMResponse

MATCH_JSON = json.dumps({"match_score": 68, "summary": "Strong Python, gaps in K8s."})
ROADMAP_JSON = json.dumps(
    {
        "goal": "Become a backend engineer",
        "horizon_weeks": 12,
        "milestones": [
            {"title": "Foundations", "focus": "Core skills", "steps": ["Learn FastAPI", "Build an API"]},
            {"title": "Ship", "focus": "Proof", "steps": ["Deploy project"]},
        ],
    }
)


def _stub_generate(requests: list[str]):
    async def generate(prompt: str, system: str | None = None):
        requests.append((system or "", prompt))
        if "recruiter" in (system or ""):
            return LLMResponse(text=MATCH_JSON, provider="stub", model="m")
        if "roadmap" in (system or ""):
            return LLMResponse(text=ROADMAP_JSON, provider="stub", model="m")
        if "cover letter" in (system or ""):
            return LLMResponse(text="Dear Hiring Manager...", provider="stub", model="m")
        return LLMResponse(text="Research brief about the company.", provider="stub", model="m")

    return generate


@pytest.fixture
def client(isolated_engine, monkeypatch):
    from app.main import app
    from app.routers import agent as agent_router
    from app.routers import jobs as jobs_router
    from app.routers import roadmap as roadmap_router

    calls: list = []
    monkeypatch.setattr(jobs_router, "generate", _stub_generate(calls))
    monkeypatch.setattr(roadmap_router, "generate", _stub_generate(calls))
    monkeypatch.setattr(agent_router, "generate", _stub_generate(calls))
    client = TestClient(app)
    client.calls = calls
    return client


def _create_job(client, with_jd=True):
    payload = {"company": "Acme", "role": "Backend Engineer"}
    if with_jd:
        payload["job_description"] = "Need Python and Kubernetes."
    res = client.post("/api/jobs", json=payload)
    assert res.status_code == 200
    return res.json()


class TestJobCrud:
    def test_create_list_update_delete(self, client):
        job = _create_job(client)
        assert job["status"] == "wishlist"
        assert job["prepared"] is False

        res = client.get("/api/jobs")
        assert len(res.json()) == 1

        res = client.put(f"/api/jobs/{job['id']}", json={"status": "applied", "follow_up_at": "2026-09-05"})
        assert res.json()["status"] == "applied"
        assert res.json()["follow_up_at"] == "2026-09-05"

        res = client.put(f"/api/jobs/{job['id']}", json={"status": "ghost"})
        assert res.status_code == 422

        res = client.delete(f"/api/jobs/{job['id']}")
        assert res.status_code == 200
        assert client.get("/api/jobs").json() == []

    def test_create_requires_company_or_role(self, client):
        res = client.post("/api/jobs", json={"company": " ", "role": " "})
        assert res.status_code == 422
        res = client.post("/api/jobs", json={"company": "", "role": "Dev"})
        assert res.status_code == 200


class TestPreparationGate:
    def test_tailor_blocked_before_prepare(self, client):
        job = _create_job(client)
        res = client.post(f"/api/jobs/{job['id']}/tailor")
        assert res.status_code == 409
        body = res.json()["detail"]
        assert "Prepare" in body["hint"]

    def test_cover_letter_blocked_before_prepare(self, client):
        job = _create_job(client)
        res = client.post(f"/api/jobs/{job['id']}/cover-letter")
        assert res.status_code == 409

    def test_prepare_requires_job_description(self, client):
        job = _create_job(client, with_jd=False)
        res = client.post(f"/api/jobs/{job['id']}/prepare")
        assert res.status_code == 422

    def test_prepare_persists_match_and_research(self, client):
        job = _create_job(client)
        res = client.post(f"/api/jobs/{job['id']}/prepare")
        assert res.status_code == 200
        body = res.json()
        assert body["prepared"] is True
        assert body["match_score"] == 68
        assert body["match_summary"].startswith("Strong Python")
        assert "Research brief" in body["research"]

    def test_changing_job_description_invalidates_preparation(self, client):
        job = _create_job(client)
        client.post(f"/api/jobs/{job['id']}/prepare")
        res = client.put(f"/api/jobs/{job['id']}", json={"job_description": "Now we need Go."})
        body = res.json()
        assert body["prepared"] is False
        assert body["match_score"] is None
        # Gate is active again
        assert client.post(f"/api/jobs/{job['id']}/tailor").status_code == 409


class TestGatedTailoring:
    def test_tailor_runs_after_prepare_with_research_context(self, client, monkeypatch):
        @dataclass
        class FakeResult:
            output: bytes = b"docx-out"
            edits_applied: int = 3
            edits_skipped: int = 0
            summary: str = "Tailored."
            provider: str = "stub"
            edits: list = field(default_factory=list)

        seen: dict = {}

        class FakeTailor:
            def __init__(self, generate_fn):
                pass

            async def tailor(self, data, job_description, research=None):
                seen["research"] = research
                seen["jd"] = job_description
                return FakeResult()

        import app.jobs.service as jobs_service

        monkeypatch.setattr(jobs_service, "CVTailorService", FakeTailor)
        monkeypatch.setattr(jobs_service, "resolve_cv_document", lambda: (b"cv-bytes", "stored"))

        job = _create_job(client)
        client.post(f"/api/jobs/{job['id']}/prepare")
        res = client.post(f"/api/jobs/{job['id']}/tailor")
        assert res.status_code == 200
        assert res.content == b"docx-out"
        assert res.headers["X-Edits-Applied"] == "3"
        assert "Acme" in res.headers["Content-Disposition"]
        assert "Match score: 68%" in seen["research"]
        assert "Research brief" in seen["research"]


class TestCoverLetter:
    def test_cover_letter_generated_after_prepare_and_listed(self, client):
        job = _create_job(client)
        client.post(f"/api/jobs/{job['id']}/prepare")
        res = client.post(f"/api/jobs/{job['id']}/cover-letter")
        assert res.status_code == 200
        letter = res.json()
        assert letter["content"] == "Dear Hiring Manager..."
        assert letter["job_id"] == job["id"]

        res = client.get(f"/api/jobs/{job['id']}/cover-letters")
        assert len(res.json()) == 1

    def test_delete_job_cascades_to_cover_letters(self, client):
        job = _create_job(client)
        client.post(f"/api/jobs/{job['id']}/prepare")
        client.post(f"/api/jobs/{job['id']}/cover-letter")
        client.delete(f"/api/jobs/{job['id']}")
        assert client.get("/api/jobs").json() == []


class TestRoadmapEndpoints:
    def test_generate_latest_and_step_toggle(self, client):
        res = client.post("/api/roadmap/generate", json={"target_role": "Backend Engineer"})
        assert res.status_code == 200
        body = res.json()
        assert body["goal"] == "Become a backend engineer"
        assert len(body["milestones"]) == 2
        assert body["progress"] == {"done": 0, "total": 3, "percent": 0}

        res = client.get("/api/roadmap/latest")
        assert res.status_code == 200

        res = client.put("/api/roadmap/step", json={"milestone": 0, "step": 0, "done": True})
        assert res.status_code == 200
        assert res.json()["progress"] == {"done": 1, "total": 3, "percent": 33}

    def test_latest_404_when_no_roadmap(self, client):
        assert client.get("/api/roadmap/latest").status_code == 404

    def test_bad_step_index_rejected(self, client):
        client.post("/api/roadmap/generate", json={"target_role": "Backend Engineer"})
        res = client.put("/api/roadmap/step", json={"milestone": 9, "step": 0, "done": True})
        assert res.status_code == 422

    def test_generate_requires_target_role(self, client):
        res = client.post("/api/roadmap/generate", json={"target_role": ""})
        assert res.status_code == 422

    def test_generate_with_custom_horizon_overrides_model(self, client):
        res = client.post("/api/roadmap/generate", json={"target_role": "Backend Engineer", "horizon_weeks": 6})
        assert res.status_code == 200
        assert res.json()["horizon_weeks"] == 6

    def test_invalid_horizon_rejected(self, client):
        assert (
            client.post("/api/roadmap/generate", json={"target_role": "X", "horizon_weeks": 0}).status_code == 422
        )
        assert (
            client.post("/api/roadmap/generate", json={"target_role": "X", "horizon_weeks": 53}).status_code == 422
        )


def _patch_fake_tailor(monkeypatch):
    @dataclass
    class FakeResult:
        output: bytes = b"docx-out"
        edits_applied: int = 3
        edits_skipped: int = 0
        summary: str = "Tailored."
        provider: str = "stub"
        edits: list = field(default_factory=list)

    class FakeTailor:
        def __init__(self, generate_fn):
            pass

        async def tailor(self, data, job_description, research=None):
            return FakeResult()

    import app.jobs.service as jobs_service

    monkeypatch.setattr(jobs_service, "CVTailorService", FakeTailor)
    monkeypatch.setattr(jobs_service, "resolve_cv_document", lambda: (b"cv-bytes", "stored"))


def _identity_generate(match_json: str):
    async def generate(prompt: str, system: str | None = None):
        if "recruiter" in (system or ""):
            return LLMResponse(text=match_json, provider="stub", model="m")
        if "cover letter" in (system or ""):
            return LLMResponse(text="Dear Hiring Manager...", provider="stub", model="m")
        return LLMResponse(text="Research brief.", provider="stub", model="m")

    return generate


class TestQuickFlows:
    def test_quick_tailor_tracks_and_prepares_job(self, client, monkeypatch):
        _patch_fake_tailor(monkeypatch)
        res = client.post("/api/jobs/quick-tailor", json={"job_description": "Need Python and Kubernetes."})
        assert res.status_code == 200
        assert res.content == b"docx-out"
        assert res.headers["X-Edits-Applied"] == "3"
        jobs = client.get("/api/jobs").json()
        assert len(jobs) == 1
        assert jobs[0]["prepared"] is True
        assert jobs[0]["match_score"] == 68
        assert jobs[0]["company"] == "Unknown company"
        assert jobs[0]["role"] == "Unknown role"

    def test_quick_tailor_backfills_company_and_role_from_jd(self, client, monkeypatch):
        _patch_fake_tailor(monkeypatch)
        from app.routers import jobs as jobs_router

        identity = json.dumps(
            {"match_score": 55, "summary": "ok", "company": "Nova Labs", "role": "ML Engineer"}
        )
        monkeypatch.setattr(jobs_router, "generate", _identity_generate(identity))
        res = client.post("/api/jobs/quick-tailor", json={"job_description": "Need Python."})
        assert res.status_code == 200
        assert "Nova Labs" in res.headers["Content-Disposition"]
        job = client.get("/api/jobs").json()[0]
        assert job["company"] == "Nova Labs"
        assert job["role"] == "ML Engineer"
        assert job["match_score"] == 55

    def test_quick_cover_letter_tracks_job(self, client):
        res = client.post(
            "/api/jobs/quick-cover-letter", json={"job_description": "Need Python.", "company": "Acme"}
        )
        assert res.status_code == 200
        body = res.json()
        assert body["job"]["company"] == "Acme"
        assert body["job"]["prepared"] is True
        assert body["letter"]["content"] == "Dear Hiring Manager..."
        assert body["letter"]["job_id"] == body["job"]["id"]
        assert len(client.get("/api/jobs").json()) == 1

    def test_quick_requires_job_description(self, client):
        assert client.post("/api/jobs/quick-tailor", json={"job_description": "  "}).status_code == 422
        assert client.post("/api/jobs/quick-cover-letter", json={"job_description": ""}).status_code == 422


class TestFileExports:
    async def test_tailor_and_cover_letter_to_file(self, client, tmp_path, monkeypatch):
        _patch_fake_tailor(monkeypatch)
        res = client.post(
            "/api/jobs/quick-cover-letter", json={"job_description": "Need Python.", "company": "Acme/R&D"}
        )
        assert res.status_code == 200
        job_id = res.json()["job"]["id"]

        import app.jobs.service as jobs_service
        from app.routers import jobs as jobs_router

        path = await jobs_service.tailor_to_file(jobs_router.generate, job_id, export_dir=tmp_path)
        assert path.read_bytes() == b"docx-out"
        assert path.name.startswith("CV_")
        assert path.suffix == ".docx"

        letter, cpath = await jobs_service.cover_letter_to_file(jobs_router.generate, job_id, export_dir=tmp_path)
        assert letter.content == "Dear Hiring Manager..."
        assert cpath.read_text(encoding="utf-8") == "Dear Hiring Manager..."
        assert cpath.name.startswith("CoverLetter_")
