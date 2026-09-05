"""Persistent storage for the user's original CV file.

The uploaded .docx lives in ~/.nemo so it survives app restarts and the user
never has to re-upload. A JSON sidecar records the original filename and
upload timestamp. The store is injectable for tests.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CV_DIR = Path.home() / ".nemo"
CV_FILENAME = "cv_original.docx"
META_FILENAME = "cv_meta.json"


class CVFileStore:
    def __init__(self, directory: Path = DEFAULT_CV_DIR):
        self._dir = Path(directory)
        self._file = self._dir / CV_FILENAME
        self._meta = self._dir / META_FILENAME

    def save(self, data: bytes, filename: str) -> dict:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file.write_bytes(data)
        meta = {
            "filename": filename,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "size": len(data),
        }
        self._meta.write_text(json.dumps(meta))
        return meta

    def load(self) -> bytes | None:
        if not self._file.exists():
            return None
        return self._file.read_bytes()

    def meta(self) -> dict:
        if not self._meta.exists():
            return {}
        try:
            return json.loads(self._meta.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def exists(self) -> bool:
        return self._file.exists()

    def delete(self) -> None:
        for path in (self._file, self._meta):
            if path.exists():
                path.unlink()


_default_store: CVFileStore | None = None


def get_cv_store() -> CVFileStore:
    global _default_store
    if _default_store is None:
        _default_store = CVFileStore()
    return _default_store
