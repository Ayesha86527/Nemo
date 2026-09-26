from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.llm.router import generate

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
