"""The Deep Agents canary: a weekly run of pyright and the suite against a newer Deep Agents
than ``uv.lock`` names, on GitHub and on GitLab.

The service holds Deep Agents to one minor version, and the lock file to one release, so a
change in the library is met only on the day somebody upgrades. The canary meets it the week
it is released. It is worth what it tests: these tests keep the two sides upgrading the same
way and running the same gates, keep the job out of every pull request and merge request,
where a library's new release would fail a change that has nothing to do with it, and keep
everything else out of the scheduled run.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
CANARY = WORKFLOWS / "deepagents-canary.yml"
GITLAB_JOB = "deepagents-canary"

# Both sides install what the lock file names first, so that the upgrade moves Deep Agents
# and what its newer release needs, and nothing else.
INSTALL = "uv sync --locked"
# The two legs, by the command that upgrades. The first resolves again inside the pin of
# pyproject.toml; the second installs the newest release whatever the pin says.
LEGS = {
    "inside the pin": "uv sync --upgrade-package deepagents",
    "newest release": "uv pip install --upgrade-package deepagents deepagents",
}
# What each leg says of itself before the gate: the release under test, and the
# requirements the upgrade broke, which fails nothing.
SAID = ["uv pip show deepagents", "uv pip check || true"]
# The gates. --no-sync, because `uv run` would otherwise put back what the lock file names
# and the gate would test the release every other job tests.
GATES = {"pyright": "uv run --no-sync pyright", "pytest": "uv run --no-sync pytest"}

SCHEDULED = '$CI_PIPELINE_SOURCE == "schedule"'


def _workflow(path: Path) -> dict[Any, Any]:
    return yaml.safe_load(path.read_text())


def _gitlab() -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())


def _expected() -> dict[tuple[str, str], list[str]]:
    return {
        (leg, gate): [INSTALL, upgrade, *SAID, command]
        for leg, upgrade in LEGS.items()
        for gate, command in GATES.items()
    }


def _github_commands() -> dict[tuple[str, str], list[str]]:
    """What each job of the workflow's matrix runs, with the matrix's values written in."""
    job = _workflow(CANARY)["jobs"]["canary"]
    matrix = job["strategy"]["matrix"]
    upgrades = {entry["leg"]: entry["upgrade"] for entry in matrix["include"]}
    runs = [step["run"] for step in job["steps"] if "run" in step]
    return {
        (leg, gate): [
            run.replace("${{ matrix.upgrade }}", upgrades[leg]).replace("${{ matrix.gate }}", gate)
            for run in runs
        ]
        for leg in matrix["leg"]
        for gate in matrix["gate"]
    }


def _gitlab_commands() -> dict[tuple[str, str], list[str]]:
    """The same for the GitLab job, whose matrix gives each job its variables."""
    job = _gitlab()[GITLAB_JOB]
    commands = [job["before_script"][-1], *job["script"]]
    return {
        (entry["LEG"], gate): [
            command.replace("$UPGRADE", entry["UPGRADE"]).replace("$GATE", gate)
            for command in commands
        ]
        for entry in job["parallel"]["matrix"]
        for gate in entry["GATE"]
    }


def test_both_sides_upgrade_the_same_way_and_run_the_same_gates():
    assert _github_commands() == _expected()
    assert _gitlab_commands() == _expected()


def test_one_leg_or_gate_that_fails_does_not_stop_the_others_on_github():
    """GitLab runs the jobs of a matrix apart; GitHub cancels the rest unless told not to."""
    assert _workflow(CANARY)["jobs"]["canary"]["strategy"]["fail-fast"] is False


def test_the_first_leg_has_a_pin_to_stay_inside():
    """Without an upper bound in pyproject.toml both legs would install the same release."""
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    pinned = [entry for entry in dependencies if entry.startswith("deepagents")]

    assert len(pinned) == 1
    assert re.fullmatch(r"deepagents>=\d+(\.\d+)*,<\d+(\.\d+)*", pinned[0]), pinned[0]


def test_github_runs_the_canary_weekly_and_by_hand_and_for_no_pull_request():
    # YAML reads the key `on` as the boolean True.
    triggers = _workflow(CANARY)[True]

    assert set(triggers) == {"schedule", "workflow_dispatch"}
    crons = [entry["cron"] for entry in triggers["schedule"]]
    assert len(crons) == 1
    minute, hour, day, month, weekday = crons[0].split()
    # One run a week: a minute, an hour, and a day of the week, on any date.
    assert minute.isdigit() and hour.isdigit() and weekday.isdigit()
    assert (day, month) == ("*", "*")


def test_gitlab_runs_the_canary_in_a_scheduled_pipeline_and_in_no_other():
    assert _gitlab()[GITLAB_JOB]["rules"] == [{"if": SCHEDULED}]


def test_a_scheduled_run_is_the_canary_s_alone_on_both_sides():
    """A new release of a library fails the canary, and should fail nothing beside it: the
    other jobs test a change, and a schedule brings none."""
    for path in sorted(WORKFLOWS.glob("*.yml")):
        if path != CANARY:
            assert "schedule" not in _workflow(path)[True], path.name

    ci = _gitlab()
    jobs = {
        name: job
        for name, job in ci.items()
        if isinstance(job, dict) and "script" in job and name != GITLAB_JOB
    }
    # The test reads what it means to read: the pipeline has other jobs.
    assert len(jobs) >= 9
    for name, job in jobs.items():
        rules = job.get("rules") or ci[job["extends"]]["rules"]
        # The first rule that matches decides, so the refusal has to come first.
        assert rules[0] == {"if": SCHEDULED, "when": "never"}, name


def test_the_canary_names_its_actions_by_commit_as_the_other_workflows_do():
    steps = _workflow(CANARY)["jobs"]["canary"]["steps"]
    used = [step["uses"] for step in steps if "uses" in step]
    tests = _workflow(WORKFLOWS / "unit-tests.yml")["jobs"]["python"]["steps"]

    assert used == [step["uses"] for step in tests if "uses" in step]
    for action in used:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", action), action
