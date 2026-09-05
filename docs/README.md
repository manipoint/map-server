# Documentation Index

This directory describes both the implemented backend and its target architecture. Every document must label proposed behavior explicitly; an unlabeled operational claim should match the code and tests.

## Recommended reading order

1. [System Architecture](architecture.md)
2. [Phase 1 Product Scope](phase-1-scope.md)
3. [Backend Structure](backend-structure.md)
4. [Authentication and Sessions](authentication.md)
5. [WebSocket Protocol](websocket-protocol.md)
6. [Flutter Location Contract](flutter-location-contract.md)
7. [LangGraph Design](langgraph.md)
8. [Flutter Onboarding Contract](flutter-onboarding-contract.md)
9. [Flutter Home Discovery Contract](flutter-home-contract.md)
10. [MCP Server Design](mcp-server.md)
11. [PostgreSQL Data Model](database.md)
12. [Model Routing and Cost Controls](model-routing.md)
13. [Deployment](deployment.md)
14. [Reliability and SPOF Review](reliability.md)
15. [Testing Strategy](testing.md)
16. [Development Commands](development-workflow.md)

## Source-of-truth boundaries

| Concern | Source of truth |
| --- | --- |
| Phase 1 product boundary and delivery order | `phase-1-scope.md` |
| Runtime boundaries and trust zones | `architecture.md` |
| Python packages and dependency direction | `backend-structure.md` |
| Graph state, nodes, and edges | `langgraph.md` |
| Tool input/output and provider behavior | `mcp-server.md` |
| Client/server event contract | `websocket-protocol.md` |
| Flutter location selection and trip payloads | `flutter-location-contract.md` |
| Flutter preference onboarding and routing | `flutter-onboarding-contract.md` |
| Flutter personalized and curated Home sections | `flutter-home-contract.md` |
| Login, rotation, revocation, and devices | `authentication.md` |
| Tables, relations, retention, and indexes | `database.md` |
| LLM selection, retry, fallback, and budgets | `model-routing.md` |
| Environments, processes, and operations | `deployment.md` |
| Failure domains, SPOFs, and release gates | `reliability.md` |
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
- Phase 1 includes trips, saved itineraries, normalized onboarding preferences, and database-backed discovery; social login, email verification, and profile statistics are deferred.
- Popular and Featured destinations are implemented as curated catalogue queries; Trending remains first-party-event driven and falls back to correctly labelled Featured content until its activity threshold is met.
- Flutter uses REST for resource operations and WebSocket for interactive search/chat events.
- FastAPI is the public backend boundary.
- FastMCP currently runs in process and is invoked through `TravelMcpClient`; a mounted HTTP transport is a target option.
- The implemented LangGraph is a bounded model/tool loop. Deterministic routing, interrupts, and checkpoint/resume are targets.
- PostgreSQL currently owns users, onboarding preferences, curated destinations and tags, sessions, conversations, messages, assistant-run leases, trips, itineraries, and itinerary items. Destination events, search snapshots, and LangGraph checkpoints are targets.
- LangSmith tracing and evaluations are planned; structured JSON logging is the current observability baseline.
- Redis is deferred until distributed WebSocket routing, shared caching, or multi-instance rate limiting is required.
