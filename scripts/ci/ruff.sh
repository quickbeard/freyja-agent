#!/bin/sh
# The ruff job: lint and formatting of the Python code. Both commands report and change
# nothing; the commit hook is where files get fixed. Called by the ruff job of
# .github/workflows/lint.yml and by the ruff job of .gitlab-ci.yml, after each has installed
# from uv.lock.
set -eu
cd "$(dirname "$0")/../.."

# On GitHub a finding is written as an annotation on the line it is about.
uv run ruff check ${GITHUB_ACTIONS:+--output-format=github} .
uv run ruff format --check .
