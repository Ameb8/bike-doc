"""Private transport-neutral authority for one diagnostic execution."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class DiagnosticExecution:
    job_id: str
    token: str = field(repr=False)
    deadline: datetime


class StaleDiagnosticExecutionError(Exception):
    """Execution has lost authority; never convert this to a product error."""

    def __init__(self) -> None:
        super().__init__("diagnostic_execution_stale")
