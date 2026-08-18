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
# The id-crm-ticketing repo's own git commit that produced this image (short
# SHA, passed by the caller — see README's "Build & push" sections). Chatwoot
# reads a static `.git_sha` file baked into the upstream base image to render
# "vX.Y.Z Build <sha>" in its UI (config/initializers/git_sha.rb); left alone,
# every one of OUR custom builds shows the SAME upstream release commit no
# matter how many of our own patches are actually in the image. Stage 2
# overwrites that file with this value so the on-screen build id actually
# reflects what's deployed.
ARG PROTON_BUILD_SHA=unknown

# ── Stage 1: patch + rebuild frontend ───────────────────────────────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS builder

# Re-declare so it is in scope after FROM.
ARG UPSTREAM_VERSION
ARG PROTON_BUILD_SHA

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
# Patch 0069's softphone needs Twilio's Voice JS SDK, which upstream does not
# depend on. Added AFTER the frozen install so upstream's dependency graph
# stays byte-reproducible and exactly one dependency is added, explicitly and
# pinned. Editing package.json in the patch instead would break
# --frozen-lockfile, since we cannot regenerate pnpm-lock.yaml here.
RUN pnpm add @twilio/voice-sdk@2.18.3
RUN pnpm exec vite build

# Overwrite the baked-in upstream .git_sha with our own build identifier —
# see the PROTON_BUILD_SHA comment above.
RUN echo "${PROTON_BUILD_SHA}" > /app/.git_sha

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

# Overwrite the baked-in upstream .git_sha so the in-app "Build <sha>" display
# (and GET /api) reflects OUR commit, not upstream's frozen v4.15.1 release
# commit — see the PROTON_BUILD_SHA comment near the top of this file.
COPY --from=builder /app/.git_sha /app/.git_sha

# Proton-fork label for audit trails. Patch list isn't enumerated here (it
# drifted stale in the past) — `docker inspect` this image's
# org.proton.chatwoot.build label, then `git show <sha>:deploy/chatwoot-fork/patches/`
# in id-crm-ticketing for the exact patch set that commit shipped.
ARG UPSTREAM_VERSION
ARG PROTON_BUILD_SHA
LABEL org.proton.chatwoot.upstream="${UPSTREAM_VERSION}" \
      org.proton.chatwoot.build="${PROTON_BUILD_SHA}"
