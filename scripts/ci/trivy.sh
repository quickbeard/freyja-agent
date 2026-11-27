#!/bin/sh
# The trivy job: known vulnerabilities in what uv.lock and web/pnpm-lock.yaml install, and
# mistakes in the Dockerfiles. Secrets are gitleaks' job. Called by the trivy job of
# .github/workflows/scan.yml, which downloads Trivy and puts it on the path, and by the trivy
# job of .gitlab-ci.yml, whose image carries it. Each names the version.
set -eu
cd "$(dirname "$0")/../.."

trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 1 \
  --skip-dirs .venv --skip-dirs web/node_modules --skip-dirs web/.next .
