"""Observe exact in-process FastMCP tool execution without logging payloads."""

import asyncio
import logging
from time import perf_counter

from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.middleware.middleware import CallNext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams

from app.observability.metrics import record_metric
from app.observability.request_context import cancellation_outcome, get_trace_context

logger = logging.getLogger(__name__)


class ToolExecutionObservabilityMiddleware(Middleware):
    """Record tool name, outcome, and duration while excluding arguments/results."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        tool_name = context.message.name
        started_at = perf_counter()
        outcome = "error"
        try:
            result = await call_next(context)
            outcome = "error" if result.is_error else "success"
            return result
        except asyncio.CancelledError:
            outcome = cancellation_outcome()
            raise
        except Exception as error:
            logger.warning(
                "MCP tool execution raised",
                extra={
                    "event": "mcp_tool_execution",
                    "tool_name": tool_name,
                    "outcome": "error",
                    "error_type": type(error).__name__,
                },
            )
            raise
        finally:
            duration_ms = round((perf_counter() - started_at) * 1000, 3)
            trace_context = get_trace_context()
            log_context: dict[str, object] = {
                "event": "mcp_tool_execution",
                "tool_name": tool_name,
                "outcome": outcome,
                "duration_ms": duration_ms,
            }
            if trace_context is not None:
                log_context.update(
                    {
                        "trace_id": trace_context.trace_id,
                        "conversation_id": trace_context.conversation_id,
                        "client_message_id": trace_context.client_message_id,
                    }
                )
            logger.info("MCP tool execution completed", extra=log_context)
            record_metric(
                name="mcp_tool_calls",
                value=1,
                metric_type="counter",
                labels={"tool_name": tool_name, "outcome": outcome},
            )
            record_metric(
                name="mcp_tool_duration_ms",
                value=duration_ms,
                metric_type="distribution",
                labels={"tool_name": tool_name},
            )
