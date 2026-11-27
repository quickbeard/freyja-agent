"""The two CI systems ask the same things, because each check is written once.

GitHub's workflows and GitLab's pipeline are two files kept by hand, and a check changed on
one side only passes a change the other side would stop. So what a job checks is in
`scripts/ci/<job>.sh`, and the job of that check on each side calls the script and runs
nothing else of the check. What a job installs before it differs by side and stays in the CI
files. These tests hold that: a job added to one side only, a check run outside its script,
and a script one side does not call each fail here.

One job with a twin calls no script, the Deep Agents canary, which a test of its own keeps
in step; it is named in a table with its reason, as a job without a twin is.
"""

import re
import stat
from copy import deepcopy
from typing import Any

import pytest
import yaml

from tests.ci_files import GITLAB, SCRIPTS, call, commands, github_jobs, gitlab_jobs, scripts

Jobs = dict[str, dict[str, Any]]

# The jobs one side has and the other does not, each with its reason. Every other job has a
# twin on the other side that calls the same script.
ONE_SIDE: dict[str, dict[str, str]] = {
    "GitHub": {
        "scan.yml:images": (
            "builds both container images, runs them, and scans them, which needs a runner "
            "that may build an image; the dev deploy pipeline scans the images it builds"
        ),
    },
    "GitLab": {},
}

# The jobs both sides have that call no script, each with its reason and the test that
# compares its two sides instead.
OWN_TEST: dict[str, dict[str, str]] = {
    "GitHub": {
        "deepagents-canary.yml:canary": (
            "upgrades Deep Agents before its gate, which a script does not do, and runs the "
            "gate without `uv run`'s sync, as no other job does; "
            "tests/test_deepagents_canary.py compares the two sides command by command"
        ),
    },
    "GitLab": {
        "deepagents-canary": "the same job as GitHub's deepagents-canary.yml:canary",
    },
}

# One call of a script, with or without arguments.
CALL = re.compile(r"scripts/ci/([a-z0-9_-]+)\.sh(?: \S.*)?")
# Where one of the checks starts, wherever it is written. No installation holds one:
# `playwright install` and the download of a scanner are none of these.
CHECK = re.compile(
    r"\b(?:ruff (?:check|format)|pyright|pytest|pnpm (?:lint|test)|pnpm (?:--dir \S+ )?run "
    r"|biome (?:check|lint|format|ci)|vitest|playwright test"
    r"|gitleaks (?:git|detect|dir|protect)|trivy (?:fs|image|config|repo|rootfs)"
    r"|semgrep (?:scan|ci))"
)


def _github(job: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What a GitHub job runs before its last command, and the last command."""
    runs = [step["run"].strip() for step in job.get("steps", []) if "run" in step]
    return runs[:-1], runs[-1:]


def _gitlab(job: dict[str, Any]) -> tuple[list[str], list[str]]:
    """What a GitLab job runs around its `script`, and its `script`."""
    around = [*job.get("before_script", []), *job.get("after_script", [])]
    return [line.strip() for line in around], [line.strip() for line in job.get("script", [])]


def _named(*tables: dict[str, dict[str, str]]) -> dict[str, set[str]]:
    """The jobs of each side that the tables name."""
    return {side: {name for table in tables for name in table[side]} for side in tables[0]}


def problems(
    github: Jobs, gitlab: Jobs, written: set[str], apart: dict[str, set[str]]
) -> list[str]:
    """Every way the two sides differ, or a check is run outside its script. ``apart`` is
    the jobs of each side that a table names, which are not read."""
    found: list[str] = []
    calls: dict[str, list[str]] = {"GitHub": [], "GitLab": []}
    for side, jobs, read in (("GitHub", github, _github), ("GitLab", gitlab, _gitlab)):
        alone = apart[side]
        found += [
            f"{side} has no job {name}, which a table of the jobs that call no script names"
            for name in sorted(alone - set(jobs))
        ]
        for name, job in jobs.items():
            if name in alone:
                continue
            before, check = read(job)
            found += [
                f"{side}'s {name} runs a check outside its script: {command!r}"
                for command in before
                if CALL.search(command) or CHECK.search(command)
            ]
            called = CALL.fullmatch(check[0]) if len(check) == 1 else None
            if called is None:
                found.append(f"{side}'s {name} does not end with one call of its script: {check}")
                continue
            calls[side].append(check[0])
            if side == "GitLab" and called.group(1) != name:
                found.append(f"GitLab's {name} calls the script of another job: {check[0]}")
    for side, other in (("GitHub", "GitLab"), ("GitLab", "GitHub")):
        rest = list(calls[other])
        for one in calls[side]:
            if one in rest:
                rest.remove(one)
            else:
                found.append(f"{side} runs `{one}` and {other} does not")
        names = {
            one.split()[0].removeprefix("scripts/ci/").removesuffix(".sh") for one in calls[side]
        }
        found += [f"{call(name)} is not called on {side}" for name in sorted(written - names)]
        found += [
            f"{side} calls {call(name)}, which is not there" for name in sorted(names - written)
        ]
    return found


def test_both_sides_call_the_same_scripts_and_run_no_check_outside_them():
    assert problems(github_jobs(), gitlab_jobs(), scripts(), _named(ONE_SIDE, OWN_TEST)) == []


def test_every_check_of_today_has_its_script():
    """The test above would pass with no script and every job in the table."""
    assert scripts() == {
        "ruff",
        "pyright",
        "pytest",
        "biome",
        "vitest",
        "playwright",
        "gitleaks",
        "trivy",
        "semgrep",
    }
    assert _named(ONE_SIDE) == {"GitHub": {"scan.yml:images"}, "GitLab": set()}
    assert _named(OWN_TEST) == {
        "GitHub": {"deepagents-canary.yml:canary"},
        "GitLab": {"deepagents-canary"},
    }


def test_a_job_with_a_test_of_its_own_is_one_the_table_is_needed_for():
    """Read like any other job, each side of the canary is found to call no script, so its
    entry stands for something and is taken out once the job calls one."""
    found = problems(github_jobs(), gitlab_jobs(), scripts(), _named(ONE_SIDE))

    assert len(found) == 2, found
    assert "GitHub's deepagents-canary.yml:canary does not end with one call" in found[0]
    assert "GitLab's deepagents-canary does not end with one call" in found[1]


def _a_job_on_gitlab_only(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    gitlab["mypy"] = {"script": ["scripts/ci/mypy.sh"]}
    written.add("mypy")


def _a_job_on_github_only(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    github["lint.yml:mypy"] = {"steps": [{"run": "scripts/ci/mypy.sh"}]}
    written.add("mypy")


def _a_job_that_calls_no_script(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    gitlab["integration"] = {"script": ["uv run pytest tests/integration"]}


def _a_job_taken_from_one_side(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    del gitlab["semgrep"]


def _an_argument_on_one_side(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    gitlab["pytest"]["script"] = ["scripts/ci/pytest.sh tests/integration"]


def _a_second_command_on_gitlab(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    gitlab["pytest"]["script"].append("uv run pytest tests/integration")


def _a_check_before_the_script_on_github(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    steps = github["unit-tests.yml:python"]["steps"]
    steps.insert(len(steps) - 1, {"run": "uv run pytest tests/integration"})


def _a_check_after_the_script_on_github(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    github["lint.yml:python"]["steps"].append({"run": "uv run ruff check --select S ."})


def _a_check_in_gitlab_s_installation(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    gitlab["trivy"]["before_script"] = ["trivy image --exit-code 1 freyja-agent"]


def _a_script_nobody_calls(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    written.add("mypy")


def _a_table_entry_for_a_job_that_is_gone(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    del github["scan.yml:images"]


def _the_canary_taken_from_one_side(github: Jobs, gitlab: Jobs, written: set[str]) -> None:
    del gitlab["deepagents-canary"]


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (_a_job_on_gitlab_only, "GitLab runs `scripts/ci/mypy.sh` and GitHub does not"),
        (_a_job_on_github_only, "scripts/ci/mypy.sh is not called on GitLab"),
        (_a_job_that_calls_no_script, "GitLab's integration does not end with one call"),
        (_a_job_taken_from_one_side, "scripts/ci/semgrep.sh is not called on GitLab"),
        (_an_argument_on_one_side, "GitHub runs `scripts/ci/pytest.sh` and GitLab does not"),
        (_a_second_command_on_gitlab, "GitLab's pytest does not end with one call"),
        (
            _a_check_before_the_script_on_github,
            "GitHub's unit-tests.yml:python runs a check outside its script",
        ),
        (
            _a_check_after_the_script_on_github,
            "GitHub's lint.yml:python does not end with one call",
        ),
        (_a_check_in_gitlab_s_installation, "GitLab's trivy runs a check outside its script"),
        (_a_script_nobody_calls, "scripts/ci/mypy.sh is not called on GitHub"),
        (_a_table_entry_for_a_job_that_is_gone, "GitHub has no job scan.yml:images"),
        (_the_canary_taken_from_one_side, "GitLab has no job deepagents-canary"),
    ],
)
def test_a_change_to_one_side_only_is_found(change, said):
    """The files as they are with one change of the kind the test exists to stop."""
    github, gitlab, written = deepcopy(github_jobs()), deepcopy(gitlab_jobs()), scripts()

    change(github, gitlab, written)

    found = problems(github, gitlab, written, _named(ONE_SIDE, OWN_TEST))
    assert [problem for problem in found if said in problem], found


def test_nothing_gives_a_gitlab_job_commands_from_outside_it():
    """`problems` reads a job's own commands and those of the jobs it extends. A default, a
    pipeline-wide script, or an included file would add commands it does not read."""
    pipeline = yaml.safe_load(GITLAB.read_text())

    assert not {"default", "before_script", "after_script", "include"} & set(pipeline)


def test_no_installation_of_today_is_taken_for_a_check():
    """`CHECK` is a list of words, so it is shown against what the jobs do install."""
    for install in (
        "uv sync --locked",
        'pip install --quiet "uv==0.12.*"',
        "npm install -g pnpm@12 && pnpm --dir web install --frozen-lockfile",
        "pnpm --dir web exec playwright install --with-deps chromium firefox",
        'uv tool install "semgrep==${SEMGREP_VERSION}"',
        'tar -xzf "$RUNNER_TEMP/trivy.tar.gz" -C "$RUNNER_TEMP" trivy',
    ):
        assert not CHECK.search(install), install
    for name in scripts():
        assert all(CHECK.search(command) for command in commands(name)), name


def test_every_script_runs_under_sh_from_wherever_it_is_called():
    for path in sorted(SCRIPTS.iterdir()):
        lines = path.read_text().splitlines()
        code = [line for line in lines if line and not line.startswith("#")]

        assert path.suffix == ".sh", path.name
        # The images of the gitleaks and Trivy jobs have no bash.
        assert lines[0] == "#!/bin/sh", path.name
        assert path.stat().st_mode & stat.S_IXUSR, f"{path.name} is not executable"
        # A command that fails stops the script, so the job fails with it.
        assert code[0] == "set -eu", path.name
        # To the repository's root, or to web/, by the script's own place.
        assert code[1] in ('cd "$(dirname "$0")/../.."', 'cd "$(dirname "$0")/../../web"'), (
            path.name
        )
        assert commands(path.stem), path.name


def test_a_script_installs_nothing():
    """What is installed differs by side, so it stays in the CI files."""
    installs = re.compile(r"\b(?:install|sync|add|curl|wget|pip|apt-get|apk)\b")

    for name in scripts():
        assert not [command for command in commands(name) if installs.search(command)], name
