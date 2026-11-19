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
through the same factory the API uses. The web app under `web/` is a client of that API and
nothing more: it MUST NOT hold a credential for a model or a delegate, call a model or a
delegate itself, or carry logic that decides what the orchestrator does.

Rationale: a bug that reproduces only in production cannot be fixed with a tool that runs
a different runtime; parity is cheaper than a second mental model.

### IV. Configuration Through Settings

Deployment configuration is what a deployment needs before it can start, such as addresses,
ports, connection strings, limits, and secrets. It MUST be read through the `Settings` class
from `FREYJA_*` environment variables. Hosts, ports, connection strings, and model
identifiers MUST NOT be hardcoded in application code. Defaults MUST be the safe local
choice; the server binds `127.0.0.1` unless configured otherwise. Secrets MUST never be
committed, and every variable MUST be documented in `.env.example`.

The web app under `web/` has no `Settings` class. Its deployment configuration MUST be read
from `FREYJA_*` environment variables in one place, `web/next.config.ts`, under the same
rules: nothing hardcoded elsewhere, the safe local choice as the default, and every variable
documented in `web/.env.example`.

Operator-managed configuration is what an operator changes while the service runs, such as
the model the orchestrator uses and the delegate agents it may hand work to. It MUST be kept
in the service's database and changed only through the service's API. What the environment
configures of the same kind stays valid as a read-only default, so a deployment configured
only through the environment behaves as it did before. A secret an operator enters, such as a
model provider's API key, MUST be stored encrypted under a key that comes from the
environment, and MUST never be returned by the API, logged, or written to a thread.

Rationale: the same image runs everywhere, and an unauthenticated API bound to all
interfaces on a developer machine is an exposure. Changing a model or a delegate should not
mean editing a file and restarting a service that has runs in progress.

### V. Automated Quality Gates

`ruff check` and `ruff format --check` MUST pass, enforced by the pre-commit hook, and
`pytest` MUST pass before merge. For the web app under `web/`, `biome check` MUST pass,
enforced by the same hook, and its unit tests (Vitest) MUST pass before merge. New behavior
MUST ship with tests. The `S` (security) and
`ASYNC` rule sets are mandatory; blocking I/O inside an async handler is a defect, not a
style issue. Rule suppressions MUST carry a reason and SHOULD be removed when the cause is
fixed.

gitleaks MUST find no secret in the repository, enforced by the same hook over what is
staged and in CI over every commit. A made-up key that a test needs MUST be let through by
its exact value, never by its path.

Trivy MUST report no known vulnerability of HIGH or CRITICAL severity in what the lock files
install, in a Dockerfile, or in a container image built from one, and Semgrep MUST report no
finding; both run in CI. A finding that is wrong MUST be suppressed where it stands, with its
reason, never by leaving a file or a rule out of the scan.

Each test suite MUST meet its coverage floor, enforced by the hook and in CI: the service's
in `pyproject.toml`, the web app's in `web/vitest.config.mts`. A floor is measured as CI
runs the suite and counts all of the project's own code. It SHOULD be raised as coverage
rises, and MUST NOT be lowered without the reason written beside it.

Rationale: the gates are cheap, already automated, and catch the two defect classes that
matter most here, insecure calls and stalled event loops. A secret that was committed stays
in the history after the file is fixed, so it has to be stopped before the commit. The
deploy pipeline refuses a vulnerable dependency or image, and it is cheaper to learn that
on the pull request. A floor keeps untested code from adding up one change at a time; it
shows what no test runs, not that what runs is checked, so it adds to the rule that new
behavior ships with tests and does not replace it.

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
- Ruff is the only linter and formatter for Python, line length 100, configuration in
  `pyproject.toml`.
- The web app lives under `web/`: Next.js with TypeScript, dependencies managed by pnpm with a
  committed `pnpm-lock.yaml`. Biome is its only linter and formatter, configuration in
  `web/biome.json`, and Vitest runs its unit tests.
- gitleaks scans for secrets, Trivy for known vulnerabilities and for mistakes in the
  Dockerfiles, and Semgrep for insecure code. Each runs at one named version wherever it
  runs.
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

**Version**: 1.5.0 | **Ratified**: 2026-10-28 | **Last Amended**: 2026-11-19
