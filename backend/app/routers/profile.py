from fastapi import APIRouter
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.engine import engine
from app.db.models import UserProfile

router = APIRouter(prefix="/api/profile", tags=["profile"])


class ProfileOut(BaseModel):
    name: str
    email: str
    target_roles: str
    education: str


class ProfileUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    target_roles: str | None = None
    education: str | None = None


def _get_or_create(session: Session) -> UserProfile:
    profile = session.exec(select(UserProfile)).first()
    if profile is None:
        profile = UserProfile()
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def _to_out(p: UserProfile) -> ProfileOut:
    return ProfileOut(name=p.name, email=p.email, target_roles=p.target_roles, education=p.education)


@router.get("", response_model=ProfileOut)
async def get_profile():
    with Session(engine) as session:
        return _to_out(_get_or_create(session))


@router.put("", response_model=ProfileOut)
async def update_profile(payload: ProfileUpdate):
    with Session(engine) as session:
        p = _get_or_create(session)
        for field_name, value in payload.model_dump(exclude_none=True).items():
            setattr(p, field_name, value)
        session.commit()
        return _to_out(p)
