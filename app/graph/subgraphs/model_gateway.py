"""Provider-independent model gateway contracts."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from langchain_core.messages import AIMessage, BaseMessage
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


@dataclass(frozen=True, slots=True)
class ModelProvider:
    """One named model provider in fallback priority order."""

    name: str
    client: AsyncChatModel


class FallbackModelGateway:
    """Try configured chat providers in order until one returns valid text."""

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
        """Return the first valid text response from configured providers."""
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

            if not isinstance(response.content, str) or not response.content.strip():
                last_error = ValueError(
                    "model provider returned an empty or non-text response"
                )
                continue
            return response
        raise ModelGatewayError(
            "No configured model provider returned a valid response"
        ) from last_error


def build_model_gateway(settings: Settings) -> FallbackModelGateway:
    """Build configured model providers in cost-aware fallback order."""

    providers: list[ModelProvider] = []

    if settings.groq_api_key is not None:
        providers.append(
            ModelProvider(
                name="groq",
                client=ChatGroq(
                    model=settings.groq_model,
                    api_key=settings.groq_api_key,
                    timeout=settings.model_timeout_seconds,
                    max_retries=0,
                ),
            )
        )

    if settings.google_api_key is not None:
        providers.append(
            ModelProvider(
                name="google",
                client=ChatGoogleGenerativeAI(
                    model=settings.google_model,
                    api_key=settings.google_api_key,
                    request_timeout=settings.model_timeout_seconds,
                    retries=0,
                ),
            )
        )

    if settings.openai_api_key is not None:
        providers.append(
            ModelProvider(
                name="openai",
                client=ChatOpenAI(
                    model=settings.openai_model,
                    api_key=settings.openai_api_key,
                    timeout=settings.model_timeout_seconds,
                    max_retries=0,
                ),
            )
        )
    return FallbackModelGateway(providers=providers)
