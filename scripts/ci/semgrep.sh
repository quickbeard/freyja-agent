#!/bin/sh
# The semgrep job: insecure code. Called by the semgrep job of .github/workflows/scan.yml,
# which installs Semgrep with uv and puts it on the path, and by the semgrep job of
# .gitlab-ci.yml, whose image carries it. Each names the version.
set -eu
cd "$(dirname "$0")/../.."

# --error: any finding fails. --metrics=off: nothing about the repository is sent; the rules
# are fetched, which is why they are named and `auto` is not used.
semgrep scan --metrics=off --error \
  --config p/default --config p/security-audit --config p/owasp-top-ten \
  --config p/dockerfile .
