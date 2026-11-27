#!/bin/sh
# The pytest job: the service's suite. Called by the pytest job of
# .github/workflows/unit-tests.yml and by the pytest job of .gitlab-ci.yml, after each has
# installed from uv.lock, started a Postgres of its own, and named it in
# FREYJA_TEST_DATABASE_URI.
set -eu
cd "$(dirname "$0")/../.."

# The Postgres tests under tests/integration run with the rest, against the database the
# variable names; without them the run falls below the floor. --cov: with coverage, held to
# fail_under in pyproject.toml.
uv run pytest --cov
