"""Privacy-preserving LangSmith tracing setup."""

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from langchain_core.tracers import LangChainTracer
from langsmith import Client

from app.config import Settings

logger = logging.getLogger(__name__)


def _safe_trace_error(error: BaseException) -> RuntimeError:
    """Remove provider exception text, which can contain request content."""

    return RuntimeError(type(error).__name__)


class PrivacyPreservingLangChainTracer(LangChainTracer):
    """LangChain tracer that never sends raw exception messages to LangSmith."""

    def on_chain_error(
        self,
        error: BaseException,
        *,
        inputs: dict[str, Any] | None = None,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        return super().on_chain_error(
            _safe_trace_error(error),
            inputs=inputs,
            run_id=run_id,
            **kwargs,
        )

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        return super().on_llm_error(
            _safe_trace_error(error),
            run_id=run_id,
            **kwargs,
        )

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        return super().on_tool_error(
            _safe_trace_error(error),
            run_id=run_id,
            **kwargs,
        )

    def on_retriever_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        **kwargs: Any,
    ) -> Any:
        return super().on_retriever_error(
            _safe_trace_error(error),
            run_id=run_id,
            **kwargs,
        )


@dataclass(frozen=True, slots=True)
class LangSmithTracerFactory:
    """Share the transport, but keep callback state local to one execution."""

    client: Client
    project_name: str
    app_env: str

    def __call__(self) -> PrivacyPreservingLangChainTracer:
        return PrivacyPreservingLangChainTracer(
            client=self.client,
            project_name=self.project_name,
            tags=["travel-assistant", self.app_env],
        )


def create_langsmith_tracer_factory(
    settings: Settings,
) -> LangSmithTracerFactory | None:
    """Create one shared client and a factory for execution-scoped tracers."""

    if not settings.langsmith_tracing:
        return None
    if (
        settings.langsmith_api_key is None
        or not settings.langsmith_api_key.get_secret_value().strip()
    ):
        logger.warning(
            "LangSmith tracing requested without LANGSMITH_API_KEY; tracing disabled"
        )
        return None

    client = Client(
        api_url=settings.langsmith_endpoint,
        api_key=settings.langsmith_api_key.get_secret_value(),
        tracing_sampling_rate=settings.langsmith_tracing_sampling_rate,
        hide_inputs=True,
        hide_outputs=True,
        auto_batch_tracing=True,
        timeout_ms=(2_000, 5_000),
    )
    return LangSmithTracerFactory(
        client=client,
        project_name=settings.langsmith_project,
        app_env=settings.app_env,
    )
