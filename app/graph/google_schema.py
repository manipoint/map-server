"""Google generation schemas; domain validation still uses original models."""

from typing import Any

from pydantic import BaseModel

# The full extraction schema is rejected by the configured Google endpoint.
# Keep its structural contract while leaving these constraints to Pydantic.
_LOCAL_VALIDATION_KEYWORDS = frozenset(
    {
        "default",
        "title",
        "pattern",
        "format",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minItems",
        "maxItems",
    }
)


def google_generation_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Return a fresh simplified schema without changing the original model.

    Preserve field names, references, required fields, unions, enums and extra
    property restrictions. The planning structured_call validates returned JSON
    against the original Pydantic model, including all omitted constraints.
    """

    def simplify(value: Any) -> Any:
        if isinstance(value, list):
            return [simplify(item) for item in value]
        if not isinstance(value, dict):
            return value
        return {
            key: {name: simplify(child) for name, child in item.items()}
            if key in {"properties", "$defs"}
            else simplify(item)
            for key, item in value.items()
            if key not in _LOCAL_VALIDATION_KEYWORDS
        }

    return simplify(model.model_json_schema())
