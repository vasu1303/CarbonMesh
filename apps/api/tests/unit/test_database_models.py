from sqlalchemy import DateTime
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from sqlalchemy.sql.sqltypes import Float

import app.db.models  # noqa: F401  # Register every mapped table with Base.metadata.
from app.db.base import Base

EXPECTED_TABLES = {
    "core": {
        "companies",
        "sites",
        "reporting_periods",
        "actors",
        "data_sources",
        "source_documents",
        "evidence_items",
        "audit_log",
    },
    "carbon": {
        "raw_activity_records",
        "activity_records",
        "emission_factors",
        "calculation_runs",
        "emission_calculations",
        "carbon_measurements",
        "data_quality_issues",
        "carbon_baselines",
        "variance_alerts",
        "agent_runs",
    },
    "ledger": {"ledger_events", "lineage_edges", "ledger_event_evidence"},
    "semantic": {
        "semantic_entities",
        "semantic_aliases",
        "metric_definitions",
        "method_definitions",
    },
    "procurement": {
        "suppliers",
        "supplier_products",
        "procurement_scenarios",
        "supplier_scores",
        "recommendations",
        "fact_bindings",
        "approvals",
    },
}


def test_metadata_contains_exact_authoritative_table_catalogue() -> None:
    actual = {
        schema: {table.name for table in Base.metadata.tables.values() if table.schema == schema}
        for schema in EXPECTED_TABLES
    }

    assert actual == EXPECTED_TABLES
    assert len(Base.metadata.tables) == 32


def test_evidence_embedding_is_768_dimension_vector_with_cosine_hnsw() -> None:
    evidence = Base.metadata.tables["core.evidence_items"]
    embedding_sql = str(evidence.c.embedding.type.compile(dialect=postgresql.dialect()))

    assert embedding_sql.upper() == "VECTOR(768)"

    index = next(
        item
        for item in evidence.indexes
        if item.name == "ix_core_evidence_items_embedding_cosine_hnsw"
    )
    options = index.dialect_options["postgresql"]

    assert options["using"] == "hnsw"
    assert options["ops"] == {"embedding": "vector_cosine_ops"}
    assert options["with"] == {"m": 16, "ef_construction": 64}
    assert options["where"] is not None


def test_ledger_event_evidence_uses_composite_primary_key() -> None:
    link = Base.metadata.tables["ledger.ledger_event_evidence"]

    assert {column.name for column in link.primary_key.columns} == {
        "ledger_event_id",
        "evidence_item_id",
    }


def test_all_foreign_keys_resolve_inside_registered_metadata() -> None:
    registered = set(Base.metadata.tables)

    for table in Base.metadata.tables.values():
        for foreign_key in table.foreign_keys:
            assert foreign_key.column.table.fullname in registered


def test_tenant_rows_and_foreign_keys_enforce_company_scope() -> None:
    for table in Base.metadata.tables.values():
        if table.fullname != "core.companies":
            assert "company_id" in table.c, table.fullname

        for constraint in table.foreign_key_constraints:
            assert constraint.ondelete == "RESTRICT", constraint.name
            referred_table = next(iter(constraint.elements)).column.table.fullname
            if referred_table != "core.companies":
                assert "company_id" in constraint.column_keys, constraint.name
                assert "company_id" in {
                    element.column.name for element in constraint.elements
                }, constraint.name


def test_exact_numeric_fields_do_not_use_floating_point_columns() -> None:
    floating_columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float)
    ]

    assert floating_columns == []


def test_all_timestamp_columns_are_timezone_aware() -> None:
    timestamp_columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]

    assert timestamp_columns
    assert all(column.type.timezone for column in timestamp_columns)


def test_entity_uuid_primary_keys_are_generated_by_postgresql() -> None:
    for table in Base.metadata.tables.values():
        if table.fullname == "ledger.ledger_event_evidence":
            continue

        identifier = table.c.id
        assert identifier.primary_key
        assert identifier.server_default is not None
        assert "gen_random_uuid()" in str(identifier.server_default.arg)


def test_all_postgresql_table_and_index_ddl_compiles() -> None:
    dialect = postgresql.dialect()

    for table in Base.metadata.tables.values():
        assert str(CreateTable(table).compile(dialect=dialect))
        for index in table.indexes:
            assert str(CreateIndex(index).compile(dialect=dialect))


def test_procurement_scoring_contract_excludes_cost_from_weighted_score() -> None:
    scenario = Base.metadata.tables["procurement.procurement_scenarios"]
    score = Base.metadata.tables["procurement.supplier_scores"]

    expected_defaults = {
        "carbon_weight": "0.4000",
        "evidence_weight": "0.2500",
        "circularity_weight": "0.2000",
        "operational_fit_weight": "0.1500",
    }
    assert {
        name: str(scenario.c[name].server_default.arg) for name in expected_defaults
    } == expected_defaults
    assert "max_cost_increase_pct" in scenario.c
    assert {
        "carbon_score",
        "evidence_score",
        "circularity_score",
        "operational_fit_score",
        "total_score",
    }.issubset(score.c.keys())
    assert "cost_score" not in score.c


def test_partial_unique_recommendation_and_approval_indexes() -> None:
    expected = {
        "procurement.recommendations": "uq_proc_recommendations_active_scenario",
        "procurement.approvals": "uq_proc_approvals_pending_recommendation",
    }

    for table_name, index_name in expected.items():
        table = Base.metadata.tables[table_name]
        index = next(item for item in table.indexes if item.name == index_name)
        options = index.dialect_options["postgresql"]

        assert index.unique
        assert options["where"] is not None


def test_approval_foreign_key_binds_reviewed_hash_and_analysis_signature() -> None:
    approval = Base.metadata.tables["procurement.approvals"]
    binding = next(
        constraint
        for constraint in approval.foreign_key_constraints
        if constraint.name == "fk_proc_approvals_recommendation_preview"
    )

    assert tuple(binding.column_keys) == (
        "company_id",
        "recommendation_id",
        "preview_hash",
        "analysis_signature",
    )
    assert tuple(element.column.name for element in binding.elements) == (
        "company_id",
        "id",
        "payload_hash",
        "analysis_signature",
    )


def test_procurement_cost_constraints_are_safe_for_percentage_calculation() -> None:
    scenario = Base.metadata.tables["procurement.procurement_scenarios"]
    checks = {str(constraint.sqltext) for constraint in scenario.constraints if hasattr(constraint, "sqltext")}

    assert "current_unit_cost > 0" in checks
    assert "max_cost_increase_pct BETWEEN 0 AND 100" in checks
