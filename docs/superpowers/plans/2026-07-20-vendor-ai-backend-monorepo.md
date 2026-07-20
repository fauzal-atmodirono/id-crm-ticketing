# Vendor the AI backend into a single-repo CRM — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor the standalone `proton-conversational-ai` backend into this repo and wire it into the per-tenant deploy, so provisioning a tenant builds and runs the AI backend in-stack — one repo, HTTP boundary preserved.

**Architecture:** `git subtree add` the whole proton repo under `backend/` (history preserved). Add a `backend` service to `docker-compose.tenant.yml` that builds `backend/apps/backend/`. Point `PROTON_BACKEND_URL` at the in-stack service and give the `agent` service that env too. Fold the backend's env vars into the tenant env template. No code welding — `agent/` ↔ backend stays HTTP + fail-open.

**Tech Stack:** git subtree, Docker Compose, FastAPI (`uv`, Python 3.12), the existing `agent/` FastAPI service.

**Spec:** `docs/superpowers/specs/2026-07-20-vendor-ai-backend-monorepo-design.md`

## Global Constraints

- **Boundary stays HTTP over `PROTON_BACKEND_URL`** — no shared DB, no shared process, no cross-imports between `agent/` and `backend/`. Co-located, not merged.
- **Fail-open preserved:** backend down → `agent` falls back to global settings (unchanged `agent/app/clients/proton.py`).
- **No behavioral change** to any phase feature — this is relocation + deploy wiring only.
- **Source vendored from** `https://github.com/Yudaadi-devo/proton-conversational-ai.git` @ branch `phase0-assist`. This repo becomes the source of truth; the old repo is retired.
- **Backend lands at `backend/apps/backend/`** (whole-repo subtree; internal paths unchanged). Its Dockerfile CMD is `uvicorn chatbot.main:app --host 0.0.0.0 --port ${PORT:-8080}`; health route is `GET /`.
- Work on branch `dev-yuda` (do not merge to `main`).

---

### Task 1: Vendor the proton repo via git subtree

Bring the entire proton repo in under `backend/`, preserving history, and prove the move is lossless by running the backend's own test suite.

**Files:**
- Create: `backend/` (entire subtree — `backend/apps/backend/`, `backend/apps/*`, `backend/docs/`, etc.)

**Interfaces:**
- Produces: `backend/apps/backend/Dockerfile` (build context for Task 2), `backend/apps/backend/.env.example` (var source for Task 3), `backend/apps/backend/pyproject.toml` (uv project).

- [ ] **Step 1: Confirm a clean working tree**

Run: `git -C /Users/yudaadipratama/Archive/id-crm-ticketing status --short`
Expected: only pre-existing untracked files (`.DS_Store`, `deploy/caddy/tenants/default.caddy`, the PDF). If anything is staged/modified, stop and reconcile first — subtree requires a clean index.

- [ ] **Step 2: Add the proton remote (read-only convenience)**

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git remote add proton https://github.com/Yudaadi-devo/proton-conversational-ai.git
git fetch proton phase0-assist
```

Expected: fetch succeeds; `git log proton/phase0-assist --oneline -1` shows the tip commit. Record that SHA for Task 4.

- [ ] **Step 3: Subtree-add the whole repo under `backend/` (full history)**

```bash
git subtree add --prefix=backend proton phase0-assist
```

Expected: a merge commit lands; `ls backend/apps/backend` shows `Dockerfile pyproject.toml uv.lock src/ ...`.
Fallback: if the subtree merge is awkward (unrelated-histories error), retry with `git subtree add --prefix=backend proton phase0-assist --squash`. Note in the commit message which was used.

- [ ] **Step 4: Verify the backend source is intact**

Run: `git ls-files backend/apps/backend | wc -l`
Expected: ~289 files. `test -f backend/apps/backend/uv.lock && echo OK` prints `OK`.

- [ ] **Step 5: Run the backend's own test suite (lossless proof)**

```bash
cd backend/apps/backend
uv sync --dev
uv run pytest -q
```

Expected: the full suite passes (~1003 tests per the spec; `testpaths = ["src"]`). If `uv` is unavailable in the environment, note it and defer this run to CI/local — do not block the vendoring on it, but flag it in the task report.

- [ ] **Step 6: Commit**

The subtree already created commits; no extra commit needed for the source. Confirm history is present:

```bash
cd /Users/yudaadipratama/Archive/id-crm-ticketing
git log --oneline backend/apps/backend/src/chatbot/main.py | head -3
```

Expected: multiple historical commits (not just the subtree merge), confirming history was preserved.

---

### Task 2: Add the `backend` service to the tenant compose

Add a `backend` service mirroring the `agent` service pattern, and point `PROTON_BACKEND_URL` at it.

**Files:**
- Modify: `deploy/docker-compose.tenant.yml` (add `backend` service; update `PROTON_BACKEND_URL` default; add PROTON env to the `agent` service)

**Interfaces:**
- Consumes: `backend/apps/backend/Dockerfile` (from Task 1).
- Produces: container `${TENANT}-backend` reachable in-network at `http://${TENANT}-backend:8080`.

- [ ] **Step 1: Add the `backend` service block**

In `deploy/docker-compose.tenant.yml`, after the `agent` service (end of file), add:

```yaml
  backend:
    build: ../backend/apps/backend
    image: platform-backend:latest
    container_name: ${TENANT}-backend
    restart: unless-stopped
    mem_limit: 768m
    env_file:
      - tenants/${TENANT}.env
    environment:
      PORT: "8080"
      HOST: 0.0.0.0
      CHATWOOT_API_URL: http://${TENANT}-chatwoot-rails:3000
      ZAMMAD_API_URL: http://${TENANT}-zammad-nginx:8080
    networks:
      platform:
        aliases:
          - ${TENANT}-backend
    healthcheck:
      test: ["CMD", "python3", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/', timeout=3).status == 200 else 1)"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 30s
```

Note: `env_file` supplies the backend's own vars (Task 3 adds them to the template so `add-tenant.sh` renders `tenants/${TENANT}.env`). The inline `environment:` block overrides host/port and the in-network CRM/Zammad URLs regardless of the file's localhost defaults.

- [ ] **Step 2: Point `PROTON_BACKEND_URL` at the in-stack service**

In the `x-chatwoot-env` anchor, change:

```yaml
  PROTON_BACKEND_URL: ${PROTON_BACKEND_URL:-}
```
to:
```yaml
  PROTON_BACKEND_URL: ${PROTON_BACKEND_URL:-http://${TENANT}-backend:8080}
```

- [ ] **Step 3: Give the `agent` service the PROTON env (fix the inert-feature gap)**

In the `agent` service `environment:` block (currently ends at `AGENT_DATABASE_URL: ...`), add:

```yaml
      PROTON_BACKEND_URL: ${PROTON_BACKEND_URL:-http://${TENANT}-backend:8080}
      PROTON_BACKEND_KEY: ${PROTON_BACKEND_KEY:-}
```

This activates the agent→backend features (per-inbox mode, persona messages) now that the backend is co-located.

- [ ] **Step 4: Validate compose renders**

```bash
cd deploy
docker compose -f docker-compose.tenant.yml --env-file tenants/example.env config >/dev/null && echo "COMPOSE OK"
```

Expected: `COMPOSE OK` with no interpolation errors. (If `tenants/example.env` lacks `TENANT`, the `config` sub-command still resolves defaults; set `TENANT=example` inline if needed: `TENANT=example docker compose ...`.)

- [ ] **Step 5: Commit**

```bash
git add deploy/docker-compose.tenant.yml
git commit -m "feat(deploy): add in-stack AI backend service; wire PROTON_BACKEND_URL + agent env"
```

---

### Task 3: Fold backend config into the tenant env template

Give `add-tenant.sh` one env file that covers the backend too, with sensible defaults and a GCP-creds note.

**Files:**
- Modify: `deploy/tenants/example.env` (append the backend's vars)

**Interfaces:**
- Consumes: `backend/apps/backend/.env.example` (from Task 1).
- Produces: `tenants/<tenant>.env` (rendered by `add-tenant.sh`) now carries backend vars consumed by the Task 2 `env_file`.

- [ ] **Step 1: Append a backend section to `example.env`**

Append to `deploy/tenants/example.env` a clearly delimited block sourced from `backend/apps/backend/.env.example`, adjusted for the in-stack deploy. Include at minimum:

```bash
# ============================================================================
# AI BACKEND (vendored from proton-conversational-ai; runs as ${TENANT}-backend)
# ----------------------------------------------------------------------------
# Provider selection
CRM_PROVIDER=chatwoot
KNOWLEDGE_PROVIDER=mock                 # mock | zendesk | vertex_search
VOICE_PROVIDER=mock

# Shared secret gating /assist/* and config reads. MUST equal PROTON_BACKEND_KEY.
PROTON_BACKEND_KEY=
FAQ_ADMIN_API_KEY=

# Gemini / Vertex — REQUIRES a GCP service account mounted into the container.
# See "GCP credentials" note below. Leave GOOGLE_GENAI_USE_VERTEXAI=false to use
# a Gemini API key path, or true for Vertex.
GOOGLE_GENAI_USE_VERTEXAI=false
GEMINI_MODEL=gemini-2.5-flash
VERTEX_PROJECT_ID=
VERTEX_LOCATION=us-central1

# Vertex AI Search (Knowledge). Only needed when KNOWLEDGE_PROVIDER=vertex_search.
VERTEX_SEARCH_PROJECT_ID=
VERTEX_SEARCH_LOCATION=global
VERTEX_SEARCH_DATA_STORE_ID=
VERTEX_SEARCH_ENGINE_ID=

# Persistence: memory (default, ephemeral) or firestore (needs GCP creds + DB).
HANDOFF_STORE=memory
SESSION_STORE=memory
FIRESTORE_PROJECT_ID=
FIRESTORE_DATABASE_ID=

# Phase toggles (all default-off / behavior-preserving)
ROUTING_ENABLED=false
ROUTING_ADMIN_API_KEY=
TASKS_API_KEY=
TASKS_REMINDER_WHATSAPP_ENABLED=false
SLA_ENGINE_ENABLED=false
METRICS_SYNC_ENABLED=false

# Escalation / PIC (Phase 2). JSON map dept->PIC; empty disables PIC notify.
PIC_MAP_JSON=
# ============================================================================
```

Do not blindly paste all ~120 lines of the backend `.env.example`; carry the vars a co-located deploy actually needs (above) and reference the full file for advanced/Twilio/telephony vars. Keep every default matching the backend's own defaults so behavior is preserved.

- [ ] **Step 2: Add the GCP credentials note**

Immediately below the block, add:

```bash
# --- GCP credentials (manual, per-tenant) ---
# The AI backend calls Gemini/Vertex/Firestore. Mount a service-account JSON and
# set GOOGLE_APPLICATION_CREDENTIALS in the backend service (add a volume +
# env in docker-compose.tenant.yml), OR run with GOOGLE_GENAI_USE_VERTEXAI=false
# and a Gemini API key. Live GCP wiring stays a manual provisioning step.
```

- [ ] **Step 3: Verify the new vars parse**

```bash
cd deploy
grep -c "PROTON_BACKEND_KEY\|ROUTING_ENABLED\|KNOWLEDGE_PROVIDER" tenants/example.env
```

Expected: `3` (each present once). Then re-run the compose config check from Task 2 Step 4 — still `COMPOSE OK`.

- [ ] **Step 4: Commit**

```bash
git add deploy/tenants/example.env
git commit -m "feat(deploy): fold AI backend config into tenant env template"
```

---

### Task 4: Housekeeping — CLAUDE.md + vendored-source pointer

Reflect the new two-service reality and record provenance.

**Files:**
- Modify: `CLAUDE.md`
- Create: `backend/VENDORED.md`

**Interfaces:**
- Consumes: the recorded proton tip SHA from Task 1 Step 2.

- [ ] **Step 1: Update `CLAUDE.md` first-party-code section**

In `CLAUDE.md`, replace the sentence stating the only first-party code lives in `agent/` (in the "What this repo is" section) with wording that names two first-party services — `agent/` and `backend/` (the vendored AI backend) — and states the boundary between them is deliberately HTTP over `PROTON_BACKEND_URL` (fail-open), not a shared process. Add a one-line "run the backend locally" pointer: `cd backend/apps/backend && uv run uvicorn chatbot.main:app --port 8080`.

- [ ] **Step 2: Create `backend/VENDORED.md`**

```markdown
# Vendored source

This directory was vendored from
`https://github.com/Yudaadi-devo/proton-conversational-ai.git` @ `phase0-assist`
(tip `<SHA-from-Task-1>`) via `git subtree add --prefix=backend` on 2026-07-20.

**This repo (`id-crm-ticketing`) is now the source of truth.** The standalone
proton-conversational-ai repo is retired — do all future AI-backend work here.

To pull any late upstream commits (before full retirement):
`git subtree pull --prefix=backend proton phase0-assist`
```

Replace `<SHA-from-Task-1>` with the recorded SHA.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md backend/VENDORED.md
git commit -m "docs: record vendored AI backend + two first-party services in CLAUDE.md"
```

---

### Task 5: End-to-end verification

Prove the whole stack is coherent without requiring live GCP.

**Files:** none (verification only).

- [ ] **Step 1: `agent/` suite still passes (boundary unchanged)**

```bash
cd agent
pip install -e '.[dev]'
pytest -q
```

Expected: PASS — no code changed in `agent/`, only env plumbing.

- [ ] **Step 2: Full compose renders with the backend service**

```bash
cd deploy
TENANT=example PUBLIC_IP=127.0.0.1 docker compose -f docker-compose.tenant.yml --env-file tenants/example.env config | grep -A2 "example-backend:" | head
```

Expected: the `example-backend` container definition appears with `image: platform-backend:latest`.

- [ ] **Step 3: Backend image builds**

```bash
cd deploy
docker compose -f docker-compose.tenant.yml --env-file tenants/example.env build backend
```

Expected: build succeeds (uv sync + copy). If Docker is unavailable in the environment, note it and defer to the operator; do not block plan completion.

- [ ] **Step 4 (optional, needs running stack): local smoke**

Bring up a throwaway tenant per `deploy/scripts/add-tenant.sh`, then:

```bash
docker exec <tenant>-agent python3 -c "import urllib.request; print(urllib.request.urlopen('http://<tenant>-backend:8080/', timeout=3).status)"
```

Expected: `200`. Then stop `<tenant>-backend` and confirm the agent still processes conversations (fail-open) — no exceptions in `docker logs <tenant>-agent`.

- [ ] **Step 5: Final report**

Summarize: subtree landed with history, backend suite result, `agent/` suite result, compose config + build result, and any deferred steps (uv/Docker/live-GCP). Do NOT claim live-GCP verification — it is out of scope.
