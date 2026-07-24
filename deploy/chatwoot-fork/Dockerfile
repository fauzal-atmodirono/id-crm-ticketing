# syntax=docker/dockerfile:1
# -------------------------------------------------------------------
# Proton-patched Chatwoot image.
# Build off-VM (Mac or Cloud Build) — never on the 16 GB production VM.
#
# Usage:
#   UPSTREAM=$(cat UPSTREAM_VERSION)
#   docker build --build-arg UPSTREAM_VERSION=$UPSTREAM \
#     -t proton-chatwoot:$UPSTREAM-custom .
#
# NOTE on the base image (verified against chatwoot/chatwoot:v4.15.1):
#   * OS is Alpine (apk), NOT Debian — so no apt-get.
#   * git is already installed.
#   * The package manager is pnpm (pinned via package.json "packageManager"),
#     NOT yarn. pnpm/npm/yarn are not on PATH, but `corepack` ships bundled with
#     node under /usr/local/lib/node_modules/corepack and can activate the exact
#     pnpm the lockfile expects.
#   * There is no `build` npm script — Chatwoot compiles the frontend with Vite
#     (via vite_ruby). Running `vite build` regenerates public/vite, which is the
#     only artifact our patches change.
# -------------------------------------------------------------------

ARG UPSTREAM_VERSION=v4.15.1

# ── Stage 1: patch + rebuild frontend ───────────────────────────────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS builder

# Re-declare so it is in scope after FROM.
ARG UPSTREAM_VERSION

USER root
WORKDIR /app

# Activate pnpm via the bundled corepack, then drop a shim on PATH.
# `corepack enable` fails in this image (it looks for a `corepack` binary on
# PATH to symlink and there isn't one), so we `prepare` the pinned version and
# invoke pnpm through corepack directly via a tiny wrapper.
ENV COREPACK_ENABLE_DOWNLOAD_PROMPT=0
RUN node /usr/local/lib/node_modules/corepack/dist/corepack.js prepare pnpm@10.2.0 --activate \
 && printf '#!/bin/sh\nexec node /usr/local/lib/node_modules/corepack/dist/corepack.js pnpm "$@"\n' > /usr/local/bin/pnpm \
 && chmod +x /usr/local/bin/pnpm \
 && pnpm --version

# Initialise a throwaway git repo so git-apply works against the working tree.
RUN git init -q \
 && git config user.email build@proton.local \
 && git config user.name proton-build \
 && git add -A \
 && git commit -q -m "upstream ${UPSTREAM_VERSION}"

# Apply our patches in order. Each must apply cleanly; if any fails the build
# errors here — guaranteeing the final image always has every patch.
COPY patches/ /tmp/proton-patches/
RUN for p in /tmp/proton-patches/*.patch; do \
      echo "applying $(basename "$p")"; \
      git apply --whitespace=fix "$p" || exit 1; \
    done

# Install frontend deps and rebuild the Vite bundle with the patched source.
# NODE_OPTIONS prevents OOM on machines with ≤4 GB free; bump if build stalls.
ENV NODE_OPTIONS="--max-old-space-size=3072"
RUN pnpm install --frozen-lockfile
RUN pnpm exec vite build

# ── Stage 2: runtime (reuse the same base, copy rebuilt assets) ─────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS runtime

# Most patches only change compiled frontend output, which Vite emits to
# public/vite — replace it with the rebuilt bundle from the builder stage.
# (Knowledge patches 0009-0011 are pure app/javascript, so they land here too.)
COPY --from=builder /app/public/vite /app/public/vite

# Patch 0001 ALSO edits a server-side Rails view to inject window.__PROTON_CONFIG__
# from the PROTON_* env vars. That file is not part of public/vite, so it must be
# copied explicitly or the config global is never emitted and every feature gate
# stays off.
COPY --from=builder /app/app/views/layouts/vueapp.html.erb /app/app/views/layouts/vueapp.html.erb

# Proton-fork label for audit trails.
ARG UPSTREAM_VERSION
LABEL org.proton.chatwoot.upstream="${UPSTREAM_VERSION}" \
      org.proton.chatwoot.patch="0001,0002,0003,0004,0005,0006,0007,0008,0009,0010,0011,0012,0013,0014,0015,0016,0017,0018,0019,0020"
