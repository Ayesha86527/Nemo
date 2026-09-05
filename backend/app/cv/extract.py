"""Plain-text extraction of the stored CV .docx.

Every LLM feature (market intel, role-match, cover letters, roadmaps) must be
grounded in the user's ACTUAL resume. The uploaded document's text is
extracted once per upload and cached next to the stored file; the cache is
invalidated on upload/delete.
"""

import io
from pathlib import Path

from docx import Document

from app.cv.storage import CVFileStore, DEFAULT_CV_DIR

TEXT_FILENAME = "cv_text.txt"
KEY_FILENAME = "cv_text_key.txt"

# Keep prompts bounded even for very long documents.
MAX_CHARS = 12000


def extract_docx_text(data: bytes) -> str:
    """Full document text: body paragraphs plus every table cell, in order."""
    doc = Document(io.BytesIO(data))
    lines: list[str] = []

    def push(text: str) -> None:
        text = text.strip()
        if text and (not lines or lines[-1] != text):
            lines.append(text)

    for paragraph in doc.paragraphs:
        push(paragraph.text)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                push(cell.text)
    return "\n".join(lines)


class CVTextCache:
    def __init__(self, directory: Path = DEFAULT_CV_DIR, store: CVFileStore | None = None):
        self._dir = Path(directory)
        self._store = store or CVFileStore(directory)
        self._text_file = self._dir / TEXT_FILENAME
        self._key_file = self._dir / KEY_FILENAME

    def _cache_key(self) -> str:
        meta = self._store.meta()
        return f"{meta.get('uploaded_at', '')}:{meta.get('size', 0)}"

    def get_text(self) -> str:
        """Extracted text of the stored CV, or '' when no file is uploaded."""
        data = self._store.load()
        if data is None:
            return ""
        key = self._cache_key()
        if self._key_file.exists():
            try:
                if self._key_file.read_text() == key:
                    return self._text_file.read_text()
            except OSError:
                pass
        text = extract_docx_text(data)[:MAX_CHARS]
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            self._text_file.write_text(text)
            self._key_file.write_text(key)
        except OSError:
            pass  # cache write failure is harmless — extraction still returns
        return text

    def invalidate(self) -> None:
        for path in (self._text_file, self._key_file):
            if path.exists():
                path.unlink()


_default_cache: CVTextCache | None = None


def get_cv_text_cache() -> CVTextCache:
    global _default_cache
    if _default_cache is None:
        _default_cache = CVTextCache()
    return _default_cache


def get_cv_text() -> str:
    return get_cv_text_cache().get_text()
