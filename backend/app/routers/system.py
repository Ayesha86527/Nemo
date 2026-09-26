"""System-level endpoints: manual full data reset."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.reset import reset_all_data

router = APIRouter(prefix="/api/system", tags=["system"])


class ResetResponse(BaseModel):
    reset: bool
    wiped: list[str]


@router.post("/reset", response_model=ResetResponse)
async def reset(include_settings: bool = False):
    """Full clean-slate reset of user data.

    Preserves the LLM endpoint settings unless include_settings=true (the
    endpoint survives so the app remains usable immediately after a reset).
    """
    result = reset_all_data(include_settings=include_settings)
    return ResetResponse(reset=True, wiped=result["wiped"])
