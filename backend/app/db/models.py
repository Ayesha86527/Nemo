from typing import Optional
from sqlmodel import Field, SQLModel


class UserProfile(SQLModel, table=True):
    """Core user profile — one row per user (single-user app)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    email: str = ""
    target_roles: str = ""  # comma-separated list of target role titles
    education: str = ""


class Settings(SQLModel, table=True):
    """App-wide LLM settings — one OpenAI-compatible endpoint."""
    id: Optional[int] = Field(default=None, primary_key=True)
    custom_base_url: str = ""  # e.g. https://api.deepseek.com/v1
    custom_api_key: str = ""
    cloud_model: str = ""  # model name served by the endpoint


class MarketIntelReport(SQLModel, table=True):
    """Stored output of a market intelligence run."""
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: str = ""
    target_role: str = ""
    gap_report: str = ""  # structured report JSON produced by the LLM
    provider: str = ""


class CVContent(SQLModel, table=True):
    """Manually-entered CV content — one row (single-user app)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    skills_json: str = "[]"  # ["Python", ...]
    experience_json: str = "[]"  # [{role, company, start, end, description}, ...]
    projects_json: str = "[]"  # [{name, tech, description}, ...]
    achievements_json: str = "[]"  # ["...", ...]
    updated_at: str = ""


class JobApplication(SQLModel, table=True):
    """A tracked job application — the unit of the gated tailoring workflow."""
    id: Optional[int] = Field(default=None, primary_key=True)
    company: str = ""
    role: str = ""
    job_description: str = ""
    status: str = "wishlist"  # wishlist | applied | interview | offer | rejected
    applied_at: str = ""
    follow_up_at: str = ""  # ISO date; empty = no reminder
    notes: str = ""
    # Preparation pipeline output (role-match + company research)
    match_score: Optional[int] = None
    match_summary: str = ""
    research: str = ""
    prepared_at: str = ""
    created_at: str = ""


class CoverLetter(SQLModel, table=True):
    """Generated cover letter, always tied to a prepared job application."""
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobapplication.id")
    content: str = ""
    provider: str = ""
    created_at: str = ""


class Roadmap(SQLModel, table=True):
    """Personalized learning roadmap produced by the Nemo agent."""
    id: Optional[int] = Field(default=None, primary_key=True)
    target_role: str = ""
    goal: str = ""
    horizon_weeks: int = 12
    focus: str = ""  # optional domain/sector the projects are set in — whatever the user names
    preferences: str = ""  # user's specific roadmap instructions (project count, named themes, constraints)
    milestones_json: str = "[]"  # [{title, focus, steps: [{task, done}]}]
    provider: str = ""
    created_at: str = ""


class AgentMessage(SQLModel, table=True):
    """Persisted Nemo agent chat history."""
    id: Optional[int] = Field(default=None, primary_key=True)
    role: str = ""  # "user" | "agent"
    content: str = ""
    created_at: str = ""
