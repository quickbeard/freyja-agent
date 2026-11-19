# The web console: the Next.js app, built as a standalone server.
#
# Build from this directory:
#
#   docker build -t freyja-web web
#
# The console forwards /api/... to the Freyja service at FREYJA_API_URL. Next.js writes that
# address into the build, so it is given when the image is built and a container cannot
# change it. The default, http://127.0.0.1:8000, is right where the service's container
# shares the network namespace: the same pod, or `--network host`. For another address:
#
#   docker build --build-arg FREYJA_API_URL=http://freyja-agent:8000 -t freyja-web web
#
# The console has no login and can change the service's configuration, so the server listens
# on 127.0.0.1:3000 inside the container. Set HOSTNAME=0.0.0.0 only where the network around
# the container already decides who may reach it; `docker run -p` needs it.

# The image can be taken from a mirror: --build-arg BASE_IMAGE=...
#
# The base is Chainguard's Wolfi: its packages are rebuilt as fixes land, so an image built
# today carries few known vulnerabilities, where a Debian-based Node image carries dozens
# that have no fix yet. It has one tag, latest; Node is pinned by the package's name.
ARG BASE_IMAGE=cgr.dev/chainguard/wolfi-base:latest

# The same Node for the build and for the image.
FROM ${BASE_IMAGE} AS node

# The base pins each of its packages to the version it was built with, and a mirror may
# serve a base that is months old. The pins are dropped and the packages brought up to the
# index Node is installed from, so the image starts from today's fixes.
RUN sed -i -E 's/=.*$//' /etc/apk/world \
    && apk upgrade --no-cache \
    && apk add --no-cache nodejs-26

FROM node AS builder

# npm only here, to install the pnpm that package.json names.
RUN apk add --no-cache npm \
    && npm install --global pnpm@12.21.0

WORKDIR /app

# The dependencies first, from the lock file alone: this layer is built again only when the
# lock file changes.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

ARG FREYJA_API_URL=http://127.0.0.1:8000
# FREYJA_WEB_STANDALONE asks next.config.ts for a build that carries the few files of
# node_modules it needs, so the next stage holds neither node_modules nor the source.
ENV FREYJA_API_URL=${FREYJA_API_URL} \
    FREYJA_WEB_STANDALONE=1 \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build


FROM node AS runtime

# HOSTNAME and PORT are read by Next.js's standalone server. Docker sets HOSTNAME to the
# container's own name unless it is given, and the server would then listen on the
# container's address.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=127.0.0.1 \
    PORT=3000

WORKDIR /app

# The standalone server, and the static files it serves itself.
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static

# The base's own unprivileged user, by number, so an orchestrator can check it is not root.
# The files stay root's: the server reads them and writes none.
USER 65532:65532

EXPOSE 3000

# Asked on loopback, which the server listens on whatever HOSTNAME is, short of another
# single address.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["node", "-e", "fetch('http://127.0.0.1:' + (process.env.PORT || 3000) + '/llm').then((r) => process.exit(r.ok ? 0 : 1), () => process.exit(1))"]

CMD ["node", "server.js"]
