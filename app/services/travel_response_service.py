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
from app.domain.planning import PlanningState
from app.graph.clarifications import extract_travel_clarification
from app.graph.nodes.input import build_travel_graph_input
from app.graph.planning_context import PlanningRuntimeContext
from app.graph.schemas.itineraries import to_itinerary_item_drafts
from app.graph.subgraphs.model_gateway import ModelGatewayError, model_call_budget
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
from app.services.conversation_planning_service import (
    ConversationPlanningService,
    PlanningTurn,
    StalePlanningRequestError,
)
from app.services.conversation_processing_service import ConversationProcessingService
from app.services.conversation_service import AcceptedTravelRequest
from app.services.generation_admission import (
    GenerationAdmission,
    GenerationAdmissionError,
    GenerationAlreadyInProgressError,
)
from app.services.itinerary_service import ItineraryService

logger = logging.getLogger(__name__)
CANCELLED_RUN_CLEANUP_TIMEOUT_SECONDS = 5.0
MAX_PLANNING_CONTEXT_MESSAGES = 6
MAX_PLANNING_CONTEXT_CHARS = 4_000
MAX_PLANNING_CONTEXT_MESSAGE_CHARS = 2_000


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


def _planning_conversation_context(
    messages: tuple[Message, ...],
    *,
    context_start_message_id: UUID,
    current_message_id: UUID,
    anchor_message: Message | None = None,
) -> list[dict[str, str]]:
    """Keep the trip's first request and its latest bounded clarifications."""
    start_index = next(
        (
            index
            for index, message in enumerate(messages)
            if message.id == context_start_message_id
        ),
        None,
    )
    if start_index is None and anchor_message is None:
        return []

    relevant = [
        message
        for message in (messages[start_index:] if start_index is not None else messages)
        if message.id != current_message_id and message.role in {"user", "assistant"}
    ]
    anchor = anchor_message or next(
        (message for message in relevant if message.id == context_start_message_id),
        None,
    )
    if anchor is None or anchor.role != "user":
        return []
    recent = [message for message in relevant if message.id != anchor.id]
    selected = [anchor, *recent[-(MAX_PLANNING_CONTEXT_MESSAGES - 1) :]]
    context = [
        {
            "role": message.role,
            "content": (
                message.content[:MAX_PLANNING_CONTEXT_MESSAGE_CHARS]
                if message.role == "user"
                else message.content[-MAX_PLANNING_CONTEXT_MESSAGE_CHARS:]
            ),
        }
        for message in selected
    ]
    total_chars = sum(len(message["content"]) for message in context)
    while len(context) > 1 and total_chars > MAX_PLANNING_CONTEXT_CHARS:
        removed = context.pop(1)
        total_chars -= len(removed["content"])
    return context


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
        planning_service: ConversationPlanningService | None = None,
        admission: GenerationAdmission | None = None,
    ) -> None:
        self.processing = processing_service
        self.itineraries = itinerary_service
        self.graph = graph
        self.assistant_run_lease_seconds = assistant_run_lease_seconds
        self.travel_response_timeout_seconds = travel_response_timeout_seconds
        self.max_model_attempts = max_model_attempts
        self.langsmith_tracer_factory = langsmith_tracer_factory
        self.planning = planning_service
        self.admission = admission

    async def generate_reply(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
    ) -> TravelResponseResult:
        # Use the persisted message ID after server-side ownership validation.
        message_id = accepted_request.user_message.id
        if self.planning is not None or self.admission is not None:
            if await self.processing.has_cached_reply(
                user_id=user_id,
                message_id=message_id,
            ):
                return await self._generate_reply(
                    user_id=user_id,
                    accepted_request=accepted_request,
                )
        try:
            if self.admission is not None:
                async with self.admission.reserve(
                    user_id,
                    message_id=message_id,
                ):
                    return await self._generate_serialized(
                        user_id=user_id,
                        accepted_request=accepted_request,
                    )

            return await self._generate_serialized(
                user_id=user_id,
                accepted_request=accepted_request,
            )

        except GenerationAlreadyInProgressError:
            return TravelResponseResult(
                message=None,
                is_cached=False,
                is_processing=True,
                error_code=None,
            )
        except StalePlanningRequestError:
            code = TravelResponseErrorCode.STALE_REQUEST
        except GenerationAdmissionError as error:
            code = error.code
            await self.processing.defer_generation(
                user_id=user_id,
                message_id=message_id,
            )

        return TravelResponseResult(
            message=None,
            is_cached=False,
            is_processing=False,
            error_code=code,
        )

    async def _generate_serialized(
        self, *, user_id: UUID, accepted_request: AcceptedTravelRequest
    ) -> TravelResponseResult:
        with model_call_budget():
            return await self._generate_turn(
                user_id=user_id, accepted_request=accepted_request
            )

    async def _generate_turn(
        self, *, user_id: UUID, accepted_request: AcceptedTravelRequest
    ) -> TravelResponseResult:
        if self.planning is not None:
            async with self.planning.turn(
                user_id=user_id,
                accepted=accepted_request,
                lease_seconds=self.assistant_run_lease_seconds,
                wait_seconds=self.travel_response_timeout_seconds,
            ) as turn:
                return await self._generate_reply(
                    user_id=user_id,
                    accepted_request=accepted_request,
                    planning_turn=turn,
                )
        return await self._generate_reply(
            user_id=user_id, accepted_request=accepted_request
        )

    async def _generate_reply(
        self,
        *,
        user_id: UUID,
        accepted_request: AcceptedTravelRequest,
        planning_turn: PlanningTurn | None = None,
    ) -> TravelResponseResult:
        graph_context: dict[str, object] = {}
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
            if (
                accepted_request.trip_id is not None
                or parsed_content.rich_content is not None
            ):
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
            runtime_context: PlanningRuntimeContext | None = None
            if planning_turn is not None:
                planning_service = self.planning
                if planning_service is None:
                    raise RuntimeError(
                        "Planning service is required for a planning turn"
                    )
                planning_turn = await planning_service.ensure_context_anchor(
                    turn=planning_turn,
                    user_message_id=accepted_request.user_message.id,
                )

                async def persist_requirements(
                    state: PlanningState, reset_trip: bool
                ) -> PlanningState:
                    nonlocal planning_turn
                    if planning_turn is None:
                        raise RuntimeError("Planning turn is unavailable")
                    planning_turn = await planning_service.save_requirements(
                        turn=planning_turn,
                        state=state,
                        user_message_id=accepted_request.user_message.id,
                        reset_trip=reset_trip,
                    )
                    return planning_turn.state

                runtime_context = PlanningRuntimeContext(
                    persist_requirements=persist_requirements
                )
                # Planning receives bounded, trip-scoped context in its typed state.
                graph_input["messages"] = []
                context_start_message_id = (
                    planning_turn.state.context_start_message_id
                    or planning_turn.state.requirements_message_id
                    or accepted_request.user_message.id
                )
                anchor_message = None
                if not any(
                    message.id == context_start_message_id
                    for message in start.context.history
                ):
                    anchor_message = await self.processing.get_context_anchor(
                        user_id=user_id,
                        conversation_id=accepted_request.conversation.id,
                        message_id=context_start_message_id,
                    )
                graph_input.update(
                    planning=planning_turn.state,
                    latest_message=accepted_request.user_message.content,
                    recent_conversation=_planning_conversation_context(
                        start.context.history,
                        context_start_message_id=context_start_message_id,
                        current_message_id=accepted_request.user_message.id,
                        anchor_message=anchor_message,
                    ),
                    user_message_id=accepted_request.user_message.id,
                    requirements_changed=False,
                    reset_trip=False,
                    planning_preferences=planning_turn.preferences,
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
            if runtime_context is not None:
                graph_context["context"] = runtime_context

            graph_started_at = perf_counter()
            graph_outcome = "error"
            async with response_timeout:
                try:
                    graph_result = await self.graph.ainvoke(
                        graph_input, config=graph_config, **graph_context
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
                clarification = graph_result.get(
                    "clarification"
                ) or extract_travel_clarification(graph_result.get("messages", []))
                itinerary_id = None
                rich_content = None
                generated_itinerary = graph_result.get("generated_itinerary")
                planning_trip = None
                if planning_turn is not None and self.planning is not None:
                    planning_trip = await self.planning.stage(
                        turn=planning_turn,
                        state=graph_result["planning"],
                        generated=generated_itinerary is not None,
                        reset_trip=graph_result.get("reset_trip", False),
                    )
                if generated_itinerary is not None:
                    trip = planning_trip or accepted_request.trip
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
                        research=graph_result.get("research"),
                        requirements=(
                            graph_result["planning"].requirements
                            if planning_turn is not None
                            else None
                        ),
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
            # Roll back staged work and release only this worker's claim. If the
            # database is unavailable, lease expiry remains the recovery fallback.
            try:
                await asyncio.wait_for(
                    self.processing.fail_processing(
                        claim=claim,
                        error_code=TravelResponseErrorCode.GENERATION_FAILED,
                    ),
                    timeout=CANCELLED_RUN_CLEANUP_TIMEOUT_SECONDS,
                )
            except (Exception, asyncio.CancelledError):
                logger.warning("Cancelled assistant run cleanup could not complete")
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
