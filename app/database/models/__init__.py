"""SQLAlchemy persistence models."""

from app.database.models.assistant_run import AssistantRun
from app.database.models.auth_session import AuthSession
from app.database.models.conversation import Conversation
from app.database.models.destination import (
    Destination,
    DestinationInterest,
    DestinationMedia,
    DestinationPlace,
    DestinationPlaceMedia,
    DestinationStyle,
    MediaAsset,
)
from app.database.models.itineraries import Itinerary
from app.database.models.itinerary_item import ItineraryItem
from app.database.models.message import Message
from app.database.models.trip import Trip
from app.database.models.user import User
from app.database.models.user_preference import (
    UserInterest,
    UserPreference,
    UserTravelStyle,
)

__all__ = [
    "AssistantRun",
    "AuthSession",
    "Conversation",
    "Destination",
    "DestinationInterest",
    "DestinationMedia",
    "DestinationPlace",
    "DestinationPlaceMedia",
    "DestinationStyle",
    "Itinerary",
    "ItineraryItem",
    "Message",
    "MediaAsset",
    "Trip",
    "User",
    "UserInterest",
    "UserPreference",
    "UserTravelStyle",
]
