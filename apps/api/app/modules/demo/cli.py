"""Explicit seed/reset command with credential-free target confirmation."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from sqlalchemy import text

from app.db.bootstrap import load_model_registry, verify_database_contract
from app.db.session import dispose_engine, get_database_target, get_engine, session_scope
from app.modules.demo.fixtures import build_demo_records
from app.modules.demo.service import reset_and_seed_demo


async def run(*, action: str, endpoint: str, database: str) -> dict[str, object]:
    target = get_database_target()
    if (target.endpoint_id, target.database) != (endpoint, database):
        raise ValueError("Configured database differs from the explicitly confirmed target.")
    metadata = load_model_registry()
    async with get_engine().connect() as connection:
        await verify_database_contract(connection, metadata)
    if action == "reset":
        async with session_scope() as session:
            summary = await reset_and_seed_demo(session)
        return {"action": action, "synthetic": True, **asdict(summary)}

    # Seed never clears existing rows. Lock through commit so a concurrent writer
    # cannot invalidate the empty-target check between verification and inserts.
    async with session_scope() as session, session.begin():
        await session.execute(text("SET LOCAL lock_timeout = '5s'"))
        names = ", ".join(f'"{t.schema}"."{t.name}"' for t in metadata.sorted_tables)
        await session.execute(text(f"LOCK TABLE {names} IN SHARE ROW EXCLUSIVE MODE"))
        for table in metadata.sorted_tables:
            if await session.scalar(
                text(f'SELECT EXISTS (SELECT 1 FROM "{table.schema}"."{table.name}")')
            ):
                raise ValueError("Seed refused: target contains data. No rows were changed.")
        records = build_demo_records()
        for record in records:
            session.add(record)
            await session.flush()
    return {"action": action, "synthetic": True, "seeded_records": len(records)}


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "reset"))
    parser.add_argument("--endpoint", required=True, help="Exact host label, without -pooler")
    parser.add_argument("--database", required=True, help="Exact database name")
    args = parser.parse_args()
    try:
        result = await run(action=args.action, endpoint=args.endpoint, database=args.database)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - operator boundary must redact driver credentials
        print(f"Demo operation failed ({type(error).__name__}); values redacted.", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    print(json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
