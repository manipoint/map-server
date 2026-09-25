"""Structured metric events for Cloud Logging based metrics and alerts."""

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)


def record_metric(
    *,
    name: str,
    value: float,
    metric_type: str,
    labels: Mapping[str, str],
) -> None:
    """Emit one low-cardinality metric sample as a structured log entry."""

    if metric_type not in {"counter", "distribution"}:
        raise ValueError("metric_type must be counter or distribution")
    try:
        logger.info(
            "Application metric",
            extra={
                "event": "application_metric",
                "metric_name": name,
                "metric_type": metric_type,
                "metric_value": value,
                "metric_labels": dict(labels),
            },
        )
    except Exception:
        # Metrics are diagnostic and must never affect the user request.
        return
