"""Weather MCP tool schemas."""

from pydantic import BaseModel, ConfigDict, Field


class CurrentWeatherInput(BaseModel):
    """Validated input for the current-weather MCP tool."""

    model_config = ConfigDict(str_strip_whitespace=True)

    city: str = Field(min_length=1, max_length=120)
