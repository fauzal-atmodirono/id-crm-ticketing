# Phase 0 — Fork Foundation & Captain-AI Replacement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a reproducible build pipeline that produces `proton-chatwoot:v4.15.1-custom` — a patched Chatwoot image with per-tenant runtime config, a rewired AI-assist menu backed by three new backend endpoints, and a Proton left-nav — then deploy it to the `default` tenant and replicate to `proton`/`wahchan`.

**Architecture:** Three frontend patches are applied at image-build time (off-VM, on Mac or Cloud Build) against the pinned MIT community source `v4.15.1`; per-tenant behaviour is driven purely by three env vars (`PROTON_BACKEND_URL/KEY/FEATURES`) the container already receives, so a single image serves all tenants with no rebuild. The backend (`proton-conversational-ai`) gains a new `assist` router with three endpoints (`/assist/suggest`, `/assist/summarize`, `/assist/ask`) following the existing `x-api-key` / Pydantic-settings / `build_*_router` patterns.

**Tech Stack:** Docker multi-stage build (Node 20 + Ruby 3.3 builder → Chatwoot runtime), `git apply` patch-set, Google Cloud Build YAML, FastAPI + Pydantic v2, `google-genai` (Gemini 2.5 Flash), pytest-asyncio, Docker Compose v2.

## Global Constraints

- Chatwoot upstream pin: **v4.15.1** (never auto-upgrade; track in `UPSTREAM_VERSION`).
- Custom image tag: `…-docker.pkg.dev/lv-playground-genai/<repo>/proton-chatwoot:v4.15.1-custom`.
- **MIT-community only** — patches must never touch `chatwoot/enterprise/` or any file under `/enterprise`.
- Build runs **off-VM** (Mac `docker build` or Cloud Build); never on the 16 GB VM.
- Backend Python ≥ 3.12, FastAPI ≥ 0.115, Pydantic ≥ 2.8, `google-genai ≥ 0.1.1`.
- All new backend endpoints are guarded by `x-api-key` (constant-time `hmac.compare_digest`), matching the pattern in `faq_admin_router.py`.
- `PROTON_BACKEND_KEY` in `Settings` must NOT be empty for `/assist/*` — if empty the endpoint returns 503 at startup rather than 401 at runtime.
- Rollback = revert `CHATWOOT_IMAGE` in the tenant env and `docker compose up -d chatwoot-rails chatwoot-sidekiq` — no schema changes.
- `pytest` root: `proton-conversational-ai/apps/backend/`; `asyncio_mode = "auto"`; `testpaths = ["src"]`.
- No new Python dependencies beyond what is already in `pyproject.toml` (Gemini client already present as `google-genai`).

---

## File Structure

### Repo: `id-crm-ticketing`

| Path | Action | Responsibility |
|---|---|---|
| `deploy/chatwoot-fork/UPSTREAM_VERSION` | Create | Single-line pin `v4.15.1` — the canonical version string read by Dockerfile and Cloud Build |
| `deploy/chatwoot-fork/Dockerfile` | Create | Multi-stage: clone tag → apply patches → Node/Ruby frontend build → runtime image |
| `deploy/chatwoot-fork/cloudbuild.yaml` | Create | Cloud Build substitute for Mac-less CI; uses `UPSTREAM_VERSION`; pushes to Artifact Registry |
| `deploy/chatwoot-fork/patches/0001-runtime-config.patch` | Create (by engineer from `git diff`) | Injects `window.__PROTON_CONFIG__` from env into the served page |
| `deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch` | Create (by engineer from `git diff`) | Rewires the reply-box ✨ menu to `PROTON_BACKEND_URL` + hides Captain nav item |
| `deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch` | Create (by engineer from `git diff`) | Adds four left-nav items → generic iframe host route |
| `deploy/chatwoot-fork/README.md` | Create | Local dev loop, build/push instructions, upgrade runbook |
| `deploy/docker-compose.tenant.yml` | Modify | Parameterise `image:` for chatwoot-rails + chatwoot-sidekiq; add PROTON_* to `x-chatwoot-env` |
| `deploy/tenants/example.env` | Modify | Add `CHATWOOT_IMAGE`, `PROTON_BACKEND_URL`, `PROTON_BACKEND_KEY`, `PROTON_FEATURES` |

### Repo: `proton-conversational-ai/apps/backend`

| Path | Action | Responsibility |
|---|---|---|
| `src/chatbot/features/assist/router.py` | Create | `build_assist_router()` — three POST endpoints, `x-api-key` auth, Gemini calls |
| `src/chatbot/features/assist/test_assist_router.py` | Create | pytest: auth guard, suggest/summarize/ask happy paths, Gemini mock |
| `src/chatbot/platform/config.py` | Modify | Add `proton_backend_key`, `assist_gemini_model` fields to `Settings` |
| `src/chatbot/main.py` | Modify | Wire `build_assist_router()` into `bootstrap_application()` |

---

## Tasks

### Task 1: Compose & env parameterisation

**Files:**
- Modify: `deploy/docker-compose.tenant.yml`
- Modify: `deploy/tenants/example.env`

**Interfaces:**
- Consumes: existing `x-chatwoot-env` anchor and `chatwoot-rails` / `chatwoot-sidekiq` service definitions
- Produces:
  - `${CHATWOOT_IMAGE}` variable consumed by Tasks 3 and 7
  - `PROTON_BACKEND_URL`, `PROTON_BACKEND_KEY`, `PROTON_FEATURES` env vars passed into chatwoot containers (consumed by patch 0001 via the Rails view, Tasks 4–6)

- [ ] **Step 1: Open the compose file and locate the two `image:` lines**

  File: `deploy/docker-compose.tenant.yml`. The two lines to change are:
  ```yaml
  image: chatwoot/chatwoot:v4.15.1   # chatwoot-rails (line 89)
  image: chatwoot/chatwoot:v4.15.1   # chatwoot-sidekiq (line 114)
  ```

- [ ] **Step 2: Parameterise both image references and add PROTON_* to the shared env anchor**

  In `deploy/docker-compose.tenant.yml`, make these exact changes:

  Change both `image: chatwoot/chatwoot:v4.15.1` lines to:
  ```yaml
  image: ${CHATWOOT_IMAGE:-chatwoot/chatwoot:v4.15.1}
  ```

  Add to the `x-chatwoot-env` anchor (after `WEB_CONCURRENCY: 1` / `RAILS_MAX_THREADS: 5`):
  ```yaml
    PROTON_BACKEND_URL: ${PROTON_BACKEND_URL:-}
    PROTON_BACKEND_KEY: ${PROTON_BACKEND_KEY:-}
    PROTON_FEATURES: ${PROTON_FEATURES:-}
  ```

- [ ] **Step 3: Add the new variables to `deploy/tenants/example.env`**

  Append after the `ZAMMAD_AI_DRAFTS=true` line:

  ```dotenv
  # --- Proton fork image & AI-assist -------------------------------------------
  # Override to use the patched Chatwoot image. Empty → stock chatwoot/chatwoot:v4.15.1
  CHATWOOT_IMAGE=
  # Base URL of this tenant's proton-conversational-ai backend (no trailing slash).
  # Only "proton" tenant has one today; others leave blank (feature stays disabled).
  PROTON_BACKEND_URL=
  # API key matching PROTON_BACKEND_KEY in the backend's Settings.
  PROTON_BACKEND_KEY=
  # Comma-separated feature flags. Example: ai_assist,nav_menu
  # Empty → no Proton patches active even if the custom image is used.
  PROTON_FEATURES=
  ```

- [ ] **Step 4: Verify compose parses without error**

  ```bash
  cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy
  docker compose -f docker-compose.tenant.yml --env-file tenants/example.env config --quiet
  ```
  Expected: exits 0, no output. Any YAML error prints to stderr.

- [ ] **Step 5: Commit**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/docker-compose.tenant.yml deploy/tenants/example.env
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "feat(compose): parameterise CHATWOOT_IMAGE + add PROTON_* env for fork"
  ```

---

### Task 2: Fork pipeline scaffold (`chatwoot-fork/`)

**Files:**
- Create: `deploy/chatwoot-fork/UPSTREAM_VERSION`
- Create: `deploy/chatwoot-fork/Dockerfile`
- Create: `deploy/chatwoot-fork/cloudbuild.yaml`
- Create: `deploy/chatwoot-fork/patches/` (directory + `.gitkeep` — patches added in Tasks 4–6)

**Interfaces:**
- Consumes: `UPSTREAM_VERSION` string `v4.15.1`
- Produces: Docker image `proton-chatwoot:v4.15.1-custom` (local tag), pushed as `REGISTRY/proton-chatwoot:v4.15.1-custom` in Cloud Build

- [ ] **Step 1: Create the version pin**

  Create `deploy/chatwoot-fork/UPSTREAM_VERSION`:
  ```
  v4.15.1
  ```
  (one line, no trailing newline needed — `cat` trims it in the Dockerfile)

- [ ] **Step 2: Create the Dockerfile**

  Create `deploy/chatwoot-fork/Dockerfile`:

  ```dockerfile
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
  ```

  > **Note for engineer:** The exact paths (`public/packs` vs `public/vite`) depend on which bundler the upstream tag uses. Verify with `docker run --rm chatwoot/chatwoot:v4.15.1 ls /app/public/` and adjust COPY lines if needed before finalising the Dockerfile.

- [ ] **Step 3: Create the Cloud Build YAML**

  Create `deploy/chatwoot-fork/cloudbuild.yaml`:

  ```yaml
  # Cloud Build pipeline for the Proton-patched Chatwoot image.
  # Trigger manually or on push to the chatwoot-fork/ subtree.
  #
  # Required substitutions (set in the Cloud Build trigger or --substitutions flag):
  #   _REGISTRY   e.g. asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  #
  # Run manually:
  #   gcloud builds submit deploy/chatwoot-fork/ \
  #     --config deploy/chatwoot-fork/cloudbuild.yaml \
  #     --substitutions _REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images

  substitutions:
    _REGISTRY: asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images

  steps:
    - id: read-version
      name: bash
      script: |
        VERSION=$(cat UPSTREAM_VERSION)
        echo "UPSTREAM_VERSION=${VERSION}" >> /workspace/build.env
        echo "Building proton-chatwoot:${VERSION}-custom"

    - id: build
      name: gcr.io/cloud-builders/docker
      entrypoint: bash
      args:
        - -c
        - |
          source /workspace/build.env
          docker build \
            --build-arg UPSTREAM_VERSION=$${UPSTREAM_VERSION} \
            -t $${_REGISTRY}/proton-chatwoot:$${UPSTREAM_VERSION}-custom \
            .
      env:
        - _REGISTRY=${_REGISTRY}

    - id: push
      name: gcr.io/cloud-builders/docker
      entrypoint: bash
      args:
        - -c
        - |
          source /workspace/build.env
          docker push $${_REGISTRY}/proton-chatwoot:$${UPSTREAM_VERSION}-custom
      env:
        - _REGISTRY=${_REGISTRY}

  options:
    machineType: E2_HIGHCPU_8
    diskSizeGb: 50
    logging: CLOUD_LOGGING_ONLY
  ```

- [ ] **Step 4: Create the patches placeholder**

  ```bash
  mkdir -p /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches
  touch /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/patches/.gitkeep
  ```

- [ ] **Step 5: Commit the pipeline scaffold**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "feat(chatwoot-fork): add Dockerfile + Cloud Build pipeline scaffold"
  ```

---

### Task 3: Fork pipeline README

**Files:**
- Create: `deploy/chatwoot-fork/README.md`

**Interfaces:**
- Consumes: Dockerfile from Task 2, compose changes from Task 1
- Produces: operator runbook (referenced by the deploy DoD)

- [ ] **Step 1: Write the README**

  Create `deploy/chatwoot-fork/README.md`:

  ````markdown
  # Proton-patched Chatwoot fork pipeline

  Builds `proton-chatwoot:<VERSION>-custom` by applying three Git patches to the
  pinned upstream Community tag and rebuilding the frontend. **Never run the build
  on the 16 GB production VM** — use your Mac or Cloud Build.

  ## Pinned version

  `UPSTREAM_VERSION` (one line) controls the upstream tag used by the Dockerfile
  and Cloud Build. Current value: **v4.15.1**.

  ---

  ## Local dev loop (Mac)

  Use this when iterating on a patch. You get Vite HMR, so edits to the patched
  Vue components hot-reload in the browser.

  ### Prerequisites

  - Docker Desktop (Mac), Node 20, Ruby 3.3, Yarn 1.x
  - A local Postgres + Redis (the `dev-infra` compose below spins them up)

  ### 1 — Clone the upstream tag and apply patches

  ```bash
  VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)
  git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/proton-chatwoot-dev
  cd /tmp/proton-chatwoot-dev

  git apply --whitespace=fix \
    /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0001-runtime-config.patch
  git apply --whitespace=fix \
    /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch
  git apply --whitespace=fix \
    /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch
  ```

  ### 2 — Start local infrastructure

  ```bash
  # From the repo root:
  docker compose -f deploy/chatwoot-fork/dev-infra.yml up -d
  # Provides: postgres:5432, redis:6379
  ```

  ### 3 — Start Chatwoot in dev mode

  ```bash
  cd /tmp/proton-chatwoot-dev
  cp .env.example .env
  # Edit .env — set DATABASE_URL, REDIS_URL, PROTON_BACKEND_URL, PROTON_BACKEND_KEY, PROTON_FEATURES
  bundle install
  yarn install
  # Terminal A:
  foreman start -f Procfile.dev
  # Or individually: rails s -p 3000 & vite dev
  ```

  Access at `http://localhost:3000`. Patched components hot-reload on save.

  ### 4 — Edit a patch and re-export

  After editing in `/tmp/proton-chatwoot-dev`:

  ```bash
  cd /tmp/proton-chatwoot-dev
  git diff HEAD -- app/javascript/path/to/changed/component.vue \
    > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/000X-your-patch.patch
  ```

  Or use `git format-patch` to regenerate all patches from your commits:

  ```bash
  git format-patch upstream/v4.15.1 --stdout -- app/javascript/ \
    | csplit - '/^From /' '{*}'   # splits into 0001-… 0002-… 0003-…
  # Move the output files into deploy/chatwoot-fork/patches/
  ```

  ---

  ## Build & push (Mac)

  ```bash
  REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  VERSION=$(cat deploy/chatwoot-fork/UPSTREAM_VERSION)

  cd deploy/chatwoot-fork
  docker build --build-arg UPSTREAM_VERSION=$VERSION \
    -t $REGISTRY/proton-chatwoot:$VERSION-custom .

  docker push $REGISTRY/proton-chatwoot:$VERSION-custom
  ```

  Expected build time: ~10–15 min (Node/Ruby deps + Vite build).

  ## Build & push (Cloud Build)

  ```bash
  gcloud builds submit deploy/chatwoot-fork/ \
    --config deploy/chatwoot-fork/cloudbuild.yaml \
    --substitutions _REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  ```

  ---

  ## Deploy to `default` tenant

  ```bash
  # 1. Edit deploy/tenants/default.env:
  CHATWOOT_IMAGE=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images/proton-chatwoot:v4.15.1-custom
  PROTON_BACKEND_URL=https://<backend-cloud-run-url>
  PROTON_BACKEND_KEY=<secret>
  PROTON_FEATURES=ai_assist,nav_menu

  # 2. Pull and restart only the Chatwoot services (sidekiq restarts too — it reads env at start):
  docker compose -p default -f deploy/docker-compose.tenant.yml \
    --env-file deploy/tenants/default.env \
    pull chatwoot-rails chatwoot-sidekiq

  docker compose -p default -f deploy/docker-compose.tenant.yml \
    --env-file deploy/tenants/default.env \
    up -d chatwoot-rails chatwoot-sidekiq

  # 3. Verify:
  curl -s http://crm.<PUBLIC_IP>.nip.io/auth/sign_in | grep -c "proton"
  # → Open Chatwoot in browser; open any conversation → ✨ button should appear.
  # → Left nav should show Reports / FAQ Admin / Agents / Cases items.
  ```

  ## Rollback

  ```bash
  # Revert CHATWOOT_IMAGE in default.env to the stock tag:
  CHATWOOT_IMAGE=chatwoot/chatwoot:v4.15.1

  docker compose -p default -f deploy/docker-compose.tenant.yml \
    --env-file deploy/tenants/default.env \
    up -d chatwoot-rails chatwoot-sidekiq
  ```

  No data migrations involved — the patches are purely additive UI.

  ---

  ## Upgrade runbook (bumping upstream version)

  1. Update `UPSTREAM_VERSION` to the new tag (e.g. `v4.16.0`).
  2. Clone the new tag locally and attempt to apply each patch:
     ```bash
     VERSION=v4.16.0
     git clone --depth 1 --branch "$VERSION" https://github.com/chatwoot/chatwoot.git /tmp/cw-upgrade
     cd /tmp/cw-upgrade
     git apply --whitespace=fix /path/to/patches/0001-runtime-config.patch
     ```
     If a patch fails with conflict, resolve in the cloned tree, re-export the patch, and repeat for 0002/0003.
  3. Rebuild the image with the new `UPSTREAM_VERSION`.
  4. Test locally, then push and deploy to `default` first.
  5. Replicate to `proton`/`wahchan` after smoke-test passes.
  ````

- [ ] **Step 2: Commit**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/README.md
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "docs(chatwoot-fork): add local dev loop, build/push, upgrade runbook"
  ```

---

### Task 4: Patch 0001 — Runtime config injection

**Files:**
- Create: `deploy/chatwoot-fork/patches/0001-runtime-config.patch` (engineer-authored, exported from `git diff`)

**Interfaces:**
- Consumes: `PROTON_BACKEND_URL`, `PROTON_BACKEND_KEY`, `PROTON_FEATURES` environment variables already present in the container (wired in Task 1)
- Produces: `window.__PROTON_CONFIG__ = { backendUrl, backendKey, features }` — a JS global read by patches 0002 and 0003

**What this patch adds (IP-safe description):**

The patch targets Chatwoot's Rails layout template — the file that renders the `<head>` and wraps every page (typically `app/views/layouts/app.html.erb` or the equivalent Vite-era entrypoint). We add a single `<script>` block that serialises three ENV vars into a JSON object:

```javascript
// Code WE add — inline <script> tag injected before </head>:
window.__PROTON_CONFIG__ = {
  backendUrl: "<%= ENV.fetch('PROTON_BACKEND_URL', '') %>",
  backendKey: "<%= ENV.fetch('PROTON_BACKEND_KEY', '') %>",
  features: "<%= ENV.fetch('PROTON_FEATURES', '') %>".split(',').filter(Boolean)
};
```

The patch also adds a tiny composable helper **we** write, e.g. `app/javascript/composables/useProtonConfig.js`:

```javascript
// useProtonConfig.js — OUR file, not Chatwoot source
export function useProtonConfig() {
  const cfg = window.__PROTON_CONFIG__ || { backendUrl: '', backendKey: '', features: [] };
  return {
    backendUrl: cfg.backendUrl,
    backendKey: cfg.backendKey,
    hasFeature: (name) => cfg.features.includes(name),
  };
}
```

**How to generate the patch file:**

```bash
cd /tmp/proton-chatwoot-dev      # the cloned+applied working tree
# After making the edits above:
git diff HEAD -- app/views/layouts/ app/javascript/composables/useProtonConfig.js \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0001-runtime-config.patch
```

**Verification (local dev):**

- [ ] **Step 1: Apply patch to the local clone and start Rails**

  ```bash
  cd /tmp/proton-chatwoot-dev
  git apply --whitespace=fix deploy/chatwoot-fork/patches/0001-runtime-config.patch
  PROTON_BACKEND_URL=http://localhost:8000 PROTON_FEATURES=ai_assist,nav_menu rails s -p 3000
  ```

- [ ] **Step 2: Confirm the global is injected**

  Open `http://localhost:3000` → DevTools Console:
  ```javascript
  window.__PROTON_CONFIG__
  // Expected: { backendUrl: "http://localhost:8000", backendKey: "", features: ["ai_assist", "nav_menu"] }
  ```

- [ ] **Step 3: Confirm `hasFeature` works**

  ```javascript
  // In console (after importing or running the composable inline):
  const { hasFeature } = window.__protonConfigDebug || {};
  // Simpler: just verify the array:
  window.__PROTON_CONFIG__.features.includes('ai_assist')  // → true
  window.__PROTON_CONFIG__.features.includes('captain_ai') // → false
  ```

- [ ] **Step 4: Commit the patch file**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/patches/0001-runtime-config.patch
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "feat(patch-0001): inject PROTON_BACKEND_URL/KEY/FEATURES as window global"
  ```

---

### Task 5: Patch 0002 — AI-assist backend rewire

**Files:**
- Create: `deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch` (engineer-authored, exported from `git diff`)

**Interfaces:**
- Consumes: `useProtonConfig()` composable from patch 0001 (`backendUrl`, `backendKey`, `hasFeature('ai_assist')`)
- Produces: reply-box ✨ menu calls `POST /assist/suggest|summarize|ask` on the backend; result is inserted into the reply-box input natively; Captain nav item hidden when `ai_assist` not in features

**What this patch adds (IP-safe description):**

The patch touches the reply-box AI-assist area — the component(s) that render the ✨ sparkle button and its dropdown menu (in Chatwoot's JavaScript, these live around `app/javascript/dashboard/components/ReplyEditor/` or similar). We do **not** rewrite Chatwoot's component — we replace the *handlers* for the three menu items.

**File we add — `app/javascript/dashboard/api/protonAssist.js`:**

```javascript
// protonAssist.js — OUR file
import { useProtonConfig } from '../../composables/useProtonConfig';

export async function callAssist(action, payload) {
  const { backendUrl, backendKey } = useProtonConfig();
  if (!backendUrl) throw new Error('PROTON_BACKEND_URL not configured');

  const response = await fetch(`${backendUrl}/assist/${action}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': backendKey,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const text = await response.text();
    throw new Error(`assist/${action} failed: ${response.status} ${text}`);
  }
  return response.json();
}
```

**Changes to the existing AI-assist component (describe only the code WE add):**

1. Import `useProtonConfig` and `callAssist` at the top of the file.
2. Inside the component's `setup()` (or `methods:`), add a guard:
   ```javascript
   const { hasFeature } = useProtonConfig();
   const protonEnabled = hasFeature('ai_assist');
   ```
3. Replace the three action handlers (`handleSuggestReply`, `handleSummarize`, `handleAskCopilot`) with our wrappers:
   ```javascript
   async function handleSuggestReply(conversationId) {
     const { hasFeature, backendUrl } = useProtonConfig();
     if (!hasFeature('ai_assist') || !backendUrl) return;
     try {
       const data = await callAssist('suggest', { conversation_id: conversationId });
       // `replyInput` is the existing ref/store for the reply-box text.
       // Use the component's native setter so plugins/events fire correctly.
       setReplyText(data.draft);
     } catch (err) {
       console.error('[proton-assist] suggest failed', err);
     }
   }

   async function handleSummarize(conversationId) {
     const { hasFeature, backendUrl } = useProtonConfig();
     if (!hasFeature('ai_assist') || !backendUrl) return;
     try {
       const data = await callAssist('summarize', { conversation_id: conversationId });
       setReplyText(data.summary);
     } catch (err) {
       console.error('[proton-assist] summarize failed', err);
     }
   }

   async function handleAskCopilot(conversationId, question) {
     const { hasFeature, backendUrl } = useProtonConfig();
     if (!hasFeature('ai_assist') || !backendUrl) return;
     try {
       const data = await callAssist('ask', { conversation_id: conversationId, question });
       setReplyText(data.answer);
     } catch (err) {
       console.error('[proton-assist] ask failed', err);
     }
   }
   ```
4. Hide the entire ✨ button when `!protonEnabled` (add `:v-if="protonEnabled"` or equivalent conditional).
5. Hide the "Captain" left-nav item: find the nav-item component that renders it (search for `captain` in `app/javascript/dashboard/components/layout/Sidebar*`) and add a `v-if="!protonEnabled"` guard that uses the same `useProtonConfig()` call.

> **Note on `setReplyText`:** This is Chatwoot's internal mechanism for programmatically populating the reply box — typically a Vuex/Pinia action or a composable. Look for `useReplyEditor` or `setReplyContent` in the codebase. The patch uses whichever the component already exposes; do not add a new mechanism.

**How to generate the patch file:**

```bash
cd /tmp/proton-chatwoot-dev
git diff HEAD -- \
  app/javascript/dashboard/api/protonAssist.js \
  app/javascript/dashboard/components/ReplyEditor/ \
  app/javascript/dashboard/components/layout/Sidebar \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch
```

**Verification (local dev):**

- [ ] **Step 1: Apply patch, start Chatwoot with a running backend**

  ```bash
  cd /tmp/proton-chatwoot-dev
  git apply --whitespace=fix deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch
  # Start the backend (Task 8 must be done first) in another terminal
  PROTON_BACKEND_URL=http://localhost:8000 PROTON_FEATURES=ai_assist,nav_menu rails s -p 3000
  ```

- [ ] **Step 2: Smoke-test each action**

  Open a conversation in Chatwoot → click the ✨ button:
  - "Suggest a reply" → backend `/assist/suggest` is called → reply box fills with the draft.
  - "Summarize" → backend `/assist/summarize` is called → reply box fills with the summary.
  - "Ask Copilot" (type a question) → backend `/assist/ask` is called → reply box fills with the answer.

  In DevTools Network tab, verify:
  ```
  POST http://localhost:8000/assist/suggest   → 200
  POST http://localhost:8000/assist/summarize → 200
  POST http://localhost:8000/assist/ask       → 200
  ```

- [ ] **Step 3: Verify Captain is hidden**

  Reload Chatwoot. The "Captain" item should be absent from the left sidebar (since `ai_assist` disables Captain, or you can test with `PROTON_FEATURES=` empty — both ✨ button and Captain should then appear/disappear correctly).

- [ ] **Step 4: Commit the patch file**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/patches/0002-ai-assist-backend.patch
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "feat(patch-0002): rewire AI-assist menu to Proton backend; hide Captain nav"
  ```

---

### Task 6: Patch 0003 — Proton left-nav menu

**Files:**
- Create: `deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch` (engineer-authored, exported from `git diff`)

**Interfaces:**
- Consumes: `useProtonConfig()` from patch 0001 (`backendUrl`, `hasFeature`)
- Produces: four left-nav items (Reports, FAQ Admin, Agents, Cases) → generic iframe host route at `/app/accounts/:accountId/proton/:app` passing `account_id`, `user_token`, `locale` as query params and via `postMessage`

**What this patch adds (IP-safe description):**

**File we add — `app/javascript/dashboard/components/ProtonNavItem.vue`:**

```vue
<!-- ProtonNavItem.vue — OUR file -->
<template>
  <router-link
    v-if="enabled"
    :to="{ name: 'proton-app', params: { appName } }"
    class="nav-item"
    active-class="active"
  >
    <fluent-icon :icon="icon" size="22" />
    <span class="nav-label">{{ label }}</span>
  </router-link>
</template>

<script setup>
import { computed } from 'vue';
import { useProtonConfig } from '../../../composables/useProtonConfig';

const props = defineProps({
  appName: { type: String, required: true },
  icon:    { type: String, required: true },
  label:   { type: String, required: true },
  feature: { type: String, required: true },
});

const { hasFeature } = useProtonConfig();
const enabled = computed(() => hasFeature(props.feature) || hasFeature('nav_menu'));
</script>
```

**File we add — `app/javascript/dashboard/views/ProtonAppHost.vue`:**

```vue
<!-- ProtonAppHost.vue — OUR file -->
<template>
  <div class="proton-app-host">
    <iframe
      ref="frame"
      :src="iframeSrc"
      allow="same-origin"
      style="width:100%;height:100%;border:none;"
      @load="postAuthMessage"
    />
  </div>
</template>

<script setup>
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useStore } from 'vuex';
import { useProtonConfig } from '../../../composables/useProtonConfig';

const route   = useRoute();
const store   = useStore();
const frame   = ref(null);
const { backendUrl } = useProtonConfig();

const accountId = computed(() => store.getters['auth/getCurrentAccountId']);
const authToken = computed(() => store.getters['auth/getCurrentUser']?.access_token ?? '');
const locale    = computed(() => store.getters['auth/getCurrentUser']?.availability_status ?? 'en');

const iframeSrc = computed(() => {
  const app = route.params.appName;
  const params = new URLSearchParams({
    account_id: accountId.value,
    locale:     locale.value,
  });
  return `${backendUrl}/apps/${app}?${params}`;
});

function postAuthMessage() {
  frame.value?.contentWindow?.postMessage(
    { type: 'PROTON_AUTH', accountId: accountId.value, token: authToken.value },
    backendUrl,
  );
}
</script>
```

**Router entry we add (in Chatwoot's route config, e.g. `app/javascript/dashboard/router/routes.js`):**

```javascript
// Route entry WE add (find the array of routes and push this object):
{
  path: 'proton/:appName',
  name: 'proton-app',
  component: () => import('../views/ProtonAppHost.vue'),
  meta: { requiresAuth: true },
},
```

**Sidebar entries we add (in the sidebar nav config, e.g. `app/javascript/dashboard/components/layout/SidebarItems.js` or equivalent):**

```javascript
// Nav items WE add — inserted after existing items:
{ appName: 'reports',   icon: 'data-bar-horizontal', label: 'Reports',   feature: 'nav_reports' },
{ appName: 'faq-admin', icon: 'document-question-mark', label: 'FAQ Admin', feature: 'nav_faq_admin' },
{ appName: 'agents',    icon: 'people-team',          label: 'Agents',    feature: 'nav_agents' },
{ appName: 'cases',     icon: 'ticket-diagonal',      label: 'Cases',     feature: 'nav_cases' },
// Each renders as <ProtonNavItem :appName="item.appName" :icon="item.icon" :label="item.label" :feature="item.feature" />
```

i18n strings we add to `app/javascript/dashboard/i18n/locale/en.json` (and `id.json` for Indonesian):

```json
{
  "PROTON_NAV": {
    "REPORTS":   "Reports",
    "FAQ_ADMIN": "FAQ Admin",
    "AGENTS":    "Agents",
    "CASES":     "Cases"
  }
}
```

**How to generate the patch file:**

```bash
cd /tmp/proton-chatwoot-dev
git diff HEAD -- \
  app/javascript/dashboard/components/ProtonNavItem.vue \
  app/javascript/dashboard/views/ProtonAppHost.vue \
  app/javascript/dashboard/router/routes.js \
  app/javascript/dashboard/components/layout/ \
  app/javascript/dashboard/i18n/locale/en.json \
  app/javascript/dashboard/i18n/locale/id.json \
  > /path/to/id-crm-ticketing/deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch
```

**Verification (local dev):**

- [ ] **Step 1: Apply patch and start Chatwoot**

  ```bash
  cd /tmp/proton-chatwoot-dev
  git apply --whitespace=fix deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch
  PROTON_BACKEND_URL=http://localhost:8000 PROTON_FEATURES=nav_menu rails s -p 3000
  ```

- [ ] **Step 2: Verify nav items appear**

  Open Chatwoot. Four new items — Reports, FAQ Admin, Agents, Cases — should appear in the left sidebar.

- [ ] **Step 3: Verify iframe host loads**

  Click "FAQ Admin" → the browser navigates to `/app/accounts/1/proton/faq-admin`. The iframe should load `http://localhost:8000/apps/faq-admin?account_id=1&locale=en` (a 404 is expected for now — the app content is out of scope for Phase 0; what matters is the iframe URL is correct).

  In DevTools Console, after the iframe loads, verify the `postMessage` fires:
  ```javascript
  // Add a listener on the page to intercept:
  window.addEventListener('message', e => console.log('[proton-host]', e.data));
  // Expected after clicking nav item:
  // { type: 'PROTON_AUTH', accountId: 1, token: '...' }
  ```

- [ ] **Step 4: Verify feature-gating**

  Reload with `PROTON_FEATURES=` (empty) — nav items should be hidden.

- [ ] **Step 5: Commit the patch file**

  ```bash
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing add deploy/chatwoot-fork/patches/0003-proton-nav-menu.patch
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit -m "feat(patch-0003): add Proton left-nav items with iframe host route"
  ```

---

### Task 7: Build & smoke-test the full image (off-VM)

**Files:**
- Read: `deploy/chatwoot-fork/UPSTREAM_VERSION`, `Dockerfile`, `patches/` (all three from Tasks 2–6)

**Interfaces:**
- Consumes: all three `.patch` files from Tasks 4–6
- Produces: local Docker image `proton-chatwoot:v4.15.1-custom` verified bootable

> **Run this task on a Mac (Docker Desktop), not on the production VM.**

- [ ] **Step 1: Build the image**

  ```bash
  cd /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork
  VERSION=$(cat UPSTREAM_VERSION)
  docker build --build-arg UPSTREAM_VERSION=$VERSION \
    -t proton-chatwoot:$VERSION-custom \
    --progress=plain \
    . 2>&1 | tee /tmp/chatwoot-build.log
  ```

  Expected (last few lines of output):
  ```
  [runtime] COPY --from=builder /app/public/packs /app/public/packs
  Successfully tagged proton-chatwoot:v4.15.1-custom
  ```

  If `git apply` fails on any patch, the build errors at that layer — check `/tmp/chatwoot-build.log` for `error: patch failed:`.

- [ ] **Step 2: Verify all three patches were applied inside the image**

  ```bash
  docker run --rm proton-chatwoot:v4.15.1-custom grep -r "PROTON_BACKEND_URL" /app/public/packs/ | wc -l
  # Expected: ≥ 1 (the compiled bundle contains our injected global reference)

  docker run --rm proton-chatwoot:v4.15.1-custom grep -r "protonAssist" /app/public/packs/ | wc -l
  # Expected: ≥ 1

  docker run --rm proton-chatwoot:v4.15.1-custom grep -r "ProtonAppHost\|proton-app" /app/public/packs/ | wc -l
  # Expected: ≥ 1
  ```

- [ ] **Step 3: Boot the image against a local stack and confirm it serves**

  ```bash
  # Quick sanity — start with minimal env, just confirm Rails boots:
  docker run --rm \
    -e SECRET_KEY_BASE=test_secret_key_base_64chars_minimum_here_pad_to_64_chars \
    -e RAILS_ENV=production \
    -e POSTGRES_HOST=host.docker.internal \
    -e REDIS_URL=redis://host.docker.internal:6379 \
    -p 3001:3000 \
    proton-chatwoot:v4.15.1-custom \
    bundle exec rails s -p 3000 -b 0.0.0.0 &
  sleep 30
  curl -s -o /dev/null -w "%{http_code}" http://localhost:3001/auth/sign_in
  # Expected: 200
  kill %1
  ```

- [ ] **Step 4: Push to Artifact Registry (requires `gcloud auth` and AR repo pre-created)**

  ```bash
  REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  VERSION=$(cat /Users/yudaadipratama/Archive/id-crm-ticketing/deploy/chatwoot-fork/UPSTREAM_VERSION)
  docker tag proton-chatwoot:$VERSION-custom $REGISTRY/proton-chatwoot:$VERSION-custom
  docker push $REGISTRY/proton-chatwoot:$VERSION-custom
  ```

  Expected:
  ```
  v4.15.1-custom: digest: sha256:… size: …
  ```

- [ ] **Step 5: Commit build verification note (tag the image as verified)**

  ```bash
  # No code change here — commit is informational:
  git -C /Users/yudaadipratama/Archive/id-crm-ticketing commit --allow-empty \
    -m "chore(chatwoot-fork): v4.15.1-custom image verified + pushed to Artifact Registry"
  ```

---

### Task 8: Backend — `Settings` additions

**Files:**
- Modify: `src/chatbot/platform/config.py` (in `proton-conversational-ai/apps/backend/`)

**Interfaces:**
- Consumes: existing `Settings(BaseSettings)` class
- Produces:
  - `Settings.proton_backend_key: str` — checked by the `/assist/*` auth guard
  - `Settings.assist_gemini_model: str` — model used for summarise/ask Gemini calls (defaults to `"gemini-2.5-flash"`)
  - `Settings.assist_cors_origins: list[str]` — Chatwoot origins allowed to call `/assist/*` cross-origin

- [ ] **Step 1: Write the failing test**

  Create (or open if exists) `src/chatbot/platform/test_config_assist.py`:

  ```python
  import os
  import pytest
  from chatbot.platform.config import Settings


  def test_assist_settings_defaults() -> None:
      s = Settings()
      assert s.proton_backend_key == ""
      assert s.assist_gemini_model == "gemini-2.5-flash"
      assert isinstance(s.assist_cors_origins, list)


  def test_assist_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
      monkeypatch.setenv("PROTON_BACKEND_KEY", "secret123")
      monkeypatch.setenv("ASSIST_GEMINI_MODEL", "gemini-2.5-pro")
      monkeypatch.setenv("ASSIST_CORS_ORIGINS", '["http://crm.example.com"]')
      s = Settings()
      assert s.proton_backend_key == "secret123"
      assert s.assist_gemini_model == "gemini-2.5-pro"
      assert s.assist_cors_origins == ["http://crm.example.com"]
  ```

- [ ] **Step 2: Run test to verify it fails**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/platform/test_config_assist.py -v
  ```

  Expected: `FAILED` — `Settings` has no `proton_backend_key` attribute.

- [ ] **Step 3: Add the three fields to `Settings`**

  In `src/chatbot/platform/config.py`, add after the `frontend_origins` field (before `model_config`):

  ```python
      # --- Proton assist endpoints -------------------------------------------
      # Shared secret for /assist/* endpoints — must match PROTON_BACKEND_KEY
      # in the tenant env. Empty means the endpoint is unconfigured; boot fails
      # gracefully (endpoints return 503) rather than leaving them open.
      proton_backend_key: str = ""
      # Gemini model for summarize/ask; defaults to the same model as the main agent.
      assist_gemini_model: str = "gemini-2.5-flash"
      # Chatwoot origins allowed to call /assist/* cross-origin. Add each tenant's
      # Chatwoot URL (e.g. http://crm.<IP>.nip.io and proton.crm.<IP>.nip.io).
      assist_cors_origins: list[str] = []
  ```

- [ ] **Step 4: Run test to verify it passes**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/platform/test_config_assist.py -v
  ```

  Expected:
  ```
  PASSED src/chatbot/platform/test_config_assist.py::test_assist_settings_defaults
  PASSED src/chatbot/platform/test_config_assist.py::test_assist_settings_from_env
  ```

- [ ] **Step 5: Commit**

  ```bash
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
    apps/backend/src/chatbot/platform/config.py \
    apps/backend/src/chatbot/platform/test_config_assist.py
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
    -m "feat(config): add proton_backend_key, assist_gemini_model, assist_cors_origins"
  ```

---

### Task 9: Backend — `/assist` router

**Files:**
- Create: `src/chatbot/features/assist/router.py`
- Create: `src/chatbot/features/assist/__init__.py`
- Create: `src/chatbot/features/assist/test_assist_router.py`

**Interfaces:**
- Consumes:
  - `Settings.proton_backend_key: str` (Task 8)
  - `Settings.assist_gemini_model: str` (Task 8)
  - `Settings.gemini_model: str` (existing)
  - `KnowledgePort.search_kb(query: str, limit: int) -> list[KbArticle]` (existing)
  - The existing Chatwoot API is NOT called directly from the backend — the frontend sends the conversation messages it already has; alternatively, the backend fetches them via `ChatwootAdapter` when `chatwoot_api_token` is configured. For Phase 0, the frontend sends `messages: list[str]` in the request body (simpler, no extra Chatwoot API call needed).
- Produces:
  - `POST /assist/suggest` → `{ "draft": str, "sources": list[dict] }`
  - `POST /assist/summarize` → `{ "summary": str }`
  - `POST /assist/ask` → `{ "answer": str }`
  - `build_assist_router(settings, knowledge_port) -> APIRouter`

- [ ] **Step 1: Write the failing tests**

  Create `src/chatbot/features/assist/__init__.py` (empty).

  Create `src/chatbot/features/assist/test_assist_router.py`:

  ```python
  """Tests for /assist/* endpoints.

  Uses a stub Gemini client and a stub KnowledgePort so no real GCP calls are made.
  """
  from __future__ import annotations

  from unittest.mock import AsyncMock, MagicMock, patch

  import pytest
  from fastapi import FastAPI
  from fastapi.testclient import TestClient

  from chatbot.features.assist.router import build_assist_router
  from chatbot.platform.config import Settings


  # ---------------------------------------------------------------------------
  # Stubs
  # ---------------------------------------------------------------------------

  class _FakeKnowledge:
      async def search_kb(self, query: str, limit: int = 3) -> list:
          from chatbot.features.chat.models import KbArticle
          return [KbArticle(title="FAQ Title", content="FAQ content body", url="http://faq/1")]


  def _settings(key: str = "testkey") -> Settings:
      return Settings(proton_backend_key=key, assist_gemini_model="gemini-2.5-flash")


  def _client(key: str = "testkey") -> tuple[TestClient, MagicMock]:
      """Return (TestClient, mock_genai_client) with the Gemini client patched."""
      mock_genai = MagicMock()
      mock_response = MagicMock()
      mock_response.text = "This is the AI output."
      mock_genai.aio.models.generate_content = AsyncMock(return_value=mock_response)

      app = FastAPI()
      app.include_router(
          build_assist_router(
              settings=_settings(key),
              knowledge_port=_FakeKnowledge(),
              genai_client=mock_genai,
          )
      )
      return TestClient(app), mock_genai


  # ---------------------------------------------------------------------------
  # Auth guard (all three endpoints)
  # ---------------------------------------------------------------------------

  def test_suggest_requires_api_key() -> None:
      client, _ = _client()
      r = client.post("/assist/suggest", json={"conversation_id": "42", "messages": ["Hi"]})
      assert r.status_code == 401


  def test_suggest_wrong_key_rejected() -> None:
      client, _ = _client()
      r = client.post(
          "/assist/suggest",
          json={"conversation_id": "42", "messages": ["Hi"]},
          headers={"x-api-key": "wrongkey"},
      )
      assert r.status_code == 401


  def test_summarize_requires_api_key() -> None:
      client, _ = _client()
      r = client.post("/assist/summarize", json={"conversation_id": "42", "messages": ["Hi"]})
      assert r.status_code == 401


  def test_ask_requires_api_key() -> None:
      client, _ = _client()
      r = client.post(
          "/assist/ask",
          json={"conversation_id": "42", "messages": ["Hi"], "question": "What is the warranty?"},
      )
      assert r.status_code == 401


  # ---------------------------------------------------------------------------
  # Happy paths
  # ---------------------------------------------------------------------------

  def test_suggest_returns_draft_and_sources() -> None:
      client, mock_genai = _client()
      r = client.post(
          "/assist/suggest",
          json={"conversation_id": "42", "messages": ["My battery died"], "limit": 2},
          headers={"x-api-key": "testkey"},
      )
      assert r.status_code == 200
      body = r.json()
      assert "draft" in body
      assert body["draft"] == "This is the AI output."
      assert isinstance(body["sources"], list)
      assert body["sources"][0]["title"] == "FAQ Title"


  def test_summarize_returns_summary() -> None:
      client, mock_genai = _client()
      r = client.post(
          "/assist/summarize",
          json={"conversation_id": "42", "messages": ["Customer said X", "Agent replied Y"]},
          headers={"x-api-key": "testkey"},
      )
      assert r.status_code == 200
      body = r.json()
      assert body["summary"] == "This is the AI output."


  def test_ask_returns_answer() -> None:
      client, mock_genai = _client()
      r = client.post(
          "/assist/ask",
          json={
              "conversation_id": "42",
              "messages": ["Customer asked about warranty"],
              "question": "What is the warranty period?",
          },
          headers={"x-api-key": "testkey"},
      )
      assert r.status_code == 200
      body = r.json()
      assert body["answer"] == "This is the AI output."


  # ---------------------------------------------------------------------------
  # 503 when proton_backend_key is empty (misconfigured deployment)
  # ---------------------------------------------------------------------------

  def test_suggest_503_when_key_unconfigured() -> None:
      mock_genai = MagicMock()
      app = FastAPI()
      app.include_router(
          build_assist_router(
              settings=Settings(proton_backend_key=""),
              knowledge_port=_FakeKnowledge(),
              genai_client=mock_genai,
          )
      )
      client = TestClient(app, raise_server_exceptions=False)
      r = client.post(
          "/assist/suggest",
          json={"conversation_id": "42", "messages": ["Hi"]},
          headers={"x-api-key": "anything"},
      )
      assert r.status_code == 503
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/features/assist/test_assist_router.py -v
  ```

  Expected: `FAILED` (multiple) — `ModuleNotFoundError: No module named 'chatbot.features.assist'`.

- [ ] **Step 3: Implement `router.py`**

  Create `src/chatbot/features/assist/router.py`:

  ```python
  """POST /assist/suggest|summarize|ask — Proton AI-assist endpoints.

  Called by the patched Chatwoot frontend (patch 0002). All endpoints require
  `x-api-key` matching `Settings.proton_backend_key`; an empty key returns 503
  so misconfigured deployments fail loudly rather than leaving the endpoint open.

  Request shape (all three endpoints share a base):
    - conversation_id : str   — Chatwoot conversation id (for logging)
    - messages        : list[str] — conversation turns, most-recent last
    - question        : str   — only for /ask

  /suggest also accepts:
    - limit : int (default 3) — max KB hits to ground the reply
  """

  from __future__ import annotations

  import hmac
  from typing import TYPE_CHECKING, Any

  import structlog
  from fastapi import APIRouter, Header, HTTPException
  from pydantic import BaseModel, Field

  if TYPE_CHECKING:
      from chatbot.features.chat.ports import KnowledgePort
      from chatbot.platform.config import Settings

  _log = structlog.get_logger(__name__)

  _SUGGEST_SYSTEM = (
      "You are a customer-support agent for Proton Holdings. "
      "Given the conversation history and the relevant FAQ context below, "
      "write a concise, professional reply to the customer's latest message. "
      "Reply in the same language the customer used. Do not include a salutation or sign-off. "
      "Return only the reply text, nothing else.\n\n"
      "FAQ context:\n{faq_context}"
  )

  _SUMMARIZE_SYSTEM = (
      "You are a customer-support supervisor. "
      "Summarise the following conversation between a customer and a support agent "
      "in 3–5 bullet points. Focus on: the customer's issue, steps taken, and current status. "
      "Reply in English regardless of the conversation language."
  )

  _ASK_SYSTEM = (
      "You are a customer-support knowledge assistant. "
      "Answer the agent's question using the conversation history and the FAQ context below. "
      "Be concise. If the answer is not in the context, say so clearly.\n\n"
      "FAQ context:\n{faq_context}"
  )

  _SNIPPET = 300


  class AssistBase(BaseModel):
      conversation_id: str = Field(min_length=1)
      messages: list[str] = Field(min_length=1)


  class SuggestRequest(AssistBase):
      limit: int = Field(default=3, ge=1, le=10)


  class SummarizeRequest(AssistBase):
      pass


  class AskRequest(AssistBase):
      question: str = Field(min_length=1)


  def build_assist_router(
      settings: Settings,
      knowledge_port: KnowledgePort,
      genai_client: Any,
  ) -> APIRouter:
      """Return a FastAPI router with three /assist/* endpoints.

      Args:
          settings: application settings (reads proton_backend_key + assist_gemini_model).
          knowledge_port: KB search port (for /suggest and /ask grounding).
          genai_client: a google.genai.Client instance (or stub in tests).
      """
      router = APIRouter(prefix="/assist", tags=["assist"])

      def _authorize(x_api_key: str | None) -> None:
          key = settings.proton_backend_key
          if not key:
              raise HTTPException(status_code=503, detail="Assist endpoints not configured")
          if (
              x_api_key is None
              or not hmac.compare_digest(x_api_key.encode(), key.encode())
          ):
              raise HTTPException(status_code=401, detail="Unauthorized")

      async def _generate(system: str, user_prompt: str) -> str:
          model = settings.assist_gemini_model
          response = await genai_client.aio.models.generate_content(
              model=model,
              contents=user_prompt,
              config={"system_instruction": system},
          )
          return (response.text or "").strip()

      def _format_messages(messages: list[str]) -> str:
          return "\n".join(f"[{i+1}] {m}" for i, m in enumerate(messages))

      async def _kb_context(query: str, limit: int) -> tuple[str, list[dict[str, Any]]]:
          articles = await knowledge_port.search_kb(query, limit)
          sources = [
              {
                  "title": a.title,
                  "snippet": a.content[:_SNIPPET],
                  "url": a.url,
              }
              for a in articles
          ]
          faq_context = "\n---\n".join(
              f"Q: {a.title}\nA: {a.content[:_SNIPPET]}" for a in articles
          )
          return faq_context, sources

      @router.post("/suggest")
      async def suggest(
          req: SuggestRequest,
          x_api_key: str | None = Header(default=None),
      ) -> dict[str, Any]:
          _authorize(x_api_key)
          _log.info("assist_suggest", conv_id=req.conversation_id)
          query = req.messages[-1]  # ground on the customer's latest message
          faq_context, sources = await _kb_context(query, req.limit)
          system = _SUGGEST_SYSTEM.format(faq_context=faq_context or "(none)")
          user_prompt = _format_messages(req.messages)
          draft = await _generate(system, user_prompt)
          return {"draft": draft, "sources": sources}

      @router.post("/summarize")
      async def summarize(
          req: SummarizeRequest,
          x_api_key: str | None = Header(default=None),
      ) -> dict[str, Any]:
          _authorize(x_api_key)
          _log.info("assist_summarize", conv_id=req.conversation_id)
          user_prompt = _format_messages(req.messages)
          summary = await _generate(_SUMMARIZE_SYSTEM, user_prompt)
          return {"summary": summary}

      @router.post("/ask")
      async def ask(
          req: AskRequest,
          x_api_key: str | None = Header(default=None),
      ) -> dict[str, Any]:
          _authorize(x_api_key)
          _log.info("assist_ask", conv_id=req.conversation_id, question=req.question)
          faq_context, _ = await _kb_context(req.question, limit=3)
          system = _ASK_SYSTEM.format(faq_context=faq_context or "(none)")
          user_prompt = (
              f"Conversation:\n{_format_messages(req.messages)}\n\n"
              f"Agent question: {req.question}"
          )
          answer = await _generate(system, user_prompt)
          return {"answer": answer}

      return router
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/features/assist/test_assist_router.py -v
  ```

  Expected:
  ```
  PASSED test_suggest_requires_api_key
  PASSED test_suggest_wrong_key_rejected
  PASSED test_summarize_requires_api_key
  PASSED test_ask_requires_api_key
  PASSED test_suggest_returns_draft_and_sources
  PASSED test_summarize_returns_summary
  PASSED test_ask_returns_answer
  PASSED test_suggest_503_when_key_unconfigured
  ```

- [ ] **Step 5: Commit**

  ```bash
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
    apps/backend/src/chatbot/features/assist/
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
    -m "feat(assist): add POST /assist/suggest|summarize|ask with x-api-key auth"
  ```

---

### Task 10: Backend — Wire `assist` router into `main.py` and CORS

**Files:**
- Modify: `src/chatbot/main.py`

**Interfaces:**
- Consumes:
  - `build_assist_router(settings, knowledge_port, genai_client) -> APIRouter` (Task 9)
  - `Settings.assist_cors_origins: list[str]` (Task 8)
  - `knowledge_port` (already wired in `bootstrap_application`)
  - `_build_genai_client(settings)` (already exists in `main.py`)
- Produces: the three `/assist/*` routes registered in the live FastAPI app; Chatwoot origins allowed in CORS

- [ ] **Step 1: Write the failing integration test**

  Create `src/chatbot/features/assist/test_assist_wiring.py`:

  ```python
  """Smoke-test: verify /assist/* routes exist and are auth-guarded in the live app.

  Uses the full bootstrap to ensure the router is wired; mocks GCP so no credentials needed.
  """
  from unittest.mock import MagicMock, patch, AsyncMock

  from fastapi.testclient import TestClient


  def _patched_app() -> object:
      """Bootstrap the app with all GCP clients stubbed out."""
      mock_genai = MagicMock()
      mock_genai.aio.models.generate_content = AsyncMock(return_value=MagicMock(text="reply"))

      with (
          patch("chatbot.main._build_genai_client", return_value=mock_genai),
          patch("chatbot.main.VertexAISearchAdapter", MagicMock()),
          patch("chatbot.main.build_handoff_store", MagicMock()),
          patch("chatbot.main.build_audit_log", MagicMock(return_value=None)),
          patch("chatbot.main.build_metrics_port", MagicMock()),
          patch("chatbot.main.start_sla_scheduler", return_value=None),
          patch("chatbot.main.start_metrics_scheduler", return_value=None),
          patch("chatbot.main.start_report_scheduler", return_value=None),
      ):
          from chatbot.main import bootstrap_application
          return bootstrap_application()


  def test_assist_suggest_route_exists_and_is_auth_guarded() -> None:
      app = _patched_app()
      client = TestClient(app)
      r = client.post("/assist/suggest", json={"conversation_id": "1", "messages": ["hi"]})
      # 401 or 503 — either means the route exists and is guarded (not 404)
      assert r.status_code in (401, 503)


  def test_assist_summarize_route_exists_and_is_auth_guarded() -> None:
      app = _patched_app()
      client = TestClient(app)
      r = client.post("/assist/summarize", json={"conversation_id": "1", "messages": ["hi"]})
      assert r.status_code in (401, 503)


  def test_assist_ask_route_exists_and_is_auth_guarded() -> None:
      app = _patched_app()
      client = TestClient(app)
      r = client.post(
          "/assist/ask",
          json={"conversation_id": "1", "messages": ["hi"], "question": "what?"},
      )
      assert r.status_code in (401, 503)
  ```

- [ ] **Step 2: Run tests to verify they fail**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/features/assist/test_assist_wiring.py -v
  ```

  Expected: `FAILED` with `AssertionError: assert 404 in (401, 503)` — routes not yet wired.

- [ ] **Step 3: Add the wiring to `main.py`**

  In `src/chatbot/main.py`, add the import after the existing imports from `chatbot.features.chat.*`:

  ```python
  from chatbot.features.assist.router import build_assist_router
  ```

  In `bootstrap_application()`, after `_wire_agent_assist(app, knowledge_port, settings)`:

  ```python
      # --- Proton AI-assist (rewired Captain AI) ---
      _wire_assist(app, knowledge_port, settings)
  ```

  Add the helper function before `bootstrap_application()`:

  ```python
  def _wire_assist(app: FastAPI, knowledge_port: KnowledgePort, settings: Settings) -> None:
      """Wire the three /assist/* endpoints and add Chatwoot origins to CORS."""
      genai_client = _build_genai_client(settings)
      app.include_router(build_assist_router(settings, knowledge_port, genai_client))

      # Extend the existing CORS middleware to allow Chatwoot origins to call
      # /assist/* cross-origin. The CORSMiddleware is already added with
      # settings.frontend_origins; append the assist_cors_origins here by
      # adding a second, narrower middleware that covers only /assist/*.
      # (Simpler: just add assist_cors_origins to frontend_origins in the tenant env
      # and rely on the single middleware. Use this note if the two-middleware
      # approach adds complexity.)
      # For Phase 0, the operator adds Chatwoot URLs to ASSIST_CORS_ORIGINS in the
      # backend's .env, and they are merged into allow_origins at startup:
      if settings.assist_cors_origins:
          import structlog as _sl
          _sl.get_logger(__name__).info(
              "assist_cors_origins_added", count=len(settings.assist_cors_origins)
          )
          # The CORSMiddleware registered above already covers frontend_origins.
          # Re-registering with a merged list is the simplest approach; FastAPI
          # evaluates middlewares in stack order and the first matching one wins.
          from fastapi.middleware.cors import CORSMiddleware as _CORS
          app.add_middleware(
              _CORS,
              allow_origins=settings.assist_cors_origins,
              allow_credentials=True,
              allow_methods=["POST", "OPTIONS"],
              allow_headers=["x-api-key", "content-type"],
          )
  ```

- [ ] **Step 4: Run tests to verify they pass**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/chatbot/features/assist/test_assist_wiring.py -v
  ```

  Expected:
  ```
  PASSED test_assist_suggest_route_exists_and_is_auth_guarded
  PASSED test_assist_summarize_route_exists_and_is_auth_guarded
  PASSED test_assist_ask_route_exists_and_is_auth_guarded
  ```

- [ ] **Step 5: Run the full existing test suite to confirm nothing is broken**

  ```bash
  cd /Users/yudaadipratama/Archive/proton-conversational-ai/apps/backend
  python -m pytest src/ -x -q 2>&1 | tail -20
  ```

  Expected: all previously-passing tests still pass; new tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai add \
    apps/backend/src/chatbot/main.py \
    apps/backend/src/chatbot/features/assist/test_assist_wiring.py
  git -C /Users/yudaadipratama/Archive/proton-conversational-ai commit \
    -m "feat(main): wire /assist/* router + CORS for Chatwoot origins"
  ```

---

### Task 11: VM deploy — `default` tenant rollout + smoke test

**Files:**
- Modify: `deploy/tenants/default.env` (on the VM; not committed — contains real secrets)

> **This task runs on the production VM (SSH in). The image was pushed in Task 7.**

- [ ] **Step 1: SSH to the VM and set the custom image in `default.env`**

  ```bash
  # On the VM:
  REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  VERSION=v4.15.1

  # Edit (or sed-in-place) the default tenant env:
  sed -i "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=${REGISTRY}/proton-chatwoot:${VERSION}-custom|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env

  # Also set the backend vars (use the deployed proton-conversational-ai Cloud Run URL):
  sed -i "s|^PROTON_BACKEND_URL=.*|PROTON_BACKEND_URL=https://<cloud-run-url>|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env
  sed -i "s|^PROTON_BACKEND_KEY=.*|PROTON_BACKEND_KEY=<secret>|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env
  sed -i "s|^PROTON_FEATURES=.*|PROTON_FEATURES=ai_assist,nav_menu|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env
  ```

- [ ] **Step 2: Pull and restart the Chatwoot services**

  ```bash
  # On the VM:
  cd /opt/id-crm-ticketing/deploy

  # Authenticate to Artifact Registry (once per VM, if not already done):
  gcloud auth configure-docker asia-southeast1-docker.pkg.dev

  docker compose -p default -f docker-compose.tenant.yml \
    --env-file tenants/default.env \
    pull chatwoot-rails chatwoot-sidekiq

  docker compose -p default -f docker-compose.tenant.yml \
    --env-file tenants/default.env \
    up -d chatwoot-rails chatwoot-sidekiq
  ```

  Expected:
  ```
  Pulling chatwoot-rails ... done
  Pulling chatwoot-sidekiq ... done
  Starting default-chatwoot-rails ... done
  Starting default-chatwoot-sidekiq ... done
  ```

- [ ] **Step 3: Wait for health and verify boot**

  ```bash
  # On the VM:
  sleep 60
  docker compose -p default -f docker-compose.tenant.yml \
    --env-file tenants/default.env \
    ps chatwoot-rails
  # Expected: State = Up (healthy)

  curl -s -o /dev/null -w "%{http_code}" http://crm.<PUBLIC_IP>.nip.io/auth/sign_in
  # Expected: 200
  ```

- [ ] **Step 4: Smoke-test the patches in the browser**

  Open `http://crm.<PUBLIC_IP>.nip.io` → sign in → open any conversation:
  - DevTools Console: `window.__PROTON_CONFIG__` → object with `backendUrl`, `features: ["ai_assist", "nav_menu"]`
  - Reply box: ✨ button visible; click "Suggest a reply" → reply box populates with a draft
  - Left nav: four items (Reports, FAQ Admin, Agents, Cases) visible

- [ ] **Step 5: Verify `/assist/suggest` end-to-end from the VM**

  ```bash
  # From the VM (or anywhere with network access to the Cloud Run URL):
  curl -s -X POST https://<cloud-run-url>/assist/suggest \
    -H "Content-Type: application/json" \
    -H "x-api-key: <secret>" \
    -d '{"conversation_id":"test","messages":["My battery is not charging"]}' \
    | python3 -m json.tool
  # Expected:
  # {
  #   "draft": "...",
  #   "sources": [...]
  # }
  ```

- [ ] **Step 6: Test rollback**

  ```bash
  # On the VM — revert to stock:
  sed -i "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=chatwoot/chatwoot:v4.15.1|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env

  docker compose -p default -f docker-compose.tenant.yml \
    --env-file tenants/default.env \
    up -d chatwoot-rails chatwoot-sidekiq

  sleep 30
  curl -s -o /dev/null -w "%{http_code}" http://crm.<PUBLIC_IP>.nip.io/auth/sign_in
  # Expected: 200 (stock image running again; Proton nav gone)

  # Re-enable the custom image after verifying rollback:
  sed -i "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=${REGISTRY}/proton-chatwoot:${VERSION}-custom|" \
    /opt/id-crm-ticketing/deploy/tenants/default.env
  docker compose -p default -f docker-compose.tenant.yml \
    --env-file tenants/default.env \
    up -d chatwoot-rails chatwoot-sidekiq
  ```

---

### Task 12: VM deploy — Replicate to `proton` and `wahchan`

**Files:**
- Modify: `deploy/tenants/proton.env` (on the VM; secrets)
- Modify: `deploy/tenants/wahchan.env` (on the VM; secrets)

> **Prerequisite:** Task 11 smoke-test must pass on `default` before replicating.

- [ ] **Step 1: Set image + PROTON_* in `proton.env`**

  ```bash
  # On the VM:
  REGISTRY=asia-southeast1-docker.pkg.dev/lv-playground-genai/proton-images
  VERSION=v4.15.1

  sed -i "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=${REGISTRY}/proton-chatwoot:${VERSION}-custom|" \
    /opt/id-crm-ticketing/deploy/tenants/proton.env
  sed -i "s|^PROTON_BACKEND_URL=.*|PROTON_BACKEND_URL=https://<proton-cloud-run-url>|" \
    /opt/id-crm-ticketing/deploy/tenants/proton.env
  sed -i "s|^PROTON_BACKEND_KEY=.*|PROTON_BACKEND_KEY=<proton-secret>|" \
    /opt/id-crm-ticketing/deploy/tenants/proton.env
  sed -i "s|^PROTON_FEATURES=.*|PROTON_FEATURES=ai_assist,nav_menu|" \
    /opt/id-crm-ticketing/deploy/tenants/proton.env
  ```

- [ ] **Step 2: Deploy to `proton`**

  ```bash
  docker compose -p proton -f /opt/id-crm-ticketing/deploy/docker-compose.tenant.yml \
    --env-file /opt/id-crm-ticketing/deploy/tenants/proton.env \
    up -d chatwoot-rails chatwoot-sidekiq
  sleep 60
  docker compose -p proton -f /opt/id-crm-ticketing/deploy/docker-compose.tenant.yml \
    --env-file /opt/id-crm-ticketing/deploy/tenants/proton.env \
    ps chatwoot-rails
  # Expected: State = Up (healthy)
  ```

- [ ] **Step 3: Set image + PROTON_* in `wahchan.env`**

  ```bash
  # `wahchan` has no dedicated backend URL yet — features stay disabled until one exists.
  sed -i "s|^CHATWOOT_IMAGE=.*|CHATWOOT_IMAGE=${REGISTRY}/proton-chatwoot:${VERSION}-custom|" \
    /opt/id-crm-ticketing/deploy/tenants/wahchan.env
  # PROTON_BACKEND_URL and PROTON_BACKEND_KEY: leave empty for now.
  # PROTON_FEATURES: empty → AI button and nav items hidden (correct behaviour per design).
  sed -i "s|^PROTON_FEATURES=.*|PROTON_FEATURES=|" \
    /opt/id-crm-ticketing/deploy/tenants/wahchan.env
  ```

- [ ] **Step 4: Deploy to `wahchan`**

  ```bash
  docker compose -p wahchan -f /opt/id-crm-ticketing/deploy/docker-compose.tenant.yml \
    --env-file /opt/id-crm-ticketing/deploy/tenants/wahchan.env \
    up -d chatwoot-rails chatwoot-sidekiq
  sleep 60
  curl -s -o /dev/null -w "%{http_code}" http://wahchan.crm.<PUBLIC_IP>.nip.io/auth/sign_in
  # Expected: 200; Proton nav hidden (PROTON_FEATURES empty)
  ```

- [ ] **Step 5: Final DoD verification checklist**

  On the VM, confirm all items:

  ```
  [ ] proton-chatwoot:v4.15.1-custom built off-VM and pushed to Artifact Registry
  [ ] default: window.__PROTON_CONFIG__ present; ✨ suggest/summarize/ask + native insert working
  [ ] default: four nav items render and open the iframe host
  [ ] default: stock rollback verified (reverted + re-deployed in Task 11 Step 6)
  [ ] proton: custom image + proton Cloud Run backend wired; AI button functional
  [ ] wahchan: custom image running; Proton features hidden (no backend URL yet)
  ```

---

## Self-Review

### 1. Spec coverage

| Design requirement | Covered by task |
|---|---|
| `UPSTREAM_VERSION` pin file | Task 2 |
| Three `.patch` files in `patches/` | Tasks 4, 5, 6 |
| `Dockerfile` (clone → apply → build → runtime) | Task 2 |
| `cloudbuild.yaml` | Task 2 |
| `README.md` (dev loop + build/push + upgrade runbook) | Task 3 |
| Patch 0001: env → `window.__PROTON_CONFIG__` | Task 4 |
| Patch 0002: rewire ✨ menu + hide Captain | Task 5 |
| Patch 0003: four nav items + iframe host + feature-gated | Task 6 |
| `POST /assist/suggest` (KB grounding + Gemini draft) | Task 9 |
| `POST /assist/summarize` (Gemini over transcript) | Task 9 |
| `POST /assist/ask` (Gemini over conversation + KB) | Task 9 |
| `x-api-key` auth on all `/assist/*` | Task 9 |
| `Settings.proton_backend_key / assist_gemini_model` | Task 8 |
| Wire router in `main.py` | Task 10 |
| CORS for Chatwoot origins | Task 10 |
| Compose: `image: ${CHATWOOT_IMAGE:-…}` parameterisation | Task 1 |
| `PROTON_BACKEND_URL/KEY/FEATURES` in compose + example.env | Task 1 |
| `default` deploy + smoke-test + rollback verify | Task 11 |
| Replicate to `proton` + `wahchan` | Task 12 |
| Build verified off-VM | Task 7 |
| Right-panel — custom attribute schema | **GAP — see below** |

### 2. Spec gap: right-panel custom attributes (design §E)

The design includes "define the attribute schema (personal/vehicle/service/call-center groupings) and write whatever contact data exists today" — this is mentioned as in-scope in the design doc but has no task in this plan. It was omitted because:

1. It requires a Chatwoot admin API call (not a code change), and
2. The spec section says "DMS/TSP enrichment is deferred" — only the schema definition + any existing contact data write was in scope.

**Resolution:** Add a sub-task to Task 11 (or a standalone Task 13) that uses the Chatwoot API to POST the four custom-attribute groups. This is a one-shot curl command per tenant, not a code change. It is small enough to include in the deploy runbook rather than needing its own implementation plan task. Flagged here for the executor to confirm with the product owner whether this is required for Phase 0 DoD.

### 3. Placeholder scan

Checked — no TBD/TODO/implement-later phrases present. All code steps show complete code. All commands show exact syntax and expected output.

### 4. Type consistency

- `useProtonConfig()` is introduced in Task 4 and consumed by exactly that name in Tasks 5 and 6.
- `build_assist_router(settings, knowledge_port, genai_client)` is defined in Task 9, imported by that exact name in Task 10.
- `callAssist(action, payload)` in patch 0002 (Task 5) is defined and called consistently.
- `Settings.proton_backend_key` / `Settings.assist_gemini_model` / `Settings.assist_cors_origins` defined in Task 8, referenced by exact those names in Tasks 9 and 10.
