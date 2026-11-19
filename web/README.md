# Freyja web console

The console of [Freyja](../README.md): a [Next.js](https://nextjs.org) app that shows and changes the service's configuration through the service's API. It is a client of that API and nothing more. It holds no key for a model or an agent, calls neither itself, and decides nothing the orchestrator does.

Like the API, the console has no authentication: whoever can reach it can change the service's configuration.

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

## What the console shows

A sidebar on the left lists the sections; on a screen narrower than 768 pixels it opens from a button. `/` redirects to `/llm`.

- **LLM** (`/llm`) shows every LLM profile as a card: its name, provider, model, base URL, the last four characters of its key, whether it is active, whether it can be used and why not, and its last test. **Add** saves a new profile, and its form can test the values before they are saved. A card has **Test**, **Set active**, **Edit**, and **Delete**; the environment's card has Test and Set active only. Activating a profile, deleting one, and changing the active one each ask for confirmation first. A key typed into a form is sent once and kept nowhere in the browser.
- **Agents** (`/agents`) shows every agent Freyja can hand work to as a card: its name, whether its messages require approval, whether it comes from the environment, and whether the service could reach it at its most recent attempt. An agent that cannot be handed work carries a warning with the reason. What the agent's card says of itself is shown as plain text and marked as the agent's own claim. The section is read-only: it adds, changes, and removes no agent.

Every page is sent with `Content-Security-Policy: frame-ancestors 'none'` and `X-Frame-Options: DENY`, so no page of another origin can show the console in a frame.

## Layout

- `app/`: the pages. `layout.tsx` is the shell, the sidebar beside the page; `llm/page.tsx` and `agents/page.tsx` are the two sections.
- `components/`: `app-sidebar.tsx`, the LLM section under `llm/` (the list, the card, the form that adds or changes a profile, the confirmations before a profile is activated or deleted, and how a test's result is shown), and the Agents section under `agents/` (the list and the card).
- `components/ui/`: the [shadcn/ui](https://ui.shadcn.com) components, generated and not edited by hand. `components.json` is shadcn's configuration.
- `lib/`: `api.ts` is the one function that calls the service, always through `/api`, and its error type `ApiError`. `llm.ts` and `agents.ts` hold the types and the calls of their sections; `utils.ts` the `cn` helper.
- `hooks/`: `use-mobile.ts`, the sidebar's width check.
- `__tests__/`: the unit tests. `e2e/`: the end-to-end tests.
- `next.config.ts`: the configuration above, the `/api` forwarding, the redirect, and the frame headers.

Lists are read in the browser with [SWR](https://swr.vercel.app), in Client Components. A read that fails is shown with a way to try again, and is not retried on its own.

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

They are `*.test.ts` and `*.test.tsx` files under `__tests__/`, which follows the layout of the code it covers: `__tests__/lib/api.test.ts` covers `lib/api.ts`. No test file sits beside the code. The tests reach no service: `__tests__/testing.ts` replaces `fetch`.

`pnpm test:coverage` holds the suite to the four thresholds in `vitest.config.mts`, 90% each of statements, branches, functions, and lines. Everything under `app/`, `components/`, `hooks/`, and `lib/` is counted, and `next.config.ts` with it, apart from `components/ui/`. The commit hook and CI run the suite this way; `pnpm test` measures nothing, so a run of one file is not held to the suite's numbers.

## End-to-end tests

The end-to-end tests run with [Playwright](https://playwright.dev) in Chromium and Firefox:

```bash
pnpm exec playwright install chromium firefox   # once per machine: downloads the two browsers
pnpm test:e2e                  # playwright test
```

Playwright builds the app and serves it on port 3100 itself, so nothing needs to be running first, the Freyja service included. Each test hands the page what the service would answer (`e2e/api.ts`), and the built app forwards `/api/...` to an address nothing listens on, so a test that hands a page no answer sees the console as it is with the service stopped.

The tests are `*.spec.ts` files under `e2e/`: `smoke.spec.ts` for the first page, `console.spec.ts` for the sidebar and the sections, `llm.spec.ts` for the LLM section as it is read, `profiles.spec.ts` for adding, activating, testing, changing, and deleting profiles, and `agents.spec.ts` for the Agents section as it is read. Playwright reads nothing outside that directory, and Vitest skips it, so `pnpm test` and `pnpm test:e2e` never run each other's files. After a run, `pnpm exec playwright show-report` opens the report. To test in another browser as well, add a project for it in `playwright.config.ts`.

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
