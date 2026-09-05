from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.engine import engine
from app.db.models import Settings
from app.llm.providers import normalize_base_url

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsOut(BaseModel):
    custom_base_url: str
    custom_api_key_set: bool
    cloud_model: str


class SettingsUpdate(BaseModel):
    custom_base_url: str | None = None
    custom_api_key: str | None = None
    cloud_model: str | None = None


def _get_or_create(session: Session) -> Settings:
    settings = session.exec(select(Settings)).first()
    if settings is None:
        settings = Settings()
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def _to_out(s: Settings) -> SettingsOut:
    return SettingsOut(
        custom_base_url=s.custom_base_url,
        custom_api_key_set=bool(s.custom_api_key),
        cloud_model=s.cloud_model,
    )


@router.get("", response_model=SettingsOut)
async def get_settings():
    with Session(engine) as session:
        return _to_out(_get_or_create(session))


@router.put("", response_model=SettingsOut)
async def update_settings(payload: SettingsUpdate):
    with Session(engine) as session:
        s = _get_or_create(session)
        if payload.custom_base_url is not None:
            s.custom_base_url = normalize_base_url(payload.custom_base_url)
        if payload.custom_api_key is not None and payload.custom_api_key != "":
            s.custom_api_key = payload.custom_api_key
        if payload.cloud_model is not None:
            s.cloud_model = payload.cloud_model
        session.commit()
        return _to_out(s)
