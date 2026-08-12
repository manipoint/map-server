"""Integration tests for HTTP access logging."""

import logging

from fastapi.testclient import TestClient

from app.main import app

ACCESS_LOGGER = "app.api.middleware.access_log"


def find_access_record(
    records: list[logging.LogRecord],
) -> logging.LogRecord:
    """Return the completed-request access record."""

    return next(
        record
        for record in records
        if (
            record.name == ACCESS_LOGGER
            and record.getMessage() == "HTTP request completed"
        )
    )


def test_successful_request_is_logged(caplog) -> None:
    """A successful request should record its outcome and duration."""

    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        with TestClient(app) as client:
            response = client.get("/health/live")

    record = find_access_record(caplog.records)

    assert response.status_code == 200
    assert record.http_method == "GET"
    assert record.http_path == "/health/live"
    assert record.status_code == 200
    assert record.duration_ms >= 0
    assert record.request_id == response.headers["X-Request-ID"]


def test_not_found_request_is_logged(caplog) -> None:
    """A missing route should still produce an access log."""

    with caplog.at_level(logging.INFO, logger=ACCESS_LOGGER):
        with TestClient(app) as client:
            response = client.get("/missing")

    record = find_access_record(caplog.records)

    assert response.status_code == 404
    assert record.http_method == "GET"
    assert record.http_path == "/missing"
    assert record.status_code == 404
    assert record.duration_ms >= 0
