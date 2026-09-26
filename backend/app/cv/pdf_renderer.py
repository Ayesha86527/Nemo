"""PDF export of the rendered CV.

The .docx render (app.cv.renderer) is the layout source of truth; this module
converts it to PDF with a real word processor's layout engine — LibreOffice
headless (cross-platform) or Microsoft Word via COM (Windows) — so the PDF is
pixel-faithful to the downloadable Word file. No network, no user interaction.
"""


import subprocess
import sys
import tempfile
from pathlib import Path


def render_cv_pdf(docx_bytes: bytes) -> bytes:
    """PDF bytes for the given rendered .docx."""
    # 1) LibreOffice headless (best fidelity, cross-platform) if installed.
    soffice = _find_soffice()
    if soffice:
        return _convert_with_soffice(soffice, docx_bytes)
    # 2) Word via COM on Windows.
    if sys.platform == "win32" and _word_available():
        return _convert_with_word(docx_bytes)
    raise PDFRenderUnavailable(
        "PDF export needs LibreOffice or Microsoft Word installed to convert the document "
        "with full fidelity.",
        hint="Install LibreOffice (free) — Nemo will find it automatically — or download the Word version instead.",
    )


class PDFRenderUnavailable(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def _find_soffice() -> str | None:
    candidates = [
        Path(r"C:\Program Files\LibreOffice\program\soffice.exe"),
        Path(r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"),
        Path("/usr/bin/soffice"),
        Path("/usr/local/bin/soffice"),
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _word_available() -> bool:
    try:
        import win32com.client  # noqa: F401

        return True
    except ImportError:
        return False


def _convert_with_soffice(soffice: str, docx_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cv.docx"
        src.write_bytes(docx_bytes)
        result = subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(src)],
            capture_output=True,
            timeout=60,
        )
        pdf = Path(tmp) / "cv.pdf"
        if result.returncode == 0 and pdf.exists():
            return pdf.read_bytes()
    raise PDFRenderUnavailable("LibreOffice could not convert the document.")


def _convert_with_word(docx_bytes: bytes) -> bytes:
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "cv.docx"
            src.write_bytes(docx_bytes)
            dst = Path(tmp) / "cv.pdf"
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = False
            try:
                doc = word.Documents.Open(str(src))
                doc.SaveAs(str(dst), FileFormat=17)  # wdFormatPDF
                doc.Close(False)
            finally:
                word.Quit()
            return dst.read_bytes()
    finally:
        pythoncom.CoUninitialize()
