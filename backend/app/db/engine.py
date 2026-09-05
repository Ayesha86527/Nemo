import os
from sqlmodel import SQLModel, create_engine

from app.db.migrations import ensure_schema, migrate_legacy_providers

DATA_DIR = os.path.join(os.path.expanduser("~"), ".nemo")
os.makedirs(DATA_DIR, exist_ok=True)
DATABASE_URL = f"sqlite:///{os.path.join(DATA_DIR, 'nemo.db')}"

engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def init_db() -> None:
    """Create tables, then upgrade pre-existing databases in place."""
    SQLModel.metadata.create_all(engine)
    ensure_schema(DATABASE_URL)
    migrate_legacy_providers(DATABASE_URL)
