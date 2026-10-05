"""Conversation routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Response, status

from app.api.dependencies import ConversationServiceDependency, CurrentPrincipal

router = APIRouter(
    prefix="/conversations",
    tags=["conversations"],
)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Permanently delete one conversation",
)
async def delete_user_conversation(
    conversation_id: Annotated[UUID, Path()],
    principal: CurrentPrincipal,
    conversation_service: ConversationServiceDependency,
) -> Response:
    """Delete one idle conversation owned by the authenticated user."""

    await conversation_service.delete_conversation(
        conversation_id=conversation_id,
        user_id=principal.user.id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
