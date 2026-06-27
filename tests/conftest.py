"""Shared test fixtures for raphael-sync."""

import tempfile
from pathlib import Path

import pytest

from raphael_sync.store import SyncStore


@pytest.fixture(autouse=True)
def isolated_sync_store(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SyncStore(db_path=Path(tmp) / "sync.db")
        from raphael_sync import routes

        monkeypatch.setattr(routes, "_store", store)
        yield
