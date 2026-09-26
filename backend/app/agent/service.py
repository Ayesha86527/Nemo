"""Nemo agent: conversational interface over the whole app.

The LLM replies and proposes STRUCTURED actions (JSON); every mutation is
applied deterministically by this service — the model never writes to the
database directly. Supported actions: CV content updates, settings changes,
profile updates, roadmap requests, and job actions (prepare/tailor/cover
letter, which auto-track the job in the Job Tracker).
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from sqlmodel import Session, desc, select

from app.cv.content import (
    CVContentData,
    ExperienceItem,
    ProjectItem,
    load_content,
    save_content,
)
from app.db.engine import engine
from app.db.models import AgentMessage, JobApplication, MarketIntelReport, Roadmap, UserProfile
from app.deps import cv_context_text, get_profile_row, now_iso
from app.jobs.service import (
    VALID_STATUSES,
    JobWorkflowError,
    cover_letter_to_file,
    find_existing_job,
    linkedin_dm_text,
    prepare_job,
    quick_prepare,
    save_linkedin_dm,
    tailor_to_file,
)
from app.llm.providers import LLMResponse
from app.market.engine import MarketIntelError, MarketIntelligenceEngine
from app.roadmap.service import RoadmapError, RoadmapService, market_context_json

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

HISTORY_LIMIT = 12


def onboarding_greeting(has_name: bool, has_experience: bool, has_goals: bool) -> str:
    """Deterministic launch greeting that drives context gathering."""
    if not has_name:
        return (
            "Hi there! Is there anything new to tell? Before we dive in — I'm Nemo, your "
            "career co-pilot. To build your profile, what's your name?"
        )
    if not has_experience:
        return (
            "Hi there — is there anything new to tell? To update my memory of you: share your "
            "recent experience (a sentence about each role is enough), or upload your CV in CV "
            "Studio and I'll extract it."
        )
    if not has_goals:
        return (
            "Hi there! Is there anything new to tell? One more thing I'd like to remember: what "
            "are your short-term and long-term career goals?"
        )
    return "Hi there! Is there anything new to tell? Any new skills, projects, or experiences to add?"

_SYSTEM = """You are Nemo, an intelligent project assistant and career co-pilot with persistent memory of one candidate. You help manage their CV (skills, experience, projects, achievements), profile, learning roadmaps, market intelligence, and job applications through natural conversation.
If the prompt includes "Current screen:", tailor your reply and action choices to that screen.
Respond with STRICT JSON only, no markdown, matching:
{"reply": "<friendly, concise reply to the user>",
 "actions": [<zero or more action objects>]}

Action objects (include ONLY when the user asked for the change):
{"type": "cv_update", "skills_add": [], "skills_remove": [], "experience_add": [{"role": "", "company": "", "start": "", "end": "", "description": ""}], "projects_add": [{"name": "", "tech": "", "description": ""}], "achievements_add": []}
{"type": "profile_update", "name": "", "email": "", "target_roles": "", "education": "", "short_term_goal": "", "long_term_goal": ""}
{"type": "roadmap_request", "target_role": "<the role the user names — never assume, default, or substitute one>", "horizon_weeks": <integer only when the user names a duration or a duration change>, "focus": "<optional domain/sector to set the roadmap's projects in — use the user's own words>", "preferences": "<the user's specific roadmap instructions, condensed but complete>"}
{"type": "market_intel_request", "target_role": ""}
{"type": "job_action", "mode": "analyze"|"tailor"|"cover_letter"|"linkedin_dm"|"full", "job_description": "<the job posting text>", "company": "<optional>", "role": "<optional>", "job_id": <optional — id of an already tracked job>}
{"type": "job_update", "job_id": <id from Tracked jobs>, "status": "wishlist"|"applied"|"interview"|"offer"|"rejected", "follow_up_at": "YYYY-MM-DD", "notes": "", "delete": <true only if the user asked to remove the job>}

YOUR KNOWLEDGE (strict scope):
- Your context contains: the candidate's profile and goals, their CV content, the current learning roadmap, the latest market intelligence report, the tracked job applications, and this conversation. That is your ENTIRE knowledge base.
- You have NO access to system or app settings (LLM endpoint, API keys, model names) or anything else not listed above. If asked about settings, say you can't see or change them and point the user to the Settings screen.

GROUNDING RULES (anti-hallucination — highest priority):
- Answer ONLY from the context above, the user's current message, and general reasoning the user explicitly requested. NEVER invent facts, skills, dates, scores, companies, market data, or roadmap content.
- If the answer is not in your context, SAY SO plainly: "I don't have that in your data" / "I don't know — I can't see that." A correct "I don't know" is always better than a plausible guess. Never fill gaps with fabricated details.
- Follow the user's instructions EXACTLY as stated. Do not reinterpret, expand, or substitute what they asked for. If an instruction is ambiguous in a way that changes the outcome, ask one clarifying question instead of guessing.
- Never claim an action was performed unless its action object is in THIS reply — the backend applies actions deterministically; describing a change without emitting the action does nothing and misleads the user.
- For cv_update, add ONLY skills, experience, projects, or achievements the user states as their OWN real, already-done work. NEVER invent or embellish — not to match a domain, fill a skill gap, or make the CV stronger.

PERSISTENT MEMORY (context engineering):
- Whenever the user shares anything personal — name, a role they held, a project they built, a skill they learned, a goal — capture it with profile_update and/or cv_update. Never let a stated fact go unrecorded.
- Until you know their name, background, and goals, gently ask about the next missing piece (one question per reply, never interrogate).

JOB DESCRIPTIONS ARE AUTO-ANALYZED: when the user pastes a job description (or asks to evaluate one), ALWAYS emit a job_action with mode="analyze" (or "full" when they asked for tailored CV / cover letter / outreach too) and the FULL verbatim job description text. Mode meanings: "analyze" = parse + research + fit verdict; "tailor" = analyze, then tailor the CV only when misaligned; "cover_letter" = analyze then generate a tailored cover letter; "linkedin_dm" = analyze then draft a short LinkedIn outreach message; "full" = analyze + tailor-if-misaligned + cover letter + LinkedIn DM.
- NEVER create a duplicate tracked job: if the request refers to a job in the Tracked jobs list (by id, company, or role), set job_action's job_id to that id.
- For status changes, follow-up dates, notes, or deleting a tracked job, use job_update with that job's id.

ROADMAPS:
- When asked to create, change, refine, re-plan, shorten, lengthen, or tailor a roadmap (including its projects, counts, themes, or domain), you MUST include a roadmap_request action in THIS reply. Never claim a change without the action present.
- Use the target role the user names — never assume, default, or substitute one. If genuinely unclear, ask first.
- Capture EVERY specific roadmap instruction in preferences (project counts, named themes verbatim, inclusions/exclusions, how to apply skills). preferences is free text — be complete and literal.
- When the user names a duration in any words ("6 weeks", "a month"), set horizon_weeks to that number. For requests that don't name a role, omit target_role — Nemo reuses the current roadmap's role, else the profile's first target role.

MARKET INTEL: when asked for skill-gap or market analysis, use market_intel_request; take target_role from the request, else the profile's target roles, else ask. Report market findings only from the latest market intelligence report in your context — never quote scores or trends you cannot see there.

Rules:
- Omit action keys you don't need; use [] for empty lists.
- Keep replies short (1-3 sentences) and confirm what you changed.
- If the user asks for something outside your scope (settings, actions you don't have), say so directly instead of working around it."""


@dataclass
class AgentTurn:
    reply: str
    changes: list[str] = field(default_factory=list)
    cv_updated: bool = False


def _decode_objects(text: str) -> tuple[list[dict], int]:
    """Recover action objects from possibly-malformed model output.

    Scans for balanced JSON objects and collects any that are an action (have a
    "type") or a wrapper (have an "actions" list). Returns the de-duped actions
    and the index of the first recovered object (so the reply can be cut there).
    """
    decoder = json.JSONDecoder()
    actions: list[dict] = []
    first_index = -1
    i, n = 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i += 1
            continue
        if isinstance(obj, dict):
            wrapped = obj.get("actions")
            if isinstance(wrapped, list):
                for a in wrapped:
                    if isinstance(a, dict) and a.get("type"):
                        if first_index < 0:
                            first_index = i
                        actions.append(a)
            elif obj.get("type"):
                if first_index < 0:
                    first_index = i
                actions.append(obj)
            i = end
            continue
        i += 1
    seen: set[str] = set()
    deduped: list[dict] = []
    for a in actions:
        key = json.dumps(a, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            deduped.append(a)
    return deduped, first_index


def _clean_reply(text: str, cut_at: int) -> str:
    """The human-readable reply, with any leading wrapper and embedded JSON removed."""
    reply = text[:cut_at] if cut_at > 0 else text
    m = re.match(r'^\s*\{\s*"reply"\s*:\s*"?', reply, flags=re.DOTALL)
    if m:
        reply = reply[m.end():].rstrip()
        if reply.endswith('"'):
            reply = reply[:-1]
    return reply.strip().rstrip(",").strip()


def parse_agent_response(text: str) -> dict:
    """Tolerant parse: recovers the reply AND actions even from malformed JSON.

    A strict json.loads fails when the model nests the actions inside the reply
    string or emits raw newlines; the old fallback then dropped every action, so
    the agent claimed a change it never applied. We always try to salvage the
    real actions and strip raw JSON out of the user-facing reply.
    """
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()

    data = None
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict) and parsed.get("reply"):
                data = parsed
        except json.JSONDecodeError:
            data = None

    if data is not None:
        reply = str(data.get("reply") or "")
        actions = [a for a in (data.get("actions") or []) if isinstance(a, dict) and a.get("type")]
        if not actions:
            recovered, idx = _decode_objects(reply)
            if recovered:
                actions, reply = recovered, _clean_reply(reply, idx)
        return {"reply": reply.strip(), "actions": actions}

    actions, idx = _decode_objects(cleaned)
    reply = _clean_reply(cleaned, idx) if idx > 0 else cleaned.split("{", 1)[0].strip()
    if not reply:
        reply = text.strip()
    return {"reply": reply, "actions": actions}


# Prompt budget constants — keeps all supported models comfortable
_MAX_HISTORY_CHARS = 4_000   # sliding window on conversation transcript
_MAX_PROMPT_CHARS  = 14_000  # total prompt hard-cap (chars ≈ 3.5k tokens)


def _truncate(text: str, max_chars: int, label: str = "") -> str:
    if len(text) <= max_chars:
        return text
    suffix = f" [...{label} truncated]" if label else " [...truncated]"
    return text[:max_chars].rstrip() + suffix


def _roadmap_summary() -> str:
    """Compact view of the newest roadmap: goal, cadence, progress, milestones."""
    with Session(engine) as session:
        row = session.exec(select(Roadmap).order_by(desc(Roadmap.id))).first()
    if row is None:
        return "Roadmap: (none yet)"
    milestones = json.loads(row.milestones_json or "[]")
    steps = [s for m in milestones for s in (m.get("steps") or [])]
    done = sum(1 for s in steps if s.get("done"))
    titles = "; ".join(m.get("title", "") for m in milestones[:6])
    progress = f"{done}/{len(steps)} steps done" if steps else "no steps"
    return (
        f"Roadmap for {row.target_role}: goal — {row.goal[:160]} | {row.horizon_weeks} weeks, "
        f"{progress} | milestones: {titles}"
        + (f" | focus: {row.focus[:60]}" if row.focus else "")
        + (f" | preferences: {row.preferences[:120]}" if row.preferences else "")
    )


def _market_summary() -> str:
    """Compact view of the newest market intelligence report."""
    with Session(engine) as session:
        row = session.exec(select(MarketIntelReport).order_by(desc(MarketIntelReport.id))).first()
    if row is None:
        return "Market intel: (none yet)"
    try:
        report = json.loads(row.gap_report or "{}")
    except json.JSONDecodeError:
        report = {}
    score = report.get("match_score")
    score_note = f"match {score}%" if isinstance(score, (int, float)) else "no score"
    gaps = report.get("skill_gaps") or []
    gap_note = "; ".join(f"{g.get('skill')} ({g.get('status')})" for g in gaps[:5] if isinstance(g, dict))
    signals = report.get("market_signals") or []
    return (
        f"Market intel for {row.target_role} ({score_note}): {str(report.get('summary') or '')[:220]}"
        + (f" | gaps: {gap_note}" if gap_note else "")
        + (f" | signals: {'; '.join(str(s) for s in signals[:3])}" if signals else "")
    )


def _state_summary() -> str:
    with Session(engine) as session:
        profile = session.exec(select(UserProfile)).first()
        content, _ = load_content(session)
        jobs = session.exec(
            select(JobApplication).order_by(desc(JobApplication.id)).limit(8)
        ).all()
    # Skills: show first 15, count the rest
    skills_preview = ", ".join(content.skills[:15])
    if len(content.skills) > 15:
        skills_preview += f" (+{len(content.skills) - 15} more)"
    lines = [
        # NOTE: system/app settings (LLM endpoint, key, model) are deliberately
        # NOT included — the agent has no access to settings by design.
        f"Candidate: {profile.name if profile else '(name unknown)'}"
        if profile and profile.name
        else "Candidate: (name unknown)",
        f"Target roles: {profile.target_roles if profile else ''}" or "Target roles: (none)",
        (
            f"Short-term goal: {profile.short_term_goal if profile else ''}"
            if profile and profile.short_term_goal
            else "Short-term goal: (unknown — ask)"
        ),
        (
            f"Long-term goal: {profile.long_term_goal if profile else ''}"
            if profile and profile.long_term_goal
            else "Long-term goal: (unknown — ask)"
        ),
        f"CV: {len(content.skills)} skills ({skills_preview}), "
        f"{len(content.experience)} exp entries, "
        f"{len(content.projects)} projects, "
        f"{len(content.achievements)} achievements",
        _roadmap_summary(),
        _market_summary(),
        "Tracked jobs: "
        + (
            "; ".join(
                f"#{j.id} {j.company} — {j.role} ({j.status}, "
                f"{'prepared' if j.prepared_at else 'not prepared'})"
                for j in jobs
            )
            if jobs
            else "(none)"
        ),
    ]
    return "\n".join(lines)


class AgentService:
    def __init__(
        self,
        generate_fn: GenerateFn,
        roadmap_service: RoadmapService | None = None,
        stream_fn=None,
    ):
        self._generate = generate_fn
        self._stream = stream_fn   # async generator (prompt, system) → chunks
        self._roadmap = roadmap_service or RoadmapService(generate_fn)

    def _build_prompt(self, message: str, context: str) -> str:
        history = self._recent_history()
        transcript = "\n".join(f"{m.role}: {m.content}" for m in history)
        # Sliding window: drop oldest turns if transcript exceeds budget
        if len(transcript) > _MAX_HISTORY_CHARS:
            lines = transcript.split("\n")
            while len("\n".join(lines)) > _MAX_HISTORY_CHARS and len(lines) > 2:
                lines = lines[2:]
            transcript = "\n".join(lines)
        prompt = (
            f"Current app state:\n{_state_summary()}\n\n"
            + (f"Current screen: {context}\n\n" if context.strip() else "")
            + (f"Conversation so far:\n{transcript}\n\n" if transcript else "")
            + f"User: {message}"
        )
        # Hard-cap total prompt — truncate state summary section if needed
        if len(prompt) > _MAX_PROMPT_CHARS:
            prompt = _truncate(prompt, _MAX_PROMPT_CHARS, "context")
        return prompt

    async def chat(self, message: str, context: str = "") -> AgentTurn:
        prompt = self._build_prompt(message, context)
        response = await self._generate(prompt, _SYSTEM)
        if response.error:
            raise AgentError(response.error, hint=response.hint or "")

        data = parse_agent_response(response.text)
        turn = AgentTurn(reply=str(data["reply"]))
        for action in data.get("actions", []):
            if not isinstance(action, dict):
                continue
            try:
                note = await self._apply(action)
            except RoadmapError as exc:
                note = f"Roadmap failed: {exc}"
            except JobWorkflowError as exc:
                note = f"Job action failed: {exc}"
            except MarketIntelError as exc:
                note = f"Market Intel failed: {exc}"
            if note:
                turn.changes.append(note)
                if action.get("type") == "cv_update":
                    turn.cv_updated = True
        if turn.changes:
            turn.reply += "\n\n" + "\n".join(f"- {c}" for c in turn.changes)

        self._persist(message, turn.reply)
        return turn

    async def stream_chat(self, message: str, context: str = ""):
        """Streaming agent chat. Yields SSE-formatted strings.

        Event types:
          {"type": "chunk", "text": "..."}          — token chunk as it arrives
          {"type": "done",  "reply": "...",
           "changes": [...], "cv_updated": bool}    — final event with actions
          {"type": "error", "error": "...",
           "hint": "..."}                           — on failure
        """
        import json as _json

        prompt = self._build_prompt(message, context)
        full_text = ""

        if self._stream is not None:
            try:
                async for chunk in self._stream(prompt, _SYSTEM):
                    full_text += chunk
                    yield f'data: {_json.dumps({"type": "chunk", "text": chunk})}\n\n'
            except Exception as exc:
                hint = exc.hint if hasattr(exc, "hint") else ""
                yield f'data: {_json.dumps({"type": "error", "error": str(exc), "hint": hint})}\n\n'
                return
        else:
            # Fallback: non-streaming generate, deliver in one chunk
            response = await self._generate(prompt, _SYSTEM)
            if response.error:
                yield f'data: {_json.dumps({"type": "error", "error": response.error, "hint": response.hint or ""})}\n\n'
                return
            full_text = response.text
            yield f'data: {_json.dumps({"type": "chunk", "text": full_text})}\n\n'

        # Parse and apply actions from the full collected text
        data = parse_agent_response(full_text)
        turn = AgentTurn(reply=str(data["reply"]))
        for action in data.get("actions", []):
            if not isinstance(action, dict):
                continue
            try:
                note = await self._apply(action)
            except (RoadmapError, JobWorkflowError, MarketIntelError) as exc:
                note = f"{type(exc).__name__}: {exc}"
            if note:
                turn.changes.append(note)
                if action.get("type") == "cv_update":
                    turn.cv_updated = True
        if turn.changes:
            turn.reply += "\n\n" + "\n".join(f"- {c}" for c in turn.changes)

        self._persist(message, turn.reply)
        yield f'data: {_json.dumps({"type": "done", "reply": turn.reply, "changes": turn.changes, "cv_updated": turn.cv_updated})}\n\n'



    # --- action application -------------------------------------------------

    async def _apply(self, action: dict) -> str:
        kind = action.get("type")
        if kind == "cv_update":
            return self._apply_cv(action)
        if kind == "settings_update":
            # Settings are outside the agent's scope by design — refuse cleanly
            # instead of silently ignoring, so the user knows where to go.
            return "I can't change app settings — that's outside my scope. Use the Settings screen."
        if kind == "profile_update":
            return self._apply_profile(action)
        if kind == "roadmap_request":
            return await self._apply_roadmap(
                str(action.get("target_role") or "").strip(),
                action.get("horizon_weeks"),
                str(action.get("focus") or "").strip(),
                str(action.get("preferences") or "").strip(),
            )
        if kind == "market_intel_request":
            return await self._apply_market(str(action.get("target_role") or "").strip())
        if kind == "job_update":
            return self._apply_job_update(action)
        if kind == "job_action":
            return await self._apply_job(action)
        return ""

    def _apply_cv(self, action: dict) -> str:
        notes: list[str] = []
        with Session(engine) as session:
            content, _ = load_content(session)
            lowered = {s.lower() for s in content.skills}
            for skill in action.get("skills_add", []) or []:
                skill = str(skill).strip()
                if skill and skill.lower() not in lowered:
                    content.skills.append(skill)
                    lowered.add(skill.lower())
                    notes.append(f"Added skill: {skill}")
            for skill in action.get("skills_remove", []) or []:
                target = str(skill).strip().lower()
                before = len(content.skills)
                content.skills = [s for s in content.skills if s.lower() != target]
                if len(content.skills) < before:
                    notes.append(f"Removed skill: {skill}")
            for item in action.get("experience_add", []) or []:
                if not isinstance(item, dict) or not str(item.get("role") or "").strip():
                    continue
                content.experience.append(
                    ExperienceItem(
                        role=str(item.get("role") or "").strip(),
                        company=str(item.get("company") or "").strip(),
                        start=str(item.get("start") or "").strip(),
                        end=str(item.get("end") or "").strip(),
                        description=str(item.get("description") or "").strip(),
                    )
                )
                notes.append(f"Added experience: {item['role']}")
            for item in action.get("projects_add", []) or []:
                if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                    continue
                content.projects.append(
                    ProjectItem(
                        name=str(item.get("name") or "").strip(),
                        tech=str(item.get("tech") or "").strip(),
                        description=str(item.get("description") or "").strip(),
                    )
                )
                notes.append(f"Added project: {item['name']}")
            for achievement in action.get("achievements_add", []) or []:
                achievement = str(achievement).strip()
                if achievement:
                    content.achievements.append(achievement)
                    notes.append(f"Added achievement: {achievement}")
            if notes:
                save_content(session, content, now_iso())
        return "; ".join(notes)

    def _apply_profile(self, action: dict) -> str:
        notes: list[str] = []
        fields = ("name", "email", "target_roles", "education", "short_term_goal", "long_term_goal")
        with Session(engine) as session:
            profile = session.exec(select(UserProfile)).first()
            if profile is None:
                profile = UserProfile()
                session.add(profile)
            for name in fields:
                value = action.get(name)
                if value is not None and str(value).strip():
                    setattr(profile, name, str(value).strip())
                    notes.append(f"Noted {name.replace('_', ' ')}")
            session.commit()
        return "; ".join(notes)

    async def _apply_roadmap(self, target_role: str, horizon_raw=None, focus: str = "", preferences: str = "") -> str:
        with Session(engine) as session:
            prev = session.exec(select(Roadmap).order_by(desc(Roadmap.id))).first()
            profile = session.exec(select(UserProfile)).first()
        if not target_role:
            target_role = (prev.target_role if prev else "") or (
                profile.target_roles.split(",")[0].strip() if profile else ""
            )
        if not target_role:
            return "Roadmap needs a target role — tell me which role to plan for."
        focus = focus.strip() or (prev.focus if prev else "")
        preferences = preferences.strip() or (prev.preferences if prev else "")
        horizon = None
        if isinstance(horizon_raw, (int, float)) and not isinstance(horizon_raw, bool):
            horizon = int(horizon_raw)
            if not 1 <= horizon <= 52:
                horizon = None
        cv_context = cv_context_text()
        market_json = await market_context_json(self._generate, target_role, cv_context)
        previous_json, previous_horizon = "", None
        if horizon is not None and prev is not None and prev.horizon_weeks != horizon:
            previous_json, previous_horizon = prev.milestones_json, prev.horizon_weeks
        plan, provider = await self._roadmap.generate(
            target_role,
            cv_context,
            market_json,
            horizon_weeks=horizon,
            previous_plan_json=previous_json,
            previous_horizon_weeks=previous_horizon,
            focus=focus,
            preferences=preferences,
        )
        with Session(engine) as session:
            row = Roadmap(
                target_role=target_role,
                goal=plan.goal,
                horizon_weeks=plan.horizon_weeks,
                focus=focus,
                preferences=preferences,
                milestones_json=json.dumps(plan.milestones),
                provider=provider,
                created_at=now_iso(),
            )
            session.add(row)
            session.commit()
        domain = f" focused on {focus}" if focus else ""
        followed = " following your preferences" if preferences else ""
        if previous_horizon:
            return (
                f"Re-synthesized your roadmap from {previous_horizon} weeks to "
                f"{plan.horizon_weeks} weeks for {target_role}{domain}{followed} — open the Roadmap view."
            )
        return f"Created a {plan.horizon_weeks}-week roadmap for {target_role}{domain}{followed} — open the Roadmap view."

    async def _apply_market(self, target_role: str) -> str:
        if not target_role:
            with Session(engine) as session:
                profile = session.exec(select(UserProfile)).first()
            target_role = (profile.target_roles if profile else "") or ""
        if not target_role.strip():
            return "Market Intel needs a target role — tell me which role to analyze."
        market = MarketIntelligenceEngine(generate_fn=self._generate)
        report = await market.run_gap_analysis(
            target_role.strip(), cv_context=cv_context_text()
        )
        with Session(engine) as session:
            row = MarketIntelReport(
                created_at=report.created_at,
                target_role=report.target_role,
                gap_report=json.dumps(report.report),
                provider=report.provider,
            )
            session.add(row)
            session.commit()
        score = report.report.get("match_score")
        score_note = f" — match {score}%" if isinstance(score, (int, float)) else ""
        return f"Market Intel refreshed for {report.target_role}{score_note} — open the Market Intel view."

    def _apply_job_update(self, action: dict) -> str:
        job_id = action.get("job_id")
        if not isinstance(job_id, int) or isinstance(job_id, bool):
            return "job_update needs a job_id — pick one from the Tracked jobs list."
        with Session(engine) as session:
            row = session.get(JobApplication, job_id)
            if row is None:
                return f"No tracked job #{job_id} — check the Tracked jobs list."
            label = f"job #{job_id} ({row.company} — {row.role})"
            if action.get("delete"):
                session.delete(row)
                session.commit()
                return f"Deleted {label}"
            changes: list[str] = []
            status = str(action.get("status") or "").strip().lower()
            if status:
                if status not in VALID_STATUSES:
                    return f"'{status}' is not a valid status (wishlist/applied/interview/offer/rejected)."
                row.status = status
                changes.append(f"status → {status}")
            follow = str(action.get("follow_up_at") or "").strip()
            if follow:
                row.follow_up_at = follow
                changes.append(f"follow-up set to {follow}")
            if action.get("notes") is not None:
                row.notes = str(action["notes"]).strip()
                changes.append("notes updated")
            if changes:
                session.commit()
        return f"Updated {label}: " + "; ".join(changes) if changes else f"No changes for {label}."

    async def _apply_job(self, action: dict) -> str:
        mode = str(action.get("mode") or "analyze").strip().lower()
        valid_modes = ("analyze", "tailor", "cover_letter", "linkedin_dm", "full")
        if mode not in valid_modes:
            return f"Ignored invalid job_action mode '{mode}'"
        jd = str(action.get("job_description") or "").strip()
        company = str(action.get("company") or "").strip()
        role = str(action.get("role") or "").strip()
        job: JobApplication | None = None
        job_id = action.get("job_id")
        if isinstance(job_id, int) and not isinstance(job_id, bool):
            with Session(engine) as session:
                job = session.get(JobApplication, job_id)
        if job is None and jd:
            # Reuse an already-tracked job instead of creating a duplicate.
            job = find_existing_job(job_description=jd, company=company, role=role)
        if job is None:
            if not jd:
                return "Job action needs the job description text or the id of a tracked job."
            job = await quick_prepare(self._generate, jd, company=company, role=role)
            note_prefix = f"Tracked job #{job.id}"
        else:
            if not job.prepared_at:
                job = await prepare_job(self._generate, job.id)
            note_prefix = f"Reused tracked job #{job.id} (no duplicate created)"

        notes = [f"{note_prefix}: {job.company} — {job.role} (match {self._fmt_score(job)})"]
        job = await self._maybe_tailor(mode, job, notes)
        if mode in ("cover_letter", "full"):
            _letter, path = await cover_letter_to_file(self._generate, job.id)
            notes.append(f"Cover letter saved: {path}")
        if mode in ("linkedin_dm", "full"):
            job = await self._apply_linkedin_dm(job)
            notes.append("LinkedIn DM drafted — open the job's detail view to copy it.")
        if mode in ("analyze", "tailor") and mode != "tailor":
            verdict = (
                f"Fit verdict: {job.match_score}% — {job.match_summary[:220]}"
                if job.match_summary
                else "Analysis complete — open the Job Tracker for the full breakdown."
            )
            notes.append(verdict)
        return "; ".join(notes)

    @staticmethod
    def _fmt_score(job: JobApplication) -> str:
        return f"{job.match_score}%" if job.match_score is not None else "n/a"

    async def _maybe_tailor(self, mode: str, job: JobApplication, notes: list[str]) -> JobApplication:
        """Tailor only when the JD asks for it AND the CV is actually misaligned."""
        if mode not in ("tailor", "full"):
            return job
        threshold = 70
        aligned = job.match_score is not None and job.match_score >= threshold
        if aligned:
            notes.append(
                f"CV already aligns ({job.match_score}% ≥ {threshold}%) — kept the original, no tailored version generated."
            )
            return job
        reason = (
            f"CV misaligned ({job.match_score}% < {threshold}%)"
            if job.match_score is not None
            else "alignment unknown"
        )
        path = await tailor_to_file(self._generate, job.id)
        notes.append(f"{reason} — Tailored CV saved: {path}")
        return job

    async def _apply_linkedin_dm(self, job: JobApplication) -> JobApplication:
        """Draft a short LinkedIn outreach message grounded in the JD + fit analysis."""
        profile = get_profile_row()
        dm = await linkedin_dm_text(
            self._generate,
            candidate_name=profile.name,
            company=job.company,
            role=job.role,
            job_description=job.job_description,
            research=job.research,
            match_summary=job.match_summary,
        )
        return save_linkedin_dm(job.id, dm)

    # --- history -------------------------------------------------------------

    def _recent_history(self) -> list[AgentMessage]:
        with Session(engine) as session:
            rows = session.exec(
                select(AgentMessage).order_by(desc(AgentMessage.id)).limit(HISTORY_LIMIT)
            ).all()
        return list(reversed(rows))

    def _persist(self, user_message: str, agent_reply: str) -> None:
        with Session(engine) as session:
            session.add(AgentMessage(role="user", content=user_message, created_at=now_iso()))
            session.add(AgentMessage(role="agent", content=agent_reply, created_at=now_iso()))
            session.commit()


class AgentError(Exception):
    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint
