# Vendor the AI backend into a single-repo CRM — Design

**Date:** 2026-07-20
**Status:** Approved (brainstorming)
**Repo:** `id-crm-ticketing` (remote `fauzal-atmodirono/id-crm-ticketing`)

## Goal

Make `id-crm-ticketing` a **single CRM codebase** so provisioning a new tenant
pulls *everything* from one repo: Chatwoot + Zammad + Caddy + the `agent`
service **and** the AI backend (currently the standalone
`proton-conversational-ai` repo). After this work, spinning up a tenant builds
and runs the AI backend in-stack — no separate repo, no external backend URL to
point at.

The AI/Captain features and the phase program stay exactly where they are
behaviorally; this is a **relocation + deploy-wiring** change, not a rewrite.

## Why

Decided direction (see memory `crm-enhancement-vision`, Option A): productize
`proton-conversational-ai` as the single unified per-tenant backend for the
enhanced Chatwoot. Today it is a sibling repo the operator must run and wire
separately. Folding it in removes that seam so the deliverable to any new tenant
is one repo.

## Source-of-truth decision

**This repo becomes the one source of truth.** After vendoring, the standalone
`proton-conversational-ai` repo (`Yudaadi-devo/proton-conversational-ai` @
`phase0-assist`) is **retired** — all future AI-backend + CRM work happens here
in one monorepo. We commit to not developing in the old repo anymore.

## What the backend contains (all phases)

Confirmed against `apps/backend/src/chatbot/features/` in the proton repo — the
one backend holds the server-side of every phase:

| Backend module | Phase / feature |
|---|---|
| `features/assist/` (copilot, assist router, `assistant_runtime`) | Phase 0 AI-assist + Captain-parity persona/tools |
| `features/chat/kb_*` routers (assistants, settings, tools, scenarios, inboxes, documents, suggest, faq_admin) | Captain-parity program |
| `features/chat/{pic_registry,escalation_notifier,sla}.py` | Phase 2 (escalation / PIC / SLA) |
| `features/metrics/` (bigquery_schema, dashboard/anomaly/export/qa/faq routers) | Phase 3 (reporting / BI) |
| `features/routing/` | Phase 5 (agent routing & presence) |
| `features/tasks/` | Phase 6 (task timers & reminders) |
| `features/chat/phone/` | Phase 7 telephony scaffolding |

Each phase has two halves. The **backend** half is what we vendor in here. The
**frontend** half (native Vue views) already lives in this repo as the Chatwoot
fork patches under `deploy/chatwoot-fork/patches/` (0001–0019). After this move,
both halves of every phase sit in one repo.

## Architecture: two pieces, one boundary

Two independent pieces of work; the coupling stays as-is.

1. **Vendor the source** (one-time git move).
2. **Wire it into the deploy** so a tenant's `docker compose up` builds + runs
   the backend alongside Chatwoot/Zammad/agent.

The boundary between `agent/` and the AI backend stays **HTTP over
`PROTON_BACKEND_URL`** — no shared DB, no shared process, no cross-imports.
"Inside the project" means *co-located and co-deployed*, not *merged into one
service*. This is the "not heavily connected" constraint.

### Piece 1 — Vendor the source (Approach A)

Use `git subtree add` to bring the **entire** proton repo under a top-level
prefix `backend/`:

```
git subtree add --prefix=backend \
  https://github.com/Yudaadi-devo/proton-conversational-ai.git phase0-assist --squash
```

(Decide `--squash` vs full history at implementation time; default to preserving
full history since it is only ~5.7 MB / 378 commits and this repo becomes the
source of truth. `--squash` is the fallback if a clean subtree merge is
awkward.)

Rationale for whole-repo (not backend-only):

- Tracked source is tiny (593 files, ~5.7 MB history, 378 commits) — history
  preservation is effectively free.
- Internal paths (`apps/backend/...`, its Dockerfile, scripts, docs) stay intact
  → **zero internal path rewrites**.
- Pulls the Chatwoot dashboard nav-apps (`apps/chatwoot-routing-admin`,
  `apps/chatwoot-my-tasks`, `apps/chatwoot-faq-admin`, `apps/chatwoot-agent-app`,
  `apps/zendesk-agent-app`, `apps/frontend`) in the same shot.

Resulting layout: the FastAPI backend lands at `backend/apps/backend/`. The
`backend/apps/backend/` nesting is cosmetic and accepted.

No collision with this repo's existing top-level `apps/` (only
`knowledge-manager` + `taxonomy-manager` live there; the vendored apps live
under `backend/apps/`).

### Piece 2 — Deploy wiring

The backend already ships a `uv`-based `Dockerfile` (entrypoint
`chatbot.main:app`, port 8080). Wiring is additive to the existing compose,
mirroring the `agent` service.

1. **New `backend` service in `deploy/docker-compose.tenant.yml`**:
   - `build: ../backend/apps/backend`, `image: platform-backend:latest`
   - `container_name: ${TENANT}-backend`, network alias `${TENANT}-backend`
   - `mem_limit` sized like the other app services
   - `/healthz`-style healthcheck consistent with `agent`/chatwoot
   - env from the tenant env file (see step 3)

2. **Point existing consumers at the in-stack service**:
   - `PROTON_BACKEND_URL` default → `http://${TENANT}-backend:8080`
     (already plumbed into chatwoot-env via the `x-chatwoot-env` anchor).
   - **Fix the gap:** the `agent` service env block does **not** currently
     receive `PROTON_BACKEND_URL`/`PROTON_BACKEND_KEY`, so the agent→backend
     features (FU-D per-inbox mode, persona messages) are inert in the deploy.
     Add both to the `agent` service env so co-location actually activates them.

3. **Backend config → tenant env template**: fold
   `backend/apps/backend/.env.example` into `deploy/tenants/example.env` so
   `add-tenant.sh` provisions one env file covering the whole stack. Includes
   Gemini/Vertex AI Search/Firestore/GCP creds, the backend `x-api-key`, and
   per-tenant knobs (`ROUTING_ENABLED`, `TASKS_API_KEY`, `PIC_MAP_JSON`, etc.).

### Piece 3 — Decoupling guarantees ("not heavily connected")

- Boundary stays HTTP over `PROTON_BACKEND_URL` — no shared DB, no shared
  process, no code imports between `agent/` and `backend/`.
- Both keep **fail-open** behavior: backend down → agent falls back to global
  settings, exactly as today (`agent/app/clients/proton.py`).
- The backend keeps its own Docker image, own Python env (`uv`), own test
  suite — still buildable/runnable/testable standalone.

### Piece 4 — Housekeeping

- Update **`CLAUDE.md`**: the "only first-party code lives in `agent/`" line
  becomes two first-party services (`agent/` + `backend/`), noting the HTTP
  boundary is deliberate and how to run the backend locally.
- Add a short pointer (e.g. `backend/VENDORED.md` or a README note) recording
  the source was vendored from `Yudaadi-devo/proton-conversational-ai` @
  `phase0-assist` at commit `<sha>`, and that this repo is now the source of
  truth (old repo retired).

## GCP credentials (flagged, out of code scope)

The backend depends on GCP services (Vertex AI Search, Firestore, Gemini).
Co-locating per-tenant means each tenant's backend needs credentials + a data
store. This is a **provisioning** concern, documented in the env template and
`add-tenant.sh` notes; wiring live GCP per tenant stays manual (same as today).

## Testing / verification

- After vendoring: `cd backend/apps/backend && uv sync --dev && uv run pytest`
  — the backend's own suite (~1003 tests per memory; `testpaths = ["src"]`)
  still passes, proving the move was lossless.
- `agent/`'s suite (`cd agent && pytest`) still passes — no code change to the
  boundary client, only env plumbing.
- `docker compose -f deploy/docker-compose.tenant.yml config` validates with the
  new `backend` service and the updated env template.
- Local smoke: bring up a tenant stack, confirm `${TENANT}-backend` is healthy,
  and that Chatwoot/agent reach it at `http://${TENANT}-backend:8080` (fail-open
  still holds when it is stopped).

## Out of scope

- No behavioral change to any phase feature.
- No merge of `agent/` and the backend into one service.
- No live GCP provisioning automation.
- Retiring/archiving the old GitHub repo is an ops action, not part of this code
  change (this spec only declares the intent).

## Open questions (resolve during implementation)

- `git subtree add` with full history vs `--squash` — pick during the git step.
- Exact `mem_limit` for the backend service.
- Whether to also serve the vendored nav-apps via the existing `agent`
  `/apps/<name>/` static mount, or leave them for the Chatwoot fork build.
