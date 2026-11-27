#!/bin/sh
# The vitest job: the web app's unit tests. Called by the vitest job of
# .github/workflows/unit-tests.yml and by the vitest job of .gitlab-ci.yml, after each has
# installed pnpm and the dependencies of web/.
set -eu
cd "$(dirname "$0")/../../web"

# With coverage, held to the thresholds in web/vitest.config.mts.
pnpm test:coverage
