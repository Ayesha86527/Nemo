from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.llm.errors import LLMError
from app.llm.providers import CustomProvider
from app.llm.router import build_provider, generate, get_settings

router = APIRouter(prefix="/api/llm", tags=["llm"])


class GenerateRequest(BaseModel):
    prompt: str
    system: Optional[str] = None


class GenerateResponse(BaseModel):
    text: str
    provider: str
    model: str
    error: Optional[str] = None
    hint: Optional[str] = None


@router.post("/generate", response_model=GenerateResponse)
async def llm_generate(req: GenerateRequest):
    """Route a prompt through the hybrid LLM router."""
    result = await generate(req.prompt, system=req.system)
    return GenerateResponse(
        text=result.text,
        provider=result.provider,
        model=result.model,
        error=result.error,
        hint=result.hint,
    )


@router.get("/models")
async def list_available_models(base_url: Optional[str] = None, api_key: Optional[str] = None):
    """Live model catalog for the endpoint (saved settings by default).

    Accepts a draft base_url/api_key so Settings can refresh the catalog
    while the user is still choosing an endpoint. Errors are returned inline
    so the UI can show guidance instead of failing.
    """
    settings = await get_settings()
    if base_url is not None and base_url.strip():
        selected = CustomProvider(
            base_url=base_url.strip(),
            api_key=api_key or settings.custom_api_key,
            model=settings.cloud_model,
        )
    else:
        selected = build_provider(settings)
    try:
        models = await selected.list_models()
        return {
            "provider": selected.name,
            "current_model": selected.model,
            "models": [{"id": m.id, "name": m.name} for m in models],
            "error": None,
            "hint": None,
        }
    except LLMError as exc:
        return {
            "provider": "custom",
            "current_model": "",
            "models": [],
            "error": str(exc),
            "hint": exc.hint,
        }
