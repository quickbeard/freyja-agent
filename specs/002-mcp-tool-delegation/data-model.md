# Data Model: MCP Tool Delegation

**Feature**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

This feature adds no database table and no stored entity. Everything below is configuration
read at startup, in-memory state held for the life of the process, or an API shape. Threads,
runs, the run lock, and messages are unchanged from
[001's data model](../001-thread-runs-api/data-model.md); the one extended shape is the action
request inside a pending decision.

## Entities

### Server configuration

One entry of `FREYJA_MCP_SERVERS`, keyed by server name (`McpServerConfig` in `config.py`).
The full contract, with examples, is [contracts/server-config.md](contracts/server-config.md).

| Field | Type | Rules |
|---|---|---|
| name (the key) | string | `^[a-z][a-z0-9_-]{0,31}$`; unique by construction. |
| `command` | string or absent | Exactly one of `command` and `url` is present (FR-001, FR-004). |
| `args` | list of string | Default empty. Only with `command`. |
| `pass_env` | list of string | Names of environment variables to hand to the launched process. Only with `command`. Each must be set and not empty when the service starts (FR-002, FR-004). |
| `url` | string or absent | Scheme `http` or `https`; host `localhost` or a loopback IP literal; an IPv4-mapped IPv6 literal is judged by the IPv4 address it carries (FR-001). |
| `read_only` | list of string | Tool names exempt from approval. Default empty. Each must be a tool the server provides (FR-004). |

Unknown fields are refused. Read once at startup; a change takes effect on restart (FR-003).

### Tool server

The running link to one configured server (`ToolServer` in `delegates/mcp_servers.py`).
In memory only; one per configured server per service instance.

| Field | Type | Rules |
|---|---|---|
| `name` | string | The configuration key. |
| `connection` | `command` or `address` | Which form the entry used. |
| `reachable` | boolean | Outcome of the most recent attempt to use the server (FR-014). |
| `checked_at` | timestamp | When that attempt finished. Set at startup, then by every call. |
| `error` | string or null | Redacted reason for the last failure, at most 500 characters, with the server's address removed; null when reachable. |
| `tools` | list of Delegate tool | As listed at startup; never refreshed (FR-003). |

Lifetime: a `command` server is launched at startup and stopped at shutdown, with one
connection for the whole time; an `address` server is connected once at startup to list its
tools and then once per call (FR-005, research R3).

State transitions of `reachable`:

```text
(startup: connected and listed) ──▶ reachable
reachable ──call returned a result or a tool error──▶ reachable
reachable ──call timed out, or the connection failed──▶ unreachable
unreachable ──call returned a result or a tool error──▶ reachable        (address server answering
                                                                          again; launched server
                                                                          whose process is alive)
unreachable ──launched process has exited──▶ unreachable                  (until the service restarts)
```

A server that cannot be connected at startup never gets a status: startup fails (FR-004).

### Delegate tool

A tool provided by a tool server, converted once at startup (`delegates/mcp_tools.py`).

| Field | Type | Rules |
|---|---|---|
| `name` | string | The server's tool name, unchanged. `^[A-Za-z0-9_-]{1,64}$`; unique across all servers and Freyja's own tools (FR-004). |
| `server` | string | The owning tool server's name. |
| `description` | string | From the server, redacted; empty if it gives none. |
| `input_schema` | JSON Schema object | From the server, string values redacted. "No declared input" means no `properties`, or an empty one (FR-009). |
| `server_hints` | object or null | The annotations the server publishes about the tool, redacted. Informational only (FR-006, FR-014). |
| `classification` | `read_only` or `gated` | `read_only` if and only if `name` is in the server's `read_only` list (FR-006). |

### Catalog entry

One tool the orchestrator is offered, as built by `build_agent` (`agent/catalog.py`) and
returned by `GET /tools`. Freyja's own tools and delegate tools share this shape.

| Field | Type | Rules |
|---|---|---|
| `name` | string | Unique in the catalog. |
| `server` | string or null | Null for Freyja's own tools. |
| `description` | string | |
| `input_schema` | JSON Schema object | For Freyja's own tools, the schema of the arguments the model supplies. |
| `classification` | `read_only`, `workspace`, or `gated` | See below. Exactly one per tool (FR-006). |
| `allowed_decisions` | list of `approve`, `edit`, `reject` | Empty unless `gated`. Without `edit` for a tool with no declared input (FR-009). |
| `server_hints` | object or null | Keys `title`, `read_only`, `destructive`, `idempotent`, `open_world`, only those the server set. Null for Freyja's own tools and when the server publishes none. |

### Classification

| Value | Meaning | Assigned to |
|---|---|---|
| `read_only` | Cannot change anything; runs without approval. | A delegate tool named in its server's `read_only` list; Freyja's `ls`, `read_file`, `glob`, `grep`. |
| `workspace` | Changes only the orchestrator's own workspace or delegates to its subagent; runs without approval. | Freyja's `write_file`, `edit_file`, `delete`, `task`. Never a delegate tool. |
| `gated` | May have effects outside the workspace; every call pauses for approve, edit, or reject (FR-007). | Every other delegate tool, including any the configuration does not mention; Freyja's `send_notification`. |

The spec's two-way rule for delegate tools maps to `read_only` and `gated`; its "effectful" is
`gated`. A tool in the compiled agent that is neither a delegate tool nor a row of the fixed
table for Freyja's own tools has no classification, and the service refuses to start
(research R8).

### Catalog

| Field | Type | Rules |
|---|---|---|
| `tools` | list of Catalog entry | Exactly the tools given to the orchestrator at startup (FR-014). Freyja's own first, then each server's in configuration order. |
| `servers` | list of server status | One per configured server, including a server with no tools: `name`, `connection`, `reachable`, `checked_at`, `error`, `tool_count`. |

The tool list is fixed at startup. The server statuses are read from the tool servers when the
catalog is requested; the request contacts no server.

### Action request (extended)

The proposal shape from 001, inside `PendingDecision.action_requests`, in the `interrupt`
event and in thread state.

| Field | Type | Rules |
|---|---|---|
| `name` | string | Unchanged. |
| `args` | object | Unchanged. |
| `description` | string or null | For a delegate tool: `<server>: <first line of the tool's description>`. |
| `allowed_decisions` | list | Unchanged in meaning; approve and reject only for a tool with no declared input. Computed against the current catalog: approve and reject for a tool that is no longer configured, approve only for a tool that is now read-only (FR-019). |
| `server` | string or null | **New.** The tool server the proposed tool belongs to now; null for Freyja's own tools and for a tool that is no longer configured (FR-008, FR-019). |

### Tool result

What the orchestrator receives from a delegate tool call. Stored in the thread as an ordinary
tool message; not a new entity.

| Outcome | Message status | Content |
|---|---|---|
| The server returned a result | `success` | Text and structured data; each non-text part replaced by a note naming its kind (FR-018). |
| The server returned a tool error | `error` | The server's error text. |
| The call exceeded the time limit | `error` | Names the server, the tool, and the limit, and says the outcome is unknown. |
| The server could not be reached | `error` | Names the server and the tool. |

All content passes through redaction of the server's `pass_env` values (FR-002). A call is
attempted once: nothing is retried (FR-012).

## Settings added

| Setting | Variable | Default | Rule |
|---|---|---|---|
| `mcp_servers` | `FREYJA_MCP_SERVERS` | `{}` | JSON object of server configurations. |
| `mcp_call_timeout_seconds` | `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | `30` | Greater than zero. One value for every server (FR-012). |
| `mcp_startup_timeout_seconds` | `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | `20` | Greater than zero. Per server, at startup (SC-004). |

## Relationships

```text
Settings.mcp_servers ──1:1──▶ Tool server ──1:N──▶ Delegate tool ──1:1──▶ Catalog entry
Freyja's own tools (fixed table) ───────────────────────────────1:1──▶ Catalog entry
Catalog entry (gated) ──generates──▶ interrupt configuration for the orchestrator and its subagent
Action request ──names──▶ Catalog entry (by tool name) ──gives──▶ server
```
