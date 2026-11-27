"""The coverage thresholds are set, and the commit hook and CI all run the suites against them.

A threshold only holds where the suite is run with coverage. These tests keep the three
places that run each suite, the commit hook, GitHub's workflow, and GitLab's job, asking for
it, so that none of them passes a change the others would stop.

The service's threshold is the one of a run with the Postgres tests, so both CI systems have
to run them: each starts a Postgres for the job and names it in the variable those tests
read. The commit hook has no database, and holds a lower threshold of its own.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

from tests.integration.conftest import TEST_URI_VAR

ROOT = Path(__file__).resolve().parent.parent
# The hook's command: the suite as it runs without a database, held to a floor of its own.
HOOK = re.compile(r"uv run pytest -q --cov --cov-fail-under=(\d+)")


def _hooks() -> dict[str, str]:
    repos = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    local = next(repo for repo in repos if repo["repo"] == "local")
    return {hook["id"]: hook["entry"] for hook in local["hooks"]}


def _github_job(job: str) -> dict[str, Any]:
    workflow = yaml.safe_load((ROOT / ".github/workflows/unit-tests.yml").read_text())
    return workflow["jobs"][job]


def _github_step(job: str, step: str) -> dict[str, Any]:
    return next(s for s in _github_job(job)["steps"] if s.get("name") == step)


def _github(job: str, step: str) -> str:
    return _github_step(job, step)["run"]


def _gitlab_job(job: str) -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())[job]


def _gitlab(job: str) -> list[str]:
    return _gitlab_job(job)["script"]


def _fail_under() -> float:
    coverage = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]
    return coverage["report"]["fail_under"]


def _compose_postgres() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["postgres"]


def _test_database(host: str, made_with: dict[str, str]) -> str:
    """The address of the database a Postgres container makes from its three variables."""
    user, password = made_with["POSTGRES_USER"], made_with["POSTGRES_PASSWORD"]
    return f"postgresql://{user}:{password}@{host}:5432/{made_with['POSTGRES_DB']}"


def test_the_service_has_a_threshold_and_measures_its_own_code():
    coverage = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]

    assert coverage["run"]["source"] == ["freyja_agent"]
    assert 80 <= coverage["report"]["fail_under"] <= 100
    # Nothing of the service is left out of the count.
    assert "omit" not in coverage["run"]
    assert "exclude_lines" not in coverage["report"]
    assert "exclude_also" not in coverage["report"]


def test_pytest_runs_with_coverage_wherever_the_whole_suite_runs():
    assert HOOK.fullmatch(_hooks()["pytest"]), _hooks()["pytest"]
    # Nothing on the command line: CI is held to fail_under in pyproject.toml.
    assert _github("python", "Run pytest") == "uv run pytest --cov"
    assert _gitlab("pytest") == ["uv run pytest --cov"]


def test_the_commit_hook_holds_a_floor_of_its_own_that_is_not_above_the_one_ci_holds():
    """The hook runs without a database, so the Postgres stores are not run there."""
    found = HOOK.fullmatch(_hooks()["pytest"])
    assert found

    assert 80 <= int(found.group(1)) <= _fail_under()


def test_github_runs_the_postgres_tests_against_a_postgres_the_job_starts():
    service = _github_job("python")["services"]["postgres"]
    step = _github_step("python", "Run pytest")

    # The Postgres a developer runs, and its port where the job's steps reach it.
    assert service["image"] == _compose_postgres()["image"]
    assert service["ports"] == ["5432:5432"]
    # The job waits until the database answers before its first step.
    assert "--health-cmd" in service["options"]
    assert step["env"] == {TEST_URI_VAR: _test_database("localhost", service["env"])}


def test_gitlab_runs_the_postgres_tests_against_a_postgres_the_job_starts():
    job = _gitlab_job("pytest")

    assert job["services"] == [{"name": _compose_postgres()["image"], "alias": "postgres"}]
    # A service's container is given the job's variables, which is how it learns its own.
    assert job["variables"][TEST_URI_VAR] == _test_database("postgres", job["variables"])


def test_both_ci_systems_give_the_postgres_tests_the_same_database():
    """One made for the tests, which drop every table: never the one the service is given."""
    github = _github_job("python")["services"]["postgres"]["env"]
    gitlab = _gitlab_job("pytest")["variables"]
    made_with = ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB")

    assert {name: github[name] for name in made_with} == {name: gitlab[name] for name in made_with}
    assert github["POSTGRES_DB"] != _compose_postgres()["environment"]["POSTGRES_DB"]


def test_the_web_app_has_thresholds():
    config = (ROOT / "web/vitest.config.mts").read_text()
    found = re.findall(r"^\s+(statements|branches|functions|lines): (\d+),$", config, re.M)
    thresholds = {name: int(value) for name, value in found}

    assert set(thresholds) == {"statements", "branches", "functions", "lines"}
    assert all(80 <= value <= 100 for value in thresholds.values()), thresholds


def test_vitest_runs_with_coverage_wherever_the_whole_suite_runs():
    scripts = json.loads((ROOT / "web/package.json").read_text())["scripts"]

    assert scripts["test:coverage"] == "vitest run --coverage"
    # A run of one file is not measured.
    assert scripts["test"] == "vitest run"
    assert _hooks()["vitest"] == "pnpm --dir web run test:coverage"
    assert _github("web", "Run Vitest") == "pnpm test:coverage"
    assert _gitlab("vitest") == ["pnpm test:coverage"]
