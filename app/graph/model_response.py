"""Shared model text handling and allowlisted diagnostics (never raw content)."""

from langchain_core.messages import AIMessage


class ModelResponseRefusedError(ValueError):
    """A refusal must not be bypassed by trying another provider."""


_FINISH_REASONS = {
    "stop",
    "end_turn",
    "tool_calls",
    "function_call",
    "length",
    "max_tokens",
    "max_output_tokens",
    "content_filter",
    "safety",
    "recitation",
    "blocklist",
    "prohibited_content",
    "spii",
    "malformed_function_call",
    "other",
}
_REFUSALS = {
    "content_filter",
    "safety",
    "recitation",
    "blocklist",
    "prohibited_content",
    "spii",
}
_TRUNCATED = {"length", "max_tokens", "max_output_tokens"}
_ERROR_CODES = {
    "insufficient_quota",
    "rate_limit_exceeded",
    "context_length_exceeded",
    "invalid_api_key",
    "model_not_found",
    "invalid_request_error",
    "resource_exhausted",
    "permission_denied",
    "unauthenticated",
    "invalid_argument",
    "unavailable",
    "deadline_exceeded",
}


def response_diagnostics(response: object) -> dict[str, object]:
    if not isinstance(response, AIMessage):
        return {"response_kind": "non_ai_message"}
    content = response.content
    metadata = response.response_metadata
    reason = metadata.get("finish_reason") or metadata.get("stop_reason")
    reason = reason.lower() if isinstance(reason, str) else None
    return {
        "response_kind": "ai_message",
        "content_kind": "text"
        if isinstance(content, str)
        else "blocks"
        if isinstance(content, list)
        else "unsupported",
        "content_block_count": len(content) if isinstance(content, list) else 0,
        "text_character_count": len(content) if isinstance(content, str) else None,
        "invalid_tool_call_count": len(response.invalid_tool_calls),
        "finish_reason": reason if reason in _FINISH_REASONS else "unknown",
    }


def model_response_text(response: AIMessage) -> str:
    """Extract only visible text, preserving chunk boundaries without mutation."""
    reason = response_diagnostics(response)["finish_reason"]
    content = response.content
    feedback = response.response_metadata.get("prompt_feedback")
    block_reason = feedback.get("block_reason") if isinstance(feedback, dict) else None
    blocked = block_reason not in (None, 0, "0", "BLOCK_REASON_UNSPECIFIED")
    if response.additional_kwargs.get("refusal") or reason in _REFUSALS or blocked:
        raise ModelResponseRefusedError("Model response was refused")
    if isinstance(content, list) and any(
        isinstance(block, dict) and block.get("type") == "refusal" for block in content
    ):
        raise ModelResponseRefusedError("Model response was refused")
    if reason in _TRUNCATED:
        raise ValueError("Model response was truncated")
    if response.invalid_tool_calls or reason == "malformed_function_call":
        raise ValueError("Model returned malformed tool calls")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        raise ValueError("Unsupported model content format")
    parts = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") in ("text", "output_text"):
            value = block.get("text")
            if not isinstance(value, str):
                raise ValueError("Malformed model text block")
            parts.append(value)
        # Reasoning, images and unknown blocks are never user-visible text.
    return "".join(parts)


def provider_error_diagnostics(error: Exception) -> dict[str, object]:
    """Read bounded exception chains; allowlist codes and never stringify errors."""
    result: dict[str, object] = {}
    current: BaseException | None = error
    seen: set[int] = set()
    for _ in range(5):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        status = getattr(current, "status_code", None)
        if not isinstance(status, int):
            status = getattr(getattr(current, "response", None), "status_code", None)
        if not isinstance(status, int):
            status = getattr(current, "code", None)
        if (
            isinstance(status, int)
            and not isinstance(status, bool)
            and 100 <= status <= 599
        ):
            result.setdefault("http_status", int(status))
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            body = body.get("error", body)
        code = (
            body.get("code")
            if isinstance(body, dict)
            else getattr(current, "code", None)
        )
        if not isinstance(code, str):
            code = getattr(current, "status", None)
        if isinstance(code, str):
            code = code.lower()
            if result.get("provider_error_code") in (None, "unknown"):
                result["provider_error_code"] = (
                    code if code in _ERROR_CODES else "unknown"
                )
        current = current.__cause__ or current.__context__
    return result
