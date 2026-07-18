# syntax=docker/dockerfile:1
# -------------------------------------------------------------------
# Proton-patched Chatwoot image.
# Build off-VM (Mac or Cloud Build) — never on the 16 GB production VM.
#
# Usage:
#   UPSTREAM=$(cat UPSTREAM_VERSION)
#   docker build --build-arg UPSTREAM_VERSION=$UPSTREAM \
#     -t proton-chatwoot:$UPSTREAM-custom .
# -------------------------------------------------------------------

ARG UPSTREAM_VERSION=v4.15.1

# ── Stage 1: patch + build ──────────────────────────────────────────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS builder

# Re-declare so it is in scope after FROM.
ARG UPSTREAM_VERSION

USER root

# Install git (needed only for patch apply — not in the Chatwoot base image).
RUN apt-get update -qq && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Initialise a throwaway git repo so git-apply works against the working tree.
RUN git init -q && git add -A && git commit -q -m "upstream ${UPSTREAM_VERSION}"

# Apply our patches in order. Each must apply cleanly; if any fails the build
# errors here — guaranteeing the final image always has all three patches.
COPY patches/ /tmp/proton-patches/
RUN git apply --whitespace=fix /tmp/proton-patches/0001-runtime-config.patch && \
    git apply --whitespace=fix /tmp/proton-patches/0002-ai-assist-backend.patch && \
    git apply --whitespace=fix /tmp/proton-patches/0003-proton-nav-menu.patch

# Re-build the frontend (Vite) with the patched source.
# NODE_OPTIONS prevents OOM on machines with ≤4 GB free; bump if build stalls.
ENV NODE_OPTIONS="--max-old-space-size=3072"
RUN yarn install --frozen-lockfile && yarn build

# ── Stage 2: runtime (reuse the same base, copy rebuilt assets) ─────
FROM chatwoot/chatwoot:${UPSTREAM_VERSION} AS runtime

# Replace the pre-built public/packs (and public/vite if Vite-era) from stage 1.
COPY --from=builder /app/public/packs /app/public/packs
# Include Vite output dir if it exists (Chatwoot ≥v3.8 uses Vite).
COPY --from=builder /app/public/vite  /app/public/vite

# Proton-fork label for audit trails.
ARG UPSTREAM_VERSION
LABEL org.proton.chatwoot.upstream="${UPSTREAM_VERSION}" \
      org.proton.chatwoot.patch="0001,0002,0003"
