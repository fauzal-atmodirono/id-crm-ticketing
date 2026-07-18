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
