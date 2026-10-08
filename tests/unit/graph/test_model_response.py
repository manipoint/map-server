"""Response normalization, failure semantics and telemetry privacy regressions."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.graph.model_response import (
    ModelResponseRefusedError,
    model_response_text,
    provider_error_diagnostics,
    response_diagnostics,
)
from app.graph.nodes.responses import build_assistant_response
from app.graph.planning_builder import (
    StructuredOutputValidationError,
    structured_call,
)
from app.graph.planning_schemas import (
    MAX_STRUCTURED_RESPONSE_CHARS,
    RequirementExtraction,
)
from app.graph.subgraphs.model_gateway import (
    FallbackModelGateway,
    ModelGatewayError,
    ModelProvider,
)


@pytest.mark.parametrize(
    "content,expected",
    [
        (" hello ", " hello "),
        ("", ""),
        ([], ""),
        (["hello", " ", "world"], "hello world"),
        (
            [
                {"type": "text", "text": "hello"},
                {"type": "output_text", "text": " world"},
            ],
            "hello world",
        ),
        (
            [
                {"type": "reasoning", "text": "PRIVATE"},
                {"type": "text", "text": "answer"},
            ],
            "answer",
        ),
        ([{"type": "thinking", "thinking": "PRIVATE"}], ""),
        (
            [
                {"type": "image_url", "image_url": "https://example.com"},
                {"text": "PRIVATE"},
                {"type": "unknown", "text": "PRIVATE"},
            ],
            "",
        ),
        (
            ['{"reply":"', {"type": "text", "text": "hello "}, 'world"}'],
            '{"reply":"hello world"}',
        ),
    ],
)
def test_normalization_preserves_visible_chunks_only(content, expected):
    message = AIMessage(content=content)
    original = message.model_dump()
    assert model_response_text(message) == expected
    assert message.model_dump() == original


@pytest.mark.parametrize("value", [None, 12, {}, ["text"]])
def test_malformed_text_block_rejected(value):
    with pytest.raises(ValueError, match="Malformed"):
        model_response_text(AIMessage(content=[{"type": "text", "text": value}]))


@pytest.mark.parametrize("reason", ["length", "MAX_TOKENS", "max_output_tokens"])
def test_truncated_response_rejected_even_with_visible_text(reason):
    with pytest.raises(ValueError, match="truncated"):
        model_response_text(
            AIMessage(
                content="partial answer", response_metadata={"finish_reason": reason}
            )
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"additional_kwargs": {"refusal": "private reason"}},
        {"response_metadata": {"finish_reason": "SAFETY"}},
        {"response_metadata": {"stop_reason": "content_filter"}},
        {"response_metadata": {"prompt_feedback": {"block_reason": 1}}},
        {
            "content": [
                {"type": "text", "text": "partial"},
                {"type": "refusal", "refusal": "private"},
            ]
        },
    ],
)
def test_refusals_never_fall_back(kwargs, caplog):
    primary = AsyncMock()
    primary.ainvoke.return_value = AIMessage(**{"content": "text", **kwargs})
    fallback = AsyncMock()
    gateway = FallbackModelGateway(
        [ModelProvider("primary", primary), ModelProvider("fallback", fallback)]
    )
    with pytest.raises(ModelGatewayError, match="refused"):
        asyncio.run(gateway.generate(messages=[HumanMessage(content="private prompt")]))
    fallback.ainvoke.assert_not_awaited()
    assert "private" not in caplog.text


def test_gateway_normalizes_without_mutating_metadata_or_triggering_fallback():
    original = AIMessage(
        content=[{"type": "text", "text": "Answer"}],
        id="message-id",
        response_metadata={"finish_reason": "STOP"},
        usage_metadata={"input_tokens": 2, "output_tokens": 1, "total_tokens": 3},
    )
    primary, fallback = AsyncMock(), AsyncMock()
    primary.ainvoke.return_value = original
    gateway = FallbackModelGateway(
        [ModelProvider("primary", primary), ModelProvider("fallback", fallback)]
    )
    result = asyncio.run(gateway.generate(messages=[HumanMessage(content="hello")]))
    assert result.content == "Answer" and result.id == original.id
    assert result.usage_metadata == original.usage_metadata
    assert result.response_metadata == original.response_metadata
    assert isinstance(original.content, list)
    fallback.ainvoke.assert_not_awaited()


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content=[]),
        AIMessage(content="  "),
        AIMessage(content=[{"type": "reasoning", "text": "PRIVATE"}]),
        AIMessage(content=[{"type": "text", "text": None}]),
        AIMessage(content="partial", response_metadata={"finish_reason": "length"}),
        AIMessage(
            content="answer",
            invalid_tool_calls=[
                {"name": "x", "args": "{", "id": "1", "error": "PRIVATE"}
            ],
        ),
        HumanMessage(content="wrong role"),
    ],
)
def test_invalid_response_uses_next_provider_once(message):
    primary, fallback = AsyncMock(), AsyncMock()
    primary.ainvoke.return_value = message
    fallback.ainvoke.return_value = AIMessage(content="Recovered")
    gateway = FallbackModelGateway(
        [ModelProvider("primary", primary), ModelProvider("fallback", fallback)]
    )
    assert (
        asyncio.run(gateway.generate(messages=[HumanMessage(content="hello")])).content
        == "Recovered"
    )
    fallback.ainvoke.assert_awaited_once()


def test_tool_only_blocks_preserve_valid_tool_calls():
    primary = AsyncMock()
    primary.ainvoke.return_value = AIMessage(
        content=[], tool_calls=[{"name": "search_places", "args": {}, "id": "call-1"}]
    )
    result = asyncio.run(
        FallbackModelGateway([ModelProvider("primary", primary)]).generate(
            messages=[HumanMessage(content="hello")]
        )
    )
    assert result.content == ""
    assert result.tool_calls[0]["id"] == "call-1"


def test_wrapped_error_diagnostics_are_bounded_and_redacted():
    inner = RuntimeError("SECRET error body")
    inner.status_code = 429
    inner.body = {
        "error": {
            "code": "insufficient_quota",
            "message": "SECRET",
            "api_key": "SECRET",
        }
    }
    outer = RuntimeError("SECRET wrapped error")
    outer.__cause__ = inner
    inner.__cause__ = outer
    assert provider_error_diagnostics(outer) == {
        "http_status": 429,
        "provider_error_code": "insufficient_quota",
    }


@pytest.mark.parametrize("status", [None, True, 0, 999, "SECRET"])
def test_invalid_status_and_untrusted_code_are_not_logged(status):
    error = RuntimeError("SECRET")
    error.status_code = status
    error.body = {"code": "SECRET"}
    assert provider_error_diagnostics(error) == {"provider_error_code": "unknown"}


def test_response_metadata_cannot_leak_arbitrary_strings():
    message = AIMessage(
        content="SECRET",
        response_metadata={"finish_reason": "SECRET", "api_key": "SECRET"},
    )
    assert "SECRET" not in str(response_diagnostics(message))


def test_exception_chain_status_controls_fallback_and_logs_safe_code(caplog):
    inner = RuntimeError("SECRET")
    inner.response = SimpleNamespace(status_code=401)
    inner.body = {"code": "invalid_api_key", "message": "SECRET"}
    wrapper = RuntimeError("SECRET")
    wrapper.__cause__ = inner
    primary, fallback = AsyncMock(), AsyncMock()
    primary.ainvoke.side_effect = wrapper
    gateway = FallbackModelGateway(
        [ModelProvider("primary", primary), ModelProvider("fallback", fallback)]
    )
    with pytest.raises(ModelGatewayError):
        asyncio.run(gateway.generate(messages=[HumanMessage(content="SECRET")]))
    record = next(
        record
        for record in caplog.records
        if record.getMessage() == "Model provider attempt failed"
    )
    assert record.http_status == 401 and record.provider_error_code == "invalid_api_key"
    assert "SECRET" not in str(record.__dict__)
    fallback.ainvoke.assert_not_awaited()


def test_reported_failure_chain_exhausts_once_and_preserves_429(caplog):
    groq, google, openai = AsyncMock(), AsyncMock(), AsyncMock()
    groq.ainvoke.return_value = AIMessage(
        content="", response_metadata={"finish_reason": "length"}
    )
    google.ainvoke.side_effect = RuntimeError("SECRET")
    rate_limit = RuntimeError("SECRET")
    rate_limit.status_code = 429
    rate_limit.body = {"code": "rate_limit_exceeded"}
    openai.ainvoke.side_effect = rate_limit
    gateway = FallbackModelGateway(
        [
            ModelProvider("groq", groq),
            ModelProvider("google", google),
            ModelProvider("openai", openai),
        ]
    )
    with pytest.raises(ModelGatewayError) as caught:
        asyncio.run(gateway.generate(messages=[HumanMessage(content="hello")]))
    assert caught.value.__cause__ is rate_limit
    for client in (groq, google, openai):
        client.ainvoke.assert_awaited_once()
    assert any(getattr(record, "http_status", None) == 429 for record in caplog.records)


def test_structured_parser_accepts_blocks_and_final_response_reuses_normalization():
    payload = json.dumps(
        {"intent": "chat", "reply": "Hello", "updates": {}, "changed_fields": []}
    )
    gateway = AsyncMock()
    gateway.generate.return_value = AIMessage(
        content=[
            {"type": "reasoning", "text": "PRIVATE"},
            {"type": "text", "text": payload[:10]},
            payload[10:],
        ]
    )
    result = asyncio.run(
        structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
    )
    assert result.reply == "Hello"
    gateway.generate.assert_awaited_once()
    assert gateway.generate.await_args.kwargs["schema"] is RequirementExtraction
    assert (
        "model_json_schema"
        not in gateway.generate.await_args.kwargs["messages"][0].content
    )
    assert build_assistant_response(
        {"messages": [AIMessage(content=[{"type": "text", "text": " Hello "}])]}
    ) == {"assistant_response": "Hello"}


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content="not JSON"),
        AIMessage(content='{"intent":"unsupported"}'),
        AIMessage(content="x" * (MAX_STRUCTURED_RESPONSE_CHARS + 1)),
        AIMessage(
            content='{"intent":"chat"}',
            tool_calls=[{"name": "x", "args": {}, "id": "1"}],
        ),
        AIMessage(
            content='{"intent":"chat"}', response_metadata={"finish_reason": "length"}
        ),
    ],
)
def test_structured_parser_rejects_invalid_output(message):
    gateway = AsyncMock()
    gateway.generate.return_value = message
    with pytest.raises(ValueError):
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )


def test_structured_parser_reports_safe_schema_diagnostics():
    gateway = AsyncMock()
    gateway.generate.return_value = AIMessage(
        content='{"intent":"secret_invalid_value","updates":{},"changed_fields":[]}'
    )

    with pytest.raises(StructuredOutputValidationError) as caught:
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )

    error = caught.value
    assert error.schema_name == "RequirementExtraction"
    assert error.error_count == 1
    assert error.error_types == ("literal_error",)
    assert error.error_fields == ("intent",)
    assert "secret_invalid_value" not in str(error)


def test_structured_parser_reports_nested_paths_without_response_values():
    gateway = AsyncMock()
    gateway.generate.return_value = AIMessage(
        content=(
            '{"intent":"search","updates":{},"changed_fields":[],"search":'
            '{"kind":"weather","arguments":{},"private_key":"PRIVATE"}}'
        )
    )

    with pytest.raises(StructuredOutputValidationError) as caught:
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )

    details = caught.value.error_details
    assert any(
        detail["type"] == "missing" and detail["path"][-2:] == ["arguments", "city"]
        for detail in details
    )
    assert any(
        detail["type"] == "extra_forbidden" and detail["path"][-1] == "<extra_field>"
        for detail in details
    )
    assert "PRIVATE" not in repr(details)


def test_structured_parser_summarizes_invalid_union_discriminator_safely():
    gateway = AsyncMock()
    gateway.generate.return_value = AIMessage(
        content=(
            '{"intent":"search","updates":{},"changed_fields":[],"search":'
            '{"kind":"private-kind","arguments":{"origin":"PRIVATE_CITY",'
            '"destination":"PRIVATE_DESTINATION"}}}'
        )
    )

    with pytest.raises(StructuredOutputValidationError) as caught:
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )

    kind_errors = [
        detail
        for detail in caught.value.error_details
        if detail["type"] == "literal_error" and detail["path"][-1] == "kind"
    ]
    assert kind_errors
    assert any(
        detail["input_present"]
        and detail["input_kind"] == "string"
        and detail["input_length"] == len("private-kind")
        and "expected" in detail
        for detail in kind_errors
    )
    assert "private-kind" not in repr(caught.value.error_details)
    assert "PRIVATE_CITY" not in repr(caught.value.error_details)


def test_structured_parser_propagates_cancellation_and_refusal():
    gateway = AsyncMock()
    gateway.generate.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )

    gateway.generate.side_effect = None
    gateway.generate.return_value = AIMessage(
        content="", additional_kwargs={"refusal": "PRIVATE"}
    )
    with pytest.raises(ModelResponseRefusedError):
        asyncio.run(
            structured_call(gateway, RequirementExtraction, prompt="JSON", data={})
        )


def test_google_sdk_wrapped_status_is_extracted_without_error_details():
    from google.genai.errors import ClientError
    from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError

    inner = ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "SECRET"}},
    )
    wrapper = ChatGoogleGenerativeAIError("SECRET")
    wrapper.__cause__ = inner
    assert provider_error_diagnostics(wrapper) == {
        "http_status": 429,
        "provider_error_code": "resource_exhausted",
    }


def test_unknown_malformed_block_type_is_not_visible_text():
    assert (
        model_response_text(
            AIMessage(
                content=[
                    {"type": [], "text": "PRIVATE"},
                    {"type": "text", "text": "answer"},
                ]
            )
        )
        == "answer"
    )


def test_malformed_function_finish_is_rejected():
    with pytest.raises(ValueError, match="malformed"):
        model_response_text(
            AIMessage(
                content="answer",
                response_metadata={"finish_reason": "MALFORMED_FUNCTION_CALL"},
            )
        )
