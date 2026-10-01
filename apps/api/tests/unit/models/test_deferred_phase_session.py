"""Deferred phase bindings preserve persistence constraints."""

from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from bike_doc_api.models.repair_session import RepairPhaseSession


def test_unbound_model_and_partial_unique_binding() -> None:
    table = RepairPhaseSession.__table__
    assert table.c.adk_session_id.nullable
    binding_index = next(
        index
        for index in table.indexes
        if index.name == "ux_repair_phase_sessions_adk_session"
    )
    ddl = str(CreateIndex(binding_index).compile(dialect=postgresql.dialect()))
    assert "UNIQUE" in ddl
    assert "WHERE adk_session_id IS NOT NULL" in ddl
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert checks["ck_repair_phase_sessions_adk_session_id_not_blank"] == (
        "length(trim(adk_session_id)) > 0"
    )
    phase_index = next(
        index
        for index in table.indexes
        if index.name == "ux_repair_phase_sessions_session_phase"
    )
    assert phase_index.unique
