import hashlib
from datetime import timedelta

from app.common.time import utc_now
from app.domain.planning import PendingTravelSelection, PlanningState
from app.services.deal_discovery_service import (
    DealDiscoveryInput,
    DealDiscoverySnapshot,
)
from app.services.standalone_search_service import (
    FlightDealsRequest,
    SearchReply,
    StandaloneRequest,
    StandaloneSearchService,
)


class StandaloneSearchWorkflow:
    def __init__(self, standalone_service: StandaloneSearchService) -> None:
        self._service = standalone_service

    async def run(
        self, *, request: StandaloneRequest, planning: PlanningState
    ) -> dict[str, object]:
        if isinstance(request, FlightDealsRequest):
            canonical = DealDiscoveryInput.model_validate(
                request.arguments.model_dump()
            )
            request_key = hashlib.sha256(
                canonical.model_dump_json().encode("utf-8")
            ).hexdigest()
            now = utc_now()
            cached = (
                DealDiscoverySnapshot.model_validate(planning.deal_discovery)
                if planning.deal_discovery is not None
                else None
            )
            reusable = (
                cached is not None
                and cached.request_key == request_key
                and cached.reply is not None
                and cached.result.status != "airport_resolution_required"
                and timedelta(0) <= now - cached.searched_at < timedelta(minutes=10)
            )
            if reusable:
                assert cached is not None
                snapshot = cached
                reply = SearchReply(content=cached.reply, deal_result=cached.result)
            else:
                reply = await self._service.search(request=request)
                snapshot = (
                    DealDiscoverySnapshot(
                        request_key=request_key,
                        searched_at=now,
                        request=canonical,
                        result=reply.deal_result,
                        reply=reply.content,
                    )
                    if reply.deal_result is not None
                    and reply.deal_result.status != "airport_resolution_required"
                    else None
                )
            result = snapshot.result if snapshot is not None else reply.deal_result
            pending = (
                PendingTravelSelection(
                    kind="deal",
                    option_ids=tuple(option.option_id for option in result.options),
                    reason="choose",
                )
                if result is not None and result.options
                else None
            )
            updated = PlanningState.model_validate(
                {
                    **planning.model_dump(),
                    "deal_discovery": (
                        snapshot.model_dump(mode="json")
                        if snapshot is not None
                        else None
                    ),
                    "pending_travel_selection": pending,
                    "pending_search": (
                        request.model_dump(mode="json")
                        if reply.input_required
                        else None
                    ),
                }
            )
            return {
                "assistant_response": reply.content,
                "clarification": reply.clarification,
                "planning": updated,
            }

        reply = await self._service.search(request=request)
        state = planning.model_copy(
            update={
                "pending_search": (
                    request.model_dump(mode="json") if reply.input_required else None
                )
            }
        )
        return {
            "assistant_response": reply.content,
            "clarification": reply.clarification,
            "planning": state,
        }
