"""The secret scan is set up the same way wherever it runs, and lets only made-up keys through.

gitleaks runs in three places: the commit hook, GitHub's workflow, and GitLab's job. Each
names the version on its own, and the three are kept equal by hand. Its allowlist is the one
thing this repository adds to its rules, and a loose entry would hide a real key.
"""

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

# The made-up keys the tests use, as they stand in the test files.
MADE_UP = (
    "sk-recognizable-key-0001",
    "sk-recognizable-other-0002",
    "sk-environment-key-9f2c",
    "sk-environment-secret-9f2c",
)
# Values that only look like them, and a provider's key as it would be pasted.
NOT_MADE_UP = (
    "sk-recognizable-key-00019aF3kLmQ2xZ7vB1nC5dE8gH",
    "prefix-sk-recognizable-key-0001",
    "sk-environment-key-9f2c-and-more",
    "sk-proj-9aF3kLmQ2xZ7vB1nC5dE8gH4jK6pR0sT",
)


def _allowlist() -> dict:
    return tomllib.loads((ROOT / ".gitleaks.toml").read_text())["allowlist"]


def _versions() -> dict[str, str]:
    hooks = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())["repos"]
    hook = next(repo for repo in hooks if repo["repo"].endswith("/gitleaks/gitleaks"))
    workflow = yaml.safe_load((ROOT / ".github/workflows/secrets.yml").read_text())
    gitlab = yaml.safe_load((ROOT / ".gitlab-ci.yml").read_text())
    return {
        "commit hook": hook["rev"].removeprefix("v"),
        "GitHub": workflow["jobs"]["gitleaks"]["env"]["GITLEAKS_VERSION"],
        "GitLab": gitlab["gitleaks"]["image"]["name"].rpartition(":v")[2],
    }


def test_the_three_places_run_the_same_gitleaks():
    versions = _versions()

    assert len(set(versions.values())) == 1, versions
    assert re.fullmatch(r"\d+\.\d+\.\d+", versions["GitHub"]), versions


def test_the_default_rules_are_kept():
    config = tomllib.loads((ROOT / ".gitleaks.toml").read_text())

    assert config["extend"] == {"useDefault": True}
    # No rule of gitleaks' own is switched off or replaced.
    assert "rules" not in config
    assert "disabledRules" not in config["extend"]


def test_a_key_is_let_through_by_its_value_and_never_by_its_path():
    allowlist = _allowlist()

    assert set(allowlist) <= {"description", "regexes"}, "no path, commit, or stop word"
    # Each pattern is tried against the secret that was found, from its start to its end.
    assert "regexTarget" not in allowlist
    for pattern in allowlist["regexes"]:
        assert pattern.startswith("^") and pattern.endswith("$"), pattern


def test_the_made_up_keys_are_let_through_and_nothing_like_them():
    patterns = [re.compile(pattern) for pattern in _allowlist()["regexes"]]

    def allowed(value: str) -> bool:
        return any(pattern.search(value) for pattern in patterns)

    assert all(allowed(value) for value in MADE_UP)
    assert not any(allowed(value) for value in NOT_MADE_UP)
