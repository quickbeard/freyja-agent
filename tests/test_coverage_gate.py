"""The coverage thresholds are set, and the commit hook and CI all run the suites against them.

A threshold only holds where the suite is run with coverage. These tests keep the three
places that run each suite, the commit hook, GitHub's workflow, and GitLab's job, asking for
it, so that none of them passes a change the others would stop.
"""

import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _hooks() -> dict[str, str]:
    repos = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    local = next(repo for repo in repos if repo["repo"] == "local")
    return {hook["id"]: hook["entry"] for hook in local["hooks"]}


def _github(job: str, step: str) -> str:
    workflow = yaml.safe_load((ROOT / ".github/workflows/unit-tests.yml").read_text())
    return next(s["run"] for s in workflow["jobs"][job]["steps"] if s.get("name") == step)


def _gitlab(job: str) -> list[str]:
    return yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())[job]["script"]


def test_the_service_has_a_threshold_and_measures_its_own_code():
    coverage = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["coverage"]

    assert coverage["run"]["source"] == ["freyja_agent"]
    assert 80 <= coverage["report"]["fail_under"] <= 100
    # Nothing of the service is left out of the count.
    assert "omit" not in coverage["run"]
    assert "exclude_lines" not in coverage["report"]
    assert "exclude_also" not in coverage["report"]


def test_pytest_runs_with_coverage_wherever_the_whole_suite_runs():
    assert _hooks()["pytest"] == "uv run pytest -q --cov"
    assert _github("python", "Run pytest") == "uv run pytest --cov"
    assert _gitlab("pytest") == ["uv run pytest --cov"]


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
