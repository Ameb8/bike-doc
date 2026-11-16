"""Private transport-neutral authority for one interactive turn execution."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class InteractiveTurnExecution:
    job_id: str
    token: str = field(repr=False)
    deadline: datetime


class StaleInteractiveTurnExecutionError(Exception):
    """Execution has lost authority; never convert this to a product error."""

    def __init__(self) -> None:
        super().__init__("interactive_turn_execution_stale")
