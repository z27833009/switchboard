# Switchboard: working agreements for coding agents

This file is the single source of rules for any coding agent (Codex, Claude Code, Cursor, Gemini CLI or others). `CLAUDE.md` only imports it.

## Read first

1. This file.
2. [docs/spec/progress.md](docs/spec/progress.md): what is done and what is next.
3. Your task's row in [docs/spec/tasks.md](docs/spec/tasks.md), including its tags, verification and AC ids.
4. The sections of [docs/spec/design.md](docs/spec/design.md) and the [ADRs](docs/adr/) the task touches. The acceptance criteria are in [docs/spec/requirements.md](docs/spec/requirements.md).

## Session protocol

1. **Pick one task.** The one the author names, otherwise the first `todo` task in progress.md whose dependencies (tasks.md, "Dependencies") are `done`. One task per session.
2. **Check tags.** `[human]`: do not write the content; offer a template or validator and stop. `[host]`: do everything that does not need the author's machine, then stop and list the exact commands for the author to run and what output to report back.
3. **Branch** `task/<id>-<short-slug>`, for example `task/T1.04-core-models`.
4. **Build to the spec.** If the spec is wrong, ambiguous or impossible, stop and explain; do not silently diverge. A deliberate change to an interface updates design.md section 4 and the relevant ADR in the same commit.
5. **Verify** with the task's verification step and paste its output into the commit message body or PR description.
6. **Record** the task as `done` in progress.md with the commit, in the same commit as the work. Add notes a later session needs (measured numbers, decisions taken).
7. **Stop.** Do not start the next task.

## Rules

- Conversation with the author is in Chinese; all files, comments, commit messages and docs are in English.
- No em dashes anywhere. Use commas, colons or parentheses.
- Clean-room: never reproduce code, prompts, configs or data from any employer. Public datasets and self-written content only.
- No invented numbers. A metric is a `target` until a report under `evals/reports/` backs it. Measured facts (row counts, VRAM, latency) come from a run, never from memory.
- Package versions come from the package index on the day they are pinned, never from memory (design.md section 21).
- `src/switchboard/core` imports only the standard library and pydantic. If a change needs a framework import there, the design is wrong; stop and discuss.
- Every call across a process or network boundary creates an OpenTelemetry span with the attributes listed in design.md section 12. Use the OTel API from the first boundary call; it is a no-op until the exporter exists (T1.12).
- Agents, tools, providers and suites are registered, never hard-coded in core or the orchestrator (ADR 0011).
- Tools are read-only. Do not add a write tool without an ADR and a human-in-the-loop hook.
- The tool server takes the tenant only from the signed run token (ADR 0014).
- Eval datasets are written or reviewed by the author before the prompts they test. Do not edit a dataset version that a report references; create a new version.
- Do not add dependencies beyond the allowlist in design.md section 21 without saying why in the commit message and adding them to that section.
- Describe the project as source-available, not open source (ADR 0009).
- Commit messages start with the task id: `T1.04: core models, plan validation and context policy`.

## Environment

- Development machine: Windows 11, Docker Desktop, LM Studio on the host (reached from containers as `host.docker.internal`), 8 GB laptop GPU. Local models are listed in design.md section 8.1.
- Secrets live in `.env` (git-ignored); `.env.example` lists every variable.

## Commands (target, once code exists)

- `uv run pytest` runs unit and contract tests.
- `uv run switchboard eval run --suite routing --provider mock` runs the smoke eval.
- `docker compose up` starts the full stack.
