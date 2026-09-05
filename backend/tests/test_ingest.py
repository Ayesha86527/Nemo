"""Tests for first-upload ingestion: parse_ingest_response, ingest_cv_text,
and the upload endpoint wiring (POST /api/cv/file)."""

import io
import json

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.routers.cv as cv_router
from app.cv.content import CVContentData, load_content, save_content
from app.cv.ingest import ingest_cv_text, parse_ingest_response
from app.cv.storage import CVFileStore
from app.llm.providers import LLMResponse

VALID_JSON = json.dumps(
    {
        "skills": ["Python", "FastAPI"],
        "experience": [
            {"role": "AI Engineer", "company": "Acme", "start": "Jan 2024", "end": "Present", "description": "Built RAG systems."}
        ],
        "projects": [{"name": "Nemo", "tech": "Electron, FastAPI", "description": "Career co-pilot."}],
        "achievements": ["Cut latency 40%"],
    }
)


def _docx_bytes(text: str) -> bytes:
    doc = Document()
    doc.add_paragraph(text)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


class TestParseIngestResponse:
    def test_valid_json(self):
        content = parse_ingest_response(VALID_JSON)
        assert content is not None
        assert content.skills == ["Python", "FastAPI"]
        assert content.experience[0].role == "AI Engineer"
        assert content.projects[0].name == "Nemo"
        assert content.achievements == ["Cut latency 40%"]

    def test_fenced_json(self):
        content = parse_ingest_response(f"```json\n{VALID_JSON}\n```")
        assert content is not None
        assert content.skills == ["Python", "FastAPI"]

    def test_malformed_json_returns_none(self):
        assert parse_ingest_response("{not valid json") is None
        assert parse_ingest_response("no json at all") is None

    def test_all_empty_lists_returns_none(self):
        assert parse_ingest_response('{"skills": [], "experience": [], "projects": [], "achievements": []}') is None

    def test_filters_blank_entries(self):
        content = parse_ingest_response('{"skills": ["Python", "  ", ""], "achievements": ["", "Award"]}')
        assert content is not None
        assert content.skills == ["Python"]
        assert content.achievements == ["Award"]


class TestIngestCvText:
    async def test_error_response_returns_none(self):
        async def failing(prompt, system):
            return LLMResponse(text="", provider="x", model="y", error="boom")

        assert await ingest_cv_text(failing, "some resume text") is None

    async def test_empty_resume_text_short_circuits(self):
        called = []

        async def generate(prompt, system):
            called.append(prompt)
            return LLMResponse(text=VALID_JSON, provider="x", model="y")

        assert await ingest_cv_text(generate, "   ") is None
        assert called == []

    async def test_valid_response_parsed(self):
        async def generate(prompt, system):
            return LLMResponse(text=VALID_JSON, provider="x", model="y")

        content = await ingest_cv_text(generate, "Ayesha Noman, AI Engineer")
        assert content is not None
        assert content.experience[0].company == "Acme"


@pytest.fixture
def client_and_store(isolated_engine, monkeypatch, tmp_path):
    store = CVFileStore(tmp_path / "cvstore")
    monkeypatch.setattr(cv_router, "get_cv_store", lambda: store)
    monkeypatch.setattr(cv_router, "get_cv_text_cache", lambda: _StubTextCache())
    app = FastAPI()
    app.include_router(cv_router.router)
    return TestClient(app), store


class _StubTextCache:
    def invalidate(self) -> None:
        pass


class TestUploadIngestion:
    def _fake_generate(self, monkeypatch, payload=VALID_JSON, error=None):
        calls = []

        async def fake(prompt, system):
            calls.append(prompt)
            return LLMResponse(text=payload, provider="test", model="test", error=error)

        monkeypatch.setattr(cv_router, "generate", fake)
        return calls

    def test_first_upload_ingests_content(self, isolated_engine, client_and_store, monkeypatch):
        client, _store = client_and_store
        calls = self._fake_generate(monkeypatch)

        resp = client.post(
            "/api/cv/file",
            files={"file": ("resume.docx", _docx_bytes("Ayesha Noman — AI Engineer"), "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] is True
        assert body["ingest_error"] is None
        assert body["has_content"] is True
        assert len(calls) == 1

        with Session(isolated_engine) as session:
            content, _ = load_content(session)
        assert content.skills == ["Python", "FastAPI"]

    def test_reupload_with_existing_content_skips_ingestion(self, isolated_engine, client_and_store, monkeypatch):
        client, _store = client_and_store
        calls = self._fake_generate(monkeypatch)
        with Session(isolated_engine) as session:
            save_content(session, CVContentData(skills=["Existing"]), "2026-08-29T00:00:00")

        resp = client.post(
            "/api/cv/file",
            files={"file": ("resume.docx", _docx_bytes("text"), "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] is False
        assert body["ingest_error"] is None
        assert calls == []

        with Session(isolated_engine) as session:
            content, _ = load_content(session)
        assert content.skills == ["Existing"]

    def test_ingestion_failure_still_saves_file(self, isolated_engine, client_and_store, monkeypatch):
        client, store = client_and_store
        self._fake_generate(monkeypatch, error="provider down")

        resp = client.post(
            "/api/cv/file",
            files={"file": ("resume.docx", _docx_bytes("text"), "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] is False
        assert body["has_file"] is True
        assert store.exists()

    def test_ingestion_exception_reports_error(self, isolated_engine, client_and_store, monkeypatch):
        client, store = client_and_store

        async def exploding(prompt, system):
            raise RuntimeError("network down")

        monkeypatch.setattr(cv_router, "generate", exploding)

        resp = client.post(
            "/api/cv/file",
            files={"file": ("resume.docx", _docx_bytes("text"), "application/octet-stream")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ingested"] is False
        assert "network down" in body["ingest_error"]
        assert store.exists()
