# Contract: Tool Server Configuration

**Feature**: [../spec.md](../spec.md) | **Plan**: [../plan.md](../plan.md)

Operators describe tool servers in one environment variable, `FREYJA_MCP_SERVERS`, read through
`Settings` like every other value. Its content is a JSON object mapping a server name to an
entry. The default is `{}`: no servers.

## Shape

```json
{
  "<server name>": {
    "command": "<executable>",
    "args": ["<argument>", "..."],
    "pass_env": ["<ENVIRONMENT_VARIABLE_NAME>", "..."],
    "read_only": ["<tool name>", "..."]
  },
  "<another name>": {
    "url": "http://127.0.0.1:<port>/<path>",
    "read_only": ["<tool name>", "..."]
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| server name | yes | Lowercase letters, digits, hyphen, underscore; starts with a letter; at most 32 characters. Shown in proposals, logs, and the catalog. |
| `command` | one of `command`, `url` | Executable the service launches on this machine. The service starts it at startup and stops it at shutdown. |
| `args` | no | Arguments for `command`. Not allowed with `url`. |
| `pass_env` | no | Names of environment variables copied from the service's environment into the launched process. Not allowed with `url`. A launched process otherwise sees only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`. |
| `url` | one of `command`, `url` | Address of a server already running on this machine, speaking MCP over Streamable HTTP. The operator starts and stops it. |
| `read_only` | no | Names of this server's tools that run without approval. Every tool not listed pauses for approve, edit, or reject. |

## Rules

Each violation stops the service at startup with an error that names the server.

1. Exactly one of `command` and `url` is present.
2. `url` uses `http` or `https`, and its host is `localhost` or a loopback IP address
   (`127.0.0.0/8` or `::1`). No name lookup is performed: `localhost` is accepted as written,
   and any other name is refused even if it would resolve to this machine. A redirect from the
   server to a different host is not followed.
3. No field other than those above is present.
4. Every variable named in `pass_env` is set in the service's environment and is not empty.
5. The server can be launched or reached, and lists its tools, within
   `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` (default 20).
6. Every name in `read_only` is a tool the server provides.
7. Every tool name consists of letters, digits, hyphen, and underscore, is at most 64
   characters, and is unique across all servers and Freyja's own tools.

A server that provides no tools is accepted.

## What decides approval

Only `read_only`. Hints a server publishes about its own tools, such as a read-only label, are
shown in the catalog as the server's claim and never change whether a tool pauses.

## Credentials

Put a credential in the service's environment and name the variable in `pass_env`. Never put a
credential in `args` or in a `url`: both are configuration, and the service does not treat them
as secret. Values of `pass_env` variables are replaced with `***` wherever text from that
server appears: results, errors, logs, and the catalog, including the tool descriptions, input
schemas, and hints the server lists. What a launched server writes to its error output is
captured, redacted the same way, and logged under the server's name. The match is on the exact
value: a value the server encodes or shortens before printing it is not recognized.

The catalog never shows a server's `command`, `args`, `url`, or `pass_env`; it shows only
which of the two forms the server uses.

## Related settings

| Variable | Default | Meaning |
|---|---|---|
| `FREYJA_MCP_CALL_TIMEOUT_SECONDS` | `30` | Time limit for one tool call, including connecting. A call that exceeds it returns an error result; the run continues. |
| `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS` | `20` | Time limit per server for connecting and listing tools at startup. |

## Examples

In `.env`, on one line, in single quotes so the JSON is kept as written.

A launched server with two read-only tools:

```bash
FREYJA_MCP_SERVERS='{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/tmp/freyja-demo-repo"], "read_only": ["git_status", "git_log"]}}'
```

A launched server that needs a credential:

```bash
TRACKER_TOKEN=<token>
FREYJA_MCP_SERVERS='{"tracker": {"command": "tracker-mcp", "pass_env": ["TRACKER_TOKEN"], "read_only": ["search_issues"]}}'
```

A server already running on this machine, every tool gated:

```bash
FREYJA_MCP_SERVERS='{"notes": {"url": "http://127.0.0.1:8765/mcp"}}'
```

Refused at startup:

```bash
FREYJA_MCP_SERVERS='{"a": {"command": "x", "url": "http://127.0.0.1:9/mcp"}}'   # both forms
FREYJA_MCP_SERVERS='{"a": {"read_only": ["x"]}}'                                # neither form
FREYJA_MCP_SERVERS='{"a": {"url": "http://10.0.0.5:8000/mcp"}}'                 # not this machine
```
