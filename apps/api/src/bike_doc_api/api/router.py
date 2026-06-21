"""Top-level API router."""

from fastapi import APIRouter

from bike_doc_api.api.v1 import (
    artifacts,
    bikes,
    decisions,
    events,
    me,
    repair_sessions,
    reports,
    turns,
)

router = APIRouter(prefix="/v1")

router.include_router(me.router)
router.include_router(bikes.router)
router.include_router(artifacts.router)
router.include_router(repair_sessions.router)
router.include_router(turns.router)
router.include_router(events.router)
router.include_router(decisions.router)
router.include_router(reports.router)
