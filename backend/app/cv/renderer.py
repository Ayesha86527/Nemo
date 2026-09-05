"""Layout-consistent CV rendering engine.

Builds a .docx from structured content using a fixed style system: one font,
fixed sizes, fixed paragraph spacing. All spacing constants live here so the
output is deterministic — no inherited Word defaults, no drifting font sizes,
no extra blank paragraphs between sections.
"""

import io

from docx import Document
from docx.enum.text import WD_TAB_ALIGNMENT
from docx.shared import Cm, Pt, RGBColor

from app.cv.content import CVContentData

# --- Style system (single source of truth for layout) ---
FONT = "Calibri"
INK = RGBColor(0x1A, 0x1A, 0x1A)
DIM = RGBColor(0x55, 0x5B, 0x66)

NAME_SIZE = Pt(20)
CONTACT_SIZE = Pt(10)
HEADING_SIZE = Pt(11)
ROLE_SIZE = Pt(11)
BODY_SIZE = Pt(10.5)

MARGIN = Cm(1.9)
CONTENT_WIDTH = Cm(17.2)  # A4 width minus margins — right tab stop for dates

SPACE_BODY = Pt(2)
SPACE_BEFORE_SECTION = Pt(10)
SPACE_AFTER_SECTION_TITLE = Pt(2)
SPACE_AFTER_NAME = Pt(1)
SPACE_AFTER_CONTACT = Pt(6)


def _run(paragraph, text: str, *, size: Pt = BODY_SIZE, bold: bool = False, color=INK):
    run = paragraph.add_run(text)
    run.font.name = FONT
    run.font.size = size
    run.font.bold = bold
    run.font.color.rgb = color
    return run


def _paragraph(doc: Document, space_before=Pt(0), space_after=SPACE_BODY):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = space_before
    p.paragraph_format.space_after = space_after
    p.paragraph_format.line_spacing = 1.0
    return p


def _section_heading(doc: Document, title: str):
    p = _paragraph(doc, space_before=SPACE_BEFORE_SECTION, space_after=SPACE_AFTER_SECTION_TITLE)
    _run(p, title.upper(), size=HEADING_SIZE, bold=True)
    return p


def _dates(item) -> str:
    if item.start and item.end:
        return f"{item.start} – {item.end}"
    return item.start or item.end or ""


def render_cv_docx(content: CVContentData, name: str = "", email: str = "", education: str = "") -> bytes:
    """Render the CV. Sections with no content are omitted entirely."""
    doc = Document()
    for section in doc.sections:
        section.top_margin = section.bottom_margin = MARGIN
        section.left_margin = section.right_margin = MARGIN

    # Normal style is pinned so nothing inherits Word's defaults.
    normal = doc.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = BODY_SIZE

    if name:
        p = _paragraph(doc, space_after=SPACE_AFTER_NAME)
        _run(p, name, size=NAME_SIZE, bold=True)
    contact = " · ".join(part for part in (email,) if part)
    if contact:
        p = _paragraph(doc, space_after=SPACE_AFTER_CONTACT)
        _run(p, contact, size=CONTACT_SIZE, color=DIM)

    if content.experience:
        _section_heading(doc, "Experience")
        for item in content.experience:
            p = _paragraph(doc)
            p.paragraph_format.tab_stops.add_tab_stop(CONTENT_WIDTH, WD_TAB_ALIGNMENT.RIGHT)
            title = item.role or "Role"
            if item.company:
                title += f" · {item.company}"
            _run(p, title, size=ROLE_SIZE, bold=True)
            dates = _dates(item)
            if dates:
                _run(p, "\t" + dates, size=BODY_SIZE, color=DIM)
            if item.description:
                d = _paragraph(doc)
                _run(d, item.description, size=BODY_SIZE)

    if content.skills:
        _section_heading(doc, "Skills")
        p = _paragraph(doc)
        _run(p, ", ".join(content.skills), size=BODY_SIZE)

    if content.projects:
        _section_heading(doc, "Projects")
        for project in content.projects:
            p = _paragraph(doc)
            title = project.name or "Project"
            if project.tech:
                title += f" · {project.tech}"
            _run(p, title, size=ROLE_SIZE, bold=True)
            if project.description:
                d = _paragraph(doc)
                _run(d, project.description, size=BODY_SIZE)

    if content.achievements:
        _section_heading(doc, "Achievements")
        for achievement in content.achievements:
            p = _paragraph(doc, space_after=Pt(1))
            _run(p, f"•  {achievement}", size=BODY_SIZE)

    if education:
        _section_heading(doc, "Education")
        p = _paragraph(doc)
        _run(p, education, size=BODY_SIZE)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
