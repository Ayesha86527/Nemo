"""Tests for the agent-driven CV replacement flow (POST /api/cv/replace)."""

import io

import pytest
from docx import Document
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session

import app.routers.cv as cv_router
from app.cv.content import CVContentData, save_content
from app.cv.storage import CVFileStore


@pytest.fixture
def client_and_store(isolated_engine, monkeypatch, tmp_path):
    store = CVFileStore(tmp_path / "cvstore")
    monkeypatch.setattr(cv_router, "get_cv_store", lambda: store)
    app = FastAPI()
    app.include_router(cv_router.router)
    return TestClient(app), store


def _seed_content(engine):
    with Session(engine) as session:
        save_content(
            session,
            CVContentData(skills=["Python", "FastAPI"], achievements=["Shipped v1"]),
            "2026-08-29T00:00:00",
        )


class TestReplaceEndpoint:
    def test_replace_renders_content_into_stored_file(self, isolated_engine, client_and_store):
        client, store = client_and_store
        _seed_content(isolated_engine)

        resp = client.post("/api/cv/replace")
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_file"] is True
        assert body["filename"] == "cv.docx"

        data = store.load()
        assert data is not None
        doc = Document(io.BytesIO(data))
        text = "\n".join(p.text for p in doc.paragraphs)
        assert "Python" in text
        assert "Shipped v1" in text

    def test_replace_overwrites_existing_upload(self, isolated_engine, client_and_store):
        client, store = client_and_store
        store.save(b"old-bytes", "old_cv.docx")
        _seed_content(isolated_engine)

        resp = client.post("/api/cv/replace")
        assert resp.status_code == 200
        assert store.meta()["filename"] == "cv.docx"
        assert store.load() != b"old-bytes"

    def test_replace_with_nothing_to_render_returns_422(self, client_and_store):
        client, store = client_and_store
        resp = client.post("/api/cv/replace")
        assert resp.status_code == 422
        assert resp.json()["detail"]["error"] == "Nothing to render yet."
        assert not store.exists()

    def test_replaced_file_is_downloadable(self, isolated_engine, client_and_store):
        client, _store = client_and_store
        _seed_content(isolated_engine)
        client.post("/api/cv/replace")

        resp = client.get("/api/cv/file")
        assert resp.status_code == 200
        assert "cv.docx" in resp.headers["content-disposition"]
