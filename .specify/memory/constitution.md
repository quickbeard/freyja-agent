# Freyja Constitution

## Core Principles

### I. Human Approval for Environment-Changing Actions (NON-NEGOTIABLE)

Any tool with effects outside the agent's workspace, such as sending a message, changing
a record in an external system, deleting data, or spending money, MUST be gated by a
human-in-the-loop interrupt with approve, edit, and reject decisions. No code path MAY
bypass the gate, including delegate agents and background runs. A paused run MUST be
resumable on the same thread after any delay, so a person can decide hours or days later
with full context. Read-only lookups, retrieval, and edits inside the workspace do not
require approval.

Rationale: the orchestrator acts on systems other people depend on, where a wrong action
can be visible to others and may be irreversible.

### II. Protocol-Based Delegation

Delegate agents and services MUST be integrated over protocols: A2A for agents that carry
their own reasoning, MCP for collections of tools, plain HTTP for services without agents.
Another agent framework MUST NOT be imported into the orchestrator process. Each delegate
stays owned by its team; the orchestrator owns routing, a single user-facing voice,
hand-off context, and approval. Deterministic pipelines stay deterministic and are called
as tools rather than wrapped in a model loop.

Rationale: the organization's projects span LangGraph, AWS Strands, Agno, and plain
services. Protocol boundaries keep dependency trees, model clients, and ownership separate.

### III. One Runtime, Every Environment

The FastAPI application with a Postgres checkpointer and store is the only runtime, for
local development, staging, and production. LangGraph Server and Studio MUST NOT be used.
Differences between environments MUST come from configuration alone, never from a second
code path. Debugging tools, such as the debug stream mode or a CLI, MUST build the agent
through the same factory the API uses.

Rationale: a bug that reproduces only in production cannot be fixed with a tool that runs
a different runtime; parity is cheaper than a second mental model.

### IV. Configuration Through Settings

All runtime configuration MUST be read through the `Settings` class from `FREYJA_*`
environment variables. Hosts, ports, connection strings, and model identifiers MUST NOT be
hardcoded in application code. Defaults MUST be the safe local choice; the server binds
`127.0.0.1` unless configured otherwise. Secrets MUST never be committed, and every
variable MUST be documented in `.env.example`.

Rationale: the same image runs everywhere, and an unauthenticated API bound to all
interfaces on a developer machine is an exposure.

### V. Automated Quality Gates

`ruff check` and `ruff format --check` MUST pass, enforced by the pre-commit hook, and
`pytest` MUST pass before merge. New behavior MUST ship with tests. The `S` (security) and
`ASYNC` rule sets are mandatory; blocking I/O inside an async handler is a defect, not a
style issue. Rule suppressions MUST carry a reason and SHOULD be removed when the cause is
fixed.

Rationale: the gates are cheap, already automated, and catch the two defect classes that
matter most here, insecure calls and stalled event loops.

### VI. Deterministic Guardrails Before Model Judgment

Safety-critical handling MUST NOT depend on model routing. A condition that must never be
missed, such as a hard policy rule or an input that must always be refused or escalated,
MUST be handled by a deterministic rule that runs before, or independently of, the
orchestrator's reasoning. Model judgment is for ambiguous, open-ended work; rules are for
cases where a miss is unacceptable.

Rationale: an LLM router can be wrong or slow. The cases that cannot tolerate either must
not wait on it.

## Technology Constraints

- Python 3.12 or newer, dependencies managed by uv with a committed `uv.lock`, src layout.
- Orchestrator built on Deep Agents over LangGraph; served by FastAPI; persistence through
  `langgraph-checkpoint-postgres`; streaming over server-sent events via `sse-starlette`.
- Model selected by a provider-prefixed string in settings, default
  `anthropic:claude-sonnet-5-5`.
- Ruff is the only linter and formatter, line length 100, configuration in
  `pyproject.toml`.
- Agent skills under `workspace/skills/` are source and are committed; working files written at
  runtime under `workspace/cases/` are not.

## Development Workflow

- Work happens on a branch and merges to `main` through a pull request.
- Every clone installs the pre-commit hook once; a commit that fails the hook is not pushed.
- Features follow the Spec Kit flow: specify, plan, tasks, implement, converge, with the
  optional clarify, checklist, and analyze gates. Each plan's Constitution Check MUST name
  the principles it touches and justify any deviation.
- Commit messages use the `type: summary` form already in the history (`chore:`, `feat:`).
- The README documents only what exists in the repository, never planned work.

## Governance

This constitution supersedes other practices in the repository. An amendment is a pull
request that edits this file, bumps the version by semantic versioning (MAJOR for removing
or redefining a principle, MINOR for adding one or materially expanding guidance, PATCH for
wording), and updates the Last Amended date. Reviewers MUST check pull requests for
compliance with the principles above, and added complexity MUST be justified in the plan.
Spec Kit artifacts live under `specs/` and are reviewed with the code they describe.

**Version**: 1.1.0 | **Ratified**: 2026-10-28 | **Last Amended**: 2026-10-29
