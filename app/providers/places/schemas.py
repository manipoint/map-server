from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.domain.places import PlaceSearchStatus
from app.providers.locations.schemas import ResolvedLocation


class PlaceSearchInput(BaseModel):
    destination: str
    interests: list[str] = Field(default_factory=list, max_length=10)
    family_friendly: bool | None = None
    max_results: int = Field(default=5, ge=1, le=10)


class PlaceOption(BaseModel):
    name: str
    summary: str
    categories: list[str]
    address: str | None = None
    website_url: HttpUrl | None = None
    source_urls: list[HttpUrl]


class PlaceSearchResult(BaseModel):
    status: PlaceSearchStatus
    searched_at: datetime
    location: ResolvedLocation
    places: list[PlaceOption] = Field(default_factory=list, max_length=10)
    message: str | None = None
