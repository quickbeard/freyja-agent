"""The CI files of both sides and the scripts their jobs call, as the gate tests read them.

GitHub's jobs are in `.github/workflows/`, GitLab's in `.gitlab-ci.yml`, and what a job
checks is in `scripts/ci/<job>.sh`, which both sides call. `tests/test_ci_parity.py` holds
that; `tests/test_coverage_gate.py`, `tests/test_type_check_gate.py`, and
`tests/test_container_images.py` read from here what a script runs.
"""

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github/workflows"
GITLAB = ROOT / ".gitlab-ci.yml"
SCRIPTS = ROOT / "scripts/ci"

# The keys at the top of `.gitlab-ci.yml` that are not jobs.
GITLAB_KEYWORDS = frozenset(
    {
        "after_script",
        "before_script",
        "cache",
        "default",
        "image",
        "include",
        "services",
        "stages",
        "variables",
        "workflow",
    }
)


def call(job: str) -> str:
    """How a job of either side calls its script: by its path, from the repository's root."""
    return f"scripts/ci/{job}.sh"


def scripts() -> set[str]:
    """The checks that have a script, by name."""
    return {path.stem for path in SCRIPTS.glob("*.sh")}


def commands(job: str) -> list[str]:
    """What a job's script runs: its lines without the comments, the shell's settings, and
    the change of directory, with a continued line joined."""
    joined = re.sub(r"\s*\\\n\s*", " ", (SCRIPTS / f"{job}.sh").read_text())
    lines = [line.strip() for line in joined.splitlines()]
    return [
        line for line in lines if line and not line.startswith(("#", "cd ")) and line != "set -eu"
    ]


def github_jobs() -> dict[str, dict[str, Any]]:
    """Every job of every workflow, named `<workflow file>:<job id>`."""
    jobs: dict[str, dict[str, Any]] = {}
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        for job_id, job in yaml.safe_load(path.read_text())["jobs"].items():
            jobs[f"{path.name}:{job_id}"] = job
    return jobs


def gitlab_jobs() -> dict[str, dict[str, Any]]:
    """Every job of the pipeline, each with what it takes from the jobs it extends."""
    pipeline = yaml.safe_load(GITLAB.read_text())

    def whole(job: dict[str, Any]) -> dict[str, Any]:
        extended = job.get("extends", [])
        merged: dict[str, Any] = {}
        for name in [extended] if isinstance(extended, str) else extended:
            merged.update(whole(pipeline[name]))
        return {**merged, **job}

    return {
        name: whole(job)
        for name, job in pipeline.items()
        if isinstance(job, dict) and not name.startswith(".") and name not in GITLAB_KEYWORDS
    }
