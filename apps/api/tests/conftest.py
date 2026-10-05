"""Offline tests use deterministic embeddings and keep live provider calls disabled."""

import pytest


@pytest.fixture(autouse=True)
def offline_backend_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("ELECTRICITY_MAPS_LIVE_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("SOURCE_STORAGE_DIR", str(tmp_path / "sources"))
