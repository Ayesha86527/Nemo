"""Shared fixtures: isolated SQLite engine so tests never touch ~/.nemo/nemo.db."""

import importlib
import pkgutil

import pytest
from sqlmodel import SQLModel, create_engine

# Import models so every table is registered on SQLModel.metadata.
from app.db import models  # noqa: F401


def _all_app_modules():
    """Every module in the app package (routers, services, helpers)."""
    import app

    names = ["app"]
    for info in pkgutil.walk_packages(app.__path__, prefix="app."):
        names.append(info.name)
    return names


@pytest.fixture
def isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    # Patch EVERY app module that binds the module-level engine, not a curated
    # list: a new router importing `engine` must never silently fall through to
    # the real ~/.nemo/nemo.db during tests.
    for name in _all_app_modules():
        if name == "app.db.engine":
            continue
        module = importlib.import_module(name)
        if hasattr(module, "engine"):
            monkeypatch.setattr(module, "engine", engine)

    # Same isolation for the on-disk CV cache/store: tests must never read or
    # write the real ~/.nemo/cv_text.txt.
    import app.cv.extract as cv_extract
    import app.deps as deps

    cache = cv_extract.CVTextCache(directory=tmp_path)
    monkeypatch.setattr(cv_extract, "get_cv_text_cache", lambda: cache)
    monkeypatch.setattr(deps, "get_cv_text", lambda: cache.get_text())
    return engine
