"""Plan report schema boundary."""

from bike_doc_api.schemas.report import PlanReportV1


class PlanReportToolPayload(PlanReportV1):
    """Internal plan report payload accepted from the planning agent."""
