"""Internal immutable job inputs. These are never public API schemas."""

import hashlib
import json
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

VersionIdentifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]


class ProfileInferenceInputV1(BaseModel):
    """Pinned instruction for profile_inference, input version 1."""

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, hide_input_in_errors=True
    )

    turn_id: str = Field(pattern=r"^turn_[0-7][0-9A-HJKMNP-TV-Z]{25}$")
    inference_schema_version: str = Field(
        max_length=128, pattern=r"^bike_profile_inference\.v[1-9][0-9]*$"
    )
    extractor_version: VersionIdentifier

    def deduplication_key(self) -> str:
        """Hash an unambiguous canonical tuple, independent of JSON field order."""
        identity = json.dumps(
            [self.turn_id, self.inference_schema_version, self.extractor_version],
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return hashlib.sha256(identity.encode("ascii")).hexdigest()
