# Quickstart: MCP Tool Delegation

Validation guide for the feature. Each scenario maps to a user story in [spec.md](spec.md). The
catalog and proposal shapes are in [contracts/openapi.yaml](contracts/openapi.yaml), the server
list in [contracts/server-config.md](contracts/server-config.md), and the entities in
[data-model.md](data-model.md). The thread endpoints and their events are unchanged from
[001's contracts](../001-thread-runs-api/contracts/).

## Prerequisites

- Everything the [001 quickstart](../001-thread-runs-api/quickstart.md) needs: Docker with
  Compose v2, `uv`, and a model provider key in `.env`.
- `git`, `jq`, and `uvx` on the `PATH` of the shell that starts the service. `uvx` is installed with
  `uv`. Use its absolute path as `command` if the service cannot find it.
- No account, token, or Node.js. Nothing below edits a credential (SC-006).

## Setup

```bash
uv sync
docker compose up -d postgres

# A scratch repository for the git server to work on.
git init -b main /tmp/freyja-demo-repo
git -C /tmp/freyja-demo-repo -c user.name=demo -c user.email=demo@example.invalid \
  commit --allow-empty -m "init"

# Download the reference git server once, so the first start is inside the startup limit.
uvx mcp-server-git --help
```

Add one line to `.env`:

```bash
FREYJA_MCP_SERVERS='{"git": {"command": "uvx", "args": ["mcp-server-git", "--repository", "/tmp/freyja-demo-repo"], "read_only": ["git_status", "git_log", "git_branch"]}}'
```

Start the service:

```bash
uv run --env-file .env freyja-api
```

Expected: after the persistence line, one line names the server `git`, its connection form
`command`, the number of tools it provided, and that three are read-only. Then the service
listens on 127.0.0.1:8000 (User Story 1, scenario 1).

## Scenario 1: read the catalog (User Story 3)

```bash
curl -s localhost:8000/tools | jq
```

Expected:

- `servers` has one entry: `git`, `connection: command`, `reachable: true`, `error: null`.
- `tools` lists Freyja's own tools first. `ls`, `read_file`, `glob`, and `grep` are
  `read_only`; `write_file`, `edit_file`, `delete`, and `task` are `workspace`;
  `send_notification` is `gated` with `server: null`.
- `git_status`, `git_log`, and `git_branch` are `read_only` with `server: git`. Every other
  git tool is `gated`.
- `git_diff` is `gated` although its `server_hints` say `read_only: true`: the hint is shown
  and ignored (User Story 2, scenario 6; User Story 3, scenario 4).
- Every tool has an `input_schema`; `git_status` requires `repo_path`.

## Scenario 2: a read-only tool runs without a pause (User Story 1)

```bash
curl -N -X POST localhost:8000/threads/mcp-1/runs \
  -H 'content-type: application/json' \
  -d '{"message": "What is the git status of the repository at /tmp/freyja-demo-repo?"}'
```

Expected: `update` events show the `git_status` call and its result, no `interrupt` event
appears, the stream ends with `event: end`, and the reply reports a clean working tree on
`main`.

A tool error is a result, not a failure of the run:

```bash
curl -N -X POST localhost:8000/threads/mcp-2/runs \
  -H 'content-type: application/json' \
  -d '{"message": "What is the git status of the repository at /etc?"}'
```

Expected: the tool result has an error status and says the path is outside the allowed
repository, the stream still ends with `event: end`, and the reply says the lookup failed
(scenario 4). The service log has a warning naming server `git` and tool `git_status`.

## Scenario 3: an effectful tool waits for a decision (User Story 2)

```bash
curl -N -X POST localhost:8000/threads/mcp-3/runs \
  -H 'content-type: application/json' \
  -d '{"message": "In the repository at /tmp/freyja-demo-repo, create a branch named demo-approve."}'
```

Expected: the stream ends with `event: interrupt`. Its action request has
`name: git_create_branch`, `server: git`, the proposed `args`, and
`allowed_decisions: [approve, edit, reject]`. `git -C /tmp/freyja-demo-repo branch` does not
list the branch yet. `GET /threads/mcp-3/state` shows the same proposal under `pending`.

Approve:

```bash
curl -N -X POST localhost:8000/threads/mcp-3/resume \
  -H 'content-type: application/json' -d '{"decisions": [{"type": "approve"}]}'
```

Expected: the continuation shows the tool result and ends with `event: end`;
`git -C /tmp/freyja-demo-repo branch` now lists `demo-approve`.

Edit, on a new thread (`mcp-4`, same request with `demo-edit`). First an edit the tool's
declared input does not accept:

```bash
curl -s -X POST localhost:8000/threads/mcp-4/resume -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "edit", "args": {"repo_path": "/tmp/freyja-demo-repo", "branch_name": 7}}]}'
```

Expected: HTTP 422 whose error location ends in `args`, `branch_name`; the proposal is still
pending in state and no branch was created. Then a valid edit:

```bash
curl -N -X POST localhost:8000/threads/mcp-4/resume -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "edit", "args": {"repo_path": "/tmp/freyja-demo-repo", "branch_name": "demo-edited"}}]}'
```

Expected: `demo-edited` exists and `demo-edit` does not.

Reject, on a new thread (`mcp-5`, same request with `demo-reject`):

```bash
curl -N -X POST localhost:8000/threads/mcp-5/resume -H 'content-type: application/json' \
  -d '{"decisions": [{"type": "reject", "message": "Not on this repository."}]}'
```

Expected: no branch is created; the reply says the action was not taken and repeats the reason.

A server-labelled read-only tool that the configuration does not list still pauses:

```bash
curl -N -X POST localhost:8000/threads/mcp-6/runs \
  -H 'content-type: application/json' \
  -d '{"message": "Show the git diff between main and demo-approve in /tmp/freyja-demo-repo."}'
```

Expected: `event: interrupt` for `git_diff` (scenarios 5 and 6).

## Scenario 4: a misbehaving server is obvious (User Story 4)

Stop the service before each configuration change. Each of these fails at startup, names the
server, and leaves nothing listening on port 8000:

| Change to the `git` entry | Expected error names |
|---|---|
| `"command": "no-such-command"` | server `git`, could not be started |
| add `"mcp_typo"` to `read_only` | server `git` and tool `mcp_typo` |
| add `"url": "http://127.0.0.1:9/mcp"` next to `command` | server `git`, exactly one form |
| replace `command` and `args` with `"url": "http://10.0.0.5:8000/mcp"` | server `git`, address not on this machine |
| replace `command` and `args` with `"url": "http://127.0.0.1:9/mcp"` | server `git`, could not be reached |

Restore the working entry and start the service. Then end the launched server while the
service runs:

```bash
pkill -f mcp-server-git
curl -N -X POST localhost:8000/threads/mcp-7/runs \
  -H 'content-type: application/json' \
  -d '{"message": "What is the git status of the repository at /tmp/freyja-demo-repo?"}'
curl -s localhost:8000/tools | jq '.servers'
```

Expected: the tool result is an error saying server `git` could not be reached, the stream
ends with `event: end`, the log has a warning naming `git` and `git_status`, and the catalog
shows `git` with `reachable: false` and a reason. A second run fails the same way: the service
does not relaunch the command. Restart the service to bring the server back.

## Scenario 5: a server reached by address, and recovery (User Story 4)

In a second terminal, serve the suite's stub over loopback HTTP:

```bash
uv run python -m tests.mcp_stub --http 8765
```

Set the server list to the address form and start the service:

```bash
FREYJA_MCP_SERVERS='{"stub": {"url": "http://127.0.0.1:8765/mcp", "read_only": ["lookup"]}}'
```

Expected: the startup line names `stub` with connection form `address`; the catalog lists its
tools with `lookup` as `read_only`.

1. Ask "Use the lookup tool to look up the key alpha." Expected: the run completes without a
   pause.
2. Stop the stub with Ctrl-C and ask again on a new thread. Expected: an error tool result,
   `event: end`, and `reachable: false` for `stub` in the catalog.
3. Start the stub again and ask once more. Expected: the run completes without restarting
   the service, and the catalog shows `reachable: true` (scenario 6).
4. Stop the service. Expected: the stub keeps running; the service does not stop a server it
   did not launch.

## Scenario 6: the suite and the gates (User Story 5)

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run pre-commit run --all-files
```

Expected: all pass with the network disconnected and with no MCP server installed. The
default suite connects to a stub server inside the test process; two transport tests launch
that stub over pipes and over the loopback interface (SC-007).

## Cleanup

```bash
rm -rf /tmp/freyja-demo-repo
```

Remove the `FREYJA_MCP_SERVERS` line from `.env` to return to a service with no tool servers.
