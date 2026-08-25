"""Validated Tavily Search API transport schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class TavilyPlaceSearchRequest(BaseModel):
    """Cost-bounded Tavily request for place discovery."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    query: str = Field(min_length=3, max_length=400)
    topic: Literal["general"] = "general"
    search_depth: Literal["basic"] = "basic"
    max_results: int = Field(default=5, ge=1, le=10)
    include_answer: Literal[False] = False
    include_raw_content: Literal[False] = False
    include_images: Literal[False] = False
    auto_parameters: Literal[False] = False


class TavilyPlaceSearchItem(BaseModel):
    """One bounded Tavily search result."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    title: str = Field(min_length=1, max_length=300)
    url: HttpUrl
    content: str = Field(min_length=1, max_length=5000)
    score: float = Field(ge=0, le=1)


class TavilyPlaceSearchResponse(BaseModel):
    """Relevant fields returned by Tavily Search."""

    model_config = ConfigDict(
        extra="ignore",
        str_strip_whitespace=True,
    )

    query: str = Field(min_length=1, max_length=400)
    results: list[TavilyPlaceSearchItem] = Field(max_length=20)
    request_id: str | None = Field(default=None, max_length=200)
