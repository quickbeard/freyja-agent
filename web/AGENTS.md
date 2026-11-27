<!-- BEGIN:nextjs-agent-rules -->

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->

## The web app's files

What each section and file of the web app holds. The commands, the conventions, and the map
of the other directories are in the `AGENTS.md` at the root of the repository, and the
end-to-end tests are described in `web/e2e/AGENTS.md`.

- `web/app/layout.tsx` is the
  shell: the sidebar (`web/components/app-sidebar.tsx`) beside the page. `/` redirects to `/tasks`;
  the sidebar lists Tasks, Agents, and Models, in that order.

- `web/app/models/` is the Models
  section, which lists the LLM profiles as cards, and `web/app/agents/` the agents, read from
  `GET /agents` (`web/lib/agents.ts`, `web/components/agents/`).

- The Agents section adds an
  agent and changes a saved agent's address and approval choice (`agent-form.tsx`, which
  keeps nothing once it is sent or closed, asks for confirmation before it sends anything
  that loosens or tightens approval or moves the agent, and sends the version it opened
  with), removes a saved agent behind a confirmation (`remove-dialog.tsx`), says how many
  pending tasks a move or a removal gave up, checks an agent from
  its card (`check-result.tsx`), and warns above the cards about every agent that cannot be
  used (`agents-warning.tsx`). Feature 010 names an agent's approval choice Manual or Auto:
  a saved agent's card switches it behind a confirmation (`mode-dialog.tsx`), and an Auto
  agent's card says when the agents' approval mode holds it to Manual. The sidebar reads the
  same list under the same SWR key (`AGENTS_KEY`) and shows how many agents cannot be used,
  so every page asks for `/api/agents` once. The agents' approval mode is read from that list
  too, as the dropdown that changes it (`web/components/agents-mode.tsx`, which asks before
  Auto), which stands left of the Send button of New task and of a task's follow-up box
  (`send-box.tsx`'s `beside`), and nowhere else.

- `web/app/tasks/` is the Tasks section (feature 007), with the list of tasks under the notice
  that the console has no users (`task-list.tsx`), and `web/app/tasks/[id]/` one task, whose
  id is read inside `<Suspense>`, as Cache Components asks of a route with a parameter; the
  sidebar's section links are read inside one too, or the build fails for that route.

- `web/lib/tasks.ts` holds the task types, the calls, and `events`, which reads a run's
  stream as it arrives; `web/lib/api.ts` has `stream` beside `api` for that answer, and
  gives both `ANSWER_SECONDS`, `api` for the whole answer and `stream` for the answer to
  begin, after which a request counts as one that got none. Both read JSON through
  `web/lib/json.ts` (`readJson`), which keeps a whole number beyond 2^53 as it is written
  where the browser can (`keepsDigits`), so that it is shown and sent back digit for
  digit.

- `web/lib/task-runs.ts` holds the runs this browser tab started, outside React, so that a
  run started by New task on the list's page is shown on the task's page as it arrives
  (`useRun`); nothing is stored. The list of tasks and the sidebar's number of tasks that
  need a decision are one reading of `GET /threads` under `THREADS_KEY`, read again every
  `LIST_SECONDS` by `web/hooks/use-every.ts`, a timer that goes on past a reading that
  failed, and at once by a task's view when a run it shows ends. Every reading of tasks
  takes `READING`, so that a page that was hidden is read as soon as it is shown. So every
  page of the console asks for `/api/threads`.

- `web/components/tasks/` holds New task (`new-task.tsx`),
  the box that sends text (`send-box.tsx`), a task's view (`task-view.tsx`), which shows a
  run as it streams, joining a reply's pieces as the service's own library joins them,
  whether they arrive as text or as blocks (`streamed`, with `web/lib/merge.ts`, a port
  of LangChain's `merge_content`), each action from the piece that names it and with its
  arguments from the model step's `update`, which the stream carries for Freyja's own steps
  and not for a subagent's, and the service's conversation once it ended,
  a message
  (`message.tsx`), the status in words (`status.tsx`), and the proposal card
  (`proposal-card.tsx`), which the view puts last on the page, in the follow-up box's
  place, and scrolls to once per proposal, which offers each proposed action only the
  decisions the service lists for it, sends them with `decide` to `POST /threads/{id}/resume/unattended`, checks
  no more than that edited arguments are a JSON object that it can send as they were
  entered (`parseArguments`) and that a reason is within the service's length, and has its
  buttons outside any form, so that Enter in a field sends no decision. It also holds the
  follow-up box (`follow-up.tsx`), which sends another message on a task through the same
  `send-box.tsx` and is given why it cannot be sent, a run in progress or a pending
  proposal, by the view, and the list of a task's agent tasks (`agent-tasks.tsx`), whose
  `awaited` says whether a task still waits for one, and which shows under a working agent
  task what its agent last reported and how long ago, worded by `web/lib/ago.ts` from the
  browser's time that the view keeps in its state, set by its timer and when a reading
  arrives, never while it renders (feature 008); and who started a task when another
  agent did (`started-by.tsx`, from the row's `caller`), on the list's row, on the
  task's page, which then offers no follow-up box and says why, and on the proposal's card,
  which says that no person asked for the action (feature 009).

- The view reads its task again by one
  timer of its own: every `FOLLOW_SECONDS` while a run it does not read is in progress,
  every `AGENT_SECONDS` while an agent task is awaited, its row alone every
  `FOLLOW_SECONDS` while it reads its own run's stream, so that a stop asked in another
  window is said, and never while the page is hidden. When the reading made at the end of
  its own run still says a run is in progress, as it can while the service gives the lease
  back, it reads once more `AGAIN_SECONDS` later.

- Stop (`stop-button.tsx`) is offered by the view for a run the console started that is in
  progress: a run of this tab once its `start` event arrived, and a run it follows when the
  row says `stoppable`. It sends `stopRun` on a press, never otherwise; the view says
  "stopping" until it learns how the run ended, from the run's last event when it reads the
  stream and otherwise from a reading of the row made after the stop, shows a task as
  stopped only when the row does, and says that the stop came too late when the run ended
  another way (FR-022a). A row names its run (`runs`) and the stop's answer the run it was
  for, so a page that follows by reading says that only of the run it stopped.

- All text of a task is put in
  text nodes and never rendered as markup. The console has no users, and the section says
  so above its list.

- `web/components/models/` holds the list, the card, the dialog that adds a profile or changes one
  (`profile-form.tsx`, which keeps nothing once it is sent or closed), the confirmations before a
  profile is activated (`activate-dialog.tsx`) and deleted (`delete-dialog.tsx`), and how a test's
  result is shown (`test-result.tsx`).

- `web/lib/api.ts` is the one function that calls the service,
  always through `/api`, and its error type; `web/lib/models.ts` holds the profile types and the calls
  that list, add, change, delete, activate, and test. Lists are read with SWR in Client Components.

- shadcn/ui is set up: `components.json`, components under `web/components/ui/`, and the `cn` helper
  in `web/lib/utils.ts`. Generated components need `pnpm format` after they are added, and three
  lint rules they do not meet are off for `web/components/ui/` only (`web/biome.json`).

- `web/next.config.ts` is the one place the web app reads its configuration: `FREYJA_API_URL`, where
  its server forwards `/api/...`, documented in `web/.env.example`; it also holds the redirect.

- Unit tests are `*.test.ts` and `*.test.tsx` files under `web/__tests__/`, which follows the layout of
  the code it covers (`web/__tests__/lib/api.test.ts` covers `web/lib/api.ts`), run by Vitest with
  jsdom; `web/__tests__/testing.ts` stubs `fetch` for them, and `web/__tests__/tasks-testing.ts`
  for the Tasks section's tests, with runs' streams built from events and the list of agents the
  approval mode beside Send reads answered apart (`serve`). No test file sits beside the code.
