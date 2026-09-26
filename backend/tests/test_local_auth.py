"""Tests for the per-launch local API token (prompt-injection / probing defense)."""

from fastapi.testclient import TestClient

from app.local_auth import is_authorized


class TestIsAuthorized:
    def test_disabled_without_env(self, monkeypatch):
        monkeypatch.delenv("NEMO_AUTH_TOKEN", raising=False)
        assert is_authorized("") is True
        assert is_authorized("anything") is True

    def test_correct_token_accepted(self, monkeypatch):
        monkeypatch.setenv("NEMO_AUTH_TOKEN", "s3cret")
        assert is_authorized("s3cret") is True

    def test_wrong_or_missing_token_rejected(self, monkeypatch):
        monkeypatch.setenv("NEMO_AUTH_TOKEN", "s3cret")
        assert is_authorized("") is False
        assert is_authorized("wrong") is False


class TestMiddleware:
    def test_api_routes_require_token_when_enabled(self, monkeypatch):
        monkeypatch.setenv("NEMO_AUTH_TOKEN", "s3cret")
        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health", headers={"x-nemo-token": "wrong"}).status_code == 401
        assert client.get("/api/health", headers={"x-nemo-token": "s3cret"}).status_code == 200

    def test_open_when_disabled(self, monkeypatch):
        monkeypatch.delenv("NEMO_AUTH_TOKEN", raising=False)
        from app.main import app

        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/api/health").status_code == 200
