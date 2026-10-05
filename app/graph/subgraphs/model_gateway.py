"""Provider-independent model gateway contracts."""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from app.config import Settings
from app.graph.google_schema import google_generation_schema
from app.graph.model_response import (
    ModelResponseRefusedError,
    model_response_text,
    provider_error_diagnostics,
    response_diagnostics,
)
from app.observability.metrics import record_metric
from app.observability.request_context import cancellation_outcome

logger = logging.getLogger(__name__)
_attempt_budget: ContextVar[list[int] | None] = ContextVar(
    "model_attempt_budget", default=None
)


@contextmanager
def model_call_budget(limit: int = 12):
    """Share a bounded counter across graph tasks for one response only."""
    token = _attempt_budget.set([limit])
    try:
        yield
    finally:
        _attempt_budget.reset(token)


class ModelGatewayError(Exception):
    """Raised when no configured model provider can return a response."""


@runtime_checkable
class ModelGateway(Protocol):
    """Generate one assistant response from bounded chat history."""

    async def generate(
        self,
        *,
        messages: Sequence[BaseMessage],
        schema: type[BaseModel] | None = None,
    ) -> AIMessage:
        """Return an assistant message, optionally constrained by a schema."""


class AsyncChatModel(Protocol):
    """Minimum async interface implemented by LangChain chat models."""

    async def ainvoke(self, input: Sequence[BaseMessage]) -> BaseMessage:
        """Generate one chat response."""

    def bind_tools(self, tools: Sequence[BaseTool]) -> "AsyncChatModel":
        """Return a model configured with callable tools."""


@dataclass(frozen=True, slots=True)
class ModelProvider:
    """One named model provider in fallback priority order."""

    name: str
    client: AsyncChatModel


class FallbackModelGateway:
    """Try providers until one returns valid text or tool calls."""

    def __init__(
        self, providers: Sequence[ModelProvider], *, max_input_chars: int = 120000
    ) -> None:
        if not providers:
            raise ValueError("at least one model provider is required")

        provider_names = [provider.name.strip() for provider in providers]

        if any(not provider_name for provider_name in provider_names):
            raise ValueError("model provider names must not be blank")
        if len(set(provider_names)) != len(provider_names):
            raise ValueError("model provider names must be unique")

        self.providers = tuple(providers)
        self.max_input_chars = max_input_chars

    async def generate(
        self,
        *,
        messages: Sequence[BaseMessage],
        schema: type[BaseModel] | None = None,
    ) -> AIMessage:
        """Return the first valid assistant or tool-call response."""
        if not messages:
            raise ValueError("model messages must not be empty")
        if (
            sum(len(str(message.content)) for message in messages)
            > self.max_input_chars
        ):
            raise ModelGatewayError("Model input exceeds the configured limit")
        last_error: Exception | None = None

        for provider in self.providers:
            budget = _attempt_budget.get()
            if budget is not None:
                if budget[0] <= 0:
                    raise ModelGatewayError("Model attempt budget exhausted")
                budget[0] -= 1
            started_at = perf_counter()
            outcome = "error"
            try:
                if schema is None:
                    response = await provider.client.ainvoke(list(messages))
                else:
                    structured_output = getattr(
                        provider.client, "with_structured_output", None
                    )
                    if not callable(structured_output):
                        raise TypeError(
                            "model provider does not support structured output"
                        )
                    result = await structured_output(
                        google_generation_schema(schema)
                        if provider.name == "google"
                        else schema,
                        method="json_schema",
                        include_raw=True,
                    ).ainvoke(list(messages))
                    response = (
                        result.get("raw") if isinstance(result, Mapping) else None
                    )
                if not isinstance(response, AIMessage):
                    last_error = TypeError("model provider returned a non-AI message")
                    outcome = "invalid_response"
                else:
                    try:
                        content = model_response_text(response)
                        if not content.strip() and not response.tool_calls:
                            raise ValueError(
                                "Model response has no visible text or tools"
                            )
                    except ModelResponseRefusedError:
                        raise
                    except ValueError as error:
                        last_error = error
                        outcome = "invalid_response"
                    else:
                        outcome = "success"
                        return (
                            response
                            if isinstance(response.content, str)
                            else response.model_copy(update={"content": content})
                        )
                logger.warning(
                    "Model provider returned an invalid response",
                    extra={
                        "model_provider": provider.name,
                        "error_type": type(last_error).__name__,
                        **(
                            response_diagnostics(response)
                            if isinstance(response, AIMessage)
                            else {}
                        ),
                    },
                )
            except ModelResponseRefusedError as error:
                outcome = "refused"
                logger.warning(
                    "Model response refused",
                    extra={
                        "model_provider": provider.name,
                        **response_diagnostics(response),
                    },
                )
                raise ModelGatewayError("Model response was refused") from error
            except asyncio.CancelledError:
                outcome = cancellation_outcome()
                raise
            except Exception as error:
                last_error = error
                diagnostics = provider_error_diagnostics(error)
                outcome = "timeout" if isinstance(error, TimeoutError) else "error"
                logger.warning(
                    "Model provider attempt failed",
                    extra={
                        "model_provider": provider.name,
                        "error_type": type(error).__name__,
                        **diagnostics,
                    },
                )
                # Invalid credentials, denied access and malformed requests do not
                # become valid by spending another provider call. Transient rate
                # limits/timeouts/server failures retain bounded fallback.
                status = diagnostics.get("http_status")
                if status in {400, 401, 403, 404, 413, 422}:
                    raise ModelGatewayError("Model request was rejected") from error
            finally:
                _record_provider_attempt(
                    provider_name=provider.name,
                    outcome=outcome,
                    started_at=started_at,
                )
        raise ModelGatewayError(
            "No configured model provider returned a valid response"
        ) from last_error


def _record_provider_attempt(
    *,
    provider_name: str,
    outcome: str,
    started_at: float,
) -> None:
    """Emit provider outcome and latency as low-cardinality metric events."""

    record_metric(
        name="model_provider_attempts",
        value=1,
        metric_type="counter",
        labels={"model_provider": provider_name, "outcome": outcome},
    )
    record_metric(
        name="model_provider_duration_ms",
        value=round((perf_counter() - started_at) * 1000, 3),
        metric_type="distribution",
        labels={"model_provider": provider_name},
    )


def build_model_gateway(
    settings: Settings, *, tools: Sequence[BaseTool] = ()
) -> FallbackModelGateway:
    """Build configured model providers in cost-aware fallback order."""

    providers: list[ModelProvider] = []
    if settings.google_api_key is not None:
        providers.append(
            ModelProvider(
                name="google",
                client=bind_model_tools(
                    ChatGoogleGenerativeAI(
                        model=settings.google_model,
                        api_key=settings.google_api_key,
                        request_timeout=settings.model_timeout_seconds,
                        retries=0,
                        max_output_tokens=settings.model_max_output_tokens,
                    ),
                    tools=tools,
                ),
            )
        )
    if settings.openai_api_key is not None:
        providers.append(
            ModelProvider(
                name="openai",
                client=bind_model_tools(
                    ChatOpenAI(
                        model=settings.openai_model,
                        api_key=settings.openai_api_key,
                        timeout=settings.model_timeout_seconds,
                        max_retries=0,
                        reasoning_effort=None,
                        max_tokens=settings.model_max_output_tokens,
                    ),
                    tools=tools,
                ),
            )
        )
    if settings.groq_api_key is not None:
        providers.append(
            ModelProvider(
                name="groq",
                client=bind_model_tools(
                    ChatGroq(
                        model=settings.groq_model,
                        api_key=settings.groq_api_key,
                        timeout=settings.model_timeout_seconds,
                        max_retries=0,
                        max_tokens=settings.model_max_output_tokens,
                    ),
                    tools=tools,
                ),
            )
        )

    return FallbackModelGateway(
        providers=providers, max_input_chars=settings.model_max_input_chars
    )


def bind_model_tools(
    client: AsyncChatModel,
    *,
    tools: Sequence[BaseTool],
) -> AsyncChatModel:
    """Bind the same tools to a provider when tools are configured."""
    if not tools:
        return client

    return client.bind_tools(tools=list(tools))
