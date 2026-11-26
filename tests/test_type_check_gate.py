"""pyright is a gate: the commit hook and both CI systems run it, at one version, over all of
the service's code.

A type checker only holds where it is run, at the version the others run, with nothing left
out of its reading. These tests keep the three places that run it asking the same question,
as ``test_coverage_gate.py`` does for the coverage thresholds.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

# The one command, wherever it runs: pyright with the settings of pyproject.toml.
COMMAND = "uv run pyright"
# Both CI systems install from the lock file, and fail when it is out of step.
INSTALL = "uv sync --locked"


def _project() -> dict[str, Any]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _local_hooks() -> list[dict[str, Any]]:
    repos = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    return next(repo for repo in repos if repo["repo"] == "local")["hooks"]


def test_pyright_is_pinned_to_one_exact_version_that_the_lock_file_installs():
    """A newer pyright reports more: a range would let the hook and CI disagree."""
    dev = _project()["dependency-groups"]["dev"]
    pinned = [entry for entry in dev if entry.startswith("pyright")]

    assert len(pinned) == 1
    # The nodejs extra brings Node from PyPI, so no job installs it.
    found = re.fullmatch(r"pyright\[nodejs\]==(\d+\.\d+\.\d+)", pinned[0])
    assert found, pinned[0]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = [package for package in lock["package"] if package["name"] == "pyright"]
    assert [package["version"] for package in locked] == [found.group(1)]


def test_pyright_reads_all_of_the_service_and_no_rule_is_switched_off():
    settings = _project()["tool"]["pyright"]

    assert settings["include"] == ["src"]
    assert settings["typeCheckingMode"] == "standard"
    assert settings["pythonVersion"] == "3.12"
    # Checked against the packages the lock file installs, where uv puts them.
    assert (settings["venvPath"], settings["venv"]) == (".", ".venv")
    # Only an ignore that names its rule silences a line.
    assert settings["enableTypeIgnoreComments"] is False
    # Nothing of the service is left out, and no rule is turned off for all of it.
    assert "exclude" not in settings
    assert "ignore" not in settings
    assert not [name for name in settings if name.startswith("report")]


def test_the_commit_hook_runs_pyright_when_the_code_or_its_packages_change():
    hook = next(hook for hook in _local_hooks() if hook["id"] == "pyright")

    assert hook["entry"] == COMMAND
    assert hook["language"] == "system"
    # The whole of src/ is read, whatever was staged.
    assert hook["pass_filenames"] is False
    changed = re.compile(hook["files"])
    for path in ("src/freyja_agent/config.py", "pyproject.toml", "uv.lock"):
        assert changed.search(path), path
    for path in ("web/app/page.tsx", "README.md", "tests/test_runs.py", "web/pyproject.toml"):
        assert not changed.search(path), path


def test_the_hook_runs_pyright_before_the_tests():
    """The quick gate first: a type error is reported in seconds, the suite takes minutes."""
    order = [hook["id"] for hook in _local_hooks()]

    assert order.index("pyright") < order.index("pytest")


def test_github_runs_pyright_from_the_lock_file():
    workflow = yaml.safe_load((ROOT / ".github/workflows/lint.yml").read_text())
    job = next(job for job in workflow["jobs"].values() if job["name"] == "pyright")
    runs = [step["run"] for step in job["steps"] if "run" in step]

    assert runs == [INSTALL, COMMAND]
    # On every pull request and on main, as the other linters. YAML reads the key `on` as
    # the boolean True.
    triggers = workflow[True]
    assert triggers["pull_request"]["branches"] == ["main"]
    assert triggers["push"]["branches"] == ["main"]


def test_gitlab_runs_pyright_from_the_lock_file():
    job = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())["pyright"]

    assert job["script"] == [COMMAND]
    assert job["before_script"][-1] == INSTALL
    # On every merge request and on main, as the other linters.
    assert job["extends"] == ".every-change"


def test_every_suppression_in_the_service_names_its_rule_and_none_covers_a_file():
    """``# pyright: ignore`` alone would silence every rule on its line, and a comment such
    as ``# pyright: basic`` at the top of a file would loosen the whole file."""
    named = re.compile(r"#\s*pyright:\s*ignore\[report[A-Za-z]+(, ?report[A-Za-z]+)*\]")
    any_directive = re.compile(r"#\s*pyright:")
    wrong: list[str] = []
    count = 0
    for path in sorted(SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not any_directive.search(line):
                continue
            count += 1
            if not named.search(line):
                wrong.append(f"{path.relative_to(ROOT)}:{number}")

    assert wrong == []
    # The test reads what it means to read: the service has suppressions today.
    assert count > 0
