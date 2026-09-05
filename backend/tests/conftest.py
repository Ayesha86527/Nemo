"""Shared fixtures: isolated SQLite engine so tests never touch ~/.nemo/nemo.db."""

import pytest
from sqlmodel import SQLModel, create_engine

# Import models so every table is registered on SQLModel.metadata.
from app.db import models  # noqa: F401

_ENGINE_CONSUMERS = (
    "app.db.engine",
    "app.deps",
    "app.jobs.service",
    "app.agent.service",
    "app.routers.jobs",
    "app.routers.agent",
    "app.routers.roadmap",
    "app.routers.cv",
)


@pytest.fixture
def isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    import importlib

    for name in _ENGINE_CONSUMERS:
        module = importlib.import_module(name)
        if hasattr(module, "engine"):
            monkeypatch.setattr(module, "engine", engine)
    return engine
