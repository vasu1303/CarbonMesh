from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.demo import cli


class AsyncContext:
    def __init__(self, value):
        self.value = value
        self.exit_error = None

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, error_type, error, traceback):
        self.exit_error = error
        return False


@pytest.mark.asyncio
async def test_seed_target_mismatch_opens_no_database():
    with (
        patch.object(cli, "get_database_target", return_value=SimpleNamespace(
            endpoint_id="other", database="carbonmesh",
        )),
        patch.object(cli, "get_engine") as engine,
        pytest.raises(ValueError, match="differs"),
    ):
        await cli.run(action="seed", endpoint="expected", database="carbonmesh")
    engine.assert_not_called()


@pytest.mark.asyncio
async def test_seed_nonempty_target_never_builds_or_writes_fixtures():
    session = MagicMock()
    session.execute = AsyncMock()
    session.scalar = AsyncMock(return_value=True)
    session.flush = AsyncMock()
    transaction = AsyncContext(session)
    session.begin.return_value = transaction
    engine = MagicMock()
    engine.connect.return_value = AsyncContext(MagicMock())
    metadata = SimpleNamespace(sorted_tables=[SimpleNamespace(schema="core", name="companies")])
    with (
        patch.object(cli, "get_database_target", return_value=SimpleNamespace(
            endpoint_id="expected", database="carbonmesh",
        )),
        patch.object(cli, "get_engine", return_value=engine),
        patch.object(cli, "load_model_registry", return_value=metadata),
        patch.object(cli, "verify_database_contract", new=AsyncMock()),
        patch.object(cli, "session_scope", return_value=AsyncContext(session)),
        patch.object(cli, "build_demo_records") as fixtures,
        pytest.raises(ValueError, match="contains data"),
    ):
        await cli.run(action="seed", endpoint="expected", database="carbonmesh")
    fixtures.assert_not_called()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    assert isinstance(transaction.exit_error, ValueError)
    assert "LOCK TABLE" in str(session.execute.await_args_list[1].args[0])
