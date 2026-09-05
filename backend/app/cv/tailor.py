"""CV tailoring service.

Builds an edit plan with the LLM (constrained to find/replace pairs), then
applies it through the layout-preserving docx editor. The LLM never touches
the document directly — it only proposes text edits, which keeps output
deterministic and layout-safe. Optional preparation research (role-match +
company research) can be injected as extra context.
"""

import io
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from docx import Document

from app.cv.docx_edit import replace_in_document
from app.cv.parser import extract_text
from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]


class TailoringError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class Edit:
    find: str
    replace: str


@dataclass
class TailorResult:
    output: bytes
    edits_applied: int
    edits_skipped: int
    summary: str
    provider: str
    edits: list[Edit] = field(default_factory=list)


_SYSTEM_PROMPT = """You are a CV tailoring assistant. You receive a candidate's CV text and a job description.
Propose precise text edits to better align the CV with the job, WITHOUT changing facts.
Rules:
- Only rewrite or lightly expand text that already exists in the CV (keywords, phrasing, emphasis).
- Every "find" string must appear VERBATIM in the CV text.
- Keep edits conservative: 3-8 edits, no fabrication of experience.
Respond with STRICT JSON only, no markdown, matching:
{"summary": "<one sentence>", "edits": [{"find": "...", "replace": "..."}]}"""


class CVTailorService:
    def __init__(self, generate_fn: GenerateFn):
        self._generate = generate_fn

    async def tailor(
        self, docx_bytes: bytes, job_description: str, research: str | None = None
    ) -> TailorResult:
        document = self._load(docx_bytes)
        cv_text = extract_text(document)
        plan = await self._plan(cv_text, job_description, research)
        applied, skipped = self._apply(document, plan.edits)
        if applied == 0:
            raise TailoringError(
                "The proposed edits did not match the document.",
                hint="Try again — the model may have drifted from the CV text.",
            )
        buf = io.BytesIO()
        document.save(buf)
        return TailorResult(
            output=buf.getvalue(),
            edits_applied=applied,
            edits_skipped=skipped,
            summary=plan.summary,
            provider=plan.provider,
            edits=plan.edits,
        )

    def _load(self, docx_bytes: bytes) -> Document:
        try:
            return Document(io.BytesIO(docx_bytes))
        except Exception as exc:
            raise TailoringError(
                f"Could not read the .docx file: {exc}",
                hint="Upload a valid .docx CV (PDF is not supported).",
            ) from exc

    async def _plan(self, cv_text: str, job_description: str, research: str | None) -> "_Plan":
        user_prompt = f"CV TEXT:\n{cv_text}\n\nJOB DESCRIPTION:\n{job_description}"
        if research and research.strip():
            user_prompt += (
                "\n\nPREPARATION RESEARCH (role-match + company research):\n" + research.strip()
            )
        response = await self._generate(user_prompt, _SYSTEM_PROMPT)
        if response.error:
            raise TailoringError(response.error, hint=response.hint or "")
        return self._parse_plan(response.text, response.provider)

    @staticmethod
    def _parse_plan(text: str, provider: str) -> "_Plan":
        cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise TailoringError(
                "The model did not return a valid edit plan.",
                hint="Try again, or switch LLM provider in Settings.",
            )
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise TailoringError(
                f"Malformed edit plan from model: {exc}",
                hint="Try again, or switch LLM provider in Settings.",
            ) from exc
        edits = [
            Edit(find=str(e["find"]), replace=str(e["replace"]))
            for e in data.get("edits", [])
            if isinstance(e, dict) and e.get("find")
        ]
        if not edits:
            raise TailoringError(
                "The model proposed no edits.",
                hint="The CV may already match well, or the model drifted — try again.",
            )
        return _Plan(edits=edits, summary=str(data.get("summary", "")), provider=provider)

    @staticmethod
    def _apply(document: Document, edits: list[Edit]) -> tuple[int, int]:
        applied = skipped = 0
        for edit in edits:
            if edit.find == edit.replace:
                skipped += 1
                continue
            hits = replace_in_document(document, edit.find, edit.replace, ignore_case=False)
            if hits == 0:
                # Retry tolerant of case differences before giving up
                hits = replace_in_document(document, edit.find, edit.replace, ignore_case=True)
            if hits > 0:
                applied += 1
            else:
                skipped += 1
        return applied, skipped


@dataclass
class _Plan:
    edits: list[Edit]
    summary: str
    provider: str
