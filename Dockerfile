# The Freyja service: the FastAPI application, started with `freyja-api`.
#
# Build from the repository root:
#
#   docker build -t freyja-agent .
#
# The image holds no configuration. Everything the service reads comes from FREYJA_*
# variables when a container starts (.env.example), so one image runs everywhere.
#
# The service listens on 127.0.0.1:8000 inside the container, as it does on a developer's
# machine: its API has no login. Something in the same network namespace reaches it there,
# such as the web app's container in the same pod, or everything on the host with
# `--network host`. Set FREYJA_HOST=0.0.0.0 only where the network around the container
# already decides who may reach it.

# Both images can be taken from a mirror: --build-arg BASE_IMAGE=... --build-arg UV_IMAGE=...
#
# The base is Chainguard's Wolfi: its packages are rebuilt as fixes land, so an image built
# today carries few known vulnerabilities, where a Debian-based Python image carries dozens
# that have no fix yet. It has one tag, latest; the Python is pinned by the package's name.
ARG BASE_IMAGE=cgr.dev/chainguard/wolfi-base:latest
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.21

FROM ${UV_IMAGE} AS uv

# The Python that .python-version names, for the build and for the image alike.
FROM ${BASE_IMAGE} AS python

# The base pins each of its packages to the version it was built with, and a mirror may
# serve a base that is months old. The pins are dropped and the packages brought up to the
# index Python is installed from, so that Python is not built against a newer C library than
# the image has, and the image starts from today's fixes.
RUN sed -i -E 's/=.*$//' /etc/apk/world \
    && apk upgrade --no-cache \
    && apk add --no-cache python-3.12 \
    && python3.12 -c 'import ssl, sqlite3, zlib, ctypes'

FROM python AS builder

# The environment is built where it will run, /app/.venv, with the Python of the image and
# files copied into it, so the next stage takes it as it is.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

# The dependencies first, from the lock file alone: this layer is built again only when the
# lock file changes. --locked fails when uv.lock is out of step with pyproject.toml.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Then the service itself, installed into the environment and not linked to the source.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev --no-editable


FROM python AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

# The orchestrator's file root (FREYJA_WORKSPACE_DIR, `workspace` under the working
# directory). Its skills are source and come with the image. cases/ is written while the
# service runs and is the one directory it needs to write to: mount a volume there to keep
# what it writes.
COPY workspace/skills /app/workspace/skills
RUN mkdir -p /app/workspace/cases && chown 65532:65532 /app/workspace/cases

# The base's own unprivileged user, by number, so an orchestrator can check it is not root.
USER 65532:65532

EXPOSE 8000

# /healthz answers without the database or a model. It is asked on loopback, which the
# service listens on whatever FREYJA_HOST is.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('FREYJA_PORT', '8000') + '/healthz', timeout=3)"]

CMD ["freyja-api"]
