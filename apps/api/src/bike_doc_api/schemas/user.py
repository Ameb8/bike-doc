"""User API schemas and mappers."""

from datetime import datetime

from bike_doc_api.models.user import User as UserModel
from bike_doc_api.schemas.common import APIBaseModel, UserSkillLevel


class User(APIBaseModel):
    """Public user profile."""

    id: str
    email: str
    display_name: str
    skill_level: UserSkillLevel
    created_at: datetime


def user_from_model(user: UserModel) -> User:
    """Map a persistence user to the public schema."""

    return User(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        skill_level=UserSkillLevel(user.skill_level),
        created_at=user.created_at,
    )
