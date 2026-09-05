"""Market intelligence engine.

Weekly-cadence research loop: compares the user's CV content (passed in as
plain text) against market context fetched through a pluggable MarketSource,
and asks the LLM for a STRUCTURED gap analysis (JSON) so the frontend can
render charts instead of dumping raw text. Storage is the caller's concern.
"""

import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.llm.providers import LLMResponse

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

WEEKLY_INTERVAL_DAYS = 7

VALID_GAP_STATUSES = ("have", "partial", "missing")


class MarketIntelError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


def is_due(last_run_iso: str | None, interval_days: int = WEEKLY_INTERVAL_DAYS) -> bool:
    """True if a new research run is due (never run, or older than interval)."""
    if not last_run_iso:
        return True
    try:
        last = datetime.fromisoformat(last_run_iso)
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last >= timedelta(days=interval_days)


class MarketSource(ABC):
    """Pluggable provider of market context — swap in a web-search-backed
    source later without touching the engine (Open/Closed)."""

    @abstractmethod
    async def gather(self, target_role: str) -> str: ...


class LLMInsightSource(MarketSource):
    """Default source: synthesizes market context from the LLM itself.

    Keeps the app dependency-free; a search-backed source can replace it when
    available.
    """

    def __init__(self, generate_fn: GenerateFn):
        self._generate = generate_fn

    async def gather(self, target_role: str) -> str:
        prompt = (
            f"Summarize the current job market for: {target_role}. "
            "List the most in-demand technical skills, tools, and trends. "
            "Be concrete and concise (max 200 words)."
        )
        response = await self._generate(prompt, "You are a tech labor-market analyst.")
        if response.error:
            raise MarketIntelError(response.error, hint=response.hint or "")
        return response.text


@dataclass
class GapReport:
    target_role: str
    report: dict  # structured analysis, see _GAP_SYSTEM_PROMPT schema
    created_at: str
    provider: str = ""


_GAP_SYSTEM_PROMPT = """You are a career strategist producing a PERSONALIZED gap analysis for one specific candidate.
Respond with STRICT JSON only, no markdown, matching exactly:
{"match_score": <integer 0-100, overall fit for the role>,
 "summary": "<2-3 sentence verdict about THIS candidate>",
 "skill_gaps": [{"skill": "<skill or tool>", "status": "have" | "partial" | "missing", "note": "<short note citing the candidate's evidence>"}],
 "market_signals": ["<in-demand skill or trend>", ...],
 "recommendations": ["<concrete next step for THIS candidate>", ...]}
Grounding rules (strict):
- The candidate's profile/CV in the prompt is the ONLY evidence of what they can do. Never assume skills they did not demonstrate.
- "have" requires explicit evidence in the CV; "partial" requires partial/related evidence; "missing" means demanded by the market but absent from the CV (say "no evidence in CV" in the note).
- The summary, match_score, and recommendations must reference the candidate's actual experience, projects, and skills by name. Generic career advice with no reference to the CV is a failure.
- If the CV/profile evidence is empty or nearly empty, set match_score low and say in the summary that the analysis is limited until CV content is added.
Rules: 6-12 skill_gaps based on REAL demanded skills; 3-6 market_signals; 3-5 recommendations."""


def empty_report() -> dict:
    return {
        "match_score": None,
        "summary": "",
        "skill_gaps": [],
        "market_signals": [],
        "recommendations": [],
    }


def parse_report(text: str) -> dict:
    """Parse the LLM's JSON report, degrading gracefully.

    Unparseable output falls back to a report carrying the raw text as the
    summary so the user still sees something instead of an error wall.
    """
    report = empty_report()
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        report["summary"] = text.strip()
        return report
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        report["summary"] = text.strip()
        return report
    if not isinstance(data, dict):
        report["summary"] = text.strip()
        return report

    score = data.get("match_score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        report["match_score"] = max(0, min(100, int(score)))
    report["summary"] = str(data.get("summary") or "")

    for item in data.get("skill_gaps", []) or []:
        if isinstance(item, dict) and item.get("skill"):
            status = str(item.get("status", "missing")).lower()
            report["skill_gaps"].append(
                {
                    "skill": str(item["skill"]),
                    "status": status if status in VALID_GAP_STATUSES else "missing",
                    "note": str(item.get("note") or ""),
                }
            )
    report["market_signals"] = [str(s) for s in data.get("market_signals", []) or [] if s]
    report["recommendations"] = [str(s) for s in data.get("recommendations", []) or [] if s]
    return report


class MarketIntelligenceEngine:
    def __init__(
        self,
        generate_fn: GenerateFn,
        source: MarketSource | None = None,
    ):
        self._generate = generate_fn
        self._source = source or LLMInsightSource(generate_fn)

    async def run_gap_analysis(self, target_role: str, cv_context: str = "") -> GapReport:
        market_context = await self._source.gather(target_role)
        prompt = (
            f"Target role: {target_role}\n\n"
            f"Market context:\n{market_context}\n\n"
            f"Candidate profile and CV content:\n{cv_context.strip() or '(none yet)'}"
        )
        response = await self._generate(prompt, _GAP_SYSTEM_PROMPT)
        if response.error:
            raise MarketIntelError(response.error, hint=response.hint or "")

        return GapReport(
            target_role=target_role,
            report=parse_report(response.text),
            created_at=datetime.now(timezone.utc).isoformat(),
            provider=response.provider,
        )
