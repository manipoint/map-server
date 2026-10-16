"""Safe invocation and validation of planning model responses."""

import json
from time import perf_counter
from typing import TypeVar, get_args

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from app.graph.model_response import model_response_text
from app.graph.planning_prompts import (
    REQUIREMENTS_PROMPT_VERSION,
    SYNTHESIS_PROMPT_VERSION,
)
from app.graph.planning_schemas import (
    MAX_STRUCTURED_RESPONSE_CHARS,
    RequirementExtraction,
    ResearchedItinerary,
)
from app.graph.subgraphs.model_gateway import ModelGateway
from app.observability.metrics import record_metric

Output = TypeVar("Output", bound=BaseModel)


class UnexpectedToolCallError(ValueError):
    pass


class StructuredResponseTooLargeError(ValueError):
    pass


class StructuredOutputValidationError(ValueError):
    """A model response failed its expected structured-output schema."""

    def __init__(
        self,
        *,
        schema_name: str,
        schema: type[BaseModel],
        validation_error: ValidationError,
        allowed_fields: set[str],
    ) -> None:
        errors = validation_error.errors(
            include_url=False,
            include_context=True,
            include_input=True,
        )
        error_types = sorted(
            {item["type"] for item in errors if isinstance(item.get("type"), str)}
        )
        error_fields = sorted(
            {
                location[0]
                for item in errors
                if (location := item.get("loc"))
                and isinstance(location[0], str)
                and location[0] in allowed_fields
            }
        )
        schema_fields = _schema_field_names(schema)
        self.schema_name = schema_name
        self.error_count = len(errors)
        self.error_types = tuple(error_types[:5])
        self.error_fields = tuple(error_fields[:5])
        self.error_details = tuple(
            _safe_validation_detail(item, schema_fields) for item in errors[:12]
        )
        super().__init__(f"Model response does not match {schema_name}.")


async def structured_call(
    gateway: ModelGateway,
    schema: type[Output],
    *,
    prompt: str,
    data: dict[str, object],
) -> Output:

    if schema is RequirementExtraction:
        stage = "requirements"
        prompt_version = REQUIREMENTS_PROMPT_VERSION

    elif schema is ResearchedItinerary:
        stage = "synthesis"
        prompt_version = SYNTHESIS_PROMPT_VERSION

    else:
        raise ValueError(f"Unsupported schema: {schema.__name__}")

    messages = [
        SystemMessage(content=prompt),
        HumanMessage(
            content=json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
    ]

    started = perf_counter()

    try:
        response = await gateway.generate(
            schema=schema,
            messages=messages,
        )
    finally:
        record_metric(
            name="planning_model_duration_ms",
            value=(perf_counter() - started) * 1000,
            metric_type="distribution",
            labels={
                "stage": stage,
                "prompt_version": prompt_version,
            },
        )

    usage = response.usage_metadata or {}

    for key in ("input_tokens", "output_tokens"):
        count = usage.get(key)

        if type(count) is int and count >= 0:
            record_metric(
                name="planning_model_tokens",
                value=count,
                metric_type="counter",
                labels={
                    "stage": stage,
                    "direction": key,
                },
            )

    if response.tool_calls:
        raise UnexpectedToolCallError("Expected tool-free structured model output")

    content = model_response_text(response)

    if len(content) > MAX_STRUCTURED_RESPONSE_CHARS:
        raise StructuredResponseTooLargeError(
            "Structured model output exceeds the size limit"
        )

    try:
        return schema.model_validate_json(content)

    except ValidationError as error:
        raise StructuredOutputValidationError(
            schema_name=schema.__name__,
            schema=schema,
            validation_error=error,
            allowed_fields=set(schema.model_fields),
        ) from error


def _schema_field_names(schema: type[BaseModel]) -> set[str]:
    """Collect declared names so arbitrary response keys remain redacted."""

    names: set[str] = set()
    pending: list[type[BaseModel]] = [schema]
    visited: set[type[BaseModel]] = set()

    while pending:
        model = pending.pop()

        if model in visited:
            continue
        visited.add(model)
        names.add(model.__name__)

        for field_name, field in model.model_fields.items():
            names.add(field_name)
            candidates = [field.annotation, *get_args(field.annotation)]

            for candidate in candidates:
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    pending.append(candidate)

    return names


def _safe_validation_detail(
    error: dict[str, object], schema_fields: set[str]
) -> dict[str, object]:
    """Describe a schema failure without exposing model or user values."""

    location = error.get("loc", ())
    error_type = error.get("type")
    path: list[str | int] = []

    for part in location if isinstance(location, tuple) else ():
        if isinstance(part, int):
            path.append(part)
        elif isinstance(part, str):
            if part in schema_fields:
                path.append(part)
            else:
                path.append(
                    "<extra_field>" if error_type == "extra_forbidden" else "<variant>"
                )
    result: dict[str, object] = {
        "type": error_type if isinstance(error_type, str) else "unknown",
        "path": path[:8],
        "input_present": "input" in error,
    }
    value = error.get("input")
    result["input_kind"] = _input_kind(value)

    if isinstance(value, str):
        result["input_length"] = len(value)
    elif isinstance(value, (dict, list, tuple)):
        result["input_size"] = len(value)

    context = error.get("ctx")
    expected = context.get("expected") if isinstance(context, dict) else None
    if error_type == "literal_error" and isinstance(expected, str):
        result["expected"] = expected[:200]

    return result


def _input_kind(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "other"
