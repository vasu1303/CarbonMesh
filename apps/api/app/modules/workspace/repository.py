from __future__ import annotations

from sqlalchemy import Select, String, and_, case, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.ai import AgentRun
from app.db.models.assurance import DisclosureDraft, Standard
from app.db.models.carbon import ActivityRecord, CarbonMeasurement
from app.db.models.core import (
    Actor,
    Company,
    DataSource,
    EvidenceItem,
    ReportingPeriod,
    Site,
    SourceDocument,
)
from app.db.models.dispatch import DispatchScenario, FlexibleLoad, GridForecast
from app.db.models.ledger import LedgerEvent
from app.db.models.procurement import ProcurementScenario, Supplier, SupplierProduct
from app.db.models.semantic import MethodDefinition, MetricDefinition, PolicyDefinition
from app.modules.workspace.schemas import WorkspaceOptionsQuery

UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _active(column):
    return case((column.is_(True), "active"), else_="inactive")


def _date(column):
    return func.to_char(func.timezone("UTC", column), 'YYYY-MM-DD HH24:MI:SS "UTC"')


def _clean_label(column):
    # Uploaded filenames and legacy titles may themselves contain UUIDs.
    return func.left(
        func.coalesce(
            func.nullif(func.btrim(func.regexp_replace(column, UUID_PATTERN, "", "gi")), ""),
            "Unnamed record",
        ),
        255,
    )


def _options(model, label, description=None, status=None, role=None):
    return select(
        model.id.label("id"),
        _clean_label(label).label("label"),
        func.left(description, 500).label("description")
        if description is not None
        else literal(None, String).label("description"),
        status.label("status") if status is not None else literal(None, String).label("status"),
        role.label("role") if role is not None else literal(None, String).label("role"),
    ).select_from(model)


def _join(statement, target, source, foreign_key):
    return statement.join(
        target,
        and_(
            target.company_id == source.company_id,
            target.id == foreign_key,
        ),
    )


def _statement(query: WorkspaceOptionsQuery) -> Select:
    kind = query.kind
    site = period = None
    shared_site = shared_period = False
    if kind == "actors":
        model = Actor
        statement = _options(
            model, model.display_name, status=_active(model.is_active), role=model.role
        )
    elif kind in {"metrics", "methods", "policies", "standards"}:
        model = {
            "metrics": MetricDefinition,
            "methods": MethodDefinition,
            "policies": PolicyDefinition,
            "standards": Standard,
        }[kind]
        description = model.method_type if kind == "methods" else model.description
        statement = _options(
            model,
            func.concat(model.name, " (", model.version, ")"),
            description,
            _active(model.is_active),
        )
    elif kind == "imports":
        model = DataSource
        statement = _options(
            model,
            func.concat(
                func.coalesce(model.configuration["filename"].astext, model.name),
                " - ",
                _date(model.created_at),
            ),
            model.configuration["source_name"].astext,
            func.coalesce(model.configuration["import_status"].astext, model.status),
        ).where(model.configuration["import_type"].astext.in_(["activity", "suppliers"]))
        site, period = model.site_id, model.configuration["reporting_period_id"].astext
        shared_site = shared_period = True
    elif kind in {"documents", "evidence"}:
        model = SourceDocument if kind == "documents" else EvidenceItem
        label = (
            SourceDocument.filename
            if kind == "documents"
            else func.concat(
                SourceDocument.filename,
                " - ",
                EvidenceItem.locator,
            )
        )
        description = (
            SourceDocument.content_type if kind == "documents" else EvidenceItem.evidence_type
        )
        statement = _options(model, label, description, DataSource.status)
        if kind == "evidence":
            statement = _join(statement, SourceDocument, model, model.source_document_id)
        statement = _join(statement, DataSource, SourceDocument, SourceDocument.data_source_id)
        site = DataSource.site_id
        period = func.coalesce(
            SourceDocument.document_metadata["reporting_period_id"].astext,
            DataSource.configuration["reporting_period_id"].astext,
        )
        shared_site = shared_period = True
    elif kind in {"activity", "measurements"}:
        model = ActivityRecord if kind == "activity" else CarbonMeasurement
        label = func.concat(
            MetricDefinition.name,
            " - ",
            Site.name,
            " - ",
            ReportingPeriod.name,
            " - ",
            _date(model.created_at),
        )
        if kind == "activity":
            label = func.concat(model.material_code, " - ", label)
        statement = _options(model, label, status=model.status)
        statement = _join(statement, MetricDefinition, model, model.metric_definition_id)
        statement = _join(statement, Site, model, model.site_id)
        statement = _join(statement, ReportingPeriod, model, model.reporting_period_id)
        site, period = model.site_id, model.reporting_period_id
    elif kind == "suppliers":
        model = Supplier
        statement = _options(model, model.name, model.country_code, model.status)
    elif kind == "products":
        model = SupplierProduct
        statement = _options(
            model,
            func.concat(model.name, " - ", Supplier.name),
            model.material_code,
            _active(model.is_active),
        )
        statement = _join(statement, Supplier, model, model.supplier_id)
    elif kind == "loads":
        model = FlexibleLoad
        statement = _options(model, model.name, model.description, _active(model.is_active))
        site = model.site_id
    elif kind == "forecasts":
        # Dispatch selects a complete immutable snapshot by source_document_id.
        model = SourceDocument
        snapshots = (
            select(
                GridForecast.company_id,
                GridForecast.site_id,
                GridForecast.source_document_id,
                GridForecast.zone,
                GridForecast.issued_at,
            )
            .distinct()
            .subquery()
        )
        statement = _options(
            model,
            func.concat(snapshots.c.zone, " forecast - ", _date(snapshots.c.issued_at)),
            model.filename,
        )
        statement = statement.join(
            snapshots,
            and_(
                snapshots.c.company_id == model.company_id,
                snapshots.c.source_document_id == model.id,
            ),
        ).distinct()
        site = snapshots.c.site_id
    elif kind == "assurance":
        model = DisclosureDraft
        statement = _options(
            model, func.concat(model.title, " (v", model.version, ")"), status=model.status
        )
        site, period = model.site_id, model.reporting_period_id
    elif kind == "procurement":
        model = ProcurementScenario
        statement = _options(
            model,
            func.concat(SupplierProduct.name, " - ", Site.name, " - ", _date(model.created_at)),
            ReportingPeriod.name,
            model.status,
        )
        statement = _join(statement, SupplierProduct, model, model.current_product_id)
        statement = _join(statement, Site, model, model.site_id)
        statement = _join(statement, ReportingPeriod, model, model.reporting_period_id)
        site, period = model.site_id, model.reporting_period_id
    elif kind == "dispatch":
        model = DispatchScenario
        statement = _options(
            model,
            func.concat(FlexibleLoad.name, " - ", _date(model.window_start)),
            model.objective,
            model.status,
        )
        statement = _join(statement, FlexibleLoad, model, model.flexible_load_id)
        site = model.site_id
    elif kind == "runs":
        model = AgentRun
        statement = _options(
            model,
            func.concat(model.workflow, " - ", _date(model.started_at)),
            model.stage,
            model.terminal_state,
        )
        site, period = (
            model.context_envelope["site_id"].astext,
            model.context_envelope["reporting_period_id"].astext,
        )
    elif kind == "ledger":
        model = LedgerEvent
        statement = _options(
            model, func.concat(model.event_type, " - ", _date(model.created_at)), model.entity_type
        )
        site, period = model.payload["site_id"].astext, model.payload["reporting_period_id"].astext
    else:
        raise ValueError("Unsupported workspace option kind")

    statement = statement.where(model.company_id == query.company_id)
    if query.id is not None:
        statement = statement.where(model.id == query.id)
    if query.role is not None:
        statement = statement.where(Actor.role == query.role)
    # Validate context ownership even for company-wide catalog kinds. Unknown or
    # foreign scopes have the same empty semantics, with no existence disclosure.
    statement = statement.where(select(Company.id).where(Company.id == query.company_id).exists())
    for identifier, scope_model, column, shared in (
        (query.site_id, Site, site, shared_site),
        (query.reporting_period_id, ReportingPeriod, period, shared_period),
    ):
        if identifier is None:
            continue
        statement = statement.where(
            select(scope_model.id)
            .where(
                scope_model.company_id == query.company_id,
                scope_model.id == identifier,
            )
            .correlate(None)
            .exists()
        )
        if column is not None:
            # JSON selectors stay text, so malformed legacy metadata never casts to UUID.
            value = str(identifier) if isinstance(column.type, String) else identifier
            match = column == value
            statement = statement.where(or_(match, column.is_(None)) if shared else match)
    return statement


async def list_options(session: AsyncSession, query: WorkspaceOptionsQuery):
    options = _statement(query).subquery()
    predicates = []
    if query.id is None and query.search and query.search.strip():
        term = query.search.strip()
        predicates.append(
            or_(
                options.c.label.icontains(term, autoescape=True),
                options.c.description.icontains(term, autoescape=True),
            )
        )
    total = await session.scalar(select(func.count()).select_from(options).where(*predicates))
    result = await session.execute(
        select(options)
        .where(*predicates)
        .order_by(func.lower(options.c.label), options.c.id)
        .limit(query.limit)
        .offset(0 if query.id is not None else query.offset)
    )
    return result.mappings().all(), int(total or 0)
