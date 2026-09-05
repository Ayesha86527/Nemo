"""LLM access layer.

Builds the single OpenAI-compatible provider from Settings and degrades
gracefully: provider failures become structured LLMResponse objects with a
user-facing hint instead of exceptions.
"""

import asyncio

from sqlmodel import Session, select

from app.db.engine import engine
from app.db.models import Settings
from app.llm.errors import LLMError
from app.llm.providers import CustomProvider, LLMProvider, LLMResponse

__all__ = ["generate", "build_provider", "get_settings", "LLMResponse"]


async def get_settings() -> Settings:
    """Read (or lazily create) the Settings row."""

    def _read() -> Settings:
        with Session(engine) as session:
            settings = session.exec(select(Settings)).first()
            if settings is None:
                settings = Settings()
                session.add(settings)
                session.commit()
                session.refresh(settings)
            return settings

    return await asyncio.to_thread(_read)


def build_provider(settings: Settings) -> LLMProvider:
    """Factory translating Settings into the configured provider."""
    return CustomProvider(
        api_key=settings.custom_api_key,
        model=settings.cloud_model,
        base_url=settings.custom_base_url,
    )


async def generate(prompt: str, system: str | None = None) -> LLMResponse:
    """Route a prompt to the configured provider.

    Never raises: provider failures are returned as LLMResponse with
    `error` and `hint` populated so callers can degrade gracefully.
    """
    settings = await get_settings()
    provider = build_provider(settings)
    try:
        return await provider.generate(prompt, system=system)
    except LLMError as exc:
        return LLMResponse(
            text="",
            provider=f"{provider.name}_error",
            model=getattr(provider, "model", "unknown"),
            error=str(exc),
            hint=exc.hint or "",
        )
    except Exception as exc:  # defensive: unexpected provider bugs
        return LLMResponse(
            text="",
            provider=f"{provider.name}_error",
            model=getattr(provider, "model", "unknown"),
            error=f"Unexpected error: {exc}",
            hint="Check backend logs for details.",
        )
