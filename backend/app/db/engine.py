import os
from sqlmodel import SQLModel, create_engine

from app.db.migrations import ensure_schema, migrate_legacy_providers

DATA_DIR = os.path.join(os.path.expanduser("~"), ".nemo")
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'nemo.db')}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def init_db() -> None:
    """Create tables, upgrade pre-existing databases in place, then enforce the
    one-time clean-slate policy for data-version changes."""
    SQLModel.metadata.create_all(engine)
    ensure_schema(DATABASE_URL)
    migrate_legacy_providers(DATABASE_URL)
    # Imported late to avoid a circular import (reset -> db.engine).
    from app.reset import reset_if_version_changed

    reset_if_version_changed()
