# Tool servers

The orchestrator can also call tools it did not write, provided by [MCP](https://modelcontextprotocol.io) servers on the same machine. At startup the service connects to every server listed in `FREYJA_MCP_SERVERS`, lists its tools, and gives them to the orchestrator beside its own. The full contract is in `specs/002-mcp-tool-delegation/contracts/server-config.md`.

## Try it with the reference git server

The reference [git server](https://github.com/modelcontextprotocol/servers/tree/main/src/git) runs through `uvx`, which comes with `uv`, and needs no account or token. Give it a scratch repository:

```bash
git init -b main /tmp/freyja-demo-repo
git -C /tmp/freyja-demo-repo -c user.name=demo -c user.email=demo@example.invalid \
  commit --allow-empty -m "init"
uvx mcp-server-git --help       # downloads it once, so the first start is inside the startup limit
```

Add one line to `.env` and start the service:

```bash
FREYJA_MCP_SERVERS='{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/tmp/freyja-demo-repo"], "read_only": ["git_status", "git_log", "git_branch"]}}'
```

```bash
uv run --env-file .env freyja-api
# ... Tool server git (command): 14 tools, 3 read-only
```

A read-only tool runs without a pause:

```bash
curl -N -X POST localhost:8000/threads/git-1/runs -H 'content-type: application/json' \
  -d '{"message": "What is the git status of the repository at /tmp/freyja-demo-repo?"}'
```

Any other git tool pauses for a decision. This run ends with an `interrupt` event for `git_create_branch`, and the branch exists only after you approve it as described under **Decide on a proposal** in [Use the API](api.md):

```bash
curl -N -X POST localhost:8000/threads/git-2/runs -H 'content-type: application/json' \
  -d '{"message": "In the repository at /tmp/freyja-demo-repo, create a branch named demo."}'
git -C /tmp/freyja-demo-repo branch
```

Remove the line from `.env` to go back to a service with no tool servers.

## Describe a server

`FREYJA_MCP_SERVERS` is one JSON object on one line, in single quotes, mapping a server name (lowercase letters, digits, hyphen, underscore; starting with a letter; at most 32 characters) to an entry in one of two forms:

- **Launched**: `command` and optional `args`. The service starts the process at startup over standard input and output, keeps one connection to it, and stops it at shutdown. Nothing relaunches it if it exits; restart the service to bring it back. What it writes to its error output is logged under the server's name.
- **Address**: `url`, an `http` or `https` address of a server you run yourself, speaking MCP over Streamable HTTP. The host must be `localhost` or a loopback address; any other name or address is refused without a lookup, and a redirect is not followed. The service connects once per call, so a server that comes back is usable again without a restart, and it never stops a server it did not launch.

```bash
FREYJA_MCP_SERVERS='{"notes": {"url": "http://127.0.0.1:8765/mcp", "read_only": ["search"]}}'
```

**`read_only` is the only thing that decides approval.** A tool listed there runs without a pause. Every other tool of that server, including one the list does not mention and one the server labels read-only itself, pauses with approve, edit, and reject exactly like `send_notification`, on the orchestrator and on its subagent. An edit is validated against the input schema the server declared for the tool; a tool that declares no input allows approve and reject only.

**Credentials** go in the service's environment, never in `args` or a `url`. Name the variable in `pass_env` and the launched process receives it; otherwise it sees only `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM`, and `USER`. The value is replaced with `***` in everything that comes from that server: results, errors, logs, and the catalog.

```bash
TRACKER_TOKEN=<token>
FREYJA_MCP_SERVERS='{"tracker": {"command": "tracker-mcp", "pass_env": ["TRACKER_TOKEN"], "read_only": ["search_issues"]}}'
```

## When a server fails

At startup, a configuration the service cannot use stops it with an error that names the server, and nothing listens: both or neither of `command` and `url`, an unknown field, an address not on this machine, a `pass_env` variable that is unset or empty, a server that cannot be launched or reached or does not list its tools within `FREYJA_MCP_STARTUP_TIMEOUT_SECONDS`, a `read_only` name the server does not provide, or a tool name that is invalid or already taken by another server or by Freyja's own tools. A server with no tools is accepted.

During a run, a failing call becomes an error result for the orchestrator and the run goes on; the stream still ends normally and the thread's run lease is unaffected. A call that takes longer than `FREYJA_MCP_CALL_TIMEOUT_SECONDS` is abandoned and its result says the effect may or may not have happened. A server that cannot be reached gives an error result saying so. Each failure logs one warning naming the server, the tool, and `timeout`, `unreachable`, or `tool_error`. No call is ever retried: an approval authorizes one attempt.

## Read the catalog

`GET /tools` lists every tool the orchestrator has and the status of every server. It never contacts a server. Before it answers it reads two rows of the database, to learn whether the agents or the active LLM profile changed (see [Agents in the catalog](agents.md#agents-in-the-catalog)); the tool servers' tools are the ones listed at startup.

```bash
curl -s localhost:8000/tools | jq
```

Each tool has its `name`, its `server` (`null` for Freyja's own tools), `description`, `input_schema`, `classification`, `allowed_decisions`, and `server_hints`. The classification is `read_only` (runs without a pause), `workspace` (Freyja's own file-writing and subagent tools, which change only the workspace), or `gated` (every call pauses). `server_hints` are what the server says about its own tool, such as a read-only label; they are shown as its claim and never change the classification. Each server has its `connection` form (`command` or `address`), `tool_count`, and `reachable`, `checked_at`, and `error` from the most recent attempt to use it, at startup or in a call. The catalog never shows a server's command, arguments, address, or variable names.

A proposal for a server tool names its server: each entry of `action_requests` in the `interrupt` event and in the thread state's `pending` has `server` set to the server's name, and `null` for Freyja's own tools.

**Next:** [Agents](agents.md): handing work to A2A agents.
