from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def versions_dir(tmp_path: Path) -> Path:
    path = tmp_path / "migrations" / "versions"
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own MongoMig env vars from leaking into tests."""
    for var in ("MONGOMIG_CONFIG", "MONGOMIG_ENV", "MONGODB_URI", "MONGODB_DATABASE"):
        monkeypatch.delenv(var, raising=False)
