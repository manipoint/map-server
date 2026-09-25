"""Tests for FastMCP execution telemetry and correlation."""

import asyncio
import logging
from unittest.mock import patch

import pytest
from fastmcp import FastMCP
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from app.mcp.observability import ToolExecutionObservabilityMiddleware
from app.observability.request_context import (
    TraceContext,
    reset_trace_context,
    set_trace_context,
)


def test_tool_middleware_logs_correlations_and_metrics_without_payloads(caplog) -> None:
    context = MiddlewareContext(
        message=CallToolRequestParams(
            name="search_places",
            arguments={"destination": "private user query"},
        )
    )
    result = ToolResult(
        content=[],
        structured_content={"result": "private provider result"},
    )
    correlation_token = set_trace_context(
        TraceContext("trace-1", "conversation-2", "message-3")
    )

    async def call_next(_context: MiddlewareContext) -> ToolResult:
        return result

    try:
        with (
            caplog.at_level(logging.INFO),
            patch("app.mcp.observability.record_metric") as record_metric,
        ):
            outcome = asyncio.run(
                ToolExecutionObservabilityMiddleware().on_call_tool(
                    context,
                    call_next,
                )
            )
    finally:
        reset_trace_context(correlation_token)

    assert outcome is result
    assert "private user query" not in caplog.text
    assert "private provider result" not in caplog.text
    record = next(
        record for record in caplog.records if record.name == "app.mcp.observability"
    )
    assert record.trace_id == "trace-1"
    assert record.conversation_id == "conversation-2"
    assert record.client_message_id == "message-3"
    assert record.tool_name == "search_places"
    assert record.outcome == "success"
    assert record_metric.call_count == 2


def test_tool_middleware_records_error_and_reraises() -> None:
    context = MiddlewareContext(
        message=CallToolRequestParams(name="search_places", arguments={})
    )
    error = RuntimeError("provider failure")

    async def call_next(_context: MiddlewareContext) -> ToolResult:
        raise error

    with patch("app.mcp.observability.record_metric") as record_metric:
        try:
            asyncio.run(
                ToolExecutionObservabilityMiddleware().on_call_tool(
                    context,
                    call_next,
                )
            )
        except RuntimeError as caught:
            assert caught is error
        else:
            raise AssertionError("Expected MCP tool failure to propagate")

    assert record_metric.call_count == 2
    assert record_metric.call_args_list[0].kwargs["labels"]["outcome"] == "error"


def test_fastmcp_call_tool_runs_observability_middleware(caplog) -> None:
    async def exercise() -> None:
        server = FastMCP(name="observability-test")
        server.add_middleware(ToolExecutionObservabilityMiddleware())

        @server.tool
        async def lookup_place(query: str) -> str:
            return f"result for {query}"

        context_token = set_trace_context(
            TraceContext("trace-actual", "conversation-actual", "message-actual")
        )
        try:
            with patch("app.mcp.observability.record_metric"):
                result = await server.call_tool(
                    "lookup_place", {"query": "private query"}
                )
        finally:
            reset_trace_context(context_token)

        assert result.is_error is False

    with caplog.at_level(logging.INFO):
        asyncio.run(exercise())

    record = next(
        record
        for record in caplog.records
        if record.name == "app.mcp.observability"
        and record.getMessage() == "MCP tool execution completed"
    )
    assert record.trace_id == "trace-actual"
    assert record.conversation_id == "conversation-actual"
    assert record.client_message_id == "message-actual"
    assert record.tool_name == "lookup_place"
    assert "private query" not in caplog.text


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
def test_middleware_preserves_cancellation_and_records_outcome(outcome) -> None:
    async def exercise():
        deadline = asyncio.timeout(0.01 if outcome == "timeout" else 75.0)
        token = set_trace_context(TraceContext("t", "c", "m", timeout=deadline))

        async def call_next(_context):
            if outcome == "cancelled":
                asyncio.current_task().cancel()
            await asyncio.Event().wait()

        try:
            expected_error = (
                TimeoutError if outcome == "timeout" else asyncio.CancelledError
            )
            with pytest.raises(expected_error):
                async with deadline:
                    await ToolExecutionObservabilityMiddleware().on_call_tool(
                        MiddlewareContext(message=CallToolRequestParams(name="test")),
                        call_next,
                    )
        finally:
            reset_trace_context(token)

    with patch("app.mcp.observability.record_metric") as metric:
        asyncio.run(exercise())
    assert metric.call_count == 2
    assert metric.call_args_list[0].kwargs["labels"]["outcome"] == outcome
