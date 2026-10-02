"""Real local PostgreSQL regressions for bounded connection lifetime."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import execution_session_scope


@pytest.mark.asyncio
async def test_null_pool_execution_reuses_one_connection_across_commits(e2e_database) -> None:
    engine = create_async_engine(
        e2e_database.url, connect_args=e2e_database.connect_args, poolclass=NullPool
    )
    counts = {"connect": 0, "close": 0}

    @event.listens_for(engine.sync_engine, "connect")
    def connected(_connection, _record):
        counts["connect"] += 1

    @event.listens_for(engine.sync_engine, "close")
    def closed(_connection, _record):
        counts["close"] += 1

    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with execution_session_scope(factory) as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.execute(text("CREATE TEMP TABLE synthetic_checkpoint (value integer)"))
            await session.execute(text("INSERT INTO synthetic_checkpoint VALUES (1)"))
            await session.commit()
            await session.execute(text("INSERT INTO synthetic_checkpoint VALUES (2)"))
            await session.commit()
            assert await session.scalar(text("SELECT pg_backend_pid()")) == pid
            assert await session.scalar(text("SELECT count(*) FROM synthetic_checkpoint")) == 2
            assert counts == {"connect": 1, "close": 0}
        assert counts == {"connect": 1, "close": 1}
        # The factory was not rebound globally; the next segment is independent.
        assert factory.kw["bind"] is engine
        async with execution_session_scope(factory) as session:
            assert await session.scalar(text("SELECT to_regclass('pg_temp.synthetic_checkpoint')")) is None
        assert counts == {"connect": 2, "close": 2}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_execution_timeout_during_database_io_closes_connection(e2e_database) -> None:
    engine = create_async_engine(
        e2e_database.url, connect_args=e2e_database.connect_args, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    retained_connection = None
    try:
        with pytest.raises(TimeoutError):
            async with execution_session_scope(factory) as session:
                retained_connection = session.bind
                async with asyncio.timeout(0.05):
                    await session.execute(text("SELECT pg_sleep(1)"))
        assert retained_connection is not None and retained_connection.closed
        async with execution_session_scope(factory) as session:
            assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_execution_scope_preserves_caller_owned_connection(e2e_database) -> None:
    engine = create_async_engine(
        e2e_database.url, connect_args=e2e_database.connect_args, poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            factory = async_sessionmaker(connection, expire_on_commit=False)
            async with execution_session_scope(factory) as session:
                assert session.bind is connection
                assert await session.scalar(text("SELECT 1")) == 1
                await session.commit()
            assert connection.closed is False
            assert await connection.scalar(text("SELECT 2")) == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [ValueError, asyncio.CancelledError])
async def test_execution_failure_rolls_back_only_uncommitted_work(e2e_database, failure) -> None:
    engine = create_async_engine(
        e2e_database.url, connect_args=e2e_database.connect_args, poolclass=NullPool
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    table = f"synthetic_execution_{uuid4().hex}"
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE TABLE "{table}" (value integer)'))
        retained_connection = None
        with pytest.raises(failure):
            async with execution_session_scope(factory) as session:
                retained_connection = session.bind
                await session.execute(text(f'INSERT INTO "{table}" VALUES (1)'))
                await session.commit()
                await session.execute(text(f'INSERT INTO "{table}" VALUES (2)'))
                raise failure("synthetic execution interruption")
        assert retained_connection is not None and retained_connection.closed
        async with engine.connect() as connection:
            assert list(await connection.scalars(text(f'SELECT value FROM "{table}"'))) == [1]
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
        await engine.dispose()
