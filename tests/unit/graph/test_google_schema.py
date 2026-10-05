"""Google compatibility preserves application-side validation."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.graph.google_schema import google_generation_schema
from app.graph.planning_schemas import RequirementExtraction
from app.graph.subgraphs.model_gateway import FallbackModelGateway, ModelProvider


class Example(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(max_length=3)
    amount: int = Field(ge=1, le=10)


def test_projection_preserves_property_names_and_original_constraints():
    original = Example.model_json_schema()
    projected = google_generation_schema(Example)
    assert projected["properties"]["title"] == {"type": "string"}
    assert projected["properties"]["amount"] == {"type": "integer"}
    assert projected["required"] == ["title", "amount"]
    assert projected["additionalProperties"] is False
    assert Example.model_json_schema() == original
    with pytest.raises(ValidationError):
        Example.model_validate_json('{"title":"too long","amount":0}')


def test_extraction_projection_preserves_refs_enums_and_required_fields():
    original = RequirementExtraction.model_json_schema()
    projected = google_generation_schema(RequirementExtraction)
    assert (
        projected["properties"]["intent"]["enum"]
        == original["properties"]["intent"]["enum"]
    )
    assert projected["required"] == original["required"]
    assert set(projected["$defs"]) == set(original["$defs"])
    assert (
        projected["properties"]["updates"]["$ref"]
        == original["properties"]["updates"]["$ref"]
    )


@pytest.mark.parametrize("provider", ["google", "openai", "groq"])
def test_only_google_gets_projection(provider):
    client = Mock()
    client.with_structured_output.return_value.ainvoke = AsyncMock(
        return_value={
            "raw": AIMessage(content=json.dumps({"title": "ok", "amount": 1}))
        }
    )
    gateway = FallbackModelGateway([ModelProvider(provider, client)])
    asyncio.run(
        gateway.generate(messages=[HumanMessage(content="test")], schema=Example)
    )
    schema = client.with_structured_output.call_args.args[0]
    assert schema == (
        google_generation_schema(Example) if provider == "google" else Example
    )
