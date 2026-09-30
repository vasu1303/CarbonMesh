from uuid import UUID

from fastapi import APIRouter

from app.api.errors import safe_http_error
from app.dependencies.database import DatabaseSession
from app.dependencies.request import TraceIdHeader
from app.modules.ledger.schemas import MeasurementLineageResult
from app.modules.ledger.service import MeasurementNotFoundError, get_measurement_lineage

router = APIRouter()


@router.get("/measurements/{measurement_id}/lineage", response_model=MeasurementLineageResult)
async def read_measurement_lineage(
    measurement_id: UUID,
    company_id: UUID,
    session: DatabaseSession,
    trace_id: TraceIdHeader = None,
) -> MeasurementLineageResult:
    try:
        return await get_measurement_lineage(
            session,
            company_id=company_id,
            measurement_id=measurement_id,
        )
    except MeasurementNotFoundError as error:
        raise safe_http_error(
            status_code=404,
            code="measurement_not_found",
            message="The requested measurement was not found.",
            retryable=False,
            trace_id=trace_id,
            field_details={"measurement_id": str(measurement_id)},
        ) from error
