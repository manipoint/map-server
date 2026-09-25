"""Provider-independent model gateway contracts."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import Settings
from app.observability.metrics import record_metric
from app.observability.request_context import cancellation_outcome

logger = logging.getLogger(__name__)


class ModelGatewayError(Exception):
    """Raised when no configured model provider can return a response."""


@runtime_checkable
class ModelGateway(Protocol):
    """Generate one assistant response from bounded chat history."""

    async def generate(
        self,
        *,
        messages: Sequence[BaseMessage],
    ) -> AIMessage:
        """Return a validated assistant message."""


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

    def __init__(self, providers: Sequence[ModelProvider]) -> None:
        if not providers:
            raise ValueError("at least one model provider is required")

        provider_names = [provider.name.strip() for provider in providers]

        if any(not provider_name for provider_name in provider_names):
            raise ValueError("model provider names must not be blank")
        if len(set(provider_names)) != len(provider_names):
            raise ValueError("model provider names must be unique")

        self.providers = tuple(providers)

    async def generate(self, *, messages: Sequence[BaseMessage]) -> AIMessage:
        """Return the first valid assistant or tool-call response."""
        if not messages:
            raise ValueError("model messages must not be empty")
        last_error: Exception | None = None

        for provider in self.providers:
            started_at = perf_counter()
            outcome = "error"
            try:
                response = await provider.client.ainvoke(list(messages))
                if not isinstance(response, AIMessage):
                    last_error = TypeError("model provider returned a non-AI message")
                    outcome = "invalid_response"
                elif (
                    not (isinstance(response.content, str) and response.content.strip())
                    and not response.tool_calls
                ):
                    last_error = ValueError(
                        "model provider returned neither text nor tool calls"
                    )
                    outcome = "invalid_response"
                else:
                    outcome = "success"
                    return response
                logger.warning(
                    "Model provider returned an invalid response",
                    extra={
                        "model_provider": provider.name,
                        "error_type": type(last_error).__name__,
                    },
                )
            except asyncio.CancelledError:
                outcome = cancellation_outcome()
                raise
            except Exception as error:
                last_error = error
                outcome = "timeout" if isinstance(error, TimeoutError) else "error"
                logger.warning(
                    "Model provider attempt failed",
                    extra={
                        "model_provider": provider.name,
                        "error_type": type(error).__name__,
                    },
                )
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
                    ),
                    tools=tools,
                ),
            )
        )

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
                    ),
                    tools=tools,
                ),
            )
        )
    return FallbackModelGateway(providers=providers)


def bind_model_tools(
    client: AsyncChatModel,
    *,
    tools: Sequence[BaseTool],
) -> AsyncChatModel:
    """Bind the same tools to a provider when tools are configured."""
    if not tools:
        return client

    return client.bind_tools(tools=list(tools))
