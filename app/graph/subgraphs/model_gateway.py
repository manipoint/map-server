"""Provider-independent model gateway contracts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.config import Settings


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
            try:
                response = await provider.client.ainvoke(list(messages))

            except Exception as error:
                last_error = error
                continue
            if not isinstance(response, AIMessage):
                last_error = TypeError("model provider returned a non-AI message")
                continue
            has_text_response = isinstance(response.content, str) and bool(
                response.content.strip()
            )
            has_tool_calls = bool(response.tool_calls)
            if not has_text_response and not has_tool_calls:
                last_error = ValueError(
                    "model provider returned neither text nor tool calls"
                )
                continue

            return response
        raise ModelGatewayError(
            "No configured model provider returned a valid response"
        ) from last_error


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
