"""Tests for provider-independent model fallback behavior."""

import asyncio
from collections.abc import Sequence

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from pydantic import SecretStr

import app.graph.subgraphs.model_gateway as model_gateway
from app.config import Settings
from app.graph.subgraphs.model_gateway import (
    FallbackModelGateway,
    ModelGatewayError,
    ModelProvider,
    build_model_gateway,
)


class FakeChatModel:
    """Deterministic async model double for fallback tests."""

    def __init__(self, result: BaseMessage | BaseException) -> None:
        self.result = result
        self.calls: list[list[BaseMessage]] = []

    async def ainvoke(self, input: Sequence[BaseMessage]) -> BaseMessage:
        """Record input and return or raise the configured result."""

        self.calls.append(list(input))

        if isinstance(self.result, BaseException):
            raise self.result

        return self.result


class FakeModelConstructor:
    """Record provider client construction without creating network clients."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> FakeChatModel:
        """Record keyword arguments and return a harmless async client."""

        self.calls.append(kwargs)
        return FakeChatModel(AIMessage(content="Unused"))


def create_settings(**overrides: object) -> Settings:
    """Create valid deterministic settings for model gateway construction tests."""

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


def test_gateway_returns_the_first_valid_provider_response() -> None:
    """A successful primary provider should prevent fallback model cost."""

    primary = FakeChatModel(AIMessage(content="Primary itinerary"))
    fallback = FakeChatModel(AIMessage(content="Fallback itinerary"))
    gateway = FallbackModelGateway(
        [
            ModelProvider(name="groq", client=primary),
            ModelProvider(name="openai", client=fallback),
        ]
    )
    messages = [HumanMessage(content="Plan a Lahore trip")]

    response = asyncio.run(gateway.generate(messages=messages))

    assert response.content == "Primary itinerary"
    assert primary.calls == [messages]
    assert fallback.calls == []


def test_gateway_accepts_a_tool_call_without_text() -> None:
    """A valid tool request should not trigger a fallback model call."""

    tool_response = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "get_current_weather",
                "args": {"city": "Lahore"},
                "id": "weather-call-1",
                "type": "tool_call",
            }
        ],
    )
    primary = FakeChatModel(tool_response)
    fallback = FakeChatModel(AIMessage(content="Fallback itinerary"))
    gateway = FallbackModelGateway(
        [
            ModelProvider(name="groq", client=primary),
            ModelProvider(name="openai", client=fallback),
        ]
    )
    messages = [HumanMessage(content="What is the weather in Lahore?")]

    response = asyncio.run(gateway.generate(messages=messages))

    assert response is tool_response
    assert response.tool_calls == [
        {
            "name": "get_current_weather",
            "args": {"city": "Lahore"},
            "id": "weather-call-1",
            "type": "tool_call",
        }
    ]
    assert primary.calls == [messages]
    assert fallback.calls == []


def test_gateway_falls_back_after_a_provider_failure() -> None:
    """A provider exception should move to the next configured provider."""

    primary = FakeChatModel(RuntimeError("primary unavailable"))
    fallback = FakeChatModel(AIMessage(content="Fallback itinerary"))
    gateway = FallbackModelGateway(
        [
            ModelProvider(name="groq", client=primary),
            ModelProvider(name="google", client=fallback),
        ]
    )
    messages = [HumanMessage(content="Plan a Lahore trip")]

    response = asyncio.run(gateway.generate(messages=messages))

    assert response.content == "Fallback itinerary"
    assert primary.calls == [messages]
    assert fallback.calls == [messages]


@pytest.mark.parametrize(
    "invalid_response",
    [HumanMessage(content="Wrong role"), AIMessage(content="   ")],
)
def test_gateway_falls_back_after_an_invalid_provider_response(
    invalid_response: BaseMessage,
) -> None:
    """Non-AI and blank provider output should never become an assistant reply."""

    primary = FakeChatModel(invalid_response)
    fallback = FakeChatModel(AIMessage(content="Fallback itinerary"))
    gateway = FallbackModelGateway(
        [
            ModelProvider(name="groq", client=primary),
            ModelProvider(name="openai", client=fallback),
        ]
    )

    response = asyncio.run(
        gateway.generate(messages=[HumanMessage(content="Plan a Lahore trip")])
    )

    assert response.content == "Fallback itinerary"
    assert len(primary.calls) == 1
    assert len(fallback.calls) == 1


def test_gateway_raises_a_safe_error_when_all_providers_fail() -> None:
    """Raw provider exceptions must not appear in the public gateway error."""

    gateway = FallbackModelGateway(
        [
            ModelProvider(
                name="groq",
                client=FakeChatModel(RuntimeError("secret provider detail")),
            ),
            ModelProvider(
                name="google",
                client=FakeChatModel(AIMessage(content="")),
            ),
        ]
    )

    with pytest.raises(ModelGatewayError) as raised_error:
        asyncio.run(
            gateway.generate(messages=[HumanMessage(content="Plan a Lahore trip")])
        )

    assert str(raised_error.value) == (
        "No configured model provider returned a valid response"
    )
    assert "secret provider detail" not in str(raised_error.value)


def test_gateway_rejects_empty_messages_without_calling_a_provider() -> None:
    """A model call without conversation context should fail before provider cost."""

    provider = FakeChatModel(AIMessage(content="Unused"))
    gateway = FallbackModelGateway([ModelProvider(name="groq", client=provider)])

    with pytest.raises(ValueError, match="model messages must not be empty"):
        asyncio.run(gateway.generate(messages=[]))

    assert provider.calls == []


@pytest.mark.parametrize(
    "providers, error_message",
    [
        ([], "at least one model provider is required"),
        (
            [ModelProvider(name="   ", client=FakeChatModel(AIMessage(content="x")))],
            "model provider names must not be blank",
        ),
        (
            [
                ModelProvider(
                    name="groq", client=FakeChatModel(AIMessage(content="x"))
                ),
                ModelProvider(
                    name=" groq ", client=FakeChatModel(AIMessage(content="y"))
                ),
            ],
            "model provider names must be unique",
        ),
    ],
)
def test_gateway_rejects_invalid_provider_configuration(
    providers: list[ModelProvider],
    error_message: str,
) -> None:
    """Configuration should avoid silent duplicate or unnamed fallback providers."""

    with pytest.raises(ValueError, match=error_message):
        FallbackModelGateway(providers)


def test_gateway_allows_task_cancellation_to_propagate() -> None:
    """Cancellation should not trigger a fallback model request."""

    primary = FakeChatModel(asyncio.CancelledError())
    fallback = FakeChatModel(AIMessage(content="Fallback itinerary"))
    gateway = FallbackModelGateway(
        [
            ModelProvider(name="groq", client=primary),
            ModelProvider(name="openai", client=fallback),
        ]
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            gateway.generate(messages=[HumanMessage(content="Plan a Lahore trip")])
        )

    assert len(primary.calls) == 1
    assert fallback.calls == []


def test_build_model_gateway_uses_the_configured_cost_aware_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configured providers should be built in Groq, Google, OpenAI order."""

    groq_constructor = FakeModelConstructor()
    google_constructor = FakeModelConstructor()
    openai_constructor = FakeModelConstructor()
    monkeypatch.setattr(model_gateway, "ChatGroq", groq_constructor)
    monkeypatch.setattr(model_gateway, "ChatGoogleGenerativeAI", google_constructor)
    monkeypatch.setattr(model_gateway, "ChatOpenAI", openai_constructor)
    settings = create_settings(
        groq_api_key=SecretStr("test-groq-key"),
        google_api_key=SecretStr("test-google-key"),
        openai_api_key=SecretStr("test-openai-key"),
        groq_model="groq-test-model",
        google_model="google-test-model",
        openai_model="openai-test-model",
        model_timeout_seconds=45.0,
        assistant_run_lease_seconds=60,
    )

    gateway = build_model_gateway(settings)

    assert [provider.name for provider in gateway.providers] == [
        "groq",
        "google",
        "openai",
    ]
    assert groq_constructor.calls == [
        {
            "model": "groq-test-model",
            "api_key": settings.groq_api_key,
            "timeout": 45.0,
            "max_retries": 0,
        }
    ]
    assert google_constructor.calls == [
        {
            "model": "google-test-model",
            "api_key": settings.google_api_key,
            "request_timeout": 45.0,
            "retries": 0,
        }
    ]
    assert openai_constructor.calls == [
        {
            "model": "openai-test-model",
            "api_key": settings.openai_api_key,
            "timeout": 45.0,
            "max_retries": 0,
        }
    ]


def test_build_model_gateway_can_use_google_as_the_only_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Google must not depend on a Groq key being configured."""

    google_constructor = FakeModelConstructor()
    monkeypatch.setattr(model_gateway, "ChatGoogleGenerativeAI", google_constructor)
    settings = create_settings(google_api_key=SecretStr("test-google-key"))

    gateway = build_model_gateway(settings)

    assert [provider.name for provider in gateway.providers] == ["google"]
    assert google_constructor.calls[0]["model"] == settings.google_model


def test_build_model_gateway_rejects_missing_provider_credentials() -> None:
    """Startup should fail clearly when no LLM provider can be constructed."""

    with pytest.raises(ValueError, match="at least one model provider is required"):
        build_model_gateway(create_settings())
