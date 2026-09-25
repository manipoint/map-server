import asyncio
import logging
from dataclasses import dataclass
from time import perf_counter
from uuid import UUID, uuid4

from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from app.database.models.message import Message
from app.domain.assistant_content import AssistantRichContent
from app.domain.clarifications import TravelClarification
from app.domain.enums import TravelResponseErrorCode
from app.graph.clarifications import extract_travel_clarification
from app.graph.nodes.input import build_travel_graph_input
from app.graph.schemas.itineraries import to_itinerary_item_drafts
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.observability.langsmith import LangSmithTracerFactory
from app.observability.metrics import record_metric
from app.observability.request_context import (
    TraceContext,
    cancellation_outcome,
    reset_trace_context,
    set_trace_context,
)
from app.services.assistant_rich_content_mapper import (
    build_itinerary_rich_content,
)
from app.services.conversation_processing_service import ConversationProcessingService
from app.services.conversation_service import AcceptedTravelRequest
from app.services.itinerary_service import ItineraryService

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ParsedAssistantStructuredContent:
    """Typed structured content restored from an assistant message."""

    clarification: TravelClarification | None = None
    rich_content: AssistantRichContent | None = None


@dataclass(frozen=True, slots=True)
class TravelResponseResult:
    message: Message | None
    is_cached: bool
    is_processing: bool
    error_code: TravelResponseErrorCode | None
    itinerary_id: UUID | None = None
    clarification: TravelClarification | None = None
    rich_content: AssistantRichContent | None = None


def parse_persisted_structured_content(
    message: Message,
) -> ParsedAssistantStructuredContent:
    """Restore validated structured content using its discriminator."""
    structured_content = message.structured_content
    if structured_content is None:
        return ParsedAssistantStructuredContent()
    content_type = structured_content.get("type")
    try:
        if content_type == "airport_selection":
            return ParsedAssistantStructuredContent(
                clarification=TravelClarification.model_validate(structured_content)
            )
        if content_type == "rich_response":
            return ParsedAssistantStructuredContent(
                rich_content=AssistantRichContent.model_validate(structured_content)
            )
    except ValidationError:
        logger.warning(
            "Stored assistant structured content is invalid",
            extra={
                "assistant_message_id": str(message.id),
                "structured_content_type": content_type,
            },
        )
        return ParsedAssistantStructuredContent()

    logger.warning(
        "Stored assistant structured content has an unsupported type",
        extra={
            "assistant_message_id": str(message.id),
            "structured_content_type": content_type,
        },
    )

    return ParsedAssistantStructuredContent()


class TravelResponseService:
    def __init__(
        self,
        *,
        processing_service: ConversationProcessingService,
        itinerary_service: ItineraryService,
        graph: CompiledStateGraph,
        assistant_run_lease_seconds: int,
        travel_response_timeout_seconds: float,
        max_model_attempts: int,
        langsmith_tracer_factory: LangSmithTracerFactory | None = None,
    ) -> None:
        self.processing = processing_service
        self.itineraries = itinerary_service
        self.graph = graph
        self.assistant_run_lease_seconds = assistant_run_lease_seconds
        self.travel_response_timeout_seconds = travel_response_timeout_seconds
        self.max_model_attempts = max_model_attempts
        self.langsmith_tracer_factory = langsmith_tracer_factory

    async def generate_reply(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
    ) -> TravelResponseResult:
        start = await self.processing.start_processing(
            user_id=user_id,
            accepted_request=accepted_request,
            lease_seconds=self.assistant_run_lease_seconds,
            max_attempts=self.max_model_attempts,
        )

        if start.context.cached_reply is not None:
            cached_reply = start.context.cached_reply
            parsed_content = parse_persisted_structured_content(cached_reply)
            generated_draft = None
            if accepted_request.trip_id is not None:
                generated_draft = await self.itineraries.find_generated_draft(
                    source_message_id=accepted_request.user_message.id,
                    user_id=user_id,
                )

            return TravelResponseResult(
                message=cached_reply,
                is_cached=True,
                is_processing=False,
                error_code=None,
                itinerary_id=(
                    generated_draft.itinerary.id
                    if generated_draft is not None
                    else None
                ),
                clarification=parsed_content.clarification,
                rich_content=parsed_content.rich_content,
            )

        if start.is_attempts_exhausted(max_attempts=self.max_model_attempts):
            return TravelResponseResult(
                message=None,
                is_cached=False,
                is_processing=False,
                error_code=TravelResponseErrorCode.ATTEMPTS_EXHAUSTED,
            )

        if not start.should_invoke_model:
            return TravelResponseResult(
                message=None,
                is_cached=False,
                is_processing=True,
                error_code=None,
            )
        claim = start.claim
        assert claim is not None
        trace_id = uuid4()
        response_timeout = asyncio.timeout(self.travel_response_timeout_seconds)
        trace_context = TraceContext(
            trace_id=str(trace_id),
            conversation_id=str(accepted_request.conversation.id),
            client_message_id=str(accepted_request.user_message.client_message_id),
            timeout=response_timeout,
        )
        context_token = set_trace_context(trace_context)
        try:
            graph_input = build_travel_graph_input(
                messages=start.context.history,
                locale=accepted_request.conversation.locale,
                trip=accepted_request.trip,
            )
            graph_config: dict[str, object] = {
                "run_id": trace_id,
                "run_name": "travel_assistant",
                "tags": ["travel-assistant"],
                "metadata": {
                    "trace_id": trace_context.trace_id,
                    "conversation_id": trace_context.conversation_id,
                    "client_message_id": trace_context.client_message_id,
                },
            }
            if self.langsmith_tracer_factory is not None:
                graph_config["callbacks"] = [self.langsmith_tracer_factory()]
            graph_started_at = perf_counter()
            graph_outcome = "error"
            async with response_timeout:
                try:
                    graph_result = await self.graph.ainvoke(
                        graph_input,
                        config=graph_config,
                    )
                    graph_outcome = "success"
                except asyncio.CancelledError:
                    graph_outcome = cancellation_outcome()
                    raise
                except TimeoutError:
                    graph_outcome = "timeout"
                    raise
                finally:
                    graph_duration_ms = round(
                        (perf_counter() - graph_started_at) * 1000,
                        3,
                    )
                    logger.info(
                        "Travel graph execution completed",
                        extra={
                            "event": "travel_graph_execution",
                            "outcome": graph_outcome,
                            "duration_ms": graph_duration_ms,
                        },
                    )
                    record_metric(
                        name="travel_graph_runs",
                        value=1,
                        metric_type="counter",
                        labels={"outcome": graph_outcome},
                    )
                    record_metric(
                        name="travel_graph_duration_ms",
                        value=graph_duration_ms,
                        metric_type="distribution",
                        labels={"outcome": graph_outcome},
                    )
                clarification = extract_travel_clarification(
                    graph_result.get("messages", [])
                )
                itinerary_id = None
                rich_content = None
                generated_itinerary = graph_result.get("generated_itinerary")
                if generated_itinerary is not None:
                    trip = accepted_request.trip
                    if trip is None:
                        raise RuntimeError("Generated itinerary requires trip context")
                    generated_draft = await self.itineraries.create_generated_draft(
                        trip_id=trip.id,
                        user_id=user_id,
                        source_message_id=accepted_request.user_message.id,
                        items=to_itinerary_item_drafts(generated_itinerary),
                        commit=False,
                    )
                    itinerary_id = generated_draft.itinerary.id
                    rich_content = build_itinerary_rich_content(
                        itinerary_id=itinerary_id,
                        trip=trip,
                        generated=generated_itinerary,
                    )
                save_arguments: dict[str, object] = {
                    "user_id": user_id,
                    "accepted_request": accepted_request,
                    "claim": claim,
                    "content": graph_result["assistant_response"],
                }
                if clarification is not None:
                    save_arguments["structured_content"] = clarification.model_dump(
                        mode="json"
                    )
                elif rich_content is not None:
                    save_arguments["structured_content"] = rich_content.model_dump(
                        mode="json"
                    )
                save_reply = await self.processing.save_reply(**save_arguments)
                parsed_content = parse_persisted_structured_content(save_reply.message)
            return TravelResponseResult(
                message=save_reply.message,
                is_cached=save_reply.is_duplicate,
                is_processing=False,
                error_code=None,
                itinerary_id=itinerary_id,
                clarification=parsed_content.clarification,
                rich_content=parsed_content.rich_content,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "Travel response exceeded its deadline",
                extra={
                    "conversation_id": str(accepted_request.conversation.id),
                    "client_message_id": str(
                        accepted_request.user_message.client_message_id
                    ),
                    "timeout_seconds": self.travel_response_timeout_seconds,
                },
            )
            await self.processing.fail_processing(
                claim=claim,
                error_code=TravelResponseErrorCode.GENERATION_FAILED,
            )
            raise
        except ModelGatewayError:
            await self.processing.fail_processing(
                claim=claim,
                error_code=TravelResponseErrorCode.PROVIDER_ERROR,
            )
            raise
        except Exception:
            await self.processing.fail_processing(
                claim=claim,
                error_code=TravelResponseErrorCode.GENERATION_FAILED,
            )
            raise
        finally:
            reset_trace_context(context_token)
