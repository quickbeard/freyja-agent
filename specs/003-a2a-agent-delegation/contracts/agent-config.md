# Contract: Agent Configuration

`FREYJA_A2A_AGENTS` names the A2A agents the orchestrator can hand work to. It is one JSON object
from agent name to entry. Empty, the default, means no agents. Read once at startup; a change
takes effect on restart.

## Shape

```json
{
  "<name>": {
    "url": "http://127.0.0.1:9999",
    "read_only": false
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `url` | yes | Where the agent runs on this machine. Freyja reads the card from `<url>/.well-known/agent-card.json` and sends every request to this scheme, host, and port. |
| `read_only` | no, default `false` | `true`: hand-offs, answers, and cancels to this agent run without approval. `false`: every one of them pauses for a decision. |

The name is shown in proposals, logs, and the catalog, and names the agent's two tools:
`handoff_to_<name>` and `cancel_<name>_task`.

## Rules

Refused when settings are read, before anything starts, with an error naming the entry's key
and never repeating its value:

- the name does not match `^[a-z][a-z0-9_-]{0,31}$`;
- `url` is missing, not `http` or `https`, or its host is neither `localhost` nor a loopback IP
  literal (`127.0.0.0/8`, `::1`, or an IPv4-mapped form of a loopback address);
- the entry has a field not listed above.

Refused while agents start, with an error beginning `Agent '<name>'`, and the service does not
serve:

- the name is also a tool server name in `FREYJA_MCP_SERVERS`;
- the card cannot be read within `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS`, or reading it is
  redirected;
- the card requires authentication (it declares security requirements or schemes);
- the card lists default input modes and none of them is text;
- the card has no JSON-RPC interface;
- one of the agent's two tool names is already taken by another tool.

What the card says about where the agent is reachable is not used: only the path of its
JSON-RPC interface is kept, joined to the configured `url`.

## Examples

The reference agent of the quickstart, gated:

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999"}}'
```

Two agents, one trusted with unreviewed hand-offs:

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://127.0.0.1:9999", "read_only": true}, "stub": {"url": "http://localhost:9998"}}'
```

Refused at settings validation (address not on this machine):

```bash
FREYJA_A2A_AGENTS='{"hello": {"url": "http://10.0.0.5:9999"}}'
```

## Related settings

| Setting | Default | Meaning |
|---|---|---|
| `FREYJA_A2A_CALL_TIMEOUT_SECONDS` | `30` | How long a hand-off, answer, or cancel waits within a run. A task still working then becomes pending. |
| `FREYJA_A2A_STARTUP_TIMEOUT_SECONDS` | `20` | Per agent, for reading its card at startup. |
| `FREYJA_A2A_POLL_INTERVAL_SECONDS` | `10` | How often pending tasks are checked and outcomes delivered. |
| `FREYJA_A2A_TASK_MAX_AGE_SECONDS` | `86400` | When a pending task is given up. |
| `FREYJA_BACKGROUND_RUN_ATTEMPTS` | `3` | Attempts for one background run, the first included. |
| `FREYJA_BACKGROUND_RETRY_SECONDS` | `60` | Wait between background run attempts. |

All must be greater than zero.
