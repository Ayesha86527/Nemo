"""Lightweight schema migration for the local SQLite database.

SQLModel's create_all only creates missing tables — it never adds columns to
existing ones. Users upgrading from earlier builds need new Settings columns
added in place, without losing data.
"""

import sqlite3

LEGACY_SETTINGS_COLUMNS = [
    "llm_provider",
    "cloud_provider",
    "cloud_api_key",
    "ollama_base_url",
    "ollama_model",
]

NEW_SETTINGS_COLUMNS = {
    "custom_api_key": "''",
    "custom_base_url": "''",
    "cloud_model": "''",
}

# Legacy provider id -> OpenAI-compatible base URL it maps to
_LEGACY_ENDPOINTS = {
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

# table -> {column: SQL default} for columns added after first release
EXTRA_COLUMNS = {
    "settings": NEW_SETTINGS_COLUMNS,
    "cvcontent": {"projects_json": "''"},
    "roadmap": {"focus": "''", "preferences": "''"},
}


def _path_from_url(database_url: str) -> str:
    return database_url.removeprefix("sqlite:///")


def ensure_schema(database_url: str) -> None:
    """Add any missing columns to existing tables (create_all only adds tables)."""
    conn = sqlite3.connect(_path_from_url(database_url))
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in EXTRA_COLUMNS.items():
            if table not in tables:
                continue
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, default in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT DEFAULT {default}")
        conn.commit()
    finally:
        conn.close()


def _legacy_columns(conn) -> set[str]:
    return {row[1] for row in conn.execute("PRAGMA table_info(settings)")}


def _get(conn, column: str) -> str:
    row = conn.execute(f"SELECT {column} FROM settings LIMIT 1").fetchone()
    return (row[0] if row else "") or ""


def migrate_legacy_providers(database_url: str) -> None:
    """Fold the old per-provider settings into the single endpoint, once.

    Runs only while custom_base_url is empty: the legacy route/provider choice
    becomes a base URL, and its API key becomes the endpoint key. Local Ollama
    maps to Ollama's own OpenAI-compatible /v1 API.
    """
    conn = sqlite3.connect(_path_from_url(database_url))
    try:
        cols = _legacy_columns(conn)
        if not {"cloud_provider", "custom_base_url"} <= cols:
            return
        if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
            return
        if _get(conn, "custom_base_url"):
            return

        if _get(conn, "llm_provider") == "local":
            base = (_get(conn, "ollama_base_url") or "http://localhost:11434").rstrip("/")
            model = _get(conn, "ollama_model")
            conn.execute(
                "UPDATE settings SET custom_base_url = ?, custom_api_key = ?",
                (f"{base}/v1", "ollama"),
            )
            if model and not _get(conn, "cloud_model"):
                conn.execute("UPDATE settings SET cloud_model = ?", (model,))
            conn.commit()
            return

        provider = _get(conn, "cloud_provider")
        base_url = _LEGACY_ENDPOINTS.get(provider)
        if not base_url:
            return
        api_key = ""
        if f"{provider}_api_key" in cols:
            api_key = _get(conn, f"{provider}_api_key")
        if not api_key and "cloud_api_key" in cols:
            api_key = _get(conn, "cloud_api_key")
        conn.execute(
            "UPDATE settings SET custom_base_url = ?, custom_api_key = ?",
            (base_url, api_key),
        )
        conn.commit()
    finally:
        conn.close()
