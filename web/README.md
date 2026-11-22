# Freyja web console

The console of [Freyja](../README.md): a [Next.js](https://nextjs.org) app through which a person changes the service's configuration, hands Freyja tasks, and decides the actions that need approval, all through the service's API. It is a client of that API and nothing more. It holds no key for a model or an agent, calls neither itself, and decides nothing the orchestrator does.

Like the API, the console has no authentication, and it has no users: whoever can reach it can change the service's configuration, sees every task, and can decide every proposal. Nothing records who decided.

## Setup

The console needs Node and [pnpm](https://pnpm.io). `package.json` names the pnpm version (`packageManager`); the container image is built with Node 26, and CI runs on the current LTS.

```bash
pnpm install                   # dependencies, from pnpm-lock.yaml
```

## Run

Start the Freyja service first (see the [root README](../README.md#run-the-dev-server)), then:

```bash
pnpm dev                       # http://localhost:3000
```

Open <http://localhost:3000>, by that name: in development Next.js serves its dev-only assets to `localhost` only, so the page does not load fully when opened as `127.0.0.1`. The address changes to `/llm`, the first section.

Without the service the console still starts: each section says that the service cannot be reached and offers to try again.

To run a production build instead:

```bash
pnpm build
pnpm start                     # http://localhost:3000
```

## Configuration

The console's deployment configuration is read in one place, `next.config.ts`, and documented in `.env.example`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `FREYJA_API_URL` | `http://127.0.0.1:8000` | Where the console's server forwards `/api/...`: the Freyja service. |
| `FREYJA_WEB_STANDALONE` | unset | Set to `1` by the build of the container image alone. `pnpm build` then writes a standalone server under `.next/standalone`, which `pnpm start` is not meant to serve. |

Set `FREYJA_API_URL` in the shell, or in `.env.local`, which Next.js loads and git ignores. `pnpm dev` reads it when it starts. `pnpm build` writes it into the build and `pnpm start` does not read it again, so a built app is built again to change the address.

The variable has no `NEXT_PUBLIC_` prefix, because the browser never learns the service's address: it only ever calls the console's own origin, and the console's server passes `/api/...` on.

## Layout

- `app/`: the pages. `layout.tsx` is the shell, the sidebar beside the page; `llm/page.tsx` and `agents/page.tsx` are two sections, and `tasks/page.tsx` and `tasks/[id]/page.tsx` the Tasks section and one task (feature 007). A task's page reads its id from its address inside `<Suspense>`, which Cache Components asks for; for the same reason the sidebar's links, which mark the section in use, are read inside one too.
- `components/`: `app-sidebar.tsx`, the LLM section under `llm/` (the list, the card, the form that adds or changes a profile, the confirmations before a profile is activated or deleted, and how a test's result is shown), and the Agents section under `agents/` (the list, the card with its Check, how a check's result is shown, the warning above the cards, the form that adds an agent or changes a saved agent's address and approval choice, with the confirmation it asks for first, and the dialog that removes one), and the Tasks section under `tasks/` (the list of tasks, New task, a task's view, a message, the status, the box that sends text, the proposal card, which offers each action the decisions the service allows and sends them through `decide` in `lib/tasks.ts`, the follow-up box, which sends another message on a task and cannot be sent while a run is in progress or a proposal is pending, the list of a task's agent tasks, and Stop, a button outside any form, which asks the service to stop a run the console started through `stopRun` in `lib/tasks.ts` and says in words that the run is stopping until the service says it has ended). A task's view reads its task again by a timer of its own: every four seconds while a run it does not read is in progress, its row alone every four seconds while it reads its own run's stream, so that a stop asked in another window is said, and every twelve while an agent task is pending or its outcome is not yet in the conversation, each shorter than the five and fifteen seconds the spec gives as the most, since a reading takes time of its own. The sidebar reads the list of agents too, under the same key as the section, and shows how many cannot be used; it reads the list of tasks under the key the Tasks section reads it under, and shows how many tasks need a decision. Both lists of tasks are read again every ten seconds by a timer of their own (`hooks/use-every.ts`), which goes on past a reading that failed, where SWR's `refreshInterval` would stop. A page that was hidden reads its tasks as soon as it is shown (`READING` in `lib/tasks.ts`), where SWR would wait five seconds after the page was opened.
- `components/ui/`: the [shadcn/ui](https://ui.shadcn.com) components, generated and not edited by hand. `components.json` is shadcn's configuration.
- `lib/`: `api.ts` is the one place that calls the service, always through `/api`: `api` for an answer of JSON, `stream` for a run's events, and their error type `ApiError`. `llm.ts`, `agents.ts`, and `tasks.ts` hold the types and the calls of their sections; `tasks.ts` also reads a run's events as they arrive. `task-runs.ts` holds the runs this browser tab started, so that a run begun from the list's page is shown on the task's page as it arrives; nothing is stored, and a reload forgets it. `json.ts` reads the service's JSON with a whole number beyond 2^53 kept as it is written, where the browser can. `merge.ts` joins the pieces of a reply as the service's own library does, so that what streams is what the task holds once the run has ended. `utils.ts` is the `cn` helper.
- `hooks/`: `use-mobile.ts`, the sidebar's width check, and `use-every.ts`, the timer the list of tasks is read again by.
- `__tests__/`: the unit tests. `e2e/`: the end-to-end tests.
- `next.config.ts`: the configuration above, the `/api` forwarding, the redirect, and the frame headers.

Lists are read in the browser with [SWR](https://swr.vercel.app), in Client Components. A read that fails is shown with a way to try again, and is not retried on its own, apart from the list of tasks and a task that is followed, which are read again on their timers.

To add a shadcn/ui component, generate it and then format it; three lint rules the generated code does not meet are off for `components/ui/` only (`biome.json`).

```bash
pnpm exec shadcn add <component>
pnpm format
```

## Lint and format

[Biome](https://biomejs.dev) is the only linter and formatter. Its configuration is `biome.json`.

```bash
pnpm lint                      # biome check: lint and report unformatted files
pnpm format                    # format in place
```

The repository's commit hook runs `biome check --write` over the console when a file under `web/` is staged, and then the unit tests with coverage.

## Unit tests

The unit tests run with [Vitest](https://vitest.dev) and jsdom:

```bash
pnpm test                      # vitest run
pnpm test:coverage             # with coverage; fails below the thresholds
```

They are `*.test.ts` and `*.test.tsx` files under `__tests__/`, which follows the layout of the code it covers: `__tests__/lib/api.test.ts` covers `lib/api.ts`. No test file sits beside the code. The tests reach no service: `__tests__/testing.ts` replaces `fetch`, and `__tests__/tasks-testing.ts` does the same for the Tasks section's tests, answering by the path asked for, with a run's stream built from its events.

`pnpm test:coverage` holds the suite to the four thresholds in `vitest.config.mts`, 90% each of statements, branches, functions, and lines. Everything under `app/`, `components/`, `hooks/`, and `lib/` is counted, and `next.config.ts` with it, apart from `components/ui/`. The commit hook and CI run the suite this way; `pnpm test` measures nothing, so a run of one file is not held to the suite's numbers.

## End-to-end tests

The end-to-end tests run with [Playwright](https://playwright.dev) in Chromium and Firefox:

```bash
pnpm exec playwright install chromium firefox   # once per machine: downloads the two browsers
pnpm test:e2e                  # playwright test
```

Playwright builds the app and serves it on port 3100 itself, so nothing needs to be running first, the Freyja service included. Each test hands the page what the service would answer (`e2e/api.ts`, and `e2e/tasks-api.ts` for the Tasks section), and the built app forwards `/api/...` to an address nothing listens on, port 3109, so a test that hands a page no answer sees the console as it is with the service stopped. One spec is the exception: `stream.spec.ts` puts a stand-in for the service on that port, to see a run's events pass the forwarding as they are produced and not all at once when the run ends. It runs in projects of its own, `stream-chromium` and `stream-firefox`, one after the other and after every other spec, so that nothing answers on that port while the others run. Port 3109 is free on a machine that runs the tests.

The tests are `*.spec.ts` files under `e2e/`: `smoke.spec.ts` for the first page, `console.spec.ts` for the sidebar and the sections, `llm.spec.ts` for the LLM section as it is read, `profiles.spec.ts` for adding, activating, testing, changing, and deleting profiles, `agents.spec.ts` for the Agents section as it is read and its warnings, `agents-manage.spec.ts` for adding an agent, checking one, changing one's approval choice, moving one, and removing one, `tasks.spec.ts` for the Tasks section, and that a page of another origin cannot show it in a frame, `tasks-decide.spec.ts` for deciding a proposal, `tasks-run.spec.ts` for follow-ups, agent tasks, a run the page does not read, a page that is closed during a run, and Stop, and `stream.spec.ts` for a run's stream through the forwarding, and for what the forwarding passes on of a request: its origin, its site, and the host the browser asked for, which the service reads to turn a page of another origin away; and that a page cannot choose that host by writing `X-Forwarded-Host` itself, which the service's refusal of a host name it does not answer for rests on. Playwright reads nothing outside that directory, and Vitest skips it, so `pnpm test` and `pnpm test:e2e` never run each other's files. After a run, `pnpm exec playwright show-report` opens the report. To test in another browser as well, add a project for it in `playwright.config.ts`.

The commit hook does not run these tests. CI does, when a file under `web/` changes.

## Container image

`Dockerfile` builds the console as a standalone Next.js server on Chainguard's Wolfi base. From the repository root:

```bash
docker build -t freyja-web web
```

- The image holds the service's address. Next.js writes `FREYJA_API_URL` into the build, so it is given when the image is built and a container cannot change it: `docker build --build-arg FREYJA_API_URL=http://freyja-agent:8000 -t freyja-web web`. The default, `http://127.0.0.1:8000`, is right where the service's container shares the network namespace: the same pod, or `--network host`.
- The server listens on `127.0.0.1:3000` inside the container and runs as user 65532. `HOSTNAME` is the address it listens on and `PORT` the port; both are read by Next.js's standalone server, not by the console's own code. Set `HOSTNAME=0.0.0.0` only where the network around the container already decides who may reach it; `docker run -p` needs it.
- The image's health check asks the console for its LLM page on loopback.

The [root README](../README.md#container-images) shows the console's container running beside the service's.

## Coding agents

`AGENTS.md` holds Next.js's own rules for coding agents, and `CLAUDE.md` only imports it for Claude Code. This version of Next.js differs from older ones in its APIs and conventions; its guides are in `node_modules/next/dist/docs/` once the dependencies are installed, and are read before the code is changed.
