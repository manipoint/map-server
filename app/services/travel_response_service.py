import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from langgraph.graph.state import CompiledStateGraph

from app.database.models.message import Message
from app.domain.enums import TravelResponseErrorCode
from app.graph.nodes.input import build_travel_graph_input
from app.graph.subgraphs.model_gateway import ModelGatewayError
from app.services.conversation_processing_service import ConversationProcessingService
from app.services.conversation_service import AcceptedTravelRequest

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TravelResponseResult:
    message: Message | None
    is_cached: bool
    is_processing: bool
    error_code: TravelResponseErrorCode | None


class TravelResponseService:
    def __init__(
        self,
        *,
        processing_service: ConversationProcessingService,
        graph: CompiledStateGraph,
        assistant_run_lease_seconds: int,
        travel_response_timeout_seconds: float,
        max_model_attempts: int,
    ) -> None:
        self.processing = processing_service
        self.graph = graph
        self.assistant_run_lease_seconds = assistant_run_lease_seconds
        self.travel_response_timeout_seconds = travel_response_timeout_seconds
        self.max_model_attempts = max_model_attempts

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
            return TravelResponseResult(
                message=start.context.cached_reply,
                is_cached=True,
                is_processing=False,
                error_code=None,
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
        try:
            graph_input = build_travel_graph_input(
                messages=start.context.history,
                locale=accepted_request.conversation.locale,
            )
            async with asyncio.timeout(self.travel_response_timeout_seconds):
                graph_result = await self.graph.ainvoke(graph_input)
                save_reply = await self.processing.save_reply(
                    user_id=user_id,
                    accepted_request=accepted_request,
                    claim=claim,
                    content=graph_result["assistant_response"],
                )
            return TravelResponseResult(
                message=save_reply.message,
                is_cached=save_reply.is_duplicate,
                is_processing=False,
                error_code=None,
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
