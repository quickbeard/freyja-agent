#!/bin/sh
# The playwright job: the web app's end-to-end tests. Called by the job of
# .github/workflows/playwright.yml, which downloads the browsers first, and by the playwright
# job of .gitlab-ci.yml, whose image carries them, after each has installed pnpm and the
# dependencies of web/.
set -eu
cd "$(dirname "$0")/../../web"

# Playwright builds the app and serves it itself (web/playwright.config.ts).
pnpm exec playwright test
