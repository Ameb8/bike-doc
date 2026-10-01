"""Strict private diagnostic instructions."""

import pytest
from pydantic import ValidationError

from bike_doc_api.schemas.background_jobs import DiagnosticTurnInputV1

VALID_TURN = "turn_01ARZ3NDEKTSV4RRFFQ69G5FAV"


def test_accepts_only_turn_identity() -> None:
    assert DiagnosticTurnInputV1(turn_id=VALID_TURN).model_dump() == {
        "turn_id": VALID_TURN
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"turn_id": ""},
        {"turn_id": " "},
        {"turn_id": "turn_bad"},
        {"turn_id": None},
        {"turn_id": 123},
        {"turn_id": "turn_81ARZ3NDEKTSV4RRFFQ69G5FAV"},
        {"turn_id": VALID_TURN, "message": "private"},
        {"turn_id": VALID_TURN, "input_version": 1},
    ],
)
def test_rejects_invalid_input(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        DiagnosticTurnInputV1.model_validate(payload)
