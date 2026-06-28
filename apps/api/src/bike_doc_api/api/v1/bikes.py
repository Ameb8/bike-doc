"""Bike profile and repair history route boundary."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from bike_doc_api.api.deps import get_current_user, get_db_session
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.repositories.bikes import BikeRepository
from bike_doc_api.schemas.bike import (
    BikeProfile,
    BikeProfileCreate,
    BikeProfileList,
    BikeProfilePatch,
)
from bike_doc_api.services.bikes import DEFAULT_BIKE_LIMIT, MAX_BIKE_LIMIT, BikeService

router = APIRouter(tags=["Bikes"])


def get_bike_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> BikeService:
    """Build the bike service for this request."""

    return BikeService(
        BikeRepository(session),
        commit=session.commit,
    )


@router.get(
    "/bikes",
    response_model=BikeProfileList,
)
async def list_bikes(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[BikeService, Depends(get_bike_service)],
    limit: Annotated[int, Query(ge=1, le=MAX_BIKE_LIMIT)] = DEFAULT_BIKE_LIMIT,
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> BikeProfileList:
    """List bike profiles for the authenticated user."""

    return await service.list_bikes(
        current_user=current_user,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/bikes",
    response_model=BikeProfile,
    status_code=status.HTTP_201_CREATED,
)
async def create_bike(
    request: BikeProfileCreate,
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[BikeService, Depends(get_bike_service)],
) -> BikeProfile:
    """Create a bike profile for the authenticated user."""

    return await service.create_bike(current_user=current_user, request=request)


@router.get(
    "/bikes/{bikeId}",
    response_model=BikeProfile,
)
async def get_bike(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[BikeService, Depends(get_bike_service)],
    bike_id: Annotated[str, Path(alias="bikeId", min_length=1)],
) -> BikeProfile:
    """Return one owned bike profile."""

    return await service.get_bike(current_user=current_user, bike_id=bike_id)


@router.patch(
    "/bikes/{bikeId}",
    response_model=BikeProfile,
)
async def update_bike(
    patch: BikeProfilePatch,
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[BikeService, Depends(get_bike_service)],
    bike_id: Annotated[str, Path(alias="bikeId", min_length=1)],
) -> BikeProfile:
    """Patch one owned bike profile."""

    return await service.update_bike(
        current_user=current_user,
        bike_id=bike_id,
        patch=patch,
    )


@router.delete(
    "/bikes/{bikeId}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_bike(
    current_user: Annotated[UserModel, Depends(get_current_user)],
    service: Annotated[BikeService, Depends(get_bike_service)],
    bike_id: Annotated[str, Path(alias="bikeId", min_length=1)],
) -> Response:
    """Soft-delete one owned bike profile."""

    await service.delete_bike(current_user=current_user, bike_id=bike_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
