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
            f"Summarize the current employment market relevant to this stated goal or role: {target_role}. "
            "Identify role- and industry-relevant capabilities, practices, credentials, and trends without "
            "assuming a profession or technology stack. Distinguish observed context from inference and be "
            "concrete and concise (max 200 words)."
        )
        response = await self._generate(prompt, "You are a role- and industry-aware labor-market analyst.")
        if response.error:
            raise MarketIntelError(response.error, hint=response.hint or "")
        return response.text


@dataclass
class GapReport:
    target_role: str
    report: dict  # structured analysis, see _GAP_SYSTEM_PROMPT schema
    created_at: str
    provider: str = ""


_GAP_SYSTEM_PROMPT = """You are a career strategist producing a PERSONALIZED, TRANSPARENT gap analysis for one specific candidate and stated goal.
Respond with STRICT JSON only, no markdown, matching exactly:
{"match_score": <integer 0-100, overall fit for the role>,
 "summary": "<2-3 sentence verdict about THIS candidate>",
 "skill_gaps": [{"skill": "<skill or tool>", "status": "have" | "partial" | "missing", "note": "<short note citing the candidate's evidence>"}],
 "market_signals": ["<in-demand skill or trend>", ...],
 "recommendations": ["<concrete next step for THIS candidate>", ...],
 "sources": [{"claim": "<the specific finding above it supports>", "source": "<the verifiable origin: a publication, report, standards body, or well-known documentation; use 'reasoned from candidate context' when the claim derives only from the supplied CV/profile>"}]}
Transparency rules (strict):
- Every market signal and every recommendation that rests on a market claim MUST have a matching entry in "sources" naming where it comes from. A source that cannot be named means the claim must be dropped or downgraded to "reasoned from candidate context".
- Never invent statistics, percentages, salary figures, or named reports. If market context supplies none, say so.
- The candidate profile/CV is the only evidence of what the candidate can do. The stated goal and market context are the only evidence for what may be relevant. Never assume an industry, profession, toolset, preference, or experience that is not supplied.
- "have" requires explicit candidate evidence; "partial" requires partial or related evidence; "missing" means relevant market evidence is absent from the CV (say "no evidence in CV" in the note).
- The summary, match_score, and recommendations must connect the named candidate evidence to the stated goal and market context. Generic advice is a failure.
- If candidate or market evidence is empty or nearly empty, state that the analysis is limited and recommend supplying the missing context rather than fabricating a gap.
- Use role- and industry-appropriate terms only when they come from the supplied goal, profile, or market context.
Rules: at most 8 skill_gaps; skill names ≤ 6 words; notes ≤ 12 words; summary ≤ 45 words; 3-5 market signals (≤ 12 words each); 3-5 recommendations (≤ 20 words each); 2-4 sources. The response MUST end as complete, valid JSON — if you are running long, cut item counts, never truncate the structure."""


def empty_report() -> dict:
    return {
        "match_score": None,
        "summary": "",
        "skill_gaps": [],
        "market_signals": [],
        "recommendations": [],
        "sources": [],
    }


def _close_opens(text: str) -> str:
    """Append the closing brackets/braces a (possibly truncated) JSON prefix needs."""
    stack = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if stack:
                stack.pop()
    if in_string:
        return ""  # unterminated string: caller must cut to a safe point
    return "".join("}" if c == "{" else "]" for c in reversed(stack))


def _safe_cut_indices(text: str) -> list[int]:
    """Positions right after a COMPLETELY finished JSON element.

    A cut is safe when what precedes it is a closed value (}, ], a value
    string, or a comma). Key strings ("skill":) are NOT safe — an object
    cannot close right after a key.
    """
    safe = []
    in_string = False
    escape = False
    prev_significant = ""
    for i, ch in enumerate(text):
        if escape:
            escape = False
            prev_significant = ch
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            if in_string and prev_significant in (":",):
                safe.append(i + 1)  # a VALUE string just closed
            in_string = not in_string
            prev_significant = '"'
            continue
        if in_string:
            continue
        if ch in "}],":
            safe.append(i + 1)
        if not ch.isspace():
            prev_significant = ch
    return safe


def repair_truncated_json(text: str) -> dict | None:
    """Recover a structured object from JSON cut off mid-stream.

    LLMs hitting the output limit leave valid-but-unfinished JSON. We try the
    longest safe prefix: cut after the last complete element, drop dangling
    separators, close the open brackets, and parse. Returns None when nothing
    parses.
    """
    candidates = [text]
    # Longest first — we want to keep as much of the report as possible.
    for idx in sorted(set(_safe_cut_indices(text)), reverse=True)[:40]:
        prefix = text[:idx].rstrip()
        while prefix.endswith((",", ":")):
            prefix = prefix[:-1].rstrip()
        candidates.append(prefix)
    for candidate in candidates:
        closing = _close_opens(candidate)
        if not closing and not candidate.rstrip().endswith("}"):
            continue
        try:
            data = json.loads(candidate + closing)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_report(text: str) -> dict:
    """Parse the LLM's JSON report, degrading gracefully.

    Truncated output is repaired (see repair_truncated_json) so a dashboard
    still renders instead of dumping raw JSON at the user. Only when nothing
    can be recovered do we fall back to a short, friendly summary.
    """
    report = empty_report()
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)

    data = None
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                data = parsed
        except json.JSONDecodeError:
            data = None
    if data is None:
        data = repair_truncated_json(cleaned)
    if data is None:
        report["summary"] = (
            "The analysis could not be structured — the model's response was cut short. "
            "Run the analysis again (smaller/better models produce more compact reports)."
        )
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
    for item in data.get("sources", []) or []:
        if isinstance(item, dict) and item.get("source"):
            report["sources"].append(
                {
                    "claim": str(item.get("claim") or ""),
                    "source": str(item["source"]),
                }
            )
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
