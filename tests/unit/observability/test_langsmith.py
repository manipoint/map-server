"""Privacy and configuration tests for LangSmith tracing."""

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from langchain_core.tracers import LangChainTracer
from pydantic import SecretStr

from app.config import Settings
from app.observability.langsmith import (
    PrivacyPreservingLangChainTracer,
    _safe_trace_error,
    create_langsmith_tracer_factory,
)


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "database_connection_mode": "url",
        "database_url": SecretStr(
            "postgresql+asyncpg://travel_user:test@localhost/travel_test"
        ),
        "jwt_signing_key": SecretStr("test-jwt-signing-key-0123456789abcdef"),
        "refresh_token_hash_key": SecretStr("test-refresh-hash-key-0123456789abcdef"),
    }
    values.update(overrides)
    return Settings(**values)


def test_disabled_langsmith_does_not_create_client(monkeypatch) -> None:
    client_factory = MagicMock()
    monkeypatch.setattr("app.observability.langsmith.Client", client_factory)

    assert create_langsmith_tracer_factory(settings()) is None
    client_factory.assert_not_called()


def test_enabled_langsmith_hides_all_run_inputs_and_outputs(monkeypatch) -> None:
    client = MagicMock()
    client_factory = MagicMock(return_value=client)
    monkeypatch.setattr("app.observability.langsmith.Client", client_factory)

    factory = create_langsmith_tracer_factory(
        settings(
            langsmith_tracing=True,
            langsmith_api_key=SecretStr("test-langsmith-key"),
            langsmith_project="test-project",
            langsmith_endpoint="https://eu.api.smith.langchain.com",
            langsmith_tracing_sampling_rate=0.1,
        )
    )

    assert factory is not None
    tracer = factory()
    client_factory.assert_called_once_with(
        api_url="https://eu.api.smith.langchain.com",
        api_key="test-langsmith-key",
        tracing_sampling_rate=0.1,
        hide_inputs=True,
        hide_outputs=True,
        auto_batch_tracing=True,
        timeout_ms=(2_000, 5_000),
    )
    assert isinstance(tracer, PrivacyPreservingLangChainTracer)
    assert tracer.project_name == "test-project"
    second_tracer = factory()
    assert second_tracer is not tracer
    assert second_tracer.client is tracer.client
    tracer.order_map[uuid4()] = (uuid4(), "synthetic-order")
    assert second_tracer.order_map == {}
    assert second_tracer.run_map is not tracer.run_map


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("  ")])
def test_missing_key_disables_tracing_without_creating_client(monkeypatch, key):
    client_factory = MagicMock()
    monkeypatch.setattr("app.observability.langsmith.Client", client_factory)
    assert (
        create_langsmith_tracer_factory(
            settings(langsmith_tracing=True, langsmith_api_key=key)
        )
        is None
    )
    client_factory.assert_not_called()


def test_langsmith_options_load_from_dotenv(tmp_path):
    env_file = tmp_path / "tracing.env"
    env_file.write_text(
        'LANGSMITH_PROJECT="travel-assistant-local"\n'
        "LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com\n"
        "LANGSMITH_TRACING_SAMPLING_RATE=0.1\n"
    )
    config = settings(_env_file=env_file)
    assert config.langsmith_project == "travel-assistant-local"
    assert config.langsmith_endpoint == "https://eu.api.smith.langchain.com"
    assert config.langsmith_tracing_sampling_rate == 0.1


def test_trace_errors_do_not_keep_provider_error_text() -> None:
    error = _safe_trace_error(ValueError("sensitive prompt fragment"))

    assert str(error) == "ValueError"
    assert "sensitive prompt fragment" not in str(error)


def test_tracer_sanitizes_tool_errors_before_forwarding(monkeypatch) -> None:
    client = MagicMock()
    tracer = PrivacyPreservingLangChainTracer(client=client)
    parent_callback = MagicMock()
    monkeypatch.setattr(LangChainTracer, "on_tool_error", parent_callback)
    original_error = ValueError("sensitive provider response")

    tracer.on_tool_error(original_error, run_id=uuid4())

    forwarded_error = parent_callback.call_args.args[0]
    assert str(forwarded_error) == "ValueError"
    assert "sensitive provider response" not in str(forwarded_error)
