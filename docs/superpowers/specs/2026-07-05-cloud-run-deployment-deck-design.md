# Cloud Run Deployment Slide Deck — Design

**Date:** 2026-07-05
**Status:** Approved
**Deliverable:** `docs/slides/proton-cloud-run-deployment-guide.pptx` + generator script

## Goal

A single PowerPoint file for a mixed audience: a standalone executive summary a
client/stakeholder can absorb (Part A), followed by a detailed engineer runbook
(Part B) covering how to run the project on Cloud Run and wire Twilio and
Zendesk (plus the BigQuery metrics stack). The main flow teaches a from-scratch
setup; each runbook section ends with a "current prod values" call-out slide
grounding it in the live `lv-playground-genai` deployment.

## Output & tooling

- **Deck:** `docs/slides/proton-cloud-run-deployment-guide.pptx` (16:9).
- **Generator:** `docs/slides/generate_deck.py` using `python-pptx`, committed so
  the deck is regenerable. Run with a throwaway venv or `uv run --with python-pptx`.
- **Style:** clean light background, one accent color, monospace text boxes for
  shell commands, native pptx tables for env-var matrices, architecture diagram
  drawn from pptx shapes (rounded rectangles + connectors) — no external images.

## Structure

### Part A — Executive summary (6 slides, standalone)

1. **Title** — Proton Conversational AI: Cloud Run Deployment & Channel
   Integration Guide.
2. **What it does** — omnichannel AI customer-service agent: web chat, web
   voice, WhatsApp, real phone calls (Twilio Media Streams + Gemini Live),
   email; Gemini-powered KB answers, human handoff + CSAT, bot metrics.
3. **Architecture** — diagram: channels (Web SPA / WhatsApp / PSTN phone /
   email) → Cloud Run backend (FastAPI + Google ADK + Gemini) → Zendesk
   (tickets, handoff), Vertex AI Search (KB), BigQuery (metrics); Vue frontend
   served by nginx on Cloud Run.
4. **What going live requires** — three vendor accounts and what each provides:
   GCP (Cloud Run, Vertex AI, Secret Manager, BigQuery), Twilio (WhatsApp
   sender + phone number + API keys), Zendesk (Suite: tickets, email, triggers).
5. **Deployment effort at a glance** — ordered phases (GCP deploy → Twilio →
   Zendesk → metrics → smoke test) with rough time per phase.
6. **Status today** — live prod URLs, channel status matrix (all ✅ as of
   2026-07-02), remaining user-side step (new Twilio account's WA sandbox
   webhook + live tests).

### Part B — Engineer runbook (~24 slides)

Section divider slide per area; every area ends with a **"Current prod values"**
call-out slide.

**Cloud Run (5):**
- Prereqs: gcloud CLI, project + APIs, billing; repo layout (`apps/backend`,
  `apps/frontend`); no CI/CD — deploys are manual.
- Deploy backend: `gcloud run deploy proton-backend --source apps/backend
  --region asia-southeast1`; `--source` preserves existing env vars/secrets.
- Deploy frontend: committed `apps/frontend/.env` (`VITE_API_BASE_URL`),
  multi-stage Dockerfile (node build → nginx:alpine, SPA fallback via
  `nginx.conf.template` + envsubst).
- Env vars + Secret Manager: full backend env-var table grouped by feature
  (from `.env.example`); secrets `twilio-auth-token`, `twilio-api-key-secret`
  wired via `--set-secrets`; runtime SA needs `secretAccessor`.
- Redeploy + gotcha: traffic may be pinned to an old revision —
  `gcloud run services update-traffic <svc> --to-latest`.

**Twilio (5):**
- Account + credentials: Account SID, auth token, API key (SK) pair, TwiML App.
- WhatsApp sandbox: join flow, "When a message comes in" →
  `POST /webhooks/twilio-whatsapp`; shared sandbox points at ONE URL at a time.
- Phone: buy number, TwiML App VoiceUrl → `/voice/phone/incoming`, number's
  VoiceApplicationSid → the app; Media Streams wss bridge to Gemini Live
  (`PUBLIC_WSS_BASE_URL`).
- Token endpoint hardening: `POST /voice/phone/token` is unauthenticated —
  Origin allowlist (`FRONTEND_ORIGINS`), short TTL, per-IP rate limit.
- Prod values: account "Demo Proton (new)" SID/SK/AP identifiers, number
  +1 510 896 3929, sandbox number +1 415 523 8886, webhook URLs.

**Zendesk (5):**
- Access: subdomain, admin email + API token; env vars.
- Ticket model: detection-gated handoff tickets + conversation-mirror tickets;
  shared-instance multi-tenancy via `ZENDESK_TICKET_TAG`,
  `ZENDESK_REQUESTER_DOMAIN`, `ZENDESK_CUSTOMER_NAME_PREFIX`.
- Webhooks + triggers: agent-relay and CSAT-handback webhooks → backend
  endpoints, create-scoped triggers; IDs are per-environment.
- Email channel: `scripts/provision_zendesk_email_webhook.py`, trigger wiring,
  `EMAIL_DRAFT_ASSIST` mode.
- Prod values: live webhook/trigger IDs pointing at the prod backend.

**Metrics (4):**
- BigQuery: project/dataset/tables (`conversations`, `turn_events`,
  `qa_labels`), seed via `scripts/seed_demo_metrics.py`, manual sync via
  `scripts/sync_zendesk_metrics.py`.
- Providers + scheduler: `METRICS_PROVIDER=bigquery`, `METRICS_SYNC_ENABLED`,
  interval.
- Dashboards: in-app Vue `/dashboard` (`GET /metrics/dashboard`), Looker tiles.
- NPS + QA: `POST /chat/nps`, authed `POST /qa/label` (`QA_API_KEY`).

**Troubleshooting (2):** traffic pinned to old revision; WhatsApp 1600-char
limit (error 21617, chunking fix); Zendesk closed-ticket 422 + rotation;
sandbox one-URL-at-a-time; Twilio signature leading-space gotcha (tunnel
setups); Vertex rejects AI-Studio `-preview` Live model ids.

**Summary (1):** phase checklist recap + links (repo, USAGE.md, ADRs, specs,
runbook memories).

## Secrets policy

Identifiers only (URLs, project id/number, region, webhook/trigger IDs, Twilio
SIDs). Never secret values — auth tokens, API key secrets, Zendesk API token
are referenced as "stored in Secret Manager / service env vars".

## Error handling / verification

- Generator script must run cleanly and produce a valid .pptx; verify by
  reopening the file with python-pptx and asserting slide count + non-empty
  titles.
- All commands/env vars/IDs in slides must be sourced from the repo
  (`.env.example`, scripts, docs) or the recorded prod deploy notes — no
  invented values.
