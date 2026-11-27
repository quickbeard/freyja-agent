#!/bin/sh
# The pyright job: the types of the service's code and of its tests, with the settings of
# pyproject.toml. The commit hook runs the same command. Called by the pyright job of .github/workflows/lint.yml
# and by the pyright job of .gitlab-ci.yml, after each has installed from uv.lock.
set -eu
cd "$(dirname "$0")/../.."

# Over src/ and tests/, as pyproject.toml has it.
uv run pyright
