"""Integration tests for application lifespan."""

import logging

from fastapi.testclient import TestClient

import app.main as main_module


def test_application_lifespan_logs_startup_and_shutdown(caplog) -> None:
    """Application lifespan should log startup and shutdown."""

    with caplog.at_level(logging.INFO, logger="app.lifespan"):
        with TestClient(main_module.app):
            pass

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.lifespan"
    ]
    assert "Application started" in messages
    assert "Application stopped" in messages
