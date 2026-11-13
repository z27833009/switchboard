# ADR 0011: Routing as a plan over registered agents

Status: accepted, 2026-11-13. Proposed 2026-11-12. Supersedes the `Route` enum in design 0.1. The plan, the registry-generated prompt and schema, `Send` fan-out, validation with one repair, the fallback plan and re-plan after a gate failure ship; `ClarifyAgent` is the one-class, one-entry-point proof (AC-23c).

## Context

Design 0.1 defined `Route` as an enum in core (`direct`, `rag`, `sql`, `parallel`). The supervisor prompt listed the four routes by hand, the graph had one edge per route, `cacheable` was derived from route names and the escalation path (`direct` to `rag` to `parallel`) was hard-coded. Adding an agent therefore changed core, the prompt, the graph and the escalation logic, which contradicts goal G2 ("adding an agent is one class plus one registry entry"). `parallel` also only meant "sql plus rag"; a third agent would have multiplied the combinations.

## Decision

- Each agent declares an `AgentSpec`: name, one-line description, `cacheable`, `exclusive`, `needs`. Agents register through the `switchboard.agents` entry point group.
- The supervisor returns a `RoutingDecision` with `agents: list[str]` and `reason`. A plan with more than one agent runs them in parallel and merges the results. The label (`data+docs`) is used in the UI, spans, runs and eval reports.
- The supervisor's prompt and JSON schema are generated at run time from the registry, filtered by the tenant's `agents.allow` and by whether each agent's needs are met. The model can only choose agents that exist and are allowed.
- Plans are validated (non-empty, no duplicates, size limit, exclusive agents alone), repaired once, and otherwise replaced by a configured fallback plan.
- The LangGraph adapter has one generic `run_agent` node and fans out with `Send`; no node is named after an agent.
- Escalation after a gate failure is a re-plan: the supervisor sees the previous plan and the gate's reason and must choose a different plan.
- A plan is cacheable when every agent in it is cacheable.
- The routing eval suite labels cases with `expected_agents` and scores exact set match, with Jaccard as partial credit.

## Alternatives

- **Keep the enum, add values as needed.** Simplest. Rejected because it breaks G2 at the first new agent and the interview walkthrough "add an agent" would touch core.
- **Free-form plans with ordering and dependencies (a DAG per request).** More expressive. Rejected because the demo questions need at most "these agents in parallel", a DAG multiplies validation and evaluation work, and the agent loop orchestrator (ADR 0002) already covers sequential multi-step behavior.
- **Let the model choose any string and map it afterwards.** Rejected because an enum in the JSON schema, generated from the registry, is the cheapest way to keep a small model inside the valid set.

## Consequences

- Adding `ClarifyAgent` in v1 is the live proof: one class, one entry point, no change under `core` or the orchestrator (AC-23c).
- Self-reported routing confidence is dropped; nothing consumed it and it is not calibrated.
- The routing dataset is labeled by agent set from the start, so 0.1 labels never existed and nothing has to be migrated.
- Plan accuracy is slightly harder to reach than route accuracy for multi-agent cases, because a superset plan counts as wrong under exact match; Jaccard shows how close it was.
