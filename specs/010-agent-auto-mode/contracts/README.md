# Contracts of feature 010

The feature adds one route and one field to the Agents API of feature 005, and its contract is
kept with that API's, so that the one file the Agents routes are checked against
(`tests/test_agents_address_nowhere.py`) holds every route of the section:

- `PUT /agents/mode` and the `AgentsMode` schema, in
  [`../../005-web-delegate-agents/contracts/openapi.yaml`](../../005-web-delegate-agents/contracts/openapi.yaml),
  marked "Added by feature 010".
- `mode` in `AgentList`, the answer of `GET /agents`, in the same file.

Nothing else changes shape: an agent's `approval` (`required`, `none`) and an environment
entry's `read_only` keep their names, and the run stream's `approval_now_required` (005,
`contracts/sse-events.md`) is the error a resume ends with when the mode was set to Manual
under a paused step.
