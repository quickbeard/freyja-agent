#!/bin/sh
# The biome job: lint and formatting of the web app. It reports and changes nothing; the
# commit hook is where files get fixed. Called by the biome job of .github/workflows/lint.yml
# and by the biome job of .gitlab-ci.yml, after each has installed pnpm and the dependencies
# of web/.
set -eu
cd "$(dirname "$0")/../../web"

# biome check: lint and formatting in one command.
pnpm lint
