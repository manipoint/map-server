"""Isolate test collection and execution from developer configuration."""

import os

import pytest

from app.config import Settings, get_settings

_settings_patch = pytest.MonkeyPatch()


def pytest_configure(config: pytest.Config) -> None:
    """Configure safe settings before test modules import app.main."""
    environment_names = {
        str(field.validation_alias or name).casefold()
        for name, field in Settings.model_fields.items()
    }
    for name in tuple(os.environ):
        if name.casefold() in environment_names:
            _settings_patch.delenv(name)

    _settings_patch.setitem(Settings.model_config, "env_file", None)
    for name, value in {
        "DATABASE_URL": "postgresql+asyncpg://test:test@localhost/travel_test",
        "JWT_SIGNING_KEY": "test-jwt-signing-key-0123456789abcdef",
        "REFRESH_TOKEN_HASH_KEY": "test-refresh-hash-key-0123456789abcdef",
    }.items():
        _settings_patch.setenv(name, value)
    get_settings.cache_clear()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Restore process configuration when pytest finishes."""
    get_settings.cache_clear()
    _settings_patch.undo()
