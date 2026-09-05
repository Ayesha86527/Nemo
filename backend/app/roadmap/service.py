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


_SYSTEM = """You are a career coach building a personalized learning roadmap.
Respond with STRICT JSON only, no markdown, matching exactly:
{"goal": "<one-sentence destination>",
 "horizon_weeks": <integer matching the requested duration, or your own sensible choice if none was requested>,
 "milestones": [{"title": "<short milestone name>",
                 "focus": "<what this milestone achieves, naming the gap skill(s) it closes>",
                 "steps": ["<concrete actionable step>", ...]}]}
Reason through the plan in this order:
STEP 1 — Strategic Market Alignment (do this FIRST, before proposing anything):
- Cross-reference the candidate's CV/profile against the market gap analysis. The gap analysis is the source of truth for WHICH SKILLS this roadmap closes and for what the market actually rewards right now.
- Read each relevant skill's status from the analysis ("have", "partial", "missing"). The roadmap's job is to close the market-relevant "missing" and "partial" gaps the candidate has not already proven — never retrain what they already have.
- Every milestone except "Portfolio Projects" must target skills the analysis marks "missing" or "partial", and name those skills in its focus. Cover the most critical missing skills first; weave the analysis' recommendations into the steps.
- Never add training the gap analysis does not justify.
STEP 2 — Project Originality & Impact (apply to every project you propose):
- Before finalizing the Portfolio Projects, evaluate each proposed project for originality and market impact against the tropes that saturate current (2026) portfolios in the candidate's target domain.
- FLAG and avoid clichéd, overdone starter projects — e.g. basic chatbots, thin generic API/LLM wrappers, and Titanic-style or other toy predictors — anything a reviewer has seen a hundred times that proves little.
- Prefer high-impact, differentiated work the gap analysis shows is actually in demand for the target role. For AI/LLM-oriented roles this means projects such as multi-agent systems, production-grade RAG with explicit failure/eval analysis, or LLM evaluation harnesses built on tools like DeepEval/Ragas; for any other domain, choose the equivalent highest-leverage, least-common, market-demanded work — never force an AI project onto a non-AI role.
- Each project must be named specifically, exercise the gap skills, and be set in the DOMAIN FOCUS domain when one is given (otherwise the target role's own domain). Never present a proposed project as work the candidate has already done.
STEP 3 — User Preferences (BINDING — these override the defaults above where they conflict):
- When a USER PREFERENCES block is included, treat it as hard requirements, not suggestions. Honor every part exactly.
- If the user states how many projects they want, the plan must contain EXACTLY that many distinct projects in total across all milestones — never more. Organize the milestones so the gap skills are learned by building those specific projects.
- If the user names project themes (in any sector), those exact themes ARE the projects — do not substitute, rename, or add others.
- If the user wants only NEW projects (not their existing ones), never make a past project the deliverable.
- EXACTLY ONE milestone must be titled "Portfolio Projects": unless USER PREFERENCES already specify the projects, its steps propose 2-3 NEW projects that exercise the gap skills and clear the STEP 2 originality bar; when USER PREFERENCES do specify the projects, use exactly those instead (preferences win over originality — but you may note an originality concern in the milestone focus). These are projects for the candidate to build — never treat them as work already done.
Grounding (no fabrication):
- Do NOT invent precise figures the user never gave and the CV/gap analysis does not contain: no made-up accuracy thresholds, latency targets, dataset sizes, model version numbers, or third-party product/API names.
- Keep steps concrete and achievable by describing the capability ("build an evaluation suite that blocks low-scoring deploys"), not fictional specifics ("block PRs below a 95% score on 50 queries").
- Name a specific tool only where the gap analysis, the CV, or STEP 2's guidance already implies it; otherwise describe the skill, not a fabricated stack.
Re-planning (when a CURRENT ROADMAP is included):
- Re-synthesize the plan from the gaps for the new horizon — never reuse the old milestones with a new week count.
- Shorter horizon: merge related milestones, drop nice-to-have steps, keep only the critical path; every week must carry more outcomes than before.
- Longer horizon: add real depth (new milestones, deeper practice, shipped artifacts) for the same gaps — never stretched filler.
The result must be a flexible, data-backed roadmap that prioritizes uniqueness and market demand. Steps must be concrete (build, ship, learn, apply) and personalized to the candidate — no generic filler."""


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


def latest_market_json() -> str:
    with Session(engine) as session:
        row = session.exec(select(MarketIntelReport).order_by(desc(MarketIntelReport.id))).first()
    return row.gap_report if row else ""


async def market_context_json(generate_fn: GenerateFn, target_role: str, cv_context: str) -> str:
    """The latest gap report — or a fresh analysis, run and persisted, when none exists.

    Guarantees every roadmap is grounded in market gaps even if the user never
    opened Market Intel.
    """
    existing = latest_market_json()
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
                f"\n\nDOMAIN FOCUS (requested by the user): {focus.strip()}. Set the Portfolio Projects "
                f"milestone and every project or worked example in the steps in this domain specifically, "
                f"while still targeting the same gap skills from the analysis. These are projects for the "
                f"candidate to build now — do not present them as work they have already done."
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
