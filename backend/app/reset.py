"""Clean-slate data reset.

`reset_all_data` wipes every data table and the stored CV, leaving the app as
if freshly installed. It runs automatically ONCE when the app's data-layout
version changes (so an upgrade starts from a clean-slate testing environment),
and is also exposed manually via POST /api/system/reset.

The LLM Settings row is preserved by default so a reset doesn't silently
break the user's endpoint configuration; `include_settings=True` wipes it too.
"""

from sqlmodel import SQLModel, Session, delete, select

from app.db.engine import engine
from app.db.models import AppState
from app.cv.storage import get_cv_store
from app.cv.extract import get_cv_text_cache

# Bump when the data layout/behavior changes and a clean slate is wanted.
DATA_VERSION = "4"

VERSION_KEY = "data_version"

# Tables wiped on reset (everything user-generated). AppState and Settings are
# handled separately: AppState holds the version marker itself; Settings holds
# the LLM endpoint/key and is only wiped when explicitly requested.
_PRESERVED_TABLES = {"appstate", "settings"}


def _data_tables() -> list:
    tables = []
    for table in SQLModel.metadata.sorted_tables:
        if table.name.lower() not in _PRESERVED_TABLES:
            tables.append(table)
    return tables


def reset_all_data(include_settings: bool = False) -> dict:
    """Wipe user data (profile, CV content, jobs, letters, roadmaps, history)."""
    wiped = []
    with Session(engine) as session:
        for table in _data_tables():
            session.exec(delete(table))
            wiped.append(table.name)
        if include_settings:
            # settings is preserved by default; wipe only on explicit request
            settings_table = SQLModel.metadata.tables.get("settings")
            if settings_table is not None:
                session.exec(delete(settings_table))
                wiped.append("settings")
        session.commit()
    store = get_cv_store()
    store.delete()
    get_cv_text_cache().invalidate()
    return {"wiped": wiped}


def reset_if_version_changed() -> bool:
    """One-time clean slate: wipe all data when the data version changes.

    Returns True if a reset happened. After the wipe the marker is (re)written,
    so subsequent launches keep whatever the user creates from that point on.
    """
    with Session(engine) as session:
        row = session.get(AppState, VERSION_KEY)
        current = row.value if row else ""
    if current == DATA_VERSION:
        return False
    reset_all_data(include_settings=False)
    with Session(engine) as session:
        row = session.get(AppState, VERSION_KEY)
        if row is None:
            row = AppState(key=VERSION_KEY)
            session.add(row)
        row.value = DATA_VERSION
        session.add(row)
        session.commit()
    return True
