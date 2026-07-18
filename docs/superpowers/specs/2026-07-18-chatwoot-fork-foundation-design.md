# Forked-Chatwoot foundation + first custom image — design

**Date:** 2026-07-18 · **Status:** design (pending approval) · **Sub-project 1 of the
Proton CRM-enhancement fork program.**

## Goal

Stand up a maintainable pipeline that builds a **patched (forked) Chatwoot image**, and
ship the first two patches in it: (1) replace the reply-box **Captain AI** button with our
own backend ("Suggest a reply / Summarize / Ask"), and (2) a native **Proton left-nav
menu**. Develop everything **locally**, then build → push to Artifact Registry → deploy to
the **`default`** tenant, verify, and **replicate** to `proton`/`wahchan`. Customer info in
the conversation's right panel is delivered the **light** way (native custom attributes,
no fork).

Only the **MIT community** frontend is patched — never the proprietary `/enterprise`
folder. We do not unlock Chatwoot's paid features; we **replace** the capability with our
own service.

## Decisions locked in brainstorming

- Build strategy: **patch-set on pinned upstream `v4.15.1`, built off-VM → Artifact
  Registry** (not a long-lived fork branch, not runtime JS injection).
- Dev model: **local-first** (Vite HMR loop) → build image → deploy to VM.
- First image ships **both** patches: AI-button replacement **and** the nav menu.
- Right panel: **light first** (custom attributes), rich forked 360 later.
- `default` is the reference tenant; the same image serves all tenants (menu is baked in;
  per-tenant behaviour comes from runtime env).

## Architecture

### Two repos in scope

- **`id-crm-ticketing`** (this repo): the fork/build pipeline + patches + multi-tenant
  compose changes.
- **`proton-conversational-ai`**: two new backend endpoints for the AI button
  (`summarize`, `ask`); `suggest` reuses the existing `/kb/suggest` grounding.

### A. Fork & build pipeline (`chatwoot-fork/` in `id-crm-ticketing`)

```
deploy/chatwoot-fork/
  UPSTREAM_VERSION            # "v4.15.1" — the pinned Chatwoot tag
  patches/                    # our diffs against the MIT frontend (git-format patches)
    0001-runtime-config.patch     # inject per-tenant config (env → window global)
    0002-ai-assist-backend.patch  # rewire the reply-box AI menu to our backend
    0003-proton-nav-menu.patch    # add left-nav items → generic iframe host route
  Dockerfile                  # clone tag → apply patches → build → runtime image
  cloudbuild.yaml             # Cloud Build variant (off-Mac option)
  README.md                   # local dev loop + build/push + upgrade runbook
```

- **Build (Dockerfile):** start from the upstream source at `UPSTREAM_VERSION`, `git apply`
  the `patches/*.patch` in order, run the frontend build, and produce a runtime image
  tagged `…-docker.pkg.dev/lv-playground-genai/<repo>/proton-chatwoot:v4.15.1-custom`.
  Runs on the **Mac (`docker build`)** or **Cloud Build** — **never on the 16 GB VM**.
- **Local dev loop (README):** clone the tag, apply patches, run Chatwoot in dev mode
  (Rails + Vite HMR) against a tiny local Postgres/Redis compose; edit the patched
  components with hot reload; when happy, regenerate the `.patch` files (`git diff`) and
  build the image.
- **Patches, not a fork branch:** patches are small, reviewable diffs. **Upgrade runbook:**
  bump `UPSTREAM_VERSION`, re-apply patches (fix any conflicts), rebuild, re-push.

### B. Runtime per-tenant config (patch 0001)

The image is shared, so per-tenant values are resolved **at container start**, not baked in.
A small patch injects a config object into the served page from env vars the Chatwoot
container already receives:

- `PROTON_BACKEND_URL` — the tenant's AI/apps backend base (e.g. the proton Cloud Run URL
  for `proton`; a configured URL for others).
- `PROTON_BACKEND_KEY` — the API key for privileged calls.
- `PROTON_FEATURES` — a comma list toggling patches per tenant (e.g. `ai_assist,nav_menu`)
  so a tenant with no backend simply doesn't show the AI button.

The AI button and nav host read from this global; **no rebuild needed to point a tenant at
a different backend or disable a feature.**

### C. AI button replacement (patch 0002) — replaces Captain AI

Rewire the reply-box AI-assist menu (the ✨ button: "Suggest a reply / Summarize the
conversation / Ask Copilot") to call `PROTON_BACKEND_URL` instead of Captain AI:

- **Suggest a reply** → `POST /assist/suggest` (backend drafts a reply grounded in the
  conversation + FAQ via existing `/kb/suggest`); the result is **inserted natively into
  the reply box** (a native component can set the input — this removes the copy-paste
  limitation the sandboxed FAQ-Assist iframe has).
- **Summarize the conversation** → `POST /assist/summarize` (**new** backend endpoint,
  Gemini over the conversation transcript).
- **Ask Copilot** → `POST /assist/ask` (**new** backend endpoint, Q&A over conversation +
  KB). Rendered in the menu/panel; answer insertable.

If `ai_assist` isn't in `PROTON_FEATURES` (or no backend URL), the button hides. The
left-nav **"Captain"** item is hidden for consistency.

**Backend (proton-conversational-ai):** add `POST /assist/summarize` and `POST /assist/ask`
(reuse the existing Gemini/ADK client, config, and `x-api-key` auth pattern); `suggest`
wraps `/kb/suggest` + a short Gemini draft. Requests carry the conversation id + account,
verified against `PROTON_BACKEND_KEY`.

### D. Proton nav menu (patch 0003)

Add left-sidebar items — **Reports**, **FAQ Admin**, **Agents**, **Cases** — each routing
to **one generic host route** that renders an `<iframe>` to `PROTON_BACKEND_URL/apps/<name>`
and passes account id + current user + a short-lived token (query + `postMessage`). Items
are driven by `PROTON_FEATURES`, so tenants show only what they have. For the foundation,
items point at **existing/placeholder** apps (FAQ Admin exists; Reports/Agents/Cases are
placeholders until their feature specs land). i18n strings + icons included.

### E. Right panel — light (no fork)

Customer info shows in Chatwoot's **native** right panel via **contact custom attributes**
the backend writes through the Chatwoot API. The foundation defines the attribute schema
(personal/vehicle/service/call-center groupings) and writes whatever contact data exists
today; **DMS/TSP enrichment is deferred** to its own spec (needs API access). No frontend
patch here.

### F. Multi-tenant deploy (default → replicate)

- Parameterize `deploy/docker-compose.tenant.yml`: `image: ${CHATWOOT_IMAGE:-chatwoot/chatwoot:v4.15.1}`
  for both `chatwoot-rails` and `chatwoot-sidekiq`; add `PROTON_BACKEND_URL`,
  `PROTON_BACKEND_KEY`, `PROTON_FEATURES` to `tenants/example.env`.
- **`default` first:** set its `CHATWOOT_IMAGE` to the custom tag + fill the PROTON_* env,
  `docker compose … pull` + `up -d chatwoot-rails chatwoot-sidekiq`, verify menu + AI button.
- **Replicate:** set the same image + per-tenant PROTON_* for `proton`/`wahchan`.
- **Rollback:** revert `CHATWOOT_IMAGE` to stock + `up -d` (the custom image only adds
  additive UI; no schema/data changes, so rollback is clean).

## Scope boundary

**In:** the pipeline; patches 0001–0003; the 3 AI-assist backend endpoints; runtime
per-tenant config; custom-attribute schema + write of existing contact data; multi-tenant
image parameterization + default→replicate rollout + rollback; local dev + upgrade runbook.

**Out (follow-on specs):** the Reports/Agents/Cases app *contents*; DMS/TSP enrichment +
rich forked 360 widget; agent routing logic; taxonomy manager; anything touching the
`/enterprise` folder.

## Success criteria

- **Local:** patched Chatwoot boots; the ✨ menu calls our backend and inserts a suggestion
  into the reply box; Summarize/Ask return results; the 4 nav items render and open the
  host route with context; toggling `PROTON_FEATURES` shows/hides features.
- **Build:** `proton-chatwoot:v4.15.1-custom` builds off-VM and pushes to Artifact Registry.
- **VM:** `default` shows both patches after pulling the image; replicated to `proton`
  (pointing at its Cloud Run backend) and `wahchan`; stock rollback verified.

## Risks

- **Frontend patch drift on upgrade** — mitigated by keeping patches tiny/additive and the
  upgrade runbook.
- **Per-tenant backend availability** — `default`/`wahchan` need a backend URL for the AI
  button, or it stays disabled via `PROTON_FEATURES` until one exists.
- **Build weight** — the source build is heavy; kept off the VM (Mac/Cloud Build) by design.
- **Auth of iframe/AI calls across origins** — short-lived token + `PROTON_BACKEND_KEY`;
  backend CORS must allow each tenant origin.
