"""Tests for the settings schema migration and legacy-provider consolidation."""

import sqlite3

from app.db.migrations import LEGACY_SETTINGS_COLUMNS, ensure_schema, migrate_legacy_providers


def _make_legacy_db(path, llm_provider="cloud", cloud_provider="groq", extra=None) -> None:
    conn = sqlite3.connect(path)
    cols = ", ".join(f"{name} TEXT DEFAULT ''" for name in LEGACY_SETTINGS_COLUMNS)
    conn.execute(
        f"CREATE TABLE settings (id INTEGER PRIMARY KEY, {cols}, "
        "gemini_api_key TEXT DEFAULT '', groq_api_key TEXT DEFAULT '', openrouter_api_key TEXT DEFAULT '')"
    )
    conn.execute(
        "INSERT INTO settings (llm_provider, cloud_provider, cloud_api_key) VALUES (?, ?, ?)",
        (llm_provider, cloud_provider, extra or ""),
    )
    conn.commit()
    conn.close()


def _columns(path) -> set[str]:
    conn = sqlite3.connect(path)
    names = {row[1] for row in conn.execute("PRAGMA table_info(settings)")}
    conn.close()
    return names


def _get(path, column):
    conn = sqlite3.connect(path)
    value = conn.execute(f"SELECT {column} FROM settings LIMIT 1").fetchone()[0]
    conn.close()
    return value


def _set(path, column, value) -> None:
    conn = sqlite3.connect(path)
    conn.execute(f"UPDATE settings SET {column} = ?", (value,))
    conn.commit()
    conn.close()


class TestSchema:
    def test_missing_columns_are_added(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db)
        ensure_schema(f"sqlite:///{db}")
        cols = _columns(db)
        for expected in ("custom_api_key", "custom_base_url", "cloud_model"):
            assert expected in cols

    def test_migration_is_idempotent(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db)
        ensure_schema(f"sqlite:///{db}")
        ensure_schema(f"sqlite:///{db}")
        assert "custom_base_url" in _columns(db)


class TestLegacyProviderConsolidation:
    def test_legacy_groq_becomes_its_openai_endpoint(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db, extra="fallback-key")
        ensure_schema(f"sqlite:///{db}")
        _set(db, "groq_api_key", "secret-key")
        migrate_legacy_providers(f"sqlite:///{db}")
        assert _get(db, "custom_base_url") == "https://api.groq.com/openai/v1"
        assert _get(db, "custom_api_key") == "secret-key"

    def test_per_provider_key_takes_precedence(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db, cloud_provider="openrouter", extra="legacy-key")
        ensure_schema(f"sqlite:///{db}")
        _set(db, "openrouter_api_key", "specific-key")
        migrate_legacy_providers(f"sqlite:///{db}")
        assert _get(db, "custom_base_url") == "https://openrouter.ai/api/v1"
        assert _get(db, "custom_api_key") == "specific-key"

    def test_local_ollama_maps_to_its_openai_api(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db, llm_provider="local", cloud_provider="")
        ensure_schema(f"sqlite:///{db}")
        _set(db, "ollama_base_url", "http://localhost:11434")
        _set(db, "ollama_model", "llama3")
        migrate_legacy_providers(f"sqlite:///{db}")
        assert _get(db, "custom_base_url") == "http://localhost:11434/v1"
        assert _get(db, "custom_api_key") == "ollama"
        assert _get(db, "cloud_model") == "llama3"

    def test_existing_endpoint_not_overwritten(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db, extra="secret-key")
        ensure_schema(f"sqlite:///{db}")
        _set(db, "custom_base_url", "https://api.deepseek.com/v1")
        _set(db, "custom_api_key", "mine")
        migrate_legacy_providers(f"sqlite:///{db}")
        assert _get(db, "custom_base_url") == "https://api.deepseek.com/v1"
        assert _get(db, "custom_api_key") == "mine"

    def test_unknown_legacy_provider_left_alone(self, tmp_path):
        db = tmp_path / "legacy.db"
        _make_legacy_db(db, cloud_provider="mystery")
        ensure_schema(f"sqlite:///{db}")
        migrate_legacy_providers(f"sqlite:///{db}")
        assert _get(db, "custom_base_url") == ""
