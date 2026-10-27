import pytest

from app import main
from app.config import Settings


@pytest.fixture(autouse=True)
def isolate_application_settings(monkeypatch):
    """Keep tests in explicit demo mode and prevent local .env secrets affecting tests."""
    monkeypatch.setattr(main, "settings", Settings(_env_file=None))
