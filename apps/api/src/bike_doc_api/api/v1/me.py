"""Authenticated user route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends

from bike_doc_api.api.deps import get_current_user
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.schemas.user import User, user_from_model

router = APIRouter(tags=["Auth"])


@router.get("/me", response_model=User)
async def get_me(
    current_user: Annotated[UserModel, Depends(get_current_user)],
) -> User:
    """Return the resolved current user."""

    return user_from_model(current_user)
