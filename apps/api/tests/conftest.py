"""Offline tests explicitly choose demo identity and deterministic embeddings.

Production auth remains enabled and live grid calls default off. Security and provider
tests override these settings to exercise the actual boundaries.
"""

import pytest


@pytest.fixture(autouse=True)
def offline_backend_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    monkeypatch.setenv("ELECTRICITY_MAPS_LIVE_ENABLED", "false")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("TELEMETRY_ENABLED", "false")
    monkeypatch.setenv("SOURCE_STORAGE_DIR", str(tmp_path / "sources"))
