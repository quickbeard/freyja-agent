# `web/e2e/`

The web app's end-to-end tests, file by file. The commands, the conventions, and the map of the
other directories are in the `AGENTS.md` at the root of the repository.

- Playwright reads nothing outside this directory, and Vitest is
  told to skip it (`web/vitest.config.mts`), so neither runner picks up the other's files.

- `console.spec.ts` covers the shell, `models.spec.ts` the Models section as it is read,
  `profiles.spec.ts` adding, activating, testing, changing, and deleting, `agents.spec.ts`
  the Agents section as it is read, its warnings, and that an agent's address stays in it, and `agents-manage.spec.ts` adding an
  agent, checking one, changing one's approval choice, moving one, and removing one;

- `agents-mode.spec.ts` the agents' approval mode beside the Tasks section's Send buttons and
  an agent's own mode on its card (feature 010);

- `tasks.spec.ts` the Tasks section, and that a page of another origin cannot show it in a
  frame, `tasks-decide.spec.ts` deciding a proposal, and
  `tasks-run.spec.ts` follow-ups, agent tasks, a run the page does not read, a page closed
  during a run, and Stop (it moves the page's clock with `page.clock` in place of waiting
  five or fifteen seconds), `tasks-progress.spec.ts` what a working agent last reported, its
  age, and its box (feature 008), `tasks-callers.spec.ts` the tasks other agents started
  (feature 009), with
  `tasks-api.ts` (`TaskService`, which answers a page's requests for tasks, its runs, and
  its decisions and keeps what each left, `pausedTask` and `continued` for a proposal and
  its decision, `followedUp` and `agentTask` for a follow-up and an agent task,
  `runningTask` and `goesOn` for a run in progress and one whose stream stops, and
  `answerThreads`; it answers a stop as the service rules it and keeps each in `stops`); and
  `stream.spec.ts`, the one spec that lets a page's requests through the app's forwarding,
  to a stand-in for the service on `STAND_IN_PORT` (3109, the address the app under test
  forwards to), to see a run's events arrive as they are produced, and that the forwarding
  passes on the three headers the service's refusal of other origins reads, and replaces an
  `X-Forwarded-Host` a page wrote with the name the console was asked under, which the
  service's refusal of unknown host names rests on. It runs in projects of
  its own, `stream-chromium` and then `stream-firefox`, after every other spec;

- `api.ts` holds what the service would answer (`answerProfiles` and `answerAgents` for a list,
  `ProfileService` and `AgentService` for a page that changes profiles or agents, which keep
  what the page saved) and the locators the specs share.

- The app under test forwards `/api/...` to an address nothing listens on, so a test that
  hands a page no answer sees the console as it is with the service stopped; only
  `stream.spec.ts` listens there, in its own projects.
