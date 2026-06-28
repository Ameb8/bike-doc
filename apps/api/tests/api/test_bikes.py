"""Bike profile API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from conftest import ApiTestUser, assert_error_response, assert_no_private_fields
from fastapi import FastAPI

from bike_doc_api.api.deps import get_current_user
from bike_doc_api.api.v1.bikes import get_bike_service
from bike_doc_api.core.errors import NotFoundError
from bike_doc_api.models.user import User as UserModel
from bike_doc_api.schemas.bike import BikeProfile, BikeProfileList

OWNED_BIKE_ID = "bike_owned_contract"
OTHER_BIKE_ID = "bike_other_user"


class FakeBikeService:
    """In-memory route service for bike API tests."""

    def __init__(self) -> None:
        timestamp = datetime(2026, 1, 1, tzinfo=UTC)
        self.bikes: dict[str, BikeProfile] = {
            OWNED_BIKE_ID: _public_bike(
                bike_id=OWNED_BIKE_ID,
                user_id="usr_contract_user",
                created_at=timestamp,
                updated_at=timestamp,
            ),
            OTHER_BIKE_ID: _public_bike(
                bike_id=OTHER_BIKE_ID,
                user_id="usr_other_user",
                created_at=timestamp,
                updated_at=timestamp,
            ),
        }

    async def list_bikes(
        self,
        *,
        current_user: UserModel,
        limit: int = 50,
        cursor: str | None = None,
    ) -> BikeProfileList:
        _ = cursor
        items = [
            bike for bike in self.bikes.values() if bike.user_id == current_user.id
        ][:limit]
        return BikeProfileList(items=items, next_cursor=None)

    async def create_bike(
        self,
        *,
        current_user: UserModel,
        request: Any,
    ) -> BikeProfile:
        timestamp = datetime(2026, 1, 2, tzinfo=UTC)
        bike = BikeProfile(
            id=f"bike_created_{len(self.bikes) + 1}",
            user_id=current_user.id,
            display_name=request.display_name,
            make=request.make,
            model=request.model,
            model_year=request.model_year,
            bike_type=request.bike_type,
            frame_material=request.frame_material,
            drivetrain=request.drivetrain,
            brake_type=request.brake_type,
            wheel_size=request.wheel_size,
            tire_size=request.tire_size,
            notes=request.notes,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self.bikes[bike.id] = bike
        return bike

    async def get_bike(
        self,
        *,
        current_user: UserModel,
        bike_id: str,
    ) -> BikeProfile:
        bike = self.bikes.get(bike_id)
        if bike is None or bike.user_id != current_user.id:
            raise NotFoundError()
        return bike

    async def update_bike(
        self,
        *,
        current_user: UserModel,
        bike_id: str,
        patch: Any,
    ) -> BikeProfile:
        bike = await self.get_bike(current_user=current_user, bike_id=bike_id)
        data = bike.model_dump()
        for field_name in patch.model_fields_set:
            data[field_name] = getattr(patch, field_name)
        updated = BikeProfile.model_validate(data)
        self.bikes[bike_id] = updated
        return updated

    async def delete_bike(
        self,
        *,
        current_user: UserModel,
        bike_id: str,
    ) -> None:
        await self.get_bike(current_user=current_user, bike_id=bike_id)
        del self.bikes[bike_id]


def _public_bike(
    *,
    bike_id: str,
    user_id: str,
    created_at: datetime,
    updated_at: datetime,
) -> BikeProfile:
    return BikeProfile(
        id=bike_id,
        user_id=user_id,
        display_name="Commuter",
        make="Surly",
        model="Straggler",
        model_year=2021,
        bike_type="gravel",
        frame_material="steel",
        drivetrain="Shimano 2x10",
        brake_type="mechanical_disc",
        wheel_size="700c",
        tire_size="700x38",
        notes="Rear rack.",
        created_at=created_at,
        updated_at=updated_at,
    )


@pytest.fixture(autouse=True)
def bike_service_override(app: FastAPI) -> None:
    """Override the route service without hitting a real database."""

    service = FakeBikeService()
    app.dependency_overrides[get_bike_service] = lambda: service


def _valid_create_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "display_name": "Commuter",
        "make": "Surly",
        "model": "Straggler",
        "model_year": 2021,
        "bike_type": "gravel",
        "frame_material": "steel",
        "drivetrain": "Shimano 2x10",
        "brake_type": "mechanical_disc",
        "wheel_size": "700c",
        "tire_size": "700x38",
        "notes": "Rear rack.",
    }
    payload.update(overrides)
    return payload


async def test_list_bikes_returns_owned_profiles(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await api_client.get("/v1/bikes", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["next_cursor"] is None
    assert [bike["id"] for bike in body["items"]] == [OWNED_BIKE_ID]
    assert body["items"][0]["user_id"] == test_user.id
    assert_no_private_fields(body)


async def test_create_bike_returns_created_profile(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    test_user: ApiTestUser,
) -> None:
    response = await api_client.post(
        "/v1/bikes",
        headers=auth_headers,
        json=_valid_create_payload(),
    )

    assert response.status_code == 201
    bike = response.json()
    assert bike["user_id"] == test_user.id
    assert bike["display_name"] == "Commuter"
    assert bike["bike_type"] == "gravel"
    assert_no_private_fields(bike)


async def test_create_bike_with_missing_display_name_returns_422(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.post(
        "/v1/bikes",
        headers=auth_headers,
        json={"make": "Surly"},
    )

    assert_error_response(
        response,
        status_code=422,
        error_code="validation_error",
    )


async def test_get_bike_returns_owned_profile(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.get(
        f"/v1/bikes/{OWNED_BIKE_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    bike = response.json()
    assert bike["id"] == OWNED_BIKE_ID
    assert bike["display_name"] == "Commuter"
    assert_no_private_fields(bike)


async def test_get_unknown_or_not_owned_bike_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for bike_id in ["bike_missing", OTHER_BIKE_ID]:
        response = await api_client.get(f"/v1/bikes/{bike_id}", headers=auth_headers)
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_patch_bike_preserves_omitted_fields_and_clears_explicit_nulls(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.patch(
        f"/v1/bikes/{OWNED_BIKE_ID}",
        headers=auth_headers,
        json={"display_name": "Updated", "notes": None},
    )

    assert response.status_code == 200
    bike = response.json()
    assert bike["display_name"] == "Updated"
    assert bike["notes"] is None
    assert bike["make"] == "Surly"
    assert bike["model_year"] == 2021


async def test_patch_bike_rejects_null_for_non_nullable_fields(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.patch(
        f"/v1/bikes/{OWNED_BIKE_ID}",
        headers=auth_headers,
        json={"bike_type": None},
    )

    assert_error_response(
        response,
        status_code=422,
        error_code="validation_error",
    )


async def test_patch_unknown_or_not_owned_bike_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for bike_id in ["bike_missing", OTHER_BIKE_ID]:
        response = await api_client.patch(
            f"/v1/bikes/{bike_id}",
            headers=auth_headers,
            json={"display_name": "Updated"},
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_delete_bike_returns_no_content(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await api_client.delete(
        f"/v1/bikes/{OWNED_BIKE_ID}",
        headers=auth_headers,
    )

    assert response.status_code == 204
    assert response.content == b""


async def test_delete_unknown_or_not_owned_bike_returns_404(
    api_client: httpx.AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    for bike_id in ["bike_missing", OTHER_BIKE_ID]:
        response = await api_client.delete(
            f"/v1/bikes/{bike_id}",
            headers=auth_headers,
        )
        assert_error_response(response, status_code=404, error_code="not_found")


async def test_bike_routes_require_authentication(
    app: FastAPI,
    api_client: httpx.AsyncClient,
) -> None:
    app.dependency_overrides.pop(get_current_user, None)

    response = await api_client.get("/v1/bikes")
    assert_error_response(response, status_code=401, error_code="unauthorized")
