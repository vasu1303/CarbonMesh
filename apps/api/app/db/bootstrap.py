"""Explicit, non-destructive code-first bootstrap for CarbonMesh PostgreSQL."""

import argparse
import asyncio
import importlib
import re
import sys
from collections import Counter
from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    MetaData,
    PrimaryKeyConstraint,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.base import Base
from app.db.ddl import APPLICATION_SCHEMAS, VIEW_NAMES, install_database_objects
from app.db.session import (
    DatabaseConfigurationError,
    dispose_engine,
    get_database_target,
    get_engine,
)

EXPECTED_TABLE_DISTRIBUTION: dict[str, int] = {
    "core": 9,
    "semantic": 5,
    "ai": 2,
    "carbon": 10,
    "ledger": 4,
    "assurance": 6,
    "procurement": 5,
    "dispatch": 5,
}
EXPECTED_TABLE_COUNT = 46
EXPECTED_VECTOR_TYPE = "vector(768)"
EXPECTED_VECTOR_INDEX = "ix_core_evidence_items_embedding_cosine_hnsw"
ACTIVE_RECOMMENDATION_INDEX = "uq_proc_recommendations_active_scenario"
PENDING_APPROVAL_INDEX = "uq_core_approvals_pending_target"
BOOTSTRAP_LOCK_KEY = "carbonmesh.database.bootstrap"
MODEL_MODULES = (
    "app.db.models.core",
    "app.db.models.semantic",
    "app.db.models.ai",
    "app.db.models.carbon",
    "app.db.models.ledger",
    "app.db.models.assurance",
    "app.db.models.procurement",
    "app.db.models.dispatch",
)
_DELETE_ACTION_CODES = {
    "NO ACTION": "a",
    "RESTRICT": "r",
    "CASCADE": "c",
    "SET NULL": "n",
    "SET DEFAULT": "d",
}
_VIEW_CONTRACTS = {
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
        "status = 'pending'",
    ),
}


@dataclass(frozen=True)
class _ConstraintContract:
    kind: str
    columns: tuple[str, ...]
    referenced_schema: str | None = None
    referenced_table: str | None = None
    referenced_columns: tuple[str, ...] = ()
    delete_action: str | None = None
    check_expression: str | None = None


@dataclass(frozen=True)
class _IndexContract:
    unique: bool
    access_method: str
    columns: tuple[str, ...]
    has_predicate: bool


class DatabaseBootstrapError(RuntimeError):
    """A safe, actionable bootstrap contract failure."""


def load_model_registry() -> MetaData:
    """Import every ORM module and validate the in-code table catalogue."""
    for module_name in MODEL_MODULES:
        importlib.import_module(module_name)
    metadata = Base.metadata

    actual_distribution = {schema: 0 for schema in APPLICATION_SCHEMAS}
    unexpected_tables: list[str] = []
    for table in metadata.tables.values():
        if table.schema in actual_distribution:
            actual_distribution[table.schema] += 1
        else:
            unexpected_tables.append(table.fullname)

    if len(metadata.tables) != EXPECTED_TABLE_COUNT:
        raise DatabaseBootstrapError(
            f"ORM registry must contain exactly {EXPECTED_TABLE_COUNT} tables; "
            f"found {len(metadata.tables)}."
        )
    if actual_distribution != EXPECTED_TABLE_DISTRIBUTION or unexpected_tables:
        raise DatabaseBootstrapError(
            "ORM registry does not match the required eight-schema table distribution."
        )
    return metadata


def _qualified_names(rows: Collection[tuple[str, str]]) -> set[str]:
    return {f"{schema}.{name}" for schema, name in rows}


async def _fetch_schema_names(connection: AsyncConnection) -> set[str]:
    result = await connection.execute(
        text(
            """
            SELECT nspname
            FROM pg_namespace
            WHERE nspname IN
                ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                 'procurement', 'dispatch')
            """
        )
    )
    return set(result.scalars())


async def _fetch_table_names(connection: AsyncConnection) -> set[str]:
    result = await connection.execute(
        text(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema IN
                  ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                   'procurement', 'dispatch')
            """
        )
    )
    return _qualified_names(set(result))


async def _fetch_view_names(connection: AsyncConnection) -> set[str]:
    result = await connection.execute(
        text(
            """
            SELECT schemaname, viewname
            FROM pg_views
            WHERE schemaname IN
                ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                 'procurement', 'dispatch')
            """
        )
    )
    return _qualified_names(set(result))


async def _assert_pristine_or_complete(
    connection: AsyncConnection,
    metadata: MetaData,
) -> bool:
    """Return True for a pristine database; reject every partial/extra state."""
    schemas = await _fetch_schema_names(connection)
    tables = await _fetch_table_names(connection)
    views = await _fetch_view_names(connection)
    expected_tables = set(metadata.tables)
    expected_schemas = set(APPLICATION_SCHEMAS)

    if not schemas and not tables and not views:
        return True
    if schemas != expected_schemas or tables != expected_tables or views != set(VIEW_NAMES):
        raise DatabaseBootstrapError(
            "Existing CarbonMesh objects are partial or unexpected; use a clean disposable "
            "database branch or restore the complete compatible contract."
        )

    # Exact object names are present. Verify compatibility before any DDL is attempted.
    await verify_database_contract(connection, metadata)
    return False


async def _create_extension_and_schemas(connection: AsyncConnection) -> None:
    await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))

    extension = await connection.execute(
        text(
            """
            SELECT ns.nspname
            FROM pg_extension AS ext
            JOIN pg_namespace AS ns ON ns.oid = ext.extnamespace
            WHERE ext.extname = 'vector'
            """
        )
    )
    if extension.scalar_one_or_none() != "public":
        raise DatabaseBootstrapError("The vector extension must be installed in schema public.")

    hnsw = await connection.execute(text("SELECT 1 FROM pg_am WHERE amname = 'hnsw'"))
    if hnsw.scalar_one_or_none() != 1:
        raise DatabaseBootstrapError("The installed pgvector version does not support HNSW.")

    for schema in APPLICATION_SCHEMAS:
        await connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))


def _check_column_names(table: Table, expression: str) -> tuple[str, ...]:
    without_literals = re.sub(r"'(?:''|[^'])*'", " ", expression)
    identifiers = set(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", without_literals))
    return tuple(sorted(column.name for column in table.columns if column.name in identifiers))


def _expected_constraint_contracts(
    metadata: MetaData,
) -> dict[tuple[str, str, str], _ConstraintContract]:
    expected: dict[tuple[str, str, str], _ConstraintContract] = {}
    for table in metadata.tables.values():
        if table.schema is None:
            continue
        for constraint in table.constraints:
            if constraint.name is None:
                raise DatabaseBootstrapError(
                    f"Every constraint on {table.fullname} must have a stable name."
                )
            key = (table.schema, table.name, constraint.name)
            if isinstance(constraint, PrimaryKeyConstraint):
                contract = _ConstraintContract(
                    kind="p",
                    columns=tuple(column.name for column in constraint.columns),
                )
            elif isinstance(constraint, UniqueConstraint):
                contract = _ConstraintContract(
                    kind="u",
                    columns=tuple(column.name for column in constraint.columns),
                )
            elif isinstance(constraint, ForeignKeyConstraint):
                delete_action = (constraint.ondelete or "NO ACTION").upper()
                if delete_action != "RESTRICT":
                    raise DatabaseBootstrapError(
                        f"Foreign key {constraint.name} must use ON DELETE RESTRICT."
                    )
                target_table = constraint.elements[0].column.table
                contract = _ConstraintContract(
                    kind="f",
                    columns=tuple(element.parent.name for element in constraint.elements),
                    referenced_schema=target_table.schema,
                    referenced_table=target_table.name,
                    referenced_columns=tuple(
                        element.column.name for element in constraint.elements
                    ),
                    delete_action=_DELETE_ACTION_CODES[delete_action],
                )
            elif isinstance(constraint, CheckConstraint):
                expression = str(constraint.sqltext)
                contract = _ConstraintContract(
                    kind="c",
                    columns=_check_column_names(table, expression),
                    check_expression=expression,
                )
            else:
                raise DatabaseBootstrapError(
                    f"Unsupported named constraint {constraint.name} on {table.fullname}."
                )
            expected[key] = contract
    return expected


def _expected_index_contracts(
    metadata: MetaData,
) -> dict[tuple[str, str, str], _IndexContract]:
    expected: dict[tuple[str, str, str], _IndexContract] = {}
    for table in metadata.tables.values():
        if table.schema is None:
            continue
        for index in table.indexes:
            if index.name is None:
                raise DatabaseBootstrapError(
                    f"Every explicit index on {table.fullname} must have a stable name."
                )
            columns: list[str] = []
            for expression in index.expressions:
                name = getattr(expression, "name", None)
                if name is None or name not in table.c:
                    raise DatabaseBootstrapError(
                        f"Index {index.name} must use named table columns."
                    )
                columns.append(name)
            options = index.dialect_options["postgresql"]
            expected[(table.schema, table.name, index.name)] = _IndexContract(
                unique=bool(index.unique),
                access_method=str(options.get("using") or "btree").lower(),
                columns=tuple(columns),
                has_predicate=options.get("where") is not None,
            )
    return expected


def _normalize_postgresql_numeric_literals(expression: str) -> str:
    """Normalize negative numeric literals rewritten as cast string constants.

    PostgreSQL deparses a negative NUMERIC bound such as ``-180`` as
    ``'-180'::integer::numeric``.  It is still a numeric constant, so treating
    it as an application string makes an otherwise identical CHECK constraint
    look incompatible.
    """
    numeric_type = (
        r"(?:smallint|integer|bigint|numeric|decimal|real|double\s+precision)"
    )
    return re.sub(
        rf"'(?P<number>[-+]?\d+(?:\.\d+)?)'\s*::\s*{numeric_type}"
        rf"(?:\s*::\s*{numeric_type})*",
        lambda match: match.group("number"),
        expression,
        flags=re.IGNORECASE,
    )


def _sql_value_signature(expression: str) -> tuple[Counter[str], Counter[str], Counter[str]]:
    expression = _normalize_postgresql_numeric_literals(expression)
    strings = Counter(
        match.group(0)[1:-1].replace("''", "'")
        for match in re.finditer(r"'(?:''|[^'])*'", expression)
    )
    without_strings = re.sub(r"'(?:''|[^'])*'", " ", expression)
    numbers = Counter(
        re.findall(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?(?![A-Za-z0-9_])", without_strings)
    )
    booleans = Counter(
        value.lower()
        for value in re.findall(r"\b(?:true|false)\b", without_strings, flags=re.IGNORECASE)
    )
    return strings, numbers, booleans


def _bound_comparison_signature(expression: str) -> Counter[tuple[str, str, str]]:
    expression = _normalize_postgresql_numeric_literals(expression)
    without_strings = re.sub(r"'(?:''|[^'])*'", " ", expression.lower())
    signature: Counter[tuple[str, str, str]] = Counter()
    operand = r"(?:[-+]?\d+(?:\.\d+)?|[a-z_][a-z0-9_]*)"
    for match in re.finditer(
        rf"\b([a-z_][a-z0-9_]*)\b\s*(>=|<=|>|<)\s*\(*\s*({operand})",
        without_strings,
    ):
        signature[(match.group(1), match.group(2), match.group(3))] += 1
    for match in re.finditer(
        rf"\b([a-z_][a-z0-9_]*)\b\s+between\s+({operand})\s+and\s+({operand})",
        without_strings,
    ):
        signature[(match.group(1), ">=", match.group(2))] += 1
        signature[(match.group(1), "<=", match.group(3))] += 1
    return signature


def _check_definition_is_compatible(expected: str, actual: str) -> bool:
    actual_body = actual.strip()
    if not actual_body.upper().startswith("CHECK"):
        return False
    if _sql_value_signature(expected) != _sql_value_signature(actual_body):
        return False
    if _bound_comparison_signature(expected) != _bound_comparison_signature(actual_body):
        return False

    expected_lower = expected.lower()
    actual_lower = actual_body.lower()
    expected_without_literals = re.sub(r"'(?:''|[^'])*'", " ", expected_lower)
    actual_without_literals = re.sub(r"'(?:''|[^'])*'", " ", actual_lower)
    if " in " in f" {expected_lower} " and not (
        " any " in f" {actual_lower} " or " in " in f" {actual_lower} "
    ):
        return False
    if " between " in f" {expected_lower} " and not (
        " between " in f" {actual_lower} "
        or (">=" in actual_lower and "<=" in actual_lower)
    ):
        return False

    for marker in (" is null", " is not null", "~", ">=", "<="):
        if expected_without_literals.count(marker) > actual_without_literals.count(marker):
            return False

    for operator in (
        re.compile(r"(?<![<>=])>(?!=)"),
        re.compile(r"(?<![<>=])<(?!=)"),
    ):
        if len(operator.findall(expected_without_literals)) != len(
            operator.findall(actual_without_literals)
        ):
            return False

    # PostgreSQL rewrites IN to = ANY, so equality is only stable otherwise.
    if " in " not in f" {expected_lower} ":
        equality = re.compile(r"(?<![<>=!])=(?!=)")
        if len(equality.findall(expected_without_literals)) != len(
            equality.findall(actual_without_literals)
        ):
            return False
    return True


def _validate_constraint_rows(
    rows: Collection[tuple[object, ...]],
    metadata: MetaData,
) -> None:
    expected = _expected_constraint_contracts(metadata)
    actual = {(str(row[0]), str(row[1]), str(row[2])): row for row in rows}
    if set(actual) != set(expected):
        raise DatabaseBootstrapError(
            "The live named constraint catalogue does not match the ORM contract."
        )

    for key, contract in expected.items():
        row = actual[key]
        kind = _catalogue_char(row[3])
        columns = tuple(row[4] or ())
        referenced_schema = row[5]
        referenced_table = row[6]
        referenced_columns = tuple(row[7] or ())
        delete_action = _catalogue_char(row[8])
        definition = str(row[9] or "")
        if kind != contract.kind:
            raise DatabaseBootstrapError(f"Constraint {key[2]} has the wrong kind.")
        if contract.kind == "c":
            if set(columns) != set(contract.columns) or not _check_definition_is_compatible(
                contract.check_expression or "", definition
            ):
                raise DatabaseBootstrapError(
                    f"Check constraint {key[2]} is structurally incompatible."
                )
        elif columns != contract.columns:
            raise DatabaseBootstrapError(
                f"Constraint {key[2]} uses incompatible local columns."
            )

        if contract.kind == "f" and (
            referenced_schema != contract.referenced_schema
            or referenced_table != contract.referenced_table
            or referenced_columns != contract.referenced_columns
            or delete_action != contract.delete_action
        ):
            raise DatabaseBootstrapError(
                f"Foreign key {key[2]} has an incompatible target or delete action."
            )


def _catalogue_char(value: object) -> str:
    """Normalize PostgreSQL internal ``char`` values returned by DB drivers."""
    if isinstance(value, bytes):
        return value.decode("ascii")
    return str(value)


def _validate_index_rows(
    rows: Collection[tuple[object, ...]],
    metadata: MetaData,
) -> None:
    expected = _expected_index_contracts(metadata)
    actual = {(str(row[0]), str(row[1]), str(row[2])): row for row in rows}
    if set(actual) != set(expected):
        raise DatabaseBootstrapError(
            "The live explicit index catalogue does not match the ORM contract."
        )

    for key, contract in expected.items():
        row = actual[key]
        unique = bool(row[3])
        access_method = str(row[4]).lower()
        columns = tuple(row[5] or ())
        has_predicate = row[6] is not None
        if (
            unique != contract.unique
            or access_method != contract.access_method
            or columns != contract.columns
            or has_predicate != contract.has_predicate
        ):
            raise DatabaseBootstrapError(f"Index {key[2]} is structurally incompatible.")


async def _verify_constraints_and_indexes(
    connection: AsyncConnection,
    metadata: MetaData,
) -> None:
    constraints_result = await connection.execute(
        text(
            """
            SELECT ns.nspname,
                   rel.relname,
                   con.conname,
                   con.contype,
                   ARRAY(
                       SELECT attr.attname
                       FROM unnest(con.conkey) WITH ORDINALITY
                            AS key_column(attnum, ordinal_position)
                       JOIN pg_attribute AS attr
                         ON attr.attrelid = con.conrelid
                        AND attr.attnum = key_column.attnum
                       ORDER BY key_column.ordinal_position
                   ),
                   ref_ns.nspname,
                   ref_rel.relname,
                   ARRAY(
                       SELECT attr.attname
                       FROM unnest(con.confkey) WITH ORDINALITY
                            AS key_column(attnum, ordinal_position)
                       JOIN pg_attribute AS attr
                         ON attr.attrelid = con.confrelid
                        AND attr.attnum = key_column.attnum
                       ORDER BY key_column.ordinal_position
                   ),
                   con.confdeltype,
                   pg_get_constraintdef(con.oid, true)
            FROM pg_constraint AS con
            JOIN pg_class AS rel ON rel.oid = con.conrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            LEFT JOIN pg_class AS ref_rel ON ref_rel.oid = con.confrelid
            LEFT JOIN pg_namespace AS ref_ns ON ref_ns.oid = ref_rel.relnamespace
            WHERE ns.nspname IN
                ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                 'procurement', 'dispatch')
              AND con.contype IN ('p', 'u', 'f', 'c')
            """
        )
    )
    _validate_constraint_rows(list(constraints_result), metadata)

    indexes_result = await connection.execute(
        text(
            """
            SELECT ns.nspname,
                   rel.relname,
                   idx_rel.relname,
                   idx.indisunique,
                   am.amname,
                   ARRAY(
                       SELECT attr.attname
                       FROM generate_series(0, idx.indnkeyatts - 1)
                            AS key_position(position)
                       JOIN pg_attribute AS attr
                         ON attr.attrelid = idx.indrelid
                        AND attr.attnum = idx.indkey[key_position.position]
                       ORDER BY key_position.position
                   ),
                   pg_get_expr(idx.indpred, idx.indrelid)
            FROM pg_index AS idx
            JOIN pg_class AS rel ON rel.oid = idx.indrelid
            JOIN pg_class AS idx_rel ON idx_rel.oid = idx.indexrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            JOIN pg_am AS am ON am.oid = idx_rel.relam
            WHERE ns.nspname IN
                ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                 'procurement', 'dispatch')
              AND NOT EXISTS (
                  SELECT 1
                  FROM pg_constraint AS con
                  WHERE con.conindid = idx.indexrelid
              )
            """
        )
    )
    _validate_index_rows(list(indexes_result), metadata)


async def _verify_columns(connection: AsyncConnection, metadata: MetaData) -> None:
    columns_result = await connection.execute(
        text(
            """
            SELECT ns.nspname,
                   rel.relname,
                   attr.attname,
                   format_type(attr.atttypid, attr.atttypmod),
                   NOT attr.attnotnull
            FROM pg_attribute AS attr
            JOIN pg_class AS rel ON rel.oid = attr.attrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            WHERE rel.relkind IN ('r', 'p')
              AND attr.attnum > 0
              AND NOT attr.attisdropped
              AND ns.nspname IN
                  ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                   'procurement', 'dispatch')
            """
        )
    )
    actual_columns = {
        (schema, table, column): (_normalize_postgresql_type(type_name), nullable)
        for schema, table, column, type_name, nullable in columns_result
    }
    expected_columns = {
        (table.schema, table.name, column.name): (
            _normalize_postgresql_type(column.type.compile(dialect=connection.dialect)),
            column.nullable,
        )
        for table in metadata.tables.values()
        for column in table.columns
        if table.schema is not None
    }
    if actual_columns != expected_columns:
        raise DatabaseBootstrapError("One or more database columns are missing or incompatible.")


def _normalize_postgresql_type(type_name: str) -> str:
    """Normalize equivalent PostgreSQL catalog/compiler type spellings."""
    normalized = type_name.strip().lower().replace("character varying", "varchar")
    normalized = re.sub(r"\s*,\s*", ",", normalized)
    return re.sub(r"\s+", " ", normalized)


def _normalize_uuid_default(expression: str) -> str:
    return re.sub(r"\s+", "", expression.lower()).replace("public.", "")


def _expected_uuid_default_columns(metadata: MetaData) -> set[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for table in metadata.tables.values():
        if table.schema is None:
            continue
        for column in table.columns:
            if column.name != "id" or not column.primary_key:
                continue
            if not isinstance(column.type, PostgreSQLUUID):
                raise DatabaseBootstrapError(
                    f"Primary key {table.fullname}.id must use PostgreSQL UUID."
                )
            default = column.server_default
            rendered_default = "" if default is None else str(default.arg)
            if _normalize_uuid_default(rendered_default) != "gen_random_uuid()":
                raise DatabaseBootstrapError(
                    f"Primary key {table.fullname}.id must default to gen_random_uuid()."
                )
            expected.add((table.schema, table.name, column.name))
    return expected


def _validate_uuid_default_rows(
    rows: Collection[tuple[object, ...]], metadata: MetaData
) -> None:
    expected = _expected_uuid_default_columns(metadata)
    actual = {
        (str(row[0]), str(row[1]), str(row[2])): str(row[3] or "") for row in rows
    }
    if set(actual) != expected or any(
        _normalize_uuid_default(default) != "gen_random_uuid()"
        for default in actual.values()
    ):
        raise DatabaseBootstrapError(
            "Every UUID id primary key must use the gen_random_uuid() server default."
        )


async def _verify_uuid_defaults(connection: AsyncConnection, metadata: MetaData) -> None:
    result = await connection.execute(
        text(
            """
            SELECT ns.nspname,
                   rel.relname,
                   attr.attname,
                   pg_get_expr(def.adbin, def.adrelid)
            FROM pg_attribute AS attr
            JOIN pg_class AS rel ON rel.oid = attr.attrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            JOIN pg_type AS typ ON typ.oid = attr.atttypid
            LEFT JOIN pg_attrdef AS def
              ON def.adrelid = attr.attrelid
             AND def.adnum = attr.attnum
            WHERE rel.relkind IN ('r', 'p')
              AND attr.attnum > 0
              AND NOT attr.attisdropped
              AND attr.attname = 'id'
              AND typ.typname = 'uuid'
              AND ns.nspname IN
                  ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                   'procurement', 'dispatch')
            """
        )
    )
    _validate_uuid_default_rows(list(result), metadata)


def _normalize_view_definition(definition: str) -> str:
    return re.sub(r"\s+", "", definition.lower().replace('"', "")).rstrip(";")


def _validate_view_definition(
    definition: str,
    source_table: str,
    alias: str,
    columns: tuple[str, ...],
    predicate: str | None,
) -> bool:
    normalized = _normalize_view_definition(definition)
    if not normalized.startswith("select"):
        return False
    projection, separator, remainder = normalized[6:].partition("from")
    if not separator:
        return False
    projected_columns = tuple(
        expression.removeprefix(f"{alias}.") for expression in projection.split(",")
    )
    if projected_columns != columns:
        return False
    source_with_alias = f"from{source_table}{alias}"
    source_with_as_alias = f"from{source_table}as{alias}"
    source_clause = f"from{remainder}"
    if source_clause.startswith(source_with_as_alias):
        remainder = source_clause[len(source_with_as_alias) :]
    elif source_clause.startswith(source_with_alias):
        remainder = source_clause[len(source_with_alias) :]
    else:
        return False
    if predicate is None:
        return remainder == ""
    if not remainder.startswith("where"):
        return False
    actual_predicate = remainder[5:]
    expected_predicate_columns = {
        column
        for column in columns
        if re.search(rf"\b{re.escape(column)}\b", predicate, flags=re.IGNORECASE)
    }
    actual_without_literals = re.sub(r"'(?:''|[^'])*'", " ", actual_predicate)
    actual_predicate_columns = {
        column
        for column in columns
        if re.search(
            rf"\b(?:{re.escape(alias)}\.)?{re.escape(column)}\b",
            actual_without_literals,
            flags=re.IGNORECASE,
        )
    }
    return actual_predicate_columns == expected_predicate_columns and (
        _check_definition_is_compatible(predicate, f"CHECK ({actual_predicate})")
    )


def _validate_view_rows(
    definition_rows: Collection[tuple[object, ...]],
    column_rows: Collection[tuple[object, ...]],
    metadata: MetaData,
) -> None:
    definitions = {
        f"{row[0]}.{row[1]}": str(row[2] or "") for row in definition_rows
    }
    if set(definitions) != set(_VIEW_CONTRACTS):
        raise DatabaseBootstrapError("The live view catalogue is incomplete or unexpected.")

    actual_columns: dict[str, list[tuple[int, str]]] = {}
    for schema, view, column, position in column_rows:
        actual_columns.setdefault(f"{schema}.{view}", []).append(
            (int(position), str(column))
        )

    for view_name, (source_table, alias, predicate) in _VIEW_CONTRACTS.items():
        expected_columns = tuple(column.name for column in metadata.tables[source_table].columns)
        installed_columns = tuple(
            name for _, name in sorted(actual_columns.get(view_name, []))
        )
        if installed_columns != expected_columns:
            raise DatabaseBootstrapError(f"View {view_name} exposes incompatible columns.")
        if not _validate_view_definition(
            definitions[view_name], source_table, alias, expected_columns, predicate
        ):
            raise DatabaseBootstrapError(f"View {view_name} has an incompatible definition.")


async def _verify_views(connection: AsyncConnection, metadata: MetaData) -> None:
    definitions_result = await connection.execute(
        text(
            """
            SELECT ns.nspname, rel.relname, pg_get_viewdef(rel.oid, true)
            FROM pg_class AS rel
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            WHERE rel.relkind = 'v'
              AND ns.nspname IN
                  ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                   'procurement', 'dispatch')
            """
        )
    )
    columns_result = await connection.execute(
        text(
            """
            SELECT table_schema, table_name, column_name, ordinal_position
            FROM information_schema.columns
            WHERE table_schema IN
                ('core', 'semantic', 'ai', 'carbon', 'ledger', 'assurance',
                 'procurement', 'dispatch')
              AND (table_schema || '.' || table_name) IN
                  ('carbon.v_measurement_summary',
                   'procurement.v_supplier_comparison',
                   'procurement.v_pending_approvals')
            ORDER BY table_schema, table_name, ordinal_position
            """
        )
    )
    _validate_view_rows(
        list(definitions_result),
        list(columns_result),
        metadata,
    )


async def _verify_partial_unique_indexes(connection: AsyncConnection) -> None:
    result = await connection.execute(
        text(
            """
            SELECT ns.nspname,
                   tbl_rel.relname,
                   idx_rel.relname,
                   idx.indisunique,
                   pg_get_expr(idx.indpred, idx.indrelid),
                   pg_get_indexdef(idx.indexrelid)
            FROM pg_index AS idx
            JOIN pg_class AS idx_rel ON idx_rel.oid = idx.indexrelid
            JOIN pg_class AS tbl_rel ON tbl_rel.oid = idx.indrelid
            JOIN pg_namespace AS ns ON ns.oid = tbl_rel.relnamespace
            WHERE ns.nspname IN ('core', 'procurement')
              AND idx_rel.relname IN (:recommendation_index, :approval_index)
            """
        ),
        {
            "recommendation_index": ACTIVE_RECOMMENDATION_INDEX,
            "approval_index": PENDING_APPROVAL_INDEX,
        },
    )
    indexes = {row[2]: row for row in result}
    recommendation = indexes.get(ACTIVE_RECOMMENDATION_INDEX)
    approval = indexes.get(PENDING_APPROVAL_INDEX)
    if recommendation is None or approval is None:
        raise DatabaseBootstrapError("One or more partial unique indexes are missing.")

    rec_schema, rec_table, _, rec_unique, rec_predicate, rec_definition = recommendation
    rec_predicate_normalized = " ".join((rec_predicate or "").lower().split())
    rec_definition_normalized = "".join((rec_definition or "").lower().split())
    if (
        rec_schema != "procurement"
        or rec_table != "procurement_recommendations"
        or not rec_unique
        or "invalidated_at" not in rec_predicate_normalized
        or "is null" not in rec_predicate_normalized
        or "status" not in rec_predicate_normalized
        or "'pending_approval'" not in rec_predicate_normalized
        or "'approved'" not in rec_predicate_normalized
        or "(company_id,scenario_id)" not in rec_definition_normalized
    ):
        raise DatabaseBootstrapError("The active-recommendation unique index is incompatible.")

    approval_schema, approval_table, _, approval_unique, approval_predicate, approval_definition = (
        approval
    )
    approval_predicate_normalized = " ".join((approval_predicate or "").lower().split())
    approval_definition_normalized = "".join((approval_definition or "").lower().split())
    if (
        approval_schema != "core"
        or approval_table != "approvals"
        or not approval_unique
        or "status" not in approval_predicate_normalized
        or "'pending'" not in approval_predicate_normalized
        or "=" not in approval_predicate_normalized
        or "target_id" not in approval_predicate_normalized
        or "is not null" not in approval_predicate_normalized
        or "(company_id,target_type,target_id)" not in approval_definition_normalized
    ):
        raise DatabaseBootstrapError("The pending-approval unique index is incompatible.")


async def _verify_vector_contract(connection: AsyncConnection) -> None:
    vector_column = await connection.execute(
        text(
            """
            SELECT format_type(attr.atttypid, attr.atttypmod)
            FROM pg_attribute AS attr
            JOIN pg_class AS rel ON rel.oid = attr.attrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'core'
              AND rel.relname = 'evidence_items'
              AND attr.attname = 'embedding'
              AND NOT attr.attisdropped
            """
        )
    )
    actual_vector_type = vector_column.scalar_one_or_none()
    if actual_vector_type is None or actual_vector_type.lower() != EXPECTED_VECTOR_TYPE:
        raise DatabaseBootstrapError("core.evidence_items.embedding must be VECTOR(768).")

    index_result = await connection.execute(
        text(
            """
            SELECT am.amname,
                   opc.opcname,
                   pg_get_expr(idx.indpred, idx.indrelid),
                   idx_rel.reloptions
            FROM pg_index AS idx
            JOIN pg_class AS idx_rel ON idx_rel.oid = idx.indexrelid
            JOIN pg_class AS tbl_rel ON tbl_rel.oid = idx.indrelid
            JOIN pg_namespace AS ns ON ns.oid = tbl_rel.relnamespace
            JOIN pg_am AS am ON am.oid = idx_rel.relam
            JOIN pg_opclass AS opc ON opc.oid = idx.indclass[0]
            WHERE ns.nspname = 'core'
              AND tbl_rel.relname = 'evidence_items'
              AND idx_rel.relname = :index_name
            """
        ),
        {"index_name": EXPECTED_VECTOR_INDEX},
    )
    index_contract = index_result.one_or_none()
    if index_contract is None:
        raise DatabaseBootstrapError("The evidence embedding HNSW index is missing.")

    access_method, operator_class, predicate, options = index_contract
    normalized_predicate = "".join((predicate or "").lower().split())
    option_set = set(options or [])
    if (
        access_method != "hnsw"
        or operator_class != "vector_cosine_ops"
        or normalized_predicate not in {"(embeddingisnotnull)", "embeddingisnotnull"}
        or not {"m=16", "ef_construction=64"}.issubset(option_set)
    ):
        raise DatabaseBootstrapError("The evidence embedding index contract is incompatible.")


async def _verify_ledger_trigger(connection: AsyncConnection) -> None:
    function_result = await connection.execute(
        text(
            """
            SELECT pg_get_functiondef(proc.oid)
            FROM pg_proc AS proc
            JOIN pg_namespace AS ns ON ns.oid = proc.pronamespace
            WHERE ns.nspname = 'ledger'
              AND proc.proname = 'prevent_ledger_event_mutation'
              AND proc.pronargs = 0
            """
        )
    )
    trigger_result = await connection.execute(
        text(
            """
            SELECT pg_get_triggerdef(trg.oid), trg.tgenabled, trg.tgtype
            FROM pg_trigger AS trg
            JOIN pg_class AS rel ON rel.oid = trg.tgrelid
            JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
            WHERE ns.nspname = 'ledger'
              AND rel.relname = 'ledger_events'
              AND trg.tgname = 'trg_ledger_events_immutable'
              AND NOT trg.tgisinternal
            """
        )
    )
    function_definition = (function_result.scalar_one_or_none() or "").upper()
    trigger_contract = trigger_result.one_or_none()
    if trigger_contract is None:
        raise DatabaseBootstrapError("The immutable ledger trigger contract is incomplete.")
    trigger_definition = str(trigger_contract[0] or "").upper()
    enabled_state = _catalogue_char(trigger_contract[1] or "")
    trigger_type = int(trigger_contract[2])
    if (
        "55000" not in function_definition
        or "BEFORE" not in trigger_definition
        or "UPDATE" not in trigger_definition
        or "DELETE" not in trigger_definition
        or "FOR EACH ROW" not in trigger_definition
        or "PREVENT_LEDGER_EVENT_MUTATION" not in trigger_definition
        or enabled_state not in {"O", "A"}
        or trigger_type != 27
    ):
        raise DatabaseBootstrapError("The immutable ledger trigger contract is incomplete.")


async def verify_database_contract(connection: AsyncConnection, metadata: MetaData) -> None:
    """Verify the live database matches the complete CarbonMesh code-first contract."""
    schemas = await _fetch_schema_names(connection)
    tables = await _fetch_table_names(connection)
    views = await _fetch_view_names(connection)

    if schemas != set(APPLICATION_SCHEMAS):
        raise DatabaseBootstrapError("The eight required application schemas are not installed.")
    if tables != set(metadata.tables):
        raise DatabaseBootstrapError(
            f"Expected exactly {EXPECTED_TABLE_COUNT} CarbonMesh tables in the required schemas."
        )
    if views != set(VIEW_NAMES):
        raise DatabaseBootstrapError("The three required CarbonMesh views are not installed.")

    extension_result = await connection.execute(
        text(
            """
            SELECT ns.nspname
            FROM pg_extension AS ext
            JOIN pg_namespace AS ns ON ns.oid = ext.extnamespace
            WHERE ext.extname = 'vector'
            """
        )
    )
    if extension_result.scalar_one_or_none() != "public":
        raise DatabaseBootstrapError("The pgvector extension is not installed in schema public.")

    await _verify_columns(connection, metadata)
    await _verify_uuid_defaults(connection, metadata)
    await _verify_constraints_and_indexes(connection, metadata)
    await _verify_partial_unique_indexes(connection)
    await _verify_vector_contract(connection)
    await _verify_views(connection, metadata)
    await _verify_ledger_trigger(connection)


async def bootstrap_database(*, check_only: bool = False) -> None:
    """Create a pristine database or verify an existing complete database."""
    metadata = load_model_registry()
    engine = get_engine()

    if check_only:
        async with engine.connect() as connection:
            await verify_database_contract(connection, metadata)
        return

    async with engine.begin() as connection:
        await connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
            {"lock_key": BOOTSTRAP_LOCK_KEY},
        )
        await _assert_pristine_or_complete(connection, metadata)
        await _create_extension_and_schemas(connection)
        await connection.run_sync(metadata.create_all)
        await install_database_objects(connection)
        await verify_database_contract(connection, metadata)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the installed contract without creating or changing database objects",
    )
    return parser.parse_args(argv)


async def _run_cli(*, check_only: bool) -> int:
    try:
        target = get_database_target()
        await bootstrap_database(check_only=check_only)
    except (DatabaseBootstrapError, DatabaseConfigurationError) as error:
        print(f"Database contract error: {error}", file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError):
        print(
            "Database operation failed. Verify DATABASE_URL, Neon availability, and permissions.",
            file=sys.stderr,
        )
        return 1
    finally:
        await dispose_engine()

    action = "verified" if check_only else "bootstrapped"
    schema_summary = ", ".join(
        f"{schema}={count}" for schema, count in EXPECTED_TABLE_DISTRIBUTION.items()
    )
    print(
        f"CarbonMesh database contract {action}: 46 tables ({schema_summary}), "
        f"3 views, pgvector enabled. Target: endpoint={target.endpoint_id}, "
        f"database={target.database}."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the database bootstrap CLI."""
    args = _parse_args(argv)
    return asyncio.run(_run_cli(check_only=args.check))


if __name__ == "__main__":
    raise SystemExit(main())
