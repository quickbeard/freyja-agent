"""The instructions for coding agents name only what the repository has.

``AGENTS.md`` at the root is in every session. What one directory holds is described in an
``AGENTS.md`` of that directory, which a session reads when it works there. A description
nobody reads on every change goes stale without a word, so these tests hold what can be
held without reading the prose: every path a file names exists, every module of the service
is named in its directory's file, each of those files is one Claude Code is given too, and
the root's list names them all.

What a path is, here: a span in backticks, without white space, that ends with ``/`` or
whose last part is a file name with one of ``EXTENSIONS`` or one of ``NAMES``. A command, a
route, a URL, a setting, and a name from the code are none of these and are not looked at.
A span with ``*``, ``<``, or another character of ``NOT_A_PATH`` is a pattern or a
placeholder, and is skipped. So is a path under a directory that is installed or built
(``NOT_COMMITTED_PARTS``) and one of the few paths git ignores (``NOT_COMMITTED``): a clean
checkout, which is what CI tests, has none of them.
"""

import os
import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "freyja_agent"

# Never walked: what is installed, built, or cached, and the worktrees a coding agent makes
# inside the checkout, each of which is a whole copy of the repository.
PRUNED = frozenset(
    {".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache", ".ruff_cache"}
)
WORKTREES = ROOT / ".claude" / "worktrees"

EXTENSIONS = (
    "py|md|ts|tsx|mts|js|mjs|json|toml|yml|yaml|lock|example|css|sh|sql|txt|ini|cfg|html|svg"
)
FILE_NAME = re.compile(rf"\.?[\w\[\]-]+(\.[\w-]+)*\.({EXTENSIONS})")
# File names that have no extension to know them by.
NAMES = frozenset({"Dockerfile", ".dockerignore", ".gitignore", ".python-version", ".env", ".venv"})
# A span that holds one of these is a pattern, a placeholder, a URL, a call, or a value.
NOT_A_PATH = frozenset("*<>{}()$=:@,|\"'")

# Named, and in no clean checkout: a directory that is installed or built ...
NOT_COMMITTED_PARTS = frozenset({"node_modules", ".next", ".venv"})
# ... and what git ignores: a developer's settings, and the files the orchestrator writes
# while it runs (`workspace/cases/`, which the root names as `cases/`).
NOT_COMMITTED = frozenset({".env", "cases", "workspace/cases"})


def _instruction_files() -> list[Path]:
    """Every ``AGENTS.md`` of the repository, the root's first."""
    found = []
    for directory, names, files in os.walk(ROOT):
        here = Path(directory)
        # A directory whose name starts with a dot holds no code of the project's own.
        names[:] = [name for name in names if not name.startswith(".") and name not in PRUNED]
        if "AGENTS.md" in files:
            found.append(here / "AGENTS.md")
    return sorted(found, key=lambda path: (len(path.parts), path))


def _repository() -> tuple[frozenset[str], frozenset[str]]:
    """Every file and every directory under the root, as paths from the root."""
    files: set[str] = set()
    directories: set[str] = set()
    for directory, names, found in os.walk(ROOT):
        here = Path(directory)
        names[:] = [name for name in names if name not in PRUNED and here / name != WORKTREES]
        directories.update((here / name).relative_to(ROOT).as_posix() for name in names)
        files.update((here / name).relative_to(ROOT).as_posix() for name in found)
    return frozenset(files), frozenset(directories)


FILES, DIRECTORIES = _repository()
INSTRUCTIONS = _instruction_files()
NESTED = [path for path in INSTRUCTIONS if path.parent != ROOT]


def named_paths(text: str) -> list[str]:
    """The spans of ``text`` that name a file or a directory of the repository."""
    found = []
    for span in re.findall(r"`([^`]+)`", text):
        if any(character.isspace() for character in span) or NOT_A_PATH & set(span):
            continue
        # A route or an absolute path, an option, a home directory.
        if span.startswith(("/", "-", "~")):
            continue
        last = span.rstrip("/").rsplit("/", 1)[-1]
        if span.endswith("/") or FILE_NAME.fullmatch(last) or last in NAMES:
            found.append(span)
    return found


def _committed(span: str) -> bool:
    path = span.rstrip("/")
    return path not in NOT_COMMITTED and not NOT_COMMITTED_PARTS & set(path.split("/"))


def exists(span: str, directory: Path) -> bool:
    """Whether the repository has what ``span`` names, for a file in ``directory``.

    A bare name is one at the root, as ``README.md`` and ``tests/`` are, or one in the
    directory of the file that names it or below, which is where a file speaks of
    ``factory.py`` or of ``agent-form.tsx``. A path with a ``/`` is taken from the root or
    from that directory, and otherwise as the end of a longer path, as
    ``runtime/migrations.py`` and ``checklists/requirements.md`` are written.
    """
    known = DIRECTORIES if span.endswith("/") else FILES
    path = span.rstrip("/")
    base = directory.relative_to(ROOT).as_posix()
    below = "" if base == "." else f"{base}/"
    if "/" not in path:
        return path in known or any(
            entry.startswith(below) and entry.rsplit("/", 1)[-1] == path for entry in known
        )
    return path in known or f"{below}{path}" in known or any(e.endswith(f"/{path}") for e in known)


def missing(text: str, directory: Path) -> list[str]:
    return sorted(
        {span for span in named_paths(text) if _committed(span) and not exists(span, directory)}
    )


def _name(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def test_the_root_has_its_file_and_each_directory_described_has_one():
    assert INSTRUCTIONS[0] == ROOT / "AGENTS.md"
    assert {_name(path) for path in NESTED} >= {
        "src/freyja_agent/AGENTS.md",
        "tests/AGENTS.md",
        "web/AGENTS.md",
        "web/e2e/AGENTS.md",
    }


@pytest.mark.parametrize("path", INSTRUCTIONS, ids=_name)
def test_every_path_a_file_names_exists(path: Path):
    """A module that was renamed or removed, and is still described, fails here."""
    assert missing(path.read_text(), path.parent) == []


def test_what_counts_as_a_path_and_what_does_not():
    text = (
        "`uv run pytest` runs `tests/`; `factory.py` holds `build_agent`, `Runtime.assemble` "
        "is in `runtime/runtime.py`, `GET /tools` and `/healthz` are routes, `*.spec.ts` and "
        "`specs/<NNN-slug>/` are patterns, `FREYJA_MODEL` is a setting, `web/Dockerfile` and "
        "`.env.example` are files, `web/app/tasks/[id]/` is a directory, `.agent` an "
        "attribute, `http://127.0.0.1:8765/mcp` an address, and `metadata.reply` a key."
    )

    assert named_paths(text) == [
        "tests/",
        "factory.py",
        "runtime/runtime.py",
        "web/Dockerfile",
        ".env.example",
        "web/app/tasks/[id]/",
    ]


def test_a_path_the_repository_lacks_is_reported():
    """The check can fail: a name nothing has, from the root, from a directory, as a tail."""
    text = (
        "`src/freyja_agent/api/nothing.py`, `nothing.py`, `api/nothing.py`, `nowhere/`, and "
        "`config.py`, which is one directory up, beside `app.py`, `runtime/runtime.py`, "
        "`tests/`, `.env`, `cases/`, and `node_modules/next/dist/docs/`."
    )

    assert missing(text, PACKAGE / "api") == [
        "api/nothing.py",
        "config.py",
        "nothing.py",
        "nowhere/",
        "src/freyja_agent/api/nothing.py",
    ]
    # From the root every one of the real ones is found, the bare name included.
    assert missing("`config.py`, `app.py`, `web/e2e/`, `spec.md`", ROOT) == []


@pytest.mark.parametrize("path", NESTED, ids=_name)
def test_claude_code_is_given_each_directory_s_file(path: Path):
    """Claude Code reads ``CLAUDE.md``, never ``AGENTS.md``: the one only imports the other."""
    beside = path.parent / "CLAUDE.md"

    assert beside.is_file(), f"{_name(path)} has no CLAUDE.md beside it"
    imports = [line for line in beside.read_text().splitlines() if line.startswith("@")]
    assert imports == ["@AGENTS.md"]


def test_no_claude_file_stands_without_the_file_it_imports():
    described = {path.parent for path in INSTRUCTIONS}
    alone = [
        name
        for name in FILES
        if name.rsplit("/", 1)[-1] == "CLAUDE.md"
        and not name.startswith(".")
        and (ROOT / name).parent not in described
    ]

    assert alone == []


def test_the_root_names_every_directory_s_file_and_none_that_is_missing():
    """Codex CLI reads the files from the root down to where it was started, and no other:
    a directory's file is read only because the root says to read it."""
    root = (ROOT / "AGENTS.md").read_text()
    named = {span for span in named_paths(root) if span.endswith("/AGENTS.md")}

    assert named == {_name(path) for path in NESTED}


def _code_directories() -> list[Path]:
    found = [PACKAGE]
    found += sorted(
        path
        for path in PACKAGE.rglob("*")
        if path.is_dir() and not path.name.startswith(("_", ".")) and any(path.glob("*.py"))
    )
    return found


@pytest.mark.parametrize("directory", _code_directories(), ids=_name)
def test_every_module_of_the_service_is_named_in_its_directory_s_file(directory: Path):
    """A package or a module added without a word here fails, so the description is asked for
    in the change that adds the code."""
    described = directory / "AGENTS.md"

    assert described.is_file(), f"{_name(directory)} holds code and has no AGENTS.md"
    named = set(named_paths(described.read_text()))
    modules = {path.name for path in directory.glob("*.py") if path.name != "__init__.py"}
    assert modules - named == set()


def test_the_files_beside_the_code_reach_no_image_and_no_installed_package():
    """They sit under ``src/``, which the image is built from and the wheel is made of."""
    rules = (ROOT / ".dockerignore").read_text().splitlines()
    for rule in ("**/AGENTS.md", "**/CLAUDE.md"):
        # A later rule wins: after the one that lets src/ in.
        assert rules.index(rule) > rules.index("!src/")

    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    excluded = project["tool"]["uv"]["build-backend"]["wheel-exclude"]
    assert {"AGENTS.md", "CLAUDE.md"} <= set(excluded)
