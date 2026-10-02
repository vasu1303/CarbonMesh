import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, Request
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.core import observability


@pytest.fixture
def recorded_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(observability, "_provider", provider)
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_request_tool_provider_spans_share_context_and_exclude_secrets(recorded_spans):
    app = FastAPI()
    app.add_middleware(observability.RequestTelemetryMiddleware)

    @app.get("/api/example/{item_id}")
    async def example(item_id: str, request: Request):
        with observability.span("tool.calculate"), observability.span("provider.grid.http"):
            observability.record_external_call("electricity_maps")
        return {"correlation": request.headers["X-Trace-ID"]}

    observability.begin_external_usage()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/api/example/secret-path-value?token=secret-query", headers={
            "Authorization": "Bearer secret-authorization", "X-Trace-ID": "safe-correlation",
        })
    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == response.json()["correlation"] == "safe-correlation"
    spans = recorded_spans.get_finished_spans()
    assert {s.name for s in spans} == {
        "GET /api/example/{item_id}", "tool.calculate", "provider.grid.http",
    }
    assert len({s.context.trace_id for s in spans}) == 1
    rendered = json.dumps([dict(s.attributes) for s in spans])
    assert "secret" not in rendered
    assert observability.take_external_usage().api_calls == 1
    assert observability.take_external_usage().api_calls == 0


def test_database_spans_never_record_sql_values_or_exception_messages(recorded_spans):
    engine = create_engine("sqlite://")
    observability.instrument_database(SimpleNamespace(sync_engine=engine))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT 'confidential-value'")) == "confidential-value"
        with pytest.raises(OperationalError):
            connection.execute(text("SELECT secret_column FROM missing_private_table"))
    spans = recorded_spans.get_finished_spans()
    assert len(spans) == 2
    rendered = json.dumps([s.to_json() for s in spans])
    assert "confidential-value" not in rendered
    assert "secret_column" not in rendered
    assert "missing_private_table" not in rendered
    assert spans[-1].status.is_ok is False
    engine.dispose()
