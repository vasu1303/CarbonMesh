"""Non-table PostgreSQL objects owned by the code-first database bootstrap."""

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

APPLICATION_SCHEMAS: tuple[str, ...] = (
    "core",
    "carbon",
    "ledger",
    "semantic",
    "procurement",
)

VIEW_NAMES: frozenset[str] = frozenset(
    {
        "carbon.v_measurement_summary",
        "procurement.v_supplier_comparison",
        "procurement.v_pending_approvals",
    }
)

VIEW_STATEMENTS: tuple[str, ...] = (
    """
    CREATE OR REPLACE VIEW carbon.v_measurement_summary AS
    SELECT measurements.*
    FROM carbon.carbon_measurements AS measurements
    """,
    """
    CREATE OR REPLACE VIEW procurement.v_supplier_comparison AS
    SELECT scores.*
    FROM procurement.supplier_scores AS scores
    """,
    """
    CREATE OR REPLACE VIEW procurement.v_pending_approvals AS
    SELECT approvals.*
    FROM procurement.approvals AS approvals
    WHERE approvals.status = 'pending'
    """,
)

LEDGER_TRIGGER_STATEMENTS: tuple[str, ...] = (
    """
    CREATE OR REPLACE FUNCTION ledger.prevent_ledger_event_mutation()
    RETURNS trigger
    LANGUAGE plpgsql
    AS $function$
    BEGIN
        RAISE EXCEPTION 'ledger events are append-only'
            USING ERRCODE = '55000';
    END;
    $function$
    """,
    "DROP TRIGGER IF EXISTS trg_ledger_events_immutable ON ledger.ledger_events",
    """
    CREATE TRIGGER trg_ledger_events_immutable
    BEFORE UPDATE OR DELETE ON ledger.ledger_events
    FOR EACH ROW
    EXECUTE FUNCTION ledger.prevent_ledger_event_mutation()
    """,
)


async def execute_ddl_statements(
    connection: AsyncConnection,
    statements: Sequence[str],
) -> None:
    """Execute trusted, source-controlled DDL statements in the current transaction."""
    for statement in statements:
        await connection.execute(text(statement))


async def install_database_objects(connection: AsyncConnection) -> None:
    """Install or refresh CarbonMesh views and the immutable-ledger trigger."""
    await execute_ddl_statements(connection, VIEW_STATEMENTS)
    await execute_ddl_statements(connection, LEDGER_TRIGGER_STATEMENTS)
