# Documentation Index

This directory defines the target architecture for the Travel Assistant MCP Backend. Documents describe intended behavior unless a section explicitly says it covers the current prototype.

## Recommended reading order

1. [System Architecture](architecture.md)
2. [Backend Structure](backend-structure.md)
3. [Authentication and Sessions](authentication.md)
4. [WebSocket Protocol](websocket-protocol.md)
5. [LangGraph Design](langgraph.md)
6. [MCP Server Design](mcp-server.md)
7. [PostgreSQL Data Model](database.md)
8. [Model Routing and Cost Controls](model-routing.md)
9. [Deployment](deployment.md)
10. [Testing Strategy](testing.md)
11. [Development Commands](development-workflow.md)

## Source-of-truth boundaries

| Concern | Source of truth |
| --- | --- |
| Runtime boundaries and trust zones | `architecture.md` |
| Python packages and dependency direction | `backend-structure.md` |
| Graph state, nodes, and edges | `langgraph.md` |
| Tool input/output and provider behavior | `mcp-server.md` |
| Client/server event contract | `websocket-protocol.md` |
| Login, rotation, revocation, and devices | `authentication.md` |
| Tables, relations, retention, and indexes | `database.md` |
| LLM selection, retry, fallback, and budgets | `model-routing.md` |
| Environments, processes, and operations | `deployment.md` |
| Verification and quality gates | `testing.md` |
| Local commands and dependency workflow | `development-workflow.md` |

## Documentation conventions

- **MUST**, **SHOULD**, and **MAY** express requirement strength.
- Mermaid diagrams are version-controlled architecture artifacts and should change with the contract they describe.
- Examples omit real credentials and use stable identifiers such as `request_id`, `conversation_id`, and `trip_id`.
- Provider-specific payloads are deliberately hidden behind normalized domain schemas.
- Proposed modules and endpoints must not be presented as implemented until corresponding code and tests exist.

## Decision summary

- The MVP is search and itinerary planning, not booking or payment.
- Flutter uses REST for resource operations and WebSocket for interactive search/chat events.
- FastAPI is the public backend boundary.
- FastMCP is mounted as an internal interface in the MVP deployment.
- LangGraph controls deterministic routing, missing-input interrupts, tool orchestration, and bounded model fallback.
- PostgreSQL owns durable application data and LangGraph checkpoints in separate schemas.
- LangSmith provides traces and evaluations, not application persistence.
- Redis is deferred until distributed WebSocket routing, shared caching, or multi-instance rate limiting is required.
