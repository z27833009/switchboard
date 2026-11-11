# ADR 0001: Modular monolith with ports and adapters

Status: proposed, 2026-11-11

## Context

The platform must show architecture thinking, be extensible without core changes, and be built by one developer in evenings with a first milestone in about two weeks. Three shapes were considered: a LangGraph-native application, a hexagonal modular monolith, and a set of services.

## Decision

Build one Python package with a framework-free `core` that defines ports (interfaces) and domain models. All integrations are adapters selected by configuration. Only three things run as separate processes: the MCP tool server, the web UI and PostgreSQL. An import-linter rule enforces that `core` imports nothing outside the standard library and pydantic.

## Alternatives

- **LangGraph-native.** Fastest; every component is a LangChain abstraction. Rejected because the project's extension points would be LangChain's, not the author's, and the core would be coupled to a fast-moving framework.
- **Services with a queue.** Looks most like a platform. Rejected because none of the internal boundaries have a real scaling or isolation need yet, and the operational cost would consume the whole time budget.

## Consequences

- About 15 to 20 percent more code up front for interfaces and contract tests.
- Any port can get a second implementation without touching the orchestrator; this is what the v2 sequential orchestrator and the Valkey cache adapter will demonstrate.
- The monolith can be split later along the port boundaries if a real need appears; the ADR for that split would reference this one.
