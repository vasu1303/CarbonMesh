from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

import app.db.bootstrap as bootstrap_module
from app.db.bootstrap import (
    ACTIVE_RECOMMENDATION_INDEX,
    EXPECTED_TABLE_DISTRIBUTION,
    PENDING_APPROVAL_INDEX,
    DatabaseBootstrapError,
    _check_definition_is_compatible,
    _expected_constraint_contracts,
    _expected_index_contracts,
    _expected_uuid_default_columns,
    _normalize_postgresql_type,
    _validate_constraint_rows,
    _validate_index_rows,
    _validate_uuid_default_rows,
    _validate_view_rows,
    _verify_columns,
    _verify_ledger_trigger,
    _verify_partial_unique_indexes,
    bootstrap_database,
    load_model_registry,
)
from app.db.ddl import LEDGER_TRIGGER_STATEMENTS, VIEW_NAMES, VIEW_STATEMENTS


class FakeResult:
    def __init__(self, rows: Iterable[tuple[Any, ...]]) -> None:
        self.rows = list(rows)

    def tuples(self) -> Iterable[tuple[Any, ...]]:
        return self.rows

    def __iter__(self) -> Iterator[tuple[Any, ...]]:
        return iter(self.rows)

    def scalar_one_or_none(self) -> Any | None:
        if not self.rows:
            return None
        row = self.rows[0]
        return row[0] if isinstance(row, tuple) else row

    def one_or_none(self) -> tuple[Any, ...] | None:
        return self.rows[0] if self.rows else None


@dataclass
class FakeConnection:
    rows: list[tuple[Any, ...]]
    dialect = postgresql.dialect()

    async def execute(self, *_args: object, **_kwargs: object) -> FakeResult:
        return FakeResult(self.rows)


@dataclass
class SequencedFakeConnection:
    responses: list[list[tuple[Any, ...]]]
    dialect = postgresql.dialect()

    async def execute(self, *_args: object, **_kwargs: object) -> FakeResult:
        return FakeResult(self.responses.pop(0))


def test_bootstrap_registry_matches_authoritative_distribution() -> None:
    metadata = load_model_registry()
    distribution = {
        schema: sum(1 for table in metadata.tables.values() if table.schema == schema)
        for schema in EXPECTED_TABLE_DISTRIBUTION
    }

    assert distribution == EXPECTED_TABLE_DISTRIBUTION
    assert len(metadata.tables) == 46


def test_non_table_ddl_declares_views_and_immutable_ledger_trigger() -> None:
    views = "\n".join(VIEW_STATEMENTS).lower()
    trigger = "\n".join(LEDGER_TRIGGER_STATEMENTS).lower()

    assert VIEW_NAMES == {
        "carbon.v_measurement_summary",
        "procurement.v_supplier_comparison",
        "procurement.v_pending_approvals",
    }
    assert all(view in views for view in VIEW_NAMES)
    assert "before update or delete" in trigger
    assert "prevent_ledger_event_mutation" in trigger
    assert "55000" in trigger


@pytest.mark.parametrize(
    ("catalogue_type", "compiled_type"),
    [
        ("character varying(100)", "VARCHAR(100)"),
        ("numeric(24,6)", "NUMERIC(24, 6)"),
        ("timestamp with time zone", "TIMESTAMP   WITH TIME ZONE"),
        ("vector(768)", "VECTOR(768)"),
    ],
)
def test_postgresql_type_normalization(
    catalogue_type: str,
    compiled_type: str,
) -> None:
    assert _normalize_postgresql_type(catalogue_type) == _normalize_postgresql_type(
        compiled_type
    )


def _catalogue_column_rows() -> list[tuple[Any, ...]]:
    metadata = load_model_registry()
    dialect = postgresql.dialect()
    rows: list[tuple[Any, ...]] = []
    for table in metadata.tables.values():
        for column in table.columns:
            compiled = column.type.compile(dialect=dialect)
            catalogue_type = compiled.lower().replace("varchar", "character varying")
            rows.append(
                (table.schema, table.name, column.name, catalogue_type, column.nullable)
            )
    return rows


@pytest.mark.asyncio
async def test_live_column_verifier_checks_types_and_nullability() -> None:
    metadata = load_model_registry()
    connection = FakeConnection(_catalogue_column_rows())

    await _verify_columns(connection, metadata)  # type: ignore[arg-type]

    connection.rows[0] = (*connection.rows[0][:3], "text", connection.rows[0][4])
    with pytest.raises(DatabaseBootstrapError, match="columns"):
        await _verify_columns(connection, metadata)  # type: ignore[arg-type]


def _partial_index_rows(*, recommendation_unique: bool = True) -> list[tuple[Any, ...]]:
    return [
        (
            "procurement",
            "procurement_recommendations",
            ACTIVE_RECOMMENDATION_INDEX,
            recommendation_unique,
            "invalidated_at IS NULL AND status IN ('pending_approval', 'approved')",
            (
                "CREATE UNIQUE INDEX x ON procurement.procurement_recommendations "
                "(company_id, scenario_id) WHERE invalidated_at IS NULL"
            ),
        ),
        (
            "core",
            "approvals",
            PENDING_APPROVAL_INDEX,
            True,
            "status = 'pending' AND target_id IS NOT NULL",
            (
                "CREATE UNIQUE INDEX x ON core.approvals "
                "(company_id, target_type, target_id) WHERE status = 'pending' "
                "AND target_id IS NOT NULL"
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_partial_unique_index_verifier_checks_predicates_and_uniqueness() -> None:
    await _verify_partial_unique_indexes(  # type: ignore[arg-type]
        FakeConnection(_partial_index_rows())
    )

    with pytest.raises(DatabaseBootstrapError, match="active-recommendation"):
        await _verify_partial_unique_indexes(  # type: ignore[arg-type]
            FakeConnection(_partial_index_rows(recommendation_unique=False))
        )


def test_check_comparison_tolerates_postgresql_rewrites() -> None:
    assert _check_definition_is_compatible(
        "status IN ('open', 'closed')",
        (
            "CHECK (((status)::text = ANY "
            "((ARRAY['open'::character varying, 'closed'::character varying])::text[])))"
        ),
    )
    assert _check_definition_is_compatible(
        "confidence BETWEEN 0 AND 1",
        "CHECK (((confidence >= (0)::numeric) AND (confidence <= (1)::numeric)))",
    )
    assert _check_definition_is_compatible(
        "longitude IS NULL OR longitude BETWEEN -180 AND 180",
        (
            "CHECK (longitude IS NULL OR "
            "longitude >= '-180'::integer::numeric AND longitude <= 180::numeric)"
        ),
    )
    assert not _check_definition_is_compatible(
        "confidence BETWEEN 0 AND 1",
        "CHECK (((confidence >= (0)::numeric) AND (confidence <= (2)::numeric)))",
    )


@pytest.mark.asyncio
async def test_check_only_path_never_opens_bootstrap_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = object()
    verified: list[object] = []

    class ReadOnlyEngine:
        @asynccontextmanager
        async def connect(self) -> Iterable[object]:
            yield connection

        def begin(self) -> None:
            raise AssertionError("--check must not enter a DDL transaction")

    async def fake_verify(actual_connection: object, _metadata: object) -> None:
        verified.append(actual_connection)

    monkeypatch.setattr(bootstrap_module, "get_engine", lambda: ReadOnlyEngine())
    monkeypatch.setattr(bootstrap_module, "verify_database_contract", fake_verify)

    await bootstrap_database(check_only=True)

    assert verified == [connection]


def _constraint_rows() -> list[tuple[Any, ...]]:
    metadata = load_model_registry()
    rows: list[tuple[Any, ...]] = []
    for (schema, table, name), contract in _expected_constraint_contracts(
        metadata
    ).items():
        definition = (
            f"CHECK ({contract.check_expression})" if contract.kind == "c" else ""
        )
        rows.append(
            (
                schema,
                table,
                name,
                contract.kind,
                list(contract.columns),
                contract.referenced_schema,
                contract.referenced_table,
                list(contract.referenced_columns),
                contract.delete_action,
                definition,
            )
        )
    return rows


def test_constraint_contract_checks_structure_and_check_semantics() -> None:
    metadata = load_model_registry()
    rows = _constraint_rows()
    _validate_constraint_rows(rows, metadata)

    foreign_key_position = next(
        position for position, row in enumerate(rows) if row[3] == "f"
    )
    incompatible_foreign_key = list(rows[foreign_key_position])
    incompatible_foreign_key[8] = "c"
    rows[foreign_key_position] = tuple(incompatible_foreign_key)
    with pytest.raises(DatabaseBootstrapError, match="Foreign key"):
        _validate_constraint_rows(rows, metadata)

    rows = _constraint_rows()
    check_position = next(
        position
        for position, row in enumerate(rows)
        if row[3] == "c" and "'pending'" in str(row[9])
    )
    incompatible_check = list(rows[check_position])
    incompatible_check[9] = str(incompatible_check[9]).replace(
        "'pending'", "'unexpected'", 1
    )
    rows[check_position] = tuple(incompatible_check)
    with pytest.raises(DatabaseBootstrapError, match="Check constraint"):
        _validate_constraint_rows(rows, metadata)


def test_constraint_contract_accepts_asyncpg_catalogue_char_bytes() -> None:
    metadata = load_model_registry()
    rows = [
        (*row[:3], str(row[3]).encode("ascii"), *row[4:8], str(row[8]).encode("ascii"), row[9])
        for row in _constraint_rows()
    ]

    _validate_constraint_rows(rows, metadata)


def _index_rows() -> list[tuple[Any, ...]]:
    metadata = load_model_registry()
    return [
        (
            schema,
            table,
            name,
            contract.unique,
            contract.access_method,
            list(contract.columns),
            "predicate" if contract.has_predicate else None,
        )
        for (schema, table, name), contract in _expected_index_contracts(
            metadata
        ).items()
    ]


def test_explicit_index_contract_checks_method_columns_and_uniqueness() -> None:
    metadata = load_model_registry()
    rows = _index_rows()
    _validate_index_rows(rows, metadata)

    ordinary_position = next(
        position
        for position, row in enumerate(rows)
        if row[4] == "btree" and row[6] is None
    )
    incompatible_index = list(rows[ordinary_position])
    incompatible_index[3] = not incompatible_index[3]
    rows[ordinary_position] = tuple(incompatible_index)
    with pytest.raises(DatabaseBootstrapError, match="Index"):
        _validate_index_rows(rows, metadata)


def test_uuid_id_defaults_are_server_generated() -> None:
    metadata = load_model_registry()
    rows = [
        (*key, "public.gen_random_uuid()")
        for key in _expected_uuid_default_columns(metadata)
    ]
    _validate_uuid_default_rows(rows, metadata)

    rows[0] = (*rows[0][:3], "uuid_generate_v4()")
    with pytest.raises(DatabaseBootstrapError, match="gen_random_uuid"):
        _validate_uuid_default_rows(rows, metadata)


def _view_rows() -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    metadata = load_model_registry()
    view_sources = {
        "carbon.v_measurement_summary": (
            "carbon.carbon_measurements",
            "measurements",
            None,
        ),
        "procurement.v_supplier_comparison": (
            "procurement.supplier_scores",
            "scores",
            None,
        ),
        "procurement.v_pending_approvals": (
            "core.approvals",
            "approvals",
            "(approvals.status)::text = 'pending'::text",
        ),
    }
    definitions: list[tuple[Any, ...]] = []
    columns: list[tuple[Any, ...]] = []
    for view_name, (source, alias, predicate) in view_sources.items():
        schema, view = view_name.split(".")
        source_columns = tuple(column.name for column in metadata.tables[source].columns)
        definition = (
            "SELECT "
            + ", ".join(source_columns)
            + f" FROM {source} AS {alias}"
        )
        if predicate is not None:
            definition += f" WHERE {predicate}"
        definitions.append((schema, view, definition))
        columns.extend(
            (schema, view, column, position)
            for position, column in enumerate(source_columns, start=1)
        )
    return definitions, columns


def test_view_contract_checks_expanded_columns_and_filter() -> None:
    metadata = load_model_registry()
    definitions, columns = _view_rows()
    _validate_view_rows(definitions, columns, metadata)

    pending_position = next(
        position
        for position, row in enumerate(definitions)
        if row[1] == "v_pending_approvals"
    )
    incompatible_definition = list(definitions[pending_position])
    incompatible_definition[2] = str(incompatible_definition[2]).replace(
        "'pending'", "'approved'"
    )
    definitions[pending_position] = tuple(incompatible_definition)
    with pytest.raises(DatabaseBootstrapError, match="definition"):
        _validate_view_rows(definitions, columns, metadata)


@pytest.mark.asyncio
async def test_ledger_trigger_must_be_enabled_and_row_level() -> None:
    function = "RAISE EXCEPTION 'immutable' USING ERRCODE = '55000'"
    trigger = (
        "CREATE TRIGGER trg_ledger_events_immutable BEFORE UPDATE OR DELETE "
        "ON ledger.ledger_events FOR EACH ROW EXECUTE FUNCTION "
        "ledger.prevent_ledger_event_mutation()"
    )
    await _verify_ledger_trigger(  # type: ignore[arg-type]
        SequencedFakeConnection([[(function,)], [(trigger, b"O", 27)]])
    )

    with pytest.raises(DatabaseBootstrapError, match="trigger contract"):
        await _verify_ledger_trigger(  # type: ignore[arg-type]
            SequencedFakeConnection([[(function,)], [(trigger, "D", 27)]])
        )
