"""What the two Dockerfiles and the scan jobs promise, read from the files themselves.

CI builds the images, runs them, and scans them (the ``images`` job of ``scan.yml``). These
tests need no Docker: they hold the choices a later edit could undo without any build
failing, such as an image that listens on every interface or runs as root, and they keep the
scanner versions equal on GitHub and on GitLab.
"""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "Dockerfile"
WEB = ROOT / "web/Dockerfile"


def _instructions(dockerfile: Path) -> list[str]:
    """The Dockerfile's instructions, comments removed and continued lines joined."""
    lines = [line for line in dockerfile.read_text().splitlines() if not line.startswith("#")]
    joined = re.sub(r"\\\n\s*", " ", "\n".join(lines))
    return [line.strip() for line in joined.splitlines() if line.strip()]


def _last_stage(dockerfile: Path) -> list[str]:
    instructions = _instructions(dockerfile)
    start = max(i for i, line in enumerate(instructions) if line.startswith("FROM "))
    return instructions[start:]


def _env(stage: list[str]) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in stage:
        if line.startswith("ENV "):
            pairs.update(pair.split("=", 1) for pair in line.removeprefix("ENV ").split())
    return pairs


def test_the_service_image_sets_no_configuration():
    """The image listens on loopback because the settings' default does; nothing moves it."""
    text = "\n".join(_instructions(SERVICE))

    assert "FREYJA_" not in text.replace("FREYJA_PORT', '8000'", "")
    assert _last_stage(SERVICE)[-1] == 'CMD ["freyja-api"]'


def test_the_service_image_lets_its_user_write_where_the_service_writes():
    """The workspace's cases and a named caller's files (feature 009, FR-009a): made in the
    image and given to the user the service runs as, and nothing else of /app."""
    stage = _last_stage(SERVICE)
    made = [" ".join(line.split()) for line in stage if line.startswith("RUN mkdir")]
    assert made == [
        "RUN mkdir -p /app/workspace/cases /app/caller-files "
        "&& chown 65532:65532 /app/workspace/cases /app/caller-files"
    ]


def test_the_web_image_listens_on_loopback_unless_told_otherwise():
    env = _env(_last_stage(WEB))

    assert env["HOSTNAME"] == "127.0.0.1"
    assert env["PORT"] == "3000"


def test_neither_image_runs_as_root():
    for dockerfile in (SERVICE, WEB):
        users = [line for line in _last_stage(dockerfile) if line.startswith("USER ")]

        assert users == ["USER 65532:65532"], dockerfile


def test_the_web_image_is_built_as_a_standalone_server_for_the_default_address():
    text = "\n".join(_instructions(WEB))

    assert "FREYJA_WEB_STANDALONE=1" in text
    assert "ARG FREYJA_API_URL=http://127.0.0.1:8000" in text
    assert _last_stage(WEB)[-1] == 'CMD ["node", "server.js"]'


def test_the_service_image_installs_what_the_lock_file_names_and_no_more():
    syncs = [line for line in _instructions(SERVICE) if line.startswith("RUN uv sync")]

    assert len(syncs) == 2
    assert all("--locked" in line and "--no-dev" in line for line in syncs)


def test_only_named_files_reach_the_service_image():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    rules = [line for line in lines if line and not line.startswith("#")]

    assert rules[0] == "*", "everything is left out first"
    kept = {line for line in rules if line.startswith("!")}
    assert kept == {"!pyproject.toml", "!uv.lock", "!README.md", "!src/", "!workspace/skills/"}


def test_a_deployment_s_values_never_reach_the_web_image():
    rules = (ROOT / "web/.dockerignore").read_text().splitlines()

    assert {".env", ".env.*", "node_modules/", ".next/"} <= set(rules)


def test_the_scanners_are_the_same_version_on_github_and_on_gitlab():
    github = yaml.safe_load((ROOT / ".github/workflows/scan.yml").read_text())["env"]
    gitlab = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())

    assert gitlab["trivy"]["image"]["name"] == f"aquasec/trivy:{github['TRIVY_VERSION']}"
    assert gitlab["semgrep"]["image"] == f"semgrep/semgrep:{github['SEMGREP_VERSION']}"


def test_the_scans_ask_the_same_on_github_and_on_gitlab():
    workflow = yaml.safe_load((ROOT / ".github/workflows/scan.yml").read_text())["jobs"]
    gitlab = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())

    def flags(command: str, tool: str) -> list[str]:
        words = " ".join(command.replace("\\\n", " ").split())
        return words[words.index(f"{tool} ") :].split()[1:]

    def step(job: str, name: str) -> str:
        return next(s["run"] for s in workflow[job]["steps"] if s.get("name") == name)

    assert flags(step("trivy", "Run Trivy over the source"), "fs") == flags(
        gitlab["trivy"]["script"][0], "fs"
    )
    assert flags(step("semgrep", "Run Semgrep"), "scan") == flags(
        gitlab["semgrep"]["script"][0], "scan"
    )
    assert "--metrics=off" in gitlab["semgrep"]["script"][0]
