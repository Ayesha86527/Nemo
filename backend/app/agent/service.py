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
from app.db.models import AgentMessage, JobApplication, MarketIntelReport, Roadmap, Settings, UserProfile
from app.deps import cv_context_text, now_iso
from app.jobs.service import (
    VALID_STATUSES,
    JobWorkflowError,
    cover_letter_to_file,
    find_existing_job,
    prepare_job,
    quick_prepare,
    tailor_to_file,
)
from app.llm.providers import LLMResponse
from app.market.engine import MarketIntelError, MarketIntelligenceEngine
from app.roadmap.service import RoadmapError, RoadmapService, market_context_json

GenerateFn = Callable[[str, str | None], Awaitable[LLMResponse]]

HISTORY_LIMIT = 12

_SYSTEM = """You are Nemo, a local-first AI career co-pilot. You help the user manage their CV (skills, experience, projects, achievements), app settings, profile, learning roadmaps, and job applications through natural conversation.
If the prompt includes "Current screen:", tailor your reply and action choices to that screen.
Respond with STRICT JSON only, no markdown, matching:
{"reply": "<friendly, concise reply to the user>",
 "actions": [<zero or more action objects>]}

Action objects (include ONLY when the user asked for the change):
{"type": "cv_update", "skills_add": [], "skills_remove": [], "experience_add": [{"role": "", "company": "", "start": "", "end": "", "description": ""}], "projects_add": [{"name": "", "tech": "", "description": ""}], "achievements_add": []}
{"type": "settings_update", "custom_base_url": "<OpenAI-compatible endpoint URL>", "custom_api_key": "", "cloud_model": "<model name>"}
{"type": "profile_update", "name": "", "email": "", "target_roles": "", "education": ""}
{"type": "roadmap_request", "target_role": "<the role the user names — never assume, default, or substitute one>", "horizon_weeks": <integer only when the user names a duration or a duration change, e.g. "six weeks" or "compress it to a month" → 4>, "focus": "<optional domain/sector to set the roadmap's projects in — use the user's own words, any sector, never a preset default>", "preferences": "<the user's specific roadmap instructions, condensed but complete — e.g. \"exactly 2 projects; brand-new projects only, not my existing ones; apply the gap skills to those 2 projects\">"}
{"type": "market_intel_request", "target_role": ""}
{"type": "job_action", "mode": "tailor"|"cover_letter"|"both", "job_description": "<the job posting text>", "company": "<optional>", "role": "<optional>", "job_id": <optional — id of an already tracked job>}
{"type": "job_update", "job_id": <id from Tracked jobs>, "status": "wishlist"|"applied"|"interview"|"offer"|"rejected", "follow_up_at": "YYYY-MM-DD", "notes": "", "delete": <true only if the user asked to remove the job>}

Rules:
- Omit keys you don't need; use [] for empty lists.
- For cv_update, add ONLY projects, experience, skills, or achievements the user states as their OWN real, already-done work. NEVER invent or embellish any of them — not to match a domain, fill a skill gap, or make the CV stronger. If the user did not state it as something they actually did, it does not go in the CV.
- Tailoring or aligning ROADMAP projects to a domain or sector — any the user names ("make the roadmap projects <sector>", "focus on <sector>") — is a roadmap_request with focus="<the user's sector, in their own words>". It changes ONLY the roadmap's proposed projects. NEVER respond to a roadmap or domain-tailoring request with cv_update.
- For settings_update include only the fields the user wants to change.
- For settings_update naming a service, use its OpenAI-compatible base URL: Groq https://api.groq.com/openai/v1, DeepSeek https://api.deepseek.com/v1, OpenRouter https://openrouter.ai/api/v1, Mistral https://api.mistral.ai/v1, local Ollama http://localhost:11434/v1. If the service is unknown, ask the user for the URL.
- When asked for a roadmap, use roadmap_request with the target role the user names — never assume, default, or substitute a role or sector. Nemo works for any role or sector without bias. If the role is genuinely unclear, ask first.
- CRITICAL — act, don't just claim: whenever the user asks to create, change, refine, re-plan, shorten, lengthen, or tailor a roadmap (including its projects, their count, their themes, or their domain), you MUST include a roadmap_request action in THIS reply. Never say you created/updated/refined/re-synthesized the roadmap unless a roadmap_request action is actually present — describing a change without the action does nothing and misleads the user.
- Capture EVERY specific roadmap instruction in roadmap_request.preferences so it truly reaches the planner: the number of projects, any named project themes (reproduce them verbatim, in whatever sector the user gives), what to include or exclude, "new projects only / not my existing ones", and how to apply the skills. Restate the user's standing preferences from the conversation, updated with this request, so nothing is dropped. preferences is free text — be complete and literal, do not paraphrase away a number or a named theme.
- When the user names a roadmap duration in any words ("6 weeks", "a month", "compress it to four weeks", "stretch it to half a year"), set roadmap_request's horizon_weeks to that number; Nemo re-synthesizes the whole plan for the new horizon instead of relabeling the old one.
- For roadmap requests that don't name a role (especially duration changes to an existing roadmap), omit target_role — Nemo reuses the current roadmap's role, else the profile's first target role.
- Roadmaps are always grounded in the latest market gap analysis; if none exists yet Nemo runs one automatically first — no need to ask the user. When Nemo plans, it first cross-references the candidate's profile/CV against those market gaps (Strategic Market Alignment) and then filters the proposed projects for originality and market impact (Project Originality & Impact) — the planner does this automatically, so just emit the roadmap_request and never claim to run that analysis yourself.
- When asked for market intel / skill-gap analysis, use market_intel_request; take target_role from the request, else from the profile's target roles, else ask.
- When the user shares a job description and wants a tailored CV and/or cover letter, use job_action with the full job description text; the job is tracked automatically.
- NEVER create a duplicate of an already tracked job: if the request refers to a job in the Tracked jobs list (by id, company, or role), set job_action's job_id to that id. Only omit job_id for a genuinely new posting.
- For status changes, follow-up dates, notes, or deleting a tracked job, use job_update with that job's id.
- Keep replies short (1-3 sentences) and confirm what you changed."""


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


def _state_summary() -> str:
    with Session(engine) as session:
        settings = session.exec(select(Settings)).first()
        profile = session.exec(select(UserProfile)).first()
        content, _ = load_content(session)
        jobs = session.exec(
            select(JobApplication).order_by(desc(JobApplication.id)).limit(8)
        ).all()
    lines = [
        f"LLM endpoint: {(settings.custom_base_url if settings else '') or '(not configured)'} "
        f"(model: {(settings.cloud_model if settings else '') or 'not set'})",
        f"Target roles: {profile.target_roles if profile else ''}" or "Target roles: (none)",
        f"CV content: {len(content.skills)} skills, {len(content.experience)} experience entries, "
        f"{len(content.projects)} projects, {len(content.achievements)} achievements",
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
    def __init__(self, generate_fn: GenerateFn, roadmap_service: RoadmapService | None = None):
        self._generate = generate_fn
        self._roadmap = roadmap_service or RoadmapService(generate_fn)

    async def chat(self, message: str, context: str = "") -> AgentTurn:
        history = self._recent_history()
        transcript = "\n".join(f"{m.role}: {m.content}" for m in history)
        prompt = (
            f"Current app state:\n{_state_summary()}\n\n"
            + (f"Current screen: {context}\n\n" if context.strip() else "")
            + (f"Conversation so far:\n{transcript}\n\n" if transcript else "")
            + f"User: {message}"
        )
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

    # --- action application -------------------------------------------------

    async def _apply(self, action: dict) -> str:
        kind = action.get("type")
        if kind == "cv_update":
            return self._apply_cv(action)
        if kind == "settings_update":
            return self._apply_settings(action)
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

    def _apply_settings(self, action: dict) -> str:
        notes: list[str] = []
        with Session(engine) as session:
            settings = session.exec(select(Settings)).first()
            if settings is None:
                settings = Settings()
                session.add(settings)
            base_url = action.get("custom_base_url")
            if base_url:
                settings.custom_base_url = str(base_url).strip()
                notes.append(f"LLM endpoint set to {settings.custom_base_url}")
            if action.get("custom_api_key"):
                settings.custom_api_key = str(action["custom_api_key"]).strip()
                notes.append("LLM API key updated")
            if action.get("cloud_model") is not None:
                settings.cloud_model = str(action["cloud_model"]).strip()
                notes.append(f"Model set to '{settings.cloud_model}'" if settings.cloud_model else "Model cleared")
            session.commit()
        return "; ".join(notes)

    def _apply_profile(self, action: dict) -> str:
        notes: list[str] = []
        fields = ("name", "email", "target_roles", "education")
        with Session(engine) as session:
            profile = session.exec(select(UserProfile)).first()
            if profile is None:
                profile = UserProfile()
                session.add(profile)
            for name in fields:
                value = action.get(name)
                if value is not None:
                    setattr(profile, name, str(value).strip())
                    notes.append(f"Updated {name.replace('_', ' ')}")
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
        mode = str(action.get("mode") or "both").strip().lower()
        if mode not in ("tailor", "cover_letter", "both"):
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
        score = f"{job.match_score}%" if job.match_score is not None else "n/a"
        notes = [f"{note_prefix}: {job.company} — {job.role} (match {score})"]
        if mode in ("tailor", "both"):
            path = await tailor_to_file(self._generate, job.id)
            notes.append(f"Tailored CV saved: {path}")
        if mode in ("cover_letter", "both"):
            _letter, path = await cover_letter_to_file(self._generate, job.id)
            notes.append(f"Cover letter saved: {path}")
        return "; ".join(notes)

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
