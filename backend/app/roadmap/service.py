"""Personalized roadmap service.

Turns the user's CV content and the latest market intel gap analysis into a
structured learning roadmap with milestones and checkable steps. The gap
analysis is the strategic source of truth: if none exists yet, a fresh one is
run and persisted first. Duration changes re-synthesize the plan (density
budget + previous-plan context) instead of relabeling old milestones.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlmodel import Session, desc, select

from app.db.engine import engine
from app.db.models import MarketIntelReport
from app.llm.providers import LLMResponse
from app.market.engine import MarketIntelligenceEngine

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]


class RoadmapError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


@dataclass
class RoadmapPlan:
    goal: str
    horizon_weeks: int
    milestones: list[dict] = field(default_factory=list)  # [{title, focus, steps:[{task,done}]}]


_SYSTEM = """You are a career coach building a personalized roadmap.
Respond with STRICT JSON only, no markdown, matching exactly:
{"goal": "<one-sentence destination>",
 "horizon_weeks": <integer matching the requested duration, or your own sensible choice if none was requested>,
 "milestones": [{"title": "<short milestone name>",
                 "focus": "<what this milestone achieves, naming the gap skill(s) it closes>",
                 "steps": ["<concrete actionable step>", ...]}]}
Build an explicit evidence-to-outcome map before proposing steps:
1. Identify only the demonstrated candidate evidence, the named desired outcome, market evidence, requested horizon, focus, and preferences supplied in the prompt. Do not infer an industry, profession, seniority, tools, prior work, or unstated preference.
2. Sequence milestones from the candidate's demonstrated level through the market-relevant gaps ("partial" and "missing") toward the named outcome. State the relevant evidence and gap in each milestone's focus. Do not retrain capabilities already demonstrated or add training the supplied evidence does not justify.
3. Make the requested horizon realistic: prioritize the critical path for a short horizon and add justified depth for a longer one. When re-planning, synthesize a new sequence rather than relabeling old milestones.
4. Treat USER PREFERENCES and DOMAIN FOCUS as binding. If the user specifies a number of projects, work samples, or other deliverables, retain that number exactly. If they supply themes, retain them exactly. Never add default deliverables, substitute a theme, or present proposed work as experience the candidate already has.
5. Propose an artifact, project, or work sample only when it directly serves supplied evidence, a market gap, and the stated context. Test it for being distinct, relevant, appropriately scoped, and demonstrable; do not use a fixed template or catalog of examples.
6. If the CV/profile or market evidence is sparse, explicitly say the plan is limited and make the next action request the missing context. Do not invent a path to fill the gap.
Grounding (no fabrication):
- Do not invent quantitative outcomes, company facts, tools, stacks, or prior experience. Name a specific tool only when it appears in supplied evidence.
- Keep steps concrete, achievable, and tied to the evidence-to-outcome map. Use the action that fits the candidate's actual context; avoid generic filler.
The result must be a concise, candidate-specific plan based only on supplied evidence."""


def _density(horizon_weeks: int) -> tuple[int, int]:
    """(milestones, steps per milestone) budget that forces real compression/expansion."""
    if horizon_weeks <= 6:
        return 3, 3
    if horizon_weeks <= 12:
        return 4, 4
    return 5, 5


def parse_roadmap(text: str) -> RoadmapPlan:
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise RoadmapError(
            "The model did not return a valid roadmap.",
            hint="Try again, or switch LLM provider in Settings.",
        )
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise RoadmapError(
            f"Malformed roadmap from model: {exc}",
            hint="Try again, or switch LLM provider in Settings.",
        ) from exc
    if not isinstance(data, dict) or not data.get("milestones"):
        raise RoadmapError(
            "The model returned an empty roadmap.",
            hint="Try again — adding CV content in CV Studio improves results.",
        )

    horizon = data.get("horizon_weeks")
    if not isinstance(horizon, int) or horizon <= 0:
        horizon = 12

    milestones: list[dict] = []
    for m in data.get("milestones", []):
        if not isinstance(m, dict) or not m.get("title"):
            continue
        steps = [
            {"task": str(s).strip(), "done": False}
            for s in m.get("steps", [])
            if str(s).strip()
        ]
        if not steps:
            continue
        milestones.append(
            {"title": str(m["title"]).strip(), "focus": str(m.get("focus") or "").strip(), "steps": steps}
        )
    if not milestones:
        raise RoadmapError(
            "The model returned no usable milestones.",
            hint="Try again, or switch LLM provider in Settings.",
        )
    return RoadmapPlan(goal=str(data.get("goal") or "").strip(), horizon_weeks=horizon, milestones=milestones)


def latest_market_json(target_role: str = "") -> str:
    """Return the newest report for the requested goal, not merely any report.

    A report for a prior target can describe a completely different market, so
    it must never silently become the strategic source of truth for a new plan.
    """
    with Session(engine) as session:
        rows = session.exec(select(MarketIntelReport).order_by(desc(MarketIntelReport.id))).all()
    if not target_role.strip():
        return rows[0].gap_report if rows else ""
    requested = target_role.strip().casefold()
    return next((row.gap_report for row in rows if row.target_role.strip().casefold() == requested), "")


async def market_context_json(generate_fn: GenerateFn, target_role: str, cv_context: str) -> str:
    """The latest gap report — or a fresh analysis, run and persisted, when none exists.

    Guarantees every roadmap is grounded in market gaps even if the user never
    opened Market Intel.
    """
    existing = latest_market_json(target_role)
    if existing:
        return existing
    report = await MarketIntelligenceEngine(generate_fn).run_gap_analysis(
        target_role, cv_context=cv_context
    )
    with Session(engine) as session:
        session.add(
            MarketIntelReport(
                created_at=report.created_at,
                target_role=report.target_role,
                gap_report=json.dumps(report.report),
                provider=report.provider,
            )
        )
        session.commit()
    return json.dumps(report.report)


class RoadmapService:
    def __init__(self, generate_fn: GenerateFn):
        self._generate = generate_fn

    async def generate(
        self,
        target_role: str,
        cv_context: str,
        market_report_json: str = "",
        horizon_weeks: int | None = None,
        previous_plan_json: str = "",
        previous_horizon_weeks: int | None = None,
        focus: str = "",
        preferences: str = "",
    ) -> tuple[RoadmapPlan, str]:
        market_block = ""
        if market_report_json.strip():
            market_block = (
                f"\n\nSTRATEGIC SOURCE OF TRUTH — LATEST MARKET GAP ANALYSIS (JSON):\n{market_report_json}"
            )
        previous_block = ""
        if previous_plan_json.strip():
            previous_block = (
                f"\n\nCURRENT ROADMAP ({previous_horizon_weeks or 'unknown'}-week horizon) — reference only, "
                f"replace it with a re-synthesized plan:\n{previous_plan_json}"
            )
        focus_block = ""
        if focus.strip():
            focus_block = (
                f"\n\nDOMAIN FOCUS (requested by the user, binding): {focus.strip()}. Keep every "
                f"recommendation within this context while still targeting the supplied market gaps."
            )
        duration_block = ""
        if horizon_weeks:
            milestones, steps = _density(horizon_weeks)
            duration_block = (
                f"\nRequested plan duration: {horizon_weeks} weeks. Re-synthesize the content for this "
                f"horizon: exactly {milestones} milestones with exactly {steps} steps each. A "
                f"{horizon_weeks}-week plan must not carry the same content as a plan of a different length."
            )
        preferences_block = ""
        if preferences.strip():
            preferences_block = (
                f"\n\nUSER PREFERENCES (BINDING — honor these exactly; they override the default project "
                f"count and structure above):\n{preferences.strip()}"
            )
        prompt = (
            f"Target role: {target_role}\n\n"
            f"CANDIDATE CV:\n{cv_context.strip() or '(none yet)'}"
            f"{market_block}{previous_block}{focus_block}{duration_block}{preferences_block}\n\n"
            "Build the roadmap now."
        )
        response = await self._generate(prompt, _SYSTEM)
        if response.error:
            raise RoadmapError(response.error, hint=response.hint or "")
        plan = parse_roadmap(response.text)
        if horizon_weeks:
            plan.horizon_weeks = horizon_weeks
        return plan, response.provider
