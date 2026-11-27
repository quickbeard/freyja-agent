#!/bin/sh
# The gitleaks job: secrets in any commit. The rules, and the made-up keys of the tests that
# are let through, are in .gitleaks.toml. Called by the gitleaks job of
# .github/workflows/secrets.yml, which downloads gitleaks and puts it on the path, and by the
# gitleaks job of .gitlab-ci.yml, whose image carries it. Each names the version, as the
# commit hook does, and each checks out every commit, not only the last.
set -eu
cd "$(dirname "$0")/../.."

# --redact: a finding is reported without the value, which a log would keep.
gitleaks git --redact --no-banner .
