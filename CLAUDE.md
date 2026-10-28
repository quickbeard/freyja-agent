# Freyja

SOC orchestrator built on Deep Agents (LangGraph) and served with FastAPI. It routes security
operations work to delegate agents and keeps analysts in the loop for any action that changes
the environment.

The project principles live in the Spec Kit constitution and are binding in every session:

@.specify/memory/constitution.md

## Commands

Always go through uv. Never call python, pip, or pytest directly.

- `uv sync` installs runtime and dev dependencies into `.venv`.
- `uv run --env-file .env uvicorn freyja_agent.api.app:app --reload` runs the dev server on
  127.0.0.1:8000.
- `uv run --env-file .env freyja-api` runs it through the console script, binding from settings.
- `uv run ruff check .` and `uv run ruff format .` lint and format; use `--fix` and `--check`
  variants as usual.
- `uv run pytest` runs the suite. Tests use FastAPI's test client; no server or database needed.
- `uv run pre-commit run --all-files` runs the commit hook over the whole repo.

`.env` is git-ignored; copy it from `.env.example`. Settings do not read `.env` themselves, so
pass `--env-file .env` to `uv run` whenever a command needs it.

## Layout

- `src/freyja_agent/config.py`: the `Settings` class (pydantic-settings, `FREYJA_*` prefix) and
  the shared `settings` instance.
- `src/freyja_agent/api/app.py`: the FastAPI app, `/healthz`, and the `freyja-api` entry point.
- `tests/`: pytest suite.
- `.specify/`: Spec Kit templates, scripts, and the constitution. Feature artifacts go under
  `specs/<NNN-slug>/` as `spec.md`, `plan.md`, and `tasks.md`.
- `.claude/skills/speckit-*/`: the Spec Kit skills.

## Conventions

- Configuration goes through `Settings`. Never hardcode hosts, ports, connection strings, or
  model ids; add a field with a safe local default and document it in `.env.example`.
- New behavior ships with a test in `tests/`.
- A Ruff suppression carries a reason in its comment and is removed once the cause is fixed.
- Commit messages use the `type: summary` form: `feat:`, `chore:`, `docs:`.
- The README documents only what exists in the repo, never planned work.

## Spec Kit

Features follow `/speckit-specify`, `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`,
then `/speckit-converge`, with `/speckit-clarify`, `/speckit-checklist`, and `/speckit-analyze`
as optional gates. Do not start a `/speckit-*` command unless the user asks for it; a human
reviews each step's output before the next one runs. Every plan includes a Constitution Check
that names the principles it touches and justifies any deviation.
