"""Provider error classification never publishes provider-controlled strings."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import ClientError
from langchain_core.messages import HumanMessage
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

from app.graph.model_response import provider_error_diagnostics
from app.graph.subgraphs.model_gateway import (
    FallbackModelGateway,
    ModelGatewayError,
    ModelProvider,
)


def schema_error():
    inner = ClientError(
        400,
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "SECRET response_json_schema is too complex",
                "details": [
                    {
                        "fieldViolations": [
                            {
                                "field": "generation_config.response_schema.SECRET",
                                "description": "Unknown name oneOf: SECRET",
                            }
                        ]
                    }
                ],
            }
        },
    )
    wrapper = ChatGoogleGenerativeAIError("SECRET wrapper")
    wrapper.__cause__ = inner
    return wrapper


def test_real_google_exception_chain_produces_only_constant_hints():
    result = provider_error_diagnostics(schema_error())
    assert result == {
        "http_status": 400,
        "provider_error_code": "invalid_argument",
        "provider_error_hints": [
            "response_schema",
            "schema_complexity",
            "union_schema",
            "unsupported_field",
        ],
    }
    assert "SECRET" not in json.dumps(result)


def test_hints_reach_gateway_log_without_changing_fallback(caplog):
    primary, fallback = AsyncMock(), AsyncMock()
    primary.ainvoke.side_effect = schema_error()
    gateway = FallbackModelGateway(
        [ModelProvider("google", primary), ModelProvider("fallback", fallback)]
    )
    with pytest.raises(ModelGatewayError):
        asyncio.run(gateway.generate(messages=[HumanMessage(content="SECRET prompt")]))
    record = next(
        r for r in caplog.records if r.getMessage() == "Model provider attempt failed"
    )
    assert "response_schema" in record.provider_error_hints
    assert "SECRET" not in str(record.__dict__)
    fallback.ainvoke.assert_not_awaited()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "SECRET",
        {"error": []},
        {"details": [None, {"fieldViolations": [None, 1]}]},
    ],
)
def test_malformed_diagnostics_do_not_raise(payload):
    error = RuntimeError("SECRET")
    error.details = payload
    assert provider_error_diagnostics(error) == {}


def test_provider_message_scan_is_bounded():
    error = RuntimeError("SECRET")
    error.message = "x" * 4096 + " response_schema"
    assert provider_error_diagnostics(error) == {}
